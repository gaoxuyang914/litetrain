from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _settings(tmp_workdir) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite:///{(tmp_workdir / 'auth.db').as_posix()}",
        execution_mode="local",
        redis_enabled=False,
        auth_enabled=True,
        serve_frontend=False,
        total_cpus=8,
        user_cpu_quota=8,
        heartbeat_timeout_seconds=20,
        default_max_runtime_seconds=600,
    )


def test_login_is_required_and_roles_are_enforced(tmp_workdir):
    app = create_app(_settings(tmp_workdir), start_scheduler=False)
    with TestClient(app) as client:
        assert client.get("/api/v1/tasks").status_code == 401

        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        assert login.status_code == 200, login.text
        admin_token = login.json()["token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        assert "admin.console" in login.json()["user"]["permissions"]
        assert client.get("/api/v1/auth/me", headers=admin_headers).status_code == 200
        assert client.get("/api/v1/tasks", headers=admin_headers).status_code == 200

        created = client.post(
            "/api/v1/admin/users",
            headers=admin_headers,
            json={
                "username": "viewer",
                "password": "viewer123",
                "role": "viewer",
            },
        )
        assert created.status_code == 201, created.text
        viewer_id = created.json()["id"]
        viewer_login = client.post(
            "/api/v1/auth/login",
            json={"username": "viewer", "password": "viewer123"},
        )
        assert viewer_login.status_code == 200, viewer_login.text
        viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['token']}"}
        assert client.get("/api/v1/tasks", headers=viewer_headers).status_code == 200
        write = client.post("/api/v1/tasks", headers=viewer_headers, json={})
        assert write.status_code == 403
        assert client.get("/api/v1/admin/users", headers=viewer_headers).status_code == 403
        assert client.patch("/api/v1/settings", headers=viewer_headers, json={}).status_code == 403

        disabled = client.patch(
            f"/api/v1/admin/users/{viewer_id}",
            headers=admin_headers,
            json={"active": False},
        )
        assert disabled.status_code == 200, disabled.text
        denied = client.post(
            "/api/v1/auth/login",
            json={"username": "viewer", "password": "viewer123"},
        )
        assert denied.status_code == 401

        logout = client.post("/api/v1/auth/logout", headers=admin_headers)
        assert logout.status_code == 200
        assert client.get("/api/v1/tasks", headers=admin_headers).status_code == 401
