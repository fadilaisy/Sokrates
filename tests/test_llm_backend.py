"""tests/test_llm_backend.py — backend._call_llm on SAP AI Core (mocked, no network)."""

import asyncio

import pytest

import backend.main as m
from rag import aicore


@pytest.fixture
def llm_env(monkeypatch):
    monkeypatch.setattr(m.aicore, "configured", lambda: True)
    m._llm_calls.clear()
    m._llm_cache.clear()
    yield monkeypatch
    m._llm_calls.clear()
    m._llm_cache.clear()


def test_background_calls_leave_room_for_interactive(monkeypatch):
    monkeypatch.setattr(m, "LLM_RPM", 4)
    monkeypatch.setattr(m, "LLM_INTERACTIVE_RESERVE", 2)
    m._llm_calls.clear()
    try:
        assert [m._llm_budget_ok(False) for _ in range(3)] == [True, True, False]
        assert m._llm_budget_ok(True) and m._llm_budget_ok(True)
        assert not m._llm_budget_ok(True)
    finally:
        m._llm_calls.clear()


def test_call_llm_records_the_model_that_answered(llm_env):
    seen = {}

    def ask_any(prompt, max_tokens, first=None):
        seen["first"] = first
        return "Halo", "anthropic--claude-4.5-sonnet"

    llm_env.setattr(m.aicore, "ask_any", ask_any)

    async def run():
        text = await m._call_llm("p", 64, model="anthropic--claude-4.7-opus")
        return text, m._active_model()

    assert asyncio.run(run()) == ("Halo", "anthropic--claude-4.5-sonnet")
    assert seen["first"] == "anthropic--claude-4.7-opus"


def test_call_llm_error_text_has_ai_prefix(llm_env):
    def ask_any(prompt, max_tokens, first=None):
        raise aicore.AICoreError("AI Core orchestration failed: HTTP 503: busy")

    llm_env.setattr(m.aicore, "ask_any", ask_any)
    out = asyncio.run(m._call_llm("p", 64))
    assert out.startswith(m.LLM_ERROR_PREFIX) and "503" in out


def test_call_llm_not_configured(monkeypatch):
    monkeypatch.setattr(m.aicore, "configured", lambda: False)
    m._llm_cache.clear()
    out = asyncio.run(m._call_llm("p-unconfigured", 64))
    assert out.startswith(m.LLM_ERROR_PREFIX) and "AICORE" in out


def test_chat_hides_raw_provider_error(llm_env):
    async def failing(prompt, max_tokens=512, *, interactive=True, model=None):
        return "[AI error: HTTP 503: UNAVAILABLE] Silakan tinjau skenario solver secara manual."

    llm_env.setattr(m, "_call_llm", failing)
    llm_env.setattr(m, "_chat_sources", lambda message, state: [])
    out = asyncio.run(m.post_chat(m.ChatRequest(message="sop mesin getar")))
    assert "503" not in out.reply
    assert out.model_used == "unavailable"
