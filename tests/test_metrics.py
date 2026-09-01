import time
from datetime import datetime, timezone

import pytest

from classin_dashboard import metrics
from classin_dashboard.ingest import ingest
from classin_dashboard.store import EventStore

# Recent (not a fixed historical epoch) so lesson_date falls inside the
# `since`/`days`/`window_hours` filters that overview/summary/missing-homework
# all apply relative to "now" at call time.
CLASS_START = int(time.time()) - 3 * 3600
CLASS_END = CLASS_START + 3600
RECENT_ISO = datetime.now(timezone.utc).isoformat()


@pytest.fixture
def store(tmp_path):
    return EventStore(tmp_path / "dashboard.db", tmp_path / "webhook")


def attendance_payload(class_id, uid, name, attendance_time, first_in=None, identity=1, msg_id=None):
    return {
        "_id": msg_id or f"att-{class_id}-{uid}",
        "Cmd": "Attendance",
        "ClassID": class_id,
        "CourseID": 42,
        "CourseName": "Algebra",
        "ClassName": f"Lesson {class_id}",
        "ClassStartTime": CLASS_START,
        "ClassEndTime": CLASS_END,
        "Data": [
            {
                "Uid": uid,
                "Name": name,
                "Identity": identity,
                "AttendanceTime": attendance_time,
                "FirstInTime": first_in,
            },
            {
                "Uid": 90001,
                "Name": "Ms. Kim",
                "Identity": 3,
                "AttendanceTime": 3600,
                "FirstInTime": CLASS_START,
            },
        ],
    }


def homework_submit(class_id, uid, activity_id, msg_id=None, late=0):
    return {
        "_id": msg_id or f"hw-{class_id}-{uid}",
        "Cmd": "HomeworkSubmit",
        "ClassID": class_id,
        "CourseID": 42,
        "Data": {
            "ActivityId": activity_id,
            "StudentInfo": {"Uid": uid, "Name": "x"},
            "IsSubmitLate": late,
        },
    }


def homework_score(class_id, uid, activity_id, rate, msg_id=None):
    return {
        "_id": msg_id or f"hws-{class_id}-{uid}",
        "Cmd": "HomeworkScore",
        "ClassID": class_id,
        "CourseID": 42,
        "Data": {
            "ActivityId": activity_id,
            "StudentInfo": {"Uid": uid, "Name": "x"},
            "StudentScoringRate": rate,
        },
    }


# -- students_summary: risk levels & attendance rate --------------------------


def test_students_summary_good_risk_full_attendance(store):
    ingest(store, attendance_payload(1, 10001, "Alice", 3600, CLASS_START))
    summary = metrics.students_summary(store)
    alice = next(s for s in summary if s["uid"] == 10001)
    assert alice["attendance_rate"] == 1.0
    assert alice["risk_level"] == "good"


def test_students_summary_high_risk_low_attendance_rate(store):
    # 1 present, 3 absent -> rate 0.25 < 0.72 -> high risk
    for i in range(4):
        attendance_time = 3600 if i == 0 else 0
        ingest(store, attendance_payload(i, 10001, "Alice", attendance_time, CLASS_START))
    summary = metrics.students_summary(store)
    alice = next(s for s in summary if s["uid"] == 10001)
    assert alice["attendance_rate"] == 0.25
    assert alice["risk_level"] == "high"


def test_students_summary_medium_risk_from_homework_missing(store):
    ingest(store, attendance_payload(1, 10001, "Alice", 3600, CLASS_START))
    ingest(store, homework_submit(1, 10001, 777))
    # sweep-equivalent: mark a second lesson's homework as missing explicitly
    store.upsert_lesson_record(
        "2",
        10001,
        course_id=42,
        homework_submitted=0,
        homework_activity_id=778,
        attendance="출석",
        lesson_date=RECENT_ISO,
    )
    summary = metrics.students_summary(store)
    alice = next(s for s in summary if s["uid"] == 10001)
    assert alice["homework_missing"] == 1
    assert alice["risk_level"] == "medium"


def test_students_summary_high_risk_from_three_missing_homeworks(store):
    ingest(store, attendance_payload(1, 10001, "Alice", 3600, CLASS_START))
    for lid in (2, 3, 4):
        store.upsert_lesson_record(
            str(lid),
            10001,
            course_id=42,
            homework_submitted=0,
            homework_activity_id=900 + lid,
            attendance="출석",
            lesson_date=RECENT_ISO,
        )
    summary = metrics.students_summary(store)
    alice = next(s for s in summary if s["uid"] == 10001)
    assert alice["homework_missing"] == 3
    assert alice["risk_level"] == "high"


def test_students_summary_low_score_avg_is_high_risk(store):
    ingest(store, attendance_payload(1, 10001, "Alice", 3600, CLASS_START))
    ingest(store, homework_submit(1, 10001, 777))
    ingest(store, homework_score(1, 10001, 777, 0.5))  # 50% -> below 70
    summary = metrics.students_summary(store)
    alice = next(s for s in summary if s["uid"] == 10001)
    assert alice["score_avg"] == 50.0
    assert alice["risk_level"] == "high"


def test_students_summary_name_falls_back_to_uid(store):
    ingest(store, attendance_payload(1, 55555, "", 3600, CLASS_START))
    # ensure_student only backfills a truthy name, so uid-only fallback still applies
    summary = metrics.students_summary(store)
    row = next(s for s in summary if s["uid"] == 55555)
    assert row["name"] == "UID 55555"


def test_attendance_rate_denominator_excludes_unknown_rows(store):
    ingest(store, attendance_payload(1, 10001, "Alice", 3600, CLASS_START))
    # a row with no attendance value at all (e.g. patched by End before Attendance)
    store.upsert_lesson_record("2", 10001, course_id=42, lesson_date=RECENT_ISO)
    summary = metrics.students_summary(store)
    alice = next(s for s in summary if s["uid"] == 10001)
    # denominator is 1 (only the known row), not 2
    assert alice["attendance_rate"] == 1.0
    assert alice["lesson_count"] == 2


# -- teachers_summary: taught_minutes -----------------------------------------


def test_teachers_summary_taught_minutes_single_lesson(store):
    ingest(store, attendance_payload(1, 10001, "Alice", 3600, CLASS_START))
    summary = metrics.teachers_summary(store)
    teacher = next(t for t in summary if t["uid"] == 90001)
    assert teacher["taught_minutes"] == 60
    assert teacher["lesson_count"] == 1


def test_teachers_summary_sums_across_lessons_deduped_by_lesson_id(store):
    ingest(store, attendance_payload(1, 10001, "Alice", 3600, CLASS_START))
    ingest(store, attendance_payload(1, 10002, "Bob", 3600, CLASS_START))  # same lesson id=1
    ingest(store, attendance_payload(2, 10001, "Alice", 3600, CLASS_START))
    summary = metrics.teachers_summary(store)
    teacher = next(t for t in summary if t["uid"] == 90001)
    # lesson 1 counted once even though two students attended it
    assert teacher["lesson_count"] == 2
    assert teacher["taught_minutes"] == 120


def test_teachers_summary_name_prefers_teachers_table(store):
    ingest(store, attendance_payload(1, 10001, "Alice", 3600, CLASS_START))
    summary = metrics.teachers_summary(store)
    teacher = next(t for t in summary if t["uid"] == 90001)
    assert teacher["name"] == "Ms. Kim"


def test_teachers_summary_sorted_by_taught_minutes_desc(store):
    ingest(store, attendance_payload(1, 10001, "Alice", 3600, CLASS_START))
    payload2 = attendance_payload(2, 20001, "Bob", 3600, CLASS_START)
    payload2["Data"][1] = {
        "Uid": 90002,
        "Name": "Mr. Lee",
        "Identity": 3,
        "AttendanceTime": 3600 * 2,
        "FirstInTime": CLASS_START,
    }
    payload2["ClassEndTime"] = CLASS_START + 7200
    ingest(store, payload2)
    summary = metrics.teachers_summary(store)
    assert summary[0]["uid"] == 90002  # 120 minutes > 60 minutes
    assert summary[0]["taught_minutes"] == 120


# -- missing_homework_rows: contract -------------------------------------------


def test_missing_homework_rows_requires_homework_activity_on_lesson(store):
    # lesson has no homework activity anywhere -> never "missing", even with
    # homework_submitted NULL.
    ingest(store, attendance_payload(1, 10001, "Alice", 3600, CLASS_START))
    rows = metrics.missing_homework_rows(store, window_hours=24 * 365)
    assert rows == []


def test_missing_homework_rows_excludes_submitted(store):
    ingest(store, attendance_payload(1, 10001, "Alice", 3600, CLASS_START))
    ingest(store, homework_submit(1, 10001, 777))
    rows = metrics.missing_homework_rows(store, window_hours=24 * 365)
    assert rows == []


def test_missing_homework_rows_includes_null_submitted_when_lesson_has_homework(store):
    # Lesson 1 gets a homework_activity_id via a submit from a *different*
    # student; student 10001's own row stays NULL (never submitted) but the
    # lesson-level "has homework" flag is now true via lessons table lookup,
    # OR via another lesson_record carrying homework_activity_id.
    ingest(store, attendance_payload(1, 10001, "Alice", 3600, CLASS_START))
    ingest(store, attendance_payload(1, 10002, "Bob", 3600, CLASS_START))
    ingest(store, homework_submit(1, 10002, 777))  # Bob submits; Alice does not
    rows = metrics.missing_homework_rows(store, window_hours=24 * 365)
    uids = {r["student_uid"] for r in rows}
    assert 10001 in uids  # Alice: NULL submitted, but lesson has homework -> missing
    assert 10002 not in uids  # Bob submitted -> excluded


def test_missing_homework_rows_via_lessons_table_homework_activity_id(store):
    ingest(store, attendance_payload(1, 10001, "Alice", 3600, CLASS_START))
    store.upsert_lesson("1", homework_activity_id=777)
    rows = metrics.missing_homework_rows(store, window_hours=24 * 365)
    uids = {r["student_uid"] for r in rows}
    assert 10001 in uids


def test_missing_homework_rows_explicit_zero_flag_included(store):
    ingest(store, attendance_payload(1, 10001, "Alice", 3600, CLASS_START))
    store.upsert_lesson_record(
        "1", 10001, course_id=42, homework_submitted=0, homework_activity_id=777
    )
    rows = metrics.missing_homework_rows(store, window_hours=24 * 365)
    row = next(r for r in rows if r["student_uid"] == 10001)
    assert row["student_name"] == "Alice"


def test_missing_homework_rows_respects_window_hours(store):
    ingest(store, attendance_payload(1, 10001, "Alice", 3600, CLASS_START))
    store.upsert_lesson("1", homework_activity_id=777)
    # lesson_date derives from ClassStartTime (year 2023), well outside a
    # narrow recent window.
    rows = metrics.missing_homework_rows(store, window_hours=1)
    assert rows == []


# -- overview -------------------------------------------------------------


def test_overview_counts_and_risk(store):
    ingest(store, attendance_payload(1, 10001, "Alice", 3600, CLASS_START))
    data = metrics.overview(store)
    assert data["student_count"] == 1
    assert data["risk_counts"]["good"] == 1
    assert data["event_counts"].get("Attendance") == 1
