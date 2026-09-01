"""Webhook event → store normalization.

Missing homework is derived by absence: Attendance creates one row per
student with homework_submitted unknown; HomeworkSubmit flips it. A sweep
then treats rows in released-homework lessons without a submit as missing.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .classin.webhook_schemas import (
    AnswerSheetScoreEvent,
    AttendanceEvent,
    EndEvent,
    HomeworkScoreEvent,
    HomeworkSubmitEvent,
    RatingEvent,
    _BaseEvent,
    parse_event,
)
from .store import EventStore

LATE_GRACE_SECONDS = 5 * 60


def attendance_label(
    seconds: int, first_in: int | None, class_start: int | None, class_end: int | None
) -> str:
    if seconds <= 0:
        return "결석"
    if first_in and class_start and (first_in - class_start) > LATE_GRACE_SECONDS:
        return "지각"
    if class_start and class_end and seconds < (class_end - class_start) * 0.5:
        return "지각"
    return "출석"


def _iso(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _lesson_key(event: _BaseEvent, activity_id: int | None = None) -> str:
    if event.ClassID:
        return str(event.ClassID)
    return f"homework:{activity_id}" if activity_id else "unknown"


def ingest(store: EventStore, raw: dict) -> str:
    """Parse and persist one webhook payload. Returns the Cmd handled.

    Delivery is at-least-once (ClassIn retries every 10s until acked), so the
    _id field dedupes: an already-seen message is stored-skipped and not
    re-applied.
    """
    event = parse_event(raw)
    cmd = event.Cmd or "unknown"

    student_uid = None
    teacher_uid = None
    data = getattr(event, "Data", None)
    if hasattr(data, "StudentInfo") and data.StudentInfo:
        student_uid = data.StudentInfo.Uid
    if hasattr(data, "TeacherInfo") and data.TeacherInfo:
        teacher_uid = data.TeacherInfo.Uid
    if isinstance(event, RatingEvent):
        teacher_uid = event.TUID

    msg_id = raw.get("_id")
    inserted = store.insert_event(
        cmd,
        raw,
        msg_id=str(msg_id) if msg_id is not None else None,
        course_id=event.CourseID,
        class_id=event.ClassID,
        student_uid=student_uid,
        teacher_uid=teacher_uid,
    )
    if not inserted:
        return cmd  # duplicate delivery

    if isinstance(event, AttendanceEvent):
        _ingest_attendance(store, event)
    elif isinstance(event, EndEvent):
        _ingest_end(store, event)
    elif isinstance(event, HomeworkSubmitEvent):
        _ingest_homework_submit(store, event)
    elif isinstance(event, HomeworkScoreEvent):
        _ingest_homework_score(store, event)
    elif isinstance(event, RatingEvent):
        if event.TUID:
            store.upsert_teacher(event.TUID)
    elif isinstance(event, AnswerSheetScoreEvent):
        pass  # kept in events; exam aggregation reads it from there
    return cmd


def _ingest_attendance(store: EventStore, event: AttendanceEvent) -> None:
    lesson_id = _lesson_key(event)
    if event.CourseID:
        store.upsert_course(event.CourseID, name=event.CourseName, created_via="webhook")
    store.upsert_lesson(
        lesson_id,
        course_id=event.CourseID,
        title=event.ClassName,
        start_time=event.ClassStartTime,
        end_time=event.ClassEndTime,
        created_via="webhook",
    )
    teacher = next((m for m in event.Data if m.is_teacher), None)
    for member in event.Data:
        if not member.is_student:
            if member.is_teacher:
                store.upsert_teacher(member.Uid, member.Name)
            continue
        store.ensure_student(member.Uid, member.Name)
        store.upsert_lesson_record(
            lesson_id,
            member.Uid,
            course_id=event.CourseID,
            course_name=event.CourseName,
            class_name=event.ClassName,
            lesson_date=_iso(event.ClassStartTime or event.ActionTime),
            class_start=event.ClassStartTime,
            class_end=event.ClassEndTime,
            attendance=attendance_label(
                member.AttendanceTime,
                member.FirstInTime,
                event.ClassStartTime,
                event.ClassEndTime,
            ),
            attendance_seconds=member.AttendanceTime,
            teacher_uid=teacher.Uid if teacher else None,
            teacher_name=teacher.Name if teacher else None,
        )


def _ingest_end(store: EventStore, event: EndEvent) -> None:
    lesson_id = _lesson_key(event)
    camera = event.camera_minutes_by_uid()
    hands = event.hand_raise_by_uid()
    trophy = event.trophy_by_uid()
    poll = event.poll_by_uid()
    for uid in set(camera) | set(hands) | set(trophy) | set(poll):
        fields = {
            k: v
            for k, v in {
                "camera_minutes": camera.get(uid),
                "hand_raise": hands.get(uid),
                "trophy": trophy.get(uid),
                "poll": poll.get(uid),
            }.items()
            if v is not None
        }
        store.patch_lesson_record(lesson_id, uid, course_id=event.CourseID, **fields)


def _ingest_homework_submit(store: EventStore, event: HomeworkSubmitEvent) -> None:
    student = event.Data.StudentInfo
    if not student or student.Uid is None:
        return
    store.ensure_student(student.Uid, student.Name)
    store.patch_lesson_record(
        _lesson_key(event, event.Data.ActivityId),
        student.Uid,
        course_id=event.CourseID,
        homework_submitted=1,
        homework_late=1 if event.Data.IsSubmitLate else 0,
        homework_activity_id=event.Data.ActivityId,
    )


def _ingest_homework_score(store: EventStore, event: HomeworkScoreEvent) -> None:
    student = event.Data.StudentInfo
    if not student or student.Uid is None:
        return
    store.patch_lesson_record(
        _lesson_key(event, event.Data.ActivityId),
        student.Uid,
        course_id=event.CourseID,
        homework_score=event.Data.score_percent(),
    )
