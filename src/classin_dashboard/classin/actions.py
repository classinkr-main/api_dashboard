"""High-level ClassIn actions used by the dashboard.

Write path (course/lesson creation, member registration) plus the SSO link
generator. Read/list actions are added in reads.py as they are confirmed
against the official docs — the reference toolkit had none.
"""

from __future__ import annotations

from typing import Any

from .client import ALREADY_MEMBER, ALREADY_REGISTERED, ClassInClient

# LMS activityType values: 2 = homework(숙제), 3 = test, 6 = discussion(토론),
# 7 = answer sheet(OMR)
ACTIVITY_HOMEWORK = 2
ACTIVITY_TEST = 3
ACTIVITY_DISCUSSION = 6
ACTIVITY_ANSWER_SHEET = 7


class ClassInActions:
    def __init__(self, client: ClassInClient) -> None:
        self._c = client

    # -- users ----------------------------------------------------------------

    def register_user(
        self,
        *,
        telephone: str | None = None,
        email: str | None = None,
        password: str,
        nickname: str,
    ) -> int:
        """Register (idempotent: already-registered still yields the UID)."""
        body: dict[str, Any] = {
            "password": password,
            "nickname": nickname,
            "addToSchoolMember": 1,
        }
        if telephone:
            body["telephone"] = telephone
        if email:
            body["email"] = email
        data = self._c.call_v1("register", body, success_codes=ALREADY_REGISTERED)
        return int(data)

    def add_school_student(self, *, student_uid: int, student_name: str) -> None:
        self._c.call_v1(
            "addSchoolStudent",
            {"studentUid": student_uid, "studentName": student_name},
            success_codes=ALREADY_MEMBER,
        )

    def add_teacher(self, *, teacher_uid: int, teacher_name: str) -> None:
        self._c.call_v1(
            "addTeacher",
            {"teacherUid": teacher_uid, "teacherName": teacher_name},
            success_codes=ALREADY_MEMBER,
        )

    # -- courses & lessons ----------------------------------------------------

    def add_course(
        self,
        *,
        course_name: str,
        main_teacher_uid: int | None = None,
        unique_identity: str | None = None,
    ) -> int:
        """courseUniqueIdentity makes retries idempotent: an existing identity
        returns the existing courseId instead of creating a duplicate."""
        body: dict[str, Any] = {"courseName": course_name}
        if main_teacher_uid:
            body["mainTeacherUid"] = main_teacher_uid
        if unique_identity:
            body["courseUniqueIdentity"] = unique_identity[:32]
        data = self._c.call_v1("addCourse", body)
        if isinstance(data, dict):
            return int(data.get("courseId") or data.get("course_id"))
        return int(data)

    def create_unit(self, *, course_id: int, name: str, content: str = "", publish: int = 2) -> int:
        data = self._c.call_v2(
            "/lms/unit/create",
            {"courseId": course_id, "name": name, "content": content, "publishFlag": publish},
        )
        return int(data["unitId"])

    def create_classroom(
        self,
        *,
        course_id: int,
        name: str,
        teacher_uid: int,
        start_time: int,
        end_time: int,
        unit_id: int | None = None,
        unique_identity: str | None = None,
    ) -> dict[str, Any]:
        """LMS createClass — replaces legacy addCourseClass (deprecated 2025-05).

        Rate limit: ≤1000 req/min. name ≤50 chars.
        """
        body: dict[str, Any] = {
            "courseId": course_id,
            "name": name[:50],
            "teacherUid": teacher_uid,
            "startTime": start_time,
            "endTime": end_time,
        }
        if unit_id:
            body["unitId"] = unit_id
        if unique_identity:
            body["uniqueIdentity"] = unique_identity[:32]
        return self._c.call_v2("/lms/activity/createClass", body)

    def create_activity(
        self,
        *,
        course_id: int,
        unit_id: int,
        activity_type: int,
        name: str,
        teacher_uid: int,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> int:
        body: dict[str, Any] = {
            "courseId": course_id,
            "unitId": unit_id,
            "activityType": activity_type,
            "name": name,
            "teacherUid": teacher_uid,
        }
        if start_time:
            body["startTime"] = start_time
        if end_time:
            body["endTime"] = end_time
        data = self._c.call_v2("/lms/activity/createActivityNoClass", body)
        return int(data["activityId"])

    def release_activity(self, *, course_id: int, activity_id: int) -> Any:
        # Live API rejects arrays despite docs saying activityIds — one call each.
        return self._c.call_v2(
            "/lms/activity/release", {"courseId": course_id, "activityId": activity_id}
        )

    def add_course_students(self, *, course_id: int, student_uids: list[int]) -> Any:
        return self._c.call_v1(
            "addCourseStudentMultiple",
            {
                "courseId": course_id,
                "identity": 1,
                "studentJson": [{"uid": str(uid)} for uid in student_uids],
            },
        )

    def add_class_students(self, *, course_id: int, class_id: int, student_uids: list[int]) -> Any:
        return self._c.call_v1(
            "addClassStudentMultiple",
            {
                "courseId": course_id,
                "classId": class_id,
                "identity": 1,
                "studentJson": [{"uid": str(uid)} for uid in student_uids],
            },
        )

    def add_activity_students(
        self, *, course_id: int, activity_id: int, student_uids: list[int]
    ) -> Any:
        return self._c.call_v2(
            "/lms/activity/addStudent",
            {"courseId": course_id, "activityId": activity_id, "studentUids": student_uids},
        )

    # -- SSO ------------------------------------------------------------------

    def get_login_link(
        self,
        *,
        uid: int | str,
        course_id: int,
        class_id: int,
        telephone: str,
        device_type: int = 1,  # 1=PC, 2=iOS, 3=Android
        life_time: int = 86400,
    ) -> str:
        """Returns a ClassIn launch URL (classin:// on PC, https:// on mobile).

        Treat the returned URL as a credential — never log or display unmasked.
        """
        data = self._c.call_v1(
            "getLoginLinked",
            {
                "courseId": course_id,
                "classId": class_id,
                "uid": str(uid),
                "telephone": telephone,
                "deviceType": device_type,
                "lifeTime": life_time,
            },
        )
        return str(data)
