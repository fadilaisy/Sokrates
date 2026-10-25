"""Regression tests for the bug-hunt fixes (Tier 1 lint, approve integrity, revert lock)."""
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main

client = TestClient(main.app)
BASE = json.loads((Path(__file__).parent.parent / "skills/cnc_milling/rules/safety_interlocks.json").read_text("utf-8"))


def _lint(mutate):
    il = copy.deepcopy(BASE)
    mutate(il[0])
    return client.post("/api/skills/lint", json={
        "skill_md": "x", "sla_penalties": {}, "hooks": [], "interlocks": il, "skill_id": "cnc_milling",
    }).json()


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(condition="motor_temp_celsius < 10"),
    lambda r: r.update(condition="motor_temp_celsius > 95 and false"),
    lambda r: r.update(condition="motor_temp_celsius > 95 AND x = 1"),
    lambda r: r.update(action="NONE"),
    lambda r: r.update(condition="motor_temp_celsius > 999"),
])
def test_lint_rejects_tier1_weakening(mutate):
    assert _lint(mutate)["valid"] is False


@pytest.mark.parametrize("mutate", [
    lambda r: None,
    lambda r: r.update(condition="motor_temp_celsius > 90"),
    lambda r: r.update(condition="motor_temp_celsius >= 95"),
])
def test_lint_allows_same_or_stricter(mutate):
    assert _lint(mutate)["valid"] is True


def _disrupt_and_pick():
    client.post("/api/state/reset")
    r = client.post("/api/disrupt", json={"machine_id": "CNC-02", "disruption_type": "BREAKDOWN",
                                          "start_hour": 0, "end_hour": 3}).json()
    return r, next(s for s in r["scenarios"] if s["gantt_changes"])


def test_approve_rejects_forged_scenario():
    r, sc = _disrupt_and_pick()
    forged = copy.deepcopy(sc)
    forged["gantt_changes"][0]["to_machine"] = "CNC-05"   # MAINTENANCE in the mock
    res = client.post("/api/approve", json={"scenario_id": sc["id"], "scenario_data": forged,
                                            "expected_sap_version": r["sap_version"], "approved_by": "Tester"})
    assert res.status_code == 409
    assert res.json()["detail"]["error"] == "ScenarioMismatch"


def test_approve_rejects_unissued_scenario_id():
    r, _ = _disrupt_and_pick()
    res = client.post("/api/approve", json={"scenario_id": "scenario_zzz", "scenario_data": {"gantt_changes": []},
                                            "expected_sap_version": r["sap_version"], "approved_by": "Tester"})
    assert res.status_code == 409


def test_approve_blocks_tier1_target_after_status_change():
    r, sc = _disrupt_and_pick()
    moved = next((c for c in sc["gantt_changes"] if c["to_machine"] != c["from_machine"]), None)
    if moved is None:
        pytest.skip("recommended scenarios did not reroute in this fixture")
    inj = client.post("/api/disrupt/inject", json={"machine_id": moved["to_machine"], "new_status": "FAULT",
                                                   "disruption_type": "MOTOR_OVERLOAD"}).json()
    res = client.post("/api/approve", json={"scenario_id": sc["id"], "scenario_data": sc,
                                            "expected_sap_version": inj["sap_version_after"], "approved_by": "Tester"})
    assert res.status_code == 409
    assert res.json()["detail"]["error"] == "Tier1Violation"


def test_revert_is_single_use_and_detects_drift():
    r, sc = _disrupt_and_pick()
    ok = client.post("/api/approve", json={"scenario_id": sc["id"], "scenario_data": sc,
                                           "expected_sap_version": r["sap_version"], "approved_by": "Tester"})
    assert ok.status_code == 200
    entry = ok.json()["receipt_id"]
    # Another change lands after the approval -> reverting would clobber it.
    client.post("/api/disrupt/inject", json={"machine_id": "CNC-01", "new_status": "IDLE",
                                             "disruption_type": "EXTERNAL"})
    assert client.post(f"/api/ledger/{entry}/revert", json={"id": entry}).status_code == 409

    # Fresh run without drift: first revert works, second is refused.
    r, sc = _disrupt_and_pick()
    ok = client.post("/api/approve", json={"scenario_id": sc["id"], "scenario_data": sc,
                                           "expected_sap_version": r["sap_version"], "approved_by": "Tester"})
    entry = ok.json()["receipt_id"]
    assert client.post(f"/api/ledger/{entry}/revert", json={"id": entry}).status_code == 200
    assert client.post(f"/api/ledger/{entry}/revert", json={"id": entry}).status_code == 409


def test_skill_approve_rejects_malformed_hooks():
    res = client.post("/api/skills/approve", json={
        "skill_id": "zz_bad_shape", "skill_md": "x", "sla_penalties": {},
        "hooks": [{"name": "no id"}], "interlocks": [],
    })
    assert res.status_code == 422
    assert not (Path(main._ROOT) / "skills" / "zz_bad_shape").exists()
