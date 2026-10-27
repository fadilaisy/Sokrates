"""
rag/aicore.py
─────────────
SAP AI Core client for the hackathon tenant, over plain REST (httpx). This is
the only LLM provider SkillForge uses (rag/llm.py adds an optional direct
Anthropic client for local runs).

Two modes
---------
Orchestration (default): POST <AI_API_URL>/v2/inference/deployments/
<AICORE_ORCHESTRATION_ID>/completion with an orchestration_config body; the
model is picked per request by the string field llm_module_config.model_name
(AICORE_MODEL_NAME, default gpt-5.4) — any of the tenant's 43 chat models is
one call away. The reply is at module_results.llm.choices[0].message.content
(orchestration_result mirrors it). Setting AICORE_ORCHESTRATION_ID="" disables
this and falls back to …
Direct deployment: POST .../deployments/<AICORE_DEPLOYMENT_ID>/chat/completions
(OpenAI style, no model field) with the reply at choices[0].message.content.

How a call works
----------------
1. Log in: POST <AICORE_AUTH_URL>/oauth/token with the client id + secret
   (OAuth client credentials). The answer is a bearer token that lasts hours,
   so it is cached here and only refreshed shortly before it expires.
2. Chat: POST with headers Authorization: Bearer <token> and AI-Resource-Group
   (mandatory on every call). A 401 triggers exactly one re-login + retry.

Env vars (put them in .env, never in a committed file)
------------------------------------------------------
AICORE_AUTH_URL         "url" from the service key (the token server)
AICORE_CLIENT_ID        "clientid"
AICORE_CLIENT_SECRET    "clientsecret"
AI_API_URL              "serviceurls.AI_API_URL"
AICORE_ORCHESTRATION_ID default d8c092efb8980093; "" disables orchestration
AICORE_MODEL_NAME       default gpt-5.4 — model_name for orchestration calls
AICORE_FALLBACK_MODELS  comma-separated model_names tried in order when the main
                        one fails (orchestration only); default
                        anthropic--claude-4.5-sonnet. "" disables fallbacks.
AICORE_DEPLOYMENT_ID    default d1a82575de60e02a (gpt-5.4), direct mode only
AICORE_RESOURCE_GROUP   default "default"
AICORE_MODEL_LABEL      optional display-label override for model_label()
AICORE_CHAT_TIMEOUT     seconds one model gets on a call a person waits for; default 35
AICORE_CHAT_TOTAL_TIMEOUT  seconds for that whole call, fallbacks included; default 50
"""

from __future__ import annotations

import os
import threading
import time
from urllib.parse import urlsplit
from typing import Dict, List, Optional, Tuple

import httpx

API_VERSION = "2024-12-01-preview"
DEFAULT_DEPLOYMENT_ID = "d1a82575de60e02a"      # gpt-5.4 in the hackathon tenant
DEFAULT_ORCHESTRATION_ID = "d8c092efb8980093"   # orchestration → all chat models
DEFAULT_MODEL_NAME = "gpt-5.4"                  # orchestration model_name
DEFAULT_FALLBACK_MODELS = "anthropic--claude-4.5-sonnet"
TOKEN_REFRESH_MARGIN = 300                      # refresh 5 min before expiry
MIN_MODEL_WAIT = 5.0                            # seconds left below which no next model is tried

_token: Optional[str] = None
_token_expires_at = 0.0
_token_lock = threading.Lock()


class AICoreError(RuntimeError):
    """Raised for any AI Core failure. Messages never include the secret or token."""


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def configured() -> bool:
    return all(_env(k) for k in ("AICORE_AUTH_URL", "AICORE_CLIENT_ID",
                                 "AICORE_CLIENT_SECRET", "AI_API_URL"))


def deployment_id() -> str:
    """Direct deployment used when orchestration is off."""
    return _env("AICORE_DEPLOYMENT_ID", DEFAULT_DEPLOYMENT_ID)


def orchestration_id() -> str:
    """Orchestration deployment; "" when the env var is explicitly set empty (direct mode)."""
    raw = os.environ.get("AICORE_ORCHESTRATION_ID")
    if raw is None:                     # unset → tenant default; set-but-empty → disabled
        return DEFAULT_ORCHESTRATION_ID
    return raw.strip()


def model_name() -> str:
    """model_name sent in orchestration calls (per-call override: chat(..., model=...))."""
    return _env("AICORE_MODEL_NAME", DEFAULT_MODEL_NAME)


def fallback_models() -> List[str]:
    """Orchestration model_names tried after model_name(), in order (none in direct mode)."""
    if not orchestration_id():
        return []
    raw = os.environ.get("AICORE_FALLBACK_MODELS")
    raw = DEFAULT_FALLBACK_MODELS if raw is None else raw
    return [m.strip() for m in raw.split(",") if m.strip()]


def models_in_order(first: Optional[str] = None) -> List[str]:
    """`first` (or model_name()), then the fallbacks, without duplicates."""
    out: List[str] = []
    for m in [first or model_name(), *fallback_models()]:
        if m and m not in out:
            out.append(m)
    return out


def interactive_timeouts() -> Tuple[float, float]:
    """(per-model, total) seconds for a call a person is waiting on, such as the chat."""
    def num(name: str, default: float) -> float:
        try:
            return float(_env(name, str(default)))
        except ValueError:
            return default
    return num("AICORE_CHAT_TIMEOUT", 35.0), num("AICORE_CHAT_TOTAL_TIMEOUT", 50.0)


def resource_group() -> str:
    return _env("AICORE_RESOURCE_GROUP", "default")


def model_label() -> str:
    """Display label: AICORE_MODEL_LABEL wins, else the active model (orchestration name
    when orchestration is on, gpt-5.4 — the direct deployment — when it is off)."""
    override = _env("AICORE_MODEL_LABEL")
    if override:
        return override
    return model_name() if orchestration_id() else DEFAULT_MODEL_NAME


def _status_error(what: str, exc: httpx.HTTPStatusError) -> AICoreError:
    # Keep the server's message short; it never echoes our credentials.
    return AICoreError(f"{what} failed: HTTP {exc.response.status_code}: {exc.response.text[:300]}")


def get_token(timeout: float = 20.0) -> str:
    """Return a valid bearer token, fetching a new one only when needed."""
    global _token, _token_expires_at
    with _token_lock:
        if _token and time.time() < _token_expires_at - TOKEN_REFRESH_MARGIN:
            return _token
        if not configured():
            raise AICoreError("AI Core is not configured (set AICORE_AUTH_URL, AICORE_CLIENT_ID, "
                              "AICORE_CLIENT_SECRET and AI_API_URL in .env)")
        url = _env("AICORE_AUTH_URL").rstrip("/")
        if not url.endswith("/oauth/token"):
            url += "/oauth/token"
        try:
            r = httpx.post(url, data={"grant_type": "client_credentials"}, timeout=timeout,
                           auth=(_env("AICORE_CLIENT_ID"), _env("AICORE_CLIENT_SECRET")))
            r.raise_for_status()
            body = r.json()
        except httpx.HTTPStatusError as exc:
            raise _status_error("AI Core login", exc) from None
        except Exception as exc:
            # Host and error only (never the client id/secret) so a wrong
            # AICORE_AUTH_URL or a DNS/network failure is visible in the logs.
            host = urlsplit(url).hostname or url[:80]
            raise AICoreError(f"AI Core login to {host} failed: {type(exc).__name__}: "
                              f"{str(exc)[:200]}") from None
        token = body.get("access_token")
        if not token:
            raise AICoreError("AI Core login returned no access_token")
        _token = token
        _token_expires_at = time.time() + float(body.get("expires_in") or 3600)
        return _token


def reset_token() -> None:
    global _token, _token_expires_at
    with _token_lock:
        _token, _token_expires_at = None, 0.0


def _direct_reply(body: dict) -> Tuple[str, Optional[str]]:
    """Reply text + finish_reason from the OpenAI-style direct deployment shape."""
    choices = body.get("choices") or [{}]
    choice = choices[0] if choices else {}
    text = ((choice.get("message") or {}).get("content") or "").strip()
    return text, choice.get("finish_reason")


def _orchestration_reply(body: dict) -> Tuple[str, Optional[str]]:
    """module_results.llm.choices[0].message.content (orchestration_result mirrors it)."""
    module = ((body.get("module_results") or {}).get("llm")
              or (body.get("orchestration_result") or {}))
    choices = module.get("choices") or [{}]
    choice = choices[0] if choices else {}
    text = ((choice.get("message") or {}).get("content") or "").strip()
    return text, choice.get("finish_reason")


def chat(messages: List[Dict[str, str]], *, max_tokens: int = 1024, timeout: float = 60.0,
         model: Optional[str] = None) -> str:
    """Send messages to AI Core and return the reply text.

    Orchestration mode (default): the model is `model` or AICORE_MODEL_NAME (gpt-5.4),
    always sent as the plain string `model_name` — never an object.
    """
    orch_id = orchestration_id()
    if orch_id:
        chosen = model.strip() if isinstance(model, str) and model.strip() else model_name()
        url = f"{_env('AI_API_URL').rstrip('/')}/v2/inference/deployments/{orch_id}/completion"
        payload = {
            "orchestration_config": {
                "module_configurations": {
                    "templating_module_config": {"template": messages},
                    "llm_module_config": {"model_name": chosen, "model_params": {}},
                }
            }
        }
        what, parse = "AI Core orchestration", _orchestration_reply
    else:
        url = (f"{_env('AI_API_URL').rstrip('/')}/v2/inference/deployments/"
               f"{deployment_id()}/chat/completions?api-version={API_VERSION}")
        payload = {"messages": messages, "max_completion_tokens": max_tokens}
        what, parse = "AI Core chat", _direct_reply

    for attempt in range(2):
        headers = {
            "Authorization": f"Bearer {get_token()}",
            "AI-Resource-Group": resource_group(),
            "Content-Type": "application/json",
        }
        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=timeout)
            if r.status_code == 401 and attempt == 0:
                reset_token()          # token revoked or expired early: log in again once
                continue
            r.raise_for_status()
            body = r.json()
        except httpx.HTTPStatusError as exc:
            raise _status_error(what, exc) from None
        except Exception as exc:
            raise AICoreError(f"{what} failed: {type(exc).__name__}: {str(exc)[:200]}") from None
        text, reason = parse(body)
        if not text:
            # GPT-5.x spends part of the token budget on hidden reasoning, so a very
            # small max_tokens can come back empty (direct mode; orchestration uses
            # the model's own defaults).
            hint = "" if orch_id else "; try a larger max_tokens"
            raise AICoreError(f"{what} returned an empty reply (finish_reason={reason}){hint}")
        return text
    raise AICoreError(f"{what} failed: unauthorized after re-login")


def ask(prompt: str, *, system: Optional[str] = None, max_tokens: int = 1024,
        timeout: float = 60.0, model: Optional[str] = None) -> str:
    """Convenience wrapper: one user prompt (plus optional system prompt) in, text out.

    `model` overrides AICORE_MODEL_NAME for this call (orchestration mode).
    """
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": prompt})
    return chat(messages, max_tokens=max_tokens, timeout=timeout, model=model)


def ask_any(prompt: str, *, system: Optional[str] = None, max_tokens: int = 1024,
            timeout: float = 60.0, first: Optional[str] = None,
            total_timeout: Optional[float] = None) -> Tuple[str, str]:
    """ask() on each model of models_in_order(first) until one answers.

    Returns (text, model_name that answered). A login failure ends the loop at
    once: every model sits behind the same tenant, so trying the next is pointless.
    `total_timeout` caps the whole loop, so a slow tenant can't stack one full
    `timeout` per model (two models at 60 s kept the chat waiting ~2 minutes).
    """
    errors: List[str] = []
    deadline = time.monotonic() + total_timeout if total_timeout else None
    for m in models_in_order(first):
        wait = timeout
        if deadline is not None:
            wait = min(timeout, deadline - time.monotonic())
            if wait < MIN_MODEL_WAIT:
                errors.append(f"{m}: skipped, {total_timeout:.0f}s total timeout reached")
                break
        try:
            return ask(prompt, system=system, max_tokens=max_tokens, timeout=wait, model=m), m
        except AICoreError as exc:
            errors.append(f"{m}: {exc}")
            if "login" in str(exc).lower():
                break
    raise AICoreError("; ".join(errors) or "no AI Core model configured")
