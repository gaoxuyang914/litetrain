"""Internal worker callbacks (heartbeat + metric ingestion)."""

from __future__ import annotations

import hmac
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.api.deps import get_ctx
from app.context import AppContext
from app.database import utcnow
from app.models import ResourceSample, Task, TaskCheckpoint, TaskMetric, TaskStatus
from app.schemas import (
    CheckpointIngestRequest,
    CheckpointIngestResponse,
    HeartbeatPayload,
    HeartbeatResponse,
    MetricsIngestRequest,
    MetricsIngestResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", tags=["internal"])


def _authenticated_task(
    ctx: AppContext,
    task_id: str,
    token: str,
    *,
    require_running: bool = True,
) -> Task:
    with ctx.db.session() as session:
        task = session.get(Task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if not hmac.compare_digest(task.worker_token, token):
            raise HTTPException(status_code=403, detail="Invalid worker token")
        if require_running and task.status != TaskStatus.RUNNING.value:
            raise HTTPException(status_code=409, detail=f"Task is {task.status}")
        session.expunge(task)
        return task


@router.post(
    "/tasks/{task_id}/heartbeat",
    response_model=HeartbeatResponse,
    summary="Worker heartbeat + resource sample",
)
def heartbeat(
    task_id: str,
    payload: HeartbeatPayload,
    x_worker_token: str = Header(alias="X-Worker-Token"),
    ctx: AppContext = Depends(get_ctx),
) -> HeartbeatResponse:
    task = _authenticated_task(ctx, task_id, x_worker_token)
    now = utcnow()
    with ctx.db.session() as session:
        live = session.get(Task, task_id)
        if live is None:
            raise HTTPException(status_code=404, detail="Task not found")
        live.last_heartbeat_at = now
        live.latest_cpu_percent = round(max(0.0, payload.cpu_percent), 2)
        live.latest_memory_mb = round(max(0.0, payload.memory_mb), 2)
        if payload.progress is not None:
            live.progress = round(payload.progress, 1)
        if not live.pid:
            live.pid = payload.pid
        session.add(
            ResourceSample(
                task_id=task_id,
                cpu_percent=round(max(0.0, payload.cpu_percent), 2),
                memory_mb=round(max(0.0, payload.memory_mb), 2),
                process_count=payload.process_count,
                sampled_at=now,
            )
        )
        status_value = live.status
    return HeartbeatResponse(
        accepted=True,
        task_status=TaskStatus(status_value),
        server_time=now,
    )


@router.post(
    "/tasks/{task_id}/metrics",
    response_model=MetricsIngestResponse,
    summary="Ingest epoch-level YOLO metrics",
)
def ingest_metrics(
    task_id: str,
    payload: MetricsIngestRequest,
    x_worker_token: str = Header(alias="X-Worker-Token"),
    ctx: AppContext = Depends(get_ctx),
) -> MetricsIngestResponse:
    _authenticated_task(ctx, task_id, x_worker_token)
    now = utcnow()
    stored = 0
    with ctx.db.session() as session:
        live = session.get(Task, task_id)
        if live is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if payload.progress is not None:
            live.progress = round(max(0.0, min(100.0, payload.progress)), 1)
        for name, value in payload.values.items():
            statement = (
                sqlite_insert(TaskMetric)
                .values(
                    task_id=task_id,
                    name=name[:96],
                    value=float(value),
                    epoch=payload.epoch,
                    step=payload.step,
                    created_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=["task_id", "name", "epoch", "step"]
                )
            )
            result = session.execute(statement)
            stored += int(result.rowcount or 0)
    return MetricsIngestResponse(accepted=True, stored=stored)


@router.post(
    "/tasks/{task_id}/checkpoints",
    response_model=CheckpointIngestResponse,
    summary="Register a training checkpoint",
)
def ingest_checkpoint(
    task_id: str,
    payload: CheckpointIngestRequest,
    x_worker_token: str = Header(alias="X-Worker-Token"),
    ctx: AppContext = Depends(get_ctx),
) -> CheckpointIngestResponse:
    _authenticated_task(ctx, task_id, x_worker_token)
    path = Path(payload.path).resolve()
    root = ctx.settings.data_dir.resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=422, detail="checkpoint must be a file under data_dir")
    relative = path.relative_to(root).as_posix()
    now = utcnow()
    with ctx.db.session() as session:
        live = session.get(Task, task_id)
        if live is None:
            raise HTTPException(status_code=404, detail="Task not found")
        row = session.scalar(
            select(TaskCheckpoint).where(
                TaskCheckpoint.task_id == task_id,
                TaskCheckpoint.epoch == payload.epoch,
            )
        )
        if row is None:
            row = TaskCheckpoint(
                task_id=task_id,
                epoch=payload.epoch,
                path=relative,
                size_bytes=payload.size_bytes,
                metrics_json=None,
                is_best=payload.is_best,
                created_at=now,
            )
            session.add(row)
        else:
            row.path = relative
            row.size_bytes = payload.size_bytes
            row.is_best = payload.is_best
            row.created_at = now
        row.metrics_json = json.dumps(payload.metrics, ensure_ascii=False)
        live.checkpoint_epoch = max(live.checkpoint_epoch or 0, payload.epoch)
        session.flush()
        checkpoint_id = row.id
    return CheckpointIngestResponse(accepted=True, checkpoint_id=checkpoint_id)
