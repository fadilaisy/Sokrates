"""
rag/llm.py
──────────
Generative LLM clients for the RAG layer, over plain REST (httpx), matching
how backend/main.py already calls Gemini (no SDK dependency).

Providers
---------
gemini   Google Gemini (GEMINI_API_KEY, GEMINI_MODEL; default gemini-3.5-flash)
claude   Anthropic Claude Haiku 4.5 (ANTHROPIC_API_KEY, CLAUDE_MODEL; default claude-haiku-4-5)

RAG_GEN_PROVIDER=auto (default) tries Gemini first and falls back to Claude,
so the demo keeps working if one key is missing or one API errors.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import httpx

from . import config

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    data: Optional[Any] = None
    provider: str = ""
    model: str = ""
    error: Optional[str] = None


def available_providers() -> List[str]:
    out = []
    if config.gemini_key():
        out.append("gemini")
    if config.anthropic_key():
        out.append("claude")
    return out


def _provider_order(provider: Optional[str]) -> List[str]:
    p = (provider or config.GEN_PROVIDER or "auto").lower()
    avail = available_providers()
    if p in ("gemini", "claude"):
        # Honour the choice, but still fall back so a single outage doesn't kill the answer.
        return [p] + [x for x in avail if x != p] if p in avail else [x for x in avail if x != p]
    return avail


def _call_gemini(system: str, user: str, max_tokens: int, json_mode: bool, timeout: float) -> LLMResult:
    model = config.gemini_model()
    gen_cfg: dict = {"maxOutputTokens": max_tokens, "temperature": 0.1}
    if json_mode:
        gen_cfg["responseMimeType"] = "application/json"
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": gen_cfg,
    }
    try:
        r = httpx.post(GEMINI_URL.format(model=model), json=payload, timeout=timeout,
                       headers={"x-goog-api-key": config.gemini_key() or "",
                                "Content-Type": "application/json"})
        r.raise_for_status()
        body = r.json()
        parts = (body.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            return LLMResult(False, provider="gemini", model=model, error="empty response")
        return LLMResult(True, text=text, provider="gemini", model=model)
    except Exception as exc:
        return LLMResult(False, provider="gemini", model=model, error=_err(exc))


def _call_claude(system: str, user: str, max_tokens: int, json_mode: bool, timeout: float) -> LLMResult:
    model = config.claude_model()
    messages = [{"role": "user", "content": user}]
    if json_mode:
        # Prefill forces the reply to start as a JSON object.
        messages.append({"role": "assistant", "content": "{"})
    payload = {"model": model, "max_tokens": max_tokens, "temperature": 0.1,
               "system": system, "messages": messages}
    try:
        r = httpx.post(ANTHROPIC_URL, json=payload, timeout=timeout, headers={
            "x-api-key": config.anthropic_key() or "",
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        })
        r.raise_for_status()
        body = r.json()
        text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
        if json_mode:
            text = "{" + text
        return LLMResult(True, text=text.strip(), provider="claude", model=model)
    except Exception as exc:
        return LLMResult(False, provider="claude", model=model, error=_err(exc))


def _err(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            detail = exc.response.json()
        except Exception:
            detail = exc.response.text[:300]
        return f"HTTP {exc.response.status_code}: {str(detail)[:300]}"
    return f"{type(exc).__name__}: {exc}"[:400]


def parse_json_loose(text: str) -> Optional[Any]:
    """Parse JSON even when wrapped in ``` fences or followed by chatter."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    start = t.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        c = t[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except Exception:
                    return None
    return None


def complete(system: str, user: str, *, max_tokens: int = 1200, json_mode: bool = True,
             provider: Optional[str] = None, timeout: float = 45.0) -> Tuple[LLMResult, List[str]]:
    """Run the prompt against providers in order. Returns (result, errors_from_failed_attempts)."""
    errors: List[str] = []
    order = _provider_order(provider)
    if not order:
        return LLMResult(False, error="Tidak ada LLM yang dikonfigurasi (set GEMINI_API_KEY atau ANTHROPIC_API_KEY)."), errors
    for p in order:
        fn = _call_gemini if p == "gemini" else _call_claude
        res = fn(system, user, max_tokens, json_mode, timeout)
        if res.ok and json_mode:
            res.data = parse_json_loose(res.text)
            if res.data is None:
                res.ok, res.error = False, "response was not valid JSON"
        if res.ok:
            return res, errors
        errors.append(f"{res.provider}/{res.model}: {res.error}")
    return LLMResult(False, error="; ".join(errors)), errors
