"""Webhook subscription and delivery inspection API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_ctx
from app.context import AppContext
from app.schemas import (
    WebhookCreate,
    WebhookDeliveryList,
    WebhookEndpointOut,
    WebhookListResponse,
    WebhookUpdate,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("", response_model=WebhookEndpointOut, status_code=201, summary="Register a webhook")
def create_webhook(payload: WebhookCreate, ctx: AppContext = Depends(get_ctx)) -> WebhookEndpointOut:
    return ctx.webhooks.create(payload)


@router.get("", response_model=WebhookListResponse, summary="Webhook endpoints")
def list_webhooks(ctx: AppContext = Depends(get_ctx)) -> WebhookListResponse:
    return ctx.webhooks.list()


@router.patch("/{endpoint_id}", response_model=WebhookEndpointOut, summary="Enable/disable a webhook")
def update_webhook(
    endpoint_id: str,
    payload: WebhookUpdate,
    ctx: AppContext = Depends(get_ctx),
) -> WebhookEndpointOut:
    try:
        return ctx.webhooks.update_active(endpoint_id, payload.active)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="webhook not found") from exc


@router.delete("/{endpoint_id}", status_code=204, summary="Delete a webhook")
def delete_webhook(endpoint_id: str, ctx: AppContext = Depends(get_ctx)) -> None:
    try:
        ctx.webhooks.delete(endpoint_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="webhook not found") from exc


@router.post("/{endpoint_id}/test", summary="Queue a signed webhook test delivery")
def test_webhook(endpoint_id: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    try:
        delivery = ctx.webhooks.enqueue_test(endpoint_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="webhook not found") from exc
    return {
        "accepted": True,
        "delivery_id": delivery.id,
        "message": "test delivery queued; it will be sent by the webhook dispatcher",
    }


@router.get("/deliveries", response_model=WebhookDeliveryList, summary="Recent webhook deliveries")
def list_deliveries(
    endpoint_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: AppContext = Depends(get_ctx),
) -> WebhookDeliveryList:
    items = ctx.webhooks.deliveries(endpoint_id=endpoint_id, limit=limit)
    return WebhookDeliveryList(items=items, total=len(items))
