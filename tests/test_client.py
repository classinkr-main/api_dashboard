import httpx
import pytest

from classin_dashboard.classin.client import ClassInClient, ClassInError

BASE_URL = "https://api.example.test"


def make_client(handler: httpx.MockTransport) -> ClassInClient:
    return ClassInClient(base_url=BASE_URL, sid="SID1", secret="SECRET1", transport=handler)


# -- call_v1 -------------------------------------------------------------


def test_call_v1_success_returns_data():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/partner/api/course.api.php"
        assert request.url.params["action"] == "someAction"
        return httpx.Response(200, json={"data": 1, "error_info": {"errno": 1, "error": "ok"}})

    client = make_client(httpx.MockTransport(handler))
    result = client.call_v1("someAction", {"foo": "bar"})
    assert result == 1


def test_call_v1_error_errno_raises_classin_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": None, "error_info": {"errno": 42, "error": "bad request"}}
        )

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(ClassInError) as exc_info:
        client.call_v1("someAction")
    assert exc_info.value.errno == 42
    assert "bad request" in exc_info.value.message


def test_call_v1_http_error_status_raises_errno_minus_one():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(ClassInError) as exc_info:
        client.call_v1("someAction")
    assert exc_info.value.errno == -1


def test_call_v1_non_json_response_raises_errno_minus_one():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(ClassInError) as exc_info:
        client.call_v1("someAction")
    assert exc_info.value.errno == -1


def test_call_v1_sends_signed_form_body():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        captured["content_type"] = request.headers["content-type"]
        return httpx.Response(200, json={"data": None, "error_info": {"errno": 1, "error": "ok"}})

    client = make_client(httpx.MockTransport(handler))
    client.call_v1("someAction", {"foo": "bar"}, ts=1234567890)
    body = captured["body"].decode()
    assert "application/x-www-form-urlencoded" in captured["content_type"]
    assert "SID=SID1" in body
    assert "safeKey=" in body
    assert "timeStamp=1234567890" in body
    assert "foo=bar" in body


def test_call_v1_custom_success_codes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": "ok", "error_info": {"errno": 133, "error": "x"}})

    client = make_client(httpx.MockTransport(handler))
    result = client.call_v1("someAction", success_codes=(1, 133))
    assert result == "ok"


# -- call_v2 -------------------------------------------------------------


def test_call_v2_success_returns_data():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/lms/some/path"
        assert request.headers["X-EEO-UID"] == "SID1"
        return httpx.Response(200, json={"code": 1, "data": {"id": 7}})

    client = make_client(httpx.MockTransport(handler))
    result = client.call_v2("/lms/some/path", {"x": 1})
    assert result == {"id": 7}


def test_call_v2_normalizes_missing_leading_slash():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/lms/some/path"
        return httpx.Response(200, json={"code": 1, "data": None})

    client = make_client(httpx.MockTransport(handler))
    client.call_v2("lms/some/path")


def test_call_v2_code_not_one_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 2, "msg": "denied"})

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(ClassInError) as exc_info:
        client.call_v2("/lms/some/path")
    assert exc_info.value.errno == 2
    assert "denied" in exc_info.value.message


def test_call_v2_message_field_fallback():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "message": "nope"})

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(ClassInError) as exc_info:
        client.call_v2("/lms/x")
    assert "nope" in exc_info.value.message


# -- verify_credentials ----------------------------------------------------


def test_verify_credentials_param_rejection_is_success():
    # errno 100 = param-level rejection -> signature was accepted -> True
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": None, "error_info": {"errno": 100, "error": "bad param"}}
        )

    client = make_client(httpx.MockTransport(handler))
    ok, reason = client.verify_credentials()
    assert ok is True


def test_verify_credentials_signature_error_is_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": None, "error_info": {"errno": 101002005, "error": "sign error"}}
        )

    client = make_client(httpx.MockTransport(handler))
    ok, reason = client.verify_credentials()
    assert ok is False
    assert "서명" in reason


def test_verify_credentials_http_500_is_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = make_client(httpx.MockTransport(handler))
    ok, reason = client.verify_credentials()
    assert ok is False


def test_verify_credentials_success_errno_one():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": 1, "error_info": {"errno": 1, "error": "ok"}})

    client = make_client(httpx.MockTransport(handler))
    ok, reason = client.verify_credentials()
    assert ok is True
    assert reason == "ok"
