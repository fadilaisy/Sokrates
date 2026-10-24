"""
plc_adapter/limits.py
─────────────────────
The per-machine limits file and the checks that run before any write.

`machine_limits.json` says, per machine, what each tag's warning and critical
values are, which setpoints may be written and within what range, and which
actions SkillForge may propose for an alarm or a trend. `pre_action_checks`
turns that into a checklist; a write goes ahead only when every line passes.

The checks sit on top of the PLC's own safety logic, never instead of it:
SkillForge does not write e-stops, interlocks or safety functions, and refuses
to write at all while a machine is e-stopped or locked out.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Optional

LIMITS_PATH = Path(__file__).with_name("machine_limits.json")

# SAP statuses in which a machine may be written to; anything else (MAINTENANCE,
# BREAKDOWN, FAULT, missing or unknown) blocks the write.
WRITABLE_STATUSES = {"RUNNING", "IDLE"}

# How an action moves its setpoint: "reduce" lowers it (feed or speed override),
# "engage" raises a 0/1 switch (feed hold). Both take load off the machine.
DIRECTIONS = {"reduce", "engage"}


class LimitsError(ValueError):
    """The limits file is malformed; SkillForge refuses to start rather than guess."""


def _number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class MachineLimits:
    def __init__(self, path: Path = LIMITS_PATH, *, raw: Optional[dict[str, Any]] = None) -> None:
        self.raw: dict[str, Any] = raw if raw is not None else json.loads(Path(path).read_text(encoding="utf-8"))
        self._defaults = self.raw.get("defaults", {})
        self.validate()

    def validate(self) -> None:
        """Check every machine's merged limits, so a bad file fails at load and never mid-write."""
        problems: list[str] = []
        for mid in self.raw.get("machines", {}):
            m = self.machine(mid) or {}
            where = f"machines.{mid}"
            if not isinstance(m.get("write_enabled", False), bool):
                problems.append(f"{where}.write_enabled must be true or false")
            for tag, t in m.get("tags", {}).items():
                if not (_number(t.get("warning")) and _number(t.get("critical"))):
                    problems.append(f"{where}.tags.{tag} needs numeric warning and critical")
                if t.get("direction", "rising") not in ("rising", "falling"):
                    problems.append(f"{where}.tags.{tag}.direction must be rising or falling")
            setpoints = m.get("setpoints", {})
            for tag, sp in setpoints.items():
                if not all(_number(sp.get(k)) for k in ("min", "max", "max_step")):
                    problems.append(f"{where}.setpoints.{tag} needs numeric min, max and max_step")
                elif sp["min"] > sp["max"] or sp["max_step"] <= 0:
                    problems.append(f"{where}.setpoints.{tag} has min > max or max_step <= 0")
            for aid, a in m.get("actions", {}).items():
                sp = setpoints.get(a.get("tag"))
                if sp is None:
                    problems.append(f"{where}.actions.{aid}.tag '{a.get('tag')}' is not a listed setpoint")
                elif not _number(a.get("value")) or not (sp.get("min", 0) <= a["value"] <= sp.get("max", 0)):
                    problems.append(f"{where}.actions.{aid}.value must be a number inside the setpoint's range")
                if a.get("direction") not in DIRECTIONS:
                    problems.append(f"{where}.actions.{aid}.direction must be one of {sorted(DIRECTIONS)}")
        if problems:
            raise LimitsError("machine_limits.json: " + "; ".join(problems))

    def machine(self, machine_id: str) -> Optional[dict[str, Any]]:
        """Effective limits for one machine (defaults merged with its overrides), or None if unlisted."""
        override = self.raw.get("machines", {}).get(machine_id)
        if override is None:
            return None
        out = copy.deepcopy(self._defaults)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                for k, v in value.items():
                    out[key][k] = {**out[key].get(k, {}), **v} if isinstance(v, dict) else v
            else:
                out[key] = value
        return out

    def tag_limits(self, machine_id: str) -> dict[str, dict[str, Any]]:
        m = self.machine(machine_id)
        return dict((m or {}).get("tags", {}))

    def action_for(self, machine_id: str, *, alarm_code: Optional[str] = None,
                   metric: Optional[str] = None) -> Optional[dict[str, Any]]:
        """The action the limits file proposes for this alarm code (first) or trending metric."""
        m = self.machine(machine_id)
        if not m:
            return None
        actions = m.get("actions", {})
        for key in ("alarm_codes", "metrics"):
            needle = alarm_code if key == "alarm_codes" else metric
            if not needle:
                continue
            for action_id, a in actions.items():
                if needle in a.get(key, []):
                    return {"action_id": action_id, **a}
        return None

    def to_public(self) -> dict[str, Any]:
        return {mid: self.machine(mid) for mid in self.raw.get("machines", {})}

    def autonomous_actions(self) -> dict[str, list[dict[str, Any]]]:
        """Per writable machine, the actions limited autonomy may run (the merged view the checks use)."""
        out: dict[str, list[dict[str, Any]]] = {}
        for mid in self.raw.get("machines", {}):
            m = self.machine(mid) or {}
            if not m.get("write_enabled", False):
                continue
            acts = [{"action_id": aid, "tag": a["tag"], "value": a["value"], "direction": a["direction"]}
                    for aid, a in m.get("actions", {}).items() if a.get("autonomous")]
            if acts:
                out[mid] = acts
        return out


def already_applied(direction: str, current: Optional[float], value: float) -> bool:
    """True when the setpoint is already at (or past) the action's value in its safe direction."""
    if current is None:
        return False
    return current >= value if direction == "engage" else current <= value


def moves_safely(direction: str, current: Optional[float], value: float) -> bool:
    """True when writing `value` over `current` moves the setpoint in the action's safe direction."""
    if current is None:
        return False
    return value > current if direction == "engage" else value < current


def _check(check_id: str, label: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "label": label, "ok": bool(ok), "detail": detail}


def pre_action_checks(
    limits: MachineLimits,
    *,
    machine_id: str,
    tag: str,
    value: float,
    row: Optional[dict[str, Any]],
    sap_status: Optional[str],
    approved_by: Optional[str],
    autonomous: bool = False,
    action_id: Optional[str] = None,
    writes_enabled: bool = True,
) -> list[dict[str, Any]]:
    """Every check a write must pass, in the order an operator would read them."""
    m = limits.machine(machine_id)
    sp = (m or {}).get("setpoints", {}).get(tag)
    current = (row or {}).get("setpoints", {}).get(tag)
    writable = bool(m and m.get("write_enabled", False))
    if not m:
        machine_detail = "tidak ada di file batas"
    else:
        machine_detail = "boleh ditulis" if writable else (m.get("note") or "write_enabled = false")
    action = (m or {}).get("actions", {}).get(action_id) if action_id else None
    checks = [
        _check("server", "Penulisan PLC diaktifkan di server", writes_enabled,
               "aktif" if writes_enabled else "PLC_WRITES_ENABLED mati"),
        _check("machine", "Mesin terdaftar di file batas dan boleh ditulis", writable, machine_detail),
        _check("setpoint", "Setpoint ada di daftar yang boleh diubah",
               sp is not None, f"diizinkan ({tag})" if sp else f"{tag} tidak diizinkan"),
    ]
    if action_id:
        checks.append(_check("action", "Sesuai aksi di file batas",
                             bool(action) and action.get("tag") == tag and action.get("value") == value,
                             action_id if action else f"aksi {action_id} tidak ada untuk {machine_id}"))
    if sp is not None:
        lo, hi, step = sp["min"], sp["max"], sp["max_step"]
        checks.append(_check("range", "Nilai di dalam batas aman", lo <= value <= hi,
                             f"{value:g} (batas {lo:g}–{hi:g}{sp.get('unit', '')})"))
        if current is None:
            checks.append(_check("step", "Perubahan tidak melebihi langkah maksimum", False,
                                 "setpoint sekarang tidak terbaca dari PLC"))
        else:
            checks.append(_check("step", "Perubahan tidak melebihi langkah maksimum",
                                 abs(value - current) <= step,
                                 f"{current:g} → {value:g} (maks ±{step:g})"))
            # A second approval of the same change (another tab, a double click) must not write again.
            checks.append(_check("change", "Nilai berbeda dari setpoint sekarang", value != current,
                                 "berubah" if value != current else f"sudah {current:g}"))
    estop, lockout = (row or {}).get("estop", False), (row or {}).get("lockout", False)
    status = str(sap_status or "").upper()
    checks += [
        _check("plc", "PLC terbaca", row is not None, "scan terakhir ada" if row else "mesin tidak terbaca di scan PLC"),
        # None means the controller has the bit but it could not be read: treat it as unsafe.
        _check("estop", "Tidak ada emergency stop aktif", estop is False,
               "aman" if estop is False else ("E-STOP aktif" if estop else "status e-stop tidak terbaca")),
        _check("lockout", "Tidak ada lockout/tagout", lockout is False,
               "aman" if lockout is False else ("LOTO terpasang" if lockout else "status lockout tidak terbaca")),
        _check("sap_status", "Status SAP mengizinkan perubahan", status in WRITABLE_STATUSES, status or "?"),
    ]
    if autonomous:
        direction = (action or {}).get("direction", "reduce")
        checks += [
            _check("policy", "Aksi masuk daftar autonomi terbatas",
                   bool(action and action.get("autonomous")) and action.get("tag") == tag and action.get("value") == value,
                   action_id or "tanpa aksi"),
            _check("direction", "Hanya mengurangi beban (arah aman)", moves_safely(direction, current, value),
                   f"{'?' if current is None else format(current, 'g')} → {value:g}"),
        ]
    else:
        checks.append(_check("approval", "Disetujui supervisor", bool((approved_by or "").strip()),
                             approved_by or "belum disetujui"))
    return checks
