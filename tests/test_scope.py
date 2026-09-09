"""ADR-0005: 관/선생님 스코프가 store 계층에서 실제로 걸리는지."""

import time

import pytest

from classin_dashboard import metrics
from classin_dashboard.ingest import ingest
from classin_dashboard.scope import Scope, scope_for
from classin_dashboard.store import EventStore, hash_password, verify_password

CLASS_START = int(time.time()) - 3 * 3600
CLASS_END = CLASS_START + 3600


@pytest.fixture
def store(tmp_path):
    return EventStore(tmp_path / "dashboard.db", tmp_path / "webhook")


def attendance(class_id, course_id, course_name, student_uid, teacher_uid, teacher_name):
    return {
        "_id": f"att-{class_id}-{student_uid}",
        "Cmd": "Attendance",
        "ClassID": class_id,
        "CourseID": course_id,
        "CourseName": course_name,
        "ClassName": f"Lesson {class_id}",
        "ClassStartTime": CLASS_START,
        "ClassEndTime": CLASS_END,
        "Data": [
            {
                "Uid": student_uid,
                "Name": f"학생 {student_uid}",
                "Identity": 1,
                "AttendanceTime": 3600,
                "FirstInTime": CLASS_START,
            },
            {
                "Uid": teacher_uid,
                "Name": teacher_name,
                "Identity": 3,
                "AttendanceTime": 3600,
                "FirstInTime": CLASS_START,
            },
        ],
    }


@pytest.fixture
def school(store):
    """두 개의 관 + 미배정 코스 하나."""
    main = store.upsert_branch("본관")
    annex = store.upsert_branch("분관")

    ingest(store, attendance(11, 1, "본관 수학", 1001, 100, "김민준"))
    ingest(store, attendance(21, 2, "분관 영어", 2001, 200, "이서연"))
    ingest(store, attendance(31, 3, "미배정 과학", 3001, 300, "박지훈"))

    store.set_course_branch(1, main)
    store.set_course_branch(2, annex)
    store.set_teacher_branch(100, main)
    store.set_teacher_branch(200, annex)
    # course 3 / teacher 300 stay unassigned on purpose
    return {"main": main, "annex": annex, "store": store}


class FakeSession:
    def __init__(self, role, *, branch_id=None, teacher_uid=None, branch_filter=None):
        self.role = role
        self.branch_id = branch_id
        self.teacher_uid = teacher_uid
        self.branch_filter = branch_filter


# -- passwords ---------------------------------------------------------------


def test_password_hash_roundtrip():
    stored = hash_password("s3cret")
    assert stored.startswith("pbkdf2$200000$")
    assert verify_password(stored, "s3cret")
    assert not verify_password(stored, "s3cre")
    assert not verify_password(None, "s3cret")
    assert not verify_password("garbage", "s3cret")


def test_password_hashes_are_salted():
    assert hash_password("x") != hash_password("x")


# -- scope derivation --------------------------------------------------------


def test_scope_for_roles(school):
    store = school["store"]
    assert scope_for(FakeSession("owner"), store) == Scope.ALL
    assert scope_for(FakeSession("owner", branch_filter=school["main"]), store).branch_ids == {
        school["main"]
    }
    assert scope_for(FakeSession("manager", branch_id=school["annex"]), store).branch_ids == {
        school["annex"]
    }
    assert scope_for(FakeSession("teacher", teacher_uid=100), store).teacher_uids == {100}
    # owner filtering on a branch that no longer exists falls back to 전체
    assert scope_for(FakeSession("owner", branch_filter=9999), store) == Scope.ALL


# -- store filtering ---------------------------------------------------------


def test_manager_sees_only_own_branch(school):
    store = school["store"]
    scope = Scope.for_branch(school["main"])

    assert {c["course_id"] for c in store.courses(scope=scope)} == {1}
    assert {s["uid"] for s in store.students(scope=scope)} == {1001}
    assert {t["uid"] for t in store.teachers(scope=scope)} == {100}
    assert {r["course_id"] for r in store.lesson_records(scope=scope)} == {1}
    assert {le["course_id"] for le in store.lessons(scope=scope)} == {1}


def test_unassigned_courses_are_owner_only(school):
    store = school["store"]
    for scope in (Scope.for_branch(school["main"]), Scope.for_branch(school["annex"])):
        assert 3 not in {c["course_id"] for c in store.courses(scope=scope)}
    assert 3 in {c["course_id"] for c in store.courses(scope=Scope.ALL)}


def test_teacher_sees_only_own_records_and_course(school):
    store = school["store"]
    scope = Scope.for_teacher(200)

    assert {c["course_id"] for c in store.courses(scope=scope)} == {2}
    assert {r["student_uid"] for r in store.lesson_records(scope=scope)} == {2001}
    assert {t["uid"] for t in store.teachers(scope=scope)} == {200}
    assert store.students(scope=scope) == [s for s in store._all_students() if s["uid"] == 2001]


def test_owner_sees_everything_and_branch_filter_narrows(school):
    store = school["store"]
    assert {c["course_id"] for c in store.courses(scope=Scope.ALL)} == {1, 2, 3}
    assert len(store.students(scope=Scope.ALL)) == 3

    narrowed = Scope.for_branch(school["annex"])
    assert {c["course_id"] for c in store.courses(scope=narrowed)} == {2}


def test_course_taught_by_branch_teacher_is_in_branch_scope(school):
    """A course with no branch_id is still visible when a branch teacher taught it."""
    store = school["store"]
    store.set_teacher_branch(300, school["main"])
    scope = Scope.for_branch(school["main"])
    assert {c["course_id"] for c in store.courses(scope=scope)} == {1, 3}


def test_events_and_notification_history_are_scoped(school):
    store = school["store"]
    store.append_notification(
        event_type="homework",
        provider="log",
        status="dry_run",
        student_uid=1001,
        student_name="학생 1001",
        message="본관",
    )
    store.append_notification(
        event_type="homework",
        provider="log",
        status="dry_run",
        student_uid=2001,
        student_name="학생 2001",
        message="분관",
    )
    scope = Scope.for_branch(school["main"])
    assert [h["student_uid"] for h in store.notification_history(scope=scope)] == [1001]
    assert len(store.notification_history(scope=Scope.ALL)) == 2

    assert {e["course_id"] for e in store.events(scope=scope)} == {1}
    assert store.counts_by_cmd(scope=scope) == {"Attendance": 1}
    assert store.counts_by_cmd(scope=Scope.ALL) == {"Attendance": 3}


def test_empty_branch_scope_sees_nothing(school):
    """A manager without a 관 gets an empty scope, not everything."""
    store = school["store"]
    scope = scope_for(FakeSession("manager"), store)
    assert store.courses(scope=scope) == []
    assert store.students(scope=scope) == []


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.courses(),
        lambda s: s.students(),
        lambda s: s.teachers(),
        lambda s: s.lessons(),
        lambda s: s.lesson_records(),
        lambda s: s.events(),
        lambda s: s.notification_history(),
        lambda s: s.counts_by_cmd(),
    ],
)
def test_page_reads_require_a_scope(store, call):
    with pytest.raises(TypeError):
        call(store)


def test_existing_database_gains_branch_columns(tmp_path):
    """ALTER TABLE migration: a pre-ADR-0005 database opens and upgrades in place."""
    import sqlite3

    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE courses (course_id INTEGER PRIMARY KEY, name TEXT,"
            " teacher_uid INTEGER, teacher_name TEXT, created_via TEXT, created_at TEXT)"
        )
        conn.execute("CREATE TABLE teachers (uid INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO courses (course_id, name) VALUES (1, '기존 코스')")
        conn.execute("INSERT INTO teachers (uid, name) VALUES (100, '김민준')")

    opened = EventStore(db, tmp_path / "webhook")
    branch = opened.upsert_branch("본관")
    opened.set_course_branch(1, branch)
    opened.set_teacher_branch(100, branch)

    assert opened._all_courses()[0]["branch_id"] == branch
    assert opened.get_teacher(100)["branch_id"] == branch
    # re-opening the same file is a no-op, not an error
    assert EventStore(db, tmp_path / "webhook")._all_courses()[0]["name"] == "기존 코스"


def test_internal_reads_stay_unscoped(school):
    """Admin/ingest paths keep seeing everything through the _all_* helpers."""
    store = school["store"]
    assert len(store._all_courses()) == 3
    assert len(store._all_teachers()) == 3
    assert len(store._all_students()) == 3


# -- metrics pass the scope through -----------------------------------------


def test_metrics_are_scoped(school):
    store = school["store"]
    main = Scope.for_branch(school["main"])

    assert metrics.overview(store, scope=main)["student_count"] == 1
    assert metrics.overview(store, scope=Scope.ALL)["student_count"] == 3
    assert [s["uid"] for s in metrics.students_summary(store, scope=main)] == [1001]
    assert [t["uid"] for t in metrics.teachers_summary(store, scope=main)] == [100]
    assert metrics.student_detail(store, 2001, scope=main)["metrics"]["lesson_count"] == 0
