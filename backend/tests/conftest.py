from __future__ import annotations

import json
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.database import utcnow
from app.main import create_app
from app.models import ResourceAllocation, Task

ALT_EXECUTOR = "local"


@pytest.fixture()
def tmp_workdir():
    base = Path(__file__).resolve().parent / ".tmp"
    base.mkdir(exist_ok=True)
    workdir = base / uuid.uuid4().hex
    workdir.mkdir()
    try:
        yield workdir
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@pytest.fixture()
def settings(tmp_workdir: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite:///{(tmp_workdir / 'platform.db').as_posix()}",
        execution_mode="local",
        redis_enabled=False,
        auth_enabled=False,
        serve_frontend=False,
        total_cpus=8,
        user_cpu_quota=8,
        heartbeat_timeout_seconds=20,
        default_max_runtime_seconds=600,
    )


@pytest.fixture()
def app(settings: Settings):
    return create_app(settings, start_scheduler=False)


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def ctx(app, client):
    return app.state.ctx


@pytest.fixture()
def payload() -> dict:
    return {
        "name": "unit smoke",
        "model": "yolov8n.pt",
        "dataset": "coco128.yaml",
        "epochs": 1,
        "imgsz": 96,
        "batch_size": 2,
        "workers": 0,
        "cpu_cores": 2,
        "priority": 50,
        "max_runtime_seconds": 300,
        "max_retries": 0,
    }


def create_task(client: TestClient, payload: dict) -> dict:
    response = client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def mark_running(ctx, task_id: str, *, started_seconds_ago: int = 2, heartbeat_seconds_ago: int = 0):
    with ctx.db.session() as session:
        task = session.get(Task, task_id)
        assert task is not None
        task.status = "running"
        task.started_at = utcnow() - timedelta(seconds=started_seconds_ago)
        task.last_heartbeat_at = utcnow() - timedelta(seconds=heartbeat_seconds_ago)
        task.executor = ALT_EXECUTOR
        if not session.scalar(
            select(ResourceAllocation).where(
                ResourceAllocation.task_id == task_id,
                ResourceAllocation.state == "active",
            )
        ):
            ctx.resources.create_allocation(session, task)
        session.flush()
        token = task.worker_token
    return token


def write_result(ctx, task_id: str, exit_code: int, error: str | None = None) -> None:
    task = get_task_model(ctx, task_id)
    path = Path(task.result_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "exit_code": exit_code,
                "error": error,
                "finished_at": utcnow().isoformat(),
            }
        ),
        encoding="utf-8",
    )


def active_allocations(ctx, task_id: str) -> int:
    with ctx.db.session() as session:
        rows = session.scalars(
            select(ResourceAllocation).where(
                ResourceAllocation.task_id == task_id,
                ResourceAllocation.state == "active",
            )
        ).all()
        return len(rows)


def get_task_model(ctx, task_id: str) -> Task:
    with ctx.db.session() as session:
        task = session.get(Task, task_id)
        assert task is not None
        session.expunge(task)
        return task
