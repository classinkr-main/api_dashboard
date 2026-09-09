"""Event store: raw webhook JSONL + normalized SQLite tables.

See ADR-0003. Raw payloads are always preserved; normalization is best-effort
and can be replayed later when schemas grow.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .scope import Scope

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
    created_at TEXT
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

-- 관(지점): a branch is a group of courses/teachers, optionally backed by its
-- own ClassIn account (sid/secret override) — see ADR-0005.
CREATE TABLE IF NOT EXISTS branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    sid TEXT,
    secret TEXT,
    created_at TEXT
);

-- Local accounts. ClassIn credentials never live here: only the role and the
-- branch/teacher this account is scoped to.
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,          -- owner | manager | teacher
    branch_id INTEGER,
    teacher_uid INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT
);
"""

# Columns added after the first release; applied by _migrate() on open.
_ADDED_COLUMNS = (
    ("courses", "branch_id", "INTEGER"),
    ("teachers", "branch_id", "INTEGER"),
)

PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """`pbkdf2$iters$salt_hex$hash_hex` (PBKDF2-HMAC-SHA256, random 16-byte salt)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(stored: str | None, password: str) -> bool:
    """Constant-time check of a password against a stored hash."""
    if not stored:
        return False
    try:
        scheme, iters, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iters)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


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
        """Add post-release columns to existing databases (idempotent)."""
        for table, column, ddl_type in _ADDED_COLUMNS:
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")

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

    # -- scope ----------------------------------------------------------------

    def course_ids_in_scope(self, scope: Scope) -> set[int] | None:
        """Course ids the scope may read; None means unrestricted.

        Branch scope = courses assigned to the branch **plus** courses taught by
        that branch's teachers. Unassigned courses (branch_id NULL) therefore
        stay invisible to everyone but Scope.ALL.
        """
        if scope.unrestricted:
            return None
        courses = self._all_courses()
        sets: list[set[int]] = []
        if scope.branch_ids is not None:
            teachers = self.teacher_uids_in_scope(Scope(branch_ids=scope.branch_ids)) or set()
            ids = {c["course_id"] for c in courses if c.get("branch_id") in scope.branch_ids}
            ids |= {c["course_id"] for c in courses if c.get("teacher_uid") in teachers}
            ids |= self._course_ids_taught_by(teachers)
            sets.append(ids)
        if scope.teacher_uids is not None:
            uids = set(scope.teacher_uids)
            ids = {c["course_id"] for c in courses if c.get("teacher_uid") in uids}
            ids |= self._course_ids_taught_by(uids)
            sets.append(ids)
        return set.intersection(*sets) if len(sets) > 1 else sets[0]

    def teacher_uids_in_scope(self, scope: Scope) -> set[int] | None:
        if scope.unrestricted:
            return None
        sets: list[set[int]] = []
        if scope.branch_ids is not None:
            sets.append(
                {
                    t["uid"]
                    for t in self._all_teachers()
                    if t.get("branch_id") in scope.branch_ids
                }
            )
        if scope.teacher_uids is not None:
            sets.append(set(scope.teacher_uids))
        return set.intersection(*sets) if len(sets) > 1 else sets[0]

    def student_uids_in_scope(self, scope: Scope) -> set[int] | None:
        if scope.unrestricted:
            return None
        return {r["student_uid"] for r in self.lesson_records(scope=scope)}

    def _course_ids_taught_by(self, teacher_uids: set[int]) -> set[int]:
        if not teacher_uids:
            return set()
        marks = ",".join("?" for _ in teacher_uids)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT course_id FROM lesson_records"
                f" WHERE teacher_uid IN ({marks}) AND course_id IS NOT NULL",
                tuple(teacher_uids),
            ).fetchall()
        return {r["course_id"] for r in rows}

    def _scope_keys(self, scope: Scope) -> tuple[set[int], set[int]] | None:
        """(course_ids, teacher_uids) a row may match; None when unrestricted."""
        if scope.unrestricted:
            return None
        return self.course_ids_in_scope(scope) or set(), self.teacher_uids_in_scope(scope) or set()

    # -- queries --------------------------------------------------------------

    def events(
        self,
        cmd: str | None = None,
        *,
        scope: Scope,
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
        allowed = self._scope_keys(scope)
        out = []
        for r in rows:
            d = dict(r)
            if not _row_in_scope(d, allowed):
                continue
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
        scope: Scope,
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
        allowed = self._scope_keys(scope)
        return [dict(r) for r in rows if _row_in_scope(dict(r), allowed)]

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

    def _all_students(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM students ORDER BY name, uid").fetchall()
            ]

    def students(self, *, scope: Scope) -> list[dict[str, Any]]:
        """Students that appear in in-scope lesson records (all of them for Scope.ALL)."""
        uids = self.student_uids_in_scope(scope)
        rows = self._all_students()
        return rows if uids is None else [r for r in rows if r["uid"] in uids]

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

    def _all_courses(self) -> list[dict[str, Any]]:
        """Unscoped read for admin screens and the webhook/sync paths."""
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM courses ORDER BY created_at DESC").fetchall()
            ]

    def courses(self, *, scope: Scope) -> list[dict[str, Any]]:
        ids = self.course_ids_in_scope(scope)
        rows = self._all_courses()
        return rows if ids is None else [r for r in rows if r["course_id"] in ids]

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

    def lessons(self, *, scope: Scope, course_id: int | None = None) -> list[dict[str, Any]]:
        where, params = "", []
        if course_id is not None:
            where, params = "WHERE course_id = ?", [course_id]
        with self._conn() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    f"SELECT * FROM lessons {where} ORDER BY start_time DESC", params
                ).fetchall()
            ]
        allowed = self._scope_keys(scope)
        return [r for r in rows if _row_in_scope(r, allowed)]

    def upsert_teacher(self, uid: int, name: str | None = None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO teachers (uid, name) VALUES (?, ?)"
                " ON CONFLICT(uid) DO UPDATE SET name=COALESCE(excluded.name, name)",
                (uid, name),
            )

    def _all_teachers(self) -> list[dict[str, Any]]:
        """Unscoped read for admin screens (관 배정 표)."""
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM teachers ORDER BY name, uid").fetchall()
            ]

    def get_teacher(self, uid: int | None) -> dict[str, Any] | None:
        if uid is None:
            return None
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM teachers WHERE uid = ?", (uid,)).fetchone()
        return dict(row) if row else None

    def teachers(self, *, scope: Scope) -> list[dict[str, Any]]:
        uids = self.teacher_uids_in_scope(scope)
        rows = self._all_teachers()
        return rows if uids is None else [r for r in rows if r["uid"] in uids]

    # -- branches & users (ADR-0005) ------------------------------------------

    def branches(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM branches ORDER BY name").fetchall()
            ]

    def get_branch(self, branch_id: int | None) -> dict[str, Any] | None:
        if branch_id is None:
            return None
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM branches WHERE id = ?", (branch_id,)).fetchone()
        return dict(row) if row else None

    def upsert_branch(
        self,
        name: str,
        *,
        branch_id: int | None = None,
        sid: str | None = None,
        secret: str | None = None,
    ) -> int:
        """Create a branch (or rename/re-credential an existing one). Returns its id."""
        with self._lock, self._conn() as conn:
            if branch_id is not None:
                conn.execute(
                    "UPDATE branches SET name = ?, sid = ?, secret = ? WHERE id = ?",
                    (name, sid, secret, branch_id),
                )
                return branch_id
            cur = conn.execute(
                "INSERT INTO branches (name, sid, secret, created_at) VALUES (?,?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET"
                " sid=COALESCE(excluded.sid, sid), secret=COALESCE(excluded.secret, secret)",
                (name, sid or None, secret or None, _now()),
            )
            if cur.lastrowid:
                return int(cur.lastrowid)
            row = conn.execute("SELECT id FROM branches WHERE name = ?", (name,)).fetchone()
            return int(row["id"])

    def delete_branch(self, branch_id: int) -> bool:
        """Delete a branch only when nothing is assigned to it."""
        with self._lock, self._conn() as conn:
            for table in ("courses", "teachers", "users"):
                used = conn.execute(
                    f"SELECT 1 FROM {table} WHERE branch_id = ? LIMIT 1", (branch_id,)
                ).fetchone()
                if used:
                    return False
            conn.execute("DELETE FROM branches WHERE id = ?", (branch_id,))
        return True

    def set_course_branch(self, course_id: int, branch_id: int | None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE courses SET branch_id = ? WHERE course_id = ?", (branch_id, course_id)
            )

    def set_teacher_branch(self, uid: int, branch_id: int | None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("UPDATE teachers SET branch_id = ? WHERE uid = ?", (branch_id, uid))

    def users(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM users ORDER BY role, username"
                ).fetchall()
            ]

    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        *,
        branch_id: int | None = None,
        teacher_uid: int | None = None,
    ) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, role, branch_id, teacher_uid,"
                " active, created_at) VALUES (?,?,?,?,?,1,?)",
                (username, hash_password(password), role, branch_id, teacher_uid, _now()),
            )
            return int(cur.lastrowid)

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    def set_user_active(self, user_id: int, active: bool) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, user_id)
            )

    def set_user_password(self, user_id: int, password: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(password), user_id),
            )

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

    def notification_history(self, *, scope: Scope, limit: int = 100) -> list[dict[str, Any]]:
        uids = self.student_uids_in_scope(scope)
        with self._conn() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM notify_history ORDER BY id DESC LIMIT ?",
                    (limit if uids is None else max(limit * 10, 1000),),
                ).fetchall()
            ]
        if uids is not None:
            rows = [r for r in rows if r["student_uid"] in uids][:limit]
        return rows

    def counts_by_cmd(self, *, scope: Scope) -> dict[str, int]:
        if scope.unrestricted:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT cmd, COUNT(*) AS n FROM events GROUP BY cmd"
                ).fetchall()
            return {r["cmd"]: r["n"] for r in rows}
        allowed = self._scope_keys(scope)
        counts: dict[str, int] = {}
        with self._conn() as conn:
            rows = conn.execute("SELECT cmd, course_id, teacher_uid FROM events").fetchall()
        for r in rows:
            if _row_in_scope(dict(r), allowed):
                counts[r["cmd"]] = counts.get(r["cmd"], 0) + 1
        return counts


def _row_in_scope(row: dict[str, Any], allowed: tuple[set[int], set[int]] | None) -> bool:
    """A row is in scope when its course or its teacher is."""
    if allowed is None:
        return True
    course_ids, teacher_uids = allowed
    return row.get("course_id") in course_ids or row.get("teacher_uid") in teacher_uids


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
