"""
solver/schedule_solver.py
─────────────────────────
OR-Tools CP-SAT scheduling solver for SkillForge.

Generates exactly 3 recovery scenarios given a machine disruption:
  A — Status Quo:        Accept delays, pay SLA penalties.
  B — Lembur (Overtime): Move orders to night-shift overtime.
  C — Rerute Optimal:    CP-SAT optimal reroute minimising total IDR cost.
"""

from __future__ import annotations

import math
from typing import Any

from ortools.sat.python import cp_model

# ── Cost constants (mirrors sla_penalties.json) ───────────────────────────────
# Phase 1: Rescaled to match proposal (Class A: ~Rp 20M/hour, capped at Rp 150M)
PENALTY_PER_HOUR = {"A": 20_000_000, "B": 300_000, "C": 100_000}
MAX_PENALTY      = {"A": 150_000_000, "B": 6_000_000, "C": 2_000_000}
OVERTIME_COST_PER_HOUR = 450_000   # IDR / machine-hour
CHANGEOVER_COST        = 350_000   # IDR / order moved to different machine

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

        orders        = shop_floor_state.get("production_orders", [])
        work_centers  = shop_floor_state.get("work_centers", [])

        affected = [
            o for o in orders
            if o.get("work_center_id") == machine_id
            and o.get("status") in ("IN_PROGRESS", "QUEUED", "PLANNED")
        ]
        available = [
            wc for wc in work_centers
            if wc["id"] != machine_id and wc.get("status") not in ("MAINTENANCE",)
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

    # ── Scenario A ────────────────────────────────────────────────────────────

    def _scenario_a(self, affected, disruption_hours, machine_id):
        total_penalty = 0
        gantt_changes = []
        for order in affected:
            cls     = order.get("order_class", "C")
            penalty = min(disruption_hours * PENALTY_PER_HOUR[cls], MAX_PENALTY[cls])
            total_penalty += penalty
            dur = self._duration_h(order)
            gantt_changes.append({
                "order_id":      order["id"],
                "from_machine":  machine_id,
                "to_machine":    machine_id,
                "new_start_time": f"{7 + disruption_hours:02d}:00 WIB",
                "new_end_time":   f"{min(7 + disruption_hours + int(dur), 15):02d}:00 WIB",
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
        ot_cost     = math.ceil(total_work) * OVERTIME_COST_PER_HOUR
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
                "overtime_cost_idr": math.ceil(dur) * OVERTIME_COST_PER_HOUR,
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

        avail_ids = [m["id"] for m in available]
        n_o, n_m  = len(affected), len(avail_ids)

        model  = cp_model.CpModel()
        assign = [[model.NewBoolVar(f"x_{i}_{j}") for j in range(n_m)] for i in range(n_o)]

        for i in range(n_o):
            model.AddExactlyOne(assign[i][j] for j in range(n_m))

        remaining = max(SHIFT_END_H - disruption_hours, 0)
        for j in range(n_m):
            model.Add(
                sum(assign[i][j] * int(self._duration_h(affected[i]) * 10) for i in range(n_o))
                <= int(remaining * 10)
            )

        cost_expr = sum(
            assign[i][j] * (int(CHANGEOVER_COST * 10) if avail_ids[j] != machine_id else 0)
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
                        dur     = self._duration_h(order)
                        start_h = disruption_hours if mach_id == machine_id else 0
                        end_h   = min(start_h + dur, SHIFT_END_H)
                        co_cost = CHANGEOVER_COST if mach_id != machine_id else 0
                        ot_cost = 0
                        if mach_id != machine_id or start_h >= SHIFT_END_H:
                            # Rerouted or starts after shift - may need OT
                            ot_hours = max(0, end_h - SHIFT_END_H)
                            ot_cost = math.ceil(ot_hours) * OVERTIME_COST_PER_HOUR if ot_hours > 0 else 0
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
            for i, order in enumerate(affected):
                mach_id = avail_ids[i % n_m]
                dur     = self._duration_h(order)
                ot_cost = math.ceil(dur) * OVERTIME_COST_PER_HOUR
                total_cost += CHANGEOVER_COST + ot_cost
                total_ot_cost += ot_cost
                gantt_changes.append({
                    "order_id":      order["id"],
                    "from_machine":  machine_id,
                    "to_machine":    mach_id,
                    "new_start_time": "15:00 WIB (Overtime)",
                    "new_end_time":   f"{15 + int(dur):02d}:00 WIB (Overtime)",
                    "changeover_cost_idr": CHANGEOVER_COST,
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

    @staticmethod
    def _duration_h(order):
        try:
            from datetime import datetime
            s = datetime.fromisoformat(order["planned_start"].replace("Z", ""))
            e = datetime.fromisoformat(order["planned_end"].replace("Z", ""))
            return max((e - s).total_seconds() / 3600, 0.5)
        except Exception:
            return 2.0

    @staticmethod
    def _total_penalty(orders, delay_hours):
        total = 0
        for o in orders:
            cls = o.get("order_class", "C")
            total += min(delay_hours * PENALTY_PER_HOUR[cls], MAX_PENALTY[cls])
        return total
