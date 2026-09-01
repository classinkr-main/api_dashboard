"""ClassIn Data Sub (webhook) payload schemas.

Every model keeps unknown fields (`extra="allow"`) because ClassIn's schema
drifts and field naming is inconsistent (PascalCase vs camelCase,
StudentUid vs Uid). Raw payloads are preserved separately, so parsing here is
best-effort normalization, never a gate.

Cmd values handled: Attendance, End, HomeworkSubmit, HomeworkScore,
AnswerSheetScore. Others (Rating, Record, EduDt, ExamScore, ChatContent, ...)
fall through to GenericEvent and are still stored.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class _BaseEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    SID: int | str | None = None
    Cmd: str = ""
    ClassID: int | None = None
    CourseID: int | None = None
    CourseName: str | None = None
    ActionTime: int | None = None
    TimeStamp: int | None = None
    SafeKey: str | None = None

    @property
    def course_id(self) -> int | None:
        return self.CourseID

    @property
    def class_id(self) -> int | None:
        return self.ClassID


# Official Identity enum (datasub): 1=Student, 2=Audit student, 3=Teacher,
# 4=Co-teacher, 193=Principal, 194=Principal assistant.
IDENTITY_STUDENT = 1
IDENTITY_AUDIT = 2
IDENTITY_TEACHER = 3
IDENTITY_CO_TEACHER = 4


class AttendanceMember(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    Uid: int
    Name: str | None = None
    Identity: int | None = None
    AttendanceTime: int = 0  # attended seconds; 0 with null times = absent
    FirstInTime: int | None = None
    LastOutTime: int | None = None

    @property
    def is_student(self) -> bool:
        return self.Identity in (None, 0, IDENTITY_STUDENT)

    @property
    def is_teacher(self) -> bool:
        return self.Identity in (IDENTITY_TEACHER, IDENTITY_CO_TEACHER)


class AttendanceEvent(_BaseEvent):
    ClassName: str | None = None
    ClassStartTime: int | None = None
    ClassEndTime: int | None = None
    ActivityID: int | None = None
    AttendanceStudentNum: int | None = None
    ClassStudentNum: int | None = None
    Data: list[AttendanceMember] = []


def _uid_map_totals(section: Any, *path: str) -> dict[int, float]:
    """Extract {uid: number} from a UID-keyed End-event section.

    Confirmed structure (datasub/classrelated): sections are maps keyed by UID
    string, e.g. handsupEnd["10001"] = {"Total": 3, "CTime": 12}. `path` walks
    nested keys (e.g. "Camera", "Total" for equipmentsEnd).
    """
    out: dict[int, float] = {}
    if not isinstance(section, dict):
        return out
    for uid, entry in section.items():
        try:
            uid_i = int(uid)
        except (TypeError, ValueError):
            continue
        value: Any = entry
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if value is None and isinstance(entry, (int, float)):
            value = entry
        try:
            out[uid_i] = float(value)
        except (TypeError, ValueError):
            continue
    return out


class EndEvent(_BaseEvent):
    """Lesson-close summary, pushed ~20 min after scheduled end.

    Data sections are UID-keyed maps: inoutEnd (in-class seconds), handsupEnd
    ({Total, CTime}), awardEnd ({Total} = trophies), equipmentsEnd
    ({Camera,Microphone}.{Total,TotalNotDisabled}), stageEnd, answerEnd (poll
    summary object), responderEnd, groupEnd, ...
    """

    CloseTime: int | None = None
    RealCloseTime: int | None = None
    StartTime: int | None = None
    Data: dict[str, Any] = {}

    def hand_raise_by_uid(self) -> dict[int, float]:
        return _uid_map_totals(self.Data.get("handsupEnd"), "Total")

    def trophy_by_uid(self) -> dict[int, float]:
        return _uid_map_totals(self.Data.get("awardEnd"), "Total")

    def in_class_seconds_by_uid(self) -> dict[int, float]:
        return _uid_map_totals(self.Data.get("inoutEnd"), "Total")

    def camera_minutes_by_uid(self) -> dict[int, float]:
        secs = _uid_map_totals(self.Data.get("equipmentsEnd"), "Camera", "Total")
        return {uid: round(v / 60, 1) for uid, v in secs.items()}

    def mic_minutes_by_uid(self) -> dict[int, float]:
        secs = _uid_map_totals(self.Data.get("equipmentsEnd"), "Microphone", "Total")
        return {uid: round(v / 60, 1) for uid, v in secs.items()}

    def poll_by_uid(self) -> dict[int, float]:
        """Per-student poll participation count from answerEnd.Answers[]."""
        answer_end = self.Data.get("answerEnd")
        out: dict[int, float] = {}
        if not isinstance(answer_end, dict):
            return out
        for answer in answer_end.get("Answers") or []:
            if not isinstance(answer, dict):
                continue
            for p in answer.get("Participants") or []:
                uid = p.get("Uid") if isinstance(p, dict) else None
                if uid is None:
                    continue
                try:
                    out[int(uid)] = out.get(int(uid), 0) + 1
                except (TypeError, ValueError):
                    continue
        return out


class HomeworkParty(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    Uid: int | None = None
    Name: str | None = None
    Account: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_official_lms_keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        out.setdefault("Uid", out.get("StudentUid") or out.get("TeacherUid"))
        out.setdefault("Name", out.get("StudentName") or out.get("TeacherName"))
        out.setdefault("Account", out.get("StudentAccount") or out.get("TeacherAccount"))
        return out


class HomeworkSubmitData(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    UnitId: int | None = None
    ActivityId: int
    ActivityName: str | None = None
    StudentInfo: HomeworkParty | None = None
    TeacherInfo: HomeworkParty | None = None
    SubmissionTime: int | None = None
    IsSubmitLate: int | bool | None = None
    StudentTotal: int | None = None
    SubmitTotal: int | None = None


class HomeworkSubmitEvent(_BaseEvent):
    Data: HomeworkSubmitData


class HomeworkScoreData(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    UnitId: int | None = None
    ActivityId: int
    ActivityName: str | None = None
    Score: float | None = None
    StudentInfo: HomeworkParty | None = None
    TeacherInfo: HomeworkParty | None = None
    StudentScoringRate: float | None = None

    def score_percent(self) -> float | None:
        if self.StudentScoringRate is not None:
            return round(float(self.StudentScoringRate) * 100, 1)
        return self.Score


class HomeworkScoreEvent(_BaseEvent):
    Data: HomeworkScoreData


class AnswerSheetScoreData(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    UnitId: int | None = None
    UnitName: str | None = None
    ActivityId: int
    ActivityName: str | None = None
    ClassId: int | None = None
    Score: float | None = None
    MaximumScore: float | None = None
    StudentInfo: HomeworkParty | None = None
    TeacherInfo: HomeworkParty | None = None
    SubmissionTime: int | None = None
    StudentScoringRate: float | None = None


class AnswerSheetScoreEvent(_BaseEvent):
    Data: AnswerSheetScoreData


class RatingEvent(_BaseEvent):
    """Teacher↔student mutual evaluation.

    Comments is keyed by student UID: {"<uid>": {"Account":..., "T2S": {"Comment","Score"},
    "S2T": {"Comment","Score"}}}. S2T scores feed the teacher-evaluation trend.
    """

    TUID: int | None = None
    Comments: dict[str, Any] = {}

    def student_to_teacher_scores(self) -> list[dict[str, Any]]:
        out = []
        for uid, entry in self.Comments.items():
            if not isinstance(entry, dict):
                continue
            s2t = entry.get("S2T")
            if isinstance(s2t, dict):
                out.append(
                    {
                        "student_uid": uid,
                        "score": s2t.get("Score"),
                        "comment": s2t.get("Comment"),
                    }
                )
        return out


class GenericEvent(_BaseEvent):
    Data: Any = None


_KNOWN: dict[str, type[_BaseEvent]] = {
    "Attendance": AttendanceEvent,
    "End": EndEvent,
    "HomeworkSubmit": HomeworkSubmitEvent,
    "HomeworkScore": HomeworkScoreEvent,
    "AnswerSheetScore": AnswerSheetScoreEvent,
    "Rating": RatingEvent,
}


def parse_event(raw: dict) -> _BaseEvent:
    cmd = raw.get("Cmd") or raw.get("cmd") or ""
    model = _KNOWN.get(cmd, GenericEvent)
    return model.model_validate(raw)
