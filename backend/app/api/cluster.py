"""Cluster and service health endpoints."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from app import __version__
from app.api.deps import get_ctx
from app.context import AppContext
from app.database import utcnow
from app.models import Task, TaskStatus
from app.schemas import (
    BenchmarkCreate,
    BenchmarkOut,
    ClusterOverview,
    HealthResponse,
    NodeInfo,
    NodeStatusUpdate,
    NodeTopologyRequest,
    NodeTopologyResponse,
    TaskDistributionOut,
)
from app.services.benchmark import BenchmarkError
from app.services.resources import ResourceError

router = APIRouter(prefix="/cluster", tags=["cluster"])

DISTRIBUTION_WINDOWS = {
    "5m": ("近 5 分钟", 5 * 60),
    "15m": ("近 15 分钟", 15 * 60),
    "30m": ("近 30 分钟", 30 * 60),
    "1h": ("近 1 小时", 60 * 60),
    "3h": ("近 3 小时", 3 * 60 * 60),
    "6h": ("近 6 小时", 6 * 60 * 60),
    "12h": ("近 12 小时", 12 * 60 * 60),
    "24h": ("近 24 小时", 24 * 60 * 60),
    "7d": ("近 7 天", 7 * 24 * 60 * 60),
    "30d": ("近 30 天", 30 * 24 * 60 * 60),
    "90d": ("近 90 天", 90 * 24 * 60 * 60),
    "180d": ("近半年", 180 * 24 * 60 * 60),
    "all": ("全部时间", None),
}


@router.get(
    "/task-distribution",
    response_model=TaskDistributionOut,
    summary="Task status distribution for a time window",
)
def task_distribution(
    window: str = Query(default="all", pattern="^(5m|15m|30m|1h|3h|6h|12h|24h|7d|30d|90d|180d|all)$"),
    ctx: AppContext = Depends(get_ctx),
) -> TaskDistributionOut:
    label, seconds = DISTRIBUTION_WINDOWS[window]
    generated = utcnow()
    since = generated - timedelta(seconds=seconds) if seconds is not None else None
    counts = {status.value: 0 for status in TaskStatus}
    with ctx.db.session() as session:
        statement = select(Task.status, func.count(Task.id)).group_by(Task.status)
        if since is not None:
            statement = statement.where(Task.submitted_at >= since)
        for status, count in session.execute(statement):
            counts[status] = int(count)
    return TaskDistributionOut(
        window=window,
        label=label,
        since=since,
        generated_at=generated,
        total=sum(counts.values()),
        counts=counts,
    )


@router.get("/overview", response_model=ClusterOverview, summary="CPU, node and queue overview")
def overview(ctx: AppContext = Depends(get_ctx)) -> ClusterOverview:
    return ctx.resources.overview(ctx.broker, ctx.execution, ctx.started_at)


@router.get("/nodes", response_model=list[NodeInfo], summary="All simulated/real nodes")
def nodes(ctx: AppContext = Depends(get_ctx)) -> list[NodeInfo]:
    return ctx.resources.overview(ctx.broker, ctx.execution, ctx.started_at).nodes


@router.post(
    "/nodes/topology",
    response_model=NodeTopologyResponse,
    summary="Replace the cluster with a simulated multi-node topology",
)
def apply_topology(
    payload: NodeTopologyRequest,
    ctx: AppContext = Depends(get_ctx),
) -> NodeTopologyResponse:
    try:
        ctx.scheduler.apply_node_topology(
            [(item.name, item.total_cpus) for item in payload.nodes]
        )
    except ResourceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    overview_data = ctx.resources.overview(ctx.broker, ctx.execution, ctx.started_at)
    return NodeTopologyResponse(nodes=overview_data.nodes, total_cpus=overview_data.cpu.total)


@router.patch("/nodes/{node_id}", response_model=NodeInfo, summary="Set a node online/offline")
def set_node_status(
    node_id: str,
    payload: NodeStatusUpdate,
    ctx: AppContext = Depends(get_ctx),
) -> NodeInfo:
    try:
        ctx.scheduler.set_node_status(node_id, payload.status)
    except ResourceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    overview_data = ctx.resources.overview(ctx.broker, ctx.execution, ctx.started_at)
    for node in overview_data.nodes:
        if node.id == node_id:
            return node
    raise HTTPException(status_code=404, detail="node not found")


@router.post(
    "/benchmarks",
    response_model=BenchmarkOut,
    status_code=201,
    summary="Start a short CPU training benchmark",
)
def start_benchmark(
    payload: BenchmarkCreate,
    ctx: AppContext = Depends(get_ctx),
) -> BenchmarkOut:
    try:
        return ctx.benchmark.start(payload)
    except BenchmarkError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get(
    "/benchmarks/latest",
    response_model=BenchmarkOut | None,
    summary="Latest CPU benchmark and selected profile",
)
def latest_benchmark(ctx: AppContext = Depends(get_ctx)) -> BenchmarkOut | None:
    return ctx.benchmark.latest()


@router.get(
    "/benchmarks/{benchmark_id}",
    response_model=BenchmarkOut,
    summary="CPU benchmark detail",
)
def get_benchmark(benchmark_id: str, ctx: AppContext = Depends(get_ctx)) -> BenchmarkOut:
    try:
        return ctx.benchmark.get(benchmark_id)
    except BenchmarkError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/health", response_model=HealthResponse, summary="Backend health")
def health(ctx: AppContext = Depends(get_ctx)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        execution_backend=ctx.execution.backend,
        redis_connected=ctx.broker.connected,
        database="ok",
    )
