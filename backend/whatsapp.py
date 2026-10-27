"""
backend/whatsapp.py
───────────────────
Send the mechanic's repair message over the WhatsApp Cloud API (Meta Graph API).

Env:
WHATSAPP_TOKEN        access token (system-user token, or the 24 h one from API Setup)
WHATSAPP_PHONE_ID     "Phone number ID" of the sending number (API Setup page)
WHATSAPP_DEMO_TO      optional: send every message to this number instead of the
                      mechanic's (the roster numbers are demo numbers)
WHATSAPP_API_VERSION  default v21.0

Without a token and phone id nothing is sent and the result says "not_configured",
so the flow still runs and the UI says the message was only prepared. The free test
number can only send free text to a phone that messaged it in the last 24 hours;
Meta then answers with error 131047, reported here as a plain-language reason.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

import httpx

GRAPH_URL = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v21.0"
TIMEOUT_S = 10.0
_REASONS = {
    131047: "lebih dari 24 jam sejak nomor tujuan terakhir mengirim pesan ke nomor WhatsApp SkillForge; "
            "kirim 'hi' dari HP tujuan lalu coba lagi",
    131030: "nomor tujuan belum didaftarkan sebagai penerima di halaman API Setup Meta",
    190: "token WhatsApp kedaluwarsa atau tidak valid",
}


def configured() -> bool:
    return bool(os.getenv("WHATSAPP_TOKEN", "").strip() and os.getenv("WHATSAPP_PHONE_ID", "").strip())


def normalize(number: Any) -> str:
    """'+62-812-5550-0101' / '0812…' → '628125550101' (digits, Indonesian 0 prefix → 62)."""
    digits = re.sub(r"\D", "", str(number or ""))
    return "62" + digits[1:] if digits.startswith("0") else digits


def recipient(mechanic_number: Any) -> str:
    return normalize(os.getenv("WHATSAPP_DEMO_TO", "").strip() or mechanic_number)


def mask(number: str) -> str:
    return number[:4] + "•" * max(0, len(number) - 7) + number[-3:] if len(number) > 7 else number


def send_text(to: Any, body: str) -> Dict[str, Any]:
    """Send one text message. Never raises: returns status sent / failed / not_configured."""
    dest = recipient(to)
    out: Dict[str, Any] = {"channel": "whatsapp", "to": mask(dest)}
    if not configured():
        return {**out, "status": "not_configured"}
    if not dest:
        return {**out, "status": "failed", "error": "nomor tujuan kosong"}
    version = os.getenv("WHATSAPP_API_VERSION", "").strip() or DEFAULT_API_VERSION
    url = f"{GRAPH_URL}/{version}/{os.environ['WHATSAPP_PHONE_ID'].strip()}/messages"
    payload = {"messaging_product": "whatsapp", "to": dest, "type": "text",
               "text": {"preview_url": False, "body": body[:4096]}}
    try:
        r = httpx.post(url, json=payload, timeout=TIMEOUT_S,
                       headers={"Authorization": f"Bearer {os.environ['WHATSAPP_TOKEN'].strip()}"})
        data = r.json() if r.content else {}
    except Exception as exc:  # network trouble must not break the maintenance flow
        return {**out, "status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    if r.status_code < 400 and data.get("messages"):
        return {**out, "status": "sent", "message_id": data["messages"][0].get("id")}
    err = (data.get("error") or {}) if isinstance(data, dict) else {}
    code: Optional[int] = err.get("code")
    reason = _REASONS.get(code) or err.get("message") or f"HTTP {r.status_code}"
    return {**out, "status": "failed", "error": str(reason)[:300], "code": code}
