"""
F1: the demo plant documents are indexed at startup, so a restart or redeploy
does not leave RAG with repair cases only.

    python3 -m pytest tests/test_seed_startup.py -q
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

import backend.main as m
from rag.samples import SEED_ORDER
from rag.store import get_store


def _indexed_demo_files() -> set[str]:
    rows = get_store().query("SELECT filename FROM documents WHERE status = 'indexed'")
    return {r["filename"] for r in rows} & set(SEED_ORDER)


def _start(monkeypatch, tmp_path, flag: str) -> TestClient:
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_AUTOSEED_DEMO", flag)
    monkeypatch.setattr(m, "_autoseed_task", None)
    async def startup() -> None:
        await m._autoseed_demo_documents()
        if m._autoseed_task is not None:
            await m._autoseed_task

    asyncio.run(startup())
    c = TestClient(m.app)
    return c


def test_startup_seeds_demo_documents(monkeypatch, tmp_path):
    c = _start(monkeypatch, tmp_path, "1")
    assert {"SOP_Overheat_Spindle_CNC_rev2.pdf", "Haas_VF-2_Operator_Manual_Spindle_Alarms.pdf"} <= _indexed_demo_files()
    res = c.get("/api/maintenance/search", params={"q": "ALM108"}).json()
    assert len(res["documents"]) >= 1


def test_startup_seed_is_skipped_when_already_indexed(monkeypatch, tmp_path):
    _start(monkeypatch, tmp_path, "1")
    count = get_store().query("SELECT COUNT(*) AS n FROM documents")[0]["n"]
    asyncio.run(m._seed_demo_documents())
    assert get_store().query("SELECT COUNT(*) AS n FROM documents")[0]["n"] == count


def test_startup_seed_can_be_turned_off(monkeypatch, tmp_path):
    c = _start(monkeypatch, tmp_path, "0")
    assert m._autoseed_task is None
    assert _indexed_demo_files() == set()
    res = c.get("/api/maintenance/search", params={"q": "ALM108"}).json()
    assert res["documents"] == []
