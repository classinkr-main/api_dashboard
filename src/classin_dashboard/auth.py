"""Session auth (ADR-0002, extended by ADR-0005).

Login paths, in the order the login form resolves them:
- local account: username + password against the `users` table; the account
  carries the role (owner/manager/teacher) and its branch/teacher scope.
  ClassIn credentials come from the server (.env) or the branch override.
- fixed: shared access password from env → owner (bootstrap entry point).
- credential: ClassIn SID/secret typed at login and verified → owner.

Roles are never chosen by the user. The browser only ever holds a signed
session-id cookie; ClassIn secrets stay in the server-side session.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from itsdangerous import BadSignature, URLSafeSerializer

SESSION_COOKIE = "dash_session"


@dataclass
class Session:
    sid: str
    secret: str
    role: str  # "owner" | "manager" | "teacher"
    user_id: int | None = None
    username: str = ""
    branch_id: int | None = None  # manager/teacher home branch
    teacher_uid: int | None = None  # teacher identity in ClassIn
    branch_filter: int | None = None  # owner's topbar 관 switch
    created_at: float = field(default_factory=time.time)


class SessionStore:
    """In-memory sessions; process restart requires re-login (accepted, ADR-0002)."""

    def __init__(self, cookie_secret: str, ttl_hours: int) -> None:
        self._sessions: dict[str, Session] = {}
        self._serializer = URLSafeSerializer(cookie_secret, salt="dash-session")
        self._ttl = ttl_hours * 3600

    def create(
        self,
        sid: str,
        secret: str,
        role: str,
        *,
        user_id: int | None = None,
        username: str = "",
        branch_id: int | None = None,
        teacher_uid: int | None = None,
    ) -> str:
        """Store a session and return the signed cookie value."""
        token = secrets.token_urlsafe(32)
        self._sessions[token] = Session(
            sid=sid,
            secret=secret,
            role=role,
            user_id=user_id,
            username=username,
            branch_id=branch_id,
            teacher_uid=teacher_uid,
        )
        return self._serializer.dumps(token)

    def resolve(self, cookie_value: str | None) -> Session | None:
        if not cookie_value:
            return None
        try:
            token = self._serializer.loads(cookie_value)
        except BadSignature:
            return None
        session = self._sessions.get(token)
        if session is None:
            return None
        if time.time() - session.created_at > self._ttl:
            self._sessions.pop(token, None)
            return None
        return session

    def destroy(self, cookie_value: str | None) -> None:
        if not cookie_value:
            return
        try:
            token = self._serializer.loads(cookie_value)
        except BadSignature:
            return
        self._sessions.pop(token, None)


class LoginThrottle:
    """Tiny in-memory per-IP brake on password guessing (process-local)."""

    def __init__(self, max_failures: int = 5, lockout_seconds: int = 60) -> None:
        self._max = max_failures
        self._lockout = lockout_seconds
        self._failures: dict[str, tuple[int, float]] = {}

    def locked_seconds(self, ip: str) -> int:
        count, last = self._failures.get(ip, (0, 0.0))
        if count < self._max:
            return 0
        remaining = self._lockout - (time.time() - last)
        if remaining <= 0:
            self._failures.pop(ip, None)
            return 0
        return int(remaining) + 1

    def record_failure(self, ip: str) -> None:
        count, _ = self._failures.get(ip, (0, 0.0))
        self._failures[ip] = (count + 1, time.time())

    def reset(self, ip: str) -> None:
        self._failures.pop(ip, None)
