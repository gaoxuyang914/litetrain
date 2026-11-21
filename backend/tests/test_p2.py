from __future__ import annotations

import hashlib
import hmac
import io
import json
import time
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.database import utcnow
from app.models import (
    Artifact,
    DatasetRecord,
    Node,
    Pipeline,
    PipelineStage,
    ResourceAllocation,
    Task,
    WebhookDelivery,
    WebhookEndpoint,
)
from tests.conftest import create_task


def _zip_dataset() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "dataset.yaml",
            "path: .\ntrain: images/train\nval: images/train\nnames:\n  0: widget\n",
        )
        zf.writestr("images/train/a.jpg", b"fake-jpeg-bytes")
        zf.writestr("labels/train/a.txt", b"0 0.5 0.5 0.2 0.2\n")
    return buffer.getvalue()


def test_dataset_registry_and_upload(client, ctx):
    builtin = client.get("/api/v1/datasets")
    assert builtin.status_code == 200
    names = {item["name"] for item in builtin.json()["items"]}
    assert "COCO128" in names

    archive = _zip_dataset()
    response = client.post(
        "/api/v1/datasets/upload",
        data={"name": "widgets", "version": "v1", "description": "unit-test dataset"},
        files={"file": ("widgets.zip", archive, "application/zip")},
    )
    assert response.status_code == 201, response.text
    dataset = response.json()
    assert dataset["image_count"] == 1
    assert dataset["class_count"] == 1
    assert dataset["is_builtin"] is False
    yaml_path = ctx.settings.datasets_dir / dataset["yaml_path"]
    assert yaml_path.is_file()
    assert "images/train" in yaml_path.read_text(encoding="utf-8")

    duplicate = client.post(
        "/api/v1/datasets/upload",
        data={"name": "widgets", "version": "v1"},
        files={"file": ("widgets.zip", archive, "application/zip")},
    )
    assert duplicate.status_code == 409

    dataset_id = dataset["id"]
    with ctx.db.session() as session:
        record = session.get(DatasetRecord, dataset_id)
        assert record is not None
        archive_path = (ctx.settings.data_dir / record.archive_path).resolve()
    assert client.get(f"/api/v1/datasets/{dataset_id}").status_code == 200
    downloaded = client.get(f"/api/v1/datasets/{dataset_id}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == archive

    deleted = client.delete(f"/api/v1/datasets/{dataset_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/datasets/{dataset_id}").status_code == 404
    options = client.get("/api/v1/tasks/options").json()
    assert Path(dataset["yaml_path"]).name not in options["datasets"]
    # Test-created files are runtime artifacts and are cleaned up explicitly.
    import shutil

    shutil.rmtree(ctx.settings.datasets_dir / "managed" / dataset_id, ignore_errors=True)
    archive_path.unlink(missing_ok=True)


def test_artifact_scan_and_download(client, ctx, payload):
    task = create_task(client, payload)
    run_dir = ctx.settings.runs_dir / task["id"]
    (run_dir / "weights").mkdir(parents=True, exist_ok=True)
    (run_dir / "weights" / "best.pt").write_bytes(b"weights")
    (run_dir / "results.csv").write_text("epoch,box_loss\n1,1.23\n", encoding="utf-8")
    with ctx.db.session() as session:
        row = session.get(Task, task["id"])
        assert row is not None
        row.status = "succeeded"
        row.exit_code = 0
        row.started_at = utcnow()
        row.finished_at = utcnow()
        session.flush()

    response = client.get("/api/v1/artifacts", params={"task_id": task["id"]})
    assert response.status_code == 200, response.text
    artifacts = response.json()["items"]
    assert {item["name"] for item in artifacts} == {"best.pt", "results.csv"}
    weight = next(item for item in artifacts if item["name"] == "best.pt")
    download = client.get(weight["download_url"])
    assert download.status_code == 200
    assert download.content == b"weights"

    import shutil

    shutil.rmtree(run_dir, ignore_errors=True)


def test_webhook_enqueue_sign_and_deliver(client, ctx, monkeypatch, payload):
    created = client.post(
        "/api/v1/webhooks",
        json={
            "name": "unit receiver",
            "url": "http://127.0.0.1:9999/hook",
            "secret": "unit-secret",
            "events": ["task.submitted", "task.succeeded"],
        },
    )
    assert created.status_code == 201, created.text
    endpoint = created.json()
    assert endpoint["events"] == ["task.submitted", "task.succeeded"]

    task = create_task(client, payload)
    pending = client.get("/api/v1/webhooks/deliveries").json()["items"]
    assert pending
    assert pending[0]["status"] == "pending"
    assert pending[0]["task_id"] == task["id"]

    calls: list[dict] = []

    class FakeResponse:
        status_code = 200
        text = "ok"

    def fake_post(url, data, headers, timeout):
        calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        return FakeResponse()

    import app.services.webhooks as module

    monkeypatch.setattr(module.requests, "post", fake_post)
    assert ctx.webhook_dispatcher.process_once() is True
    assert len(calls) == 1
    headers = calls[0]["headers"]
    expected = hmac.new(
        b"unit-secret",
        headers["X-LiteTrain-Timestamp"].encode() + b"." + calls[0]["data"],
        hashlib.sha256,
    ).hexdigest()
    assert headers["X-LiteTrain-Signature"] == expected
    payload_data = json.loads(calls[0]["data"])
    assert payload_data["task"]["id"] == task["id"]

    deliveries = client.get("/api/v1/webhooks/deliveries").json()["items"]
    assert deliveries[0]["status"] == "success"
    assert deliveries[0]["response_code"] == 200

    listed = client.get("/api/v1/webhooks").json()
    assert listed["total"] == 1
    assert listed["items"][0]["success_count"] == 1

    tested = client.post(f"/api/v1/webhooks/{endpoint['id']}/test")
    assert tested.status_code == 200
    assert tested.json()["accepted"] is True
    assert ctx.webhook_dispatcher.process_once() is True
    assert len(calls) == 2

    assert client.delete(f"/api/v1/webhooks/{endpoint['id']}").status_code == 204
    assert client.get("/api/v1/webhooks").json()["total"] == 0


def test_two_node_topology_and_balanced_allocation(client, ctx, payload):
    applied = client.post(
        "/api/v1/cluster/nodes/topology",
        json={"nodes": [{"name": "sim-a", "total_cpus": 4}, {"name": "sim-b", "total_cpus": 4}]},
    )
    assert applied.status_code == 200, applied.text
    data = applied.json()
    assert data["total_cpus"] == 8
    online = [node for node in data["nodes"] if node["status"] == "online"]
    assert len(online) == 2
    options = client.get("/api/v1/tasks/options").json()
    assert options["limits"]["cpu_cores"] == [1, 4]

    first = create_task(client, payload | {"name": "node task a", "cpu_cores": 3})
    second = create_task(client, payload | {"name": "node task b", "cpu_cores": 3})
    with ctx.db.session() as session:
        task_a = session.get(Task, first["id"])
        task_b = session.get(Task, second["id"])
        assert task_a is not None and task_b is not None
        node_a = ctx.resources.choose_node(session, "demo", 3)
        assert node_a is not None
        ctx.resources.create_allocation(session, task_a, node_a)
        session.flush()
        node_b = ctx.resources.choose_node(session, "demo", 3)
        assert node_b is not None and node_b.id != node_a.id
        ctx.resources.create_allocation(session, task_b, node_b)
        session.flush()
        assert ctx.resources.choose_node(session, "demo", 2) is None
        task_a.status = "running"
        task_a.started_at = utcnow()
        session.flush()
    overview_nodes = client.get("/api/v1/cluster/overview").json()["nodes"]
    node_payload = next(node for node in overview_nodes if node["id"] == node_a.id)
    assert [task["name"] for task in node_payload["active_tasks"]] == ["node task a"]
    offline = client.patch(
        f"/api/v1/cluster/nodes/{node_a.id}",
        json={"status": "offline"},
    )
    assert offline.status_code == 409

    # New topology while allocations exist must be refused.
    rejected = client.post(
        "/api/v1/cluster/nodes/topology",
        json={"nodes": [{"name": "only", "total_cpus": 8}]},
    )
    assert rejected.status_code == 409

    with ctx.db.session() as session:
        ctx.resources.release_allocation(session, task_a.id)
        ctx.resources.release_allocation(session, task_b.id)
        session.flush()
    offline = client.patch(f"/api/v1/cluster/nodes/{node_a.id}", json={"status": "offline"})
    assert offline.status_code == 200
    overview = client.get("/api/v1/cluster/overview").json()
    assert overview["cpu"]["total"] == 4


def test_webhook_retry_then_success(client, ctx, monkeypatch):
    created = client.post(
        "/api/v1/webhooks",
        json={
            "name": "retry receiver",
            "url": "http://127.0.0.1:9998/hook",
            "secret": "retry-secret",
            "events": ["webhook.test"],
        },
    )
    assert created.status_code == 201, created.text
    endpoint = created.json()
    queued = client.post(f"/api/v1/webhooks/{endpoint['id']}/test")
    assert queued.status_code == 200

    class FakeResponse:
        def __init__(self, code: int) -> None:
            self.status_code = code
            self.text = "ok" if code < 300 else "server error"

    import app.services.webhooks as module

    monkeypatch.setattr(module.requests, "post", lambda *a, **k: FakeResponse(500))
    assert ctx.webhook_dispatcher.process_once() is True
    deliveries = client.get("/api/v1/webhooks/deliveries").json()["items"]
    assert deliveries[0]["status"] == "pending"
    assert deliveries[0]["attempts"] == 1
    assert deliveries[0]["response_code"] == 500

    with ctx.db.session() as session:
        row = session.get(WebhookDelivery, deliveries[0]["id"])
        assert row is not None
        row.next_attempt_at = utcnow()
        session.flush()
    monkeypatch.setattr(module.requests, "post", lambda *a, **k: FakeResponse(200))
    assert ctx.webhook_dispatcher.process_once() is True
    deliveries = client.get("/api/v1/webhooks/deliveries").json()["items"]
    assert deliveries[0]["status"] == "success"
    assert deliveries[0]["attempts"] == 2
    assert client.delete(f"/api/v1/webhooks/{endpoint['id']}").status_code == 204


def test_task_cpu_must_fit_a_single_node(client, ctx, payload):
    applied = client.post(
        "/api/v1/cluster/nodes/topology",
        json={"nodes": [{"name": "small-a", "total_cpus": 4}, {"name": "small-b", "total_cpus": 4}]},
    )
    assert applied.status_code == 200
    response = client.post("/api/v1/tasks", json={**payload, "cpu_cores": 5})
    assert response.status_code == 422
    assert "single node" in response.json()["detail"]


def test_pipeline_coordinator_serial_stages(client, ctx, payload):
    response = client.post(
        "/api/v1/pipelines",
        json={
            "name": "unit pipeline",
            "source_dataset": "coco128.yaml",
            "model": "yolov8n.pt",
            "epochs": 1,
            "imgsz": 64,
            "batch_size": 1,
            "cpu_cores": 2,
            "priority": 50,
            "max_runtime_seconds": 120,
            "max_retries": 0,
            "max_images": 1,
        },
    )
    assert response.status_code == 201, response.text
    pipeline = response.json()
    pipeline_id = pipeline["id"]
    assert [stage["stage_key"] for stage in pipeline["stages"]] == [
        "preprocess",
        "train",
        "evaluate",
    ]
    first_task_id = pipeline["stages"][0]["task"]["id"]

    output = ctx.settings.datasets_dir / "pipelines" / pipeline_id
    output.mkdir(parents=True, exist_ok=True)
    (output / "dataset.yaml").write_text(
        "path: .\ntrain: images/train\nval: images/train\nnames:\n  0: widget\n",
        encoding="utf-8",
    )
    with ctx.db.session() as session:
        task = session.get(Task, first_task_id)
        assert task is not None
        task.status = "succeeded"
        task.exit_code = 0
        task.finished_at = utcnow()
        session.flush()
    ctx.pipelines.advance()

    current = client.get(f"/api/v1/pipelines/{pipeline_id}").json()
    assert current["stages"][0]["status"] == "succeeded"
    train_task_id = current["stages"][1]["task"]["id"]
    assert train_task_id
    assert current["stages"][2]["task"] is None

    weights_dir = ctx.settings.runs_dir / train_task_id / "weights"
    weights_dir.mkdir(parents=True, exist_ok=True)
    (weights_dir / "best.pt").write_bytes(b"fake-weights")
    with ctx.db.session() as session:
        task = session.get(Task, train_task_id)
        assert task is not None
        task.status = "succeeded"
        task.exit_code = 0
        task.finished_at = utcnow()
        session.flush()
    ctx.pipelines.advance()

    current = client.get(f"/api/v1/pipelines/{pipeline_id}").json()
    assert current["stages"][1]["status"] == "succeeded"
    eval_task_id = current["stages"][2]["task"]["id"]
    assert eval_task_id
    assert current["stages"][2]["status"] == "pending"

    with ctx.db.session() as session:
        task = session.get(Task, eval_task_id)
        assert task is not None
        task.status = "succeeded"
        task.exit_code = 0
        task.finished_at = utcnow()
        session.flush()
    ctx.pipelines.advance()
    current = client.get(f"/api/v1/pipelines/{pipeline_id}").json()
    assert current["status"] == "succeeded"
    assert current["progress"] == 100.0 or current["progress"] > 99
    assert current["finished_at"] is not None

    import shutil

    shutil.rmtree(output, ignore_errors=True)
    shutil.rmtree(ctx.settings.runs_dir / first_task_id, ignore_errors=True)
    shutil.rmtree(ctx.settings.runs_dir / train_task_id, ignore_errors=True)
    shutil.rmtree(ctx.settings.runs_dir / eval_task_id, ignore_errors=True)


def test_pipeline_failure_stops_later_stages(client, ctx):
    created = client.post(
        "/api/v1/pipelines",
        json={
            "name": "failure pipeline",
            "source_dataset": "coco128.yaml",
            "model": "yolov8n.pt",
            "epochs": 1,
            "imgsz": 64,
            "batch_size": 1,
            "cpu_cores": 2,
            "priority": 50,
            "max_runtime_seconds": 120,
            "max_retries": 0,
            "max_images": 1,
        },
    )
    assert created.status_code == 201, created.text
    pipeline_id = created.json()["id"]
    first_task_id = created.json()["stages"][0]["task"]["id"]
    with ctx.db.session() as session:
        task = session.get(Task, first_task_id)
        assert task is not None
        task.status = "failed"
        task.exit_code = 1
        task.failure_reason = "dataset_validation_failed"
        task.finished_at = utcnow()
        session.flush()
    ctx.pipelines.advance()
    current = client.get(f"/api/v1/pipelines/{pipeline_id}").json()
    assert current["status"] == "failed"
    assert current["stages"][0]["status"] == "failed"
    assert current["stages"][1]["status"] == "cancelled"
    assert current["stages"][2]["status"] == "cancelled"
    assert current["failure_reason"] == "dataset_validation_failed"


def test_pipeline_cancel(client, ctx):
    created = client.post(
        "/api/v1/pipelines",
        json={
            "name": "cancel pipeline",
            "source_dataset": "coco128.yaml",
            "model": "yolov8n.pt",
            "epochs": 1,
            "imgsz": 64,
            "batch_size": 1,
            "cpu_cores": 2,
            "priority": 50,
            "max_runtime_seconds": 120,
            "max_retries": 0,
            "max_images": 1,
        },
    )
    assert created.status_code == 201, created.text
    pipeline_id = created.json()["id"]
    cancelled = client.post(f"/api/v1/pipelines/{pipeline_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    again = client.post(f"/api/v1/pipelines/{pipeline_id}/cancel")
    assert again.status_code == 409
