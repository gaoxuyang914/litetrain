"""Execution backends: Ray actors (preferred) or local subprocesses.

Both backends run the same ``app.training.worker`` process.  The actor is
detached and named after the task attempt, which lets the control plane
reattach after a restart and keeps Ray's own CPU accounting in sync with the
platform allocator.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from app.config import BACKEND_DIR, Settings
from app.models import Task
from app.training.config import build_worker_command

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LaunchInfo:
    executor: str
    pid: int
    pid_create_time: float
    actor_name: str | None
    log_path: str
    result_path: str


def _read_json(path: str | Path) -> dict[str, Any] | None:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _pid_alive(pid: int | None, create_time: float | None) -> bool:
    if not pid:
        return False
    try:
        process = psutil.Process(pid)
        if create_time is not None and abs(process.create_time() - create_time) > 0.75:
            return False
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def _terminate_pid_tree(pid: int | None, create_time: float | None, timeout: float = 7.0) -> bool:
    if not pid:
        return False
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False
    if create_time is not None:
        try:
            if abs(process.create_time() - create_time) > 0.75:
                return False
        except psutil.Error:
            pass
    try:
        children = process.children(recursive=True)
    except psutil.Error:
        children = []
    for child in children:
        try:
            child.terminate()
        except psutil.Error:
            pass
    try:
        process.terminate()
    except psutil.Error:
        pass
    _gone, alive = psutil.wait_procs([process, *children], timeout=timeout)
    for item in alive:
        try:
            item.kill()
        except psutil.Error:
            pass
    return True


class ExecutionManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.backend = "local"
        self.requested_mode = settings.execution_mode
        self.ray_connected = False
        self.ray_version: str | None = None
        self.ray_cpus: float | None = None
        self.ray_address = settings.ray_gcs_address
        self.connection_error: str | None = None
        self._ray: Any = None
        self._actor_cls: Any = None
        self._namespace = settings.ray_namespace
        self._local_processes: dict[str, subprocess.Popen[bytes]] = {}

    # ------------------------------------------------------------------ setup
    def connect(self) -> None:
        if self.requested_mode == "local":
            self.backend = "local"
            logger.info("execution backend: local (explicit)")
            return
        try:
            self._connect_ray()
            self.backend = "ray"
            logger.info("execution backend: Ray (%s, %s CPUs)", self.ray_address, self.ray_cpus)
        except Exception as exc:
            self.backend = "local"
            self.ray_connected = False
            self.connection_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Ray unavailable, falling back to local executor: %s", self.connection_error)

    def _connect_ray(self) -> None:
        host, _, port_text = self.settings.ray_gcs_address.rpartition(":")
        if not host:
            host, port_text = self.settings.ray_gcs_address, "6379"
        port = int(port_text)
        with socket.create_connection((host, port), timeout=self.settings.ray_connect_timeout_seconds):
            pass

        os.environ.setdefault("RAY_ENABLE_UV_RUN_RUNTIME_ENV", "0")
        os.environ.setdefault("RAY_AUTH_MODE", "disabled")
        import ray
        from app.services.ray_actor import TrainingActor

        ray.init(
            address=self.settings.ray_gcs_address,
            namespace=self._namespace,
            ignore_reinit_error=True,
            log_to_driver=False,
            include_dashboard=False,
        )
        resources = ray.cluster_resources()
        self._ray = ray
        self._actor_cls = ray.remote(TrainingActor)
        self.ray_connected = True
        self.ray_version = getattr(ray, "__version__", None)
        self.ray_cpus = float(resources.get("CPU", 0) or 0)
        self.connection_error = None

    # ------------------------------------------------------------- utilities
    @property
    def native(self) -> str:
        return self.backend

    def actor_name(self, task: Task) -> str:
        return f"{self.settings.ray_actor_prefix}-{task.id}-a{task.retry_count}"

    def _environment(self, task: Task) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": os.pathsep.join(
                    [str(BACKEND_DIR), env.get("PYTHONPATH", "")]
                ).rstrip(os.pathsep),
                "KMP_DUPLICATE_LIB_OK": "TRUE",
                "OMP_NUM_THREADS": str(max(1, task.cpu_cores)),
                "MKL_NUM_THREADS": str(max(1, task.cpu_cores)),
                "YOLO_CONFIG_DIR": str(self.settings.ultralytics_config_dir),
                "MPLCONFIGDIR": str(self.settings.matplotlib_dir),
                "LITETRAIN_WORKER_API_BASE": self.settings.worker_api_base,
                "LITETRAIN_WORKER_TOKEN": task.worker_token,
                "LITETRAIN_RESULT_PATH": task.result_path,
                "LITETRAIN_HEARTBEAT_INTERVAL": str(self.settings.heartbeat_interval_seconds),
                "LITETRAIN_TASK_ID": task.id,
                "TQDM_DISABLE": "1",
            }
        )
        return env

    def _command(self, task: Task) -> list[str]:
        return build_worker_command(
            self.settings,
            task_id=task.id,
            model=task.model,
            dataset=task.dataset,
            epochs=task.epochs,
            imgsz=task.imgsz,
            batch_size=task.batch_size,
            workers=task.workers,
            cpu_cores=task.cpu_cores,
            run_dir=Path(task.result_path).parent,
        )

    def _ensure_workdir(self, task: Task) -> Path:
        run_dir = Path(task.result_path).parent
        run_dir.mkdir(parents=True, exist_ok=True)
        Path(task.log_path).parent.mkdir(parents=True, exist_ok=True)
        return run_dir

    # -------------------------------------------------------------- launching
    def launch(self, task: Task) -> LaunchInfo:
        self._ensure_workdir(task)
        command = self._command(task)
        env = self._environment(task)
        if self.backend == "ray":
            try:
                return self._launch_ray(task, command, env)
            except Exception:
                raise
        return self._launch_local(task, command, env)

    def _launch_local(self, task: Task, command: list[str], env: dict[str, str]) -> LaunchInfo:
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        handle = open(task.log_path, "ab", buffering=0)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.settings.project_root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                close_fds=True,
            )
        finally:
            handle.close()
        self._local_processes[task.id] = process
        try:
            create_time = psutil.Process(process.pid).create_time()
        except psutil.Error:
            create_time = 0.0
        return LaunchInfo(
            executor="local",
            pid=process.pid,
            pid_create_time=create_time,
            actor_name=None,
            log_path=task.log_path,
            result_path=task.result_path,
        )

    def _launch_ray(self, task: Task, command: list[str], env: dict[str, str]) -> LaunchInfo:
        ray = self._ray
        if not self.ray_connected or ray is None or self._actor_cls is None:
            raise RuntimeError("Ray backend requested but not connected")
        name = self.actor_name(task)
        self._kill_actor_by_name(name, missing_ok=True)
        actor = self._actor_cls.options(
            num_cpus=task.cpu_cores,
            name=name,
            lifetime="detached",
            max_restarts=0,
            runtime_env={
                "env_vars": {
                    "PYTHONPATH": str(BACKEND_DIR),
                    "RAY_ENABLE_UV_RUN_RUNTIME_ENV": "0",
                }
            },
        ).remote()
        try:
            info = ray.get(
                actor.start.remote(
                    command,
                    str(self.settings.project_root),
                    env,
                    task.log_path,
                ),
                timeout=self.settings.ray_actor_start_timeout_seconds,
            )
        except Exception:
            try:
                ray.kill(actor, no_restart=True)
            except Exception:
                pass
            raise
        return LaunchInfo(
            executor="ray",
            pid=int(info.get("pid") or 0),
            pid_create_time=float(info.get("create_time") or 0.0),
            actor_name=name,
            log_path=task.log_path,
            result_path=task.result_path,
        )

    # ---------------------------------------------------------------- probing
    def result(self, task: Task) -> dict[str, Any] | None:
        return _read_json(task.result_path)

    def process_alive(self, task: Task) -> bool:
        return _pid_alive(task.pid, task.pid_create_time)

    def actor_alive(self, task: Task) -> bool:
        if self.backend != "ray" or not task.ray_actor_name:
            return False
        try:
            actor = self._ray.get_actor(task.ray_actor_name, namespace=self._namespace)
            self._ray.get(actor.ping.remote(), timeout=2.0)
            return True
        except Exception:
            return False

    def has_live_evidence(self, task: Task, *, started_within_grace: bool = False) -> bool:
        if self.result(task) is not None:
            return False
        if self.process_alive(task):
            return True
        if self.actor_alive(task):
            return True
        return started_within_grace and not task.pid

    # ------------------------------------------------------------ termination
    def stop(self, task: Task, *, reason: str = "") -> None:
        if task.executor == "ray" or (task.executor is None and self.backend == "ray"):
            self._stop_ray(task)
        else:
            self._stop_local(task)
        # Always defensively terminate a recorded PID as well in case the actor
        # handle was lost.
        _terminate_pid_tree(task.pid, task.pid_create_time)

    def _stop_local(self, task: Task) -> None:
        process = self._local_processes.pop(task.id, None)
        if process is not None:
            if process.poll() is None:
                _terminate_pid_tree(process.pid, None)
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
        else:
            _terminate_pid_tree(task.pid, task.pid_create_time)

    def _stop_ray(self, task: Task) -> None:
        name = task.ray_actor_name or self.actor_name(task)
        actor = None
        try:
            actor = self._ray.get_actor(name, namespace=self._namespace)
        except Exception:
            actor = None
        if actor is not None:
            try:
                self._ray.get(actor.stop.remote(force=True), timeout=10.0)
            except Exception:
                logger.warning("Ray actor stop timed out for task %s", task.id)
            try:
                self._ray.kill(actor, no_restart=True)
            except Exception:
                pass

    def cleanup(self, task: Task) -> None:
        """Release executor-side handles after the task is terminal."""
        if task.executor == "ray" or (task.executor is None and self.backend == "ray"):
            name = task.ray_actor_name or self.actor_name(task)
            self._kill_actor_by_name(name, missing_ok=True)
            # A detached actor can be killed while its child survives briefly;
            # make sure the worker tree does not outlive the durable result.
            _terminate_pid_tree(task.pid, task.pid_create_time)
        else:
            process = self._local_processes.pop(task.id, None)
            if process is not None and process.poll() is None:
                _terminate_pid_tree(process.pid, None)
            _terminate_pid_tree(task.pid, task.pid_create_time)

    def _kill_actor_by_name(self, name: str, *, missing_ok: bool = True) -> None:
        if self._ray is None:
            return
        try:
            actor = self._ray.get_actor(name, namespace=self._namespace)
        except Exception:
            if missing_ok:
                return
            raise
        try:
            self._ray.kill(actor, no_restart=True)
        except Exception as exc:
            logger.debug("failed to kill Ray actor %s: %s", name, exc)

    def shutdown(self) -> None:
        # Do not terminate training here: running jobs should survive an API
        # restart. Detached Ray actors keep running; local child processes are
        # left to the OS (their result files still allow reconciliation).
        self._local_processes.clear()
        if self._ray is not None and self.ray_connected:
            try:
                self._ray.shutdown()
            except Exception:
                pass
            self._ray = None
            self.ray_connected = False

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "requested_mode": self.requested_mode,
            "ray_connected": self.ray_connected,
            "ray_address": self.ray_address,
            "ray_version": self.ray_version,
            "ray_cpus": self.ray_cpus,
            "error": self.connection_error,
        }
