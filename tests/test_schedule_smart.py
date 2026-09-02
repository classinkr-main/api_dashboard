from datetime import date

import pytest

from classin_dashboard.config import Settings
from classin_dashboard.intelligence import schedule_parser as sp
from classin_dashboard.store import EventStore


@pytest.fixture
def store(tmp_path):
    return EventStore(tmp_path / "dashboard.db", tmp_path / "webhook")


def settings(**overrides):
    kwargs = dict(anthropic_api_key="", secret_key="k")
    kwargs.update(overrides)
    return Settings(**kwargs)


def plan_of(store, text, *, today=date(2026, 4, 1), **overrides):
    return sp.smart_parse(settings(**overrides), store, text, today=today)


# -- CSV / TSV ---------------------------------------------------------------

CSV_KO = """코스,선생님,날짜,시작,종료,수업,숙제,마감
고2 수학 A반,김선생,2026-05-06,19:00,21:00,지수함수 1,워크북 p.42-48,2026-05-08 23:59
고2 수학 A반,김선생,2026-05-08,19:00,21:00,지수함수 2,,
"""

CSV_EN = """course_name,teacher,date,start,end,lesson_title,homework_title,homework_due
고2 수학 A반,김선생,2026-05-06,19:00,21:00,Exponential 1,Workbook p.42,2026-05-08
"""

TSV_COMBINED = (
    "코스\t선생님\t날짜\t수업\n"
    "고2 수학 A반\t김선생\t2026-05-06 19:00-21:00\t지수함수 1\n"
    "고2 수학 A반\t김선생\t2026-05-08 19:00-21:00\t지수함수 2\n"
)


def test_detect_format_csv_vs_text():
    assert sp.detect_format(CSV_KO) == "csv"
    assert sp.detect_format(CSV_EN) == "csv"
    assert sp.detect_format(TSV_COMBINED) == "csv"
    assert sp.detect_format("고2 수학 A반 김선생 화/목 19:00-21:00") == "text"
    # A comma-separated free-text line is not a table.
    assert sp.detect_format("고2 수학 A반, 김선생, 화/목 19:00-21:00\n숙제, 워크북") == "text"


def test_csv_korean_headers(store):
    plan = plan_of(store, CSV_KO)
    assert plan.source_format == "csv"
    (course,) = plan.courses
    assert course.course_name == "고2 수학 A반"
    assert course.teacher_name == "김선생"
    assert [lesson.title for lesson in course.lessons] == ["지수함수 1", "지수함수 2"]

    first = course.lessons[0]
    assert first.start_at.utcoffset().total_seconds() == 9 * 3600
    assert first.start_at.isoformat() == "2026-05-06T19:00:00+09:00"
    assert first.end_at.isoformat() == "2026-05-06T21:00:00+09:00"
    assert first.homework.title == "워크북 p.42-48"
    assert first.homework.due_at.isoformat() == "2026-05-08T23:59:00+09:00"
    assert course.lessons[1].homework is None


def test_csv_english_headers_and_bare_due_date(store):
    plan = plan_of(store, CSV_EN)
    (course,) = plan.courses
    lesson = course.lessons[0]
    assert lesson.title == "Exponential 1"
    assert lesson.homework.due_at.isoformat() == "2026-05-08T23:59:00+09:00"


def test_tsv_with_combined_datetime_cell(store):
    plan = plan_of(store, TSV_COMBINED)
    assert plan.source_format == "csv"
    (course,) = plan.courses
    assert [lesson.start_at.isoformat() for lesson in course.lessons] == [
        "2026-05-06T19:00:00+09:00",
        "2026-05-08T19:00:00+09:00",
    ]
    assert course.lessons[0].end_at.isoformat() == "2026-05-06T21:00:00+09:00"


def test_csv_without_end_column_assumes_two_hours(store):
    text = "코스,날짜,시작\n고1 국어,2026-05-06,19:00\n"
    plan = plan_of(store, text)
    lesson = plan.courses[0].lessons[0]
    assert lesson.end_at.isoformat() == "2026-05-06T21:00:00+09:00"
    assert any("+2시간" in a for a in plan.assumptions)


# -- heuristic free text -----------------------------------------------------


def test_heuristic_expands_weekday_pattern_over_four_weeks(store):
    plan = plan_of(
        store,
        "고2 수학 A반 김선생 화/목 19:00-21:00 5월 첫째 주부터 4주",
    )
    assert plan.source_format == "text-heuristic"
    (course,) = plan.courses
    assert course.course_name == "고2 수학 A반"
    assert course.teacher_name == "김선생"
    assert len(course.lessons) == 8

    starts = [lesson.start_at for lesson in course.lessons]
    assert starts[0].isoformat() == "2026-05-05T19:00:00+09:00"  # Tue of the 5/4 week
    assert starts[1].isoformat() == "2026-05-07T19:00:00+09:00"
    assert starts[-1].isoformat() == "2026-05-28T19:00:00+09:00"
    assert {s.weekday() for s in starts} == {1, 3}
    assert all(s.utcoffset().total_seconds() == 9 * 3600 for s in starts)
    assert all(lesson.end_at.hour == 21 for lesson in course.lessons)
    assert course.confidence == pytest.approx(0.6)
    assert any("5/4" in a for a in plan.assumptions)


def test_heuristic_time_shorthand_maps_to_evening(store):
    plan = plan_of(store, "중3 영어 B반 월,수 7-9시 다음 주부터 2주")
    (course,) = plan.courses
    assert len(course.lessons) == 4
    assert course.lessons[0].start_at.hour == 19
    assert course.lessons[0].end_at.hour == 21
    assert course.lessons[0].start_at.isoformat() == "2026-04-06T19:00:00+09:00"


def test_heuristic_session_count_and_homework(store):
    plan = plan_of(
        store,
        "고1 국어 박선생 화/목 19:00-21:00 5월 첫째 주부터 3회\n숙제 워크북 p.42 이틀 뒤 마감",
    )
    (course,) = plan.courses
    assert len(course.lessons) == 3
    homework = course.lessons[0].homework
    assert homework.title == "워크북 p.42"
    assert homework.due_at.isoformat() == "2026-05-07T23:59:00+09:00"


def test_heuristic_multiple_courses_in_one_paste(store):
    plan = plan_of(
        store,
        "고2 수학 A반 김선생 화/목 19:00-21:00 5월 첫째 주부터 2주\n"
        "중3 영어 B반 이선생 월/수 17:00-19:00 5월 첫째 주부터 2주",
    )
    assert [c.course_name for c in plan.courses] == ["고2 수학 A반", "중3 영어 B반"]
    assert all(len(c.lessons) == 4 for c in plan.courses)


def test_no_ai_key_never_raises_on_junk_text(store):
    for junk in ("안녕하세요", "?????", "숙제만 있음"):
        with pytest.raises(ValueError):
            plan_of(store, junk)
    # a parseable line still works without a key and without touching the network
    assert plan_of(store, "고1 국어 화/목 19:00-21:00 5월 첫째 주부터 1주").courses


def test_heuristic_uses_known_teacher_names_from_store(store):
    store.upsert_teacher(20001, "Alex Kim")
    plan = plan_of(store, "고3 물리 Alex Kim 화/목 19:00-21:00 5월 첫째 주부터 1주")
    (course,) = plan.courses
    assert course.teacher_name == "Alex Kim"
    assert course.teacher_uid == 20001


# -- AI path (mocked) --------------------------------------------------------


def test_text_ai_path_uses_run_json(store, monkeypatch):
    captured = {}

    def fake_run_json(settings, *, system, user, **kwargs):
        captured["system"] = system
        captured["user"] = user
        return {
            "courses": [
                {
                    "course_name": "고2 수학 A반",
                    "teacher_name": "김선생",
                    "confidence": 0.92,
                    "lessons": [
                        {
                            "title": "지수함수 1",
                            "start_at": "2026-05-06T19:00:00+09:00",
                            "end_at": "2026-05-06T21:00:00+09:00",
                            "confidence": 0.9,
                            "homework": {
                                "title": "워크북 p.42",
                                "due_at": "2026-05-08T23:59:00+09:00",
                            },
                        }
                    ],
                }
            ],
            "assumptions": ["첫째 주를 5/4 주로 가정"],
        }

    monkeypatch.setattr(sp, "run_json", fake_run_json)
    plan = plan_of(store, "고2 수학 A반 김선생 화/목 저녁", anthropic_api_key="sk-test")

    assert plan.source_format == "text-ai"
    assert plan.assumptions == ["첫째 주를 5/4 주로 가정"]
    (course,) = plan.courses
    assert course.confidence == pytest.approx(0.92)
    assert course.lessons[0].confidence == pytest.approx(0.9)
    assert "confidence" in captured["system"]
    assert "2026-04-01" in captured["user"]


def test_ai_failure_falls_back_to_heuristics(store, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("no")

    monkeypatch.setattr(sp, "run_json", boom)
    plan = plan_of(
        store,
        "고2 수학 A반 김선생 화/목 19:00-21:00 5월 첫째 주부터 1주",
        anthropic_api_key="sk-test",
    )
    assert plan.source_format == "text-heuristic"
    assert len(plan.courses[0].lessons) == 2


# -- entity resolution -------------------------------------------------------


def test_teacher_resolution_exact_suffix_and_fuzzy(store):
    store.upsert_teacher(20001, "김선생")
    store.upsert_teacher(20002, "이영희")
    plan = sp.ParsePlan(
        courses=[
            sp.PlannedCourse(course_name="A", teacher_name="김선생"),
            sp.PlannedCourse(course_name="B", teacher_name="김선생님"),
            sp.PlannedCourse(course_name="C", teacher_name="이영희님"),
        ]
    )
    sp.resolve_entities(plan, store)
    assert [c.teacher_uid for c in plan.courses] == [20001, 20001, 20002]
    assert plan.needs_confirm == []


def test_normalize_teacher_name_strips_titles():
    assert sp.normalize_teacher_name("김선생님") == sp.normalize_teacher_name("김선생") == "김"
    assert sp.normalize_teacher_name("김 쌤") == "김"
    assert sp.normalize_teacher_name("민수T") == "민수"


def test_single_registered_teacher_is_assigned_by_default(store):
    store.upsert_teacher(20009, "유일선생")
    plan = sp.ParsePlan(courses=[sp.PlannedCourse(course_name="고1 국어")])
    sp.resolve_entities(plan, store)
    assert plan.courses[0].teacher_uid == 20009
    assert plan.needs_confirm == []
    assert any("유일선생" in a for a in plan.assumptions)


def test_two_equally_matching_teachers_need_confirmation(store):
    store.upsert_teacher(20001, "김선생")
    store.upsert_teacher(20002, "김쌤")
    plan = sp.ParsePlan(courses=[sp.PlannedCourse(course_name="A", teacher_name="김선생님")])
    sp.resolve_entities(plan, store)

    course = plan.courses[0]
    assert course.teacher_uid is None
    assert sorted(t["uid"] for t in course.teacher_candidates) == [20001, 20002]
    (question,) = plan.needs_confirm
    assert question.kind == "teacher"
    assert question.subject == "김선생님"
    assert len(question.candidates) == 2


def test_unknown_teacher_asks_with_all_candidates(store):
    store.upsert_teacher(20001, "김선생")
    plan = sp.ParsePlan(courses=[sp.PlannedCourse(course_name="A", teacher_name="박아무개")])
    sp.resolve_entities(plan, store)
    assert plan.courses[0].teacher_uid is None
    assert plan.needs_confirm[0].kind == "teacher"
    assert plan.needs_confirm[0].candidates == [{"uid": 20001, "name": "김선생"}]


def test_existing_course_is_reused(store):
    store.upsert_teacher(20001, "김선생")
    store.upsert_course(777, name="고2 수학 A반", teacher_uid=20001)
    plan = plan_of(store, CSV_KO)
    course = plan.courses[0]
    assert course.existing_course_id == 777
    assert course.teacher_uid == 20001
    assert any("재사용" in a for a in plan.assumptions)


def test_unrelated_course_name_is_not_reused(store):
    store.upsert_course(777, name="고2 수학 A반")
    plan = sp.ParsePlan(courses=[sp.PlannedCourse(course_name="중3 영어 B반")])
    sp.resolve_entities(plan, store)
    assert plan.courses[0].existing_course_id is None


def test_smart_parse_rejects_empty_input(store):
    with pytest.raises(ValueError):
        plan_of(store, "   ")
