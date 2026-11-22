"""Base model weight discovery and validated imports."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.schemas import ModelWeightListResponse, ModelWeightOut

MAX_MODEL_BYTES = 512 * 1024 * 1024
BUILTIN_WEIGHTS = {"yolov8n.pt"}
_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ModelWeightError(ValueError):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _weight_out(path: Path, sha256: str | None = None) -> ModelWeightOut:
    stat = path.stat()
    return ModelWeightOut(
        name=path.name,
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        sha256=sha256,
        is_builtin=path.name in BUILTIN_WEIGHTS,
    )


def list_weights(settings: Settings) -> ModelWeightListResponse:
    root = settings.weights_dir
    if not root.is_dir():
        return ModelWeightListResponse(items=[], total=0)
    items = [
        _weight_out(path)
        for path in root.iterdir()
        if path.is_file() and not path.is_symlink() and path.suffix.lower() == ".pt"
    ]
    items.sort(key=lambda item: item.name.lower())
    return ModelWeightListResponse(items=items, total=len(items))


def validate_upload_name(raw_name: str | None) -> str:
    if not raw_name:
        raise ModelWeightError("请选择 .pt 模型权重文件")
    if raw_name != Path(raw_name).name or _INVALID_NAME.search(raw_name):
        raise ModelWeightError("文件名不能包含路径或非法字符")
    name = raw_name.strip()
    if len(name) > 120:
        raise ModelWeightError("文件名长度不能超过 120 个字符")
    if not name.lower().endswith(".pt"):
        raise ModelWeightError("只支持 Ultralytics / PyTorch .pt 权重")
    return name


def target_path(settings: Settings, name: str) -> Path:
    root = settings.weights_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / name).resolve()
    if target.parent != root:
        raise ModelWeightError("模型权重路径不合法")
    if target.exists():
        raise ModelWeightError(f"模型 {name} 已存在，请使用不同文件名", 409)
    return target


def checkpoint_prefix_valid(prefix: bytes) -> bool:
    """Accept PyTorch zip checkpoints and legacy pickle-based checkpoints."""
    if not prefix:
        return False
    return prefix.startswith(b"PK\x03\x04") or prefix.startswith(b"PK\x05\x06") or prefix.startswith(b"\x80")


def commit_upload(settings: Settings, name: str, temporary: Path, sha256: str) -> ModelWeightOut:
    target = target_path(settings, name)
    os.replace(temporary, target)
    return _weight_out(target, sha256=sha256)
