"""Skill test run: dry run of a PLC scenario against a skill; approving a skill.json needs a passed run."""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.main as m
from skills.loader import SkillRegistry

client = TestClient(m.app)
SKILLS = Path(__file__).parent.parent / "skills"


def _run(skill_id="coolant_pressure", scenario_id="cnc02_coolant", spec=None):
    body = {"skill_id": skill_id, "scenario_id": scenario_id}
    if spec is not None:
        body["spec"] = spec
    return client.post("/api/skills/test-run", json=body)


def _steps(d):
    return {s["id"]: s for s in d["steps"]}


def _shipped_spec():
    return json.loads((SKILLS / "coolant_pressure" / "skill.json").read_text(encoding="utf-8"))


@pytest.fixture
def isolated_skills(tmp_path, monkeypatch):
    """Approve writes under _ROOT/skills; point it and the registry at a copy."""
    root = tmp_path / "repo"
    shutil.copytree(SKILLS, root / "skills", ignore=shutil.ignore_patterns("__pycache__", "*.py"))
    reg = SkillRegistry(root / "skills")
    reg.index()
    monkeypatch.setattr(m, "_ROOT", root)
    monkeypatch.setattr(m, "_skills", reg)
    return root / "skills"


def test_shipped_coolant_skill_passes_its_scenario_and_writes_nothing():
    plc_before = {k: v for k, v in m._plc.info().items() if k != "elapsed_s"}
    sap_before = m._sap.get_version()
    r = _run()
    assert r.status_code == 200
    d = r.json()
    assert d["passed"] is True
    st = _steps(d)
    assert st["classify"]["data"]["rule_triggered"] == "SAFE-003"
    assert st["plc_action"]["data"]["action_id"] == "feed_hold"
    assert all(c["ok"] for c in st["plc_action"]["data"]["checks"])
    assert len(st["sap"]["data"]["options"]) == 3
    assert {k: v for k, v in m._plc.info().items() if k != "elapsed_s"} == plc_before and m._sap.get_version() == sap_before
    ledger = [e for e in m._ledger.get_all() if e.get("id") == d["ledger_entry_id"]]
    assert ledger and ledger[0]["action_type"] == "SKILL_TEST_RUN" and ledger[0]["passed"] is True


def test_spindle_skill_runs_its_autonomous_action_in_the_dry_run():
    d = _run(skill_id="spindle_overheat", scenario_id="cnc04_overheat").json()
    assert d["passed"] is True
    plc = _steps(d)["plc_action"]["data"]
    assert plc["action_id"] == "reduce_feed_50" and plc["autonomous"] is True
    assert {c["id"] for c in plc["checks"]} >= {"policy", "direction"}


def test_wrong_scenario_fails_the_match():
    d = _run(scenario_id="cnc04_overheat").json()
    assert d["passed"] is False
    st = _steps(d)
    assert st["match"]["ok"] is False and st["match"]["data"]["active_skill"] == "spindle_overheat"
    assert "tidak diizinkan" in st["plc_action"]["detail"]  # reduce_feed_50 is not in this skill


def test_missing_source_and_unknown_action_fail():
    spec = _shipped_spec()
    spec["root_causes"][0]["sources"] = ["CASE-9999"]
    spec["allowed_actions"].append("open_the_valves")
    d = _run(spec=spec).json()
    assert d["passed"] is False
    assert "CASE-9999" in _steps(d)["root_causes"]["detail"]
    checks = {c["id"]: c for c in d["checks"]}
    assert checks["sources"]["ok"] is False and "open_the_valves" in checks["actions"]["detail"]


def test_bad_requests():
    assert _run(scenario_id="normal").status_code == 404
    assert _run(skill_id="cnc_milling").status_code == 404  # v1 skill, no skill.json
    assert _run(skill_id="../x").status_code == 422
    assert _run(spec={"trigger": "x"}).status_code == 422


def _approve(skill_id, spec):
    base = SKILLS / "coolant_pressure"
    return client.post("/api/skills/approve", json={
        "skill_id": skill_id,
        "skill_md": "# SKILL.md\nUji",
        "sla_penalties": json.loads((base / "rules" / "sla_penalties.json").read_text(encoding="utf-8")),
        "hooks": json.loads((base / "hooks.json").read_text(encoding="utf-8")),
        "interlocks": json.loads((base / "rules" / "safety_interlocks.json").read_text(encoding="utf-8")),
        "approved_by": "Kepala perawatan",
        "spec": spec,
    })


def test_approve_needs_a_passed_test_run_of_the_same_spec(isolated_skills):
    spec = _shipped_spec()
    spec["name"] = "Coolant CNC-02"
    spec["trigger"]["machines"] = ["CNC-02"]

    r = _approve("coolant_cnc02", spec)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "TestRunRequired"
    assert not (isolated_skills / "coolant_cnc02").exists()

    assert _run(skill_id="coolant_cnc02", spec=spec).json()["passed"] is True
    changed = {**spec, "tasks": spec["tasks"][:1]}
    assert _approve("coolant_cnc02", changed).status_code == 409  # edited after the test

    r = _approve("coolant_cnc02", spec)
    assert r.status_code == 200, r.text
    assert json.loads((isolated_skills / "coolant_cnc02" / "skill.json").read_text(encoding="utf-8")) == spec
    assert m._skills.route("CNC-02", "coolant_pressure_bar") == "coolant_cnc02"  # live without a restart
    approved = [e for e in m._ledger.get_all() if e.get("id") == r.json()["ledger_entry_id"]][0]
    assert approved["spec_sha256"] == m._spec_hash(spec)


def test_approve_renders_skill_md_when_left_empty(isolated_skills):
    spec = {**_shipped_spec(), "name": "Coolant render"}
    assert _run(skill_id="coolant_render", spec=spec).json()["passed"] is True
    base = client.get("/api/skills/cnc_milling").json()
    r = client.post("/api/skills/approve", json={
        "skill_id": "coolant_render", "skill_md": "", "sla_penalties": base["sla_penalties"],
        "hooks": base["hooks"], "interlocks": base["interlocks"], "approved_by": "Kepala perawatan", "spec": spec})
    assert r.status_code == 200, r.text
    md = (isolated_skills / "coolant_render" / "SKILL.md").read_text(encoding="utf-8")
    assert md.startswith("# SKILL.md — Coolant render") and "CASE-0009" in md
    assert m._skills.skills["coolant_render"]["name"] == "Coolant render"


def test_approve_rejects_an_invalid_spec(isolated_skills):
    r = _approve("coolant_bad", {"trigger": {"metrics": "x"}})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "InvalidSkillSpec"


def test_guidance_puts_retrieved_causes_first_and_filters_plc_actions():
    g = m._skill_guidance("CNC-04", alarm_code="ALM-410", evidence_ids=["CASE-0018"])
    assert g["skill_id"] == "spindle_overheat" and g["has_know_how"] and g["tasks"]
    assert g["root_causes"][0]["evidence_found"] is True and "CASE-0018" in g["root_causes"][0]["sources"]
    feed = {"action_id": "reduce_feed_50"}
    hold = {"action_id": "feed_hold"}
    assert m._skill_allows(g, feed) == feed and m._skill_allows(g, hold) is None
    v1 = m._skill_guidance("CNC-01", alarm_code="ALM-162")
    assert v1["skill_id"] == "cnc_milling" and not v1["has_know_how"]
    assert m._skill_allows(v1, hold) == hold  # v1 skill keeps the limits-file default


def test_library_lists_skills_tools_and_scenarios():
    _run()  # leaves a SKILL_TEST_RUN in the history
    d = client.get("/api/skills").json()
    by_id = {s["skill_id"]: s for s in d["skills"]}
    assert {"cnc_milling", "coolant_pressure", "spindle_overheat"} <= set(by_id)
    assert by_id["cnc_milling"]["is_default"] and not by_id["cnc_milling"]["has_know_how"]
    assert by_id["coolant_pressure"]["history"][0]["action_type"] == "SKILL_TEST_RUN"
    tools = {t["id"]: t for t in d["tools"]}
    assert tools["reduce_feed_50"]["autonomous"] and tools["reduce_feed_50"]["used_by"] == ["spindle_overheat"]
    assert {s["id"] for s in d["scenarios"]} == {"cnc04_overheat", "cnc01_bearing", "cnc02_coolant"}
    one = client.get("/api/skills/coolant_pressure").json()
    assert one["spec"]["name"] == "Tekanan coolant turun" and one["skill_md"].startswith("# SKILL.md")
    assert client.get("/api/skills/nope").status_code == 404
