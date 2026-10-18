"""tests/test_metrics.py — downtime + OEE are computed from live state, not sample numbers."""

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.metrics import compute_metrics, events_for_approval

client = TestClient(app)
STATE_FILE = Path(__file__).resolve().parent.parent / "erp_adapter" / "mock_sap_state.json"
DISRUPTION = {"machine_id": "CNC-02", "disruption_type": "BREAKDOWN", "start_hour": 2, "end_hour": 5}


@pytest.fixture(autouse=True)
def reset_state():
    client.post("/api/state/reset")
    yield


@pytest.fixture
def base_state():
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def _cat(m, key):
    return next(c["hours"] for c in m["downtime"]["by_category"] if c["key"] == key)


# ── Pure computation on the baseline mock ────────────────────────────────────

def test_baseline_downtime_comes_from_maintenance_slots(base_state):
    m = compute_metrics(base_state)
    assert m["shift"]["date"] == "2025-01-15"
    assert m["shift"]["total_hours"] == 40          # 5 machines × 8 h
    # CNC-05 corrective all shift (8 h) + CNC-03 corrective 13:00–15:00 (2 h).
    # CNC-01 preventive PM-001 is on 2025-01-17, outside this shift.
    assert _cat(m, "unplanned") == pytest.approx(10)
    assert _cat(m, "maintenance") == pytest.approx(0)
    assert _cat(m, "changeover") == pytest.approx(0)
    assert m["downtime"]["percent_of_shift"] == pytest.approx(25.0)


def test_baseline_oee(base_state):
    o = compute_metrics(base_state)["oee"]
    assert o["availability"] == pytest.approx(75.0)
    assert o["performance"] == pytest.approx(90.0)  # mean feed rate of RUNNING machines (85, 90, 95)
    assert o["quality"] == pytest.approx(98.0)
    assert o["sources"]["quality"] == "assumption"
    assert o["oee"] == pytest.approx(75 * 90 * 98 / 10_000, abs=0.06)


def test_preventive_maintenance_reduces_planned_time_not_availability(base_state):
    s = copy.deepcopy(base_state)
    s["maintenance_slots"].append({
        "id": "PM-T", "work_center_id": "CNC-04", "type": "PREVENTIVE",
        "start": "2025-01-15T07:00:00", "end": "2025-01-15T09:00:00",
    })
    m = compute_metrics(s)
    assert _cat(m, "maintenance") == pytest.approx(2)
    # Planned time 40 − 2 = 38 h, unplanned still 10 h → (38 − 10) / 38.
    assert m["oee"]["availability"] == pytest.approx(28 / 38 * 100, abs=0.06)


def test_fault_status_counts_whole_shift_once(base_state):
    s = copy.deepcopy(base_state)
    next(w for w in s["work_centers"] if w["id"] == "CNC-03")["status"] = "FAULT"
    m = compute_metrics(s)
    cnc03 = next(x for x in m["downtime"]["by_machine"] if x["machine_id"] == "CNC-03")
    assert cnc03["unplanned_hours"] == pytest.approx(8)  # 13–15 window merged into the full shift
    assert _cat(m, "unplanned") == pytest.approx(16)


def test_events_for_approval_breakdown_and_changeover(base_state):
    sc = {
        "id": "scenario_c",
        "disruption": dict(DISRUPTION),
        "gantt_changes": [
            {"order_id": "WO-2025-002", "from_machine": "CNC-02", "to_machine": "CNC-03", "changeover_cost_idr": 350000},
            {"order_id": "WO-2025-003", "from_machine": "CNC-02", "to_machine": "CNC-02"},
        ],
    }
    ev = events_for_approval(base_state, sc, "2026-10-13T00:00:00+00:00")
    brk = [e for e in ev if e["category"] == "unplanned"]
    co = [e for e in ev if e["category"] == "changeover"]
    assert brk[0]["start"].startswith("2025-01-15T09:00") and brk[0]["end"].startswith("2025-01-15T12:00")
    assert len(co) == 1 and co[0]["machine_id"] == "CNC-03" and co[0]["minutes"] == 30


# ── Through the API ─────────────────────────────────────────────────────────

def _approve(scenario_id=None):
    d = client.post("/api/disrupt", json=DISRUPTION).json()
    sc = next(s for s in d["scenarios"] if (s["id"] == scenario_id if scenario_id else s["recommended"]))
    r = client.post("/api/approve", json={
        "scenario_id": sc["id"], "scenario_data": sc,
        "expected_sap_version": d["sap_version"], "approved_by": "Tester",
    })
    assert r.status_code == 200
    return sc, r.json()


def test_metrics_endpoint_baseline():
    m = client.get("/api/metrics").json()
    assert _cat(m, "unplanned") == pytest.approx(10)
    assert m["oee"]["availability"] == pytest.approx(75.0)


def test_whatif_alone_does_not_add_downtime():
    client.post("/api/disrupt", json=DISRUPTION)
    assert _cat(client.get("/api/metrics").json(), "unplanned") == pytest.approx(10)


def test_approval_records_breakdown_and_reset_clears_it():
    sc = _approve()[0]
    moved = [c for c in sc["gantt_changes"]
             if c.get("to_machine") != c.get("from_machine") and (c.get("changeover_cost_idr") or 0) > 0]
    m = client.get("/api/metrics").json()
    assert _cat(m, "unplanned") == pytest.approx(13)          # + CNC-02 09:00–12:00
    # Availability = (5 machines × 8h − 13h unplanned − 0.5h per moved order) / 40h,
    # derived from the approved plan so it stays true whatever the solver recommends.
    expected_availability = (40 - 13 - 0.5 * len(moved)) / 40 * 100
    assert m["oee"]["availability"] == pytest.approx(expected_availability, abs=0.1)
    client.post("/api/state/reset")
    assert _cat(client.get("/api/metrics").json(), "unplanned") == pytest.approx(10)


def test_reroute_adds_changeover_and_revert_removes_it():
    sc, receipt = _approve("scenario_c")
    moved = [c for c in sc["gantt_changes"]
             if c.get("to_machine") != c.get("from_machine") and (c.get("changeover_cost_idr") or 0) > 0]
    m = client.get("/api/metrics").json()
    assert _cat(m, "changeover") == pytest.approx(0.5 * len(moved))
    rid = receipt["receipt_id"]
    r = client.post(f"/api/ledger/{rid}/revert", json={"id": rid, "approved_by": "Tester"})
    assert r.status_code == 200
    after = client.get("/api/metrics").json()
    assert _cat(after, "changeover") == pytest.approx(0)
    assert _cat(after, "unplanned") == pytest.approx(13)      # the breakdown still happened


def test_drill_fault_shows_up():
    client.post("/api/disrupt/inject", json={
        "machine_id": "CNC-03", "new_status": "FAULT", "disruption_type": "MOTOR_OVERLOAD",
    })
    assert _cat(client.get("/api/metrics").json(), "unplanned") == pytest.approx(16)
