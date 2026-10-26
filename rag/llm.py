"""
rag/llm.py
──────────
Generative LLM clients for the RAG layer, over plain REST (httpx, no SDK).

Providers
---------
aicore   SAP AI Core (AICORE_* + AI_API_URL; see rag/aicore.py). With orchestration
         on (default) it tries AICORE_MODEL_NAME (default gpt-5.4), then each of
         AICORE_FALLBACK_MODELS on the same tenant.
claude   Anthropic directly (ANTHROPIC_API_KEY, CLAUDE_MODEL; default
         claude-haiku-4-5). Optional, for local runs without AI Core.

RAG_GEN_PROVIDER=auto (default) tries AI Core first, then Claude, skipping any
that are not configured.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import httpx

from . import aicore, config

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
    """aicore → claude, each only when configured."""
    out = []
    if aicore.configured():
        out.append("aicore")
    if config.anthropic_key():
        out.append("claude")
    return out


def _provider_order(provider: Optional[str]) -> List[str]:
    p = (provider or config.GEN_PROVIDER or "auto").lower()
    avail = available_providers()
    if p in ("aicore", "claude"):
        # Honour the choice, but still fall back so a single outage doesn't kill the answer.
        return [p] + [x for x in avail if x != p] if p in avail else avail
    return avail


# Orchestration exposes no response_format knob, so json_mode is prompt-steered here;
# complete() still parses the reply with parse_json_loose (fences/chatter tolerated).
JSON_STEER = "\n\nBalas dengan JSON saja, tanpa teks lain."


def _call_aicore(system: str, user: str, max_tokens: int, json_mode: bool, timeout: float,
                 total_timeout: Optional[float] = None) -> LLMResult:
    if json_mode and aicore.orchestration_id():
        user = f"{user}{JSON_STEER}"
    try:
        # GPT-5.x counts hidden reasoning against the token budget, so give it headroom.
        text, model = aicore.ask_any(user, system=system, max_tokens=max(max_tokens, 2048),
                                     timeout=timeout, total_timeout=total_timeout)
        return LLMResult(True, text=text, provider="aicore", model=model)
    except Exception as exc:
        return LLMResult(False, provider="aicore", model=aicore.model_label(), error=str(exc)[:400])


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
             provider: Optional[str] = None, timeout: float = 45.0,
             total_timeout: Optional[float] = None) -> Tuple[LLMResult, List[str]]:
    """Run the prompt against providers in order. Returns (result, errors_from_failed_attempts).

    `timeout` is per model; `total_timeout` caps the AI Core fallback chain as a whole.
    """
    errors: List[str] = []
    order = _provider_order(provider)
    if not order:
        return LLMResult(False, error="Tidak ada LLM yang dikonfigurasi (set AICORE_* atau ANTHROPIC_API_KEY)."), errors
    for p in order:
        if p == "aicore":
            res = _call_aicore(system, user, max_tokens, json_mode, timeout, total_timeout)
        else:
            res = _call_claude(system, user, max_tokens, json_mode, timeout)
        if res.ok and json_mode:
            res.data = parse_json_loose(res.text)
            if res.data is None:
                res.ok, res.error = False, "response was not valid JSON"
        if res.ok:
            return res, errors
        errors.append(f"{res.provider}/{res.model}: {res.error}")
    return LLMResult(False, error="; ".join(errors)), errors
