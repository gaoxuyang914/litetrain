from __future__ import annotations


def test_runtime_settings_update_and_task_defaults(client):
    initial = client.get("/api/v1/settings")
    assert initial.status_code == 200, initial.text
    assert initial.json()["default_max_runtime_seconds"] == 600

    updated = client.patch(
        "/api/v1/settings",
        json={
            "default_max_runtime_seconds": 900,
            "heartbeat_timeout_seconds": 10,
            "default_max_retries": 1,
            "default_cpu_cores": 2,
            "user_cpu_quota": 4,
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["default_max_runtime_seconds"] == 900
    assert body["heartbeat_timeout_seconds"] == 10
    assert body["default_max_retries"] == 1
    assert body["default_cpu_cores"] == 2
    assert body["user_cpu_quota"] == 4

    options = client.get("/api/v1/tasks/options")
    assert options.status_code == 200, options.text
    data = options.json()
    assert data["defaults"]["max_runtime_seconds"] == 900
    assert data["defaults"]["max_retries"] == 1
    assert data["defaults"]["cpu_cores"] == 2
    assert data["user_cpu_quota"] == 4

    bad = client.patch(
        "/api/v1/settings",
        json={"user_cpu_quota": 2, "default_cpu_cores": 4},
    )
    assert bad.status_code == 422
    assert "default_cpu_cores" in bad.json()["detail"]

    invalid = client.patch(
        "/api/v1/settings",
        json={"heartbeat_timeout_seconds": 1},
    )
    assert invalid.status_code == 422
