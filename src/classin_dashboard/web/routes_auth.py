"""Login / logout (ADR-0002: credential and fixed modes)."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from ..auth import SESSION_COOKIE
from ..classin import ClassInClient
from .app import AppState, current_session, get_state, render

router = APIRouter()


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
    sid: str = Form(""),
    secret: str = Form(""),
    password: str = Form(""),
    role: str = Form("owner"),
):
    settings = state.settings
    if role not in ("owner", "teacher"):
        role = "owner"

    if settings.auth_mode == "fixed":
        if not settings.access_password or not hmac.compare_digest(
            password, settings.access_password
        ):
            return _login_error(request, state, "접속 비밀번호가 올바르지 않습니다.")
        sid, secret = settings.classin_sid, settings.classin_secret
        if not sid or not secret:
            return _login_error(
                request, state, "서버에 ClassIn 자격증명이 설정되지 않았습니다 (.env 확인)."
            )
    else:
        sid, secret = sid.strip(), secret.strip()
        if not sid or not secret:
            return _login_error(request, state, "SID와 secret을 입력해 주세요.")
        with ClassInClient(
            base_url=settings.classin_base_url, sid=sid, secret=secret
        ) as client:
            ok, reason = client.verify_credentials()
        if not ok:
            return _login_error(request, state, reason)

    cookie = state.sessions.create(sid, secret, role)
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


@router.get("/logout", name="logout")
def logout(request: Request, state: AppState = Depends(get_state)):
    state.sessions.destroy(request.cookies.get(SESSION_COOKIE))
    resp = RedirectResponse(request.url_for("login_page"), status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path=state.settings.root_path or "/")
    return resp
