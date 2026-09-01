"""알림 (선생님): 숙제 미제출 조회 → 문구 생성 → 발송 기록.

확인된 제약: ClassIn 파트너 API에는 원격 메시지 발송 기능이 없다 (2026-09 기준,
IM 그룹은 자동 생성만 되고 쓰기 API 없음). 따라서 여기서는 문구를 생성해 기록으로
남기고(복사해 카톡/문자 발송), 알림톡 등 외부 채널 연동은 Layer 5(dispatcher)에
플러그인으로 붙인다.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from .. import metrics
from ..intelligence.notify_copy import compose_ai_messages, compose_template_messages
from ..notify.dispatcher import OutgoingMessage, dispatch
from .app import AppState, get_state, require_session, render

log = logging.getLogger(__name__)
router = APIRouter()

ACADEMY_NAME_DEFAULT = "우리 학원"


@router.get("/notify", name="notify_home")
def notify_home(
    request: Request,
    state: AppState = Depends(get_state),
    window_hours: int = 48,
):
    session = require_session(request)
    if isinstance(session, RedirectResponse):
        return session
    window_hours = max(1, min(window_hours, 24 * 14))
    rows = metrics.missing_homework_rows(state.events, window_hours=window_hours)
    history = state.events.notification_history(limit=50)
    return render(
        request,
        "notify.html",
        {
            "rows": rows,
            "history": history,
            "window_hours": window_hours,
            "ai_available": bool(state.settings.anthropic_api_key),
        },
        session=session,
        nav="notify",
    )


@router.post("/notify/compose", name="notify_compose")
def notify_compose(
    request: Request,
    state: AppState = Depends(get_state),
    window_hours: int = Form(48),
    mode: str = Form("template"),  # template | ai
    tone: str = Form("soft"),
    academy_name: str = Form(ACADEMY_NAME_DEFAULT),
):
    session = require_session(request)
    if isinstance(session, RedirectResponse):
        return session
    rows = metrics.missing_homework_rows(state.events, window_hours=window_hours)
    if not rows:
        return RedirectResponse(
            f"{request.url_for('notify_home')}?window_hours={window_hours}", status_code=303
        )
    by_student: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_student[r["student_uid"]].append(r)

    error = None
    if mode == "ai" and state.settings.anthropic_api_key:
        try:
            messages = compose_ai_messages(state.settings, by_student, academy_name)
        except Exception as exc:
            log.exception("AI compose failed; falling back to template")
            error = f"AI 문구 생성 실패({exc}); 템플릿으로 대체했습니다."
            messages = compose_template_messages(by_student, academy_name, tone)
    else:
        messages = compose_template_messages(by_student, academy_name, tone)

    preview = [
        {
            "uid": uid,
            "name": by_student[uid][0].get("student_name", ""),
            "missing_count": len(by_student[uid]),
            "message": messages.get(uid, ""),
        }
        for uid in by_student
    ]
    return render(
        request,
        "notify_preview.html",
        {"preview": preview, "window_hours": window_hours, "error": error},
        session=session,
        nav="notify",
    )


@router.post("/notify/send", name="notify_send")
async def notify_send(request: Request, state: AppState = Depends(get_state)):
    session = require_session(request)
    if isinstance(session, RedirectResponse):
        return session
    form = await request.form()
    messages = []
    for key, value in form.multi_items():
        if not key.startswith("message_"):
            continue
        uid = int(key.removeprefix("message_"))
        name = str(form.get(f"name_{uid}", ""))
        text = str(value).strip()
        if text:
            messages.append(OutgoingMessage(student_uid=uid, student_name=name, message=text))
    result = dispatch(state.events, messages, dry_run=True)
    url = str(request.url_for("notify_home"))
    return RedirectResponse(f"{url}?sent={result['sent']}", status_code=303)
