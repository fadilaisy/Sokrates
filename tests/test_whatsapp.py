"""tests/test_whatsapp.py — WhatsApp Cloud API sending (HTTP mocked) and the mechanic notify path."""

import asyncio

import httpx
import pytest

import backend.main as m
from backend import whatsapp as wa


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data, self.content = status, data, b"x"

    def json(self):
        return self._data


class _Sent(list):
    """Requests posted to Meta; `reply` is what the fake Graph API answers."""
    reply = _Resp(200, {"messages": [{"id": "wamid.1"}]})


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("WHATSAPP_TOKEN", "tok")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "123")
    monkeypatch.delenv("WHATSAPP_DEMO_TO", raising=False)
    monkeypatch.delenv("WHATSAPP_API_VERSION", raising=False)
    sent = _Sent()

    def post(url, json, timeout, headers):
        sent.append((url, json, headers))
        return sent.reply

    monkeypatch.setattr(wa.httpx, "post", post)
    return sent


def test_normalize_and_mask():
    assert wa.normalize("+62-812-5550-0101") == "6281255500101"
    assert wa.normalize("0812 3456 789") == "628123456789"
    assert wa.normalize(None) == ""
    assert wa.mask("6281255500101") == "6281••••••101"


def test_not_configured_sends_nothing(monkeypatch):
    monkeypatch.delenv("WHATSAPP_TOKEN", raising=False)
    monkeypatch.setattr(wa.httpx, "post", lambda *a, **k: pytest.fail("must not call Meta"))
    assert wa.send_text("+62-812-1", "hi")["status"] == "not_configured"


def test_sent_posts_text_message(env):
    res = wa.send_text("+62-812-5550-0101", "Ganti bearing")
    assert res == {"channel": "whatsapp", "to": "6281••••••101", "status": "sent", "message_id": "wamid.1"}
    url, body, headers = env[0]
    assert url == "https://graph.facebook.com/v21.0/123/messages"
    assert body["to"] == "6281255500101" and body["text"]["body"] == "Ganti bearing"
    assert headers["Authorization"] == "Bearer tok"


def test_demo_number_overrides_mechanic(env, monkeypatch):
    monkeypatch.setenv("WHATSAPP_DEMO_TO", "0811111111")
    wa.send_text("+62-812-5550-0101", "x")
    assert env[0][1]["to"] == "62811111111"


def test_meta_error_is_explained(env):
    env.reply = _Resp(400, {"error": {"code": 131047, "message": "Re-engagement message"}})
    res = wa.send_text("0812", "x")
    assert res["status"] == "failed" and res["code"] == 131047 and "24 jam" in res["error"]


def test_network_error_never_raises(env, monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(wa.httpx, "post", boom)
    res = wa.send_text("0812", "x")
    assert res["status"] == "failed" and "ConnectError" in res["error"]


def test_notify_mechanic_logs_once_and_dedupes(env, monkeypatch):
    monkeypatch.setattr(m, "_wa_recent", {})
    mech = {"id": "MECH-1", "name": "Hendra", "whatsapp": "+62-812-5550-0101"}
    before = len(m._ledger.get_all())
    first = asyncio.run(m._notify_mechanic(mech, "Ganti bearing", {"machine_id": "CNC-01"}))
    again = asyncio.run(m._notify_mechanic(mech, "Ganti bearing", {"machine_id": "CNC-01"}))
    assert first["status"] == "sent" and again.get("duplicate") and len(env) == 1
    new = m._ledger.get_all()[before:]
    assert [e["action_type"] for e in new] == ["WHATSAPP_SENT"]
    assert new[0]["mechanic_name"] == "Hendra" and new[0]["machine_id"] == "CNC-01"


def test_notify_mechanic_failure_is_logged(env, monkeypatch):
    monkeypatch.setattr(m, "_wa_recent", {})
    env.reply = _Resp(401, {"error": {"code": 190, "message": "expired"}})
    before = len(m._ledger.get_all())
    res = asyncio.run(m._notify_mechanic({"name": "Ani", "whatsapp": "0812"}, "x", {}))
    assert res["status"] == "failed"
    assert m._ledger.get_all()[before]["action_type"] == "WHATSAPP_FAILED"
