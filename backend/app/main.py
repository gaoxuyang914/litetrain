"""FastAPI application entry point."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.admin import router as admin_router
from app.api.assets import artifacts_router, datasets_router
from app.api.auth import router as auth_router
from app.api.cluster import router as cluster_router
from app.api.internal import router as internal_router
from app.api.pipelines import router as pipelines_router
from app.api.settings import router as settings_router
from app.api.tasks import router as tasks_router
from app.api.webhooks import router as webhooks_router
from app.config import Settings, get_settings
from app.context import AppContext
from app.services.auth import permissions_for

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    level = getattr(logging, os.environ.get("LITETRAIN_LOG_LEVEL", "INFO").upper(), logging.INFO)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("ray").setLevel(logging.WARNING)


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    dist = Path(settings.frontend_dist)
    index = dist / "index.html"
    if not settings.serve_frontend or not index.is_file():
        return
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    logger.info("serving built frontend from %s", dist)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):  # noqa: ANN202
        if full_path.startswith(("api/", "docs", "redoc", "openapi.json")):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (dist / full_path).resolve()
        try:
            candidate.relative_to(dist.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found")
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


def create_app(
    settings: Settings | None = None,
    *,
    start_scheduler: bool = True,
) -> FastAPI:
    _configure_logging()
    settings = settings or get_settings()
    ctx = AppContext.build(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        logger.info("starting %s v%s", settings.app_name, __version__)
        ctx.initialize()
        if start_scheduler:
            ctx.scheduler.start()
            ctx.webhook_dispatcher.start()
        logger.info(
            "ready · api=http://%s:%s · executor=%s · redis=%s",
            settings.host,
            settings.port,
            ctx.execution.backend,
            "connected" if ctx.broker.connected else "unavailable",
        )
        try:
            yield
        finally:
            logger.info("shutting down")
            ctx.shutdown()

    app = FastAPI(
        title="LiteTrain API",
        version=__version__,
        description=(
            "A lightweight CPU YOLO training control plane: FastAPI + SQLite "
            "for durable state, Redis for scheduler coordination, and Ray "
            "detached actors for isolated training execution."
        ),
        lifespan=lifespan,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.state.ctx = ctx
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    prefix = settings.api_prefix
    app.include_router(auth_router, prefix=prefix)
    app.include_router(admin_router, prefix=prefix)
    app.include_router(tasks_router, prefix=prefix)
    app.include_router(cluster_router, prefix=prefix)
    app.include_router(internal_router, prefix=prefix)
    app.include_router(pipelines_router, prefix=prefix)
    app.include_router(datasets_router, prefix=prefix)
    app.include_router(artifacts_router, prefix=prefix)
    app.include_router(webhooks_router, prefix=prefix)
    app.include_router(settings_router, prefix=prefix)

    public_api = {
        f"{prefix}/auth/login",
    }

    def required_permission(method: str, path: str) -> str:
        if path.startswith(f"{prefix}/admin"):
            return "admin.console"
        if path == f"{prefix}/settings":
            return "admin.settings"
        if method in {"GET", "HEAD", "OPTIONS"}:
            return "platform.view"
        if path.startswith(f"{prefix}/tasks"):
            if any(path.endswith(suffix) for suffix in ("/cancel", "/rerun", "/resume")):
                return "platform.operate"
            return "platform.submit"
        if path.startswith(f"{prefix}/pipelines"):
            if path.endswith("/cancel"):
                return "platform.operate"
            return "platform.submit"
        if path.startswith(f"{prefix}/cluster"):
            return "platform.operate"
        if path.startswith(
            (
                f"{prefix}/datasets",
                f"{prefix}/artifacts",
                f"{prefix}/webhooks",
            )
        ):
            return "platform.assets"
        return "platform.operate"

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):  # noqa: ANN001, ANN202
        path = request.url.path
        if request.method == "OPTIONS" or not path.startswith(f"{prefix}/"):
            return await call_next(request)
        if path in public_api or path.startswith(f"{prefix}/internal"):
            return await call_next(request)
        if not settings.auth_enabled:
            request.state.user = None
            request.state.auth_token = None
            return await call_next(request)

        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        admin_path = path.startswith(f"{prefix}/admin") or path == f"{prefix}/settings"
        if scheme.lower() != "bearer" or not token:
            token = "" if admin_path else (request.cookies.get("litetrain_session") or "")
        if not token:
            return JSONResponse(
                status_code=401,
                content={"detail": "请先登录"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        user = ctx.auth.authenticate(token)
        if user is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "登录已失效，请重新登录"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        required = required_permission(request.method, path)
        if required not in set(permissions_for(user.role)):
            return JSONResponse(
                status_code=403,
                content={"detail": f"当前账号缺少权限：{required}"},
            )
        request.state.user = user
        request.state.auth_token = token
        return await call_next(request)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "version": __version__,
                "executor": ctx.execution.backend,
                "redis": ctx.broker.connected,
            }
        )

    @app.get(f"{prefix}/version", tags=["system"], summary="Build/version information")
    def version_info() -> dict:
        return {
            "version": __version__,
            "api_prefix": prefix,
            "execution": ctx.execution.health(),
            "redis_connected": ctx.broker.connected,
            "scheduler": ctx.scheduler.health(),
        }

    @app.exception_handler(Exception)
    async def unhandled_exception(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled API error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "type": type(exc).__name__},
        )

    _mount_frontend(app, settings)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    runtime = get_settings()
    uvicorn.run(
        "app.main:app",
        host=runtime.host,
        port=runtime.port,
        reload=False,
    )
