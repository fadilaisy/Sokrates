"""
backend/telegram.py
───────────────────
Sends the mechanic's repair message through a Telegram bot (Bot API).

Env:
TELEGRAM_BOT_TOKEN  token from @BotFather
TELEGRAM_CHAT_ID    chat that receives the messages (the demo phone; mechanics in the
                    roster have no Telegram ids). Find it by sending the bot any message,
                    then open https://api.telegram.org/bot<token>/getUpdates → message.chat.id

Once the person has pressed Start on the bot, it can message them at any time. Without
the token and chat id nothing is sent and the result says "not_configured", so the flow
still runs and the UI says the message was only prepared.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

import httpx

API_URL = "https://api.telegram.org"
TIMEOUT_S = 10.0


def configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and os.getenv("TELEGRAM_CHAT_ID", "").strip())


def mask(chat_id: str) -> str:
    return "•" * max(0, len(chat_id) - 3) + chat_id[-3:] if len(chat_id) > 3 else chat_id


def send_text(body: str, button: Optional[Tuple[str, str]] = None) -> Dict[str, Any]:
    """Send one message to TELEGRAM_CHAT_ID, optionally with one (label, url) link button.

    Never raises: status sent / failed / not_configured.
    """
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    out: Dict[str, Any] = {"channel": "telegram", "to": mask(chat)}
    if not configured():
        return {**out, "status": "not_configured"}
    url = f"{API_URL}/bot{os.environ['TELEGRAM_BOT_TOKEN'].strip()}/sendMessage"
    try:
        payload: Dict[str, Any] = {"chat_id": chat, "text": body[:4096]}
        if button:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": button[0], "url": button[1]}]]}
        r = httpx.post(url, json=payload, timeout=TIMEOUT_S)
        data = r.json() if r.content else {}
    except Exception as exc:  # the token is in the URL, so never echo the request
        return {**out, "status": "failed", "error": type(exc).__name__}
    if data.get("ok"):
        return {**out, "status": "sent", "message_id": (data.get("result") or {}).get("message_id")}
    reason = data.get("description") or f"HTTP {r.status_code}"
    if r.status_code == 403:
        reason = "penerima belum menekan Start di bot Telegram SkillForge"
    elif r.status_code == 401:
        reason = "token bot Telegram tidak valid"
    return {**out, "status": "failed", "error": str(reason)[:300], "code": r.status_code}
