"""tests/test_skill_driven.py — Phase 2: skill files drive solver + telemetry."""

import json
from pathlib import Path

import pytest

from solver.schedule_solver import ScheduleSolver, TIER1_BLOCKED_STATUSES
from erp_adapter.sap_client import SAPClient

SLA_PATH = Path(__file__).parent.parent / "skills" / "cnc_milling" / "rules" / "sla_penalties.json"


@pytest.fixture
def solver():
    return ScheduleSolver()


@pytest.fixture
def live_state():
    return SAPClient().get_state()


DISRUPTION = {"machine_id": "CNC-02", "disruption_type": "BREAKDOWN", "start_hour": 2, "end_hour": 5}


def test_solver_matches_skill_file_costs(solver, live_state):
    """Solver totals must equal what sla_penalties.json prescribes."""
    skill = json.loads(SLA_PATH.read_text(encoding="utf-8"))
    a_rate = skill["order_classes"]["A"]["penalty_per_hour_idr"]
    a_max = skill["order_classes"]["A"]["max_penalty_idr"]
    ot = skill["operational_costs"]["overtime_cost_per_hour_idr"]

    scenarios = solver.solve(live_state, DISRUPTION)
    by_id = {s["id"]: s for s in scenarios}
    # Scenario B cost = ceil(total affected hours) * skill overtime rate.
    total_work = sum(solver._duration_h(o) for o in live_state["production_orders"]
                     if o.get("work_center_id") == "CNC-02"
                     and o.get("status") in ("IN_PROGRESS", "QUEUED", "PLANNED"))
    import math
    assert by_id["scenario_b"]["total_cost_idr"] == math.ceil(total_work) * ot
    assert a_rate > 0 and a_max > 0  # sanity: file was actually read


def test_solver_cost_edit_takes_effect(solver, live_state, tmp_path, monkeypatch):
    """Editing sla_penalties.json changes solver output without code change."""
    skill = json.loads(SLA_PATH.read_text(encoding="utf-8"))
    skill["order_classes"]["A"]["penalty_per_hour_idr"] = 1_000_000
    tmp = tmp_path / "sla_penalties.json"
    tmp.write_text(json.dumps(skill))
    import solver.schedule_solver as mod
    monkeypatch.setattr(mod, "_SKILL_SLA_PATH", tmp)
    scenarios = solver.solve(live_state, DISRUPTION)
    assert scenarios[0]["total_cost_idr"] < 60_900_000  # was 60.9M with 20M/hr


def test_tier1_fault_machines_never_routed(solver, live_state):
    """Tier 1 rule: FAULT (and MAINTENANCE) machines are excluded from reroute."""
    assert "FAULT" in TIER1_BLOCKED_STATUSES
    for w in live_state["work_centers"]:
        if w["id"] == "CNC-04":
            w["status"] = "FAULT"
    scenarios = solver.solve(live_state, DISRUPTION)
    sc_c = next(s for s in scenarios if s["id"] == "scenario_c")
    targets = {c["to_machine"] for c in sc_c["gantt_changes"]}
    assert "CNC-04" not in targets


def test_telemetry_driven_by_skill_files():
    """CRITICAL comes from safety_interlocks.json, WARNING from hooks.json."""
    from backend.main import _classify_telemetry
    assert _classify_telemetry("motor_temp_celsius", 96) == ("CRITICAL", "SAFE-001")
    sev, _ = _classify_telemetry("motor_temp_celsius", 90)
    assert sev == "WARNING"  # HOOK-001 (>85), not hardcoded
    assert _classify_telemetry("coolant_pressure_bar", 1.5) == ("CRITICAL", "SAFE-003")
    # SAFE-004 exists only in the skill file — old hardcoded table returned OK.
    assert _classify_telemetry("emergency_stop_activated", True) == ("CRITICAL", "SAFE-004")
