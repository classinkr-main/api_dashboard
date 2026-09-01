"""FastAPI app factory. Runs behind a reverse proxy at /dash (root_path)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import SESSION_COOKIE, Session, SessionStore
from ..classin import ClassInClient
from ..classin.actions import ClassInActions
from ..config import Settings, get_settings
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

ROLE_LABELS = {"owner": "원장/대표", "teacher": "선생님"}


class AppState:
    """Per-process singletons shared by routers."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sessions = SessionStore(settings.secret_key, settings.session_ttl_hours)
        self.events = EventStore(settings.db_path, settings.webhook_raw_dir)
        self.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        self.templates.env.filters["ts_kst"] = _ts_kst
        self.templates.env.filters["date_kst"] = _date_kst

    def client_for(self, session: Session) -> ClassInClient:
        return ClassInClient(
            base_url=self.settings.classin_base_url,
            sid=session.sid,
            secret=session.secret,
        )

    def actions_for(self, session: Session) -> ClassInActions:
        return ClassInActions(self.client_for(session))


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


def render(
    request: Request,
    template: str,
    context: dict | None = None,
    *,
    session: Session | None = None,
    nav: str = "",
):
    state: AppState = request.app.state.dash
    ctx = {
        "request": request,
        "session": _session_view(session),
        "nav": nav,
        **(context or {}),
    }
    return state.templates.TemplateResponse(request, template, ctx)


def _session_view(session: Session | None):
    if session is None:
        return None
    return {
        "sid": session.sid,
        "role": session.role,
        "role_label": ROLE_LABELS.get(session.role, session.role),
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="ClassIn Dashboard", version="0.1.0", root_path=settings.root_path)
    app.state.dash = AppState(settings)

    from . import routes_auth, routes_create, routes_dashboard, routes_notify, routes_webhook

    app.include_router(routes_auth.router)
    app.include_router(routes_dashboard.router)
    app.include_router(routes_create.router)
    app.include_router(routes_notify.router)
    app.include_router(routes_webhook.router)

    @app.get("/health")
    def health(state: AppState = Depends(get_state)) -> dict:
        return {"ok": True, "app": "classin-dashboard"}

    return app
