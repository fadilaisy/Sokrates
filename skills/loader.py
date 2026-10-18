"""
skills/loader.py
────────────────
Skill registry for SkillForge - dynamically loads skill definitions
from JSON files and evaluates telemetry conditions.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Version-backup directories created by the skill-approve flow: skills/<id>.v<N>
_BACKUP_DIR_RE = re.compile(r"\.v\d+$")
# YAML frontmatter delimited by --- at the very start of the file
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
# HTML comment block used by hand-authored skills: <!-- skill_metadata ... -->
_SKILL_METADATA_COMMENT_RE = re.compile(r"<!--\s*skill_metadata\b(.*?)-->", re.DOTALL)


def _strip_quotes(value: str) -> str:
    """Trim whitespace and one layer of surrounding quotes from a value."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
        return value[1:-1].strip()
    return value


def _split_machine_types(value: str) -> list[str]:
    """Parse a comma-separated machine_types value into a trimmed list."""
    return [cleaned for cleaned in (_strip_quotes(part) for part in value.split(",")) if cleaned]


def _parse_top_level_metadata(text: str) -> dict[str, Any]:
    """
    Extract metadata from top-level ``key: value`` lines only.

    Lines with leading whitespace (nested structures such as ``tier_hierarchy:``)
    are ignored. Recognised keys: ``name``, ``machine_type`` (singular) and
    ``machine_types`` (comma-separated). The first non-empty occurrence wins.
    """
    found: dict[str, Any] = {}
    for line in text.splitlines():
        if not line or line[:1].isspace() or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = _strip_quotes(raw_value)
        if not value:
            continue
        if key == "name" and "name" not in found:
            found["name"] = value
        elif key == "machine_type" and "machine_types" not in found:
            found["machine_types"] = [value]
        elif key == "machine_types" and "machine_types" not in found:
            found["machine_types"] = _split_machine_types(value)
    return found


class Hook:
    """Represents a hook from hooks.json"""

    def __init__(self, data: dict[str, Any]):
        self.id = data.get("id", "")
        self.name = data.get("name", "")
        self.name_id = data.get("name_id", "")
        self.trigger_type = data.get("trigger_type", "")
        self.condition = data.get("condition", "")
        self.condition_description_id = data.get("condition_description_id", "")
        self.skill_action = data.get("skill_action", "")
        self.skill_action_steps = data.get("skill_action_steps", [])
        self.auto_resolve_if = data.get("auto_resolve_if")
        self.notify_roles = data.get("notify_roles", [])
        self.severity = data.get("severity", "INFO")
        self.debounce_seconds = data.get("debounce_seconds", 0)

    def evaluate(self, telemetry: dict[str, Any]) -> bool:
        """Evaluate hook condition against current telemetry"""
        return evaluate_condition(self.condition, telemetry)


class SafetyInterlock:
    """Represents an interlock from safety_interlocks.json"""

    def __init__(self, data: dict[str, Any]):
        self.id = data.get("id", "")
        self.tier = data.get("tier", 1)
        self.name = data.get("name", "")
        self.description_id = data.get("description_id", "")
        self.condition = data.get("condition", "")
        self.threshold_value = data.get("threshold_value")
        self.threshold_unit = data.get("threshold_unit")
        self.action = data.get("action", "")
        self.action_detail_id = data.get("action_detail_id", "")
        self.override_allowed = data.get("override_allowed", False)
        self.auto_restart_allowed = data.get("auto_restart_allowed", False)
        self.auto_restart_condition = data.get("auto_restart_condition")
        self.responsible_role = data.get("responsible_role", "")
        self.notify_roles = data.get("notify_roles", [])

    def evaluate(self, telemetry: dict[str, Any]) -> bool:
        """Check if interlock condition is triggered"""
        return evaluate_condition(self.condition, telemetry)


class SkillRegistry:
    """
    Registry for loading and evaluating skills.

    At startup, indexes skill metadata only.
    Loads full rules/*.json only when needed (progressive disclosure).
    """

    def __init__(self, skills_dir: Path | None = None):
        self._skills_dir = skills_dir or Path(__file__).parent
        self._registry: dict[str, dict[str, Any]] = {}  # skill_id -> {name, machine_types, hook_ids}
        self._hooks: dict[str, list[Hook]] = {}  # skill_id -> [Hook]
        self._interlocks: dict[str, list[SafetyInterlock]] = {}  # skill_id -> [SafetyInterlock]
        self._sla_penalties: dict[str, dict[str, Any]] = {}  # skill_id -> sla_penalties data
        self._loaded: set[str] = set()  # Track which files have been loaded

    @property
    def skills(self) -> dict[str, dict[str, Any]]:
        """Get skill metadata (name, machine_types, hook_ids)"""
        return self._registry

    @property
    def loaded_skills(self) -> list[str]:
        """List of skill IDs that have been loaded"""
        return list(self._registry.keys())

    def index(self) -> None:
        """
        Index skill metadata only. Does NOT load full rules.
        Rebuilds the registry from scratch on every call so deleted skills do
        not linger. Reads SKILL.md files to extract skill names and machine types.
        Version-backup dirs (``<id>.v<N>``), hidden dirs and ``__pycache__`` are skipped.
        """
        self._registry = {}

        for skill_dir in self._skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_id = skill_dir.name

            if (
                skill_id.startswith(".")
                or skill_id == "__pycache__"
                or _BACKUP_DIR_RE.search(skill_id)
            ):
                continue

            skill_file = skill_dir / "SKILL.md"

            if skill_file.exists():
                metadata = self._extract_metadata(skill_file)
                self._registry[skill_id] = {
                    "name": metadata.get("name", skill_id.replace("_", " ").title()),
                    "machine_types": metadata.get("machine_types", []),
                    "hook_ids": [],
                }

                # Collect hook IDs from hooks.json if it exists
                hooks_file = skill_dir / "hooks.json"
                if hooks_file.exists():
                    with hooks_file.open("r", encoding="utf-8") as f:
                        hooks_data = json.load(f)
                        self._registry[skill_id]["hook_ids"] = [h.get("id", "") for h in hooks_data]

    def _extract_metadata(self, skill_file: Path) -> dict[str, Any]:
        """
        Extract metadata from SKILL.md.

        Two formats are merged, first non-empty value wins:
        - YAML frontmatter delimited by ``---`` at the start of the file
        - an HTML comment block starting with ``<!-- skill_metadata``
        Only top-level (unindented) ``key: value`` lines are read, so nested
        keys such as ``tier_hierarchy:`` are ignored. Never raises on malformed
        input; always returns a dict with at least ``machine_types``.
        """
        metadata: dict[str, Any] = {"machine_types": []}

        try:
            content = skill_file.read_text(encoding="utf-8")
            sources: list[dict[str, Any]] = []

            frontmatter = _FRONTMATTER_RE.match(content.lstrip("\ufeff"))
            if frontmatter:
                sources.append(_parse_top_level_metadata(frontmatter.group(1)))

            comment = _SKILL_METADATA_COMMENT_RE.search(content)
            if comment:
                sources.append(_parse_top_level_metadata(comment.group(1)))

            for source in sources:
                for key in ("name", "machine_types"):
                    if not metadata.get(key) and source.get(key):
                        metadata[key] = source[key]
        except Exception:
            # Metadata is best-effort; malformed SKILL.md files must not break indexing.
            pass

        return metadata

    def load_hooks(self, skill_id: str) -> list[Hook]:
        """Load hooks for a specific skill — always read fresh from disk so
        editing hooks.json takes effect without a restart."""
        hooks_file = self._skills_dir / skill_id / "hooks.json"
        if hooks_file.exists():
            with hooks_file.open("r", encoding="utf-8") as f:
                hooks_data = json.load(f)
                self._hooks[skill_id] = [Hook(h) for h in hooks_data]
                # Update registry with hook IDs
                if skill_id in self._registry:
                    self._registry[skill_id]["hook_ids"] = [h.id for h in self._hooks[skill_id]]
        else:
            self._hooks[skill_id] = []
        return self._hooks[skill_id]

    def load_interlocks(self, skill_id: str) -> list[SafetyInterlock]:
        """Load safety interlocks — always read fresh from disk."""
        interlocks_file = self._skills_dir / skill_id / "rules/safety_interlocks.json"
        if interlocks_file.exists():
            with interlocks_file.open("r", encoding="utf-8") as f:
                interlocks_data = json.load(f)
                self._interlocks[skill_id] = [SafetyInterlock(i) for i in interlocks_data]
        else:
            self._interlocks[skill_id] = []
        return self._interlocks[skill_id]

    def load_sla_penalties(self, skill_id: str) -> dict[str, Any]:
        """Load SLA penalties — always read fresh from disk."""
        penalties_file = self._skills_dir / skill_id / "rules/sla_penalties.json"
        if penalties_file.exists():
            with penalties_file.open("r", encoding="utf-8") as f:
                self._sla_penalties[skill_id] = json.load(f)
        else:
            self._sla_penalties[skill_id] = {}
        return self._sla_penalties[skill_id]

    def get_hooks_for_machine(self, skill_id: str, machine_id: str) -> list[Hook]:
        """Get hooks relevant to a specific machine"""
        hooks = self.load_hooks(skill_id)
        # For now, return all hooks - in the future, filter by machine types
        return hooks

    def evaluate_telemetry(self, skill_id: str, telemetry: dict[str, Any]) -> list[Hook]:
        """Evaluate all hooks for a skill against current telemetry"""
        hooks = self.load_hooks(skill_id)
        return [h for h in hooks if h.evaluate(telemetry)]

    def evaluate_interlock(self, skill_id: str, telemetry: dict[str, Any]) -> SafetyInterlock | None:
        """Check if any interlock is triggered"""
        interlocks = self.load_interlocks(skill_id)
        for interlock in interlocks:
            if interlock.evaluate(telemetry):
                return interlock
        return None


def evaluate_condition(condition: str, telemetry: dict[str, Any]) -> bool:
    """
    Safely evaluate a condition string against telemetry data.
    Supports: >, <, >=, <=, ==, != operators
    Never uses eval() - parses condition manually.

    Examples:
        "motor_temp_celsius > 85"
        "spindle_vibration_mm_per_s > 5"
        "coolant_pressure_bar < 2.5"
        "emergency_stop_activated = true"
    """
    if not condition:
        return False

    # Replace Python operators with space-separated versions for parsing
    # Handle common operator patterns
    operators = [">=", "<=", ">", "<", "==", "!=", "="]

    # Simple parser for conditions like "metric > value" or "metric = value"
    # Pattern: <metric_name> <operator> <value>
    pattern = r"^(\w+)\s*(>=|<=|>|<|==|!=|=)\s*(.+)$"
    match = re.match(pattern, condition.strip())

    if not match:
        return False

    metric_name, operator, value_str = match.groups()
    metric_name = metric_name.strip()
    value_str = value_str.strip()

    # Get metric value from telemetry
    metric_value = telemetry.get(metric_name)

    # Handle boolean values
    if value_str.lower() == "true":
        value = True
    elif value_str.lower() == "false":
        value = False
    else:
        try:
            value = float(value_str)
        except ValueError:
            # If not a number, treat as string
            value = value_str

    # If metric_value is None, condition can't be satisfied
    if metric_value is None:
        return False

    # Compare based on operator
    try:
        if operator == ">":
            return float(metric_value) > float(value)
        elif operator == "<":
            return float(metric_value) < float(value)
        elif operator == ">=":
            return float(metric_value) >= float(value)
        elif operator == "<=":
            return float(metric_value) <= float(value)
        elif operator == "==" or operator == "=":
            return metric_value == value
        elif operator == "!=":
            return metric_value != value
    except (ValueError, TypeError):
        return False

    return False
