"""Durable per-task log files with offset-based incremental reads."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def clean_log_text(text: str) -> str:
    """Make terminal output safe to render in a browser.

    ANSI colour codes are removed and bare carriage returns (used by progress
    bars) become newlines so offsets stay deterministic and the frontend can
    split the stream into fixed-height terminal lines.
    """
    text = ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


@dataclass(slots=True)
class LogWindow:
    start_offset: int
    next_offset: int
    content: str
    eof: bool
    file_size: int


class LogStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()

    def path_for(self, task_id: str) -> Path:
        return self.root / f"{task_id}.log"

    def ensure(self, task_id: str) -> Path:
        path = self.path_for(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        return path

    def append(self, task_id: str, text: str) -> None:
        path = self.ensure(task_id)
        if not text.endswith("\n"):
            text += "\n"
        with self._write_lock:
            with path.open("ab") as handle:
                handle.write(text.encode("utf-8", errors="replace"))

    def header(
        self,
        task_id: str,
        *,
        name: str,
        command: str,
        cpu_cores: int,
        owner: str,
        submitted_at: datetime,
    ) -> None:
        stamp = submitted_at.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
        self.append(
            task_id,
            "\n".join(
                [
                    "=" * 78,
                    " LiteTrain task",
                    f" id        : {task_id}",
                    f" name      : {name}",
                    f" owner     : {owner}",
                    f" resources : {cpu_cores} CPU core(s)",
                    f" submitted : {stamp}",
                    f" command   : {command}",
                    "=" * 78,
                ]
            ),
        )

    def size(self, task_id: str) -> int:
        path = self.path_for(task_id)
        try:
            return path.stat().st_size
        except FileNotFoundError:
            return 0

    def read_chunk(self, task_id: str, offset: int = 0, limit: int = 64 * 1024) -> LogWindow:
        path = self.ensure(task_id)
        file_size = path.stat().st_size
        offset = max(0, int(offset))
        limit = max(1024, min(int(limit), 1024 * 1024))
        if offset > file_size:
            offset = file_size
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read(limit)
        next_offset = offset + len(data)
        content = clean_log_text(data.decode("utf-8", errors="replace"))
        return LogWindow(
            start_offset=offset,
            next_offset=next_offset,
            content=content,
            eof=next_offset >= file_size,
            file_size=file_size,
        )
