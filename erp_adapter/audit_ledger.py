"""
erp_adapter/audit_ledger.py
───────────────────────────
Append-only JSONL audit ledger with SHA-256 hash chaining.

Each entry is a single JSON line. The sha256_hash field is:
    sha256(previous_entry_hash + current_entry_content)
making the chain tamper-evident.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLedger:
    """
    Append-only ledger persisted as newline-delimited JSON (.jsonl).
    Thread-safe via a single lock shared across all FastAPI handlers.
    """

    def __init__(self, ledger_file=None):
        if ledger_file is None:
            ledger_file = Path(__file__).parent.parent / "audit_ledger.jsonl"
        self._file = Path(ledger_file)
        self._lock = threading.Lock()
        self._entries = self._load_all()

    def _load_all(self):
        if not self._file.exists():
            return []
        entries = []
        with self._file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return entries

    @staticmethod
    def _compute_hash(prev_hash, content):
        """SHA-256(prev_hash + content) for tamper-evident chaining."""
        raw = (prev_hash + content).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _get_prev_hash(self):
        if not self._entries:
            return hashlib.sha256(b"SKILLFORGE_GENESIS").hexdigest()
        return self._entries[-1].get("sha256_hash", "")

    def append(self, entry):
        """
        Append a new entry, enriching it with id, timestamp, and sha256_hash.

        Recommended entry keys:
            action_type, scenario_chosen, delta_applied,
            sap_version_before, sap_version_after, approved_by
        """
        with self._lock:
            entry.setdefault("id", str(uuid.uuid4()))
            entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

            content_for_hash = json.dumps(
                {k: v for k, v in entry.items() if k != "sha256_hash"},
                sort_keys=True,
                ensure_ascii=False,
            )
            entry["sha256_hash"] = self._compute_hash(
                self._get_prev_hash(), content_for_hash
            )

            self._file.parent.mkdir(parents=True, exist_ok=True)
            with self._file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

            self._entries.append(entry)
            return entry

    def get_all(self):
        """Return all entries in chronological order."""
        with self._lock:
            return list(self._entries)

    def verify_chain(self):
        """
        Verify the SHA-256 hash chain integrity.

        Returns dict with: valid (bool), total_entries, first_broken_index, message.
        """
        with self._lock:
            if not self._entries:
                return {
                    "valid": True,
                    "total_entries": 0,
                    "first_broken_index": None,
                    "message": "Ledger is empty — chain trivially valid.",
                }

            prev_hash = hashlib.sha256(b"SKILLFORGE_GENESIS").hexdigest()
            for idx, entry in enumerate(self._entries):
                stored_hash = entry.get("sha256_hash", "")
                content = json.dumps(
                    {k: v for k, v in entry.items() if k != "sha256_hash"},
                    sort_keys=True,
                    ensure_ascii=False,
                )
                expected = self._compute_hash(prev_hash, content)
                if stored_hash != expected:
                    return {
                        "valid": False,
                        "total_entries": len(self._entries),
                        "first_broken_index": idx,
                        "message": (
                            f"Chain broken at index {idx} "
                            f"(id={entry.get('id', 'unknown')}). "
                            "Record may have been tampered with."
                        ),
                    }
                prev_hash = stored_hash

            return {
                "valid": True,
                "total_entries": len(self._entries),
                "first_broken_index": None,
                "message": f"Chain intact — all {len(self._entries)} entries verified.",
            }
