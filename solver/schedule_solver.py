"""
solver/schedule_solver.py
─────────────────────────
OR-Tools CP-SAT scheduling solver for SkillForge.

Generates exactly 3 recovery scenarios given a machine disruption:
  A — Status Quo:        Accept delays, pay SLA penalties.
  B — Lembur (Overtime): Move orders to night-shift overtime.
  C — Rerute Optimal:    CP-SAT optimal reroute minimising changeover + overtime IDR cost.

Every reroute (B and C) only uses a machine that is (1) not in a Tier 1 blocked
status, (2) able to make the part (machine type rule in
skills/cnc_milling/rules/machine_capability.json, or the order's own
eligible_work_centers), and (3) free: moved work starts after the orders and
planned maintenance already on that machine, and must end by 23:00 WIB.
reroute_violations() re-checks the same rules at approval time.
"""

from __future__ import annotations

import math
from typing import Any

from ortools.sat.python import cp_model

import json
from pathlib import Path

# Skill cost source — read fresh from disk on every solve so edits take effect.
_SKILL_SLA_PATH = Path(__file__).parent.parent / "skills" / "cnc_milling" / "rules" / "sla_penalties.json"


def _load_skill_costs() -> tuple[dict, dict, int, int]:
    """Load (penalty_per_hour, max_penalty, overtime, changeover) from skill file.

    Falls back to module defaults when the file is missing/unparseable, so the
    solver (and its unit tests) still work without skill files on disk.
    """
    try:
        data = json.loads(_SKILL_SLA_PATH.read_text(encoding="utf-8"))
        classes = data.get("order_classes", {})
        ops = data.get("operational_costs", {})
        pph = {
            cls: int(classes[cls]["penalty_per_hour_idr"])
            for cls in ("A", "B", "C") if cls in classes
        } or dict(PENALTY_PER_HOUR)
        mp = {
            cls: int(classes[cls]["max_penalty_idr"])
            for cls in ("A", "B", "C") if cls in classes
        } or dict(MAX_PENALTY)
        ot = int(ops.get("overtime_cost_per_hour_idr", OVERTIME_COST_PER_HOUR))
        co = int(ops.get("changeover_cost_idr", CHANGEOVER_COST))
        # Ensure all three classes present even if file is partial.
        for cls in ("A", "B", "C"):
            pph.setdefault(cls, PENALTY_PER_HOUR[cls])
            mp.setdefault(cls, MAX_PENALTY[cls])
        return pph, mp, ot, co
    except Exception:
        return dict(PENALTY_PER_HOUR), dict(MAX_PENALTY), OVERTIME_COST_PER_HOUR, CHANGEOVER_COST

# ── Cost defaults (fallback when skill files are missing) ───────────────────
# Live values are loaded from skills/cnc_milling/rules/sla_penalties.json on
# every solve() — editing the skill file changes solver behaviour, no restart.
PENALTY_PER_HOUR = {"A": 20_000_000, "B": 300_000, "C": 100_000}
MAX_PENALTY      = {"A": 150_000_000, "B": 6_000_000, "C": 2_000_000}
OVERTIME_COST_PER_HOUR = 450_000   # IDR / machine-hour
CHANGEOVER_COST        = 350_000   # IDR / order moved to different machine

# ── Tier 1 safety: statuses a solver must NEVER route work to ────────────────
# Mirrors the inviolable Tier 1 hierarchy in skills/cnc_milling/SKILL.md.
# MAINTENANCE = machine offline; FAULT = safety interlock tripped.
TIER1_BLOCKED_STATUSES = ("MAINTENANCE", "FAULT")

SHIFT_START_H  = 0   # 07:00 WIB
SHIFT_END_H    = 8   # 15:00 WIB
OVERTIME_END_H = 16  # 23:00 WIB

_SKILL_CAPABILITY_PATH = _SKILL_SLA_PATH.parent / "machine_capability.json"

# Orders that occupy a machine. RESCHEDULED is work an approved reroute put there.
BOOKED_STATUSES = ("IN_PROGRESS", "QUEUED", "PLANNED", "RESCHEDULED")
CLASS_RANK = {"A": 0, "B": 1, "C": 2}


def _load_capability() -> dict[str, list[str]]:
    """{target machine type: [source machine types it may take work from]}, read fresh."""
    try:
        data = json.loads(_SKILL_CAPABILITY_PATH.read_text(encoding="utf-8"))
        return {k: list(v) for k, v in data.get("can_take_work_from", {}).items()}
    except Exception:
        return {}


def duration_h(order) -> float:
    try:
        from datetime import datetime
        s = datetime.fromisoformat(order["planned_start"].replace("Z", ""))
        e = datetime.fromisoformat(order["planned_end"].replace("Z", ""))
        return max((e - s).total_seconds() / 3600, 0.5)
    except Exception:
        return 2.0


def _day(ts: Any) -> str:
    return str(ts or "")[:10]


def plan_day(orders) -> str:
    """The shift being planned: the earliest day any open order is planned on."""
    days = [_day(o.get("planned_start")) for o in orders
            if o.get("status") in BOOKED_STATUSES and o.get("planned_start")]
    return min(days) if days else ""


def can_take(order, source_wc, target_wc, capability) -> bool:
    """May target_wc make this order? The order's own routing list wins, then the machine type rule."""
    eligible = order.get("eligible_work_centers")
    if isinstance(eligible, list) and eligible:
        return target_wc.get("id") in eligible
    src_t = (source_wc or {}).get("machine_type")
    tgt_t = target_wc.get("machine_type")
    if not src_t and not tgt_t:
        return True       # no machine types recorded at all: nothing to compare
    if not src_t or not tgt_t:
        return False
    return src_t == tgt_t or src_t in capability.get(tgt_t, [])


def booked_hours(state, machine_id, exclude_ids=(), day=None) -> float:
    """Hours already committed on a machine in the planned shift, counted from 07:00.

    A machine runs one order at a time, so its open orders count back to back,
    plus any planned maintenance window that day."""
    orders = state.get("production_orders", [])
    day = plan_day(orders) if day is None else day
    hours = sum(
        duration_h(o) for o in orders
        if o.get("work_center_id") == machine_id and o.get("status") in BOOKED_STATUSES
        and o.get("id") not in exclude_ids and _day(o.get("planned_start")) == day
    )
    wc = next((w for w in state.get("work_centers", []) if w.get("id") == machine_id), {})
    for w in wc.get("planned_maintenance_windows") or []:
        if _day(w.get("start")) == day:
            hours += duration_h({"planned_start": w.get("start", ""), "planned_end": w.get("end", "")})
    return hours


def reroute_violations(state, gantt_changes, capability=None) -> list[dict]:
    """Capability and capacity problems in a set of moves, checked against `state`.

    The solver only proposes moves that pass; approval calls this again on live
    SAP so a plan that went stale (or was never valid) cannot be written."""
    capability = _load_capability() if capability is None else capability
    wcs = {w.get("id"): w for w in state.get("work_centers", [])}
    orders = {o.get("id"): o for o in state.get("production_orders", [])}
    moving_ids = {c.get("order_id") for c in gantt_changes}
    day = plan_day(state.get("production_orders", []))
    out: list[dict] = []
    added: dict[str, float] = {}
    for c in gantt_changes:
        target, order = wcs.get(c.get("to_machine")), orders.get(c.get("order_id"))
        if c.get("to_machine") == c.get("from_machine") or target is None or order is None:
            continue
        if not can_take(order, wcs.get(c.get("from_machine")), target, capability):
            out.append({"error": "CapabilityViolation", "order_id": order["id"], "machine_id": target["id"],
                        "message": f"{target['id']} ({target.get('machine_type') or '-'}) tidak bisa "
                                   f"mengerjakan {order['id']} dari {c.get('from_machine')}."})
        added[target["id"]] = added.get(target["id"], 0.0) + duration_h(order)
    for mid, extra in added.items():
        booked = booked_hours(state, mid, moving_ids, day)
        if booked + extra > OVERTIME_END_H + 1e-9:
            out.append({"error": "CapacityViolation", "machine_id": mid,
                        "message": f"{mid} sudah terisi {booked:g} jam; tambahan {extra:g} jam "
                                   f"melewati batas {OVERTIME_END_H} jam (07:00–23:00 WIB)."})
    return out


def _ot_hours(end10: int) -> int:
    """Whole overtime hours past 15:00 for work ending at end10 (tenths of an hour from 07:00)."""
    return max(0, -(-(end10 - SHIFT_END_H * 10) // 10))


class ScheduleSolver:
    """Generates Scenario A, B, C for a given machine disruption."""

    def solve(self, shop_floor_state, disruption):
        """
        Parameters
        ----------
        shop_floor_state : dict  Full state from SAPClient.get_state()
        disruption : dict
            machine_id, disruption_type, start_hour (0-8), end_hour (0-8)

        Returns list of 3 scenario dicts (A, B, C) with recommended flag set.
        """
        machine_id       = disruption["machine_id"]
        disruption_hours = int(disruption["end_hour"]) - int(disruption["start_hour"])
        # Work on the disrupted machine resumes when the outage ENDS, not after
        # `disruption_hours` from shift start (they differ when start_hour > 0).
        self._resume_h = int(disruption["end_hour"])
        # False when the machine stays down past tonight (e.g. waiting days for a
        # part): then it can't take back its own work in overtime either.
        self._own_ok = disruption.get("machine_back_today", True) is not False

        # Phase 2: costs come from the skill file, read fresh every solve.
        pph, mp, ot_cost, co_cost = _load_skill_costs()
        self._pph, self._mp = pph, mp
        self._ot, self._co = ot_cost, co_cost

        orders        = shop_floor_state.get("production_orders", [])
        work_centers  = shop_floor_state.get("work_centers", [])

        affected = [
            o for o in orders
            if o.get("work_center_id") == machine_id
            and o.get("status") in ("IN_PROGRESS", "QUEUED", "PLANNED")
        ]
        # Tier 1 safety rule (inviolable): never route work to a machine that is
        # in a blocked safety status. Driven by the Tier 1 hierarchy — the
        # solver treats MAINTENANCE/FAULT as unsafe regardless of cost.
        available = [
            wc for wc in work_centers
            if wc["id"] != machine_id and wc.get("status") not in TIER1_BLOCKED_STATUSES
        ]

        # Capability and free time per safe alternative, shared by scenarios B and C.
        capability = _load_capability()
        day = plan_day(orders)
        source = next((wc for wc in work_centers if wc["id"] == machine_id), None)
        affected_ids = {o["id"] for o in affected}
        self._booked = {wc["id"]: booked_hours(shop_floor_state, wc["id"], affected_ids, day)
                        for wc in available}
        self._eligible = {o["id"]: [wc["id"] for wc in available if can_take(o, source, wc, capability)]
                          for o in affected}

        scenarios = [
            self._scenario_a(affected, disruption_hours, machine_id),
            self._scenario_b(affected, disruption_hours, machine_id, available),
            self._scenario_c(affected, orders, disruption_hours, machine_id, available),
        ]

        # Why each other machine can or can't take this work, shown with every scenario.
        min_dur = min((self._duration_h(o) for o in affected), default=0.0)
        checks = []
        for wc in work_centers:
            if wc["id"] == machine_id:
                continue
            booked = booked_hours(shop_floor_state, wc["id"], affected_ids, day)
            free = max(0.0, OVERTIME_END_H - booked)
            if wc.get("status") in TIER1_BLOCKED_STATUSES:
                usable, reason = False, f"status {wc.get('status')} (Tier 1)"
            elif not any(wc["id"] in self._eligible[o["id"]] for o in affected) and affected:
                usable, reason = False, f"tidak mampu: {wc.get('machine_type') or 'jenis tidak tercatat'}"
            elif free + 1e-9 < min_dur:
                usable, reason = False, f"penuh: {booked:g} jam sudah terisi, sisa {free:g} jam"
            else:
                usable, reason = True, f"{booked:g} jam terisi, sisa {free:g} jam sampai 23:00"
            checks.append({"machine_id": wc["id"], "machine_type": wc.get("machine_type"),
                           "status": wc.get("status"), "booked_hours": round(booked, 1),
                           "free_hours": round(free, 1), "usable": usable, "reason": reason})
        for sc in scenarios:
            sc["machine_checks"] = checks

        # Recommend by lowest total_cost_idr (best value), ties broken by net_savings (higher is better)
        # net_savings can be negative now, so we don't use max(..., 0) anywhere
        best = min(range(3), key=lambda i: scenarios[i]["total_cost_idr"])
        for i, s in enumerate(scenarios):
            s["recommended"] = (i == best)
        return scenarios

    # ── Skill-driven cost accessors (fresh per solve, defaults otherwise) ──────

    @property
    def _resume(self):
        return getattr(self, "_resume_h", 0)

    @property
    def _cost_pph(self):
        return getattr(self, "_pph", PENALTY_PER_HOUR)

    @property
    def _cost_mp(self):
        return getattr(self, "_mp", MAX_PENALTY)

    @property
    def _cost_ot(self):
        return getattr(self, "_ot", OVERTIME_COST_PER_HOUR)

    @property
    def _cost_co(self):
        return getattr(self, "_co", CHANGEOVER_COST)

    def _total_penalty(self, orders, delay_hours):
        total = 0
        for o in orders:
            cls = o.get("order_class") if o.get("order_class") in ("A", "B", "C") else "C"
            total += min(delay_hours * self._cost_pph[cls], self._cost_mp[cls])
        return total

    # ── Scenario A ────────────────────────────────────────────────────────────

    def _scenario_a(self, affected, disruption_hours, machine_id):
        total_penalty = 0
        gantt_changes = []
        for order in affected:
            cls     = order.get("order_class") if order.get("order_class") in ("A", "B", "C") else "C"
            penalty = min(disruption_hours * self._cost_pph[cls], self._cost_mp[cls])
            total_penalty += penalty
            dur = self._duration_h(order)
            gantt_changes.append({
                "order_id":      order["id"],
                "from_machine":  machine_id,
                "to_machine":    machine_id,
                "new_start_time": f"{7 + self._resume:02d}:00 WIB",
                "new_end_time":   f"{min(7 + self._resume + int(dur), 15):02d}:00 WIB",
                "delay_hours":   disruption_hours,
                "sla_penalty_idr": penalty,
            })
        return {
            "id": "scenario_a", "name_en": "Status Quo — Accept Delay",
            "name_id": "Status Quo — Terima Keterlambatan",
            "recommended": False,
            "total_cost_idr": total_penalty,
            "net_savings_idr": -total_penalty,  # No action means paying full penalty
            "cost_breakdown": {"sla": total_penalty, "overtime": 0, "changeover": 0, "freight": 0},
            "affected_orders": [o["id"] for o in affected],
            "gantt_changes": gantt_changes,
            "rationale_template": (
                f"Mesin {machine_id} mengalami gangguan selama {disruption_hours} jam. "
                f"{len(affected)} pesanan terlambat. "
                f"Total denda SLA: Rp {total_penalty:,.0f}. "
                "Tidak ada tindakan aktif — produksi lanjut setelah mesin pulih."
            ),
        }

    # ── Scenario B ────────────────────────────────────────────────────────────

    def _scenario_b(self, affected, disruption_hours, machine_id, available):
        # Night shift (15:00-23:00): the repaired machine first, else an eligible
        # machine with room after its own work. An order that fits nowhere stays late.
        free = {machine_id: max(SHIFT_END_H, self._resume)}
        for wc in available:
            free[wc["id"]] = max(SHIFT_END_H, self._booked[wc["id"]])
        avoided = self._total_penalty(affected, disruption_hours)
        gantt_changes, ot_total, co_total, sla_total = [], 0, 0, 0
        for order in self._by_priority(affected):
            dur = self._duration_h(order)
            fits = [m for m in [machine_id] * self._own_ok + self._eligible[order["id"]]
                    if free[m] + dur <= OVERTIME_END_H + 1e-9]
            if not fits:
                change = self._late_change(order, machine_id, disruption_hours)
                sla_total += change["sla_penalty_idr"]
                gantt_changes.append(change)
                continue
            target = min(fits, key=lambda m: (free[m] + dur, m != machine_id))
            start, end = free[target], free[target] + dur
            free[target] = end
            ot = (_ot_hours(round(end * 10)) - _ot_hours(round(start * 10))) * self._cost_ot
            co = 0 if target == machine_id else self._cost_co
            ot_total, co_total = ot_total + ot, co_total + co
            gantt_changes.append({
                "order_id":      order["id"],
                "from_machine":  machine_id,
                "to_machine":    target,
                "new_start_time": f"{7 + int(start):02d}:00 WIB (Lembur)",
                "new_end_time":   f"{7 + math.ceil(end):02d}:00 WIB (Lembur)",
                "changeover_cost_idr": co,
                "overtime_cost_idr": ot,
            })
        total = ot_total + co_total + sla_total
        return {
            "id": "scenario_b", "name_en": "Overtime — Night Shift",
            "name_id": "Lembur — Shift Malam",
            "recommended": False,
            "total_cost_idr": total,
            "net_savings_idr": avoided - total,  # Can be negative
            "cost_breakdown": {"sla": sla_total, "overtime": ot_total, "changeover": co_total, "freight": 0},
            "affected_orders": [o["id"] for o in affected],
            "gantt_changes": gantt_changes,
            "rationale_template": (
                f"Pesanan dari {machine_id} dikerjakan di shift lembur malam, di mesin yang sama setelah "
                "diperbaiki atau di mesin lain yang mampu dan masih kosong. "
                f"Biaya lembur: Rp {ot_total:,.0f}. "
                f"Denda dihindari: Rp {avoided - sla_total:,.0f}. "
                f"Penghematan bersih: Rp {avoided - total:,.0f}. "
                "Diperlukan persetujuan Production Manager."
            ),
        }

    # ── Scenario C ────────────────────────────────────────────────────────────

    def _scenario_c(self, affected, all_orders, disruption_hours, machine_id, available):
        if not affected:
            return {
                "id": "scenario_c", "name_en": "Optimal Reroute",
                "name_id": "Rerute Optimal", "recommended": False,
                "total_cost_idr": 0, "net_savings_idr": 0,
                "cost_breakdown": {"sla": 0, "overtime": 0, "changeover": 0, "freight": 0},
                "affected_orders": [], "gantt_changes": [],
                "rationale_template": "Tidak ada pesanan yang terdampak.",
            }

        # Each order goes to exactly one place: the disrupted machine once it
        # resumes, an eligible safe machine after the work already on it, or
        # late (pays its SLA penalty). Times are tenths of an hour from 07:00.
        start10 = {machine_id: round(self._resume * 10)}
        start10.update({m: round(h * 10) for m, h in self._booked.items()})
        dur10 = [max(1, round(self._duration_h(o) * 10)) for o in affected]
        options = [[machine_id] * self._own_ok + self._eligible[o["id"]] for o in affected]

        model = cp_model.CpModel()
        assign = {(i, m): model.NewBoolVar(f"x_{i}_{m}") for i, opts in enumerate(options) for m in opts}
        late = [model.NewBoolVar(f"late_{i}") for i in range(len(affected))]
        for i, opts in enumerate(options):
            model.AddExactlyOne([assign[i, m] for m in opts] + [late[i]])

        cost = []
        for m in start10:
            on_m = [(i, v) for (i, mm), v in assign.items() if mm == m]
            if not on_m:
                continue
            load10 = start10[m] + sum(v * dur10[i] for i, v in on_m)
            # Capacity: the machine's existing work plus anything moved here ends by 23:00.
            model.Add(load10 <= OVERTIME_END_H * 10)
            # Whole overtime hours past 15:00, minus those the existing work already needed.
            base_ot = _ot_hours(start10[m])
            ot_h = model.NewIntVar(base_ot, base_ot + OVERTIME_END_H, f"ot_{m}")
            model.Add(ot_h * 10 >= load10 - SHIFT_END_H * 10)
            cost.append((ot_h - base_ot) * self._cost_ot)
            if m != machine_id:
                cost.extend(v * self._cost_co for _, v in on_m)
        for i, o in enumerate(affected):
            cost.append(late[i] * self._order_penalty(o, disruption_hours))
        model.Minimize(sum(cost))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 5.0
        solved = solver.Solve(model) in (cp_model.OPTIMAL, cp_model.FEASIBLE)

        placed: dict[str, list[int]] = {}
        late_ids = []
        for i in range(len(affected)):
            target = next((m for m in options[i] if solved and solver.Value(assign[i, m])), None)
            if target is None:
                late_ids.append(i)
            else:
                placed.setdefault(target, []).append(i)

        gantt_changes, ot_total, co_total, sla_total = [], 0, 0, 0
        for m, idxs in placed.items():
            t10 = start10[m]
            for i in sorted(idxs, key=lambda i: self._priority_key(affected[i])):
                end10 = t10 + dur10[i]
                ot = (_ot_hours(end10) - _ot_hours(t10)) * self._cost_ot
                co = 0 if m == machine_id else self._cost_co
                ot_total, co_total = ot_total + ot, co_total + co
                gantt_changes.append({
                    "order_id":      affected[i]["id"],
                    "from_machine":  machine_id,
                    "to_machine":    m,
                    "new_start_time": f"{7 + t10 // 10:02d}:00 WIB",
                    "new_end_time":   f"{7 + -(-end10 // 10):02d}:00 WIB",
                    "changeover_cost_idr": co,
                    "overtime_cost_idr": ot,
                })
                t10 = end10
        for i in late_ids:
            change = self._late_change(affected[i], machine_id, disruption_hours)
            sla_total += change["sla_penalty_idr"]
            gantt_changes.append(change)

        total_cost = ot_total + co_total + sla_total
        avoided = self._total_penalty(affected, disruption_hours)
        moved = sum(1 for c in gantt_changes if c["to_machine"] != machine_id)
        return {
            "id": "scenario_c", "name_en": "Optimal Reroute",
            "name_id": "Rerute Optimal", "recommended": False,
            "total_cost_idr": total_cost,
            "net_savings_idr": avoided - total_cost,
            "cost_breakdown": {"sla": sla_total, "overtime": ot_total, "changeover": co_total, "freight": 0},
            "affected_orders": [o["id"] for o in affected],
            "gantt_changes": gantt_changes,
            "rationale_template": (
                f"CP-SAT menemukan alokasi optimal untuk {len(affected)} pesanan dari {machine_id}: "
                f"{moved} dipindah, hanya ke mesin yang mampu dan masih punya jam kosong setelah "
                "order yang sudah ada. "
                + (f"{len(late_ids)} pesanan tetap terlambat karena tidak ada tempat. " if late_ids else "")
                + f"Biaya: Rp {total_cost:,.0f}. "
                f"Denda dihindari: Rp {avoided - sla_total:,.0f}. "
                f"Penghematan bersih: Rp {avoided - total_cost:,.0f}. "
                "Pesanan diprioritaskan: Kelas A > B > C."
            ),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _order_penalty(self, order, delay_hours):
        cls = order.get("order_class") if order.get("order_class") in ("A", "B", "C") else "C"
        return min(delay_hours * self._cost_pph[cls], self._cost_mp[cls])

    def _late_change(self, order, machine_id, delay_hours):
        """An order that stays on the disrupted machine and runs late, paying its SLA penalty."""
        dur = self._duration_h(order)
        return {
            "order_id":      order["id"],
            "from_machine":  machine_id,
            "to_machine":    machine_id,
            "new_start_time": f"{7 + self._resume:02d}:00 WIB",
            "new_end_time":   f"{min(7 + self._resume + int(dur), 15):02d}:00 WIB",
            "delay_hours":   delay_hours,
            "sla_penalty_idr": self._order_penalty(order, delay_hours),
            "changeover_cost_idr": 0,
            "overtime_cost_idr": 0,
            "late": True,
        }

    @staticmethod
    def _priority_key(order):
        return (CLASS_RANK.get(order.get("order_class"), 3), str(order.get("planned_start") or ""), order["id"])

    def _by_priority(self, orders):
        return sorted(orders, key=self._priority_key)

    @staticmethod
    def _duration_h(order):
        return duration_h(order)
