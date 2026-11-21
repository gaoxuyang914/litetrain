"""Task-facing REST API and Server-Sent-Events log stream."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse

from app.api.deps import get_ctx
from app.context import AppContext
from app.models import TaskStatus
from app.schemas import (
    CancelResponse,
    LogChunk,
    ResumeTaskRequest,
    RunManifestOut,
    TaskCheckpointListResponse,
    TaskCompareResponse,
    TaskCreate,
    TaskEventsResponse,
    TaskListResponse,
    TaskMetricsResponse,
    TaskOptions,
    TaskOut,
    TaskPreviewResponse,
)
from app.services.scheduler import TaskConflictError
from app.services.tasks import TaskNotFoundError, TaskValidationError

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _not_found(task_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Task '{task_id}' not found")


@router.get("/options", response_model=TaskOptions, summary="Form options and limits")
def task_options(ctx: AppContext = Depends(get_ctx)) -> TaskOptions:
    return ctx.tasks.options(ctx.execution.backend)


@router.post("/preview", response_model=TaskPreviewResponse, summary="Preview the worker command")
def preview_task(payload: TaskCreate, ctx: AppContext = Depends(get_ctx)) -> TaskPreviewResponse:
    try:
        return ctx.tasks.preview(payload)
    except TaskValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post(
    "",
    response_model=TaskOut,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a YOLO CPU training task",
)
def create_task(payload: TaskCreate, ctx: AppContext = Depends(get_ctx)) -> TaskOut:
    try:
        task = ctx.tasks.create(payload)
    except TaskValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    ctx.scheduler.wake()
    return task


@router.get("", response_model=TaskListResponse, summary="Paginated task list")
def list_tasks(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, max_length=120),
    sort_by: str = Query(default="submitted_at"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    ctx: AppContext = Depends(get_ctx),
) -> TaskListResponse:
    try:
        return ctx.tasks.list(
            page=page,
            page_size=page_size,
            status=status_filter,
            query=q,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except TaskValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/compare", response_model=TaskCompareResponse, summary="Compare 2–4 runs")
def compare_tasks(
    ids: str = Query(..., min_length=1, max_length=200),
    ctx: AppContext = Depends(get_ctx),
) -> TaskCompareResponse:
    task_ids = [item.strip() for item in ids.split(",") if item.strip()]
    try:
        return ctx.tasks.compare(task_ids)
    except TaskNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except TaskValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/{task_id}", response_model=TaskOut, summary="Task detail")
def get_task(task_id: str, ctx: AppContext = Depends(get_ctx)) -> TaskOut:
    try:
        return ctx.tasks.get(task_id)
    except TaskNotFoundError as exc:
        raise _not_found(task_id) from exc


@router.get(
    "/{task_id}/manifest",
    response_model=RunManifestOut,
    summary="Reproducible run manifest",
)
def get_task_manifest(task_id: str, ctx: AppContext = Depends(get_ctx)) -> RunManifestOut:
    try:
        return ctx.tasks.manifest(task_id)
    except TaskNotFoundError as exc:
        raise _not_found(task_id) from exc
    except TaskValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get(
    "/{task_id}/checkpoints",
    response_model=TaskCheckpointListResponse,
    summary="Checkpoints saved by a task",
)
def get_task_checkpoints(
    task_id: str, ctx: AppContext = Depends(get_ctx)
) -> TaskCheckpointListResponse:
    try:
        return ctx.tasks.checkpoints(task_id)
    except TaskNotFoundError as exc:
        raise _not_found(task_id) from exc


@router.post(
    "/{task_id}/resume",
    response_model=TaskOut,
    status_code=status.HTTP_201_CREATED,
    summary="Resume from a checkpoint",
)
def resume_task(
    task_id: str,
    payload: ResumeTaskRequest,
    ctx: AppContext = Depends(get_ctx),
) -> TaskOut:
    try:
        task = ctx.tasks.resume(task_id, payload.mode)
        ctx.scheduler.wake()
        return task
    except TaskNotFoundError as exc:
        raise _not_found(task_id) from exc
    except TaskValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/{task_id}/cancel", response_model=CancelResponse, summary="Cancel a task")
def cancel_task(task_id: str, ctx: AppContext = Depends(get_ctx)) -> CancelResponse:
    try:
        return ctx.scheduler.cancel(task_id)
    except TaskNotFoundError as exc:
        raise _not_found(task_id) from exc
    except TaskConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{task_id}/logs", response_model=LogChunk, summary="Incremental log read")
def read_logs(
    task_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1024, le=1_048_576),
    ctx: AppContext = Depends(get_ctx),
) -> LogChunk:
    try:
        task = ctx.tasks.get(task_id)
    except TaskNotFoundError as exc:
        raise _not_found(task_id) from exc
    window = ctx.log_store.read_chunk(
        task_id, offset=offset, limit=limit or ctx.settings.log_chunk_size
    )
    return LogChunk(
        task_id=task_id,
        start_offset=window.start_offset,
        next_offset=window.next_offset,
        content=window.content,
        eof=window.eof,
        file_size=window.file_size,
        task_status=task.status,
    )


@router.get("/{task_id}/logs/download", summary="Download the full task log")
def download_logs(task_id: str, ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    try:
        ctx.tasks.get(task_id)
    except TaskNotFoundError as exc:
        raise _not_found(task_id) from exc
    path = ctx.log_store.ensure(task_id)
    return FileResponse(
        path,
        media_type="text/plain; charset=utf-8",
        filename=f"{task_id}.log",
    )


@router.get("/{task_id}/logs/stream", summary="SSE incremental log stream")
async def stream_logs(
    request: Request,
    task_id: str,
    offset: int = Query(default=0, ge=0),
    ctx: AppContext = Depends(get_ctx),
) -> StreamingResponse:
    try:
        ctx.tasks.get(task_id)
    except TaskNotFoundError as exc:
        raise _not_found(task_id) from exc

    async def generate():
        cursor = offset
        last_keepalive = asyncio.get_event_loop().time()
        terminal_seen_empty = 0
        yield f"retry: 2000\nevent: ready\ndata: {json.dumps({'offset': cursor})}\n\n"
        while True:
            if await request.is_disconnected():
                break
            window = await asyncio.to_thread(
                ctx.log_store.read_chunk,
                task_id,
                cursor,
                ctx.settings.log_chunk_size,
            )
            if window.content:
                terminal_seen_empty = 0
                cursor = window.next_offset
                payload = {
                    "offset": cursor,
                    "content": window.content,
                    "file_size": window.file_size,
                }
                yield f"event: log\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                continue
            try:
                task = await asyncio.to_thread(ctx.tasks.get, task_id)
                terminal = task.status in {
                    TaskStatus.SUCCEEDED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                }
            except TaskNotFoundError:
                terminal = True
            if terminal and window.eof:
                terminal_seen_empty += 1
                if terminal_seen_empty >= 2:
                    yield f"event: end\ndata: {json.dumps({'offset': cursor, 'file_size': window.file_size})}\n\n"
                    break
            now = asyncio.get_event_loop().time()
            if now - last_keepalive > 12:
                last_keepalive = now
                yield ": keepalive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{task_id}/events", response_model=TaskEventsResponse, summary="Task lifecycle events")
def task_events(
    task_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    ctx: AppContext = Depends(get_ctx),
) -> TaskEventsResponse:
    try:
        return ctx.tasks.events(task_id, limit=limit)
    except TaskNotFoundError as exc:
        raise _not_found(task_id) from exc


@router.get("/{task_id}/metrics", response_model=TaskMetricsResponse, summary="YOLO + resource metrics")
def task_metrics(task_id: str, ctx: AppContext = Depends(get_ctx)) -> TaskMetricsResponse:
    try:
        return ctx.tasks.metrics(task_id)
    except TaskNotFoundError as exc:
        raise _not_found(task_id) from exc
