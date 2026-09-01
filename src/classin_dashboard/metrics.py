"""Dashboard aggregations over accumulated lesson records.

Formulas follow the reference toolkit's course_dashboard: attendance rate
divides by *known* rows only; risk levels use the same thresholds.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .store import EventStore

PRESENT, LATE, ABSENT = "출석", "지각", "결석"


def _risk_level(
    attendance_rate: float | None, score_avg: float | None, homework_missing: int
) -> str:
    if (
        (attendance_rate is not None and attendance_rate < 0.72)
        or (score_avg is not None and score_avg < 70)
        or homework_missing >= 3
    ):
        return "high"
    if (
        (attendance_rate is not None and attendance_rate < 0.86)
        or (score_avg is not None and score_avg < 80)
        or homework_missing >= 1
    ):
        return "medium"
    return "good"


def _attendance_rate(rows: list[dict]) -> float | None:
    known = [r for r in rows if r.get("attendance") in (PRESENT, LATE, ABSENT)]
    if not known:
        return None
    ok = sum(1 for r in known if r["attendance"] in (PRESENT, LATE))
    return round(ok / len(known), 3)


def _week_start(date_iso: str) -> str:
    dt = datetime.fromisoformat(date_iso)
    monday = dt - timedelta(days=dt.weekday())
    return monday.date().isoformat()


def student_metrics(rows: list[dict]) -> dict[str, Any]:
    scores = [r["homework_score"] for r in rows if r.get("homework_score") is not None]
    homework_missing = sum(1 for r in rows if r.get("homework_submitted") == 0)
    rate = _attendance_rate(rows)
    avg = round(sum(scores) / len(scores), 1) if scores else None
    return {
        "lesson_count": len(rows),
        "attendance_rate": rate,
        "present_count": sum(1 for r in rows if r.get("attendance") == PRESENT),
        "late_count": sum(1 for r in rows if r.get("attendance") == LATE),
        "absent_count": sum(1 for r in rows if r.get("attendance") == ABSENT),
        "homework_missing": homework_missing,
        "score_avg": avg,
        "risk_level": _risk_level(rate, avg, homework_missing),
    }


def weekly_attendance_trend(rows: list[dict], buckets: int = 14) -> list[dict]:
    by_week: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("lesson_date"):
            by_week[_week_start(r["lesson_date"])].append(r)
    out = []
    for week in sorted(by_week)[-buckets:]:
        wrows = by_week[week]
        out.append(
            {
                "week": week,
                "attendance_rate": _attendance_rate(wrows),
                "present": sum(1 for r in wrows if r.get("attendance") == PRESENT),
                "late": sum(1 for r in wrows if r.get("attendance") == LATE),
                "absent": sum(1 for r in wrows if r.get("attendance") == ABSENT),
                "total": len(wrows),
            }
        )
    return out


def weekly_score_trend(rows: list[dict], buckets: int = 14) -> list[dict]:
    by_week: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.get("lesson_date") and r.get("homework_score") is not None:
            by_week[_week_start(r["lesson_date"])].append(r["homework_score"])
    return [
        {"week": w, "avg_score": round(sum(v) / len(v), 1), "count": len(v)}
        for w, v in sorted(by_week.items())[-buckets:]
    ]


def overview(store: EventStore, days: int = 90) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = store.lesson_records(since=since)
    courses = store.courses()
    lessons = store.lessons()
    per_student: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        per_student[r["student_uid"]].append(r)
    risk_counts = {"high": 0, "medium": 0, "good": 0}
    for srows in per_student.values():
        risk_counts[student_metrics(srows)["risk_level"]] += 1
    return {
        "days": days,
        "course_count": len(courses),
        "lesson_count": len(lessons),
        "student_count": len(per_student),
        "attendance_rate": _attendance_rate(rows),
        "homework_missing": sum(1 for r in rows if r.get("homework_submitted") == 0),
        "risk_counts": risk_counts,
        "attendance_trend": weekly_attendance_trend(rows),
        "score_trend": weekly_score_trend(rows),
        "event_counts": store.counts_by_cmd(),
    }


def students_summary(store: EventStore, days: int = 90) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = store.lesson_records(since=since)
    names = {s["uid"]: s for s in store.students()}
    per_student: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        per_student[r["student_uid"]].append(r)
    out = []
    for uid, srows in per_student.items():
        m = student_metrics(srows)
        student = names.get(uid, {})
        out.append(
            {
                "uid": uid,
                "name": student.get("name") or f"UID {uid}",
                "class_name": srows[0].get("class_name") if srows else None,
                **m,
            }
        )
    rank = {"high": 0, "medium": 1, "good": 2}
    out.sort(
        key=lambda s: (
            rank.get(s["risk_level"], 3),
            -s["homework_missing"],
            s["attendance_rate"] if s["attendance_rate"] is not None else 1.0,
            s["name"],
        )
    )
    return out


def student_detail(store: EventStore, uid: int, days: int = 180) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = store.lesson_records(student_uid=uid, since=since)
    names = {s["uid"]: s for s in store.students()}
    exam_events = store.events("AnswerSheetScore", student_uid=uid, limit=100)
    exams = []
    for ev in exam_events:
        data = (ev["payload"].get("Data") or {}) if isinstance(ev["payload"], dict) else {}
        exams.append(
            {
                "name": data.get("ActivityName") or data.get("UnitName") or "시험",
                "score": data.get("Score"),
                "max_score": data.get("MaximumScore"),
                "rate": data.get("StudentScoringRate"),
                "received_at": ev["received_at"],
            }
        )
    return {
        "uid": uid,
        "name": names.get(uid, {}).get("name") or f"UID {uid}",
        "metrics": student_metrics(rows),
        "rows": rows,
        "exams": exams,
        "attendance_trend": weekly_attendance_trend(rows),
        "score_trend": weekly_score_trend(rows),
    }


def teachers_summary(store: EventStore, days: int = 90) -> list[dict[str, Any]]:
    """Per-teacher lesson log: distinct lessons, taught minutes, weekly trend.

    Teacher identity comes from Attendance rows (Identity==2) captured onto
    lesson_records; there is no ClassIn API that enumerates teachers.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = store.lesson_records(since=since)
    teacher_names = {t["uid"]: t.get("name") for t in store.teachers()}
    by_teacher: dict[int, dict[str, dict]] = defaultdict(dict)  # uid -> lesson_id -> row
    for r in rows:
        uid = r.get("teacher_uid")
        if uid is None:
            continue
        by_teacher[uid].setdefault(r["lesson_id"], r)
    out = []
    for uid, lessons in by_teacher.items():
        minutes = 0.0
        weekly: dict[str, float] = defaultdict(float)
        for r in lessons.values():
            if r.get("class_start") and r.get("class_end"):
                dur = (r["class_end"] - r["class_start"]) / 60
                minutes += dur
                if r.get("lesson_date"):
                    weekly[_week_start(r["lesson_date"])] += dur
        srows = [x for x in rows if x.get("teacher_uid") == uid]
        out.append(
            {
                "uid": uid,
                "name": teacher_names.get(uid)
                or next((r.get("teacher_name") for r in srows if r.get("teacher_name")), None)
                or f"UID {uid}",
                "lesson_count": len(lessons),
                "taught_minutes": round(minutes),
                "attendance_rate": _attendance_rate(srows),
                "weekly_minutes": [
                    {"week": w, "minutes": round(m)} for w, m in sorted(weekly.items())[-14:]
                ],
                "lessons": sorted(
                    (
                        {
                            "lesson_id": lid,
                            "title": r.get("class_name"),
                            "course_name": r.get("course_name"),
                            "date": r.get("lesson_date"),
                            "minutes": round((r["class_end"] - r["class_start"]) / 60)
                            if r.get("class_start") and r.get("class_end")
                            else None,
                        }
                        for lid, r in lessons.items()
                    ),
                    key=lambda x: x["date"] or "",
                    reverse=True,
                ),
            }
        )
    out.sort(key=lambda t: -t["taught_minutes"])
    return out


def missing_homework_rows(store: EventStore, window_hours: int = 48) -> list[dict[str, Any]]:
    """Rows where homework is explicitly not submitted within the window.

    homework_submitted == 0 only exists after a sweep marks lesson rows whose
    lesson has a released homework activity; NULL (unknown) is never treated
    as missing — same rule as the reference toolkit.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    rows = store.lesson_records(since=since)
    names = {s["uid"]: s for s in store.students()}

    # A lesson "has homework" if any record in it carries a homework activity
    # or any submit happened for it.
    lessons_with_hw = {
        r["lesson_id"]
        for r in store.lesson_records()
        if r.get("homework_activity_id") is not None
    }
    lessons_with_hw |= {
        lesson["lesson_id"]
        for lesson in store.lessons()
        if lesson.get("homework_activity_id") is not None
    }

    out = []
    for r in rows:
        if r["lesson_id"] not in lessons_with_hw:
            continue
        if r.get("homework_submitted") == 1:
            continue
        student = names.get(r["student_uid"], {})
        out.append(
            {
                **r,
                "student_name": student.get("name") or f"UID {r['student_uid']}",
                "parent_phone": student.get("parent_phone"),
            }
        )
    return out
