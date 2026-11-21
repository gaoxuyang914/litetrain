"""Cluster and service health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app import __version__
from app.api.deps import get_ctx
from app.context import AppContext
from app.schemas import (
    ClusterOverview,
    HealthResponse,
    NodeInfo,
    NodeStatusUpdate,
    NodeTopologyRequest,
    NodeTopologyResponse,
)
from app.services.resources import ResourceError

router = APIRouter(prefix="/cluster", tags=["cluster"])


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


@router.get("/health", response_model=HealthResponse, summary="Backend health")
def health(ctx: AppContext = Depends(get_ctx)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        execution_backend=ctx.execution.backend,
        redis_connected=ctx.broker.connected,
        database="ok",
    )
