from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import create_task, get_task_model, mark_running


def test_checkpoint_ingest_and_resume(client, ctx, payload):
    task = create_task(client, payload)
    token = mark_running(ctx, task["id"])
    model = get_task_model(ctx, task["id"])
    checkpoint = Path(model.result_path).parent / "weights" / "last.pt"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_bytes(b"checkpoint")

    response = client.post(
        f"/api/v1/internal/tasks/{task['id']}/checkpoints",
        headers={"X-Worker-Token": token},
        json={
            "epoch": 1,
            "path": str(checkpoint),
            "size_bytes": checkpoint.stat().st_size,
            "metrics": {"metrics/mAP50(B)": 0.5},
            "is_best": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] is True

    checkpoints = client.get(f"/api/v1/tasks/{task['id']}/checkpoints")
    assert checkpoints.status_code == 200
    assert checkpoints.json()["total"] == 1
    assert checkpoints.json()["items"][0]["epoch"] == 1

    with ctx.db.session() as session:
        from app.models import Task

        row = session.get(Task, task["id"])
        assert row is not None
        row.status = "failed"
        row.failure_reason = "unit test crash"
        session.flush()

    resumed = client.post(f"/api/v1/tasks/{task['id']}/resume", json={"mode": "full"})
    assert resumed.status_code == 201, resumed.text
    resumed_task = resumed.json()
    assert resumed_task["resume_from_task_id"] == task["id"]
    assert resumed_task["resume_mode"] == "full"
    assert resumed_task["checkpoint_epoch"] == 1


def test_compare_tasks_uses_manifest_and_dataset(client, ctx, payload):
    first = create_task(client, payload)
    second = create_task(client, dict(payload, name="second run"))
    response = client.get(f"/api/v1/tasks/compare?ids={first['id']},{second['id']}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["compatible"] is True
    assert len(body["items"]) == 2
    assert body["items"][0]["manifest"]["dataset"]["content_sha256"]
