"""tests/test_telegram.py — Telegram bot sending (HTTP mocked) and the mechanic notify path."""

import asyncio

import httpx
import pytest

import backend.main as m
from backend import telegram as tg


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data, self.content = status, data, b"x"

    def json(self):
        return self._data


class _Sent(list):
    """Requests posted to Telegram; `reply` is what the fake Bot API answers."""
    reply = _Resp(200, {"ok": True, "result": {"message_id": 7}})


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot:tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
    sent = _Sent()

    def post(url, json, timeout):
        sent.append((url, json))
        return sent.reply

    monkeypatch.delenv("PUBLIC_APP_URL", raising=False)
    monkeypatch.setattr(tg.httpx, "post", post)
    monkeypatch.setattr(m, "_notify_recent", {})
    return sent


def test_mask():
    assert tg.mask("987654321") == "••••••321"
    assert tg.mask("12") == "12"


def test_not_configured_sends_nothing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(tg.httpx, "post", lambda *a, **k: pytest.fail("must not call Telegram"))
    assert tg.send_text("hi")["status"] == "not_configured"


def test_sent_posts_message(env):
    assert tg.send_text("Ganti bearing") == {"channel": "telegram", "to": "••••••321", "status": "sent", "message_id": 7}
    url, body = env[0]
    assert url == "https://api.telegram.org/botbot:tok/sendMessage"
    assert body == {"chat_id": "987654321", "text": "Ganti bearing"}


@pytest.mark.parametrize("status, expect", [(403, "Start"), (401, "token"), (400, "chat not found")])
def test_errors_are_explained(env, status, expect):
    env.reply = _Resp(status, {"ok": False, "description": "Bad Request: chat not found"})
    res = tg.send_text("x")
    assert res["status"] == "failed" and res["code"] == status and expect in res["error"]


def test_network_error_never_raises_or_leaks_token(env, monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("https://api.telegram.org/botbot:tok/sendMessage")

    monkeypatch.setattr(tg.httpx, "post", boom)
    res = tg.send_text("x")
    assert res == {"channel": "telegram", "to": "••••••321", "status": "failed", "error": "ConnectError"}


def test_notify_mechanic_sends_alert_then_checklist_once(env):
    mech = {"id": "MECH-1", "name": "Hendra"}
    before = len(m._ledger.get_all())
    first = asyncio.run(m._notify_mechanic(mech, ["alert", "steps"], {"machine_id": "CNC-01"}))
    again = asyncio.run(m._notify_mechanic(mech, ["alert", "steps"], {"machine_id": "CNC-01"}))
    assert first["status"] == "sent" and first["message_ids"] == [7, 7]
    assert again.get("duplicate") and [b["text"] for _, b in env] == ["alert", "steps"]
    assert all("reply_markup" not in b for _, b in env)             # no public URL, no button
    new = m._ledger.get_all()[before:]
    assert [e["action_type"] for e in new] == ["TELEGRAM_SENT"]
    assert new[0]["mechanic_name"] == "Hendra" and new[0]["machine_id"] == "CNC-01"


def test_checklist_gets_playbook_button_on_public_url(env, monkeypatch):
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.example.com/")
    asyncio.run(m._notify_mechanic({"id": "MECH-3"}, ["alert", "steps"], {}))
    alert, steps = (b for _, b in env)
    assert "reply_markup" not in alert
    assert steps["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://app.example.com/#maintenance"


def test_playbook_button_opens_the_assigned_case(env, monkeypatch):
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.example.com")
    asyncio.run(m._notify_mechanic({"id": "MECH-4"}, ["alert", "steps"], {"maintenance_case_id": "CASE-0031"}))
    _, steps = (b for _, b in env)
    assert steps["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://app.example.com/#maintenance/CASE-0031"


def test_notify_mechanic_failure_is_logged_and_stops(env):
    env.reply = _Resp(403, {"ok": False, "description": "Forbidden"})
    before = len(m._ledger.get_all())
    res = asyncio.run(m._notify_mechanic({"id": "MECH-2", "name": "Ani"}, ["alert", "steps"], {}))
    assert res["status"] == "failed" and len(env) == 1
    assert m._ledger.get_all()[before]["action_type"] == "TELEGRAM_FAILED"


def test_notify_mechanic_not_configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    res = asyncio.run(m._notify_mechanic({"id": "MECH-1"}, ["x"], {}))
    assert res == {"channel": "telegram", "status": "not_configured"}
