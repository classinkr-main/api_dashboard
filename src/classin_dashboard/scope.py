"""View scope derived from the logged-in session (ADR-0005).

A `Scope` says *which slice of the school* a request may read. It is threaded
through every page-facing store/metrics call as a **required keyword argument**
so that forgetting to filter raises `TypeError` at call time instead of
silently leaking another branch's data.

- `Scope.ALL` (both fields None) = unrestricted (owner without a branch filter).
- `branch_ids` set → the courses of those branches + the courses their teachers
  taught. Unassigned courses (`branch_id IS NULL`) are visible to `Scope.ALL` only.
- `teacher_uids` set → that teacher's own courses and lesson records.
- An empty frozenset means "nothing matches" (e.g. a manager with no branch).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

OWNER, MANAGER, TEACHER = "owner", "manager", "teacher"
ROLES = (OWNER, MANAGER, TEACHER)


@dataclass(frozen=True)
class Scope:
    branch_ids: frozenset[int] | None = None
    teacher_uids: frozenset[int] | None = None

    ALL: ClassVar[Scope]

    @property
    def unrestricted(self) -> bool:
        return self.branch_ids is None and self.teacher_uids is None

    @classmethod
    def for_branch(cls, branch_id: int | None) -> Scope:
        """Branch scope; a missing branch id yields a scope that matches nothing."""
        return cls(branch_ids=frozenset({branch_id} if branch_id is not None else ()))

    @classmethod
    def for_teacher(cls, teacher_uid: int | None) -> Scope:
        return cls(teacher_uids=frozenset({teacher_uid} if teacher_uid is not None else ()))


Scope.ALL = Scope(None, None)


def scope_for(session: Any, store: Any = None) -> Scope:
    """Derive the scope of a session.

    owner   → ALL, or the branch picked in the topbar switch (`branch_filter`)
    manager → own branch
    teacher → own teacher uid
    """
    role = getattr(session, "role", None)
    if role == OWNER:
        branch_id = getattr(session, "branch_filter", None)
        if branch_id is None:
            return Scope.ALL
        if store is not None and not _branch_exists(store, branch_id):
            return Scope.ALL
        return Scope.for_branch(branch_id)
    if role == MANAGER:
        return Scope.for_branch(getattr(session, "branch_id", None))
    if role == TEACHER:
        return Scope.for_teacher(getattr(session, "teacher_uid", None))
    return Scope.for_branch(None)  # unknown role: see nothing


def _branch_exists(store: Any, branch_id: int) -> bool:
    return any(b["id"] == branch_id for b in store.branches())
