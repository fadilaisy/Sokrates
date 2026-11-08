"""Login, roles and route guards for the SkillForge API.

Accounts come from the ``AUTH_USERS`` environment variable, one per line or
separated by ``;``::

    username:role:password:Display Name

``password`` is either plain text or a hash made with
``python scripts/hash_password.py`` (``pbkdf2_sha256$...``). Roles:

- ``admin``             everything, including resetting the demo state
- ``maintenance_head``  approves SAP writes, PLC writes, skills and cost changes
- ``supervisor``        runs the cockpit and drills, cannot approve SAP writes
- ``planner``           same as supervisor
- ``mechanic``          logs and closes maintenance cases, uses chat
- ``viewer``            read only (GET) plus chat

When ``AUTH_USERS`` is empty, login is off and the API behaves as before, so
existing demo deploys keep working until the accounts are set.

The guard is one ASGI middleware, so every route and WebSocket is covered
without touching each handler. It also:

- replaces the person fields of JSON bodies (``approved_by``, ``by``,
  ``requested_by``, ``author``, ``reported_by``) with the logged-in user, so
  the audit ledger records who really approved, not a typed name;
- sets ``X-User-Role`` for the document library from the account's role, so a
  caller can no longer pick their own role with that header.

Tokens are HMAC-signed (``AUTH_SECRET``) and expire after ``AUTH_TOKEN_HOURS``
(default 12). Without ``AUTH_SECRET`` a random secret is used, so everyone is
logged out when the server restarts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs

log = logging.getLogger("skillforge.auth")

ROLES: Dict[str, str] = {
    "admin": "Admin",
    "maintenance_head": "Kepala Maintenance",
    "supervisor": "Supervisor Produksi",
    "planner": "Planner Maintenance",
    "mechanic": "Mekanik",
    "viewer": "Tamu (lihat saja)",
}

APPROVER_ROLES = frozenset({"admin", "maintenance_head"})

# Role the document library (rag/api.py) understands.
_RAG_ROLE = {
    "admin": "admin",
    "maintenance_head": "supervisor",
    "supervisor": "supervisor",
    "planner": "supervisor",
    "mechanic": "operator",
    "viewer": "operator",
}

# Body fields that name the person acting. The guard overwrites them.
IDENTITY_FIELDS = ("approved_by", "by", "requested_by", "author", "reported_by")

# Writes to SAP, the PLC, approved skills and plant costs: approver roles only.
_APPROVAL_ROUTES = [re.compile(p) for p in (
    r"^/api/approve$",
    r"^/api/agent/runs/[^/]+/approve$",
    r"^/api/ledger/[^/]+/revert$",
    r"^/api/plc/actions$",
    r"^/api/plc/autonomy$",
    r"^/api/skills/approve$",
    r"^/api/costs/requests/[^/]+/(approve|reject)$",
)]
_ADMIN_ROUTES = [re.compile(p) for p in (
    r"^/api/state/reset$",
    r"^/api/rag/seed-demo$",
)]
# POSTs that only read or answer, open to viewers.
_READ_ONLY_POSTS = [re.compile(p) for p in (
    r"^/api/chat$",
    r"^/api/maintenance/chat$",
    r"^/api/retrieve$",
    r"^/api/answer$",
)]
# Writes a mechanic may do: log and close maintenance cases, add expert notes.
_MECHANIC_WRITES = [re.compile(p) for p in (
    r"^/api/maintenance/entries$",
    r"^/api/maintenance/entries/[^/]+/resolve$",
    r"^/api/skills/expert-notes$",
)]
# Reachable without a token.
_PUBLIC = [re.compile(p) for p in (
    r"^/health$",
    r"^/api/auth/login$",
    r"^/api/auth/config$",
    r"^/docs$", r"^/docs/", r"^/redoc$", r"^/openapi\.json$",
)]


@dataclass(frozen=True)
class User:
    username: str
    role: str
    name: str

    @property
    def role_label(self) -> str:
        return ROLES.get(self.role, self.role)

    @property
    def signature(self) -> str:
        """How the user appears in the audit ledger: name, role and username."""
        return f"{self.name} — {self.role_label} (@{self.username})"

    @property
    def can_approve(self) -> bool:
        return self.role in APPROVER_ROLES

    def public(self) -> Dict[str, Any]:
        return {"username": self.username, "name": self.name, "role": self.role,
                "role_label": self.role_label, "can_approve": self.can_approve,
                "signature": self.signature}


# ── Accounts ────────────────────────────────────────────────────────────────

def _parse_users(raw: str) -> Dict[str, Tuple[User, str]]:
    users: Dict[str, Tuple[User, str]] = {}
    for line in re.split(r"[;\n]", raw or ""):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":", 3)
        if len(parts) < 3:
            raise ValueError(f"AUTH_USERS entry needs username:role:password[:name], got {line.split(':', 1)[0]!r}")
        username, role, password = parts[0].strip().lower(), parts[1].strip().lower(), parts[2]
        name = (parts[3] if len(parts) > 3 else "").strip() or username
        if role not in ROLES:
            raise ValueError(f"AUTH_USERS: unknown role {role!r} for {username!r}. Roles: {', '.join(ROLES)}")
        if not username or not password:
            raise ValueError("AUTH_USERS: username and password may not be empty")
        users[username] = (User(username, role, name), password)
    return users


class AuthConfigError(RuntimeError):
    """AUTH_USERS is set but can't be read. Every request is refused until it is fixed."""


_cache: Dict[str, Any] = {"raw": None, "users": {}, "error": None}


def users() -> Dict[str, Tuple[User, str]]:
    """Accounts from AUTH_USERS, parsed once per value (not on every request)."""
    raw = os.getenv("AUTH_USERS", "")
    if raw != _cache["raw"]:
        try:
            _cache.update(raw=raw, users=_parse_users(raw), error=None)
        except ValueError as e:
            _cache.update(raw=raw, users={}, error=str(e))
            log.error("Login: %s. Every API request is refused until AUTH_USERS is fixed.", e)
    if _cache["error"]:
        raise AuthConfigError(_cache["error"])
    return _cache["users"]


def enabled() -> bool:
    try:
        return bool(users())
    except AuthConfigError:
        return True  # fail closed: a broken AUTH_USERS must not open every route


def hash_password(password: str, *, iterations: int = 240_000, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def _check_password(stored: str, given: str) -> bool:
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, it, salt, digest = stored.split("$", 3)
            calc = hashlib.pbkdf2_hmac("sha256", given.encode(), salt.encode(), int(it)).hex()
        except ValueError:
            return False
        return hmac.compare_digest(calc, digest)
    return hmac.compare_digest(stored.encode(), given.encode())


_DUMMY: List[str] = []


def _dummy_hash() -> str:
    if not _DUMMY:
        _DUMMY.append(hash_password(secrets.token_hex(8)))  # default iterations, like a real hash
    return _DUMMY[0]


def authenticate(username: str, password: str) -> Optional[User]:
    try:
        accounts = users()
    except AuthConfigError:
        return None
    entry = accounts.get((username or "").strip().lower())
    if entry is None:
        # Same work as a real check, so timing doesn't reveal which usernames exist.
        _check_password(_dummy_hash(), password or "")
        return None
    user, stored = entry
    return user if _check_password(stored, password or "") else None


# ── Tokens ──────────────────────────────────────────────────────────────────

_RANDOM_SECRET = secrets.token_bytes(32)


def _secret() -> bytes:
    s = os.getenv("AUTH_SECRET", "")
    return s.encode() if s else _RANDOM_SECRET


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(user: User, *, now: Optional[float] = None) -> Tuple[str, int]:
    hours = float(os.getenv("AUTH_TOKEN_HOURS", "12") or 12)
    exp = int((time.time() if now is None else now) + hours * 3600)
    payload = _b64(json.dumps({"u": user.username, "r": user.role, "exp": exp}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}", exp


def verify_token(token: str, *, now: Optional[float] = None) -> Optional[User]:
    try:
        payload, sig = (token or "").split(".", 1)
        want = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(want, sig):
            return None
        data = json.loads(_unb64(payload))
    except Exception:
        return None
    if int(data.get("exp", 0)) < (time.time() if now is None else now):
        return None
    try:
        entry = users().get(str(data.get("u", "")))
    except AuthConfigError:
        return None
    # Account removed or its role changed since the token was issued: log in again.
    if entry is None or entry[0].role != data.get("r"):
        return None
    return entry[0]


# ── Login rate limit ────────────────────────────────────────────────────────

_FAILS: Dict[str, Deque[float]] = defaultdict(deque)
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW_S = 60.0


_FAILS_MAX_KEYS = 10_000


def login_blocked(key: str, *, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    q = _FAILS.get(key)
    if q is None:
        return False  # don't create an entry just for checking
    while q and q[0] < now - LOGIN_WINDOW_S:
        q.popleft()
    if not q:
        del _FAILS[key]
        return False
    return len(q) >= LOGIN_MAX_FAILS


def record_failure(key: str, *, now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    if key not in _FAILS and len(_FAILS) >= _FAILS_MAX_KEYS:
        # Drop keys whose failures have all aged out; if still full, drop the oldest.
        for k in [k for k, q in _FAILS.items() if not q or q[-1] < now - LOGIN_WINDOW_S]:
            del _FAILS[k]
        if len(_FAILS) >= _FAILS_MAX_KEYS:
            del _FAILS[min(_FAILS, key=lambda k: _FAILS[k][-1])]
    _FAILS[key].append(now)


def client_ip(host: Optional[str], headers: Any) -> str:
    """The caller's IP. Behind Caddy every request comes from the proxy, so with
    AUTH_TRUST_PROXY=1 the last X-Forwarded-For hop (the one Caddy adds) is used."""
    if os.getenv("AUTH_TRUST_PROXY", "") == "1":
        fwd = (headers.get("x-forwarded-for") or "").split(",")
        if fwd[-1].strip():
            return fwd[-1].strip()
    return host or "?"


def reset_failures() -> None:
    _FAILS.clear()


# ── Route rules ─────────────────────────────────────────────────────────────

def _matches(path: str, rules: Iterable[re.Pattern]) -> bool:
    return any(r.search(path) for r in rules)


def is_public(path: str) -> bool:
    return _matches(path, _PUBLIC)


def required(method: str, path: str) -> str:
    """What a request needs: 'read', 'write', 'approve' or 'admin'."""
    if _matches(path, _ADMIN_ROUTES):
        return "admin"
    if _matches(path, _APPROVAL_ROUTES):
        return "approve"
    if method in ("GET", "HEAD") or _matches(path, _READ_ONLY_POSTS):
        return "read"
    return "write"


def allowed(user: User, need: str, path: str = "") -> bool:
    if user.role == "admin":
        return True
    if need == "admin":
        return False
    if need == "approve":
        return user.can_approve
    if need == "write":
        if user.role == "viewer":
            return False
        if user.role == "mechanic":
            return _matches(path, _MECHANIC_WRITES)
        return True
    return True


def denial_message(need: str) -> str:
    return {
        "approve": "Hanya Kepala Maintenance atau Admin yang boleh menyetujui perubahan ini.",
        "admin": "Hanya Admin yang boleh melakukan ini.",
        "write": "Akun Anda tidak boleh mengubah ini.",
    }.get(need, "Tidak diizinkan.")


# ── ASGI middleware ─────────────────────────────────────────────────────────

def _token_from_scope(scope: Dict[str, Any]) -> str:
    for k, v in scope.get("headers") or []:
        if k == b"authorization":
            val = v.decode("latin-1")
            if val.lower().startswith("bearer "):
                return val[7:].strip()
    # WebSockets and plain links (file downloads) can't set headers.
    qs = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
    return (qs.get("token") or [""])[0]


def _with_role_header(scope: Dict[str, Any], user: User) -> Dict[str, Any]:
    headers = [(k, v) for k, v in scope.get("headers") or [] if k != b"x-user-role"]
    headers.append((b"x-user-role", _RAG_ROLE.get(user.role, "operator").encode()))
    return {**scope, "headers": headers}


def stamp_identity(body: Any, user: User, *, approval: bool = False) -> Any:
    if isinstance(body, dict):
        for f in IDENTITY_FIELDS:
            if f in body:
                body[f] = user.signature
        # Some approval models default approved_by to a placeholder; never let that reach the ledger.
        if approval:
            body["approved_by"] = user.signature
    return body


class AuthMiddleware:
    """Checks the token and the role for every /api and /ws request when login is on."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        kind = scope.get("type")
        if kind not in ("http", "websocket") or not enabled():
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        method = scope.get("method", "GET") if kind == "http" else "GET"
        if method == "OPTIONS" or is_public(path) or not (path.startswith("/api") or path.startswith("/ws")):
            await self.app(scope, receive, send)
            return

        try:
            users()
        except AuthConfigError as e:
            await _deny(kind, receive, send, 503, f"Login salah dikonfigurasi (AUTH_USERS): {e}")
            return
        user = verify_token(_token_from_scope(scope))
        if user is None:
            await _deny(kind, receive, send, 401, "Silakan login dulu.")
            return
        need = required(method, path)
        if not allowed(user, need, path):
            await _deny(kind, receive, send, 403, denial_message(need))
            return

        scope = _with_role_header(scope, user)
        scope.setdefault("state", {})["user"] = user
        if kind == "websocket" and not allowed(user, "write", ""):
            # /ws/telemetry turns client frames into maintenance cases and playbooks:
            # read-only roles may watch the feed but what they send is dropped.
            receive = _listen_only(receive)
        if kind == "http" and method in ("POST", "PUT", "PATCH") and _has_json_body(scope):
            receive = await _stamped_receive(receive, user, approval=need in ("approve", "admin"))
        await self.app(scope, receive, send)


def _has_json_body(scope: Dict[str, Any]) -> bool:
    """True unless the body is a form or upload. FastAPI parses JSON whatever the
    content-type's case or +json suffix, and even with none, so stamp all of those."""
    for k, v in scope.get("headers") or []:
        if k == b"content-type":
            ct = v.decode("latin-1").strip().lower()
            return not (ct.startswith("multipart/") or ct.startswith("application/x-www-form-urlencoded"))
    return True


def _listen_only(receive: Any) -> Any:
    async def wrapped() -> Dict[str, Any]:
        while True:
            msg = await receive()
            if msg.get("type") != "websocket.receive":
                return msg
    return wrapped


async def _stamped_receive(receive: Any, user: User, *, approval: bool = False) -> Any:
    chunks: List[bytes] = []
    while True:
        msg = await receive()
        if msg["type"] != "http.request":
            break
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body"):
            break
    raw = b"".join(chunks)
    try:
        raw = json.dumps(stamp_identity(json.loads(raw), user, approval=approval)).encode() if raw else raw
    except (ValueError, UnicodeDecodeError):
        pass  # not JSON after all: let FastAPI report the error
    sent = False

    async def replay() -> Dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": raw, "more_body": False}
        return await receive()

    return replay


async def _deny(kind: str, receive: Any, send: Any, code: int, message: str) -> None:
    if kind == "websocket":
        # Closing before accept makes the handshake fail; 4401/4403 tell the client why.
        await receive()  # websocket.connect
        await send({"type": "websocket.close", "code": 4000 + code})
        return
    body = json.dumps({"detail": message}).encode()
    await send({"type": "http.response.start", "status": code,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
