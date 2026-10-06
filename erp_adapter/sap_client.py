"""
erp_adapter/sap_client.py
─────────────────────────
SAP S/4HANA mock adapter for SkillForge.

Loads mock_sap_state.json and exposes read/write methods that mimic
the SAP OData-style interface with optimistic-locking via a
monotonically-increasing version number (drift detection).
"""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any


class DriftError(Exception):
    """
    Raised when the caller's expected_version does not match the current
    in-memory version — guards against concurrent updates (optimistic locking).
    """

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"SAP state version mismatch: expected={expected}, actual={actual}. "
            "Reload state and recompute your delta before retrying."
        )


class SAPClient:
    """
    Thread-safe in-process mock of an SAP S/4HANA OData client.

    The JSON file is read once at startup. All mutations go through
    apply_delta() which performs an optimistic-lock check on
    _state["_meta"]["version"] before writing. State is kept in memory;
    the JSON file is never written back (audit trail is in audit_ledger.py).
    """

    def __init__(self, state_file=None):
        if state_file is None:
            state_file = Path(__file__).parent / "mock_sap_state.json"
        self._state_file = Path(state_file)
        self._lock = threading.Lock()
        self._state = self._load()

    def _load(self):
        with self._state_file.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        raw.setdefault("_meta", {})
        raw["_meta"].setdefault("version", 1)
        return raw

    def _deep_copy_state(self):
        return copy.deepcopy(self._state)

    def reset(self):
        """Reset in-memory state back to original file contents."""
        with self._lock:
            self._state = self._load()
            return self._deep_copy_state()

    def get_state(self):
        """Return entire shop-floor state including current version."""
        with self._lock:
            return self._deep_copy_state()

    def get_version(self):
        """Return the current optimistic-lock version number."""
        with self._lock:
            return int(self._state["_meta"]["version"])

    def get_work_centers(self):
        """Return the list of work-center dicts."""
        with self._lock:
            return copy.deepcopy(self._state.get("work_centers", []))

    def get_production_orders(self):
        """Return all production orders."""
        with self._lock:
            return copy.deepcopy(self._state.get("production_orders", []))

    def get_production_order_by_id(self, order_id):
        with self._lock:
            for order in self._state.get("production_orders", []):
                if order["id"] == order_id:
                    return copy.deepcopy(order)
        return None

    def get_work_center_by_id(self, wc_id):
        with self._lock:
            for wc in self._state.get("work_centers", []):
                if wc["id"] == wc_id:
                    return copy.deepcopy(wc)
        return None

    def apply_delta(self, delta, expected_version):
        """
        Apply a structured delta to the in-memory SAP state.

        Parameters
        ----------
        delta : dict
            - work_center_updates: list[dict] — each needs "id"
            - order_updates: list[dict] — each needs "id"
            - meta_updates: dict — merged into _meta
        expected_version : int
            Must match current _meta.version; raises DriftError on mismatch.

        Returns
        -------
        dict  New state snapshot with incremented _meta.version.

        Raises
        ------
        DriftError  If expected_version != current version.
        """
        with self._lock:
            current_version = int(self._state["_meta"]["version"])
            if current_version != expected_version:
                raise DriftError(expected=expected_version, actual=current_version)

            for wc_patch in delta.get("work_center_updates", []):
                wc_id = wc_patch.get("id")
                for wc in self._state.get("work_centers", []):
                    if wc["id"] == wc_id:
                        wc.update({k: v for k, v in wc_patch.items() if k != "id"})
                        break

            for order_patch in delta.get("order_updates", []):
                order_id = order_patch.get("id")
                for order in self._state.get("production_orders", []):
                    if order["id"] == order_id:
                        order.update({k: v for k, v in order_patch.items() if k != "id"})
                        break

            if "meta_updates" in delta:
                self._state["_meta"].update(delta["meta_updates"])

            self._state["_meta"]["version"] = current_version + 1
            return self._deep_copy_state()
