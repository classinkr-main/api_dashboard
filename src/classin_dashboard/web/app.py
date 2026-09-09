"""FastAPI app factory. Runs behind a reverse proxy at /dash (root_path)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import SESSION_COOKIE, LoginThrottle, Session, SessionStore
from ..classin import ClassInClient
from ..classin.actions import ClassInActions
from ..config import Settings, get_settings
from ..scope import Scope, scope_for
from ..store import EventStore

TEMPLATES_DIR = Path(__file__).parent / "templates"

KST = timezone(timedelta(hours=9))


def _ts_kst(value) -> str:
    """Unix epoch seconds → 'YYYY-MM-DD HH:MM' in KST."""
    if not value:
        return "—"
    try:
        return datetime.fromtimestamp(int(value), tz=KST).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "—"


def _date_kst(value) -> str:
    """ISO datetime string → 'YYYY-MM-DD' in KST."""
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(str(value))
        return dt.astimezone(KST).strftime("%Y-%m-%d")
    except ValueError:
        return str(value)[:10]

ROLE_LABELS = {"owner": "원장/대표", "manager": "관장", "teacher": "선생님"}


class AppState:
    """Per-process singletons shared by routers."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sessions = SessionStore(settings.secret_key, settings.session_ttl_hours)
        self.throttle = LoginThrottle()
        self.events = EventStore(settings.db_path, settings.webhook_raw_dir)
        self.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        self.templates.env.filters["ts_kst"] = _ts_kst
        self.templates.env.filters["date_kst"] = _date_kst

    def client_for(self, session: Session, *, branch_id: int | None = None) -> ClassInClient:
        """ClassIn client for this session; a branch's own SID/secret wins when set."""
        sid, secret = session.sid, session.secret
        branch = self.events.get_branch(branch_id)
        if branch and branch.get("sid") and branch.get("secret"):
            sid, secret = branch["sid"], branch["secret"]
        return ClassInClient(base_url=self.settings.classin_base_url, sid=sid, secret=secret)

    def actions_for(self, session: Session) -> ClassInActions:
        return ClassInActions(self.client_for(session))

    def scope_of(self, session: Session) -> Scope:
        return scope_for(session, self.events)


def get_state(request: Request) -> AppState:
    return request.app.state.dash


def current_session(request: Request) -> Session | None:
    state: AppState = request.app.state.dash
    return state.sessions.resolve(request.cookies.get(SESSION_COOKIE))


def require_session(request: Request) -> Session | RedirectResponse:
    session = current_session(request)
    if session is None:
        return RedirectResponse(request.url_for("login_page"), status_code=303)
    return session


def require_role(request: Request, session: Session, *roles: str):
    """Return a 403 page when the session's role isn't in `roles`, else None."""
    if session.role in roles:
        return None
    return forbidden(
        request,
        session,
        "이 화면은 " + " / ".join(ROLE_LABELS.get(r, r) for r in roles) + " 권한이 필요합니다.",
    )


def forbidden(request: Request, session: Session, message: str):
    """Small 403 page (also used when a row falls outside the session's scope)."""
    return render(
        request, "forbidden.html", {"message": message}, session=session, status_code=403
    )


def render(
    request: Request,
    template: str,
    context: dict | None = None,
    *,
    session: Session | None = None,
    nav: str = "",
    status_code: int = 200,
):
    state: AppState = request.app.state.dash
    ctx = {
        "request": request,
        "session": _session_view(state, session),
        "nav": nav,
        **(context or {}),
    }
    return state.templates.TemplateResponse(request, template, ctx, status_code=status_code)


def _session_view(state: AppState, session: Session | None):
    """Topbar context: role label, the owner's 관 switch, fixed scope labels."""
    if session is None:
        return None
    branches = state.events.branches() if session.role in ("owner", "manager") else []
    home = state.events.get_branch(session.branch_id)
    scope_label = ROLE_LABELS.get(session.role, session.role)
    if session.role == "manager":
        scope_label = f"{(home or {}).get('name', '미지정 관')} · 관장"
    elif session.role == "teacher":
        teacher = state.events.get_teacher(session.teacher_uid)
        scope_label = f"{(teacher or {}).get('name') or session.username} 선생님"
    return {
        "sid": session.sid,
        "role": session.role,
        "username": session.username,
        "role_label": ROLE_LABELS.get(session.role, session.role),
        "scope_label": scope_label,
        "branches": branches,
        "branch_filter": session.branch_filter,
        "is_owner": session.role == "owner",
        "is_admin": session.role in ("owner", "manager"),
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="ClassIn Dashboard", version="0.1.0", root_path=settings.root_path)
    app.state.dash = AppState(settings)

    from . import (
        routes_admin,
        routes_auth,
        routes_create,
        routes_dashboard,
        routes_notify,
        routes_webhook,
    )

    app.include_router(routes_auth.router)
    app.include_router(routes_admin.router)
    app.include_router(routes_dashboard.router)
    app.include_router(routes_create.router)
    app.include_router(routes_notify.router)
    app.include_router(routes_webhook.router)

    @app.get("/health")
    def health(state: AppState = Depends(get_state)) -> dict:
        return {"ok": True, "app": "classin-dashboard"}

    return app
