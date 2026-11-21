from __future__ import annotations

from datetime import timedelta

from app.database import utcnow
from app.models import Task
from tests.conftest import active_allocations, create_task, mark_running, write_result


def _set_task(ctx, task_id: str, **values) -> None:
    with ctx.db.session() as session:
        task = session.get(Task, task_id)
        assert task is not None
        for key, value in values.items():
            setattr(task, key, value)
        session.flush()


def test_runtime_timeout_fails_and_releases(client, ctx, payload):
    task = create_task(client, payload)
    mark_running(ctx, task["id"], started_seconds_ago=100, heartbeat_seconds_ago=0)
    _set_task(ctx, task["id"], max_runtime_seconds=10)
    assert active_allocations(ctx, task["id"]) == 1

    ctx.scheduler._inspect_running(task["id"])

    detail = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert detail["status"] == "failed"
    assert detail["failure_reason"] == "runtime_timeout"
    assert detail["finished_at"] is not None
    assert active_allocations(ctx, task["id"]) == 0


def test_heartbeat_timeout_fails_and_releases(client, ctx, payload):
    task = create_task(client, payload)
    mark_running(ctx, task["id"], started_seconds_ago=2, heartbeat_seconds_ago=100)

    ctx.scheduler._inspect_running(task["id"])

    detail = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert detail["status"] == "failed"
    assert detail["failure_reason"] == "heartbeat_timeout"
    assert active_allocations(ctx, task["id"]) == 0


def test_successful_result_marks_succeeded(client, ctx, payload):
    task = create_task(client, payload)
    mark_running(ctx, task["id"])
    write_result(ctx, task["id"], 0)

    ctx.scheduler._inspect_running(task["id"])

    detail = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert detail["status"] == "succeeded"
    assert detail["exit_code"] == 0
    assert detail["progress"] == 100.0
    assert active_allocations(ctx, task["id"]) == 0


def test_nonzero_exit_can_auto_retry(client, ctx, payload):
    retry_payload = payload | {"max_retries": 1, "name": "retry me"}
    task = create_task(client, retry_payload)
    mark_running(ctx, task["id"])
    write_result(ctx, task["id"], 1, "CUDA out of memory (simulated)")

    ctx.scheduler._inspect_running(task["id"])

    detail = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert detail["status"] == "pending"
    assert detail["retry_count"] == 1
    assert detail["attempt"] == 2
    assert "simulated" in detail["failure_reason"]
    assert active_allocations(ctx, task["id"]) == 0


def test_cancel_running_task_releases_resources(client, ctx, payload):
    task = create_task(client, payload)
    mark_running(ctx, task["id"])

    response = client.post(f"/api/v1/tasks/{task['id']}/cancel")
    assert response.status_code == 200, response.text
    detail = response.json()["task"]
    assert detail["status"] == "cancelled"
    assert detail["failure_reason"] == "cancelled_by_user"
    assert active_allocations(ctx, task["id"]) == 0


def test_capacity_and_priority_queue(client, ctx, payload):
    low = create_task(
        client,
        payload
        | {"name": "large low priority", "cpu_cores": 6, "priority": 10},
    )
    high = create_task(
        client,
        payload
        | {"name": "small high priority", "cpu_cores": 4, "priority": 90},
    )
    listed = client.get("/api/v1/tasks").json()["items"]
    by_id = {item["id"]: item for item in listed}
    assert by_id[high["id"]]["queue_position"] == 1
    assert by_id[low["id"]]["queue_position"] == 2

    mark_running(ctx, low["id"])
    with ctx.db.session() as session:
        assert ctx.resources.available_for(session, "demo", 4) is False
        assert ctx.resources.available_for(session, "demo", 2) is True
        assert ctx.resources.allocated_cpus(session) == 6

    # A queue position still exists for the other task, and the allocation shows
    # up in the cluster endpoint.
    overview = client.get("/api/v1/cluster/overview").json()
    assert overview["cpu"]["allocated"] == 6
    assert overview["cpu"]["available"] == 2
    assert overview["tasks"]["running"] == 1
    assert overview["tasks"]["pending"] == 1


def test_retry_then_terminal_failure(client, ctx, payload):
    retry_payload = payload | {"max_retries": 1, "name": "retry twice"}
    task = create_task(client, retry_payload)
    mark_running(ctx, task["id"])
    write_result(ctx, task["id"], 1, "worker blew up")
    ctx.scheduler._inspect_running(task["id"])
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["retry_count"] == 1

    # Simulate the second attempt failing.
    mark_running(ctx, task["id"])
    write_result(ctx, task["id"], 1, "worker blew up again")
    ctx.scheduler._inspect_running(task["id"])

    detail = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert detail["status"] == "failed"
    assert detail["retry_count"] == 1
    assert "again" in detail["failure_reason"]
