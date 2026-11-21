"""Cluster and service health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app import __version__
from app.api.deps import get_ctx
from app.context import AppContext
from app.schemas import ClusterOverview, HealthResponse

router = APIRouter(prefix="/cluster", tags=["cluster"])


@router.get("/overview", response_model=ClusterOverview, summary="CPU, node and queue overview")
def overview(ctx: AppContext = Depends(get_ctx)) -> ClusterOverview:
    return ctx.resources.overview(ctx.broker, ctx.execution, ctx.started_at)


@router.get("/health", response_model=HealthResponse, summary="Backend health")
def health(ctx: AppContext = Depends(get_ctx)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        execution_backend=ctx.execution.backend,
        redis_connected=ctx.broker.connected,
        database="ok",
    )
