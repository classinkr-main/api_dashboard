from classin_dashboard.classin.signing import (
    build_signing_string,
    sign_v1_safekey,
    sign_v2,
    verify_webhook_safekey,
)


def test_v1_safekey_is_md5_secret_ts():
    key, ts = sign_v1_safekey("XXXXXX", ts=1234567890)
    import hashlib

    assert ts == 1234567890
    assert key == hashlib.md5(b"XXXXXX1234567890").hexdigest()


def test_v2_signing_string_matches_docs_example():
    s = build_signing_string(
        {"courseName": "Math101", "teacherUid": 20001},
        sid=123456,
        timestamp=1234567890,
        secret="XXXXXX",
    )
    assert s == "courseName=Math101&sid=123456&teacherUid=20001&timeStamp=1234567890&key=XXXXXX"


def test_v2_excludes_nested_and_long_values():
    s = build_signing_string(
        {"a": [1, 2], "b": {"x": 1}, "c": "v" * 2000, "d": "keep"},
        sid=1,
        timestamp=2,
        secret="S",
    )
    assert s == "d=keep&sid=1&timeStamp=2&key=S"


def test_v2_headers():
    headers, ts = sign_v2({"x": 1}, sid=99, secret="S", ts=100)
    assert headers["X-EEO-UID"] == "99"
    assert headers["X-EEO-TS"] == "100"
    assert len(headers["X-EEO-SIGN"]) == 32


def test_webhook_safekey_roundtrip():
    import hashlib

    body = {"TimeStamp": 1700000000, "SafeKey": hashlib.md5(b"SEC1700000000").hexdigest()}
    assert verify_webhook_safekey(body, "SEC")
    assert not verify_webhook_safekey(body, "WRONG")
    assert not verify_webhook_safekey({"TimeStamp": 1}, "SEC")
