"""tests/test_bughunt_track_h.py — regression tests for bug-hunt Track H (H1-H7)."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main as backend_main
from backend.main import app
from solver.schedule_solver import ScheduleSolver

client = TestClient(app)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def reset_state():
    client.post("/api/state/reset")
    yield


def _approve_body(skill_id):
    return {
        "skill_id": skill_id,
        "skill_md": "# x",
        "sla_penalties": {},
        "hooks": [],
        "interlocks": [],
    }


# H1 — path traversal on skill approve / lint
def test_h1_skill_approve_rejects_traversal_and_writes_nothing():
    escaped = ROOT / "x"
    existed = escaped.exists()
    r = client.post("/api/skills/approve", json=_approve_body("../x"))
    assert r.status_code == 422
    assert escaped.exists() == existed
    assert not (ROOT / "skills" / ".." / "x" / "SKILL.md").exists()


def test_h1_skill_lint_invalid_id_does_not_touch_disk():
    before = sorted(p.name for p in ROOT.iterdir())
    r = client.post("/api/skills/lint", json={
        "skill_md": "x", "sla_penalties": {}, "hooks": [], "interlocks": [],
        "base_skill_id": "../x",
    })
    assert r.status_code == 422
    assert sorted(p.name for p in ROOT.iterdir()) == before


# H2 — unknown order_class must not raise in the solver penalty helpers
def test_h2_unknown_order_class_does_not_raise():
    solver = ScheduleSolver()
    orders = [{"id": "O-1", "order_class": "D"}]
    assert solver._total_penalty(orders, 2) == solver._total_penalty(
        [{"id": "O-1", "order_class": "C"}], 2)
    solver._scenario_a(orders, 2, "CNC-01")


# H3 — unknown machine on /api/disrupt is 404 and writes no ledger entries
def test_h3_disrupt_unknown_machine_404_and_no_ledger_writes():
    before = len(client.get("/api/ledger").json())
    r = client.post("/api/disrupt", json={
        "machine_id": "CNC-99", "disruption_type": "BREAKDOWN", "start_hour": 2, "end_hour": 5,
    })
    assert r.status_code == 404
    assert len(client.get("/api/ledger").json()) == before


# H4 — non-object WebSocket payload gets an error frame and the socket stays open
def test_h4_ws_telemetry_list_payload_is_rejected_without_closing():
    with client.websocket_connect("/ws/telemetry") as ws:
        ws.send_text("[1, 2, 3]")
        assert ws.receive_json() == {"error": "Payload must be a JSON object"}
        ws.send_text("[]")
        assert ws.receive_json() == {"error": "Payload must be a JSON object"}


# H5 — Gemini cache is capped at 256 entries
def test_h5_gemini_cache_is_capped(monkeypatch):
    monkeypatch.setattr(backend_main.aicore, "configured", lambda: True)
    monkeypatch.setattr(backend_main.aicore, "ask", lambda prompt, max_tokens: f"reply {prompt}")
    backend_main._gemini_cache.clear()
    try:
        for i in range(300):
            asyncio.run(backend_main._call_gemini(f"prompt-{i}", 64))
        assert len(backend_main._gemini_cache) <= 256
        # Newest entries survive; the oldest were evicted.
        assert ("prompt-299", 64) in backend_main._gemini_cache
        assert ("prompt-0", 64) not in backend_main._gemini_cache
    finally:
        backend_main._gemini_cache.clear()


# H6 — upload is rejected with 413 once the size limit is exceeded (chunked read)
def test_h6_upload_over_limit_returns_413(monkeypatch):
    from rag import config as rag_config
    monkeypatch.setattr(rag_config, "MAX_UPLOAD_MB", 1)
    big = b"a" * (1024 * 1024 + 1)
    r = client.post("/api/documents",
                    files={"file": ("big.txt", big, "text/plain")},
                    headers={"X-User-Role": "supervisor"})
    assert r.status_code == 413


# H7 — default role is "agent" when no X-User-Role header is sent (behavior pinned, not changed)
def test_h7_retrieve_default_role_is_agent(monkeypatch):
    from rag import api as rag_api
    seen = {}

    class FakeRetriever:
        def retrieve(self, incident, query, top_k=None, mode="full", include_superseded=False):
            seen["role"] = incident.get("role")
            return {"results": [], "abstained": True}

    monkeypatch.setattr(rag_api, "Retriever", FakeRetriever)
    r = client.post("/api/retrieve", json={"query": "x"})
    assert r.status_code == 200
    assert seen["role"] == "agent"
