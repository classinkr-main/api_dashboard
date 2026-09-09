"""관리 (/admin): 관 CRUD · 코스/선생님 → 관 배정 · 계정 관리 (ADR-0005).

owner sees everything; a manager gets the reduced view — creating teacher
accounts for their own 관 only.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from ..scope import ROLES
from .app import AppState, get_state, render, require_role, require_session

router = APIRouter()


def _int_or_none(value: str) -> int | None:
    value = (value or "").strip()
    return int(value) if value.lstrip("-").isdigit() else None


def _back(request: Request, *, msg: str = "", err: str = ""):
    query = urlencode({k: v for k, v in (("msg", msg), ("err", err)) if v})
    url = str(request.url_for("admin_home"))
    return RedirectResponse(f"{url}?{query}" if query else url, status_code=303)


def _guard(request: Request, *roles: str):
    """Resolve the session and check its role. Returns (session, response)."""
    session = require_session(request)
    if isinstance(session, RedirectResponse):
        return None, session
    denied = require_role(request, session, *roles)
    if denied is not None:
        return None, denied
    return session, None


@router.get("/admin", name="admin_home")
def admin_home(
    request: Request,
    state: AppState = Depends(get_state),
    msg: str = "",
    err: str = "",
):
    session, denied = _guard(request, "owner", "manager")
    if denied is not None:
        return denied
    store = state.events
    is_owner = session.role == "owner"
    branches = store.branches()
    teachers = store._all_teachers()
    if not is_owner:
        teachers = [t for t in teachers if t.get("branch_id") == session.branch_id]
    return render(
        request,
        "admin.html",
        {
            "is_owner": is_owner,
            "branches": branches,
            "branch_names": {b["id"]: b["name"] for b in branches},
            "courses": store._all_courses() if is_owner else [],
            "teachers": teachers,
            "users": store.users() if is_owner else _branch_users(store, session.branch_id),
            "roles": ROLES,
            "msg": msg,
            "err": err,
        },
        session=session,
        nav="admin",
    )


def _branch_users(store, branch_id: int | None) -> list[dict]:
    return [u for u in store.users() if u["branch_id"] == branch_id]


# -- 관 관리 -------------------------------------------------------------------


@router.post("/admin/branch/add", name="admin_branch_add")
def admin_branch_add(
    request: Request,
    state: AppState = Depends(get_state),
    name: str = Form(""),
    sid: str = Form(""),
    secret: str = Form(""),
):
    _, denied = _guard(request, "owner")
    if denied is not None:
        return denied
    name = name.strip()
    if not name:
        return _back(request, err="관 이름을 입력하세요.")
    state.events.upsert_branch(name, sid=sid.strip() or None, secret=secret.strip() or None)
    return _back(request, msg=f"관 「{name}」 저장됨")


@router.post("/admin/branch/delete", name="admin_branch_delete")
def admin_branch_delete(
    request: Request,
    state: AppState = Depends(get_state),
    branch_id: int = Form(...),
):
    _, denied = _guard(request, "owner")
    if denied is not None:
        return denied
    if state.events.delete_branch(branch_id):
        return _back(request, msg="관을 삭제했습니다.")
    return _back(request, err="코스·선생님·계정이 배정된 관은 삭제할 수 없습니다.")


# -- 배정 ----------------------------------------------------------------------


@router.post("/admin/course/branch", name="admin_course_branch")
async def admin_course_branch(request: Request, state: AppState = Depends(get_state)):
    """Bulk assign: every `course_branch_<course_id>` field in the form."""
    _, denied = _guard(request, "owner")
    if denied is not None:
        return denied
    form = await request.form()
    changed = _assign(form, "course_branch_", state.events.set_course_branch)
    return _back(request, msg=f"코스 {changed}건의 관 배정을 저장했습니다.")


@router.post("/admin/teacher/branch", name="admin_teacher_branch")
async def admin_teacher_branch(request: Request, state: AppState = Depends(get_state)):
    _, denied = _guard(request, "owner")
    if denied is not None:
        return denied
    form = await request.form()
    changed = _assign(form, "teacher_branch_", state.events.set_teacher_branch)
    return _back(request, msg=f"선생님 {changed}건의 관 배정을 저장했습니다.")


def _assign(form, prefix: str, setter) -> int:
    changed = 0
    for key, value in form.multi_items():
        if not key.startswith(prefix):
            continue
        target = _int_or_none(key.removeprefix(prefix))
        if target is None:
            continue
        setter(target, _int_or_none(str(value)))
        changed += 1
    return changed


# -- 계정 ----------------------------------------------------------------------


@router.post("/admin/user/create", name="admin_user_create")
def admin_user_create(
    request: Request,
    state: AppState = Depends(get_state),
    username: str = Form(""),
    password: str = Form(""),
    role: str = Form("teacher"),
    branch_id: str = Form(""),
    teacher_uid: str = Form(""),
):
    session, denied = _guard(request, "owner", "manager")
    if denied is not None:
        return denied
    username, password = username.strip(), password.strip()
    if not username or not password:
        return _back(request, err="아이디와 비밀번호를 입력하세요.")
    if role not in ROLES:
        return _back(request, err="알 수 없는 역할입니다.")
    branch = _int_or_none(branch_id)
    if session.role == "manager":
        # 관장은 자기 관의 선생님 계정만 만들 수 있다.
        if role != "teacher":
            return _back(request, err="관장은 선생님 계정만 생성할 수 있습니다.")
        branch = session.branch_id
    if state.events.get_user_by_username(username):
        return _back(request, err="이미 있는 아이디입니다.")
    state.events.create_user(
        username,
        password,
        role,
        branch_id=branch,
        teacher_uid=_int_or_none(teacher_uid),
    )
    return _back(request, msg=f"계정 「{username}」을 만들었습니다.")


@router.post("/admin/user/active", name="admin_user_active")
def admin_user_active(
    request: Request,
    state: AppState = Depends(get_state),
    user_id: int = Form(...),
    active: int = Form(1),
):
    session, denied = _guard(request, "owner", "manager")
    if denied is not None:
        return denied
    target = _managed_user(state, session, user_id)
    if target is None:
        return _back(request, err="권한 범위 밖의 계정입니다.")
    state.events.set_user_active(user_id, bool(active))
    return _back(request, msg="계정 상태를 변경했습니다.")


@router.post("/admin/user/password", name="admin_user_password")
def admin_user_password(
    request: Request,
    state: AppState = Depends(get_state),
    user_id: int = Form(...),
    password: str = Form(""),
):
    session, denied = _guard(request, "owner", "manager")
    if denied is not None:
        return denied
    if not password.strip():
        return _back(request, err="새 비밀번호를 입력하세요.")
    target = _managed_user(state, session, user_id)
    if target is None:
        return _back(request, err="권한 범위 밖의 계정입니다.")
    state.events.set_user_password(user_id, password.strip())
    return _back(request, msg="비밀번호를 재설정했습니다.")


def _managed_user(state: AppState, session, user_id: int) -> dict | None:
    """The user row, if this session may manage it."""
    target = next((u for u in state.events.users() if u["id"] == user_id), None)
    if target is None:
        return None
    if session.role == "owner":
        return target
    if target["role"] == "teacher" and target["branch_id"] == session.branch_id:
        return target
    return None
