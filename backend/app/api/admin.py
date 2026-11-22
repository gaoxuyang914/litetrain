"""Administrator-only account management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import user_out
from app.api.deps import get_ctx
from app.context import AppContext
from app.schemas import AuthUserOut, UserCreateRequest, UserListResponse, UserUpdateRequest
from app.services.auth import AuthError, permissions_for

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(request: Request, ctx: AppContext = Depends(get_ctx)) -> None:
    if not ctx.settings.auth_enabled:
        return
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    if "admin.console" not in set(permissions_for(user.role)):
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _raise_auth(exc: AuthError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/users", response_model=UserListResponse, summary="List platform accounts")
def list_users(ctx: AppContext = Depends(get_ctx), _: None = Depends(require_admin)) -> UserListResponse:
    users = [user_out(user) for user in ctx.auth.list_users()]
    return UserListResponse(items=users, total=len(users))


@router.post("/users", response_model=AuthUserOut, status_code=201, summary="Create an account")
def create_user(
    payload: UserCreateRequest,
    ctx: AppContext = Depends(get_ctx),
    _: None = Depends(require_admin),
) -> AuthUserOut:
    try:
        user = ctx.auth.create_user(payload.username, payload.password, payload.role)
    except AuthError as exc:
        _raise_auth(exc)
    return user_out(user)


@router.patch("/users/{user_id}", response_model=AuthUserOut, summary="Update role, status or password")
def update_user(
    user_id: str,
    payload: UserUpdateRequest,
    ctx: AppContext = Depends(get_ctx),
    _: None = Depends(require_admin),
) -> AuthUserOut:
    try:
        user = ctx.auth.update_user(
            user_id,
            role=payload.role,
            active=payload.active,
            password=payload.password,
        )
    except AuthError as exc:
        _raise_auth(exc)
    return user_out(user)
