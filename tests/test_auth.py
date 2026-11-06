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
