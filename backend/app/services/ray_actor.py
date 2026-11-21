"""Ray actor that owns one training subprocess.

The actor is *detached* and named deterministically, so a restarted API process
can reattach to a still-running job instead of losing it.  Its only job is
process lifecycle: launching, inspecting and terminating the worker tree.  All
platform state lives in SQLite and all logs live in the task log file.
"""

from __future__ import annotations

import platform
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil


def _terminate_tree(pid: int, create_time: float | None = None, timeout: float = 8.0) -> bool:
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
    children: list[psutil.Process] = []
    try:
        children = process.children(recursive=True)
    except psutil.Error:
        pass
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


class TrainingActor:
    """A tiny process supervisor hosted by Ray."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle = None
        self._pid: int | None = None
        self._create_time: float | None = None
        self._started_at: float | None = None
        self._log_path: str | None = None

    def start(self, command: list[str], cwd: str, env: dict[str, str], log_path: str) -> dict[str, Any]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise RuntimeError("actor is already supervising a running process")
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = open(log_path, "ab", buffering=0)
            creationflags = 0
            if platform.system() == "Windows":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            self._process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                close_fds=True,
            )
            # The child owns its duplicated handle; the actor does not need to
            # keep the parent handle open for the lifetime of the process.
            self._log_handle.close()
            self._log_handle = None
            self._pid = self._process.pid
            self._create_time = psutil.Process(self._pid).create_time()
            self._started_at = time.time()
            self._log_path = log_path
            return {
                "pid": self._pid,
                "create_time": self._create_time,
                "started_at": self._started_at,
                "host": platform.node(),
            }

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self._process is None:
                return {
                    "running": False,
                    "exit_code": None,
                    "pid": self._pid,
                    "started_at": self._started_at,
                }
            code = self._process.poll()
            return {
                "running": code is None,
                "exit_code": code,
                "pid": self._pid,
                "started_at": self._started_at,
                "create_time": self._create_time,
            }

    def stop(self, force: bool = True) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is None:
                return {"stopped": False, "reason": "no process"}
            if process.poll() is not None:
                self._close_log()
                return {"stopped": True, "exit_code": process.returncode}
            _terminate_tree(process.pid, self._create_time, timeout=6.0 if force else 10.0)
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
            self._close_log()
            return {"stopped": True, "exit_code": process.poll()}

    def _close_log(self) -> None:
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None

    def ping(self) -> dict[str, Any]:
        return {
            "ok": True,
            "pid": self._pid,
            "log_path": self._log_path,
            "host": platform.node(),
            "python": sys.executable,
        }
