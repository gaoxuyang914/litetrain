"""Pipeline orchestration API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_ctx
from app.context import AppContext
from app.schemas import PipelineCreate, PipelineListResponse, PipelineOut
from app.services.pipelines import PipelineError, PipelineNotFound

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


@router.post(
    "",
    response_model=PipelineOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a serial preprocess → train → evaluate pipeline",
)
def create_pipeline(payload: PipelineCreate, ctx: AppContext = Depends(get_ctx)) -> PipelineOut:
    try:
        pipeline = ctx.pipelines.create(payload)
    except PipelineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    ctx.scheduler.wake()
    return pipeline


@router.get("", response_model=PipelineListResponse, summary="Pipeline list")
def list_pipelines(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    ctx: AppContext = Depends(get_ctx),
) -> PipelineListResponse:
    return ctx.pipelines.list(page=page, page_size=page_size)


@router.get("/{pipeline_id}", response_model=PipelineOut, summary="Pipeline detail with stages")
def get_pipeline(pipeline_id: str, ctx: AppContext = Depends(get_ctx)) -> PipelineOut:
    try:
        return ctx.pipelines.get(pipeline_id)
    except PipelineNotFound as exc:
        raise HTTPException(status_code=404, detail="pipeline not found") from exc


@router.post(
    "/{pipeline_id}/resume",
    response_model=PipelineOut,
    summary="Resume a failed or cancelled pipeline from its last successful stage",
)
def resume_pipeline(pipeline_id: str, ctx: AppContext = Depends(get_ctx)) -> PipelineOut:
    try:
        pipeline = ctx.pipelines.resume_pipeline(pipeline_id)
    except PipelineNotFound as exc:
        raise HTTPException(status_code=404, detail="pipeline not found") from exc
    except PipelineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    ctx.scheduler.wake()
    return pipeline


@router.post("/{pipeline_id}/cancel", response_model=PipelineOut, summary="Cancel a pipeline")
def cancel_pipeline(pipeline_id: str, ctx: AppContext = Depends(get_ctx)) -> PipelineOut:
    try:
        return ctx.scheduler.cancel_pipeline(pipeline_id)
    except PipelineNotFound as exc:
        raise HTTPException(status_code=404, detail="pipeline not found") from exc
    except PipelineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
