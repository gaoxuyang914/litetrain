"""CPU benchmark orchestration and selected profile persistence."""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import psutil
from sqlalchemy import select

from app.config import Settings
from app.database import Database, utcnow
from app.models import BenchmarkRun, Task, TaskStatus
from app.schemas import BenchmarkCreate, BenchmarkOut
from app.training.config import resolve_dataset_path, resolve_model_path

logger = logging.getLogger(__name__)


class BenchmarkError(ValueError):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class BenchmarkService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cpu-benchmark")
        self._lock = threading.RLock()
        self._active: set[str] = set()

    def recover(self) -> None:
        with self.db.session() as session:
            rows = session.scalars(
                select(BenchmarkRun).where(BenchmarkRun.status.in_(["queued", "running"]))
            ).all()
            for row in rows:
                row.status = "failed"
                row.error = "platform restarted before benchmark completed"
                row.finished_at = utcnow()
            session.flush()

    def stop(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def start(self, payload: BenchmarkCreate) -> BenchmarkOut:
        with self.db.session() as session:
            running = session.scalar(
                select(Task.id).where(Task.status == TaskStatus.RUNNING.value).limit(1)
            )
            if running is not None:
                raise BenchmarkError("stop or wait for the running training task first", 409)
        try:
            dataset_path = resolve_dataset_path(self.settings, payload.dataset)
            model_path = resolve_model_path(self.settings, payload.model)
        except Exception as exc:
            raise BenchmarkError(str(exc)) from exc
        benchmark_id = "bm-" + uuid.uuid4().hex[:12]
        workdir = self.settings.benchmarks_dir / benchmark_id
        workdir.mkdir(parents=True, exist_ok=True)
        combos = [
            {
                "threads": threads,
                "workers": workers,
                "imgsz": payload.imgsz,
                "batch": payload.batch,
                "epochs": payload.epochs,
                "cache": "none",
            }
            for threads in payload.threads[:4]
            for workers in payload.workers[:2]
        ][:8]
        host = {
            "cpu": platform.processor() or platform.machine(),
            "logical_cores": os.cpu_count(),
            "memory_gb": round(psutil.virtual_memory().total / 1024**3, 1),
            "platform": platform.platform(),
            "python": platform.python_version(),
        }
        with self.db.session() as session:
            row = BenchmarkRun(
                id=benchmark_id,
                status="running",
                dataset=str(dataset_path),
                host_json=json.dumps(host, ensure_ascii=False),
                matrix_json=json.dumps({"configs": combos}, ensure_ascii=False),
                log_path=str(workdir / "run.log"),
                created_at=utcnow(),
            )
            session.add(row)
            session.flush()
        with self._lock:
            self._active.add(benchmark_id)
        self._executor.submit(
            self._run, benchmark_id, dataset_path, model_path, combos, workdir
        )
        return self.get(benchmark_id)

    def get(self, benchmark_id: str) -> BenchmarkOut:
        with self.db.session() as session:
            row = session.get(BenchmarkRun, benchmark_id)
            if row is None:
                raise BenchmarkError("benchmark not found", 404)
            return self._to_schema(row)

    def latest(self) -> BenchmarkOut | None:
        with self.db.session() as session:
            row = session.scalar(
                select(BenchmarkRun).order_by(BenchmarkRun.created_at.desc()).limit(1)
            )
            return self._to_schema(row) if row is not None else None

    def _to_schema(self, row: BenchmarkRun) -> BenchmarkOut:
        def load(value: str | None, fallback: Any) -> Any:
            if not value:
                return fallback
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return fallback

        return BenchmarkOut(
            id=row.id,
            status=row.status,
            dataset=row.dataset,
            host=load(row.host_json, {}),
            matrix=load(row.matrix_json, {}),
            selected=load(row.selected_json, None),
            log_path=row.log_path,
            error=row.error,
            created_at=row.created_at,
            finished_at=row.finished_at,
        )

    def _run(
        self,
        benchmark_id: str,
        dataset_path: Path,
        model_path: Path,
        combos: list[dict[str, Any]],
        workdir: Path,
    ) -> None:
        log_path = workdir / "run.log"
        results: list[dict[str, Any]] = []
        try:
            with log_path.open("a", encoding="utf-8") as log:
                for index, combo in enumerate(combos):
                    output = workdir / f"combo-{index}.json"
                    command = [
                        self.settings.resolved_worker_python,
                        "-m",
                        "app.training.benchmark",
                        "--data",
                        str(dataset_path),
                        "--model",
                        str(model_path),
                        "--imgsz",
                        str(combo["imgsz"]),
                        "--batch",
                        str(combo["batch"]),
                        "--workers",
                        str(combo["workers"]),
                        "--threads",
                        str(combo["threads"]),
                        "--epochs",
                        str(combo["epochs"]),
                        "--cache",
                        str(combo["cache"]),
                        "--output",
                        str(output),
                        "--project",
                        str(workdir / "runs"),
                        "--name",
                        f"combo-{index}",
                    ]
                    log.write(f"\n$ {' '.join(command)}\n")
                    log.flush()
                    env = os.environ.copy()
                    env["PYTHONPATH"] = str(self.settings.project_root / "backend")
                    env["YOLO_CONFIG_DIR"] = str(self.settings.ultralytics_config_dir)
                    env["MPLCONFIGDIR"] = str(self.settings.matplotlib_dir)
                    try:
                        completed = subprocess.run(
                            command,
                            cwd=str(self.settings.project_root),
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            timeout=150,
                            check=False,
                        )
                        if output.is_file():
                            data = json.loads(output.read_text(encoding="utf-8"))
                        else:
                            data = {
                                "status": "failed",
                                "error": f"benchmark exited {completed.returncode} without output",
                            }
                    except subprocess.TimeoutExpired:
                        data = {"status": "failed", "error": "benchmark timed out (150s)"}
                    except Exception as exc:
                        data = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
                    data.update(combo)
                    results.append(data)
                    log.write(json.dumps(data, ensure_ascii=False) + "\n")
                    log.flush()
            usable = [
                item
                for item in results
                if item.get("status") == "succeeded" and item.get("images_per_second")
            ]
            selected = (
                max(usable, key=lambda item: float(item.get("images_per_second") or 0))
                if usable
                else None
            )
            with self.db.session() as session:
                row = session.get(BenchmarkRun, benchmark_id)
                if row is not None:
                    row.status = "succeeded" if selected else "failed"
                    row.matrix_json = json.dumps(
                        {"configs": combos, "results": results}, ensure_ascii=False
                    )
                    row.selected_json = (
                        json.dumps(selected, ensure_ascii=False) if selected else None
                    )
                    row.error = None if selected else "no benchmark configuration succeeded"
                    row.finished_at = utcnow()
                    session.flush()
        except Exception as exc:
            logger.exception("benchmark %s failed", benchmark_id)
            with self.db.session() as session:
                row = session.get(BenchmarkRun, benchmark_id)
                if row is not None:
                    row.status = "failed"
                    row.error = f"{type(exc).__name__}: {exc}"
                    row.finished_at = utcnow()
                    session.flush()
        finally:
            with self._lock:
                self._active.discard(benchmark_id)
