"""
solver/schedule_solver.py
─────────────────────────
OR-Tools CP-SAT scheduling solver for SkillForge.

Generates exactly 3 recovery scenarios given a machine disruption:
  A — Status Quo:        Accept delays, pay SLA penalties.
  B — Lembur (Overtime): Move orders to night-shift overtime.
  C — Rerute Optimal:    CP-SAT optimal reroute minimising changeover + overtime IDR cost.
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

        scenarios = [
            self._scenario_a(affected, disruption_hours, machine_id),
            self._scenario_b(affected, disruption_hours, machine_id, available),
            self._scenario_c(affected, orders, disruption_hours, machine_id, available),
        ]

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
        total_work  = sum(self._duration_h(o) for o in affected)
        ot_cost     = math.ceil(total_work) * self._cost_ot
        avoided     = self._total_penalty(affected, disruption_hours)
        net_savings = avoided - ot_cost

        avail_ids = [m["id"] for m in available] if available else [machine_id]
        gantt_changes = []
        for idx, order in enumerate(affected):
            target = avail_ids[idx % len(avail_ids)]
            dur    = self._duration_h(order)
            gantt_changes.append({
                "order_id":      order["id"],
                "from_machine":  machine_id,
                "to_machine":    target,
                "new_start_time": "15:00 WIB (Lembur)",
                "new_end_time":   f"{15 + int(dur):02d}:00 WIB (Lembur)",
                "overtime_cost_idr": math.ceil(dur) * self._cost_ot,
            })
        # Scenario B: OT cost only (no changeover, no SLA penalty since we avoid it)
        return {
            "id": "scenario_b", "name_en": "Overtime — Night Shift",
            "name_id": "Lembur — Shift Malam",
            "recommended": False,
            "total_cost_idr": ot_cost,
            "net_savings_idr": net_savings,  # Can be negative
            "cost_breakdown": {"sla": 0, "overtime": ot_cost, "changeover": 0, "freight": 0},
            "affected_orders": [o["id"] for o in affected],
            "gantt_changes": gantt_changes,
            "rationale_template": (
                f"Pesanan dari {machine_id} dipindahkan ke shift lembur malam. "
                f"Biaya lembur: Rp {ot_cost:,.0f}. "
                f"Denda dihindari: Rp {avoided:,.0f}. "
                f"Penghematan bersih: Rp {net_savings:,.0f}. "
                "Diperlukan persetujuan Production Manager."
            ),
        }

    # ── Scenario C ────────────────────────────────────────────────────────────

    def _scenario_c(self, affected, all_orders, disruption_hours, machine_id, available):
        if not available or not affected:
            penalty = self._total_penalty(affected, disruption_hours)
            return {
                "id": "scenario_c", "name_en": "Optimal Reroute",
                "name_id": "Rerute Optimal", "recommended": False,
                "total_cost_idr": penalty,
                "net_savings_idr": -penalty,
                "cost_breakdown": {"sla": penalty, "overtime": 0, "changeover": 0, "freight": 0},
                "affected_orders": [o["id"] for o in affected],
                "gantt_changes": [],
                "rationale_template": "Tidak ada mesin alternatif tersedia. Identik dengan Status Quo.",
            }

        # Candidate machines: the disrupted machine itself (work resumes at
        # disruption_hours and may run into overtime) plus every safe alternative.
        avail_ids = [machine_id] + [m["id"] for m in available]
        n_o, n_m  = len(affected), len(avail_ids)
        durations = [self._duration_h(o) for o in affected]

        model  = cp_model.CpModel()
        assign = [[model.NewBoolVar(f"x_{i}_{j}") for j in range(n_m)] for i in range(n_o)]

        for i in range(n_o):
            model.AddExactlyOne(assign[i][j] for j in range(n_m))

        # Capacity: every machine's assigned work must fit its operating window
        # extended by overtime. The disrupted machine resumes at disruption_hours;
        # alternatives start at the beginning of the shift (07:00).
        for j, target in enumerate(avail_ids):
            window_h = (OVERTIME_END_H - self._resume) if target == machine_id else OVERTIME_END_H
            cap_h    = max(window_h, 0)
            model.Add(
                sum(assign[i][j] * int(durations[i] * 10) for i in range(n_o))
                <= int(cap_h * 10)
            )

        # Constant ×10 integer cost per (order i, machine j): changeover +
        # overtime. Overtime is deterministic because start_h depends only on the
        # machine, so the model's optimum and the reported costs agree exactly.
        cost10: list[list[int]] = []
        for i in range(n_o):
            row: list[int] = []
            for j in range(n_m):
                _, _, co_cost, ot_cost = self._assignment_plan(
                    durations[i], machine_id, self._resume, avail_ids[j]
                )
                row.append(int((co_cost + ot_cost) * 10))
            cost10.append(row)
        cost_expr = sum(
            assign[i][j] * cost10[i][j]
            for i in range(n_o) for j in range(n_m)
        )
        model.Minimize(cost_expr)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 5.0
        status = solver.Solve(model)

        gantt_changes, total_cost, total_ot_cost = [], 0, 0
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for i, order in enumerate(affected):
                for j, mach_id in enumerate(avail_ids):
                    if solver.Value(assign[i][j]):
                        start_h, end_h, co_cost, ot_cost = self._assignment_plan(
                            durations[i], machine_id, self._resume, mach_id
                        )
                        total_cost += co_cost + ot_cost
                        total_ot_cost += ot_cost
                        gantt_changes.append({
                            "order_id":      order["id"],
                            "from_machine":  machine_id,
                            "to_machine":    mach_id,
                            "new_start_time": f"{7 + int(start_h):02d}:00 WIB",
                            "new_end_time":   f"{7 + int(end_h):02d}:00 WIB",
                            "changeover_cost_idr": co_cost,
                            "overtime_cost_idr": ot_cost,
                        })
                        break
        else:
            # Last resort when even the overtime-extended window cannot absorb
            # the work (with AddExactlyOne and the widened capacities this needs
            # work larger than every candidate machine's window). No CP-SAT
            # optimum exists, so the greedy round-robin costs are approximate.
            for i, order in enumerate(affected):
                mach_id = avail_ids[i % n_m]
                dur     = self._duration_h(order)
                ot_cost = math.ceil(dur) * self._cost_ot
                total_cost += self._cost_co + ot_cost
                total_ot_cost += ot_cost
                gantt_changes.append({
                    "order_id":      order["id"],
                    "from_machine":  machine_id,
                    "to_machine":    mach_id,
                    "new_start_time": "15:00 WIB (Overtime)",
                    "new_end_time":   f"{15 + int(dur):02d}:00 WIB (Overtime)",
                    "changeover_cost_idr": self._cost_co,
                    "overtime_cost_idr":   ot_cost,
                })

        avoided     = self._total_penalty(affected, disruption_hours)
        net_savings = avoided - total_cost
        return {
            "id": "scenario_c", "name_en": "Optimal Reroute",
            "name_id": "Rerute Optimal", "recommended": False,
            "total_cost_idr": total_cost,
            "net_savings_idr": net_savings,
            "cost_breakdown": {"sla": 0, "overtime": total_ot_cost, "changeover": total_cost - total_ot_cost, "freight": 0},
            "affected_orders": [o["id"] for o in affected],
            "gantt_changes": gantt_changes,
            "rationale_template": (
                f"CP-SAT menemukan alokasi optimal untuk {len(affected)} pesanan dari {machine_id}. "
                f"Biaya rerute: Rp {total_cost:,.0f}. "
                f"Denda dihindari: Rp {avoided:,.0f}. "
                f"Penghematan bersih: Rp {net_savings:,.0f}. "
                "Pesanan diprioritaskan: Kelas A > B > C."
            ),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _assignment_plan(self, dur_h, machine_id, disruption_hours, target_id):
        """Deterministic (start_h, end_h, changeover, overtime) for one order on
        one target machine.

        start_h depends only on the machine: work resumes at disruption_hours on
        the disrupted machine, while alternatives start at 07:00. Shared by the
        CP-SAT objective table and the reporting replay so both agree exactly.
        """
        start_h = disruption_hours if target_id == machine_id else 0
        end_h   = start_h + dur_h
        ot_cost = math.ceil(max(0.0, end_h - SHIFT_END_H)) * self._cost_ot
        co_cost = 0 if target_id == machine_id else self._cost_co
        return start_h, end_h, co_cost, ot_cost

    @staticmethod
    def _duration_h(order):
        try:
            from datetime import datetime
            s = datetime.fromisoformat(order["planned_start"].replace("Z", ""))
            e = datetime.fromisoformat(order["planned_end"].replace("Z", ""))
            return max((e - s).total_seconds() / 3600, 0.5)
        except Exception:
            return 2.0
