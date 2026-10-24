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

# SAP statuses in which nothing may be written to a machine.
BLOCKED_STATUSES = {"MAINTENANCE", "BREAKDOWN", "FAULT"}


class MachineLimits:
    def __init__(self, path: Path = LIMITS_PATH) -> None:
        self.raw: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
        self._defaults = self.raw.get("defaults", {})

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
) -> list[dict[str, Any]]:
    """Every check a write must pass, in the order an operator would read them."""
    m = limits.machine(machine_id)
    sp = (m or {}).get("setpoints", {}).get(tag)
    current = (row or {}).get("setpoints", {}).get(tag)
    writable = bool(m and m.get("write_enabled", True))
    if not m:
        machine_detail = "tidak ada di file batas"
    else:
        machine_detail = "write_enabled" if writable else (m.get("note") or "write_enabled = false")
    checks = [
        _check("machine", "Mesin terdaftar di file batas dan boleh ditulis", writable, machine_detail),
        _check("setpoint", "Setpoint ada di daftar yang boleh diubah",
               sp is not None, tag if sp else f"{tag} tidak diizinkan"),
    ]
    if sp is not None:
        lo, hi, step = sp.get("min"), sp.get("max"), sp.get("max_step")
        checks.append(_check("range", "Nilai di dalam batas aman", lo <= value <= hi,
                             f"{value:g} (batas {lo:g}–{hi:g}{sp.get('unit', '')})"))
        if current is not None and step is not None:
            checks.append(_check("step", "Perubahan tidak melebihi langkah maksimum",
                                 abs(value - current) <= step,
                                 f"{current:g} → {value:g} (maks ±{step:g})"))
    checks += [
        _check("plc", "PLC terbaca", row is not None, "scan terakhir ada" if row else "mesin tidak terbaca di scan PLC"),
        _check("estop", "Tidak ada emergency stop aktif", not (row or {}).get("estop", False),
               "E-STOP aktif" if (row or {}).get("estop") else "aman"),
        _check("lockout", "Tidak ada lockout/tagout", not (row or {}).get("lockout", False),
               "LOTO terpasang" if (row or {}).get("lockout") else "aman"),
        _check("sap_status", "Status SAP mengizinkan perubahan",
               str(sap_status or "").upper() not in BLOCKED_STATUSES, str(sap_status or "?")),
    ]
    if autonomous:
        action = (m or {}).get("actions", {}).get(action_id or "", {})
        checks += [
            _check("policy", "Aksi masuk daftar autonomi terbatas",
                   bool(action.get("autonomous")) and action.get("tag") == tag and action.get("value") == value,
                   action_id or "tanpa aksi"),
            _check("direction", "Hanya mengurangi beban (arah aman)",
                   current is not None and value < current if tag != "feed_hold" else value >= 1,
                   f"{current if current is None else format(current, 'g')} → {value:g}"),
        ]
    else:
        checks.append(_check("approval", "Disetujui supervisor", bool((approved_by or "").strip()),
                             approved_by or "belum disetujui"))
    return checks
