import pytest

from classin_dashboard.ingest import attendance_label, ingest
from classin_dashboard.store import EventStore

CLASS_START = 1_700_000_000
CLASS_END = CLASS_START + 3600  # 1 hour lesson


@pytest.fixture
def store(tmp_path):
    return EventStore(tmp_path / "dashboard.db", tmp_path / "webhook")


def attendance_payload(msg_id="evt-1"):
    return {
        "_id": msg_id,
        "Cmd": "Attendance",
        "ClassID": 555,
        "CourseID": 42,
        "CourseName": "Algebra",
        "ClassName": "Algebra L1",
        "ClassStartTime": CLASS_START,
        "ClassEndTime": CLASS_END,
        "Data": [
            # normal attendance: present for full class, on time
            {
                "Uid": 10001,
                "Name": "Alice",
                "Identity": 1,
                "AttendanceTime": 3600,
                "FirstInTime": CLASS_START,
            },
            # absent: AttendanceTime 0
            {
                "Uid": 10002,
                "Name": "Bob",
                "Identity": 1,
                "AttendanceTime": 0,
                "FirstInTime": None,
            },
            # late: joined more than 5 minutes after start
            {
                "Uid": 10003,
                "Name": "Cara",
                "Identity": 1,
                "AttendanceTime": 3000,
                "FirstInTime": CLASS_START + 600,
            },
            # teacher
            {
                "Uid": 90001,
                "Name": "Ms. Kim",
                "Identity": 3,
                "AttendanceTime": 3600,
                "FirstInTime": CLASS_START,
            },
        ],
    }


def end_payload(msg_id="evt-2"):
    return {
        "_id": msg_id,
        "Cmd": "End",
        "ClassID": 555,
        "CourseID": 42,
        "Data": {
            "equipmentsEnd": {"10001": {"Camera": {"Total": 3600}}},
            "handsupEnd": {"10001": {"Total": 5}},
            "awardEnd": {"10001": {"Total": 2}},
        },
    }


def homework_submit_payload(msg_id="evt-3", uid=10001, activity_id=777):
    return {
        "_id": msg_id,
        "Cmd": "HomeworkSubmit",
        "ClassID": 555,
        "CourseID": 42,
        "Data": {
            "ActivityId": activity_id,
            "StudentInfo": {"Uid": uid, "Name": "Alice"},
            "IsSubmitLate": 0,
        },
    }


# -- attendance_label unit tests ---------------------------------------------


def test_attendance_label_zero_seconds_is_absent():
    assert attendance_label(0, None, CLASS_START, CLASS_END) == "결석"


def test_attendance_label_negative_seconds_is_absent():
    assert attendance_label(-5, None, CLASS_START, CLASS_END) == "결석"


def test_attendance_label_late_first_in_beyond_grace():
    first_in = CLASS_START + 6 * 60
    assert attendance_label(1800, first_in, CLASS_START, CLASS_END) == "지각"


def test_attendance_label_on_time_within_grace_is_present():
    first_in = CLASS_START + 4 * 60
    assert attendance_label(3600, first_in, CLASS_START, CLASS_END) == "출석"


def test_attendance_label_short_duration_is_late_even_if_on_time():
    # attended < 50% of class duration -> late, even without a late first-in
    assert attendance_label(1000, CLASS_START, CLASS_START, CLASS_END) == "지각"


def test_attendance_label_full_duration_present():
    assert attendance_label(3600, CLASS_START, CLASS_START, CLASS_END) == "출석"


def test_attendance_label_missing_times_falls_back_to_present():
    assert attendance_label(100, None, None, None) == "출석"


# -- ingest: attendance -------------------------------------------------------


def test_ingest_attendance_creates_lesson_records(store):
    cmd = ingest(store, attendance_payload())
    assert cmd == "Attendance"

    rows = {r["student_uid"]: r for r in store.lesson_records()}
    assert set(rows) == {10001, 10002, 10003}

    assert rows[10001]["attendance"] == "출석"
    assert rows[10002]["attendance"] == "결석"
    assert rows[10003]["attendance"] == "지각"

    # teacher captured on every student row
    for r in rows.values():
        assert r["teacher_uid"] == 90001
        assert r["teacher_name"] == "Ms. Kim"


def test_ingest_attendance_registers_teacher_in_teachers_table(store):
    ingest(store, attendance_payload())
    teachers = {t["uid"]: t for t in store.teachers()}
    assert 90001 in teachers
    assert teachers[90001]["name"] == "Ms. Kim"


def test_ingest_attendance_registers_students(store):
    ingest(store, attendance_payload())
    students = {s["uid"]: s for s in store.students()}
    assert students[10001]["name"] == "Alice"
    assert students[10002]["name"] == "Bob"
    assert students[10003]["name"] == "Cara"
    # teacher must not appear in students table
    assert 90001 not in students


def test_ingest_attendance_does_not_create_row_for_teacher(store):
    ingest(store, attendance_payload())
    rows = store.lesson_records()
    assert all(r["student_uid"] != 90001 for r in rows)


# -- ingest: end patches -------------------------------------------------------


def test_ingest_end_patches_camera_hand_trophy(store):
    ingest(store, attendance_payload())
    ingest(store, end_payload())
    rows = {r["student_uid"]: r for r in store.lesson_records()}
    r = rows[10001]
    assert r["camera_minutes"] == 60.0
    assert r["hand_raise"] == 5.0
    assert r["trophy"] == 2.0
    # attendance from the earlier event must be preserved (patch, not overwrite)
    assert r["attendance"] == "출석"


# -- ingest: homework submit ---------------------------------------------------


def test_ingest_homework_submit_sets_submitted_flag(store):
    ingest(store, attendance_payload())
    ingest(store, homework_submit_payload())
    rows = {r["student_uid"]: r for r in store.lesson_records()}
    assert rows[10001]["homework_submitted"] == 1
    assert rows[10001]["homework_activity_id"] == 777
    assert rows[10001]["homework_late"] == 0


def test_ingest_homework_submit_late_flag(store):
    payload = homework_submit_payload()
    payload["Data"]["IsSubmitLate"] = 1
    ingest(store, attendance_payload())
    ingest(store, payload)
    rows = {r["student_uid"]: r for r in store.lesson_records()}
    assert rows[10001]["homework_late"] == 1


def test_ingest_homework_submit_other_students_unaffected(store):
    ingest(store, attendance_payload())
    ingest(store, homework_submit_payload())
    rows = {r["student_uid"]: r for r in store.lesson_records()}
    assert rows[10002]["homework_submitted"] is None


# -- ingest: dedupe on _id -----------------------------------------------------


def test_ingest_duplicate_msg_id_is_ignored(store):
    payload = attendance_payload(msg_id="dup-1")
    ingest(store, payload)
    ingest(store, payload)  # same payload, same _id
    events = store.events()
    assert len(events) == 1


def test_ingest_duplicate_does_not_double_apply_effects(store):
    payload = end_payload(msg_id="dup-end")
    ingest(store, attendance_payload())
    ingest(store, payload)
    ingest(store, payload)  # duplicate End event
    rows = {r["student_uid"]: r for r in store.lesson_records()}
    # hand_raise should be exactly the single value applied once, not summed
    assert rows[10001]["hand_raise"] == 5.0


def test_ingest_events_without_id_are_all_stored(store):
    # no _id -> msg_id None -> unique index (WHERE msg_id IS NOT NULL) doesn't dedupe
    p1 = attendance_payload(msg_id=None)
    del p1["_id"]
    p2 = attendance_payload(msg_id=None)
    del p2["_id"]
    ingest(store, p1)
    ingest(store, p2)
    events = store.events()
    assert len(events) == 2
