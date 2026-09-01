"""모아보기 (원장/대표): overview, per-student, per-teacher views."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from .. import metrics
from ..classin.reads import ClassInReads, sync_masters
from .app import AppState, get_state, require_session, render
from .charts import bar_chart, line_chart

router = APIRouter()


@router.post("/dashboard/sync", name="dashboard_sync")
def dashboard_sync(request: Request, state: AppState = Depends(get_state)):
    """Pull course/lesson/member masters from ClassIn read actions if enabled.

    The get* family is gated per SID; when disabled, this reports which
    actions were rejected and the dashboard keeps using webhook-derived data.
    """
    session = require_session(request)
    if isinstance(session, RedirectResponse):
        return session
    with state.client_for(session) as client:
        result = sync_masters(ClassInReads(client), state.events)
    url = str(request.url_for("dashboard_home"))
    synced = result["courses"] + result["lessons"] + result["students"] + result["teachers"]
    if synced:
        return RedirectResponse(f"{url}?sync=ok&n={synced}", status_code=303)
    return RedirectResponse(f"{url}?sync=unavailable", status_code=303)


@router.get("/dashboard", name="dashboard_home")
def dashboard_home(
    request: Request,
    state: AppState = Depends(get_state),
    days: int = 90,
    sync: str | None = None,
    n: int = 0,
):
    session = require_session(request)
    if isinstance(session, RedirectResponse):
        return session
    days = max(7, min(days, 365))
    data = metrics.overview(state.events, days=days)
    courses = state.events.courses()
    lessons_by_course: dict[int, int] = {}
    for lesson in state.events.lessons():
        if lesson.get("course_id"):
            lessons_by_course[lesson["course_id"]] = (
                lessons_by_course.get(lesson["course_id"], 0) + 1
            )
    return render(
        request,
        "dashboard.html",
        {
            "data": data,
            "courses": courses,
            "lessons_by_course": lessons_by_course,
            "sync": sync,
            "sync_n": n,
            "attendance_chart": line_chart(
                [
                    {**p, "pct": (p["attendance_rate"] or 0) * 100}
                    for p in data["attendance_trend"]
                ],
                "pct",
                vmin=0,
                vmax=100,
                fmt="{:.0f}%",
            ),
            "score_chart": line_chart(
                data["score_trend"], "avg_score", vmin=0, vmax=100, fmt="{:.1f}점"
            ),
        },
        session=session,
        nav="dashboard",
    )


@router.get("/dashboard/course/{course_id}", name="course_detail")
def course_detail(course_id: int, request: Request, state: AppState = Depends(get_state)):
    session = require_session(request)
    if isinstance(session, RedirectResponse):
        return session
    course = next(
        (c for c in state.events.courses() if c["course_id"] == course_id), None
    )
    lessons = state.events.lessons(course_id=course_id)
    rows = state.events.lesson_records(course_id=course_id)
    per_lesson: dict[str, list[dict]] = {}
    for r in rows:
        per_lesson.setdefault(r["lesson_id"], []).append(r)
    lesson_stats = {
        lid: {
            "present": sum(1 for r in lrows if r.get("attendance") == "출석"),
            "late": sum(1 for r in lrows if r.get("attendance") == "지각"),
            "absent": sum(1 for r in lrows if r.get("attendance") == "결석"),
        }
        for lid, lrows in per_lesson.items()
    }
    return render(
        request,
        "course_detail.html",
        {
            "course": course or {"course_id": course_id, "name": f"코스 {course_id}"},
            "lessons": lessons,
            "lesson_stats": lesson_stats,
            "record_count": len(rows),
        },
        session=session,
        nav="dashboard",
    )


@router.get("/students", name="students_view")
def students_view(
    request: Request,
    state: AppState = Depends(get_state),
    uid: int | None = None,
    days: int = 90,
):
    session = require_session(request)
    if isinstance(session, RedirectResponse):
        return session
    days = max(7, min(days, 365))
    students = metrics.students_summary(state.events, days=days)
    detail = None
    detail_charts = {}
    if uid is not None:
        detail = metrics.student_detail(state.events, uid)
        detail_charts = {
            "attendance": line_chart(
                [
                    {**p, "pct": (p["attendance_rate"] or 0) * 100}
                    for p in detail["attendance_trend"]
                ],
                "pct",
                vmin=0,
                vmax=100,
                fmt="{:.0f}%",
            ),
            "score": line_chart(
                detail["score_trend"], "avg_score", vmin=0, vmax=100, fmt="{:.1f}점"
            ),
        }
    return render(
        request,
        "students.html",
        {"students": students, "detail": detail, "detail_charts": detail_charts, "days": days},
        session=session,
        nav="students",
    )


@router.get("/teachers", name="teachers_view")
def teachers_view(
    request: Request,
    state: AppState = Depends(get_state),
    uid: int | None = None,
    days: int = 90,
):
    session = require_session(request)
    if isinstance(session, RedirectResponse):
        return session
    days = max(7, min(days, 365))
    teachers = metrics.teachers_summary(state.events, days=days)
    detail = next((t for t in teachers if t["uid"] == uid), None) if uid else None
    detail_chart = (
        bar_chart(detail["weekly_minutes"], "minutes", fmt="{:.0f}분") if detail else ""
    )
    # 강의 평가: 확인 결과 ClassIn AI 강의분석 API는 아직 파트너 API에 없다.
    # 대신 Rating 웹훅(학생→교사 S2T 평가)을 누적해 목록/추이로 보여준다.
    ratings = []
    rating_avg = None
    if uid:
        from ..classin.webhook_schemas import RatingEvent

        scores = []
        for ev in state.events.events("Rating", teacher_uid=uid, limit=100):
            try:
                rating = RatingEvent.model_validate(ev["payload"])
            except Exception:
                continue
            for s in rating.student_to_teacher_scores():
                if s.get("score") is not None:
                    scores.append(float(s["score"]))
                ratings.append({**s, "received_at": ev["received_at"]})
        if scores:
            rating_avg = round(sum(scores) / len(scores), 2)
    return render(
        request,
        "teachers.html",
        {
            "teachers": teachers,
            "detail": detail,
            "detail_chart": detail_chart,
            "ratings": ratings,
            "rating_avg": rating_avg,
            "days": days,
        },
        session=session,
        nav="teachers",
    )
