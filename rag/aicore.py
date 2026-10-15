"""
rag/aicore.py
─────────────
SAP AI Core client for the hackathon's GPT-5.4 deployment, over plain REST
(httpx), like the Gemini and Claude clients in rag/llm.py.

How a call works
----------------
1. Log in: POST <AICORE_AUTH_URL>/oauth/token with the client id + secret
   (OAuth client credentials). The answer is a bearer token that lasts hours,
   so it is cached here and only refreshed shortly before it expires.
2. Chat:  POST <AI_API_URL>/v2/inference/deployments/<id>/chat/completions
   with headers Authorization: Bearer <token> and AI-Resource-Group.
   The body is OpenAI-style (messages + max_completion_tokens) and the reply
   is in choices[0].message.content.

Env vars (put them in .env, never in a committed file)
------------------------------------------------------
AICORE_AUTH_URL        "url" from the service key (the token server)
AICORE_CLIENT_ID       "clientid"
AICORE_CLIENT_SECRET   "clientsecret"
AI_API_URL             "serviceurls.AI_API_URL"
AICORE_DEPLOYMENT_ID   default d1a82575de60e02a (gpt-5.4)
AICORE_RESOURCE_GROUP  default "default"
"""

from __future__ import annotations

import os
import threading
import time
from typing import Dict, List, Optional

import httpx

API_VERSION = "2024-12-01-preview"
DEFAULT_DEPLOYMENT_ID = "d1a82575de60e02a"   # gpt-5.4 in the hackathon tenant
TOKEN_REFRESH_MARGIN = 300                    # refresh 5 min before expiry

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
    return _env("AICORE_DEPLOYMENT_ID", DEFAULT_DEPLOYMENT_ID)


def resource_group() -> str:
    return _env("AICORE_RESOURCE_GROUP", "default")


def model_label() -> str:
    return _env("AICORE_MODEL_LABEL", "gpt-5.4")


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
            raise AICoreError(f"AI Core login failed: {type(exc).__name__}") from None
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


def chat(messages: List[Dict[str, str]], *, max_tokens: int = 1024, timeout: float = 60.0) -> str:
    """Send OpenAI-style messages to the deployment and return the reply text."""
    url = (f"{_env('AI_API_URL').rstrip('/')}/v2/inference/deployments/"
           f"{deployment_id()}/chat/completions?api-version={API_VERSION}")
    payload = {"messages": messages, "max_completion_tokens": max_tokens}

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
            raise _status_error("AI Core chat", exc) from None
        except Exception as exc:
            raise AICoreError(f"AI Core chat failed: {type(exc).__name__}: {str(exc)[:200]}") from None
        choices = body.get("choices") or [{}]
        text = ((choices[0].get("message") or {}).get("content") or "").strip()
        if not text:
            # GPT-5.x spends part of max_completion_tokens on hidden reasoning,
            # so a very small limit can come back empty.
            reason = choices[0].get("finish_reason")
            raise AICoreError(f"AI Core returned an empty reply (finish_reason={reason}); "
                              "try a larger max_tokens")
        return text
    raise AICoreError("AI Core chat failed: unauthorized after re-login")


def ask(prompt: str, *, system: Optional[str] = None, max_tokens: int = 1024,
        timeout: float = 60.0) -> str:
    """Convenience wrapper: one user prompt (plus optional system prompt) in, text out."""
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": prompt})
    return chat(messages, max_tokens=max_tokens, timeout=timeout)
