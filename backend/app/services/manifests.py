"""Run manifests capture the data, code and environment behind a task."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import subprocess
from importlib import metadata
from pathlib import Path
from typing import Any

import psutil
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import utcnow
from app.models import DatasetRecord, RunManifest, Task
from app.schemas import RunManifestOut

logger = logging.getLogger(__name__)


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _git(settings: Settings) -> tuple[str | None, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=settings.project_root,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=settings.project_root,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout.strip()
        return (commit or None, bool(dirty))
    except Exception:
        return (None, True)


def _config_for(task: Task) -> dict[str, Any]:
    if task.config_json:
        try:
            loaded = json.loads(task.config_json)
            if isinstance(loaded, dict):
                return loaded
        except json.JSONDecodeError:
            pass
    return {
        "model": task.model,
        "dataset": task.dataset,
        "epochs": task.epochs,
        "imgsz": task.imgsz,
        "batch_size": task.batch_size,
        "workers": task.workers,
        "cpu_cores": task.cpu_cores,
        "compute_threads": task.compute_threads,
        "cache_mode": task.cache_mode,
        "seed": task.seed,
        "patience": task.patience,
        "save_period": task.save_period,
    }


class RunManifestService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build(
        self,
        task: Task,
        *,
        model_path: Path | None,
        dataset: DatasetRecord | None,
    ) -> dict[str, Any]:
        commit, dirty = _git(self.settings)
        model_sha = sha256_file(model_path) if model_path and model_path.is_file() else None
        memory = psutil.virtual_memory()
        manifest = {
            "task_id": task.id,
            "name": task.name,
            "dataset": {
                "id": dataset.id if dataset else task.dataset_id,
                "name": dataset.name if dataset else None,
                "version": dataset.version if dataset else None,
                "content_sha256": dataset.content_sha256 if dataset else task.dataset_sha256,
                "quality_status": dataset.quality_status if dataset else "unknown",
                "yaml": dataset.yaml_path if dataset else task.dataset,
            },
            "model": {
                "name": task.model,
                "sha256": model_sha,
            },
            "config": _config_for(task),
            "runtime": {
                "python": platform.python_version(),
                "torch": _version("torch"),
                "ultralytics": _version("ultralytics"),
                "ray": _version("ray"),
                "fastapi": _version("fastapi"),
                "platform": platform.platform(),
                "cpu": platform.processor() or platform.machine(),
                "logical_cores": os.cpu_count(),
                "memory_gb": round(memory.total / 1024**3, 1),
                "git_commit": commit,
                "git_dirty": dirty,
            },
            "execution": {
                "executor": task.executor,
                "cpu_cores": task.cpu_cores,
                "max_runtime_seconds": task.max_runtime_seconds,
            },
            "created_at": utcnow().isoformat() + "Z",
        }
        return manifest

    def persist(
        self,
        session: Session,
        task: Task,
        *,
        model_path: Path | None,
        dataset: DatasetRecord | None,
    ) -> RunManifestOut:
        manifest = self.build(task, model_path=model_path, dataset=dataset)
        row = RunManifest(
            task_id=task.id,
            manifest_json=json.dumps(manifest, ensure_ascii=False),
            dataset_sha256=(dataset.content_sha256 if dataset else task.dataset_sha256),
            model_sha256=manifest["model"]["sha256"],
            git_commit=manifest["runtime"]["git_commit"],
            created_at=utcnow(),
        )
        session.merge(row)
        task.dataset_sha256 = row.dataset_sha256
        session.flush()
        return RunManifestOut(
            task_id=task.id,
            manifest=manifest,
            dataset_sha256=row.dataset_sha256,
            model_sha256=row.model_sha256,
            git_commit=row.git_commit,
            created_at=row.created_at,
        )

    def get(self, session: Session, task_id: str) -> RunManifestOut | None:
        row = session.get(RunManifest, task_id)
        if row is None:
            return None
        try:
            manifest = json.loads(row.manifest_json)
        except json.JSONDecodeError:
            manifest = {}
        return RunManifestOut(
            task_id=row.task_id,
            manifest=manifest,
            dataset_sha256=row.dataset_sha256,
            model_sha256=row.model_sha256,
            git_commit=row.git_commit,
            created_at=row.created_at,
        )
