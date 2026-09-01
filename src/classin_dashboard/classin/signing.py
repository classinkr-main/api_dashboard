"""ClassIn API v1/v2 signing + webhook SafeKey verification.

Spec: https://docs.eeo.cn/api/en/appendix/signature.html

v1 (legacy form APIs): body carries `SID`, `safeKey`, `timeStamp` where
`safeKey = MD5(SECRET + timeStamp)` (lowercase hex).

v2 (JSON APIs): exclude list/dict values and strings > 1024 bytes from the
body, add `sid`/`timeStamp` (signing only, not sent in body), sort keys ASCII
ascending, join `k=v&...`, append `&key=SECRET`, MD5 lowercase hex. Headers:
`X-EEO-UID`, `X-EEO-TS` (epoch seconds, ±5 min of server), `X-EEO-SIGN`.

Webhook (Data Sub): payload carries `SafeKey = MD5(SECRET + TimeStamp)`.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

_MAX_VAL_BYTES = 1024


def sign_v1_safekey(secret: str, *, ts: int | None = None) -> tuple[str, int]:
    ts = ts or int(time.time())
    safe_key = hashlib.md5(f"{secret}{ts}".encode()).hexdigest()
    return safe_key, ts


def _should_include(value: Any) -> bool:
    if isinstance(value, (list, dict)):
        return False
    if isinstance(value, str) and len(value.encode()) > _MAX_VAL_BYTES:
        return False
    return True


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def build_signing_string(
    body: dict[str, Any], *, sid: str | int, timestamp: int, secret: str
) -> str:
    pairs = {k: _stringify(v) for k, v in body.items() if _should_include(v)}
    pairs["sid"] = str(sid)
    pairs["timeStamp"] = str(timestamp)
    joined = "&".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    return f"{joined}&key={secret}"


def sign_v2(
    body: dict[str, Any], *, sid: str | int, secret: str, ts: int | None = None
) -> tuple[dict[str, str], int]:
    """Return (headers, timestamp) for a v2-signed JSON request."""
    ts = ts or int(time.time())
    signing = build_signing_string(body, sid=sid, timestamp=ts, secret=secret)
    sig = hashlib.md5(signing.encode()).hexdigest()
    return (
        {
            "X-EEO-UID": str(sid),
            "X-EEO-TS": str(ts),
            "X-EEO-SIGN": sig,
            "Content-Type": "application/json",
        },
        ts,
    )


def verify_webhook_safekey(body: dict, secret: str) -> bool:
    sent = body.get("SafeKey") or body.get("safeKey")
    ts = body.get("TimeStamp") or body.get("timeStamp") or ""
    if not sent or not ts:
        return False
    expected = hashlib.md5(f"{secret}{ts}".encode()).hexdigest()
    return str(sent).lower() == expected
