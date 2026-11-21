from __future__ import annotations

from app.models import Task


def test_health_and_overview(client):
    health = client.get("/api/v1/cluster/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    overview = client.get("/api/v1/cluster/overview")
    assert overview.status_code == 200
    data = overview.json()
    assert data["cpu"]["total"] == 8
    assert data["quota"]["limit_cpus"] == 8
    assert data["services"]["execution_backend"] == "local"
    assert data["nodes"][0]["total_cpus"] == 8


def test_options_and_preview(client, payload):
    options = client.get("/api/v1/tasks/options")
    assert options.status_code == 200
    body = options.json()
    assert "yolov8n.pt" in body["models"]
    assert "coco128.yaml" in body["datasets"]
    assert body["execution_backend"] == "local"

    preview = client.post("/api/v1/tasks/preview", json=payload)
    assert preview.status_code == 200, preview.text
    preview_data = preview.json()
    assert "--task-id <task-id>" in preview_data["command"]
    assert "--cpu-cores 2" in preview_data["command"]
    assert "device=cpu" in preview_data["equivalent_yolo_command"]


def test_create_list_filter_search_and_pagination(client, payload):
    first = payload | {"name": "alpha baseline", "priority": 20}
    second = payload | {"name": "beta run", "priority": 90, "cpu_cores": 4}
    created = [client.post("/api/v1/tasks", json=item) for item in (first, second)]
    for response in created:
        assert response.status_code == 201, response.text
    first_id = created[0].json()["id"]

    all_tasks = client.get("/api/v1/tasks", params={"page": 1, "page_size": 1}).json()
    assert all_tasks["total"] == 2
    assert len(all_tasks["items"]) == 1
    assert all_tasks["pages"] == 2
    assert all_tasks["status_counts"]["pending"] == 2

    filtered = client.get("/api/v1/tasks", params={"status": "pending"}).json()
    assert filtered["total"] == 2

    searched = client.get("/api/v1/tasks", params={"q": first_id}).json()
    assert searched["total"] == 1
    assert searched["items"][0]["id"] == first_id

    by_name = client.get("/api/v1/tasks", params={"q": "beta"}).json()
    assert by_name["total"] == 1
    assert by_name["items"][0]["name"] == "beta run"

    detail = client.get(f"/api/v1/tasks/{first_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "pending"
    assert detail.json()["queue_position"] in (1, 2)

    options = client.get("/api/v1/tasks/options").json()
    assert options["defaults"]["imgsz"] == 160


def test_logs_are_incremental(client, payload):
    task = client.post("/api/v1/tasks", json=payload).json()
    first = client.get(f"/api/v1/tasks/{task['id']}/logs", params={"offset": 0, "limit": 4096})
    assert first.status_code == 200, first.text
    chunk = first.json()
    assert "LiteTrain task" in chunk["content"]
    assert chunk["next_offset"] > 0
    assert chunk["task_status"] == "pending"
    assert chunk["file_size"] >= chunk["next_offset"]

    second = client.get(
        f"/api/v1/tasks/{task['id']}/logs",
        params={"offset": chunk["next_offset"], "limit": 4096},
    )
    assert second.status_code == 200
    assert second.json()["content"] == ""
    assert second.json()["next_offset"] == chunk["next_offset"]


def test_cancel_pending_and_conflict(client, payload):
    task = client.post("/api/v1/tasks", json=payload).json()
    cancelled = client.post(f"/api/v1/tasks/{task['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["task"]["status"] == "cancelled"

    again = client.post(f"/api/v1/tasks/{task['id']}/cancel")
    assert again.status_code == 409
    detail = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert detail["status"] == "cancelled"
    assert detail["finished_at"] is not None


def test_heartbeat_and_metrics_ingest(client, ctx, payload):
    from tests.conftest import mark_running

    task = client.post("/api/v1/tasks", json=payload).json()
    token = mark_running(ctx, task["id"], started_seconds_ago=2, heartbeat_seconds_ago=0)

    forbidden = client.post(
        f"/api/v1/internal/tasks/{task['id']}/heartbeat",
        json={"pid": 1234, "cpu_percent": 10, "memory_mb": 50, "process_count": 1},
        headers={"X-Worker-Token": "wrong"},
    )
    assert forbidden.status_code == 403

    heartbeat = client.post(
        f"/api/v1/internal/tasks/{task['id']}/heartbeat",
        json={
            "pid": 1234,
            "cpu_percent": 210.5,
            "memory_mb": 512.2,
            "process_count": 2,
            "progress": 33.3,
            "message": "epoch 1",
        },
        headers={"X-Worker-Token": token},
    )
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["accepted"] is True

    metrics = client.post(
        f"/api/v1/internal/tasks/{task['id']}/metrics",
        json={
            "epoch": 1,
            "step": 1,
            "values": {
                "train/box_loss": 1.25,
                "metrics/mAP50(B)": 0.42,
            },
            "progress": 50,
        },
        headers={"X-Worker-Token": token},
    )
    assert metrics.status_code == 200, metrics.text
    assert metrics.json()["accepted"] is True

    detail = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert detail["last_heartbeat_at"] is not None
    assert detail["latest_cpu_percent"] == 210.5
    assert detail["progress"] == 50.0

    series = client.get(f"/api/v1/tasks/{task['id']}/metrics").json()
    names = {item["name"] for item in series["series"]}
    assert names == {"train/box_loss", "metrics/mAP50(B)"}
    assert series["resources"][0]["memory_mb"] == 512.2


def test_internal_endpoints_reject_non_running_task(client, ctx, payload):
    task = client.post("/api/v1/tasks", json=payload).json()
    with ctx.db.session() as session:
        row = session.get(Task, task["id"])
        token = row.worker_token
    response = client.post(
        f"/api/v1/internal/tasks/{task['id']}/heartbeat",
        json={"pid": 1, "cpu_percent": 0, "memory_mb": 1, "process_count": 1},
        headers={"X-Worker-Token": token},
    )
    assert response.status_code == 409


def test_validation_and_not_found(client, payload):
    invalid_cpu = client.post("/api/v1/tasks", json=payload | {"cpu_cores": 99})
    assert invalid_cpu.status_code == 422

    invalid_model = client.post("/api/v1/tasks", json=payload | {"model": "not-there.pt"})
    assert invalid_model.status_code == 422

    missing = client.get("/api/v1/tasks/does-not-exist")
    assert missing.status_code == 404
    logs = client.get("/api/v1/tasks/does-not-exist/logs")
    assert logs.status_code == 404

    events = client.get("/api/v1/tasks/does-not-exist/events")
    assert events.status_code == 404

    metrics = client.get("/api/v1/tasks/does-not-exist/metrics")
    assert metrics.status_code == 404
