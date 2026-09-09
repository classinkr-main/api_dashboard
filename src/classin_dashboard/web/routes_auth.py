"""Login / logout / 관 전환 (ADR-0002 + ADR-0005).

Resolution order on POST /login:
  1. username given  → local account (users table); role and scope come from it
  2. fixed mode      → shared access password → owner (bootstrap)
  3. credential mode → ClassIn SID/secret → owner
Roles are never picked by the user.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from ..auth import SESSION_COOKIE
from ..classin import ClassInClient
from ..store import verify_password
from .app import AppState, current_session, get_state, render

router = APIRouter()

BAD_LOGIN = "아이디 또는 비밀번호가 올바르지 않습니다."
NO_SERVER_CREDS = "서버에 ClassIn 자격증명이 설정되지 않았습니다 (.env 확인)."


@router.get("/", name="root")
def root(request: Request):
    if current_session(request) is None:
        return RedirectResponse(request.url_for("login_page"), status_code=303)
    return RedirectResponse(request.url_for("dashboard_home"), status_code=303)


@router.get("/login", name="login_page")
def login_page(request: Request, state: AppState = Depends(get_state)):
    return render(request, "login.html", {"auth_mode": state.settings.auth_mode})


@router.post("/login", name="login_submit")
def login_submit(
    request: Request,
    state: AppState = Depends(get_state),
    username: str = Form(""),
    sid: str = Form(""),
    secret: str = Form(""),
    password: str = Form(""),
):
    ip = request.client.host if request.client else "-"
    locked = state.throttle.locked_seconds(ip)
    if locked:
        return _login_error(
            request, state, f"로그인 시도가 많습니다. {locked}초 후 다시 시도하세요."
        )

    username = username.strip()
    if username:
        result = _login_local_user(state, username, password)
    elif state.settings.auth_mode == "fixed":
        result = _login_fixed(state, password)
    else:
        result = _login_credentials(state, sid.strip(), secret.strip())

    if isinstance(result, str):
        state.throttle.record_failure(ip)
        return _login_error(request, state, result)

    state.throttle.reset(ip)
    cookie = state.sessions.create(**result)
    return _session_redirect(request, state, cookie)


def _login_local_user(state: AppState, username: str, password: str) -> dict | str:
    """Local account → session kwargs, or an error message."""
    user = state.events.get_user_by_username(username)
    if user is None or not user["active"] or not verify_password(user["password_hash"], password):
        return BAD_LOGIN
    settings = state.settings
    sid, secret = settings.classin_sid, settings.classin_secret
    branch = state.events.get_branch(user["branch_id"])
    if branch and branch.get("sid") and branch.get("secret"):
        sid, secret = branch["sid"], branch["secret"]
    return {
        "sid": sid,
        "secret": secret,
        "role": user["role"],
        "user_id": user["id"],
        "username": user["username"],
        "branch_id": user["branch_id"],
        "teacher_uid": user["teacher_uid"],
    }


def _login_fixed(state: AppState, password: str) -> dict | str:
    settings = state.settings
    if not settings.access_password or not hmac.compare_digest(
        password, settings.access_password
    ):
        return "접속 비밀번호가 올바르지 않습니다."
    if not settings.classin_sid or not settings.classin_secret:
        return NO_SERVER_CREDS
    return {
        "sid": settings.classin_sid,
        "secret": settings.classin_secret,
        "role": "owner",
        "username": "owner",
    }


def _login_credentials(state: AppState, sid: str, secret: str) -> dict | str:
    if not sid or not secret:
        return "SID와 secret을 입력해 주세요."
    with ClassInClient(
        base_url=state.settings.classin_base_url, sid=sid, secret=secret
    ) as client:
        ok, reason = client.verify_credentials()
    if not ok:
        return reason
    return {"sid": sid, "secret": secret, "role": "owner", "username": f"SID {sid}"}


def _session_redirect(request: Request, state: AppState, cookie: str):
    settings = state.settings
    resp = RedirectResponse(request.url_for("dashboard_home"), status_code=303)
    resp.set_cookie(
        SESSION_COOKIE,
        cookie,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        path=settings.root_path or "/",
    )
    return resp


def _login_error(request: Request, state: AppState, message: str):
    return render(
        request, "login.html", {"auth_mode": state.settings.auth_mode, "error": message}
    )


@router.post("/scope/branch", name="set_branch_scope")
def set_branch_scope(
    request: Request,
    state: AppState = Depends(get_state),
    branch_id: str = Form(""),
):
    """Owner's topbar 관 switch; stored on the session (empty value = 전체)."""
    session = current_session(request)
    if session is None:
        return RedirectResponse(request.url_for("login_page"), status_code=303)
    if session.role == "owner":
        session.branch_filter = int(branch_id) if branch_id.strip().isdigit() else None
    fallback = str(request.url_for("dashboard_home"))
    referer = request.headers.get("referer") or ""
    same_origin = referer.startswith(str(request.base_url))  # never redirect off-site
    return RedirectResponse(referer if same_origin else fallback, status_code=303)


@router.get("/logout", name="logout")
def logout(request: Request, state: AppState = Depends(get_state)):
    state.sessions.destroy(request.cookies.get(SESSION_COOKIE))
    resp = RedirectResponse(request.url_for("login_page"), status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path=state.settings.root_path or "/")
    return resp
