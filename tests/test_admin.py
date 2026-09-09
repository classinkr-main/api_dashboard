"""/admin: 관 CRUD · 배정 · 계정 관리 (ADR-0005)."""

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
def store(app):
    return app.state.dash.events


@pytest.fixture
def owner(app):
    client = TestClient(app, follow_redirects=False)
    client.post("/login", data={"password": "pw"})
    return client


def login_as(app, username, password):
    client = TestClient(app, follow_redirects=False)
    resp = client.post("/login", data={"username": username, "password": password})
    return client, resp


# -- 관 관리 -------------------------------------------------------------------


def test_owner_can_add_and_delete_branch(owner, store):
    assert owner.post("/admin/branch/add", data={"name": "본관"}).status_code == 303
    branches = store.branches()
    assert [b["name"] for b in branches] == ["본관"]

    resp = owner.post("/admin/branch/delete", data={"branch_id": branches[0]["id"]})
    assert resp.status_code == 303
    assert store.branches() == []


def test_branch_with_courses_is_not_deletable(owner, store):
    owner.post("/admin/branch/add", data={"name": "본관"})
    branch_id = store.branches()[0]["id"]
    store.upsert_course(1, name="수학")
    store.set_course_branch(1, branch_id)

    owner.post("/admin/branch/delete", data={"branch_id": branch_id})
    assert len(store.branches()) == 1


def test_branch_with_classin_credentials_is_stored(owner, store):
    owner.post("/admin/branch/add", data={"name": "분관", "sid": "42", "secret": "shh"})
    branch = store.branches()[0]
    assert (branch["sid"], branch["secret"]) == ("42", "shh")


# -- 배정 ----------------------------------------------------------------------


def test_bulk_assign_courses_and_teachers_to_branches(owner, store):
    owner.post("/admin/branch/add", data={"name": "본관"})
    branch_id = store.branches()[0]["id"]
    store.upsert_course(1, name="수학")
    store.upsert_course(2, name="영어")
    store.upsert_teacher(100, "김민준")

    owner.post(
        "/admin/course/branch",
        data={"course_branch_1": str(branch_id), "course_branch_2": ""},
    )
    owner.post("/admin/teacher/branch", data={"teacher_branch_100": str(branch_id)})

    by_id = {c["course_id"]: c for c in store._all_courses()}
    assert by_id[1]["branch_id"] == branch_id
    assert by_id[2]["branch_id"] is None
    assert store.get_teacher(100)["branch_id"] == branch_id


# -- 계정 ----------------------------------------------------------------------


def test_owner_creates_user_who_can_then_log_in(app, owner, store):
    resp = owner.post(
        "/admin/user/create",
        data={"username": "kim", "password": "pw1234", "role": "teacher", "teacher_uid": "100"},
    )
    assert resp.status_code == 303
    user = store.get_user_by_username("kim")
    assert (user["role"], user["teacher_uid"], user["active"]) == ("teacher", 100, 1)

    client, login = login_as(app, "kim", "pw1234")
    assert login.status_code == 303
    assert client.get("/dashboard").status_code == 200


def test_duplicate_username_is_rejected(owner, store):
    data = {"username": "kim", "password": "pw1234", "role": "teacher"}
    owner.post("/admin/user/create", data=data)
    owner.post("/admin/user/create", data=data)
    assert len([u for u in store.users() if u["username"] == "kim"]) == 1


def test_deactivated_user_cannot_log_in_until_reactivated(app, owner, store):
    owner.post(
        "/admin/user/create",
        data={"username": "kim", "password": "pw1234", "role": "teacher"},
    )
    user_id = store.get_user_by_username("kim")["id"]

    owner.post("/admin/user/active", data={"user_id": user_id, "active": 0})
    _, login = login_as(app, "kim", "pw1234")
    assert login.status_code == 200  # error page, not a redirect

    owner.post("/admin/user/active", data={"user_id": user_id, "active": 1})
    _, login = login_as(app, "kim", "pw1234")
    assert login.status_code == 303


def test_password_reset_replaces_the_old_password(app, owner, store):
    owner.post(
        "/admin/user/create",
        data={"username": "kim", "password": "pw1234", "role": "teacher"},
    )
    user_id = store.get_user_by_username("kim")["id"]
    owner.post("/admin/user/password", data={"user_id": user_id, "password": "new-pass"})

    assert login_as(app, "kim", "pw1234")[1].status_code == 200
    assert login_as(app, "kim", "new-pass")[1].status_code == 303


# -- 권한 ----------------------------------------------------------------------


def test_teacher_is_forbidden_from_admin(app, owner, store):
    owner.post(
        "/admin/user/create",
        data={"username": "kim", "password": "pw1234", "role": "teacher"},
    )
    client, _ = login_as(app, "kim", "pw1234")
    assert client.get("/admin").status_code == 403
    assert client.post("/admin/branch/add", data={"name": "몰래"}).status_code == 403
    assert store.branches() == []


def test_manager_gets_the_reduced_admin_view(app, owner, store):
    owner.post("/admin/branch/add", data={"name": "본관"})
    branch_id = store.branches()[0]["id"]
    owner.post(
        "/admin/user/create",
        data={
            "username": "mgr",
            "password": "pw1234",
            "role": "manager",
            "branch_id": str(branch_id),
        },
    )
    client, _ = login_as(app, "mgr", "pw1234")

    page = client.get("/admin")
    assert page.status_code == 200
    assert "코스 → 관 배정" not in page.text  # owner-only section

    # 자기 관 선생님 계정은 만들 수 있다
    assert client.post(
        "/admin/user/create", data={"username": "t1", "password": "pw1234", "role": "teacher"}
    ).status_code == 303
    assert store.get_user_by_username("t1")["branch_id"] == branch_id

    # 관장/원장 계정은 만들 수 없다
    client.post(
        "/admin/user/create", data={"username": "t2", "password": "pw1234", "role": "owner"}
    )
    assert store.get_user_by_username("t2") is None

    # 관 CRUD·배정은 원장 전용
    assert client.post("/admin/branch/add", data={"name": "분관"}).status_code == 403
    assert client.post("/admin/course/branch", data={}).status_code == 403


def test_manager_cannot_touch_other_branch_accounts(app, owner, store):
    owner.post("/admin/branch/add", data={"name": "본관"})
    owner.post("/admin/branch/add", data={"name": "분관"})
    main, annex = (b["id"] for b in store.branches() if b["name"] in ("본관", "분관"))
    owner.post(
        "/admin/user/create",
        data={"username": "mgr", "password": "pw1234", "role": "manager",
              "branch_id": str(main)},
    )
    owner.post(
        "/admin/user/create",
        data={"username": "other", "password": "pw1234", "role": "teacher",
              "branch_id": str(annex)},
    )
    other_id = store.get_user_by_username("other")["id"]

    client, _ = login_as(app, "mgr", "pw1234")
    client.post("/admin/user/active", data={"user_id": other_id, "active": 0})
    assert store.get_user_by_username("other")["active"] == 1
