"""Best-effort ClassIn read/list actions.

These actions (getCourseList, getCourseClass, getStudentList, ...) exist on
the v1 entrypoint but are gated per-SID and absent from the published docs —
ClassIn must enable them for the school. Every wrapper here degrades
gracefully: a permission error (errno 102) or unknown action returns None,
and the dashboard falls back to webhook-derived state.

Known live-observed params: getCourseList takes courseStatus + perpage.
Response shapes are handled defensively (dict-or-list).
"""

from __future__ import annotations

import logging
from typing import Any

from .client import ClassInClient, ClassInError

log = logging.getLogger(__name__)


class ClassInReads:
    def __init__(self, client: ClassInClient) -> None:
        self._c = client
        self.last_errors: list[str] = []

    def _try(self, action: str, body: dict[str, Any]) -> Any | None:
        try:
            return self._c.call_v1(action, body)
        except ClassInError as exc:
            log.info("read action %s unavailable: errno=%s %s", action, exc.errno, exc.message)
            self.last_errors.append(f"{action}: errno={exc.errno} {exc.message}")
            return None

    @staticmethod
    def _rows(data: Any, *keys: str) -> list[dict]:
        """Extract a list of dicts from an unknown envelope shape."""
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        if isinstance(data, dict):
            for key in keys:
                inner = data.get(key)
                if isinstance(inner, list):
                    return [r for r in inner if isinstance(r, dict)]
            # single-record dict
            return [data] if data else []
        return []

    def course_list(self, *, course_status: int | None = None, perpage: int = 100) -> list[dict] | None:
        body: dict[str, Any] = {"perpage": perpage, "page": 1}
        if course_status is not None:
            body["courseStatus"] = course_status
        data = self._try("getCourseList", body)
        if data is None:
            return None
        return self._rows(data, "courseList", "course_list", "list", "courses")

    def course_classes(self, course_id: int, *, perpage: int = 100) -> list[dict] | None:
        data = self._try(
            "getCourseClass", {"courseId": course_id, "perpage": perpage, "page": 1}
        )
        if data is None:
            return None
        return self._rows(data, "classList", "class_list", "list", "classes")

    def course_students(self, course_id: int, *, perpage: int = 100) -> list[dict] | None:
        data = self._try(
            "getCourseStudent", {"courseId": course_id, "perpage": perpage, "page": 1}
        )
        if data is None:
            return None
        return self._rows(data, "studentList", "student_list", "list", "students")

    def student_list(self, *, perpage: int = 100) -> list[dict] | None:
        data = self._try("getStudentList", {"perpage": perpage, "page": 1})
        if data is None:
            return None
        return self._rows(data, "studentList", "student_list", "list", "students")

    def teacher_list(self, *, perpage: int = 100) -> list[dict] | None:
        data = self._try("getTeacherList", {"perpage": perpage, "page": 1})
        if data is None:
            return None
        return self._rows(data, "teacherList", "teacher_list", "list", "teachers")


def _pick(row: dict, *keys: str) -> Any:
    for k in keys:
        if row.get(k) is not None:
            return row[k]
    return None


def sync_masters(reads: ClassInReads, store) -> dict[str, Any]:
    """Pull whatever read actions are enabled into the local master tables."""
    result = {"courses": 0, "lessons": 0, "students": 0, "teachers": 0, "errors": []}

    courses = reads.course_list()
    if courses is not None:
        for c in courses:
            cid = _pick(c, "courseId", "course_id", "id")
            if cid is None:
                continue
            store.upsert_course(
                int(cid),
                name=_pick(c, "courseName", "course_name", "name"),
                teacher_uid=_pick(c, "mainTeacherUid", "teacherUid"),
                created_via="api",
            )
            result["courses"] += 1
            classes = reads.course_classes(int(cid))
            for cl in classes or []:
                lid = _pick(cl, "classId", "class_id", "id")
                if lid is None:
                    continue
                store.upsert_lesson(
                    str(lid),
                    course_id=int(cid),
                    title=_pick(cl, "className", "class_name", "name"),
                    start_time=_pick(cl, "beginTime", "begin_time", "startTime"),
                    end_time=_pick(cl, "endTime", "end_time"),
                    teacher_uid=_pick(cl, "teacherUid", "teacher_uid"),
                    created_via="api",
                )
                result["lessons"] += 1

    students = reads.student_list()
    if students is not None:
        for s in students:
            uid = _pick(s, "studentUid", "student_uid", "uid", "id")
            if uid is None:
                continue
            store.ensure_student(int(uid), _pick(s, "studentName", "student_name", "name"))
            result["students"] += 1

    teachers = reads.teacher_list()
    if teachers is not None:
        for t in teachers:
            uid = _pick(t, "teacherUid", "teacher_uid", "uid", "id")
            if uid is None:
                continue
            store.upsert_teacher(int(uid), _pick(t, "teacherName", "teacher_name", "name"))
            result["teachers"] += 1

    result["errors"] = reads.last_errors
    return result
