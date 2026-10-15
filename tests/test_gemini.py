"""tests/test_gemini.py — Gemini REST handling (mocked httpx, no network)."""

import asyncio

import httpx
import pytest

import backend.main as m
from rag import llm


def _resp(code, body):
    return httpx.Response(code, json=body, request=httpx.Request("POST", "https://x"))


def test_text_joins_parts_and_skips_thoughts():
    body = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"text": "hidden", "thought": True}, {"text": "Halo "}, {"text": "dunia"}]}}]}
    assert llm.gemini_text(body) == ("Halo dunia", "STOP")


def test_text_empty_when_thinking_used_the_budget():
    body = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"role": "model"}}]}
    assert llm.gemini_text(body) == ("", "MAX_TOKENS")


def test_generation_config_has_room_for_thinking(monkeypatch):
    monkeypatch.delenv("GEMINI_THINKING_LEVEL", raising=False)
    cfg = llm.gemini_generation_config(150, "gemini-3.5-flash")
    assert cfg["maxOutputTokens"] >= llm.GEMINI_MIN_OUTPUT_TOKENS
    assert cfg["thinkingConfig"] == {"thinkingLevel": "low"}
    assert "thinkingConfig" not in llm.gemini_generation_config(150, "gemini-2.5-flash")


def test_error_surfaces_google_reason_without_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "SECRETKEY123")
    body = {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                      "message": "API key not valid. SECRETKEY123",
                      "details": [{"reason": "API_KEY_INVALID"}]}}
    resp = _resp(400, body)
    exc = httpx.HTTPStatusError("bad", request=resp.request, response=resp)
    msg = llm.gemini_error(exc)
    assert "HTTP 400" in msg and "API_KEY_INVALID" in msg and "INVALID_ARGUMENT" in msg
    assert "SECRETKEY123" not in msg


def test_background_calls_leave_room_for_interactive(monkeypatch):
    monkeypatch.setattr(m, "GEMINI_RPM", 4)
    monkeypatch.setattr(m, "GEMINI_INTERACTIVE_RESERVE", 2)
    m._gemini_calls.clear()
    try:
        assert [m._gemini_budget_ok(False) for _ in range(3)] == [True, True, False]
        assert m._gemini_budget_ok(True) and m._gemini_budget_ok(True)
        assert not m._gemini_budget_ok(True)
    finally:
        m._gemini_calls.clear()


class _FakeClient:
    def __init__(self, resp):
        self.resp, self.payload = resp, None

    async def post(self, url, json=None, headers=None):
        self.payload = json
        return self.resp


@pytest.fixture
def gemini_only(monkeypatch):
    for k in ("AICORE_AUTH_URL", "AICORE_CLIENT_ID", "AICORE_CLIENT_SECRET", "AI_API_URL"):
        monkeypatch.delenv(k, raising=False)
    m._gemini_calls.clear()
    m._gemini_cache.clear()
    yield monkeypatch
    m._gemini_calls.clear()
    m._gemini_cache.clear()


def test_call_gemini_reports_empty_reply(gemini_only):
    fake = _FakeClient(_resp(200, {"candidates": [{"finishReason": "MAX_TOKENS", "content": {}}]}))
    gemini_only.setattr(m, "_gemini_client", fake)
    out = asyncio.run(m._call_gemini("p-empty", max_tokens=150))
    assert "MAX_TOKENS" in out
    assert fake.payload["generationConfig"]["maxOutputTokens"] >= llm.GEMINI_MIN_OUTPUT_TOKENS


def test_call_gemini_returns_joined_text(gemini_only):
    fake = _FakeClient(_resp(200, {"candidates": [{"finishReason": "STOP",
                                                   "content": {"parts": [{"text": "A"}, {"text": "B"}]}}]}))
    gemini_only.setattr(m, "_gemini_client", fake)
    assert asyncio.run(m._call_gemini("p-ok")) == "AB"
