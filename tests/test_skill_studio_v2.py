"""Skill Studio v2: agent proposals, expert notes, field trial, generated test + backtest, learning loop, agent run."""

import asyncio

import pytest
from fastapi.testclient import TestClient

import backend.main as m
from backend import chat_context, skill_studio
from skills.loader import normalize_alarm_code

client = TestClient(m.app)


def test_ocr_slips_in_alarm_codes_compare_equal():
    assert normalize_alarm_code("ALM-I62") == normalize_alarm_code("ALM-162")
    assert normalize_alarm_code("Alarm I08") == normalize_alarm_code("Alarm 108")
    assert normalize_alarm_code("SV040I") == normalize_alarm_code("SV0401")


def test_agent_proposes_a_skill_for_cases_no_skill_covers():
    props = client.get("/api/skills/proposals").json()
    bearing = next(p for p in props if p["id"] == "new:ALM162")
    assert bearing["kind"] == "new"
    assert {"CASE-0001", "CASE-0026"} <= set(bearing["case_ids"])
    # Alarm 970 is already caught by the coolant skill, so it is never proposed again.
    assert not any(p["id"] == "new:ALARM970" for p in props)

    draft = client.post("/api/skills/proposals/new:ALM162/draft").json()
    assert draft["spec"]["trigger"]["alarm_codes"]
    run = client.post("/api/skills/test-run", json={"skill_id": draft["skill_id"], "spec": draft["spec"]}).json()
    assert run["scenario_id"] == "generated"
    bt = next(s for s in run["steps"] if s["id"] == "backtest")
    assert bt["data"]["total"] >= 2 and bt["ok"]
    assert run["passed"], [s for s in run["steps"] if not s["ok"]]


def test_dismissed_proposal_stays_hidden():
    client.post("/api/skills/proposals/new:SV0401/dismiss")
    assert not any(p["id"] == "new:SV0401" for p in client.get("/api/skills/proposals").json())


def test_expert_note_makes_a_field_trial_skill_that_only_advises():
    note = client.post("/api/skills/expert-notes", json={
        "author": "Pak Budi", "role": "Teknisi senior", "cause": "Toolholder tidak seimbang",
        "text": "Kalau getaran naik tepat setelah ganti tool, itu holder.",
        "machines": ["CNC-03"], "metric": "spindle_vibration_mm_per_s",
        "tasks": ["Lakukan lockout-tagout.", "Lepas dan seimbangkan toolholder."]}).json()
    assert note["id"].startswith("EXPERT-")
    assert any(n["id"] == note["id"] for n in client.get("/api/skills/expert-notes").json())

    draft = client.post("/api/skills/compose", json={"expert_note_ids": [note["id"]]}).json()
    spec = draft["spec"]
    assert spec["status"] == "trial"
    assert spec["root_causes"][0]["sources"] == [note["id"]]

    run = client.post("/api/skills/test-run", json={"skill_id": draft["skill_id"], "spec": spec}).json()
    steps = {s["id"]: s for s in run["steps"]}
    assert steps["root_causes"]["ok"]                     # an expert note counts as a source
    assert steps["alarm"]["data"]["machine_id"] == "CNC-03"
    assert "saran" in steps["plc_action"]["title"] or "tidak ada" in steps["plc_action"]["title"]


def test_trial_skill_never_proposes_a_plc_write():
    proposal = {"action_id": "spindle_slowdown"}
    assert m._skill_allows({"has_know_how": True, "status": "trial", "allowed_actions": ["spindle_slowdown"]},
                           proposal) is None
    assert m._skill_allows({"has_know_how": True, "status": "active", "allowed_actions": ["spindle_slowdown"]},
                           proposal) == proposal


def test_learning_loop_proposes_a_change_and_promotes_a_trial():
    spec = {"name": "Coolant", "version": "1.0.0", "status": "trial",
            "trigger": {"machines": [], "metrics": [], "alarm_codes": ["Alarm 970"], "predictions": False},
            "root_causes": [{"cause": "Filter coolant tersumbat", "sources": ["CASE-0009"]},
                            {"cause": "Impeller pompa aus", "sources": ["CASE-0012"]}]}
    missed = skill_studio.feedback_for({"id": "CASE-0099", "root_cause": "Selang coolant tertekuk di belakang meja"},
                                       "coolant", spec)
    second = skill_studio.feedback_for({"id": "CASE-0098", "root_cause": "Impeller pompa coolant aus"}, "coolant", spec)
    ok = [skill_studio.feedback_for({"id": f"CASE-00{n}", "root_cause": "Filter coolant tersumbat serpihan"},
                                    "coolant", spec) for n in (90, 91)]
    assert (missed["verdict"], second["verdict"], ok[0]["verdict"]) == ("new_cause", "other_listed", "confirmed")

    props = skill_studio.change_proposals([missed, second, *ok], {"coolant": spec})
    kinds = sorted(p["kind"] for p in props)
    assert kinds == ["change", "change", "promote"]
    change = next(p for p in props if p["case_id"] == "CASE-0099")
    new = skill_studio.apply_change(spec, change)
    assert new["version"] == "1.1.0" and new["root_causes"][0]["sources"] == ["CASE-0099"]
    up = skill_studio.apply_change(spec, next(p for p in props if p.get("case_id") == "CASE-0098"))
    assert up["root_causes"][0]["cause"] == "Impeller pompa aus"
    assert "CASE-0098" in up["root_causes"][0]["sources"]


def test_backtest_counts_cases_the_trigger_catches():
    spec = m._skills.get_spec("coolant_pressure")
    bt = skill_studio.backtest(spec, m._maint_ledger.cases(), m._case_metrics)
    assert bt["total"] >= 3 and bt["hits"] >= 3


def test_chat_context_lists_the_skills():
    text = chat_context.skills_block([{"skill_id": sid, **s} for sid, s in m._installed_specs().items()])
    assert "Tekanan coolant turun" in text and "Penyebab 1" in text


@pytest.fixture
def runner(monkeypatch):
    async def llm(prompt, max_tokens, model):
        return "[AI tidak tersedia]"

    monkeypatch.setattr(m._agent_runner.deps, "llm", llm)
    monkeypatch.setattr(m._agent_runner.deps, "step_delay", 0)
    m._sap.reset()
    yield
    m._sap.reset()


def test_agent_run_uses_the_routed_skill(runner):
    run = m._agent_runner.create({"machine_id": "CNC-02", "alarm_code": "Alarm 970", "alarm_text": "COOLANT LOW",
                                  "tag": "coolant_pressure_bar", "value": 2.1})
    run = asyncio.run(m._agent_runner.execute(run["id"]))
    step = next(s for s in run["steps"] if s["tool"] == "find_similar_cases")
    assert step["output"]["skill"]["skill_id"] == "coolant_pressure"
    assert "Memakai skill Tekanan coolant turun" in step["summary"]
    assert any(e.get("action_type") == "SKILL_FIRED" and e.get("agent_run_id") == run["id"]
               for e in m._ledger.get_all())


def test_closing_a_repair_feeds_the_skill_that_handled_it():
    r = client.post("/api/maintenance/entries", json={
        "machine_id": "CNC-02", "symptom": "Alarm 970 tekanan coolant rendah", "error_codes": ["Alarm 970"],
        "reported_by": "Operator"})
    assert r.status_code == 200, r.text
    case_id = r.json()["entry"]["id"]
    done = client.post(f"/api/maintenance/entries/{case_id}/resolve", json={
        "root_cause": "Selang coolant tertekuk di belakang meja", "fix_steps": ["Luruskan selang"]}).json()
    assert done["skill_feedback"] == {"skill_id": "coolant_pressure", "verdict": "new_cause", "rank": None}
    props = client.get("/api/skills/proposals").json()
    change = next(p for p in props if p["id"] == f"change:coolant_pressure:{case_id}")
    draft = client.post(f"/api/skills/proposals/{change['id']}/draft").json()
    assert draft["spec"]["version"] == "1.1.0"
    assert draft["spec"]["root_causes"][0]["sources"] == [case_id]
