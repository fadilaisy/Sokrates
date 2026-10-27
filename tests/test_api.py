"""tests/test_api.py — Phase 6: backend API contract tests (TestClient, no server needed)."""

import json
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


def test_approve_requires_an_approver_name():
    d = client.post("/api/disrupt", json=DISRUPTION).json()
    sc = next(s for s in d["scenarios"] if s["recommended"])
    r = client.post("/api/approve", json={
        "scenario_id": sc["id"], "scenario_data": sc,
        "expected_sap_version": d["sap_version"], "approved_by": "",
    })
    assert r.status_code == 422
    assert "SCENARIO_APPROVED" not in _ledger_types()[-1:]


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
        # Draft endpoint must never 500, with or without AI Core configured.
        draft = client.post("/api/skills/draft", json={
            "machine_type": "Test Mill", "failure_mode": "Overheat",
            "sla_class_a_penalty_per_hour_idr": 20_000_000,
            "sla_class_b_penalty_per_hour_idr": 300_000,
            "sla_class_c_penalty_per_hour_idr": 100_000,
            "overtime_cost_per_hour_idr": 450_000, "changeover_cost_idr": 350_000,
            "safety_thresholds": {"motor_temp_celsius": 90, "spindle_vibration_mm_per_s": 7, "coolant_pressure_bar": 2.5},
        })
        assert draft.status_code == 200
        # Deterministic approve roundtrip (independent of AI output shape).
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


def test_chat_prompt_includes_maintenance_reason(monkeypatch):
    """The chat prompt must carry the maintenance log, so 'why is CNC-05 in maintenance?' is answerable."""
    import backend.main as m

    captured = {}

    async def fake_llm_reply(prompt, max_tokens=512):
        captured["prompt"] = prompt
        captured["max_tokens"] = max_tokens
        return "CNC-05 sedang penggantian turret servo motor."

    monkeypatch.setattr(m, "_call_llm", fake_llm_reply)
    r = client.post("/api/chat", json={"message": "CNC-05 lagi maintenance karena masalah apa?"})
    assert r.status_code == 200
    p = captured["prompt"]
    assert "CNC-05 [PM-003] CORRECTIVE" in p
    assert "Turret servo motor replacement" in p
    assert "07:00-15:00" in p
    assert "Bambang Susilo" in p
    assert captured["max_tokens"] >= 1024


def test_maintenance_context_falls_back_to_work_center_windows():
    from backend.main import _maintenance_context

    state = {
        "work_centers": [{
            "id": "CNC-09",
            "planned_maintenance_windows": [{
                "id": "PM-X", "type": "PREVENTIVE",
                "start": "2025-01-16T08:00:00", "end": "2025-01-16T09:30:00",
                "description": "Ganti filter coolant",
            }],
        }],
    }
    ctx = _maintenance_context(state)
    assert "CNC-09 [PM-X] PREVENTIVE, 2025-01-16 08:00-09:30: Ganti filter coolant" in ctx
    assert _maintenance_context({}) == "- (tidak ada jadwal maintenance)"


_SKILL_INTERLOCKS_PATH = Path(__file__).parent.parent / "skills" / "cnc_milling" / "rules" / "safety_interlocks.json"


def test_revert_restores_previous_schedule_fields():
    """Revert must restore pre-approval order fields, not re-apply the new ones."""
    d = client.post("/api/disrupt", json=DISRUPTION).json()
    before = {o["id"]: o for o in client.get("/api/state").json()["production_orders"]}
    sc = next(s for s in d["scenarios"] if s["recommended"])
    touched = [c["order_id"] for c in sc.get("gantt_changes", [])]
    assert touched, "recommended scenario should change at least one order"

    approved = client.post("/api/approve", json={
        "scenario_id": sc["id"], "scenario_data": sc,
        "expected_sap_version": d["sap_version"], "approved_by": "Tester",
    })
    assert approved.status_code == 200, approved.text[:300]
    entry = [e for e in client.get("/api/ledger").json() if e.get("action_type") == "SCENARIO_APPROVED"][-1]

    r = client.post(f"/api/ledger/{entry['id']}/revert",
                    json={"id": entry["id"], "approved_by": "Tester"})
    assert r.status_code == 200, r.text[:300]

    after = {o["id"]: o for o in client.get("/api/state").json()["production_orders"]}
    for order_id in touched:
        for field in ("work_center_id", "planned_start", "planned_end", "status"):
            assert after[order_id][field] == before[order_id][field], (
                order_id, field, before[order_id].get(field), after[order_id].get(field),
            )
    # The revert itself is audited, including the inverse delta it applied.
    revert_entry = [e for e in client.get("/api/ledger").json() if e.get("action_type") == "SCENARIO_REVERTED"][-1]
    assert revert_entry.get("delta_applied", {}).get("order_updates")


def test_skills_lint_rejects_rule_removal_and_covers_all_base_rules():
    """Lint must diff against the real skill files: no silent removal, SAFE-005 included."""
    base = json.loads(_SKILL_INTERLOCKS_PATH.read_text(encoding="utf-8"))

    # Removing SAFE-004 while replacing cnc_milling must be rejected.
    partial = [il for il in base if il["id"] != "SAFE-004"]
    bad = client.post("/api/skills/lint", json={
        "skill_id": "cnc_milling", "skill_md": "x", "sla_penalties": {},
        "hooks": [], "interlocks": partial,
    }).json()
    assert bad["valid"] is False
    assert any("SAFE-004" in e for e in bad["errors"])

    # An empty rule list "removes" all five.
    bad2 = client.post("/api/skills/lint", json={
        "skill_id": "cnc_milling", "skill_md": "x", "sla_penalties": {},
        "hooks": [], "interlocks": [],
    }).json()
    assert bad2["valid"] is False
    assert {e.split(":")[0] for e in bad2["errors"]} >= {il["id"] for il in base}

    # Relaxing SAFE-005 (never covered by the old hardcoded table) must fail.
    lax = []
    for il in base:
        il = dict(il)
        if il["id"] == "SAFE-005":
            il["condition"] = "concurrent_axis_faults >= 3"
        lax.append(il)
    bad3 = client.post("/api/skills/lint", json={
        "skill_id": "cnc_milling", "skill_md": "x", "sla_penalties": {},
        "hooks": [], "interlocks": lax,
    }).json()
    assert bad3["valid"] is False
    assert any("SAFE-005" in e for e in bad3["errors"])

    # Keeping all rules and tightening SAFE-001 is allowed.
    stricter = []
    for il in base:
        il = dict(il)
        if il["id"] == "SAFE-001":
            il["condition"] = "motor_temp_celsius > 90"
            il["threshold_value"] = 90
        stricter.append(il)
    ok = client.post("/api/skills/lint", json={
        "skill_id": "cnc_milling", "skill_md": "x", "sla_penalties": {},
        "hooks": [], "interlocks": stricter,
    }).json()
    assert ok["valid"] is True, ok["errors"]


def test_skills_approve_gate_blocks_tier1_removal():
    """Approving over an existing skill must not drop a Tier 1 rule from disk."""
    skill_id = "test_skill_gate_tmp"
    target = Path("skills") / skill_id
    if target.exists():
        shutil.rmtree(target)
    base = json.loads(_SKILL_INTERLOCKS_PATH.read_text(encoding="utf-8"))
    payload = {
        "skill_md": "# gate test",
        "sla_penalties": {},
        "hooks": [],
        "interlocks": base,
    }
    try:
        first = client.post("/api/skills/approve", json={"skill_id": skill_id, "approved_by": "Tester", **payload})
        assert first.status_code == 200, first.text[:300]

        stripped = {**payload, "interlocks": [il for il in base if il["id"] != "SAFE-004"]}
        second = client.post("/api/skills/approve", json={"skill_id": skill_id, "approved_by": "Tester", **stripped})
        assert second.status_code == 400
        assert second.json()["detail"]["error"] == "Tier1Violation"

        on_disk = json.loads((target / "rules" / "safety_interlocks.json").read_text(encoding="utf-8"))
        assert [il["id"] for il in on_disk] == [il["id"] for il in base]
        assert not list(Path("skills").glob(f"{skill_id}.v*")), "rejected write must not create a backup"
    finally:
        if target.exists():
            shutil.rmtree(target)
        for backup in Path("skills").glob(f"{skill_id}.v*"):
            shutil.rmtree(backup, ignore_errors=True)


def test_ws_telemetry_rejects_bad_value_and_keeps_session():
    """A non-numeric value must not kill the socket, and no traceback leaks."""
    with client.websocket_connect("/ws/telemetry") as ws:
        ws.send_json({"machine_id": "CNC-04", "metric": "motor_temp_celsius", "value": "bukan-angka"})
        bad = ws.receive_json()
        assert "error" in bad and "traceback" not in bad
        # The session must still work for the next frame.
        ws.send_json({"machine_id": "CNC-04", "metric": "motor_temp_celsius", "value": 70})
        ok = ws.receive_json()
        assert ok["event"] == "telemetry_analysis"
        assert ok["severity"] == "OK" and ok["value"] == 70


def test_chat_prompt_carries_history_and_new_context_blocks(monkeypatch):
    """Follow-ups keep the conversation; origin, PLC, agent runs, plans and OEE are in the prompt."""
    import backend.main as m

    captured = {}

    async def fake_llm_reply(prompt, max_tokens=512):
        captured["prompt"] = prompt
        return "WO-2025-002 masih rencana awal SAP."

    monkeypatch.setattr(m, "_call_llm", fake_llm_reply)
    r = client.post("/api/chat", json={
        "message": "kenapa?",
        "history": [{"role": "user", "text": "Order apa saja di CNC-02?"},
                    {"role": "assistant", "text": "WO-2025-002 dan WO-2025-003."}],
    })
    assert r.status_code == 200, r.text
    p = captured["prompt"]
    assert "Supervisor: Order apa saja di CNC-02?\nAI Supervisor: WO-2025-002 dan WO-2025-003." in p
    for heading in ("Asal/riwayat order", "Bacaan PLC live", "Agent run terbaru",
                    "Skenario dari analisis gangguan terakhir", "Downtime dan OEE"):
        assert heading in p
    assert "maks 3 kalimat" not in p


def test_dashboard_approval_records_its_source_and_reset_marks_the_ledger():
    import backend.main as m

    client.post("/api/state/reset")
    assert m._ledger.get_all()[-1]["action_type"] == "STATE_RESET"
    r = client.post("/api/disrupt", json={"machine_id": "CNC-02", "disruption_type": "BREAKDOWN",
                                          "start_hour": 2, "end_hour": 5})
    best = next(s for s in r.json()["scenarios"] if s["recommended"])
    ok = client.post("/api/approve", json={"scenario_id": best["id"], "scenario_data": best,
                                           "expected_sap_version": r.json()["sap_version"],
                                           "approved_by": "Supervisor Budi"})
    assert ok.status_code == 200, ok.text
    entry = m._ledger.get_all()[-1]
    assert entry["action_type"] == "SCENARIO_APPROVED" and entry["source"] == {"kind": "dashboard"}
    client.post("/api/state/reset")


def test_approved_reroute_stores_iso_times_on_the_plan_day():
    """SAP keeps ISO timestamps; the solver's '09:00 WIB' labels are display text only."""
    import backend.main as m

    client.post("/api/state/reset")
    r = client.post("/api/disrupt", json={"machine_id": "CNC-02", "disruption_type": "BREAKDOWN",
                                          "start_hour": 2, "end_hour": 5}).json()
    for sid in ("scenario_c", "scenario_b"):
        sc = next(s for s in r["scenarios"] if s["id"] == sid)
        if sc["gantt_changes"]:
            break
    ok = client.post("/api/approve", json={"scenario_id": sc["id"], "scenario_data": sc,
                                           "expected_sap_version": r["sap_version"],
                                           "approved_by": "Supervisor Budi"})
    assert ok.status_code == 200, ok.text
    orders = {o["id"]: o for o in m._sap.get_state()["production_orders"]}
    for c in sc["gantt_changes"]:
        o = orders[c["order_id"]]
        assert o["planned_start"] == "2025-01-15T" + c["new_start_time"][:5] + ":00", o
        assert o["planned_end"].startswith("2025-01-15T") and "WIB" not in o["planned_end"]
    client.post("/api/state/reset")


def test_plan_timestamp_parses_solver_labels():
    from datetime import date
    from backend.main import _plan_timestamp

    d = date(2025, 1, 15)
    assert _plan_timestamp("09:00 WIB", d) == "2025-01-15T09:00:00"
    assert _plan_timestamp("21:00 WIB (Lembur)", d) == "2025-01-15T21:00:00"
    assert _plan_timestamp("25:00 WIB (Overtime)", d) == "2025-01-16T01:00:00"
    assert _plan_timestamp("2025-01-15T07:00:00", d) == "2025-01-15T07:00:00"
    assert _plan_timestamp("?", d) == "?" and _plan_timestamp("09:00 WIB", None) == "09:00 WIB"
