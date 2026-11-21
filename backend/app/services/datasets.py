"""Dataset registry: built-in demo data and uploaded YOLO datasets."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from app.config import Settings
from app.database import Database, utcnow
from app.models import DatasetRecord
from app.schemas import DatasetOut

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
MAX_UPLOAD_BYTES = 512 * 1024 * 1024


class DatasetError(ValueError):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except Exception as exc:
        raise DatasetError(f"invalid YAML {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise DatasetError(f"YAML {path.name} must contain a mapping")
    return data


def _count_tree(root: Path) -> tuple[int, int, int, int]:
    images = 0
    labels = 0
    size = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            size += path.stat().st_size
        except OSError:
            pass
        suffix = path.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            images += 1
        elif suffix == ".txt" and "labels" in path.parts:
            labels += 1
    return images, labels, size, 0


def _class_count(data: dict[str, Any]) -> int:
    names = data.get("names")
    if isinstance(names, dict):
        return len(names)
    if isinstance(names, list):
        return len(names)
    nc = data.get("nc")
    return int(nc) if isinstance(nc, int) else 0


def _find_yaml(root: Path) -> Path:
    candidates = []
    for path in root.rglob("*.yaml"):
        try:
            data = _read_yaml(path)
        except DatasetError:
            continue
        if "train" in data and ("val" in data or "validation" in data):
            candidates.append((0 if path.parent == root else 1, len(path.parts), path))
    if not candidates:
        raise DatasetError("archive does not contain a YOLO dataset YAML with train/val keys")
    candidates.sort(key=lambda item: item[:2])
    return candidates[0][2]


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return cleaned or "dataset"


class DatasetService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db

    def seed_builtin(self) -> None:
        builtin_yaml = self.settings.datasets_dir / "coco128" / "coco128.yaml"
        if not builtin_yaml.is_file():
            return
        with self.db.session() as session:
            exists = session.scalar(
                select(DatasetRecord).where(
                    DatasetRecord.name == "COCO128",
                    DatasetRecord.version == "builtin",
                )
            )
            if exists is not None:
                return
            root = builtin_yaml.parent
            images, labels, size, _ = _count_tree(root)
            data = _read_yaml(builtin_yaml)
            record = DatasetRecord(
                id="ds-builtin-coco128",
                name="COCO128",
                version="builtin",
                description="Ultralytics coco128 示例数据集，仓库内置",
                yaml_path=builtin_yaml.relative_to(self.settings.datasets_dir).as_posix(),
                image_count=images,
                label_count=labels,
                class_count=_class_count(data),
                size_bytes=size,
                status="ready",
                is_builtin=True,
                created_at=utcnow(),
            )
            session.add(record)
            session.flush()
        logger.info("dataset registry seeded with built-in COCO128")

    def _to_schema(self, record: DatasetRecord) -> DatasetOut:
        archive_name = Path(record.archive_path).name if record.archive_path else None
        return DatasetOut(
            id=record.id,
            name=record.name,
            version=record.version,
            description=record.description,
            yaml_path=record.yaml_path,
            source_filename=archive_name,
            image_count=record.image_count,
            label_count=record.label_count,
            class_count=record.class_count,
            size_bytes=record.size_bytes,
            status=record.status,
            is_builtin=record.is_builtin,
            created_at=record.created_at,
        )

    def list(self) -> list[DatasetOut]:
        with self.db.session() as session:
            records = session.scalars(
                select(DatasetRecord)
                .where(DatasetRecord.status != "deleted")
                .order_by(DatasetRecord.created_at.desc())
            ).all()
            return [self._to_schema(record) for record in records]

    def get(self, dataset_id: str) -> DatasetOut:
        with self.db.session() as session:
            record = session.get(DatasetRecord, dataset_id)
            if record is None or record.status == "deleted":
                raise DatasetError("dataset not found", 404)
            return self._to_schema(record)

    def import_archive(
        self,
        *,
        name: str,
        version: str,
        description: str | None,
        filename: str,
        source_path: Path,
    ) -> DatasetOut:
        if not filename.lower().endswith(".zip"):
            raise DatasetError("only .zip dataset archives are supported")
        try:
            if source_path.stat().st_size > MAX_UPLOAD_BYTES:
                raise DatasetError("dataset archive exceeds 512 MB")
        except OSError as exc:
            raise DatasetError(f"could not read uploaded archive: {exc}") from exc

        with self.db.session() as session:
            exists = session.scalar(
                select(DatasetRecord).where(
                    DatasetRecord.name == name,
                    DatasetRecord.version == version,
                )
            )
            if exists is not None:
                raise DatasetError(f"dataset {name!r} version {version!r} already exists", 409)

        dataset_id = "ds-" + uuid.uuid4().hex[:12]
        target = self.settings.datasets_dir / "managed" / dataset_id
        archive_dir = self.settings.datasets_dir / "_archives"
        target.mkdir(parents=True, exist_ok=True)
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_target = archive_dir / f"{dataset_id}.zip"
        try:
            shutil.move(str(source_path), archive_target)
            with zipfile.ZipFile(archive_target) as zf:
                root = target.resolve()
                for member in zf.infolist():
                    destination = (target / member.filename).resolve()
                    if root not in destination.parents and destination != root:
                        raise DatasetError("archive contains an unsafe path")
                zf.extractall(target)
            yaml_path = _find_yaml(target)
            data = _read_yaml(yaml_path)
            # Normalize the dataset root to an absolute path and give the YAML a
            # collision-free file name based on the registry entry.
            data["path"] = yaml_path.parent.resolve().as_posix()
            normalized_yaml = yaml_path.parent / f"{_slug(name)}-{_slug(version)}-{dataset_id[-6:]}.yaml"
            with normalized_yaml.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=False)
            if normalized_yaml != yaml_path:
                yaml_path.unlink(missing_ok=True)
            yaml_path = normalized_yaml
            images, labels, size, _ = _count_tree(target)
            if images == 0:
                raise DatasetError("dataset archive contains no images")
            record = DatasetRecord(
                id=dataset_id,
                name=name,
                version=version,
                description=description,
                yaml_path=yaml_path.relative_to(self.settings.datasets_dir).as_posix(),
                archive_path=archive_target.relative_to(self.settings.data_dir).as_posix(),
                image_count=images,
                label_count=labels,
                class_count=_class_count(data),
                size_bytes=size,
                status="ready",
                is_builtin=False,
                created_at=utcnow(),
            )
            with self.db.session() as session:
                session.add(record)
                session.flush()
            logger.info("dataset %s v%s imported from %s", name, version, filename)
            return self._to_schema(record)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            archive_target.unlink(missing_ok=True)
            raise

    def delete(self, dataset_id: str) -> None:
        with self.db.session() as session:
            record = session.get(DatasetRecord, dataset_id)
            if record is None:
                raise DatasetError("dataset not found", 404)
            if record.is_builtin:
                raise DatasetError("built-in dataset cannot be deleted", 409)
            # Keep files: historical tasks may still reference the YAML. Soft-delete
            # the registry entry so downloads/options can be cleaned up separately.
            record.status = "deleted"
            session.flush()

    def download_archive(self, dataset_id: str) -> Path:
        with self.db.session() as session:
            record = session.get(DatasetRecord, dataset_id)
            if record is None or record.status == "deleted":
                raise DatasetError("dataset not found", 404)
            if not record.archive_path:
                raise DatasetError("this dataset has no original archive", 404)
            path = (self.settings.data_dir / record.archive_path).resolve()
            root = self.settings.data_dir.resolve()
            if root not in path.parents or not path.is_file():
                raise DatasetError("dataset archive is missing", 404)
            return path

    @staticmethod
    def digest(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
