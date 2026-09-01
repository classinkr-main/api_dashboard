"""Event store: raw webhook JSONL + normalized SQLite tables.

See ADR-0003. Raw payloads are always preserved; normalization is best-effort
and can be replayed later when schemas grow.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    cmd TEXT NOT NULL,
    msg_id TEXT,                 -- ClassIn _id; dedupe key (at-least-once delivery)
    course_id INTEGER,
    class_id INTEGER,
    student_uid INTEGER,
    teacher_uid INTEGER,
    payload TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_msg ON events(msg_id) WHERE msg_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_events_cmd ON events(cmd);
CREATE INDEX IF NOT EXISTS idx_events_course ON events(course_id);
CREATE INDEX IF NOT EXISTS idx_events_student ON events(student_uid);
CREATE INDEX IF NOT EXISTS idx_events_teacher ON events(teacher_uid);

-- One row per (lesson, student), created by Attendance events and patched by
-- End/HomeworkSubmit/HomeworkScore. homework_submitted stays NULL (unknown)
-- until a homework activity is known for the lesson.
CREATE TABLE IF NOT EXISTS lesson_records (
    lesson_id TEXT NOT NULL,
    student_uid INTEGER NOT NULL,
    course_id INTEGER,
    course_name TEXT,
    class_name TEXT,
    lesson_date TEXT,
    class_start INTEGER,
    class_end INTEGER,
    attendance TEXT,             -- 출석 | 지각 | 결석
    attendance_seconds INTEGER,
    camera_minutes REAL,
    hand_raise REAL,
    trophy REAL,
    poll REAL,
    homework_submitted INTEGER,  -- NULL=unknown, 0=missing, 1=submitted
    homework_late INTEGER,
    homework_score REAL,
    homework_activity_id INTEGER,
    teacher_uid INTEGER,
    teacher_name TEXT,
    updated_at TEXT,
    PRIMARY KEY (lesson_id, student_uid)
);
CREATE INDEX IF NOT EXISTS idx_lr_course ON lesson_records(course_id);
CREATE INDEX IF NOT EXISTS idx_lr_student ON lesson_records(student_uid);
CREATE INDEX IF NOT EXISTS idx_lr_date ON lesson_records(lesson_date);

CREATE TABLE IF NOT EXISTS students (
    uid INTEGER PRIMARY KEY,
    name TEXT,
    class_name TEXT,
    parent_phone TEXT
);

-- Course/lesson master seeded by our own create calls (ClassIn returns IDs
-- that would otherwise be lost) and enriched from webhook headers.
CREATE TABLE IF NOT EXISTS courses (
    course_id INTEGER PRIMARY KEY,
    name TEXT,
    teacher_uid INTEGER,
    teacher_name TEXT,
    created_via TEXT,            -- api | webhook
    created_at TEXT,
    reminder_unit_id INTEGER     -- LMS unit reused for app-reminder discussions
);
CREATE TABLE IF NOT EXISTS lessons (
    lesson_id TEXT PRIMARY KEY,  -- ClassIn classId as text
    course_id INTEGER,
    title TEXT,
    start_time INTEGER,
    end_time INTEGER,
    teacher_uid INTEGER,
    homework_activity_id INTEGER,
    created_via TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_lessons_course ON lessons(course_id);

-- Teacher registry (no ClassIn API enumerates teachers).
CREATE TABLE IF NOT EXISTS teachers (
    uid INTEGER PRIMARY KEY,
    name TEXT
);

-- Notification history (dry-run and live), newest first by id.
CREATE TABLE IF NOT EXISTS notify_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,        -- dry_run | sent | failed
    student_uid INTEGER,
    student_name TEXT,
    message TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_nh_student ON notify_history(student_uid);
"""


class EventStore:
    def __init__(self, db_path: Path, raw_dir: Path) -> None:
        self.db_path = db_path
        self.raw_dir = raw_dir
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Additive column migrations for pre-existing databases."""
        try:
            conn.execute("ALTER TABLE courses ADD COLUMN reminder_unit_id INTEGER")
        except sqlite3.OperationalError:
            pass  # column already exists

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # -- ingest ---------------------------------------------------------------

    def append_raw(self, payload: dict[str, Any]) -> None:
        """Always-succeeds audit log of the raw webhook body."""
        day = time.strftime("%Y-%m-%d")
        line = json.dumps(
            {"received_at": _now(), "payload": payload}, ensure_ascii=False
        )
        with self._lock:
            with open(self.raw_dir / f"{day}.jsonl", "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def insert_event(
        self,
        cmd: str,
        payload: dict[str, Any],
        *,
        msg_id: str | None = None,
        course_id: int | None = None,
        class_id: int | None = None,
        student_uid: int | None = None,
        teacher_uid: int | None = None,
    ) -> bool:
        """Insert one event; returns False when msg_id was already seen."""
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO events (received_at, cmd, msg_id, course_id,"
                " class_id, student_uid, teacher_uid, payload) VALUES (?,?,?,?,?,?,?,?)",
                (
                    _now(),
                    cmd,
                    msg_id,
                    course_id,
                    class_id,
                    student_uid,
                    teacher_uid,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            return cur.rowcount > 0

    # -- queries --------------------------------------------------------------

    def events(
        self,
        cmd: str | None = None,
        *,
        course_id: int | None = None,
        class_id: int | None = None,
        student_uid: int | None = None,
        teacher_uid: int | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        for col, val in (
            ("cmd", cmd),
            ("course_id", course_id),
            ("class_id", class_id),
            ("student_uid", student_uid),
            ("teacher_uid", teacher_uid),
        ):
            if val is not None:
                clauses.append(f"{col} = ?")
                params.append(val)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM events {where} ORDER BY id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out

    # -- lesson records (Attendance creates, others patch) --------------------

    def upsert_lesson_record(self, lesson_id: str, student_uid: int, **fields: Any) -> None:
        cols = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{c}=excluded.{c}" for c in fields)
        with self._lock, self._conn() as conn:
            conn.execute(
                f"INSERT INTO lesson_records (lesson_id, student_uid, {cols}, updated_at)"
                f" VALUES (?, ?, {placeholders}, ?)"
                f" ON CONFLICT(lesson_id, student_uid) DO UPDATE SET {updates},"
                f" updated_at=excluded.updated_at",
                (lesson_id, student_uid, *fields.values(), _now()),
            )

    def patch_lesson_record(self, lesson_id: str, student_uid: int, **fields: Any) -> None:
        """Like upsert, but only overwrites the given fields (row may not exist yet)."""
        self.upsert_lesson_record(lesson_id, student_uid, **fields)

    def lesson_records(
        self,
        *,
        course_id: int | None = None,
        student_uid: int | None = None,
        teacher_uid: int | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if course_id is not None:
            clauses.append("course_id = ?")
            params.append(course_id)
        if student_uid is not None:
            clauses.append("student_uid = ?")
            params.append(student_uid)
        if teacher_uid is not None:
            clauses.append("teacher_uid = ?")
            params.append(teacher_uid)
        if since:
            clauses.append("lesson_date >= ?")
            params.append(since)
        if until:
            clauses.append("lesson_date < ?")
            params.append(until)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM lesson_records {where} ORDER BY lesson_date DESC", params
            ).fetchall()
        return [dict(r) for r in rows]

    # -- masters --------------------------------------------------------------

    def ensure_student(self, uid: int, name: str | None = None) -> None:
        """Create the student if unknown; only backfill name when empty."""
        with self._lock, self._conn() as conn:
            conn.execute("INSERT OR IGNORE INTO students (uid, name) VALUES (?, ?)", (uid, name))
            if name:
                conn.execute(
                    "UPDATE students SET name = ? WHERE uid = ? AND (name IS NULL OR name = '')",
                    (name, uid),
                )

    def students(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM students ORDER BY name, uid").fetchall()
            ]

    def upsert_course(
        self,
        course_id: int,
        *,
        name: str | None = None,
        teacher_uid: int | None = None,
        teacher_name: str | None = None,
        created_via: str = "api",
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO courses (course_id, name, teacher_uid, teacher_name,"
                " created_via, created_at) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(course_id) DO UPDATE SET"
                " name=COALESCE(excluded.name, name),"
                " teacher_uid=COALESCE(excluded.teacher_uid, teacher_uid),"
                " teacher_name=COALESCE(excluded.teacher_name, teacher_name)",
                (course_id, name, teacher_uid, teacher_name, created_via, _now()),
            )

    def courses(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM courses ORDER BY created_at DESC").fetchall()
            ]

    def set_course_reminder_unit(self, course_id: int, unit_id: int) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE courses SET reminder_unit_id = ? WHERE course_id = ?",
                (unit_id, course_id),
            )

    def course_reminder_unit(self, course_id: int) -> int | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT reminder_unit_id FROM courses WHERE course_id = ?", (course_id,)
            ).fetchone()
        return row["reminder_unit_id"] if row and row["reminder_unit_id"] else None

    def upsert_lesson(
        self,
        lesson_id: str,
        *,
        course_id: int | None = None,
        title: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        teacher_uid: int | None = None,
        homework_activity_id: int | None = None,
        created_via: str = "api",
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO lessons (lesson_id, course_id, title, start_time, end_time,"
                " teacher_uid, homework_activity_id, created_via, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(lesson_id) DO UPDATE SET"
                " course_id=COALESCE(excluded.course_id, course_id),"
                " title=COALESCE(excluded.title, title),"
                " start_time=COALESCE(excluded.start_time, start_time),"
                " end_time=COALESCE(excluded.end_time, end_time),"
                " teacher_uid=COALESCE(excluded.teacher_uid, teacher_uid),"
                " homework_activity_id=COALESCE(excluded.homework_activity_id,"
                "   homework_activity_id)",
                (
                    lesson_id,
                    course_id,
                    title,
                    start_time,
                    end_time,
                    teacher_uid,
                    homework_activity_id,
                    created_via,
                    _now(),
                ),
            )

    def lessons(self, *, course_id: int | None = None) -> list[dict[str, Any]]:
        where, params = "", []
        if course_id is not None:
            where, params = "WHERE course_id = ?", [course_id]
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    f"SELECT * FROM lessons {where} ORDER BY start_time DESC", params
                ).fetchall()
            ]

    def upsert_teacher(self, uid: int, name: str | None = None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO teachers (uid, name) VALUES (?, ?)"
                " ON CONFLICT(uid) DO UPDATE SET name=COALESCE(excluded.name, name)",
                (uid, name),
            )

    def teachers(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM teachers ORDER BY name, uid").fetchall()
            ]

    # -- notifications --------------------------------------------------------

    def append_notification(
        self,
        *,
        event_type: str,
        provider: str,
        status: str,
        student_uid: int | None,
        student_name: str | None,
        message: str,
        error: str | None = None,
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO notify_history (created_at, event_type, provider, status,"
                " student_uid, student_name, message, error) VALUES (?,?,?,?,?,?,?,?)",
                (_now(), event_type, provider, status, student_uid, student_name, message, error),
            )

    def notification_history(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM notify_history ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            ]

    def counts_by_cmd(self) -> dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT cmd, COUNT(*) AS n FROM events GROUP BY cmd"
            ).fetchall()
        return {r["cmd"]: r["n"] for r in rows}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
