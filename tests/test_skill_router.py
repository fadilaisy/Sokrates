"""Skill router: skill.json triggers pick the skill for a reading, cnc_milling is the fallback."""

import json
import shutil
from pathlib import Path

import pytest

import backend.main as m
from skills.loader import DEFAULT_SKILL_ID, SkillRegistry, normalize_alarm_code, parse_skill_spec

SKILLS = Path(__file__).parent.parent / "skills"


def _skill(root: Path, skill_id: str, spec: dict | None = None, interlocks: list | None = None) -> None:
    d = root / skill_id
    (d / "rules").mkdir(parents=True)
    (d / "SKILL.md").write_text(f"# {skill_id}\n", encoding="utf-8")
    (d / "hooks.json").write_text("[]", encoding="utf-8")
    (d / "rules" / "safety_interlocks.json").write_text(json.dumps(interlocks or []), encoding="utf-8")
    if spec is not None:
        (d / "skill.json").write_text(json.dumps(spec), encoding="utf-8")


def _spec(**trigger) -> dict:
    return {"schema": "skillforge_skill_v2", "status": "active", "trigger": trigger}


@pytest.fixture
def registry(tmp_path):
    shutil.copytree(SKILLS / DEFAULT_SKILL_ID, tmp_path / DEFAULT_SKILL_ID)
    return tmp_path


def _index(root: Path) -> SkillRegistry:
    r = SkillRegistry(root)
    r.index()
    return r


def test_shipped_skills_route_by_metric_and_alarm():
    r = _index(SKILLS)
    assert r.route("CNC-02", "coolant_pressure_bar") == "coolant_pressure"
    assert r.route("CNC-04", None, "ALM-441") == "coolant_pressure"
    assert r.route("CNC-04", "motor_temp_celsius") == "spindle_overheat"
    assert r.route("CNC-04", None, "Alarm 108") == "spindle_overheat"
    assert r.route("CNC-04", "spindle_vibration_mm_per_s") == DEFAULT_SKILL_ID
    assert r.route(None, "coolant_pressure_bar", prediction=True) == "coolant_pressure"


def test_alarm_codes_compare_loosely():
    assert normalize_alarm_code("Alarm 970") == normalize_alarm_code("ALARM-970") == "ALARM970"
    assert _index(SKILLS).match(alarm_code="alarm970") == "coolant_pressure"


def test_machine_scope_and_tie_breaks(registry):
    _skill(registry, "plant_wide", _spec(metrics=["motor_temp_celsius"]))
    _skill(registry, "cnc04_only", _spec(machines=["CNC-04"], metrics=["motor_temp_celsius"]))
    _skill(registry, "by_alarm", _spec(alarm_codes=["ALM-410"]))
    r = _index(registry)
    assert r.match("CNC-04", "motor_temp_celsius") == "cnc04_only"  # machine-scoped wins a tie
    assert r.match("CNC-01", "motor_temp_celsius") == "plant_wide"  # out of scope
    assert r.match(None, "motor_temp_celsius") == "plant_wide"      # unknown machine: scoped skills skip
    assert r.match("CNC-04", "motor_temp_celsius", "ALM-410") == "by_alarm"  # alarm beats metric


def test_inactive_broken_and_v1_skills_never_route(registry):
    _skill(registry, "draft_skill", {**_spec(metrics=["coolant_pressure_bar"]), "status": "draft"})
    _skill(registry, "broken_skill", {"schema": "skillforge_skill_v2", "trigger": {"metrics": "coolant"}})
    (registry / "garbled").mkdir()
    (registry / "garbled" / "SKILL.md").write_text("# g", encoding="utf-8")
    (registry / "garbled" / "skill.json").write_text("{not json", encoding="utf-8")
    r = _index(registry)
    assert set(r.skills) == {DEFAULT_SKILL_ID, "draft_skill", "broken_skill", "garbled"}
    assert r.skills["broken_skill"]["spec_errors"] and r.skills["garbled"]["spec_errors"]
    assert r.match(metric="coolant_pressure_bar") is None
    assert r.route(metric="coolant_pressure_bar") == DEFAULT_SKILL_ID


def test_prediction_needs_opt_in(registry):
    _skill(registry, "no_pred", _spec(metrics=["motor_temp_celsius"]))
    r = _index(registry)
    assert r.match(metric="motor_temp_celsius") == "no_pred"
    assert r.match(metric="motor_temp_celsius", prediction=True) is None


def test_root_causes_need_sources():
    spec, errors = parse_skill_spec({**_spec(), "root_causes": [{"cause": "Filter tersumbat"}]})
    assert spec is None and any("sumber" in e for e in errors)
    spec, errors = parse_skill_spec({**_spec(), "root_causes": [{"cause": "Filter", "sources": ["CASE-0009"]}]})
    assert errors == [] and spec["trigger"]["machines"] == []


def test_routed_skill_cannot_drop_default_tier1(registry, monkeypatch):
    # A skill with no interlocks of its own still stops the spindle via cnc_milling's SAFE-003.
    _skill(registry, "lax", _spec(metrics=["coolant_pressure_bar"]), interlocks=[])
    monkeypatch.setattr(m, "_skills", _index(registry))
    assert m._skills.route("CNC-02", "coolant_pressure_bar") == "lax"
    assert m._classify_telemetry("coolant_pressure_bar", 1.7, "CNC-02") == ("CRITICAL", "SAFE-003")
    assert m._classify_telemetry("coolant_pressure_bar", 2.3, "CNC-02") == ("WARNING", None)
    assert m._classify_telemetry("coolant_pressure_bar", 3.4, "CNC-02") == ("OK", None)


def test_routed_skill_can_add_a_stricter_rule(registry, monkeypatch):
    strict = [{"id": "SAFE-103", "tier": 1, "name": "Coolant strict", "condition": "coolant_pressure_bar < 2.8"}]
    _skill(registry, "strict", _spec(machines=["CNC-02"], metrics=["coolant_pressure_bar"]), interlocks=strict)
    monkeypatch.setattr(m, "_skills", _index(registry))
    assert m._classify_telemetry("coolant_pressure_bar", 2.7, "CNC-02") == ("CRITICAL", "SAFE-103")
    assert m._classify_telemetry("coolant_pressure_bar", 2.7, "CNC-01") == ("OK", None)  # out of scope


@pytest.mark.parametrize("skill_id", ["coolant_pressure", "spindle_overheat"])
def test_shipped_skills_keep_every_default_tier1_rule(skill_id):
    r = _index(SKILLS)
    base = {il.id: il.condition for il in r.load_interlocks(DEFAULT_SKILL_ID) if il.tier == 1}
    own = {il.id: il.condition for il in r.load_interlocks(skill_id)}
    assert base.items() <= own.items()
    spec = r.get_spec(skill_id)
    assert spec and all(c["sources"] for c in spec["root_causes"])
