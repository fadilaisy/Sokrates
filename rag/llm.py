"""
rag/llm.py
──────────
Generative LLM clients for the RAG layer, over plain REST (httpx), matching
how backend/main.py already calls Gemini (no SDK dependency).

Providers
---------
aicore   SAP AI Core (AICORE_* + AI_API_URL; see rag/aicore.py). With orchestration
         on (default) the model is AICORE_MODEL_NAME, default gpt-5.4.
gemini   AI Core orchestration as AICORE_GEMINI_MODEL (default gemini-3.5-flash)
         when orchestration is on; otherwise Google Gemini directly
         (GEMINI_API_KEY, GEMINI_MODEL; default gemini-3.5-flash).
claude   AI Core orchestration as AICORE_CLAUDE_MODEL (default
         anthropic--claude-4.5-haiku) when orchestration is on; otherwise
         Anthropic directly (ANTHROPIC_API_KEY, CLAUDE_MODEL; default claude-haiku-4-5).

RAG_GEN_PROVIDER=auto (default) tries AI Core first, then Gemini, then Claude,
then (orchestration on) Gemini and Claude on their own APIs when their keys are set,
skipping any that are not configured, so the demo keeps working if one key is
missing or one API errors.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import httpx

from . import aicore, config

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
    """aicore → gemini → claude. With AI Core orchestration on (the default),
    gemini/claude are served by the same tenant; with orchestration disabled
    (AICORE_ORCHESTRATION_ID="") or AI Core unset, each needs its own vendor key."""
    out = []
    if aicore.configured():
        out.append("aicore")
        if aicore.orchestration_id():
            out.extend(["gemini", "claude"])
            # All three above share one AI Core login; if that tenant is down,
            # the vendors' own APIs are the only way left to answer.
            if config.gemini_key():
                out.append("gemini_direct")
            if config.anthropic_key():
                out.append("claude_direct")
            return out
    if config.gemini_key():
        out.append("gemini")
    if config.anthropic_key():
        out.append("claude")
    return out


def _provider_order(provider: Optional[str]) -> List[str]:
    p = (provider or config.GEN_PROVIDER or "auto").lower()
    avail = available_providers()
    if p in ("aicore", "gemini", "claude"):
        # Honour the choice, but still fall back so a single outage doesn't kill the answer.
        return [p] + [x for x in avail if x != p] if p in avail else [x for x in avail if x != p]
    return avail


def _call_aicore(system: str, user: str, max_tokens: int, json_mode: bool, timeout: float) -> LLMResult:
    model = aicore.model_label()
    try:
        # GPT-5.x counts hidden reasoning against the token budget, so give it headroom.
        text = aicore.ask(user, system=system, max_tokens=max(max_tokens, 2048), timeout=timeout,
                          model=aicore.model_name())
        return LLMResult(True, text=text, provider="aicore", model=model)
    except Exception as exc:
        return LLMResult(False, provider="aicore", model=model, error=str(exc)[:400])


# Orchestration exposes no response_format knob, so json_mode is prompt-steered here;
# complete() still parses the reply with parse_json_loose (fences/chatter tolerated).
JSON_STEER = "\n\nBalas dengan JSON saja, tanpa teks lain."


def _call_aicore_model(provider: str, model: str, system: str, user: str, max_tokens: int,
                       json_mode: bool, timeout: float) -> LLMResult:
    """Run one call under a logical provider name (gemini/claude) via orchestration."""
    if json_mode:
        user = f"{user}{JSON_STEER}"
    try:
        text = aicore.ask(user, system=system, max_tokens=max(max_tokens, 2048), timeout=timeout,
                          model=model)
        return LLMResult(True, text=text, provider=provider, model=model)
    except Exception as exc:
        return LLMResult(False, provider=provider, model=model, error=str(exc)[:400])


# Gemini 2.5+/3.x are thinking models: hidden reasoning counts against
# maxOutputTokens, so a small limit can end with MAX_TOKENS and no text at all.
GEMINI_MIN_OUTPUT_TOKENS = 2048


def gemini_generation_config(max_tokens: int, model: str) -> dict:
    """generationConfig with room for thinking, and low thinking on Gemini 3.x."""
    cfg: dict = {"maxOutputTokens": max(max_tokens, GEMINI_MIN_OUTPUT_TOKENS)}
    level = config.gemini_thinking_level()
    if level and model.startswith("gemini-3"):
        cfg["thinkingConfig"] = {"thinkingLevel": level}
    return cfg


def gemini_text(body: dict) -> Tuple[str, Optional[str]]:
    """(text of all non-thought parts, finishReason) from a generateContent reply."""
    cand = (body.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    return text, cand.get("finishReason")


def gemini_error(exc: Exception) -> str:
    """Short, key-free error: HTTP code plus Google's status/reason/message."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        try:
            err = exc.response.json().get("error") or {}
            reason = next((d.get("reason") for d in err.get("details") or [] if d.get("reason")), None)
            detail = " ".join(x for x in (err.get("status"), reason, (err.get("message") or "")[:200]) if x)
        except Exception:
            detail = exc.response.text[:200]
        if code == 429:
            detail = f"kuota Gemini terlampaui, coba lagi nanti ({detail})"
        msg = f"HTTP {code}: {detail}" if detail else f"HTTP {code}"
    else:
        msg = f"{type(exc).__name__}: {exc}"[:300]
    key = config.gemini_key()
    return msg.replace(key, "***") if key else msg


def _call_gemini(system: str, user: str, max_tokens: int, json_mode: bool, timeout: float) -> LLMResult:
    if aicore.configured() and aicore.orchestration_id():
        # Same tenant call as aicore, different model_name — no vendor key needed.
        return _call_aicore_model("gemini", config.aicore_gemini_model(), system, user,
                                  max_tokens, json_mode, timeout)
    return _call_gemini_direct(system, user, max_tokens, json_mode, timeout)


def _call_gemini_direct(system: str, user: str, max_tokens: int, json_mode: bool,
                        timeout: float) -> LLMResult:
    model = config.gemini_model()
    gen_cfg: dict = {**gemini_generation_config(max_tokens, model), "temperature": 0.1}
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
        text, finish = gemini_text(r.json())
        if not text:
            return LLMResult(False, provider="gemini", model=model,
                             error=f"empty response (finishReason={finish})")
        return LLMResult(True, text=text, provider="gemini", model=model)
    except Exception as exc:
        return LLMResult(False, provider="gemini", model=model, error=gemini_error(exc))


def _call_claude(system: str, user: str, max_tokens: int, json_mode: bool, timeout: float) -> LLMResult:
    if aicore.configured() and aicore.orchestration_id():
        # Same tenant call as aicore, different model_name — no vendor key needed.
        return _call_aicore_model("claude", config.aicore_claude_model(), system, user,
                                  max_tokens, json_mode, timeout)
    return _call_claude_direct(system, user, max_tokens, json_mode, timeout)


def _call_claude_direct(system: str, user: str, max_tokens: int, json_mode: bool,
                        timeout: float) -> LLMResult:
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
        return LLMResult(False, error="Tidak ada LLM yang dikonfigurasi (set AICORE_*, GEMINI_API_KEY atau ANTHROPIC_API_KEY)."), errors
    for p in order:
        fn = {"aicore": _call_aicore, "gemini": _call_gemini, "gemini_direct": _call_gemini_direct,
              "claude_direct": _call_claude_direct}.get(p, _call_claude)
        res = fn(system, user, max_tokens, json_mode, timeout)
        if res.ok and json_mode:
            res.data = parse_json_loose(res.text)
            if res.data is None:
                res.ok, res.error = False, "response was not valid JSON"
        if res.ok:
            return res, errors
        errors.append(f"{res.provider}/{res.model}: {res.error}")
    return LLMResult(False, error="; ".join(errors)), errors
