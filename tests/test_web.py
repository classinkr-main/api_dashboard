import pytest
from fastapi.testclient import TestClient

from classin_dashboard.config import Settings
from classin_dashboard.web.app import create_app


def make_settings(tmp_path, **overrides):
    kwargs = dict(
        data_dir=tmp_path,
        root_path="",
        auth_mode="fixed",
        access_password="pw",
        classin_sid="1",
        classin_secret="s",
        secret_key="k",
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


@pytest.fixture
def app(tmp_path):
    return create_app(make_settings(tmp_path))


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=False)


# -- auth ----------------------------------------------------------------


def test_dashboard_unauthenticated_redirects_to_login(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]


def test_login_wrong_password_shows_error(client):
    resp = client.post("/login", data={"password": "wrong", "role": "owner"})
    assert resp.status_code == 200
    assert "올바르지 않습니다" in resp.text


def test_login_correct_password_sets_cookie_and_dashboard_loads(client):
    resp = client.post("/login", data={"password": "pw", "role": "owner"})
    assert resp.status_code == 303
    assert "dash_session" in resp.cookies

    dash_resp = client.get("/dashboard")
    assert dash_resp.status_code == 200


def test_logout_clears_session(client):
    client.post("/login", data={"password": "pw", "role": "owner"})
    assert client.get("/dashboard").status_code == 200

    logout_resp = client.get("/logout")
    assert logout_resp.status_code == 303
    assert "/login" in logout_resp.headers["location"]

    after_logout = client.get("/dashboard")
    assert after_logout.status_code == 303
    assert "/login" in after_logout.headers["location"]


def test_login_fixed_mode_missing_server_credentials_errors(tmp_path):
    app = create_app(make_settings(tmp_path, classin_sid="", classin_secret=""))
    c = TestClient(app, follow_redirects=False)
    resp = c.post("/login", data={"password": "pw", "role": "owner"})
    assert resp.status_code == 200
    assert "ClassIn 자격증명" in resp.text


# -- webhook ---------------------------------------------------------------


def test_webhook_always_acks_valid_json(client, app):
    resp = client.post(
        "/webhook/classin",
        json={"_id": "e1", "Cmd": "Attendance", "ClassID": 1, "Data": []},
    )
    assert resp.status_code == 200
    assert resp.json() == {"error_info": {"errno": 1, "error": "程序正常执行"}}
    events = app.state.dash.events.events()
    assert len(events) == 1
    assert events[0]["cmd"] == "Attendance"


def test_webhook_acks_non_json_body_without_ingesting(client, app):
    resp = client.post(
        "/webhook/classin",
        content=b"not-json-at-all",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"error_info": {"errno": 1, "error": "程序正常执行"}}
    assert app.state.dash.events.events() == []


def test_webhook_safekey_mismatch_acks_but_skips_ingest(tmp_path):
    settings = make_settings(tmp_path, webhook_safekey="secretkey")
    app = create_app(settings)
    client = TestClient(app, follow_redirects=False)

    resp = client.post(
        "/webhook/classin",
        json={
            "_id": "e2",
            "Cmd": "Attendance",
            "ClassID": 1,
            "Data": [],
            "TimeStamp": 1700000000,
            "SafeKey": "wrong-signature",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"error_info": {"errno": 1, "error": "程序正常执行"}}
    # raw payload preserved, but not turned into a normalized event row
    assert app.state.dash.events.events() == []


def test_webhook_safekey_match_ingests(tmp_path):
    import hashlib

    secret = "secretkey"
    ts = 1700000000
    safekey = hashlib.md5(f"{secret}{ts}".encode()).hexdigest()
    settings = make_settings(tmp_path, webhook_safekey=secret)
    app = create_app(settings)
    client = TestClient(app, follow_redirects=False)

    resp = client.post(
        "/webhook/classin",
        json={
            "_id": "e3",
            "Cmd": "Attendance",
            "ClassID": 1,
            "Data": [],
            "TimeStamp": ts,
            "SafeKey": safekey,
        },
    )
    assert resp.status_code == 200
    events = app.state.dash.events.events()
    assert len(events) == 1
    assert events[0]["cmd"] == "Attendance"


# -- create (smart schedule parsing) --------------------------------------


class FakeActions:
    """Records ClassIn write calls instead of performing them."""

    calls: list[tuple] = []

    def __init__(self, client):
        pass

    def add_course(self, **kwargs):
        FakeActions.calls.append(("add_course", kwargs))
        return 900

    def create_unit(self, **kwargs):
        FakeActions.calls.append(("create_unit", kwargs))
        return 901

    def create_classroom(self, **kwargs):
        FakeActions.calls.append(("create_classroom", kwargs))
        return {"classId": 902}

    def create_activity(self, **kwargs):
        FakeActions.calls.append(("create_activity", kwargs))
        return 903


@pytest.fixture
def logged_in(client, app):
    client.post("/login", data={"password": "pw", "role": "teacher"})
    return client


def _plan_json(html_text):
    import html as html_mod
    import re

    match = re.search(r'name="plan_json" value="(.*?)">', html_text, re.S)
    return html_mod.unescape(match.group(1))


def test_create_form_has_only_the_textarea(logged_in):
    resp = logged_in.get("/create")
    assert resp.status_code == 200
    assert 'name="schedule_text"' in resp.text
    assert "teacher_map" not in resp.text
    assert "default_teacher_uid" not in resp.text


def test_create_parse_resolves_teacher_without_a_mapping(logged_in, app):
    app.state.dash.events.upsert_teacher(20001, "김선생")
    resp = logged_in.post(
        "/create/parse",
        data={"schedule_text": "고2 수학 A반 김선생님 화/목 19:00-21:00 5월 첫째 주부터 2주"},
    )
    assert resp.status_code == 200
    assert "UID 20001" in resp.text
    assert "<select" not in resp.text  # nothing left to ask about


def test_create_execute_uses_override_and_reuses_existing_course(logged_in, app, monkeypatch):
    from classin_dashboard.web import routes_create

    FakeActions.calls = []
    monkeypatch.setattr(routes_create, "ClassInActions", FakeActions)
    app.state.dash.events.upsert_course(777, name="고2 수학 A반")

    preview = logged_in.post(
        "/create/parse",
        data={"schedule_text": "고2 수학 A반 화/목 19:00-21:00 5월 첫째 주부터 1주"},
    )
    assert "기존 코스 재사용" in preview.text

    resp = logged_in.post(
        "/create/execute",
        data={"plan_json": _plan_json(preview.text), "teacher_uid_manual_0": "20007"},
    )
    assert resp.status_code == 200
    kinds = [name for name, _ in FakeActions.calls]
    assert "add_course" not in kinds  # existing course reused
    assert kinds.count("create_classroom") == 2
    assert all(
        kwargs["teacher_uid"] == 20007
        for name, kwargs in FakeActions.calls
        if name == "create_classroom"
    )
    assert app.state.dash.events.lessons(course_id=777)


def test_create_execute_reports_unresolved_teacher(logged_in, app, monkeypatch):
    from classin_dashboard.web import routes_create

    FakeActions.calls = []
    monkeypatch.setattr(routes_create, "ClassInActions", FakeActions)
    preview = logged_in.post(
        "/create/parse",
        data={"schedule_text": "고1 국어 화/목 19:00-21:00 5월 첫째 주부터 1주"},
    )
    resp = logged_in.post("/create/execute", data={"plan_json": _plan_json(preview.text)})
    assert resp.status_code == 200
    assert "선생님을 확정하지 못했습니다" in resp.text
    assert FakeActions.calls == []


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "app": "classin-dashboard"}
