"""Notification dispatch. dry_run records copy without sending.

Live channels plug in here (Layer 5 isolation): a failed send is logged per
message and never aborts the batch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..store import EventStore

log = logging.getLogger(__name__)


@dataclass
class OutgoingMessage:
    student_uid: int
    student_name: str
    message: str


def dispatch(
    store: EventStore,
    messages: list[OutgoingMessage],
    *,
    event_type: str = "missing_homework",
    dry_run: bool = True,
    sender=None,  # callable(OutgoingMessage) -> None, raises on failure
    provider: str = "dry_run",
) -> dict[str, int]:
    sent = failed = 0
    for msg in messages:
        if dry_run or sender is None:
            store.append_notification(
                event_type=event_type,
                provider="dry_run",
                status="dry_run",
                student_uid=msg.student_uid,
                student_name=msg.student_name,
                message=msg.message,
            )
            sent += 1
            continue
        try:
            sender(msg)
        except Exception as exc:
            log.exception("send failed uid=%s", msg.student_uid)
            store.append_notification(
                event_type=event_type,
                provider=provider,
                status="failed",
                student_uid=msg.student_uid,
                student_name=msg.student_name,
                message=msg.message,
                error=str(exc)[:500],
            )
            failed += 1
            continue
        store.append_notification(
            event_type=event_type,
            provider=provider,
            status="sent",
            student_uid=msg.student_uid,
            student_name=msg.student_name,
            message=msg.message,
        )
        sent += 1
    return {"sent": sent, "failed": failed}
