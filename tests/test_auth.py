"""Login and roles: tokens, route guards, the approver recorded in the ledger."""

import pytest
from fastapi.testclient import TestClient

import backend.auth as auth
import backend.main as m

client = TestClient(m.app)

USERS = ";".join([
    "admin:admin:admin-pw:Admin Demo",
    "budi:maintenance_head:" + auth.hash_password("budi-pw", iterations=1000) + ":Budi Santoso",
    "sari:supervisor:sari-pw:Sari Dewi",
    "tamu:viewer:tamu-pw:Tamu",
])


@pytest.fixture(autouse=True)
def login_on(monkeypatch):
    monkeypatch.setenv("AUTH_USERS", USERS)
    monkeypatch.setenv("AUTH_SECRET", "test-secret")
    auth.reset_failures()
    yield
    auth.reset_failures()


def _token(user, pw):
    r = client.post("/api/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _h(user, pw):
    return {"Authorization": f"Bearer {_token(user, pw)}"}


def test_off_without_accounts(monkeypatch):
    monkeypatch.delenv("AUTH_USERS")
    assert client.get("/api/state").status_code == 200
    assert client.get("/api/auth/config").json()["enabled"] is False
    assert client.get("/health").json()["auth"].startswith("off")


def test_every_api_route_needs_a_token():
    assert client.get("/api/auth/config").json()["enabled"] is True
    assert client.get("/health").status_code == 200
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/plc/scenario", json={"scenario": "normal"}).status_code == 401
    assert client.get("/api/state", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_login_and_me():
    r = client.post("/api/auth/login", json={"username": "BUDI", "password": "budi-pw"})
    assert r.status_code == 200
    user = r.json()["user"]
    assert user["role"] == "maintenance_head" and user["can_approve"] is True
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"}).json()
    assert me["user"]["signature"] == "Budi Santoso — Kepala Maintenance (@budi)"


def test_wrong_password_and_rate_limit():
    for _ in range(auth.LOGIN_MAX_FAILS):
        assert client.post("/api/auth/login", json={"username": "sari", "password": "x"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "sari", "password": "sari-pw"}).status_code == 429


def test_token_from_query_string_for_links():
    assert client.get(f"/api/state?token={_token('tamu', 'tamu-pw')}").status_code == 200


def test_expired_or_changed_role_token_is_refused(monkeypatch):
    user = auth.users()["sari"][0]
    old, _ = auth.issue_token(user, now=0)
    assert auth.verify_token(old) is None
    tok, _ = auth.issue_token(user)
    assert auth.verify_token(tok) == user
    monkeypatch.setenv("AUTH_USERS", USERS.replace("sari:supervisor", "sari:mechanic"))
    assert auth.verify_token(tok) is None


def test_viewer_reads_but_cannot_write():
    h = _h("tamu", "tamu-pw")
    assert client.get("/api/state", headers=h).status_code == 200
    assert client.post("/api/plc/scenario", json={"scenario": "normal"}, headers=h).status_code == 403


def test_only_approvers_approve():
    sup, head = _h("sari", "sari-pw"), _h("budi", "budi-pw")
    for path in ("/api/approve", "/api/agent/runs/x/approve", "/api/ledger/x/revert", "/api/plc/actions",
                 "/api/plc/autonomy", "/api/skills/approve", "/api/costs/requests/x/approve"):
        r = client.post(path, json={}, headers=sup)
        assert r.status_code == 403, path
        assert "Kepala Maintenance" in r.json()["detail"]
        # An approver gets past the guard (the handler then answers on its own terms).
        assert client.post(path, json={}, headers=head).status_code not in (401, 403), path


def test_reset_is_admin_only():
    assert client.post("/api/state/reset", headers=_h("budi", "budi-pw")).status_code == 403
    assert client.post("/api/state/reset", headers=_h("admin", "admin-pw")).status_code == 200


def test_ledger_records_the_logged_in_user_not_the_typed_name():
    h = _h("budi", "budi-pw")
    r = client.post("/api/plc/autonomy", json={"enabled": False, "by": "Someone Else"}, headers=h)
    assert r.status_code == 200, r.text
    entry = next(e for e in reversed(m._ledger.get_all()) if e["id"] == r.json()["receipt_id"])
    assert entry["approved_by"] == "Budi Santoso — Kepala Maintenance (@budi)"


def test_cost_change_needs_two_real_people():
    sari, budi = _h("sari", "sari-pw"), _h("budi", "budi-pw")
    req = client.post("/api/costs/requests", json={"key": "overtime", "value": 460_000, "requested_by": "Budi"},
                      headers=budi).json()
    try:
        # Typing someone else's name no longer gets round the second-person rule.
        assert client.post(f"/api/costs/requests/{req['request_id']}/approve", json={"by": "Rina"},
                           headers=budi).status_code == 403
        assert "@budi" in req["requested_by"]
    finally:
        client.post(f"/api/costs/requests/{req['request_id']}/reject", json={"by": "x"}, headers=budi)
    assert sari  # supervisor may ask for changes but not approve them (covered above)


def test_document_role_comes_from_the_account():
    # A viewer claiming admin in X-User-Role is treated as an operator.
    h = {**_h("tamu", "tamu-pw"), "X-User-Role": "admin"}
    assert client.post("/api/rag/seed-demo", headers=h).status_code == 403
    assert client.post("/api/documents/x/reindex", headers=h).status_code == 403


def test_websocket_needs_a_token():
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/plc"):
            pass
    with client.websocket_connect(f"/ws/plc?token={_token('tamu', 'tamu-pw')}") as ws:
        assert ws.receive_json()


def test_malformed_accounts_fail_closed_with_a_clear_message(monkeypatch):
    monkeypatch.setenv("AUTH_USERS", "budi:boss:pw")
    r = client.get("/api/state")
    assert r.status_code == 503 and "AUTH_USERS" in r.json()["detail"]
    assert client.post("/api/auth/login", json={"username": "budi", "password": "pw"}).status_code == 401


def test_accounts_are_parsed_once_per_value(monkeypatch):
    calls = []
    real = auth._parse_users
    monkeypatch.setattr(auth, "_parse_users", lambda raw: calls.append(raw) or real(raw))
    monkeypatch.setenv("AUTH_USERS", USERS + ";x:viewer:x")
    for _ in range(3):
        auth.users()
    assert len(calls) == 1


def test_approval_records_the_account_even_without_the_field():
    # LedgerEntryId and SkillApproveRequest default approved_by to a placeholder name.
    body = auth.stamp_identity({"id": "abc"}, auth.users()["budi"][0], approval=True)
    assert body["approved_by"] == "Budi Santoso — Kepala Maintenance (@budi)"


def test_bm25_mode_still_applies_document_access():
    from rag.retrieve import IncidentContext, Retriever
    where, params, applied = Retriever.__new__(Retriever)._filters(
        IncidentContext(role="operator"), None, "bm25", False)
    assert "json_each(d.acl)" in where and "operator" in params


def test_inject_accepts_only_known_statuses():
    h = _h("sari", "sari-pw")
    bad = client.post("/api/disrupt/inject", json={"machine_id": "CNC-03", "new_status": "maintenance-ish",
                                                   "disruption_type": "X"}, headers=h)
    assert bad.status_code == 422
    import backend.main as mm
    assert mm.DisruptionInjectRequest(machine_id="CNC-03", new_status="maintenance",
                                      disruption_type="X").new_status == "MAINTENANCE"


def test_identity_is_stamped_whatever_the_json_content_type():
    h = _h("budi", "budi-pw")
    # (No content-type at all is refused by FastAPI with 422, so it never reaches the ledger.)
    for ct in ("APPLICATION/JSON", "application/vnd.x+json"):
        hdr = {**h, **({"Content-Type": ct} if ct else {})}
        r = client.post("/api/plc/autonomy", content=b'{"enabled": false, "by": "Someone Else"}', headers=hdr)
        assert r.status_code == 200, (ct, r.text)
        entry = next(e for e in reversed(m._ledger.get_all()) if e["id"] == r.json()["receipt_id"])
        assert entry["approved_by"].endswith("(@budi)"), ct


def test_read_only_roles_cannot_send_on_the_telemetry_socket():
    received = []

    async def app(scope, receive, send):
        await receive()  # connect
        received.append(await receive())

    mw = auth.AuthMiddleware(app)
    user = auth.users()["tamu"][0]
    tok, _ = auth.issue_token(user)
    msgs = [{"type": "websocket.connect"}, {"type": "websocket.receive", "text": "{}"},
            {"type": "websocket.disconnect", "code": 1000}]

    async def recv():
        return msgs.pop(0)

    async def send(msg):
        pass

    import asyncio
    asyncio.run(mw({"type": "websocket", "path": "/ws/telemetry", "query_string": f"token={tok}".encode(),
                    "headers": []}, recv, send))
    assert received == [{"type": "websocket.disconnect", "code": 1000}]  # the frame was dropped


def test_mechanic_only_logs_cases(monkeypatch):
    monkeypatch.setenv("AUTH_USERS", USERS + ";joko:mechanic:joko-pw:Joko")
    h = _h("joko", "joko-pw")
    assert client.post("/api/plc/interlock", json={"machine_id": "CNC-01", "estop": True}, headers=h).status_code == 403
    assert client.post("/api/plc/scenario", json={"scenario": "normal"}, headers=h).status_code == 403
    assert client.post("/api/maintenance/entries", json={}, headers=h).status_code not in (401, 403)


def test_rate_limit_memory_is_bounded(monkeypatch):
    monkeypatch.setattr(auth, "_FAILS_MAX_KEYS", 50)
    for i in range(200):
        auth.record_failure(f"ip:{i}")
        auth.login_blocked(f"other:{i}")  # checking alone adds nothing
    assert len(auth._FAILS) <= 50


def test_client_ip_uses_forwarded_for_only_when_trusted(monkeypatch):
    hdr = {"x-forwarded-for": "6.6.6.6, 1.2.3.4"}
    assert auth.client_ip("10.0.0.1", hdr) == "10.0.0.1"
    monkeypatch.setenv("AUTH_TRUST_PROXY", "1")
    assert auth.client_ip("10.0.0.1", hdr) == "1.2.3.4"


def test_unknown_username_costs_a_full_hash():
    assert auth._dummy_hash().split("$")[1] == "240000"
