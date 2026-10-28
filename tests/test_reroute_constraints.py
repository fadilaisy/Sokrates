"""
tests/test_reroute_constraints.py
─────────────────────────────────
A reroute must only use a machine that can make the part and still has time
after the work already on it. The solver enforces it, and /api/approve checks
it again on live SAP, so a stale or invalid plan cannot be written.
"""

import copy

import pytest
from fastapi.testclient import TestClient

from backend import main
from solver.schedule_solver import (
    OVERTIME_END_H, ScheduleSolver, booked_hours, reroute_violations,
)

client = TestClient(main.app)
VMC, FIVE_AXIS, LATHE = "Vertical Machining Center", "5-Axis Machining Center", "CNC Turning Center"


def _order(oid, wc, start, end, cls="A", **extra):
    return {"id": oid, "order_class": cls, "work_center_id": wc, "status": "QUEUED",
            "planned_start": f"2025-01-15T{start}:00:00", "planned_end": f"2025-01-15T{end}:00:00", **extra}


def _state(machines, orders):
    return {"work_centers": [{"id": i, "machine_type": t, "status": "RUNNING", **x} for i, t, x in machines],
            "production_orders": orders}


def _solve(state, end_hour=8):
    return {s["id"]: s for s in ScheduleSolver().solve(
        state, {"machine_id": "CNC-01", "disruption_type": "BREAKDOWN", "start_hour": 0, "end_hour": end_hour})}


def _moves(scenario):
    return {c["order_id"]: c["to_machine"] for c in scenario["gantt_changes"] if c["to_machine"] != c["from_machine"]}


def test_full_machine_is_never_a_reroute_target():
    """CNC-02 is booked 07:00-23:00, so nothing may move there even though it is RUNNING and the same type."""
    state = _state([("CNC-01", VMC, {}), ("CNC-02", VMC, {})], [
        _order("WO-MOVE", "CNC-01", "07", "13"),
        _order("WO-FULL1", "CNC-02", "07", "15"),
        _order("WO-FULL2", "CNC-02", "07", "15"),
    ])
    for sc in _solve(state).values():
        assert "CNC-02" not in _moves(sc).values()
        assert reroute_violations(state, sc["gantt_changes"]) == []
    checks = {c["machine_id"]: c for c in _solve(state)["scenario_c"]["machine_checks"]}
    assert checks["CNC-02"]["usable"] is False and checks["CNC-02"]["booked_hours"] == 16


def test_moved_work_starts_after_the_work_already_there():
    state = _state([("CNC-01", VMC, {}), ("CNC-02", VMC, {})], [
        _order("WO-MOVE", "CNC-01", "07", "09"),
        _order("WO-THERE", "CNC-02", "07", "12"),     # 5 h already on CNC-02
    ])
    sc = _solve(state, end_hour=8)["scenario_c"]
    change = next(c for c in sc["gantt_changes"] if c["order_id"] == "WO-MOVE")
    assert change["to_machine"] == "CNC-02"
    assert change["new_start_time"] == "12:00 WIB"   # not 07:00, on top of WO-THERE


def test_machine_that_cannot_make_the_part_is_never_used():
    """An idle lathe is not a home for a milling job; a 5-axis job never goes to a 3-axis VMC."""
    state = _state([("CNC-01", VMC, {}), ("CNC-05", LATHE, {})], [_order("WO-MILL", "CNC-01", "07", "11")])
    for sc in _solve(state).values():
        assert "CNC-05" not in _moves(sc).values()

    state = _state([("CNC-01", FIVE_AXIS, {}), ("CNC-02", VMC, {})], [_order("WO-5AX", "CNC-01", "07", "11")])
    for sc in _solve(state).values():
        assert "CNC-02" not in _moves(sc).values()


def test_five_axis_machine_may_take_vmc_work():
    state = _state([("CNC-01", VMC, {}), ("CNC-04", FIVE_AXIS, {})], [_order("WO-MILL", "CNC-01", "07", "11")])
    assert _moves(_solve(state)["scenario_c"]) == {"WO-MILL": "CNC-04"}


def test_order_routing_list_overrides_the_machine_type_rule():
    state = _state([("CNC-01", VMC, {}), ("CNC-02", VMC, {}), ("CNC-04", FIVE_AXIS, {})],
                   [_order("WO-R", "CNC-01", "07", "11", eligible_work_centers=["CNC-01", "CNC-04"])])
    assert _moves(_solve(state)["scenario_c"]) == {"WO-R": "CNC-04"}


def test_planned_maintenance_window_counts_as_booked_time():
    pm = [{"id": "PM-1", "start": "2025-01-15T07:00:00", "end": "2025-01-15T23:00:00"}]
    state = _state([("CNC-01", VMC, {}), ("CNC-02", VMC, {"planned_maintenance_windows": pm})],
                   [_order("WO-MOVE", "CNC-01", "07", "09")])
    assert booked_hours(state, "CNC-02") == OVERTIME_END_H
    assert "CNC-02" not in _moves(_solve(state)["scenario_c"]).values()


def test_when_nothing_fits_the_order_is_shown_late_with_its_penalty():
    """No room anywhere: the plan says so and prices the delay, instead of overbooking a machine."""
    state = _state([("CNC-01", VMC, {}), ("CNC-02", VMC, {})], [
        _order("WO-BIG", "CNC-01", "07", "19", cls="C"),     # 12 h: cannot fit 15:00-23:00 on CNC-01
        _order("WO-FULL", "CNC-02", "07", "23"),
    ])
    sc = _solve(state)["scenario_c"]
    change = sc["gantt_changes"][0]
    assert change["late"] is True and change["to_machine"] == "CNC-01"
    assert sc["cost_breakdown"]["sla"] == change["sla_penalty_idr"] > 0


def test_violations_catch_a_hand_written_overload_and_wrong_machine():
    state = _state([("CNC-01", VMC, {}), ("CNC-02", VMC, {}), ("CNC-05", LATHE, {})], [
        _order("WO-MOVE", "CNC-01", "07", "13"),
        _order("WO-THERE", "CNC-02", "07", "19"),         # 12 h already
    ])
    plan = [{"order_id": "WO-MOVE", "from_machine": "CNC-01", "to_machine": "CNC-02"}]
    assert [v["error"] for v in reroute_violations(state, plan)] == ["CapacityViolation"]
    plan = [{"order_id": "WO-MOVE", "from_machine": "CNC-01", "to_machine": "CNC-05"}]
    assert [v["error"] for v in reroute_violations(state, plan)] == ["CapabilityViolation"]


def test_demo_cnc01_plan_does_not_overload_cnc02():
    """The finals demo: CNC-02 already has 13 h of work, so CNC-01's 6 h order cannot go there."""
    state = main._sap.get_state()
    for sc in ScheduleSolver().solve(state, {"machine_id": "CNC-01", "disruption_type": "BREAKDOWN",
                                             "start_hour": 0, "end_hour": 8}):
        assert reroute_violations(state, sc["gantt_changes"]) == []


# ── /api/approve re-checks on live SAP ──────────────────────────────────────

def _issued_move():
    client.post("/api/state/reset")
    r = client.post("/api/disrupt", json={"machine_id": "CNC-02", "disruption_type": "BREAKDOWN",
                                          "start_hour": 0, "end_hour": 8}).json()
    sc = next((s for s in r["scenarios"] if _moves(s)), None)
    if sc is None:
        pytest.fail("expected a scenario that moves an order off CNC-02")
    return r, sc


@pytest.mark.parametrize("break_target,error", [
    ("overload", "CapacityViolation"),
    ("wrong_type", "CapabilityViolation"),
])
def test_approve_refuses_a_plan_that_no_longer_fits(break_target, error):
    r, sc = _issued_move()
    target = next(iter(_moves(sc).values()))
    with main._sap._lock:
        live = main._sap._state
        if break_target == "overload":
            live["production_orders"].append(_order("WO-RUSH", target, "07", "23"))
        else:
            next(w for w in live["work_centers"] if w["id"] == target)["machine_type"] = LATHE
    res = client.post("/api/approve", json={"scenario_id": sc["id"], "scenario_data": sc,
                                            "expected_sap_version": r["sap_version"], "approved_by": "Tester"})
    last = main._ledger.get_all()[-1]
    client.post("/api/state/reset")
    assert res.status_code == 409
    assert res.json()["detail"]["error"] == error
    assert last["action_type"] == "APPROVAL_REJECTED_CONSTRAINT"
    assert last["violations"][0]["error"] == error


def test_machine_waiting_days_for_a_part_does_not_take_its_own_work_tonight():
    """If the bearing arrives in 2 days, CNC-01 cannot run its order in tonight's overtime."""
    state = _state([("CNC-01", VMC, {}), ("CNC-02", VMC, {})], [
        _order("WO-MOVE", "CNC-01", "07", "13"),
        _order("WO-THERE", "CNC-02", "07", "15"),
    ])
    disruption = {"machine_id": "CNC-01", "disruption_type": "BREAKDOWN", "start_hour": 0, "end_hour": 8}
    back = {s["id"]: s for s in ScheduleSolver().solve(state, disruption)}
    away = {s["id"]: s for s in ScheduleSolver().solve(state, {**disruption, "machine_back_today": False})}
    assert back["scenario_b"]["gantt_changes"][0]["to_machine"] == "CNC-01"   # repaired: own overtime
    for sc in (away["scenario_b"], away["scenario_c"]):
        c = sc["gantt_changes"][0]
        assert c["to_machine"] == "CNC-02" or c.get("late") is True
