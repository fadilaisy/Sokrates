"""
tests/test_solver.py
────────────────────
Tests for the CP-SAT scheduler in solver/schedule_solver.py.
"""

import pytest
from solver.schedule_solver import ScheduleSolver, PENALTY_PER_HOUR, MAX_PENALTY


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
