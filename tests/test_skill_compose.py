"""AI-assisted skill drafting: story + resolved cases → skill.json draft, citations checked."""

import json

import pytest
from fastapi.testclient import TestClient

import backend.main as m

client = TestClient(m.app)
BODY = {"story": "Kalau tekanan coolant turun, saya cek filter dulu, baru pompanya.",
        "case_ids": ["CASE-0009", "case-0012"]}


@pytest.fixture
def llm(monkeypatch):
    box = {}

    async def fake(prompt, max_tokens=512, **kw):
        box["prompt"] = prompt
        return box["text"]

    monkeypatch.setattr(m, "_call_llm", fake)
    monkeypatch.setattr(m.aicore, "configured", lambda: True)
    return box


def _good(sources=("CASE-0009",), task="Lakukan LOTO lalu bersihkan filter FLT-CLN-10U."):
    return json.dumps({
        "name": "Tekanan coolant turun (filter dulu)",
        "confirm": [{"kind": "operator", "text": "Pompa berisik?"}],
        "root_causes": [{"cause": "Filter tersumbat", "evidence": "tekanan turun perlahan", "sources": list(sources)}],
        "tasks": [task, "Uji tekanan di atas 3.5 bar."],
    })


def test_ai_draft_is_used_and_marked(llm):
    llm["text"] = "```json\n" + _good() + "\n```"
    r = client.post("/api/skills/compose", json=BODY)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["spec"]["name"] == "Tekanan coolant turun (filter dulu)"
    assert d["spec"]["root_causes"][0]["sources"] == ["CASE-0009"]
    assert d["provenance"]["root_causes"]["by"] == "ai" and d["provenance"]["trigger"]["by"] == "rules"
    assert d["spec"]["trigger"]["alarm_codes"] == ["Alarm 970"]
    assert d["spec"]["trigger"]["metrics"] == ["coolant_pressure_bar"]
    assert "feed_hold" in d["spec"]["allowed_actions"] and "reduce_feed_50" not in d["spec"]["allowed_actions"]
    assert "saya cek filter dulu" in llm["prompt"] and "CASE-0012" in llm["prompt"]
    # The draft goes straight into a test run and passes.
    t = client.post("/api/skills/test-run", json={"skill_id": d["skill_id"], "scenario_id": "cnc02_coolant",
                                                  "spec": d["spec"]}).json()
    assert t["passed"] is True


@pytest.mark.parametrize("text", [
    _good(sources=("CASE-0042",)),                         # cites a case the expert did not pick
    _good(task="Bypass switch tekanan supaya mesin jalan"),  # suggests a bypass
    "maaf, saya tidak bisa",                               # no JSON
    "[AI error: AI Core orchestration returned an empty reply]",
])
def test_rejected_ai_falls_back_to_rules(llm, text):
    llm["text"] = text
    d = client.post("/api/skills/compose", json=BODY).json()
    assert d["model_used"] is None and d["warnings"]
    assert d["provenance"]["root_causes"]["by"] == "rules"
    assert {s for c in d["spec"]["root_causes"] for s in c["sources"]} == {"CASE-0009", "CASE-0012"}
    assert d["spec"]["name"] == "Tekanan coolant turun"


def test_expert_name_and_scope_win(llm):
    llm["text"] = _good()
    d = client.post("/api/skills/compose", json={**BODY, "name": "Coolant CNC-02", "machines": ["CNC-02"]}).json()
    assert d["spec"]["name"] == "Coolant CNC-02" and d["skill_id"] == "coolant_cnc_02"
    assert d["provenance"]["name"]["by"] == "expert" and d["spec"]["trigger"]["machines"] == ["CNC-02"]


def test_unknown_case_is_404():
    r = client.post("/api/skills/compose", json={"case_ids": ["CASE-9999"]})
    assert r.status_code == 404 and "CASE-9999" in r.text
    assert client.post("/api/skills/compose", json={"case_ids": []}).status_code == 422


def test_vibration_cases_do_not_pick_up_coolant_from_the_root_cause():
    # Both cases blame coolant ingress in the root cause; the symptom is vibration.
    cases = [m._maint_ledger.get_entry(c) for c in ("CASE-0026", "CASE-0001")]
    assert m._case_metrics(cases) == ["spindle_vibration_mm_per_s"]
