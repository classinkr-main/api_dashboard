"""생성 (선생님): AI 스케줄 파싱 → 코스/수업 일괄 생성."""

from __future__ import annotations

import hashlib
import json
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from ..classin.actions import ACTIVITY_HOMEWORK, ClassInActions
from ..classin.client import ClassInError
from ..intelligence.schedule_parser import ParsedCourse, parse_schedule
from .app import AppState, get_state, require_session, render

log = logging.getLogger(__name__)
router = APIRouter()


def _parse_teacher_map(raw: str) -> dict[str, int]:
    """'김선생=20001' lines → {name: uid}."""
    out: dict[str, int] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        name, _, uid = line.partition("=")
        try:
            out[name.strip()] = int(uid.strip())
        except ValueError:
            continue
    return out


@router.get("/create", name="create_home")
def create_home(request: Request, state: AppState = Depends(get_state)):
    session = require_session(request)
    if isinstance(session, RedirectResponse):
        return session
    return render(
        request,
        "create.html",
        {"ai_available": bool(state.settings.anthropic_api_key)},
        session=session,
        nav="create",
    )


@router.post("/create/parse", name="create_parse")
def create_parse(
    request: Request,
    state: AppState = Depends(get_state),
    schedule_text: str = Form(...),
    teacher_map: str = Form(""),
    default_teacher_uid: str = Form(""),
):
    session = require_session(request)
    if isinstance(session, RedirectResponse):
        return session
    try:
        courses = parse_schedule(state.settings, schedule_text)
    except Exception as exc:
        log.exception("schedule parse failed")
        return render(
            request,
            "create.html",
            {
                "ai_available": bool(state.settings.anthropic_api_key),
                "error": f"스케줄 파싱 실패: {exc}",
                "schedule_text": schedule_text,
                "teacher_map": teacher_map,
                "default_teacher_uid": default_teacher_uid,
            },
            session=session,
            nav="create",
        )
    plan = [c.model_dump(mode="json") for c in courses]
    return render(
        request,
        "create_preview.html",
        {
            "courses": courses,
            "plan_json": json.dumps(plan, ensure_ascii=False),
            "teacher_map": teacher_map,
            "default_teacher_uid": default_teacher_uid,
        },
        session=session,
        nav="create",
    )


@router.post("/create/execute", name="create_execute")
def create_execute(
    request: Request,
    state: AppState = Depends(get_state),
    plan_json: str = Form(...),
    teacher_map: str = Form(""),
    default_teacher_uid: str = Form(""),
):
    session = require_session(request)
    if isinstance(session, RedirectResponse):
        return session
    courses = [ParsedCourse.model_validate(c) for c in json.loads(plan_json)]
    uid_map = _parse_teacher_map(teacher_map)
    default_uid = int(default_teacher_uid) if default_teacher_uid.strip().isdigit() else None

    created: list[str] = []
    errors: list[str] = []
    with state.client_for(session) as client:
        actions = ClassInActions(client)
        for course in courses:
            teacher_uid = uid_map.get(course.teacher_name or "") or default_uid
            if not teacher_uid:
                errors.append(
                    f"{course.course_name}: 선생님 UID를 찾을 수 없습니다"
                    f" ({course.teacher_name or '미지정'}) — 매핑을 입력하세요."
                )
                continue
            # Idempotency key so a retried submit can't duplicate the course.
            identity = hashlib.md5(
                f"dash:{course.course_name}:{course.lessons[0].start_at if course.lessons else ''}".encode()
            ).hexdigest()[:32]
            try:
                course_id = actions.add_course(
                    course_name=course.course_name,
                    main_teacher_uid=teacher_uid,
                    unique_identity=identity,
                )
            except ClassInError as exc:
                errors.append(f"{course.course_name}: addCourse 실패 — {exc.message}")
                continue
            state.events.upsert_course(
                course_id, name=course.course_name, teacher_uid=teacher_uid, created_via="api"
            )
            created.append(f"코스 「{course.course_name}」 (ID {course_id})")

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
                        teacher_uid=teacher_uid,
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
                        teacher_uid=teacher_uid,
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
                            teacher_uid=teacher_uid,
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
