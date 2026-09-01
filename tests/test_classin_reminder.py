from classin_dashboard.notify.classin_reminder import (
    NAME_LIMIT,
    group_rows_by_course,
    post_app_reminders,
    reminder_name,
)
from classin_dashboard.store import EventStore


def _store(tmp_path):
    return EventStore(tmp_path / "t.db", tmp_path / "raw")


def test_group_rows_by_course_skips_missing_course():
    rows = [
        {"course_id": 1, "student_uid": 10},
        {"course_id": 1, "student_uid": 11},
        {"course_id": 2, "student_uid": 12},
        {"course_id": None, "student_uid": 13},
    ]
    grouped = group_rows_by_course(rows)
    assert set(grouped) == {1, 2}
    assert len(grouped[1]) == 2


def test_reminder_name_lists_students_within_limit():
    rows = [{"student_name": "김성실"}, {"student_name": "이지각"}]
    name = reminder_name(rows, today="9/1")
    assert name == "숙제 리마인드 9/1 — 김성실, 이지각"
    assert len(name) <= NAME_LIMIT


def test_reminder_name_truncates_with_count():
    rows = [{"student_name": f"학생이름{i:02d}"} for i in range(20)]
    name = reminder_name(rows, today="9/1")
    assert len(name) <= NAME_LIMIT
    assert "외" in name


def test_reminder_name_dedupes_students():
    rows = [{"student_name": "김성실"}, {"student_name": "김성실"}]
    assert reminder_name(rows, today="9/1") == "숙제 리마인드 9/1 — 김성실"


class FakeActions:
    def __init__(self, fail_release=False):
        self.calls = []
        self.fail_release = fail_release

    def create_unit(self, *, course_id, name):
        self.calls.append(("unit", course_id, name))
        return 900 + course_id

    def create_activity(self, *, course_id, unit_id, activity_type, name, teacher_uid):
        assert activity_type == 6
        self.calls.append(("activity", course_id, unit_id, name, teacher_uid))
        return 7000 + course_id

    def release_activity(self, *, course_id, activity_id):
        if self.fail_release:
            from classin_dashboard.classin.client import ClassInError

            raise ClassInError("/lms/activity/release", 29601, "内容不能为空")
        self.calls.append(("release", course_id, activity_id))
        return {}


def test_post_app_reminders_posts_per_course_and_reuses_unit(tmp_path):
    store = _store(tmp_path)
    store.upsert_course(1, name="수학 A반", teacher_uid=20001)
    rows = [
        {"course_id": 1, "course_name": "수학 A반", "student_uid": 10, "student_name": "김성실"},
        {"course_id": 1, "course_name": "수학 A반", "student_uid": 11, "student_name": "이지각"},
    ]
    actions = FakeActions()
    result = post_app_reminders(actions, store, rows)
    assert len(result["posted"]) == 1 and not result["errors"]
    assert store.course_reminder_unit(1) == 901
    history = store.notification_history()
    assert history[0]["provider"] == "classin_discussion"

    # Second run reuses the stored unit (no new create_unit call).
    actions2 = FakeActions()
    post_app_reminders(actions2, store, rows)
    assert all(c[0] != "unit" for c in actions2.calls)


def test_post_app_reminders_skips_course_without_teacher(tmp_path):
    store = _store(tmp_path)
    store.upsert_course(2, name="영어 B반")  # no teacher_uid anywhere
    rows = [{"course_id": 2, "course_name": "영어 B반", "student_uid": 10, "student_name": "김"}]
    result = post_app_reminders(FakeActions(), store, rows)
    assert not result["posted"]
    assert "선생님 UID" in result["errors"][0]


def test_post_app_reminders_reports_classin_error(tmp_path):
    store = _store(tmp_path)
    store.upsert_course(3, name="과학 C반", teacher_uid=20001)
    rows = [{"course_id": 3, "course_name": "과학 C반", "student_uid": 10, "student_name": "김"}]
    result = post_app_reminders(FakeActions(fail_release=True), store, rows)
    assert not result["posted"]
    assert "内容不能为空" in result["errors"][0]
    assert store.notification_history() == []
