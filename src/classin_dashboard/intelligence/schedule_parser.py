"""Free-form schedule text/CSV → structured courses/lessons, resolved to ClassIn entities.

Three parse paths, picked automatically by :func:`smart_parse`:

* ``csv``            — a header row with recognizable (ko/en) column names; deterministic.
* ``text-ai``        — free text with an Anthropic key configured; Claude fills the schema.
* ``text-heuristic`` — free text without a key (or after an AI failure); regex/rule reader.

Whatever the path, :func:`resolve_entities` maps teacher names to ClassIn UIDs and course
names onto already-known courses, so the teacher never types a mapping table. Anything that
stays ambiguous is reported as a :class:`Question` in ``ParsePlan.needs_confirm`` instead of
being guessed.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Literal

from pydantic import BaseModel

from ..config import Settings
from .claude import run_json

log = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

SCHEDULE_PARSE_PROMPT = """당신은 학원 스케줄 파싱 어시스턴트다.
입력으로 받은 자유 형식 스케줄 표(또는 CSV)를 구조화된 JSON 으로 변환한다.

## 출력 스키마 (반드시 이 JSON 객체만 출력, 다른 설명 금지)

{
  "courses": [
    {
      "course_name": "고2 수학 A반",
      "teacher_name": "김선생",
      "confidence": 0.9,
      "lessons": [
        {
          "title": "수1 - 지수함수 1",
          "start_at": "2026-05-06T19:00:00+09:00",
          "end_at":   "2026-05-06T21:00:00+09:00",
          "confidence": 0.9,
          "homework": { "title": "수1 워크북 p.42-48", "due_at": "2026-05-08T23:59:00+09:00" }
        }
      ]
    }
  ],
  "assumptions": ["'5월 첫째 주'를 5/4 주로 가정"]
}

## 규칙
- 한국 시간(KST, +09:00) 기준.
- 숙제가 명시되지 않은 수업은 "homework": null 로 둔다.
- 날짜가 "화/목 7-9시" 같은 요일 패턴이면 명시된 시작/종료 주를 기준으로 실제 날짜로 펼쳐라.
- "7-9시"처럼 오전/오후가 없는 1~9시는 학원 운영시간을 고려해 오후로 해석한다 (19:00-21:00).
- confidence 는 0~1 실수. 입력에 명시된 정보만으로 확신하면 1.0 에 가깝게,
  추론이 섞였으면 낮게 매긴다. 코스마다, 수업마다 각각 매긴다.
- assumptions 에는 입력에 없어서 추론한 내용을 한국어 한 줄씩 적는다
  (예: "첫째 주를 5/4 주로 가정", "종료 시각이 없어 시작 +2시간으로 가정").
- 모호한 항목은 추측하지 말고 생략하라. 환각 금지."""


# -- models -------------------------------------------------------------------


class ParsedHomework(BaseModel):
    title: str
    due_at: datetime | None = None


class ParsedLesson(BaseModel):
    title: str
    start_at: datetime
    end_at: datetime
    homework: ParsedHomework | None = None
    confidence: float = 1.0


class ParsedCourse(BaseModel):
    course_name: str
    teacher_name: str | None = None
    lessons: list[ParsedLesson] = []
    confidence: float = 1.0


class PlannedCourse(ParsedCourse):
    teacher_uid: int | None = None
    existing_course_id: int | None = None
    teacher_candidates: list[dict[str, Any]] = []


class Question(BaseModel):
    kind: Literal["teacher", "course", "date"]
    subject: str
    candidates: list[dict[str, Any]] = []
    message: str


class ParsePlan(BaseModel):
    courses: list[PlannedCourse] = []
    assumptions: list[str] = []
    needs_confirm: list[Question] = []
    source_format: Literal["csv", "text-ai", "text-heuristic"] = "text-heuristic"


SOURCE_LABELS = {
    "csv": "표 (CSV/TSV) 자동 인식",
    "text-ai": "자유 텍스트 · AI 파싱",
    "text-heuristic": "자유 텍스트 · 규칙 기반 파싱",
}


# -- column vocabulary --------------------------------------------------------


def _canon(text: str) -> str:
    return re.sub(r"[\s_\-()]", "", (text or "")).lower()


_COLUMN_ALIASES: dict[str, str] = {}
for _canonical, _names in {
    "course_name": ("course_name", "course", "class", "코스", "코스명", "반", "반이름", "강좌", "강좌명"),
    "teacher": ("teacher", "teacher_name", "선생님", "선생", "강사", "강사명", "담당", "담당선생님"),
    "date": ("date", "날짜", "일자", "수업일", "일정"),
    "start": ("start", "start_at", "start_time", "시작", "시작시간", "시작시각"),
    "end": ("end", "end_at", "end_time", "종료", "종료시간", "종료시각", "끝"),
    "lesson_title": ("lesson_title", "lesson", "title", "수업", "수업명", "수업제목", "차시"),
    "homework_title": ("homework_title", "homework", "숙제", "숙제명", "과제", "과제명"),
    "homework_due": ("homework_due", "due", "due_at", "마감", "마감일", "숙제마감", "제출기한"),
}.items():
    for _name in _names:
        _COLUMN_ALIASES[_canon(_name)] = _canonical


def _column_of(header_cell: str) -> str | None:
    return _COLUMN_ALIASES.get(_canon(header_cell))


# -- small date/time helpers --------------------------------------------------

_WEEKDAY_CHARS = "월화수목금토일"
_KO_COUNT_WORDS = {"하루": 1, "이틀": 2, "사흘": 3, "나흘": 4, "당일": 0}


def _as_date(value: date | datetime | None) -> date:
    if value is None:
        return datetime.now(KST).date()
    if isinstance(value, datetime):
        return value.astimezone(KST).date() if value.tzinfo else value.date()
    return value


def _kst(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=KST)


def _academy_time(hour: int, minute: int, *, shift: bool) -> time:
    """1~9시 with no am/pm marker means evening in an academy timetable."""
    if shift and 1 <= hour <= 9:
        hour += 12
    return time(hour % 24, minute)


_TIME_RANGE_PATTERNS = (
    # 19:00-21:00 / 19시00분~21시00분
    re.compile(r"(\d{1,2})\s*[:시]\s*(\d{2})\s*분?\s*[-~–—]\s*(\d{1,2})\s*[:시]\s*(\d{2})\s*분?"),
    # 7-9시 / 19~21시
    re.compile(r"(\d{1,2})\s*[-~–—]\s*(\d{1,2})\s*시"),
    # 7시-9시
    re.compile(r"(\d{1,2})\s*시\s*[-~–—]\s*(\d{1,2})\s*시"),
)


def _parse_time_range(text: str, *, academy_shift: bool = True) -> tuple[time, time] | None:
    """First 'start-end' time range in *text*, or None."""
    hit = _TIME_RANGE_PATTERNS[0].search(text)
    if hit:
        sh, sm, eh, em = (int(g) for g in hit.groups())
        shift = academy_shift and not _has_am_marker(text)
        return _academy_time(sh, sm, shift=shift), _academy_time(eh, em, shift=shift)
    for pattern in _TIME_RANGE_PATTERNS[1:]:
        hit = pattern.search(text)
        if hit:
            sh, eh = int(hit.group(1)), int(hit.group(2))
            shift = not _has_am_marker(text)
            return _academy_time(sh, 0, shift=shift), _academy_time(eh, 0, shift=shift)
    return None


def _has_am_marker(text: str) -> bool:
    return "오전" in text or "am" in text.lower()


def _parse_clock(text: str, *, academy_shift: bool = False) -> time | None:
    """A single clock reading: '19:00', '7시', '오후 7시'."""
    text = (text or "").strip()
    if not text:
        return None
    pm = "오후" in text or "pm" in text.lower()
    hit = re.search(r"(\d{1,2})\s*[:시]\s*(\d{1,2})", text)
    if hit:
        hour, minute = int(hit.group(1)), int(hit.group(2))
        shift = pm or (academy_shift and not _has_am_marker(text))
        return _academy_time(hour, minute, shift=shift)
    hit = re.search(r"(\d{1,2})\s*시", text)
    if hit:
        return _academy_time(int(hit.group(1)), 0, shift=pm or not _has_am_marker(text))
    if re.fullmatch(r"\d{1,2}", text):
        return _academy_time(int(text), 0, shift=pm or academy_shift)
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _strip_times(text: str) -> str:
    """Blank out clock readings ('19:00-21:00', '7-9시') before scanning for dates."""
    out = text or ""
    for pattern in (
        r"\d{1,2}\s*[:시]\s*\d{2}\s*분?\s*[-~–—]\s*\d{1,2}\s*[:시]\s*\d{2}\s*분?",
        r"\d{1,2}\s*[-~–—]\s*\d{1,2}\s*시",
        r"\d{1,2}\s*[:시]\s*\d{2}\s*분?",
    ):
        out = re.sub(pattern, " ", out)
    return out


def _parse_date(text: str, *, year: int) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    hit = re.search(r"(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})", text)
    if hit:
        return _safe_date(int(hit.group(1)), int(hit.group(2)), int(hit.group(3)))
    hit = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
    if hit:
        return _safe_date(year, int(hit.group(1)), int(hit.group(2)))
    hit = re.search(r"(?<![\d:])(\d{1,2})\s*[-/.]\s*(\d{1,2})(?![\d:])", _strip_times(text))
    if hit:
        return _safe_date(year, int(hit.group(1)), int(hit.group(2)))
    return None


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _nth_week_monday(year: int, month: int, nth: int) -> date:
    """Monday of the *nth* Monday-containing week of *month*."""
    first = date(year, month, 1)
    first_monday = first + timedelta(days=(7 - first.weekday()) % 7)
    return first_monday + timedelta(weeks=nth - 1)


# -- CSV / TSV ----------------------------------------------------------------


def detect_format(raw_text: str) -> Literal["csv", "text"]:
    """'csv' when the first line looks like a delimited header we understand."""
    lines = [ln for ln in (raw_text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return "text"
    header = lines[0]
    delimiter = "\t" if "\t" in header else ("," if "," in header else None)
    if delimiter is None:
        return "text"
    columns = {c for c in (_column_of(cell) for cell in header.split(delimiter)) if c}
    if len(columns) >= 2 and ({"date", "course_name"} & columns):
        return "csv"
    return "text"


def parse_table(
    raw_text: str, *, today: date | datetime | None = None
) -> tuple[list[ParsedCourse], list[str]]:
    """Deterministic CSV/TSV reader. Returns (courses, assumptions)."""
    day = _as_date(today)
    lines = [ln for ln in raw_text.splitlines() if ln.strip()]
    delimiter = "\t" if "\t" in lines[0] else ","
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))
    header = [_column_of(cell) for cell in rows[0]]

    assumptions: list[str] = []
    grouped: dict[str, ParsedCourse] = {}
    for raw_row in rows[1:]:
        cells = {
            column: raw_row[i].strip()
            for i, column in enumerate(header)
            if column and i < len(raw_row)
        }
        lesson_day = _parse_date(cells.get("date", ""), year=day.year)
        if lesson_day is None:
            continue

        start_at, end_at, row_notes = _row_times(cells, lesson_day)
        if start_at is None:
            continue
        for note in row_notes:
            if note not in assumptions:
                assumptions.append(note)

        name = cells.get("course_name") or "미지정 코스"
        course = grouped.get(name)
        if course is None:
            course = grouped[name] = ParsedCourse(course_name=name, lessons=[])
        if not course.teacher_name and cells.get("teacher"):
            course.teacher_name = cells["teacher"]

        title = cells.get("lesson_title") or f"{name} {len(course.lessons) + 1}회차"
        course.lessons.append(
            ParsedLesson(
                title=title,
                start_at=start_at,
                end_at=end_at,
                homework=_row_homework(cells, end_at, day.year),
            )
        )
    return list(grouped.values()), assumptions


def _row_times(
    cells: dict[str, str], lesson_day: date
) -> tuple[datetime | None, datetime | None, list[str]]:
    notes: list[str] = []
    # A single cell may carry the whole slot: "2026-05-06 19:00-21:00".
    combined = _parse_time_range(cells.get("date", ""), academy_shift=False) or _parse_time_range(
        cells.get("start", ""), academy_shift=False
    )
    if combined:
        return _kst(lesson_day, combined[0]), _kst(lesson_day, combined[1]), notes

    start = _parse_clock(cells.get("start", ""))
    if start is None:
        return None, None, notes
    end = _parse_clock(cells.get("end", ""))
    if end is None:
        notes.append("종료 시각이 없어 시작 +2시간으로 가정")
        return (
            _kst(lesson_day, start),
            _kst(lesson_day, start) + timedelta(hours=2),
            notes,
        )
    return _kst(lesson_day, start), _kst(lesson_day, end), notes


def _row_homework(cells: dict[str, str], end_at: datetime, year: int) -> ParsedHomework | None:
    title = cells.get("homework_title")
    if not title:
        return None
    raw_due = cells.get("homework_due", "")
    due_day = _parse_date(raw_due, year=year)
    if due_day is None:
        return ParsedHomework(title=title)
    due_time = _parse_clock(raw_due) or time(23, 59)
    return ParsedHomework(title=title, due_at=_kst(due_day, due_time))


# -- heuristic free-text ------------------------------------------------------

_TEACHER_TOKEN = re.compile(r"([가-힣]{1,4}(?:선생님|선생|쌤|샘)|[가-힣]{2,4}T)(?![가-힣])")
_WEEKDAY_GROUP = re.compile(
    rf"(?<![가-힣])((?:[{_WEEKDAY_CHARS}](?:요일)?)(?:\s*[/,·、]?\s*[{_WEEKDAY_CHARS}](?:요일)?)*)"
    r"(?![가-힣])"
)
_NTH_WEEK = re.compile(r"(?:(\d{1,2})\s*월\s*)?(첫|둘|셋|넷|다섯)\s*째?\s*주")
_WEEK_COUNT = re.compile(r"(\d{1,2})\s*주(?:간|\s*동안|\s*간)?")
_SESSION_COUNT = re.compile(r"(\d{1,3})\s*회(?!차)")
_DUE_PHRASE = re.compile(
    r"(?:수업\s*)?(\d{1,2}|하루|이틀|사흘|나흘|당일)\s*일?\s*(?:뒤|후|후에)\s*(?:까지|마감|제출)?"
)


def normalize_teacher_name(name: str | None) -> str:
    """'김선생님' / '김 선생' / '김T' → '김' for comparison purposes."""
    text = re.sub(r"\s+", "", name or "")
    for suffix in ("선생님", "선생", "쌤", "샘", "티처"):
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    else:
        if len(text) > 1 and text[-1] in "Tt" and text[-2] not in "Tt":
            text = text[:-1]
    return text.lower()


def normalize_course_name(name: str | None) -> str:
    return re.sub(r"\s+", "", name or "").lower()


def _weekday_match(text: str) -> re.Match[str] | None:
    """First weekday token that is really a weekday list — '5월'/'수학' don't count."""
    for hit in _WEEKDAY_GROUP.finditer(text):
        token = hit.group(1)
        letters = [c for c in token if c in _WEEKDAY_CHARS]
        separated = bool(re.search(r"[/,·、]", token)) or "요일" in token
        if letters and (len(letters) >= 2 or separated):
            return hit
    return None


def _weekdays_in(text: str) -> list[int]:
    """Weekday indexes (Mon=0) from '화/목', '월수금', '화요일'."""
    hit = _weekday_match(text)
    if hit is None:
        return []
    seen: list[int] = []
    for letter in hit.group(1):
        if letter in _WEEKDAY_CHARS and _WEEKDAY_CHARS.index(letter) not in seen:
            seen.append(_WEEKDAY_CHARS.index(letter))
    return sorted(seen)


def _anchor_monday(text: str, today: date, assumptions: list[str]) -> date:
    hit = _NTH_WEEK.search(text)
    if hit:
        nth = "첫둘셋넷다섯".index(hit.group(2)[0]) + 1
        month = int(hit.group(1)) if hit.group(1) else today.month
        monday = _nth_week_monday(today.year, month, nth)
        assumptions.append(
            f"'{month}월 {hit.group(2)}째 주'를 {monday.month}/{monday.day} 주로 가정"
        )
        return monday
    explicit = _parse_date(text, year=today.year)
    if explicit:
        return _monday_of(explicit)
    if "다음" in text and "주" in text:
        return _monday_of(today) + timedelta(weeks=1)
    if "이번" in text and "주" in text:
        return _monday_of(today)
    monday = _monday_of(today) + timedelta(weeks=1)
    assumptions.append(f"시작 시점이 없어 다음 주({monday.month}/{monday.day})부터로 가정")
    return monday


def _split_course_name(line: str) -> str:
    """Everything before the first teacher/weekday/time/date token."""
    cuts = [len(line)]
    for hit in (_TEACHER_TOKEN.search(line), _weekday_match(line), _NTH_WEEK.search(line)):
        if hit:
            cuts.append(hit.start())
    hit = re.search(r"\d{1,2}\s*[:시]", line)
    if hit:
        cuts.append(hit.start())
    hit = re.search(r"\d{4}\s*[-/.]\s*\d{1,2}", line)
    if hit:
        cuts.append(hit.start())
    name = line[: min(cuts)].strip(" \t,;·-–—")
    return name


def _find_teacher(line: str, known: list[str]) -> str | None:
    for name in known:
        if name and name in line:
            return name
    hit = _TEACHER_TOKEN.search(line)
    return hit.group(1) if hit else None


def _homework_from(line: str, lesson_end: datetime) -> ParsedHomework:
    body = re.sub(r"^\s*(숙제|과제)\s*[:：\-]?\s*", "", line.strip())
    offset_days: int | None = None
    hit = _DUE_PHRASE.search(body)
    if hit:
        token = hit.group(1)
        offset_days = _KO_COUNT_WORDS.get(token, None)
        if offset_days is None:
            offset_days = int(token)
        body = (body[: hit.start()] + " " + body[hit.end() :]).strip(" ()[],·-")
    title = re.sub(r"\s+", " ", body).strip() or "숙제"
    if offset_days is None:
        return ParsedHomework(title=title)
    due_day = (lesson_end + timedelta(days=offset_days)).date()
    return ParsedHomework(title=title, due_at=_kst(due_day, time(23, 59)))


def heuristic_parse(
    raw_text: str,
    known_teachers: list[str] | None = None,
    *,
    today: date | datetime | None = None,
) -> tuple[list[ParsedCourse], list[str]]:
    """Rule-based reader for free Korean text. Returns (courses, assumptions)."""
    day = _as_date(today)
    known = [n for n in (known_teachers or []) if n]
    assumptions: list[str] = []
    courses: list[ParsedCourse] = []
    homework_lines: dict[int, list[str]] = {}

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^(숙제|과제)(?![가-힣])", line):
            if courses:
                homework_lines.setdefault(len(courses) - 1, []).append(line)
            continue

        weekdays = _weekdays_in(line)
        span = _parse_time_range(line)
        if not weekdays and span is None and _parse_date(line, year=day.year) is None:
            continue

        course_assumptions: list[str] = []
        name = _split_course_name(line) or f"코스 {len(courses) + 1}"
        teacher = _find_teacher(line, known)
        if span is None:
            span = (time(19, 0), time(21, 0))
            course_assumptions.append(f"「{name}」 시간이 없어 19:00-21:00으로 가정")
        else:
            shorthand = re.search(r"\d{1,2}\s*[-~–—]\s*\d{1,2}\s*시", line)
            if shorthand and span[0].hour >= 12:
                course_assumptions.append(
                    f"「{name}」 '{shorthand.group(0)}'를 오후 수업으로 해석"
                )

        dates = _lesson_dates(line, weekdays, day, course_assumptions)
        if not dates:
            continue
        lessons = [
            ParsedLesson(
                title=f"{name} {i + 1}회차",
                start_at=_kst(d, span[0]),
                end_at=_kst(d, span[1]),
                confidence=0.6,
            )
            for i, d in enumerate(dates)
        ]
        courses.append(
            ParsedCourse(
                course_name=name, teacher_name=teacher, lessons=lessons, confidence=0.6
            )
        )
        assumptions.extend(course_assumptions)

    for index, lines in homework_lines.items():
        course = courses[index]
        for line in lines:
            for lesson in course.lessons:
                lesson.homework = _homework_from(line, lesson.end_at)

    if courses:
        assumptions.append("AI 없이 규칙 기반으로 해석했습니다 — 날짜/시간을 꼭 확인하세요.")
    return courses, assumptions


def _lesson_dates(
    line: str, weekdays: list[int], today: date, assumptions: list[str]
) -> list[date]:
    explicit = _parse_date(line, year=today.year)
    if not weekdays:
        return [explicit] if explicit else []

    monday = _anchor_monday(line, today, assumptions)
    week_hit = _WEEK_COUNT.search(line)
    session_hit = _SESSION_COUNT.search(line)
    if week_hit:
        weeks = int(week_hit.group(1))
    elif session_hit:
        weeks = -(-int(session_hit.group(1)) // len(weekdays))
    else:
        weeks = 4
        assumptions.append("기간이 없어 4주로 가정")

    dates = [
        monday + timedelta(weeks=week, days=weekday)
        for week in range(weeks)
        for weekday in weekdays
    ]
    if session_hit and not week_hit:
        dates = dates[: int(session_hit.group(1))]
    return dates


# -- AI path ------------------------------------------------------------------


def _coerce_ai_payload(data: Any) -> tuple[list[ParsedCourse], list[str]]:
    if isinstance(data, dict):
        raw_courses = data.get("courses") or []
        assumptions = [str(a) for a in (data.get("assumptions") or [])]
    elif isinstance(data, list):
        raw_courses, assumptions = data, []
    else:
        raise ValueError(f"expected a JSON object or array, got {type(data).__name__}")
    return [ParsedCourse.model_validate(c) for c in raw_courses], assumptions


def parse_schedule(settings: Settings, raw_text: str) -> list[ParsedCourse]:
    """Claude-only parse (kept for callers that just want the courses)."""
    data = run_json(
        settings,
        system=SCHEDULE_PARSE_PROMPT,
        user=f"## 입력 스케줄\n\n{raw_text.strip()}",
    )
    courses, _ = _coerce_ai_payload(data)
    return courses


def _parse_with_ai(
    settings: Settings, raw_text: str, today: date
) -> tuple[list[ParsedCourse], list[str]]:
    data = run_json(
        settings,
        system=SCHEDULE_PARSE_PROMPT,
        user=f"## 오늘 날짜\n{today.isoformat()} (KST)\n\n## 입력 스케줄\n\n{raw_text.strip()}",
    )
    return _coerce_ai_payload(data)


# -- entity resolution --------------------------------------------------------


def _store_teachers(store: Any) -> list[dict[str, Any]]:
    if store is None:
        return []
    return [
        {"uid": int(row["uid"]), "name": row.get("name") or str(row["uid"])}
        for row in store.teachers()
        if row.get("uid") is not None
    ]


def _teacher_scores(name: str, teachers: list[dict[str, Any]]) -> list[tuple[float, dict]]:
    target = normalize_teacher_name(name)
    scored: list[tuple[float, dict]] = []
    for teacher in teachers:
        candidate = normalize_teacher_name(teacher["name"])
        if not candidate or not target:
            continue
        if candidate == target:
            score = 1.0
        elif candidate.startswith(target) or target.startswith(candidate):
            score = 0.9
        else:
            ratio = SequenceMatcher(None, candidate, target).ratio()
            score = ratio if ratio >= 0.75 else 0.0
        if score:
            scored.append((score, teacher))
    return scored


def _resolve_teacher(
    course: PlannedCourse,
    teachers: list[dict[str, Any]],
    plan: ParsePlan,
    *,
    course_teacher_uid: int | None = None,
) -> None:
    name = (course.teacher_name or "").strip()
    if not name:
        if course_teacher_uid:
            course.teacher_uid = course_teacher_uid
            plan.assumptions.append(
                f"「{course.course_name}」 선생님이 없어 기존 코스의 담당 선생님"
                f"(UID {course_teacher_uid})에게 배정"
            )
            return
        if len(teachers) == 1:
            course.teacher_uid = teachers[0]["uid"]
            course.teacher_name = teachers[0]["name"]
            plan.assumptions.append(
                f"「{course.course_name}」 선생님이 없어 등록된 유일한 선생님"
                f" {teachers[0]['name']}(UID {teachers[0]['uid']})에게 배정"
            )
            return
        course.teacher_candidates = list(teachers)
        plan.needs_confirm.append(
            Question(
                kind="teacher",
                subject=course.course_name,
                candidates=list(teachers),
                message=f"「{course.course_name}」의 선생님을 지정하세요.",
            )
        )
        return

    scored = _teacher_scores(name, teachers)
    if not scored:
        course.teacher_candidates = list(teachers)
        plan.needs_confirm.append(
            Question(
                kind="teacher",
                subject=name,
                candidates=list(teachers),
                message=f"「{name}」과(와) 일치하는 선생님이 없습니다 — 선택하거나 UID를 입력하세요.",
            )
        )
        return

    best = max(score for score, _ in scored)
    winners = [teacher for score, teacher in scored if score >= best - 1e-9]
    course.teacher_candidates = winners
    if len(winners) == 1:
        course.teacher_uid = winners[0]["uid"]
        if best < 1.0:
            plan.assumptions.append(
                f"선생님 「{name}」 → {winners[0]['name']}(UID {winners[0]['uid']})로 추정 매칭"
            )
        return
    plan.needs_confirm.append(
        Question(
            kind="teacher",
            subject=name,
            candidates=winners,
            message=f"「{name}」에 해당하는 선생님 후보가 {len(winners)}명입니다 — 선택하세요.",
        )
    )


def _resolve_course(
    course: PlannedCourse, rows: list[dict[str, Any]], plan: ParsePlan
) -> int | None:
    """Point the course at an existing one when the names match; return its teacher uid."""
    target = normalize_course_name(course.course_name)
    if not target:
        return None
    best_row, best_score = None, 0.0
    for row in rows:
        candidate = normalize_course_name(row.get("name"))
        if not candidate:
            continue
        ratio = 1.0 if candidate == target else SequenceMatcher(None, candidate, target).ratio()
        if ratio > best_score:
            best_row, best_score = row, ratio
    if best_row is None or best_score < 0.85:
        return None
    course.existing_course_id = int(best_row["course_id"])
    plan.assumptions.append(
        f"「{course.course_name}」은 기존 코스 「{best_row.get('name')}」"
        f"(ID {best_row['course_id']})로 판단해 재사용합니다."
    )
    return int(best_row["teacher_uid"]) if best_row.get("teacher_uid") else None


def resolve_entities(plan: ParsePlan, store: Any) -> ParsePlan:
    """Fill teacher_uid / existing_course_id in place; append questions we can't answer."""
    teachers = _store_teachers(store)
    course_rows = store.courses() if store is not None else []
    for course in plan.courses:
        course_teacher_uid = _resolve_course(course, course_rows, plan)
        _resolve_teacher(course, teachers, plan, course_teacher_uid=course_teacher_uid)
    return plan


# -- pipeline -----------------------------------------------------------------


def smart_parse(
    settings: Settings,
    store: Any,
    raw_text: str,
    *,
    today: date | datetime | None = None,
) -> ParsePlan:
    """Parse anything a teacher pasted and resolve it against known ClassIn entities."""
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("입력이 비어 있습니다.")
    day = _as_date(today)

    if detect_format(text) == "csv":
        courses, assumptions = parse_table(text, today=day)
        source: str = "csv"
    else:
        courses, assumptions, source = _free_text_courses(settings, store, text, day)

    if not courses:
        raise ValueError("스케줄을 인식하지 못했습니다 — 코스명, 요일, 시간을 포함해 다시 입력해 주세요.")

    plan = ParsePlan(
        courses=[PlannedCourse.model_validate(c.model_dump()) for c in courses],
        assumptions=assumptions,
        source_format=source,  # type: ignore[arg-type]
    )
    return resolve_entities(plan, store)


def _free_text_courses(
    settings: Settings, store: Any, text: str, day: date
) -> tuple[list[ParsedCourse], list[str], str]:
    if getattr(settings, "anthropic_api_key", ""):
        try:
            courses, assumptions = _parse_with_ai(settings, text, day)
            if courses:
                return courses, assumptions, "text-ai"
        except Exception as exc:  # fall through to the deterministic reader
            log.warning("AI schedule parse failed, falling back to heuristics: %s", exc)
    known = [t["name"] for t in _store_teachers(store)]
    courses, assumptions = heuristic_parse(text, known, today=day)
    return courses, assumptions, "text-heuristic"
