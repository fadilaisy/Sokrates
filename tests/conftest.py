"""
tests/conftest.py — keep the suite away from the repo's runtime files.

Without this, every pytest process appends to (and verifies the chain of) the
real `audit_ledger.jsonl`, so tests depend on whatever previous runs left
behind — and a ledger written by two concurrent test processes can never
verify as one hash chain (each process chains onto its own in-memory tail).
Point the app-level ledgers at a per-session temp directory instead; the
product defaults are untouched.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sf_test_ledgers_"))
os.environ.setdefault("AUDIT_LEDGER_PATH", str(_TMP / "audit_ledger.jsonl"))
os.environ.setdefault("MAINTENANCE_LEDGER_PATH", str(_TMP / "maintenance_ledger.jsonl"))
# Startup demo-document seeding writes into the process-wide RAG store; tests that
# need it turn it on themselves (tests/test_seed_startup.py).
os.environ.setdefault("RAG_AUTOSEED_DEMO", "0")
