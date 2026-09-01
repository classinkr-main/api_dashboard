"""ClassIn Data Sub receiver.

ClassIn's push queue is strict FIFO with head-of-line blocking: an
unacknowledged message is retried every 10s forever and blocks every later
event. So this endpoint ALWAYS returns the required success envelope
{"error_info": {"errno": 1, ...}} — raw payload is persisted first, parsing
failures are logged, never surfaced. Dedupe on the _id field handles the
resulting at-least-once delivery.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request

from ..classin.signing import verify_webhook_safekey
from ..ingest import ingest
from .app import AppState, get_state

log = logging.getLogger(__name__)
router = APIRouter()

ACK = {"error_info": {"errno": 1, "error": "程序正常执行"}}


@router.post("/webhook/classin", name="classin_webhook")
async def classin_webhook(request: Request, state: AppState = Depends(get_state)) -> dict:
    body = await request.body()
    try:
        raw = json.loads(body)
    except json.JSONDecodeError:
        log.warning("non-JSON webhook body (%d bytes)", len(body))
        return ACK

    state.events.append_raw(raw)

    safekey = state.settings.webhook_safekey
    if safekey and not verify_webhook_safekey(raw, safekey):
        # Ack anyway (blocking the queue would stall all events); the payload
        # is preserved in the raw log for investigation but not ingested.
        log.warning("SafeKey mismatch cmd=%s — stored raw, skipped ingest", raw.get("Cmd"))
        return ACK

    try:
        ingest(state.events, raw)
    except Exception:
        log.exception("ingest failed cmd=%s (raw preserved)", raw.get("Cmd"))
    return ACK
