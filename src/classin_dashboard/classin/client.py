"""ClassIn (EEO) partner API client.

Two API generations coexist:
- v1 (legacy CED): POST {base}/partner/api/course.api.php?action=<ACTION>,
  form-encoded body carrying SID/safeKey/timeStamp; success = error_info.errno == 1.
- v2 (LMS): POST {base}/lms/<path>, JSON body, v2 MD5 signature headers
  (X-EEO-UID / X-EEO-TS / X-EEO-SIGN); success = top-level code == 1.

Bulk v1 actions can return top-level success with per-item errno inside data[]
— callers must inspect item results themselves.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .signing import sign_v1_safekey, sign_v2

ENTRYPOINT = "/partner/api/course.api.php"

# Error codes accepted as idempotent success in specific calls:
#   133 = already a school student/teacher, 135/461 = phone/email already
#   registered (UID still returned in data).
ALREADY_MEMBER = (1, 133)
ALREADY_REGISTERED = (1, 135, 461)


class ClassInError(RuntimeError):
    """errno == -1 marks transport-level failures (HTTP >= 400, non-JSON)."""

    def __init__(self, action: str, errno: int, message: str, payload: Any = None):
        super().__init__(f"ClassIn [{action}] errno={errno} {message}")
        self.action = action
        self.errno = errno
        self.message = message
        self.payload = payload


def _encode_v1_form(body: dict[str, Any]) -> dict[str, str]:
    encoded: dict[str, str] = {}
    for key, value in body.items():
        if value is None:
            continue
        if isinstance(value, (list, dict)):
            encoded[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        elif isinstance(value, bool):
            encoded[key] = "1" if value else "0"
        else:
            encoded[key] = str(value)
    return encoded


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


class ClassInClient:
    def __init__(
        self,
        *,
        base_url: str,
        sid: str,
        secret: str,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.sid = sid
        self.secret = secret
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ClassInClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _envelope(self, resp: httpx.Response, action: str) -> dict[str, Any]:
        if resp.status_code >= 400:
            raise ClassInError(action, -1, f"HTTP {resp.status_code}: {resp.text[:400]}")
        try:
            envelope = resp.json()
        except json.JSONDecodeError:
            raise ClassInError(action, -1, f"non-JSON response: {resp.text[:400]}") from None
        if not isinstance(envelope, dict):
            raise ClassInError(action, -1, f"unexpected response shape: {envelope!r}")
        return envelope

    def call_v1(
        self,
        action: str,
        body: dict[str, Any] | None = None,
        *,
        success_codes: tuple[int, ...] = (1,),
        ts: int | None = None,
    ) -> Any:
        safe_key, timestamp = sign_v1_safekey(self.secret, ts=ts)
        form_body = _encode_v1_form(
            {"SID": self.sid, "safeKey": safe_key, "timeStamp": timestamp, **(body or {})}
        )
        resp = self._http.post(
            ENTRYPOINT,
            params={"action": action},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=form_body,
        )
        envelope = self._envelope(resp, action)
        err = envelope.get("error_info") or {}
        errno = _int_value(err.get("errno", -1))
        if errno not in success_codes:
            raise ClassInError(action, errno, str(err.get("error", "unknown")), payload=envelope)
        return envelope.get("data")

    def call_v2(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        success_codes: tuple[int, ...] = (1,),
        ts: int | None = None,
    ) -> Any:
        body = dict(body or {})
        headers, _ = sign_v2(body, sid=self.sid, secret=self.secret, ts=ts)
        normalized = path if path.startswith("/") else f"/{path}"
        resp = self._http.post(normalized, headers=headers, json=body)
        envelope = self._envelope(resp, normalized)
        code = _int_value(envelope.get("code", -1))
        if code not in success_codes:
            raise ClassInError(
                normalized,
                code,
                str(envelope.get("msg") or envelope.get("message") or "unknown"),
                payload=envelope,
            )
        return envelope.get("data")

    # -- credential probe (non-mutating) --------------------------------------

    def verify_credentials(self) -> tuple[bool, str]:
        """Prove SID/secret without creating anything.

        Sends deliberately invalid requests: a *parameter/validation* rejection
        means the signature was accepted (credentials OK); a *signature*
        rejection means bad credentials or clock skew (±5 min tolerance).
        """
        try:
            self.call_v1(
                "getLoginLinked",
                {
                    "courseId": 0,
                    "classId": 0,
                    "uid": "0",
                    "telephone": "0",
                    "deviceType": 1,
                    "lifeTime": 60,
                },
            )
            return True, "ok"
        except ClassInError as exc:
            text = f"{exc.errno} {exc.message}".lower()
            if exc.errno in (101002005,) or any(
                marker in text for marker in ("sign", "signature", "签名", "서명")
            ):
                return False, f"서명 오류 (SID/secret 확인, 서버 시계 ±5분): {exc.message}"
            if exc.errno == -1:
                return False, f"ClassIn API 연결 실패: {exc.message}"
            # Param-level rejection (e.g. errno 100): request was signed OK.
            return True, "ok"
