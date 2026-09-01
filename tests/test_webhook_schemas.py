from classin_dashboard.classin.webhook_schemas import (
    AnswerSheetScoreEvent,
    AttendanceEvent,
    AttendanceMember,
    EndEvent,
    GenericEvent,
    HomeworkParty,
    HomeworkScoreEvent,
    HomeworkSubmitEvent,
    RatingEvent,
    parse_event,
)


# -- parse_event dispatch by Cmd ---------------------------------------------


def test_parse_event_dispatches_attendance():
    event = parse_event({"Cmd": "Attendance", "ClassID": 1, "Data": []})
    assert isinstance(event, AttendanceEvent)


def test_parse_event_dispatches_end():
    event = parse_event({"Cmd": "End", "ClassID": 1, "Data": {}})
    assert isinstance(event, EndEvent)


def test_parse_event_dispatches_homework_submit():
    event = parse_event(
        {"Cmd": "HomeworkSubmit", "Data": {"ActivityId": 5, "StudentInfo": {"Uid": 1}}}
    )
    assert isinstance(event, HomeworkSubmitEvent)


def test_parse_event_dispatches_homework_score():
    event = parse_event({"Cmd": "HomeworkScore", "Data": {"ActivityId": 5}})
    assert isinstance(event, HomeworkScoreEvent)


def test_parse_event_dispatches_answer_sheet_score():
    event = parse_event({"Cmd": "AnswerSheetScore", "Data": {"ActivityId": 5}})
    assert isinstance(event, AnswerSheetScoreEvent)


def test_parse_event_dispatches_rating():
    event = parse_event({"Cmd": "Rating", "TUID": 1, "Comments": {}})
    assert isinstance(event, RatingEvent)


def test_parse_event_unknown_cmd_falls_back_to_generic():
    event = parse_event({"Cmd": "ChatContent", "foo": "bar"})
    assert isinstance(event, GenericEvent)
    assert event.Cmd == "ChatContent"


def test_parse_event_missing_cmd_is_generic_with_empty_cmd():
    event = parse_event({"foo": "bar"})
    assert isinstance(event, GenericEvent)
    assert event.Cmd == ""


# -- AttendanceMember.is_student / is_teacher --------------------------------


def test_is_student_identity_none():
    m = AttendanceMember(Uid=1, Identity=None)
    assert m.is_student is True
    assert m.is_teacher is False


def test_is_student_identity_0():
    m = AttendanceMember(Uid=1, Identity=0)
    assert m.is_student is True
    assert m.is_teacher is False


def test_is_student_identity_1_student():
    m = AttendanceMember(Uid=1, Identity=1)
    assert m.is_student is True
    assert m.is_teacher is False


def test_identity_2_audit_is_neither_student_nor_teacher():
    m = AttendanceMember(Uid=1, Identity=2)
    assert m.is_student is False
    assert m.is_teacher is False


def test_identity_3_is_teacher():
    m = AttendanceMember(Uid=1, Identity=3)
    assert m.is_student is False
    assert m.is_teacher is True


def test_identity_4_co_teacher_is_teacher():
    m = AttendanceMember(Uid=1, Identity=4)
    assert m.is_student is False
    assert m.is_teacher is True


# -- EndEvent extractors ------------------------------------------------------


def test_hand_raise_by_uid():
    event = EndEvent(
        Cmd="End", ClassID=1, Data={"handsupEnd": {"10001": {"Total": 3, "CTime": 12}}}
    )
    assert event.hand_raise_by_uid() == {10001: 3.0}


def test_trophy_by_uid():
    event = EndEvent(Cmd="End", ClassID=1, Data={"awardEnd": {"10001": {"Total": 2}}})
    assert event.trophy_by_uid() == {10001: 2.0}


def test_camera_minutes_by_uid_converts_seconds_to_minutes():
    event = EndEvent(
        Cmd="End",
        ClassID=1,
        Data={"equipmentsEnd": {"10001": {"Camera": {"Total": 3600}, "Microphone": {"Total": 60}}}},
    )
    assert event.camera_minutes_by_uid() == {10001: 60.0}


def test_mic_minutes_by_uid():
    event = EndEvent(
        Cmd="End",
        ClassID=1,
        Data={"equipmentsEnd": {"10001": {"Microphone": {"Total": 120}}}},
    )
    assert event.mic_minutes_by_uid() == {10001: 2.0}


def test_in_class_seconds_by_uid_uses_total():
    event = EndEvent(Cmd="End", ClassID=1, Data={"inoutEnd": {"10001": {"Total": 1800}}})
    assert event.in_class_seconds_by_uid() == {10001: 1800.0}


def test_poll_by_uid_counts_participants_across_answers():
    event = EndEvent(
        Cmd="End",
        ClassID=1,
        Data={
            "answerEnd": {
                "Answers": [
                    {"Participants": [{"Uid": "10001"}, {"Uid": 10002}]},
                    {"Participants": [{"Uid": 10001}]},
                ]
            }
        },
    )
    assert event.poll_by_uid() == {10001: 2.0, 10002: 1.0}


def test_poll_by_uid_missing_answer_end_returns_empty():
    event = EndEvent(Cmd="End", ClassID=1, Data={})
    assert event.poll_by_uid() == {}


def test_end_event_extractors_ignore_non_numeric_uid_keys():
    event = EndEvent(Cmd="End", ClassID=1, Data={"handsupEnd": {"not-a-uid": {"Total": 3}}})
    assert event.hand_raise_by_uid() == {}


# -- HomeworkParty: Uid/StudentUid alias handling ----------------------------


def test_homework_party_accepts_uid_form():
    p = HomeworkParty(Uid=1, Name="Kim")
    assert p.Uid == 1
    assert p.Name == "Kim"


def test_homework_party_accepts_student_uid_form():
    p = HomeworkParty.model_validate(
        {"StudentUid": 2, "StudentName": "Lee", "StudentAccount": "lee1"}
    )
    assert p.Uid == 2
    assert p.Name == "Lee"
    assert p.Account == "lee1"


def test_homework_party_accepts_teacher_uid_form():
    p = HomeworkParty.model_validate({"TeacherUid": 3, "TeacherName": "Park"})
    assert p.Uid == 3
    assert p.Name == "Park"


def test_homework_party_uid_form_takes_priority_over_student_uid():
    # setdefault: explicit Uid wins if already present in payload.
    p = HomeworkParty.model_validate({"Uid": 9, "StudentUid": 2})
    assert p.Uid == 9


# -- RatingEvent.student_to_teacher_scores -----------------------------------


def test_student_to_teacher_scores_extracts_s2t_entries():
    event = RatingEvent(
        Cmd="Rating",
        TUID=99,
        Comments={
            "10001": {
                "Account": "a",
                "T2S": {"Comment": "good job", "Score": 5},
                "S2T": {"Comment": "great teacher", "Score": 4.5},
            },
            "10002": {"T2S": {"Comment": "nice"}},
        },
    )
    scores = event.student_to_teacher_scores()
    assert scores == [{"student_uid": "10001", "score": 4.5, "comment": "great teacher"}]


def test_student_to_teacher_scores_empty_comments():
    event = RatingEvent(Cmd="Rating", TUID=99, Comments={})
    assert event.student_to_teacher_scores() == []
