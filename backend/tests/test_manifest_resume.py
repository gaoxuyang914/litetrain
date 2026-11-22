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

    missing = client.post(
        f"/api/v1/tasks/{task['id']}/resume",
        json={"mode": "full", "checkpoint_epoch": 99},
    )
    assert missing.status_code == 404

    resumed = client.post(
        f"/api/v1/tasks/{task['id']}/resume",
        json={"mode": "weights", "checkpoint_epoch": 1},
    )
    assert resumed.status_code == 201, resumed.text
    resumed_task = resumed.json()
    assert resumed_task["resume_from_task_id"] == task["id"]
    assert resumed_task["resume_mode"] == "weights"
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


def test_task_distribution_time_windows(client, payload):
    create_task(client, payload)
    all_time = client.get("/api/v1/cluster/task-distribution?window=all")
    assert all_time.status_code == 200, all_time.text
    assert all_time.json()["total"] >= 1
    assert all_time.json()["counts"]["pending"] >= 1

    recent = client.get("/api/v1/cluster/task-distribution?window=5m")
    assert recent.status_code == 200, recent.text
    assert recent.json()["total"] >= 1
    assert recent.json()["since"]

    half_year = client.get("/api/v1/cluster/task-distribution?window=180d")
    assert half_year.status_code == 200
    assert half_year.json()["label"] == "近半年"


def test_rerun_reuses_task_and_preserves_attempts(client, ctx, payload):
    from app.models import Task

    task = create_task(client, payload)
    with ctx.db.session() as session:
        row = session.get(Task, task["id"])
        assert row is not None
        row.status = "cancelled"
        row.name = "unit smoke · rerun"
        session.flush()
    response = client.post(f"/api/v1/tasks/{task['id']}/rerun")
    assert response.status_code == 201, response.text
    rerun = response.json()
    assert rerun["id"] == task["id"]
    assert rerun["name"] == "unit smoke"
    assert rerun["attempt"] == 2
    assert rerun["status"] == "pending"
    assert rerun["resume_from_task_id"] is None
    assert rerun["max_runtime_seconds"] == ctx.settings.default_max_runtime_seconds
    assert rerun["max_retries"] == ctx.settings.default_max_retries

    attempts = client.get(f"/api/v1/tasks/{task['id']}/attempts").json()
    assert attempts["total"] == 2
    assert [item["status"] for item in attempts["items"]] == ["cancelled", "pending"]
    assert [item["attempt"] for item in attempts["items"]] == [1, 2]

    with ctx.db.session() as session:
        row = session.get(Task, task["id"])
        assert row is not None
        row.status = "cancelled"
        session.flush()
    rerun_again = client.post(f"/api/v1/tasks/{task['id']}/rerun")
    assert rerun_again.status_code == 201, rerun_again.text
    assert rerun_again.json()["id"] == task["id"]
    assert rerun_again.json()["attempt"] == 3
    assert client.get(f"/api/v1/tasks/{task['id']}/attempts").json()["total"] == 3


def test_unregistered_dataset_cannot_bypass_quality_gate(client, payload):
    options = client.get("/api/v1/tasks/options")
    assert options.status_code == 200
    assert options.json()["datasets"] == ["coco128.yaml"]

    response = client.post(
        "/api/v1/tasks",
        json={**payload, "dataset": "legacy-unregistered.yaml"},
    )
    assert response.status_code == 422
    assert "not registered" in response.json()["detail"]


def test_resume_artifacts_are_copied_to_new_run():
    import tempfile

    from app.training.worker import _sync_resume_artifacts

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "old-run"
        target = root / "new-run"
        (source / "weights").mkdir(parents=True)
        target.mkdir()
        (source / "weights" / "best.pt").write_bytes(b"best")
        (source / "weights" / "last.pt").write_bytes(b"last")
        (source / "results.csv").write_text("epoch,mAP50\n1,0.5\n", encoding="utf-8")
        (source / "args.yaml").write_text("epochs: 1\n", encoding="utf-8")

        _sync_resume_artifacts(source, target)

        assert (target / "weights" / "best.pt").read_bytes() == b"best"
        assert (target / "weights" / "last.pt").read_bytes() == b"last"
        assert (target / "results.csv").is_file()
        assert (target / "args.yaml").is_file()
