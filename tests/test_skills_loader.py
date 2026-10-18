"""
tests/test_skills_loader.py
───────────────────────────
Tests for the skills loader in skills/loader.py.
"""

import shutil
from pathlib import Path

import pytest
from skills.loader import SkillRegistry, evaluate_condition


class TestEvaluateCondition:
    """Test the safe condition parser."""

    def test_greater_than(self):
        """Test > operator."""
        telemetry = {"motor_temp_celsius": 90}
        assert evaluate_condition("motor_temp_celsius > 85", telemetry) is True
        assert evaluate_condition("motor_temp_celsius > 95", telemetry) is False

    def test_less_than(self):
        """Test < operator."""
        telemetry = {"coolant_pressure_bar": 2.8}
        assert evaluate_condition("coolant_pressure_bar < 3.0", telemetry) is True
        assert evaluate_condition("coolant_pressure_bar < 2.5", telemetry) is False

    def test_greater_equal(self):
        """Test >= operator."""
        telemetry = {"spindle_vibration_mm_per_s": 8}
        assert evaluate_condition("spindle_vibration_mm_per_s >= 8", telemetry) is True
        assert evaluate_condition("spindle_vibration_mm_per_s >= 8.5", telemetry) is False

    def test_equal(self):
        """Test == and = operators."""
        telemetry = {"emergency_stop_activated": True}
        assert evaluate_condition("emergency_stop_activated = true", telemetry) is True
        assert evaluate_condition("emergency_stop_activated == true", telemetry) is True

    def test_inequality(self):
        """Test != operator."""
        telemetry = {"motor_temp_celsius": 90}
        assert evaluate_condition("motor_temp_celsius != 85", telemetry) is True
        assert evaluate_condition("motor_temp_celsius != 90", telemetry) is False

    def test_missing_metric(self):
        """Test that missing metric returns False."""
        telemetry = {"motor_temp_celsius": 90}
        assert evaluate_condition("coolant_pressure_bar > 2", telemetry) is False

    def test_invalid_condition(self):
        """Test that invalid condition returns False."""
        telemetry = {"motor_temp_celsius": 90}
        assert evaluate_condition("", telemetry) is False
        assert evaluate_condition("invalid syntax", telemetry) is False
        assert evaluate_condition("motor_temp_celsius >>> 85", telemetry) is False


class TestSkillRegistry:
    """Test the skill registry."""

    @pytest.fixture
    def registry(self):
        reg = SkillRegistry()
        reg.index()
        return reg

    def test_indexes_skills(self, registry):
        """Registry should index available skills."""
        assert "cnc_milling" in registry.loaded_skills

    def test_loads_hooks(self, registry):
        """Registry should load hooks from hooks.json."""
        hooks = registry.load_hooks("cnc_milling")
        assert len(hooks) > 0
        assert all(hasattr(h, "id") for h in hooks)

    def test_loads_interlocks(self, registry):
        """Registry should load interlocks from safety_interlocks.json."""
        interlocks = registry.load_interlocks("cnc_milling")
        assert len(interlocks) > 0
        assert all(hasattr(i, "tier") for i in interlocks)
        # All should be tier 1
        assert all(i.tier == 1 for i in interlocks)

    def test_evaluates_hooks(self, registry):
        """Registry should evaluate hooks against telemetry."""
        hooks = registry.load_hooks("cnc_milling")
        telemetry_high = {"motor_temp_celsius": 90}
        telemetry_low = {"motor_temp_celsius": 80}

        # Find HOOK-001 (Motor Temperature High Alert, condition: > 85)
        hook_001 = next(h for h in hooks if h.id == "HOOK-001")
        assert hook_001.evaluate(telemetry_high) is True
        assert hook_001.evaluate(telemetry_low) is False

    def test_evaluates_interlocks(self, registry):
        """Registry should evaluate interlocks against telemetry."""
        interlocks = registry.load_interlocks("cnc_milling")
        telemetry_high = {"motor_temp_celsius": 96}
        telemetry_normal = {"motor_temp_celsius": 90}

        # Find SAFE-001 (Motor Overtemperature, threshold: 95)
        safe_001 = next(i for i in interlocks if i.id == "SAFE-001")
        assert safe_001.evaluate(telemetry_high) is True
        assert safe_001.evaluate(telemetry_normal) is False


class TestIndexRebuild:
    """index() must rebuild from scratch and ignore backup/hidden dirs (finding #3)."""

    @staticmethod
    def _make_skill(base: Path, name: str, content: str = "# Test Skill\n") -> Path:
        skill_dir = base / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        return skill_dir

    def test_skips_backup_hidden_and_cache_dirs(self, tmp_path):
        """Backup dirs (<id>.v<N>), hidden dirs and __pycache__ are not indexed."""
        self._make_skill(tmp_path, "real_skill")
        self._make_skill(tmp_path, "real_skill.v1")
        self._make_skill(tmp_path, "real_skill.v12")
        self._make_skill(tmp_path, ".hidden_skill")
        self._make_skill(tmp_path, "__pycache__")

        registry = SkillRegistry(skills_dir=tmp_path)
        registry.index()

        assert "real_skill" in registry.skills
        assert "real_skill.v1" not in registry.skills
        assert "real_skill.v12" not in registry.skills
        assert ".hidden_skill" not in registry.skills
        assert "__pycache__" not in registry.skills
        assert set(registry.loaded_skills) == {"real_skill"}

    def test_reindex_drops_deleted_skills(self, tmp_path):
        """Re-indexing after deleting a skill dir removes it from the registry."""
        self._make_skill(tmp_path, "kept_skill")
        removed_dir = self._make_skill(tmp_path, "removed_skill")

        registry = SkillRegistry(skills_dir=tmp_path)
        registry.index()
        assert set(registry.loaded_skills) == {"kept_skill", "removed_skill"}

        shutil.rmtree(removed_dir)
        registry.index()

        assert set(registry.loaded_skills) == {"kept_skill"}
        assert "removed_skill" not in registry.skills


class TestMetadataExtraction:
    """_extract_metadata must read YAML frontmatter and HTML comment blocks (finding #4)."""

    @staticmethod
    def _index_single(tmp_path: Path, name: str, content: str) -> dict:
        skill_dir = tmp_path / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        registry = SkillRegistry(skills_dir=tmp_path)
        registry.index()
        return registry.skills[name]

    def test_comment_block_name_and_singular_machine_type(self, tmp_path):
        """<!-- skill_metadata ... --> gives name + singular machine_type as a 1-item list."""
        content = (
            "# SKILL.md — comment metadata\n"
            "<!-- skill_metadata\n"
            "name: Comment Skill\n"
            "version: 1.0.0\n"
            "machine_type: cnc_lathe\n"
            "  machine_type: indented_ignored\n"
            "tier_hierarchy:\n"
            "  tier_1: Safety (never override)\n"
            "references:\n"
            "  - rules/safety_interlocks.json\n"
            "-->\n"
            "\n"
            "# body starts here\n"
        )
        entry = self._index_single(tmp_path, "comment_skill", content)
        assert entry["name"] == "Comment Skill"
        assert entry["machine_types"] == ["cnc_lathe"]

    def test_nested_keys_are_ignored(self, tmp_path):
        """Indented keys inside the comment block must not be treated as top-level."""
        content = "<!-- skill_metadata\n  machine_type: indented_ignored\nname: Nested Test\n-->\n"
        entry = self._index_single(tmp_path, "nested_skill", content)
        assert entry["name"] == "Nested Test"
        assert entry["machine_types"] == []

    def test_yaml_frontmatter_comma_separated_machine_types(self, tmp_path):
        """YAML frontmatter `machine_types: a, b` becomes a trimmed list."""
        content = "---\nname: YAML Skill\nmachine_types: milling, turning\n---\n\n# body\n"
        entry = self._index_single(tmp_path, "yaml_skill", content)
        assert entry["name"] == "YAML Skill"
        assert entry["machine_types"] == ["milling", "turning"]

    def test_yaml_frontmatter_singular_machine_type(self, tmp_path):
        """YAML frontmatter `machine_type` (singular) becomes a 1-item list; quotes stripped."""
        content = '---\nname: "Quoted Skill"\nmachine_type: CNC_milling_center\n---\n'
        entry = self._index_single(tmp_path, "quoted_skill", content)
        assert entry["name"] == "Quoted Skill"
        assert entry["machine_types"] == ["CNC_milling_center"]

    def test_malformed_metadata_does_not_raise(self, tmp_path):
        """An unclosed comment block must not break indexing; defaults apply."""
        content = "<!-- skill_metadata\nname: Unclosed Comment Skill\nmachine_type: lathe\n"
        entry = self._index_single(tmp_path, "broken_skill", content)
        assert entry["machine_types"] == []
        assert entry["name"] == "Broken Skill"  # fallback: dir id title-cased

    def test_real_cnc_milling_skill_metadata(self):
        """The real skills/cnc_milling/SKILL.md comment block is parsed correctly."""
        registry = SkillRegistry()
        registry.index()
        entry = registry.skills["cnc_milling"]
        assert entry["name"] == "CNC Milling Production Supervisor"
        assert "CNC_milling_center" in entry["machine_types"]
