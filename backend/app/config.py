"""Application configuration.

All values can be overridden by environment variables with the ``LITETRAIN_``
prefix (or by a ``backend/.env`` file).
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LITETRAIN_",
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "LiteTrain"
    environment: Literal["development", "production", "test"] = "development"
    api_prefix: str = "/api/v1"
    host: str = "127.0.0.1"
    port: int = 8000

    # Storage
    data_dir: Path = BACKEND_DIR / "data"
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'platform.db').as_posix()}"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    serve_frontend: bool = True
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"

    # Scheduling
    node_id: str = "node-local-01"
    node_name: str = "local-cpu"
    total_cpus: int = Field(default=8, ge=1, le=256)
    user_cpu_quota: int = Field(default=8, ge=1, le=256)
    scheduler_interval_seconds: float = Field(default=1.0, ge=0.2, le=30)
    scheduler_lock_ttl_ms: int = Field(default=30_000, ge=2_000, le=60_000)
    default_cpu_cores: int = Field(default=4, ge=1, le=256)
    default_max_runtime_seconds: int = Field(default=1_800, ge=10, le=86_400)
    max_runtime_seconds_limit: int = Field(default=86_400, ge=60, le=604_800)
    heartbeat_interval_seconds: int = Field(default=4, ge=1, le=60)
    heartbeat_timeout_seconds: int = Field(default=25, ge=5, le=600)
    start_grace_seconds: int = Field(default=15, ge=2, le=300)
    default_max_retries: int = Field(default=0, ge=0, le=5)

    # Redis (protocol 2 also supports the Redis 5 build shipped on Windows)
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_protocol: int = Field(default=2, ge=2, le=3)
    redis_socket_timeout: float = 1.5
    redis_enabled: bool = True

    # Ray Core execution
    execution_mode: Literal["auto", "ray", "local"] = "auto"
    ray_gcs_address: str = "127.0.0.1:6380"
    ray_dashboard_url: str = "http://127.0.0.1:8265"
    ray_namespace: str = "litetrain"
    ray_connect_timeout_seconds: float = 3.0
    ray_actor_start_timeout_seconds: float = 40.0
    ray_actor_prefix: str = "litetrain-task"

    # Training runtime
    worker_python: str = ""
    worker_api_base: str = "http://127.0.0.1:8000/api/v1"
    worker_host: str = "127.0.0.1"
    ultralytics_config_dir: Path = PROJECT_ROOT / ".ultralytics"
    default_model: str = "yolov8n.pt"
    default_dataset: str = "coco128.yaml"
    log_chunk_size: int = Field(default=65_536, ge=4_096, le=1_048_576)

    @field_validator("user_cpu_quota")
    @classmethod
    def quota_within_cluster(cls, value: int, info) -> int:
        total = info.data.get("total_cpus")
        return min(value, total) if total else value

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def project_root(self) -> Path:
        return BACKEND_DIR.parent

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def weights_dir(self) -> Path:
        return self.data_dir / "weights"

    @property
    def datasets_dir(self) -> Path:
        return self.data_dir / "datasets"

    @property
    def assets_dir(self) -> Path:
        return self.data_dir / "assets"

    @property
    def matplotlib_dir(self) -> Path:
        return self.data_dir / ".matplotlib"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def resolved_worker_python(self) -> str:
        return self.worker_python or sys.executable

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.logs_dir,
            self.runs_dir,
            self.weights_dir,
            self.datasets_dir,
            self.assets_dir,
            self.matplotlib_dir,
            self.tmp_dir,
            self.ultralytics_config_dir / "Ultralytics",
        ):
            path.mkdir(parents=True, exist_ok=True)
        # Ultralytics can try to fetch Arial at runtime; on Windows a compatible
        # system font is always available and avoids a needless download.
        font_target = self.ultralytics_config_dir / "Ultralytics" / "Arial.ttf"
        if not font_target.exists() and os.name == "nt":
            source = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf"
            if source.exists():
                import shutil

                shutil.copyfile(source, font_target)


@lru_cache
def get_settings() -> Settings:
    return Settings()
