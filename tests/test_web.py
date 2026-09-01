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


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "app": "classin-dashboard"}
