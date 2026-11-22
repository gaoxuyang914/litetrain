"""Runtime policy settings for the scheduling watchdog and task defaults."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_ctx
from app.context import AppContext
from app.schemas import RuntimeSettingsOut, RuntimeSettingsUpdate

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=RuntimeSettingsOut, summary="Read runtime policy settings")
def get_runtime_settings(ctx: AppContext = Depends(get_ctx)) -> RuntimeSettingsOut:
    return ctx.runtime_settings.snapshot()


@router.patch("", response_model=RuntimeSettingsOut, summary="Update runtime policy settings")
def update_runtime_settings(
    payload: RuntimeSettingsUpdate,
    ctx: AppContext = Depends(get_ctx),
) -> RuntimeSettingsOut:
    try:
        return ctx.runtime_settings.update(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
