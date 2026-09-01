"""Per-student personalized notification copy (missing homework)."""

from __future__ import annotations

import json
from typing import Any

from ..config import Settings
from .claude import run_json

MISSING_HOMEWORK_PROMPT = """당신은 학원 알림 문구 작성 어시스턴트다.

## 목표
숙제를 제출하지 않은 학생별로, 학생마다 다른 자연스러운 알림 문구를 작성한다.
일괄 복붙 문구는 학부모가 즉시 알아챈다. 학생 이름·과목·특징을 반영해 개인화하라.

## 톤
- 학원 ↔ 학부모 관계를 해치지 않을 정도로 부드럽게.
- 반복 미제출이면 단계적으로 톤을 조정 (참고용, 별도 강조 금지).
- 이모지 1~2개까지 허용. 과하게 사용 금지.
- 120자 내외. 길어져도 180자 넘지 않기.

## 출력 스키마 (JSON 배열만)
[ { "student_uid": 10001, "message": "안녕하세요 ○○ 어머님, ..." } ]

아무 추가 설명도 출력하지 말고 JSON 배열만 반환한다."""

# Deterministic fallback templates (no AI key needed).
TONE_TEMPLATES = {
    "soft": (
        "안녕하세요 학부모님 😊\n[{student_name}] 학생이 {class_name} 숙제를 아직 "
        "제출하지 않았어요.\n마감 전 한 번만 확인 부탁드려요.\n\n- {academy_name}"
    ),
    "firm": (
        "[{student_name}] 학생 학부모님께 안내드립니다.\n{class_name} ({date}) 숙제 "
        "미제출이 확인되었습니다. 최근 {missing_count}회 누적되었으니 오늘 안에 제출 "
        "확인 부탁드립니다.\n\n- {academy_name}"
    ),
    "brief": (
        "[{student_name}] {class_name} 숙제 미제출. {date} 안에 제출 확인 부탁드립니다."
        "\n- {academy_name}"
    ),
}


def compose_ai_messages(
    settings: Settings, rows_by_student: dict[int, list[dict]], academy_name: str
) -> dict[int, str]:
    payload: list[dict[str, Any]] = []
    for uid, rows in rows_by_student.items():
        payload.append(
            {
                "student_uid": uid,
                "student_name": rows[0].get("student_name", ""),
                "class_name": rows[0].get("class_name"),
                "missing_lessons": [
                    {"date": (r.get("lesson_date") or "")[:10], "lesson_id": r.get("lesson_id")}
                    for r in rows
                ],
            }
        )
    data = run_json(
        settings,
        system=MISSING_HOMEWORK_PROMPT,
        user=(
            f"학원: {academy_name}\n미제출 학생 목록 (JSON):\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        ),
    )
    if not isinstance(data, list):
        raise ValueError("model must return a JSON array")
    out: dict[int, str] = {}
    for item in data:
        try:
            out[int(item["student_uid"])] = str(item.get("message", ""))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def compose_template_messages(
    rows_by_student: dict[int, list[dict]], academy_name: str, tone: str = "soft"
) -> dict[int, str]:
    template = TONE_TEMPLATES.get(tone, TONE_TEMPLATES["soft"])
    out: dict[int, str] = {}
    for uid, rows in rows_by_student.items():
        first = rows[0]
        out[uid] = template.format(
            student_name=first.get("student_name", ""),
            class_name=first.get("class_name") or first.get("course_name") or "수업",
            date=(first.get("lesson_date") or "")[:10],
            missing_count=len(rows),
            academy_name=academy_name,
        )
    return out
