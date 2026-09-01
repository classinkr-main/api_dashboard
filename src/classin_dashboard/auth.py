"""Session auth (ADR-0002).

Two modes:
- credential: user logs in with ClassIn SID/secret; we verify against the
  ClassIn API and keep the secret server-side only.
- fixed: SID/secret come from env; users log in with a shared access password
  and pick a role (owner/teacher).

The browser only ever holds a signed session-id cookie.
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
    role: str  # "owner" | "teacher"
    created_at: float = field(default_factory=time.time)


class SessionStore:
    """In-memory sessions; process restart requires re-login (accepted, ADR-0002)."""

    def __init__(self, cookie_secret: str, ttl_hours: int) -> None:
        self._sessions: dict[str, Session] = {}
        self._serializer = URLSafeSerializer(cookie_secret, salt="dash-session")
        self._ttl = ttl_hours * 3600

    def create(self, sid: str, secret: str, role: str) -> str:
        """Store a session and return the signed cookie value."""
        token = secrets.token_urlsafe(32)
        self._sessions[token] = Session(sid=sid, secret=secret, role=role)
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
