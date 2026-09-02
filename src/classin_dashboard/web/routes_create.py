"""생성 (선생님): 붙여넣기 한 번 → 코스/수업/숙제 자동 인식 + 기존 엔티티 자동 배정."""

from __future__ import annotations

import hashlib
import json
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from ..classin.actions import ACTIVITY_HOMEWORK, ClassInActions
from ..classin.client import ClassInError
from ..intelligence.schedule_parser import SOURCE_LABELS, ParsePlan, smart_parse
from .app import AppState, get_state, require_session, render

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/create", name="create_home")
def create_home(request: Request, state: AppState = Depends(get_state)):
    session = require_session(request)
    if isinstance(session, RedirectResponse):
        return session
    return render(
        request,
        "create.html",
        {
            "ai_available": bool(state.settings.anthropic_api_key),
            "teacher_count": len(state.events.teachers()),
        },
        session=session,
        nav="create",
    )


@router.post("/create/parse", name="create_parse")
def create_parse(
    request: Request,
    state: AppState = Depends(get_state),
    schedule_text: str = Form(...),
):
    session = require_session(request)
    if isinstance(session, RedirectResponse):
        return session
    try:
        plan = smart_parse(state.settings, state.events, schedule_text)
    except Exception as exc:
        log.exception("schedule parse failed")
        return render(
            request,
            "create.html",
            {
                "ai_available": bool(state.settings.anthropic_api_key),
                "teacher_count": len(state.events.teachers()),
                "error": f"스케줄 파싱 실패: {exc}",
                "schedule_text": schedule_text,
            },
            session=session,
            nav="create",
        )
    return render(
        request,
        "create_preview.html",
        {
            "plan": plan,
            "source_label": SOURCE_LABELS.get(plan.source_format, plan.source_format),
            "all_teachers": state.events.teachers(),
            "plan_json": json.dumps(plan.model_dump(mode="json"), ensure_ascii=False),
        },
        session=session,
        nav="create",
    )


@router.post("/create/execute", name="create_execute")
async def create_execute(request: Request, state: AppState = Depends(get_state)):
    session = require_session(request)
    if isinstance(session, RedirectResponse):
        return session
    form = await request.form()
    plan = ParsePlan.model_validate(json.loads(str(form.get("plan_json") or "{}")))
    _apply_teacher_overrides(plan, form)

    created: list[str] = []
    errors: list[str] = []
    with state.client_for(session) as client:
        actions = ClassInActions(client)
        for course in plan.courses:
            if not course.teacher_uid:
                errors.append(
                    f"{course.course_name}: 선생님을 확정하지 못했습니다"
                    f" ({course.teacher_name or '미지정'}) — 미리보기에서 선택하세요."
                )
                continue
            course_id = course.existing_course_id
            if course_id:
                created.append(f"기존 코스 「{course.course_name}」 (ID {course_id}) 재사용")
            else:
                # Idempotency key so a retried submit can't duplicate the course.
                identity = hashlib.md5(
                    f"dash:{course.course_name}:"
                    f"{course.lessons[0].start_at if course.lessons else ''}".encode()
                ).hexdigest()[:32]
                try:
                    course_id = actions.add_course(
                        course_name=course.course_name,
                        main_teacher_uid=course.teacher_uid,
                        unique_identity=identity,
                    )
                except ClassInError as exc:
                    errors.append(f"{course.course_name}: addCourse 실패 — {exc.message}")
                    continue
                created.append(f"코스 「{course.course_name}」 (ID {course_id})")
            state.events.upsert_course(
                course_id,
                name=course.course_name,
                teacher_uid=course.teacher_uid,
                created_via="api",
            )

            unit_id = None
            if course.lessons:
                try:
                    first = course.lessons[0].start_at.date()
                    last = course.lessons[-1].start_at.date()
                    span = f"{first}~{last}" if first != last else str(first)
                    unit_id = actions.create_unit(
                        course_id=course_id, name=f"대시보드 등록 - {span}"
                    )
                except ClassInError as exc:
                    errors.append(f"{course.course_name}: 단원 생성 실패 — {exc.message}")

            for lesson in course.lessons:
                try:
                    result = actions.create_classroom(
                        course_id=course_id,
                        name=lesson.title,
                        teacher_uid=course.teacher_uid,
                        start_time=int(lesson.start_at.timestamp()),
                        end_time=int(lesson.end_at.timestamp()),
                        unit_id=unit_id,
                    )
                except ClassInError as exc:
                    errors.append(f"{lesson.title}: 수업 생성 실패 — {exc.message}")
                    continue
                class_id = result.get("classId") or result.get("activityId")
                if class_id:
                    state.events.upsert_lesson(
                        str(class_id),
                        course_id=course_id,
                        title=lesson.title,
                        start_time=int(lesson.start_at.timestamp()),
                        end_time=int(lesson.end_at.timestamp()),
                        teacher_uid=course.teacher_uid,
                        created_via="api",
                    )
                created.append(f"수업 「{lesson.title}」 (ID {class_id})")

                if lesson.homework and unit_id:
                    try:
                        activity_id = actions.create_activity(
                            course_id=course_id,
                            unit_id=unit_id,
                            activity_type=ACTIVITY_HOMEWORK,
                            name=lesson.homework.title,
                            teacher_uid=course.teacher_uid,
                            start_time=int(lesson.end_at.timestamp()),
                            end_time=int(lesson.homework.due_at.timestamp())
                            if lesson.homework.due_at
                            else None,
                        )
                        if class_id:
                            state.events.upsert_lesson(
                                str(class_id), homework_activity_id=activity_id
                            )
                        created.append(
                            f"숙제 「{lesson.homework.title}」 (activity {activity_id}) — "
                            "내용은 ClassIn에서 채운 뒤 출제하세요 (빈 숙제는 API 출제 불가)"
                        )
                    except ClassInError as exc:
                        errors.append(
                            f"{lesson.homework.title}: 숙제 생성 실패 — {exc.message}"
                        )

    return render(
        request,
        "create_result.html",
        {"created": created, "errors": errors},
        session=session,
        nav="create",
    )


def _apply_teacher_overrides(plan: ParsePlan, form) -> None:
    """Per-course selects/manual UID inputs from the preview page win over resolution."""
    for index, course in enumerate(plan.courses):
        for field in (f"teacher_uid_manual_{index}", f"teacher_uid_{index}"):
            value = str(form.get(field) or "").strip()
            if value.isdigit():
                course.teacher_uid = int(value)
                break
