"""Experimental: post homework reminders into ClassIn as discussion activities.

There is no reminder/messaging API (confirmed), so this releases a discussion
activity (activityType=6) per course — students see it in the course feed.
Whether it fires an in-app push is undocumented client behavior: verify on a
real course before enabling (DASH_CLASSIN_APP_REMINDER, default off; ADR-0004).

Constraints: activity name ≤50 chars, no body/content param exists.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from ..classin.actions import ACTIVITY_DISCUSSION, ClassInActions
from ..classin.client import ClassInError
from ..store import EventStore

log = logging.getLogger(__name__)

REMINDER_UNIT_NAME = "알림/리마인드 (대시보드)"
NAME_LIMIT = 50


def group_rows_by_course(rows: list[dict]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("course_id") is not None:
            out[int(r["course_id"])].append(r)
    return dict(out)


def reminder_name(rows: list[dict], today: str | None = None) -> str:
    """'숙제 리마인드 9/1 — 김성실, 이지각 외 1명' fitted into 50 chars."""
    day = today or datetime.now(timezone.utc).astimezone().strftime("%-m/%-d")
    names = []
    seen = set()
    for r in rows:
        n = r.get("student_name") or ""
        if n and n not in seen:
            seen.add(n)
            names.append(n)
    base = f"숙제 리마인드 {day} — "
    listed: list[str] = []
    for i, n in enumerate(names):
        rest = len(names) - (i + 1)
        suffix = f" 외 {rest}명" if rest else ""
        candidate = base + ", ".join(listed + [n]) + suffix
        if len(candidate) > NAME_LIMIT:
            break
        listed.append(n)
    if not listed:
        return (base + f"미제출 {len(names)}명")[:NAME_LIMIT]
    rest = len(names) - len(listed)
    return (base + ", ".join(listed) + (f" 외 {rest}명" if rest else ""))[:NAME_LIMIT]


def _course_teacher_uid(store: EventStore, course_id: int) -> int | None:
    for c in store.courses():
        if c["course_id"] == course_id and c.get("teacher_uid"):
            return int(c["teacher_uid"])
    for r in store.lesson_records(course_id=course_id):
        if r.get("teacher_uid"):
            return int(r["teacher_uid"])
    return None


def post_app_reminders(
    actions: ClassInActions, store: EventStore, rows: list[dict]
) -> dict[str, Any]:
    """Release one reminder discussion per course. Returns per-course results."""
    posted: list[str] = []
    errors: list[str] = []
    for course_id, course_rows in group_rows_by_course(rows).items():
        course_label = course_rows[0].get("course_name") or f"코스 {course_id}"
        teacher_uid = _course_teacher_uid(store, course_id)
        if not teacher_uid:
            errors.append(f"{course_label}: 담당 선생님 UID를 찾지 못해 건너뜀")
            continue
        try:
            unit_id = store.course_reminder_unit(course_id)
            if not unit_id:
                unit_id = actions.create_unit(course_id=course_id, name=REMINDER_UNIT_NAME)
                store.set_course_reminder_unit(course_id, unit_id)
            name = reminder_name(course_rows)
            activity_id = actions.create_activity(
                course_id=course_id,
                unit_id=unit_id,
                activity_type=ACTIVITY_DISCUSSION,
                name=name,
                teacher_uid=teacher_uid,
            )
            actions.release_activity(course_id=course_id, activity_id=activity_id)
        except ClassInError as exc:
            log.warning("app reminder failed course=%s: %s", course_id, exc)
            errors.append(f"{course_label}: {exc.message}")
            continue
        posted.append(f"{course_label}: 「{name}」 게시 (activity {activity_id})")
        store.append_notification(
            event_type="missing_homework",
            provider="classin_discussion",
            status="sent",
            student_uid=None,
            student_name=course_label,
            message=name,
        )
    return {"posted": posted, "errors": errors}
