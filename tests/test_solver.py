"""
tests/test_solver.py
────────────────────
Tests for the CP-SAT scheduler in solver/schedule_solver.py.
"""

import math

import pytest

from erp_adapter.sap_client import SAPClient
from solver.schedule_solver import (
    ScheduleSolver, PENALTY_PER_HOUR, MAX_PENALTY, SHIFT_END_H, OVERTIME_END_H,
)


@pytest.fixture
def solver():
    return ScheduleSolver()


@pytest.fixture
def sample_state():
    """Sample shop floor state for testing."""
    return {
        "_meta": {"version": 1},
        "work_centers": [
            {"id": "CNC-01", "name": "Machine 1", "status": "RUNNING", "capacity_hours_per_day": 8},
            {"id": "CNC-02", "name": "Machine 2", "status": "RUNNING", "capacity_hours_per_day": 8},
            {"id": "CNC-03", "name": "Machine 3", "status": "MAINTENANCE", "capacity_hours_per_day": 8},
        ],
        "production_orders": [
            {
                "id": "WO-001",
                "order_class": "A",
                "work_center_id": "CNC-02",
                "planned_start": "2025-01-15T07:00:00",
                "planned_end": "2025-01-15T12:00:00",
                "deadline": "2025-01-15T15:00:00",
                "status": "IN_PROGRESS",
            },
            {
                "id": "WO-002",
                "order_class": "B",
                "work_center_id": "CNC-02",
                "planned_start": "2025-01-15T13:00:00",
                "planned_end": "2025-01-15T17:00:00",
                "deadline": "2025-01-16T12:00:00",
                "status": "QUEUED",
            },
        ],
    }


class TestSolverCosts:
    """Test that solver calculates costs correctly."""

    def test_no_max_zero_rounding(self, solver, sample_state):
        """Test that net_savings can be negative (no max(..., 0) rounding)."""
        disruption = {
            "machine_id": "CNC-02",
            "disruption_type": "BREAKDOWN",
            "start_hour": 1,
            "end_hour": 2,
        }
        scenarios = solver.solve(sample_state, disruption)

        # At least one scenario should have negative net_savings
        assert any(s["net_savings_idr"] < 0 for s in scenarios), \
            "At least one scenario should have negative net_savings"

    def test_recommends_lowest_cost(self, solver, sample_state):
        """Test that the recommended scenario has the lowest total_cost_idr."""
        disruption = {
            "machine_id": "CNC-02",
            "disruption_type": "BREAKDOWN",
            "start_hour": 1,
            "end_hour": 2,
        }
        scenarios = solver.solve(sample_state, disruption)

        # Find recommended and minimum cost scenarios
        recommended = next(s for s in scenarios if s["recommended"])
        min_cost = min(s["total_cost_idr"] for s in scenarios)

        assert recommended["total_cost_idr"] == min_cost, \
            "Recommended scenario should have lowest total_cost_idr"


class TestSolverCostBreakdown:
    """Test that solver includes cost_breakdown in all scenarios."""

    def test_scenario_a_has_cost_breakdown(self, solver, sample_state):
        """Scenario A (status quo) should have cost_breakdown."""
        disruption = {"machine_id": "CNC-02", "disruption_type": "BREAKDOWN", "start_hour": 1, "end_hour": 2}
        scenarios = solver.solve(sample_state, disruption)

        scenario_a = next(s for s in scenarios if s["id"] == "scenario_a")
        assert "cost_breakdown" in scenario_a, "Scenario A should have cost_breakdown"
        assert all(k in scenario_a["cost_breakdown"] for k in ["sla", "overtime", "changeover", "freight"])

    def test_scenario_b_has_cost_breakdown(self, solver, sample_state):
        """Scenario B (overtime) should have cost_breakdown."""
        disruption = {"machine_id": "CNC-02", "disruption_type": "BREAKDOWN", "start_hour": 1, "end_hour": 2}
        scenarios = solver.solve(sample_state, disruption)

        scenario_b = next(s for s in scenarios if s["id"] == "scenario_b")
        assert "cost_breakdown" in scenario_b, "Scenario B should have cost_breakdown"
        assert scenario_b["cost_breakdown"]["sla"] == 0, "Scenario B should have 0 SLA cost (avoided)"


class TestSolverConstraints:
    """Test solver respects constraints."""

    def test_ignores_maintenance_machines(self, solver, sample_state):
        """Solver should not assign work to MAINTENANCE machines."""
        disruption = {"machine_id": "CNC-03", "disruption_type": "BREAKDOWN", "start_hour": 1, "end_hour": 2}
        scenarios = solver.solve(sample_state, disruption)

        scenario_c = next(s for s in scenarios if s["id"] == "scenario_c")
        for change in scenario_c.get("gantt_changes", []):
            assert change["to_machine"] != "CNC-03", "Should not reroute to MAINTENANCE machine"


# ── Finding #6 regression: overtime is a first-class part of scenario C ─────


def test_scenario_c_charges_real_overtime_past_shift_end(solver):
    """Work past 15:00 must carry real overtime cost (finding #6 regression).

    After a 3 h disruption the disrupted machine resumes at 10:00: the 4 h
    order still fits before 15:00 (stays put, no changeover), while the 9 h
    order cannot fit the residual shift but fits the overtime-extended window.
    The cost-optimal plan moves the 9 h order and pays changeover + exactly the
    one hour that really runs past 15:00. The old model capped ends at 15:00
    and charged 0 OT (or dumped the pair into the round-robin fallback).
    """
    state = {
        "work_centers": [
            {"id": "CNC-01", "status": "RUNNING"},
            {"id": "CNC-02", "status": "RUNNING"},
        ],
        "production_orders": [
            {
                "id": "WO-STAY",
                "order_class": "A",
                "work_center_id": "CNC-02",
                "planned_start": "2025-01-15T07:00:00",
                "planned_end": "2025-01-15T11:00:00",  # 4 h
                "status": "QUEUED",
            },
            {
                "id": "WO-OT",
                "order_class": "B",
                "work_center_id": "CNC-02",
                "planned_start": "2025-01-15T07:00:00",
                "planned_end": "2025-01-15T16:00:00",  # 9 h
                "status": "QUEUED",
            },
        ],
    }
    disruption = {"machine_id": "CNC-02", "disruption_type": "BREAKDOWN",
                  "start_hour": 0, "end_hour": 3}
    disruption_hours = disruption["end_hour"] - disruption["start_hour"]

    scenarios = solver.solve(state, disruption)
    sc_c = next(s for s in scenarios if s["id"] == "scenario_c")
    by_order = {c["order_id"]: c for c in sc_c["gantt_changes"]}

    total_work = sum(solver._duration_h(o) for o in state["production_orders"])
    assert total_work > SHIFT_END_H - disruption_hours        # 13 > 5: no fit in the residual shift
    assert total_work <= OVERTIME_END_H - disruption_hours    # 13 <= 13: fits the OT-extended window
    dur_ot = solver._duration_h(state["production_orders"][1])       # 9 h
    expected_ot = math.ceil(dur_ot - SHIFT_END_H) * solver._cost_ot  # 1 real hour beyond 15:00
    expected_total = solver._cost_co + expected_ot                   # moved: changeover + OT

    # Short order stays on the recovering machine at zero changeover/OT.
    assert by_order["WO-STAY"]["to_machine"] == "CNC-02"
    assert by_order["WO-STAY"]["changeover_cost_idr"] == 0
    assert by_order["WO-STAY"]["overtime_cost_idr"] == 0
    # Long order moves and pays exactly the real overtime beyond 15:00.
    assert by_order["WO-OT"]["to_machine"] == "CNC-01"
    assert by_order["WO-OT"]["new_start_time"] == "07:00 WIB"
    assert by_order["WO-OT"]["new_end_time"] == "16:00 WIB"  # not truncated at 15:00
    assert by_order["WO-OT"]["changeover_cost_idr"] == solver._cost_co
    assert by_order["WO-OT"]["overtime_cost_idr"] == expected_ot
    assert sc_c["cost_breakdown"] == {"sla": 0, "overtime": expected_ot,
                                      "changeover": solver._cost_co, "freight": 0}
    assert sc_c["total_cost_idr"] == expected_total
    assert sc_c["total_cost_idr"] == sum(
        c["changeover_cost_idr"] + c["overtime_cost_idr"] for c in sc_c["gantt_changes"]
    )


def test_scenario_c_costs_are_internally_consistent(solver):
    """Scenario C: total == sla + overtime + changeover == sum of per-change costs."""
    state = SAPClient().get_state()
    disruption = {"machine_id": "CNC-02", "disruption_type": "BREAKDOWN",
                  "start_hour": 2, "end_hour": 5}
    scenarios = solver.solve(state, disruption)
    sc_c = next(s for s in scenarios if s["id"] == "scenario_c")

    brk = sc_c["cost_breakdown"]
    assert sc_c["total_cost_idr"] == brk["sla"] + brk["overtime"] + brk["changeover"]
    assert brk["sla"] == sum(c.get("sla_penalty_idr", 0) for c in sc_c["gantt_changes"])

    co_sum = sum(c.get("changeover_cost_idr", 0) for c in sc_c["gantt_changes"])
    ot_sum = sum(c.get("overtime_cost_idr", 0) for c in sc_c["gantt_changes"])
    assert co_sum == brk["changeover"]
    assert ot_sum == brk["overtime"]
    assert co_sum + ot_sum + brk["sla"] == sc_c["total_cost_idr"]


def test_resume_time_uses_end_hour_not_duration(solver):
    """A 2 h outage at 4-6 resumes at 06:00-relative hour 6, not hour 2."""
    state = {
        "work_centers": [{"id": "CNC-01", "status": "RUNNING"}, {"id": "CNC-02", "status": "RUNNING"}],
        "production_orders": [{
            "id": "WO-1", "order_class": "B", "work_center_id": "CNC-02",
            "planned_start": "2025-01-15T07:00:00", "planned_end": "2025-01-15T09:00:00",
            "status": "QUEUED",
        }],
    }
    late = solver.solve(state, {"machine_id": "CNC-02", "disruption_type": "BREAKDOWN",
                                "start_hour": 4, "end_hour": 6})
    early = solver.solve(state, {"machine_id": "CNC-02", "disruption_type": "BREAKDOWN",
                                 "start_hour": 0, "end_hour": 2})
    a_late = next(s for s in late if s["id"] == "scenario_a")["gantt_changes"][0]
    a_early = next(s for s in early if s["id"] == "scenario_a")["gantt_changes"][0]
    assert a_early["new_start_time"] == "09:00 WIB"
    assert a_late["new_start_time"] == "13:00 WIB"
