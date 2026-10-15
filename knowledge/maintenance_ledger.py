"""
knowledge/maintenance_ledger.py
───────────────────────────────
Hash-chained ledger of maintenance cases — the shop floor's repair memory.

Reuses the audit ledger's SHA-256 chaining (`AuditLedger`) with its own file, so
every case, resolution and dispatched playbook is tamper-evident and citable by
`id` + `sha256_hash`.

Entry types (field `entry_type`):
    CASE           an incident / repair record (OPEN until resolved)
    CASE_RESOLVED  closes a CASE (append-only: the case view merges it)
    PLAYBOOK       output of the proactive flow for one CASE (audit of what was sent)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

from erp_adapter.audit_ledger import AuditLedger

_HERE = Path(__file__).parent
DEFAULT_LEDGER = _HERE.parent / "maintenance_ledger.jsonl"
DEFAULT_SEED = _HERE / "maintenance_seed.json"
DEFAULT_MECHANICS = _HERE / "mechanics.json"


class MaintenanceLedger:
    def __init__(self, ledger_file: Optional[str | Path] = None, seed_file: Optional[Path] = DEFAULT_SEED) -> None:
        self._chain = AuditLedger(ledger_file or DEFAULT_LEDGER)
        self._lock = threading.Lock()
        if not self._chain.get_all() and seed_file and Path(seed_file).is_file():
            self._seed(Path(seed_file))

    # ── seeding / append ────────────────────────────────────────────────────
    def _seed(self, seed_file: Path) -> None:
        data = json.loads(seed_file.read_text(encoding="utf-8"))
        for case in sorted(data.get("cases", []), key=lambda c: c.get("logged_at", "")):
            entry = dict(case)
            entry["timestamp"] = entry.get("logged_at")
            self._chain.append(entry)

    def _next_id(self, prefix: str) -> str:
        n = sum(1 for e in self._chain.get_all() if str(e.get("id", "")).startswith(prefix + "-"))
        return f"{prefix}-{n + 1:04d}"

    def append(self, entry_type: str, prefix: str, fields: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            entry = dict(fields)
            entry["entry_type"] = entry_type
            entry["id"] = self._next_id(prefix)
            return self._chain.append(entry)

    # ── reads ───────────────────────────────────────────────────────────────
    def raw(self) -> list[dict[str, Any]]:
        return self._chain.get_all()

    def version(self) -> int:
        return len(self._chain.get_all())

    def cases(self) -> list[dict[str, Any]]:
        """CASE entries with any CASE_RESOLVED entry merged in (latest wins)."""
        entries = self._chain.get_all()
        resolutions = {e["case_id"]: e for e in entries if e.get("entry_type") == "CASE_RESOLVED"}
        out = []
        for e in entries:
            if e.get("entry_type") != "CASE":
                continue
            case = dict(e)
            res = resolutions.get(e["id"])
            if res:
                for k in ("root_cause", "fix_steps", "part_codes", "downtime_hours", "mechanic_id",
                          "outcome", "safety_notes"):
                    if res.get(k) not in (None, "", []):
                        case[k] = res[k]
                case["status"] = "RESOLVED"
                case["resolved_by_ledger_id"] = res["id"]
                case["resolved_hash"] = res.get("sha256_hash")
            out.append(case)
        return out

    def get_case(self, case_id: str) -> Optional[dict[str, Any]]:
        for c in self.cases():
            if c["id"] == case_id:
                return c
        return None

    def get_entry(self, entry_id: str) -> Optional[dict[str, Any]]:
        for e in self._chain.get_all():
            if e.get("id") == entry_id:
                return e
        return None

    def playbooks(self) -> list[dict[str, Any]]:
        return [e for e in self._chain.get_all() if e.get("entry_type") == "PLAYBOOK"]

    def verify(self) -> dict[str, Any]:
        return self._chain.verify_chain()


def load_mechanics(path: Path = DEFAULT_MECHANICS) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")).get("mechanics", [])
