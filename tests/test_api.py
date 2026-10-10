"""tests/test_api.py — Phase 6: backend API contract tests (TestClient, no server needed)."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

DISRUPTION = {"machine_id": "CNC-02", "disruption_type": "BREAKDOWN", "start_hour": 2, "end_hour": 5}


@pytest.fixture(autouse=True)
def reset_state():
    client.post("/api/state/reset")
    yield


def _ledger_types():
    return [e.get("action_type") for e in client.get("/api/ledger").json()]


def test_health_and_state():
    h = client.get("/health").json()
    assert h["status"] == "ok"
    state = client.get("/api/state").json()
    assert len(state["work_centers"]) == 5
    assert len(state["production_orders"]) == 8


def test_disrupt_returns_three_scenarios_and_logs():
    before = len(client.get("/api/ledger").json())
    r = client.post("/api/disrupt", json=DISRUPTION)
    assert r.status_code == 200
    body = r.json()
    assert len(body["scenarios"]) == 3
    rec = [s for s in body["scenarios"] if s["recommended"]]
    assert len(rec) == 1
    assert rec[0]["total_cost_idr"] == min(s["total_cost_idr"] for s in body["scenarios"])
    # Phase 5: analysis itself is audited.
    types = _ledger_types()[before:]
    assert "DISRUPTION_DETECTED" in types
    assert "SCENARIOS_GENERATED" in types


def test_inject_mutates_state_and_stale_approve_conflicts():
    d = client.post("/api/disrupt", json=DISRUPTION).json()
    inj = client.post("/api/disrupt/inject", json={
        "machine_id": "CNC-03", "new_status": "FAULT", "disruption_type": "MOTOR_OVERLOAD",
    })
    assert inj.status_code == 200
    assert inj.json()["sap_version_after"] == inj.json()["sap_version_before"] + 1
    state = client.get("/api/state").json()
    assert next(w for w in state["work_centers"] if w["id"] == "CNC-03")["status"] == "FAULT"
    assert "EXTERNAL_CHANGE" in _ledger_types()
    # Approving the pre-inject analysis version must now 409.
    sc = next(s for s in d["scenarios"] if s["recommended"])
    r = client.post("/api/approve", json={
        "scenario_id": sc["id"], "scenario_data": sc,
        "expected_sap_version": d["sap_version"], "approved_by": "Tester",
    })
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "DriftError"


def test_revert_roundtrip_keeps_chain_valid():
    d = client.post("/api/disrupt", json=DISRUPTION).json()
    sc = next(s for s in d["scenarios"] if s["recommended"])
    client.post("/api/approve", json={
        "scenario_id": sc["id"], "scenario_data": sc,
        "expected_sap_version": d["sap_version"], "approved_by": "Tester",
    })
    approved = [e for e in client.get("/api/ledger").json() if e.get("action_type") == "SCENARIO_APPROVED"][-1]
    r = client.post(f"/api/ledger/{approved['id']}/revert",
                    json={"id": approved["id"], "approved_by": "Tester"})
    assert r.status_code == 200, r.text[:300]
    assert "SCENARIO_REVERTED" in _ledger_types()
    v = client.get("/api/ledger/verify").json()
    assert v["valid"] is True


def test_chat_replies_without_changing_state():
    """Hybrid fallback: /api/chat answers text-only, SAP version untouched."""
    v0 = client.get("/api/state").json()["_meta"]["version"]
    r = client.post("/api/chat", json={"message": "Mesin mana yang butuh perhatian?"})
    assert r.status_code == 200
    assert isinstance(r.json()["reply"], str) and len(r.json()["reply"]) > 0
    assert client.get("/api/state").json()["_meta"]["version"] == v0


def test_skill_lint_rejects_relaxed_tier1():    # Draft with a relaxed Tier 1 threshold must come back as validation errors.
    r = client.post("/api/skills/draft", json={
        "machine_type": "Test Mill", "failure_mode": "Overheat",
        "sla_class_a_penalty_per_hour_idr": 20_000_000,
        "sla_class_b_penalty_per_hour_idr": 300_000,
        "sla_class_c_penalty_per_hour_idr": 100_000,
        "overtime_cost_per_hour_idr": 450_000, "changeover_cost_idr": 350_000,
        "safety_thresholds": {"motor_temp_celsius": 120, "spindle_vibration_mm_per_s": 8, "coolant_pressure_bar": 2},
    })
    assert r.status_code == 200
    assert any("Tier 1" in e for e in r.json()["validation_errors"])
    # Lint must reject override_allowed on Tier 1 and relaxed thresholds.
    bad = client.post("/api/skills/lint", json={
        "skill_md": "x", "sla_penalties": {},
        "hooks": [],
        "interlocks": [{"id": "SAFE-001", "tier": 1, "condition": "motor_temp_celsius > 120",
                        "threshold_value": 120, "override_allowed": True}],
    }).json()
    assert bad["valid"] is False
    assert len(bad["errors"]) >= 2


def test_skill_approve_writes_files_and_reloads_registry():
    skill_id = "test_skill_tmp"
    target = Path("skills") / skill_id
    if target.exists():
        shutil.rmtree(target)
    try:
        # Draft endpoint must never 500, with or without Gemini configured.
        draft = client.post("/api/skills/draft", json={
            "machine_type": "Test Mill", "failure_mode": "Overheat",
            "sla_class_a_penalty_per_hour_idr": 20_000_000,
            "sla_class_b_penalty_per_hour_idr": 300_000,
            "sla_class_c_penalty_per_hour_idr": 100_000,
            "overtime_cost_per_hour_idr": 450_000, "changeover_cost_idr": 350_000,
            "safety_thresholds": {"motor_temp_celsius": 90, "spindle_vibration_mm_per_s": 7, "coolant_pressure_bar": 2.5},
        })
        assert draft.status_code == 200
        # Deterministic approve roundtrip (independent of Gemini output shape).
        payload = {
            "skill_md": "# SKILL.md test",
            "sla_penalties": {"order_classes": {"A": {"penalty_per_hour_idr": 1}}},
            "hooks": [{"id": "HOOK-T1", "trigger_type": "telemetry", "condition": "motor_temp_celsius > 80"}],
            "interlocks": [{"id": "SAFE-T1", "tier": 1, "condition": "motor_temp_celsius > 95",
                            "threshold_value": 95, "override_allowed": False}],
        }
        lint = client.post("/api/skills/lint", json=payload).json()
        assert lint["valid"] is True
        appr = client.post("/api/skills/approve", json={
            "skill_id": skill_id, "approved_by": "Tester", **payload,
        })
        assert appr.status_code == 200
        assert (target / "SKILL.md").exists()
        assert "SKILL_APPROVED" in _ledger_types()
    finally:
        if target.exists():
            shutil.rmtree(target)
        for backup in Path("skills").glob(f"{skill_id}.v*"):
            shutil.rmtree(backup, ignore_errors=True)
