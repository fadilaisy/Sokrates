"""Biaya & SLA: one row at a time, a second person approves, the solver uses the new value."""

import shutil
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import backend.main as m
import solver.schedule_solver as ss

client = TestClient(m.app)


@pytest.fixture(autouse=True)
def cost_file(tmp_path, monkeypatch):
    path = tmp_path / "sla_penalties.json"
    shutil.copy(ss._SKILL_SLA_PATH, path)
    monkeypatch.setattr(ss, "_SKILL_SLA_PATH", path)
    yield path
    for r in m._cost_requests():  # close what a test left open so the next one starts clean
        if r["status"] in ("pending", "approved"):
            m._ledger.append({"action_type": "COST_CHANGE_REJECTED", "request_id": r["request_id"], "rejected_by": "test"})


def _row(key):
    return next(r for r in client.get("/api/costs").json()["rows"] if r["key"] == key)


def test_rows_show_what_the_solver_reads():
    assert _row("overtime")["value"] == 450_000
    assert _row("penalty_A")["value"] == 20_000_000


def test_change_needs_a_second_person_and_then_reaches_the_solver():
    req = client.post("/api/costs/requests", json={"key": "overtime", "value": 500_000, "requested_by": "Rina"}).json()
    assert req["status"] == "pending" and req["old_value"] == 450_000
    assert _row("overtime")["value"] == 450_000                 # nothing changes before approval
    assert client.post("/api/costs/requests", json={"key": "overtime", "value": 1, "requested_by": "Budi"}).status_code == 409
    assert client.post(f"/api/costs/requests/{req['request_id']}/approve", json={"by": "rina"}).status_code == 403
    done = client.post(f"/api/costs/requests/{req['request_id']}/approve", json={"by": "Ibu Sari"}).json()
    assert done["status"] == "applied"
    assert _row("overtime")["value"] == 500_000
    assert ss._load_skill_costs()[2] == 500_000


def test_future_change_waits_for_its_date():
    later = (date.today() + timedelta(days=5)).isoformat()
    req = client.post("/api/costs/requests", json={"key": "changeover", "value": 400_000, "requested_by": "Rina",
                                                   "effective_from": later}).json()
    done = client.post(f"/api/costs/requests/{req['request_id']}/approve", json={"by": "Ibu Sari"}).json()
    assert done["status"] == "approved"
    assert _row("changeover")["value"] == 350_000
    assert _row("changeover")["open_request"]["effective_from"] == later


def test_rejected_change_is_never_applied():
    req = client.post("/api/costs/requests", json={"key": "penalty_B", "value": 400_000, "requested_by": "Rina"}).json()
    assert client.post(f"/api/costs/requests/{req['request_id']}/reject", json={"by": "Ibu Sari"}).json()["status"] == "rejected"
    assert _row("penalty_B")["value"] == 300_000 and _row("penalty_B")["open_request"] is None
