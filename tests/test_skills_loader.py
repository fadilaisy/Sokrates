"""
tests/test_skills_loader.py
───────────────────────────
Tests for the skills loader in skills/loader.py.
"""

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
