"""Authentication endpoints and request-level user helpers."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.deps import get_ctx
from app.context import AppContext
from app.models import User
from app.schemas import AuthUserOut, LoginRequest, LoginResponse
from app.services.auth import AuthError, permissions_for

router = APIRouter(prefix="/auth", tags=["auth"])
SESSION_COOKIE = "litetrain_session"


def user_out(user: User) -> AuthUserOut:
    return AuthUserOut(
        id=user.id,
        username=user.username,
        role=user.role,
        permissions=permissions_for(user.role),
        active=user.active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def request_user(request: Request, ctx: AppContext) -> User:
    user = getattr(request.state, "user", None)
    if user is None:
        if not ctx.settings.auth_enabled:
            raise HTTPException(status_code=401, detail="当前环境未启用登录")
        raise HTTPException(status_code=401, detail="请先登录")
    return user


@router.post("/login", response_model=LoginResponse, summary="Sign in to LiteTrain")
def login(
    payload: LoginRequest,
    response: Response,
    ctx: AppContext = Depends(get_ctx),
) -> LoginResponse:
    try:
        token, user, expires_at = ctx.auth.login(payload.username, payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if payload.client == "platform":
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            max_age=max(60, ctx.settings.auth_session_hours * 3600),
            path="/",
        )
    return LoginResponse(token=token, expires_at=expires_at, user=user_out(user))


@router.get("/me", response_model=AuthUserOut, summary="Current signed-in user")
def me(request: Request, ctx: AppContext = Depends(get_ctx)) -> AuthUserOut:
    return user_out(request_user(request, ctx))


@router.post("/logout", summary="Sign out the current session")
def logout(request: Request, response: Response, ctx: AppContext = Depends(get_ctx)) -> dict[str, bool]:
    token = getattr(request.state, "auth_token", None)
    ctx.auth.logout(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}
