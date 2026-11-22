"""Model/results artifact discovery, hashing and safe downloads."""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.config import Settings
from app.database import Database
from app.models import Artifact, PipelineStage, Task, TaskStatus
from app.schemas import ArtifactOut

logger = logging.getLogger(__name__)

ARTIFACT_PATTERNS = (
    ("weights/*.pt", "model"),
    ("results.csv", "metrics"),
    ("args.yaml", "config"),
    ("evaluation.json", "metrics"),
    ("weights/*.onnx", "model"),
)


class ArtifactService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self._lock = threading.RLock()

    def _to_schema(self, record: Artifact) -> ArtifactOut:
        return ArtifactOut(
            id=record.id,
            task_id=record.task_id,
            pipeline_id=record.pipeline_id,
            kind=record.kind,
            name=record.name,
            size_bytes=record.size_bytes,
            sha256=record.sha256,
            created_at=record.created_at,
            download_url=f"/api/v1/artifacts/{record.id}/download",
        )

    def _pipeline_for_task(self, session, task_id: str) -> str | None:
        stage = session.scalar(select(PipelineStage).where(PipelineStage.task_id == task_id))
        return stage.pipeline_id if stage is not None else None

    @staticmethod
    def _sha256(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def scan_task(self, task_id: str) -> list[ArtifactOut]:
        with self._lock, self.db.session() as session:
            task = session.get(Task, task_id)
            if task is None:
                return []
            run_dir = Path(task.result_path).parent
            if not run_dir.is_dir():
                return []
            pipeline_id = self._pipeline_for_task(session, task_id)
            existing = {
                row.path: row
                for row in session.scalars(select(Artifact).where(Artifact.task_id == task_id)).all()
            }
            seen: set[str] = set()
            for pattern, kind in ARTIFACT_PATTERNS:
                for path in sorted(run_dir.glob(pattern)):
                    if not path.is_file():
                        continue
                    try:
                        relative = path.resolve().relative_to(self.settings.data_dir.resolve()).as_posix()
                    except ValueError:
                        continue
                    seen.add(relative)
                    size = path.stat().st_size
                    file_time = datetime.fromtimestamp(
                        path.stat().st_mtime, tz=timezone.utc
                    ).replace(tzinfo=None)
                    record = existing.get(relative)
                    if record is None:
                        record = Artifact(
                            id="ar-" + uuid.uuid4().hex[:12],
                            task_id=task_id,
                            pipeline_id=pipeline_id,
                            kind=kind,
                            name=path.name,
                            path=relative,
                            size_bytes=size,
                            sha256=self._sha256(path),
                            created_at=file_time,
                        )
                        session.add(record)
                    else:
                        record.pipeline_id = record.pipeline_id or pipeline_id
                        record.size_bytes = size
                        record.created_at = file_time
            # Remove rows for files that no longer exist (e.g. a cleaned run dir).
            for relative, record in existing.items():
                if relative not in seen:
                    session.delete(record)
            session.flush()
            rows = session.scalars(
                select(Artifact).where(Artifact.task_id == task_id).order_by(Artifact.kind, Artifact.name)
            ).all()
            output = [self._to_schema(row) for row in rows]
        logger.info("scanned %s artifact(s) for task %s", len(output), task_id)
        return output

    def scan_recent(self, limit: int = 200) -> None:
        with self.db.session() as session:
            tasks = session.scalars(
                select(Task)
                .where(Task.status == TaskStatus.SUCCEEDED.value)
                .order_by(Task.finished_at.desc())
                .limit(limit)
            ).all()
            candidates = []
            for task in tasks:
                count = session.scalar(
                    select(func.count(Artifact.id)).where(Artifact.task_id == task.id)
                ) or 0
                if count == 0 and (Path(task.result_path).parent / "weights").is_dir():
                    candidates.append(task.id)
        for task_id in candidates:
            try:
                self.scan_task(task_id)
            except Exception:
                logger.exception("artifact scan failed for task %s", task_id)

    def list(
        self,
        *,
        task_id: str | None = None,
        pipeline_id: str | None = None,
        kind: str | None = None,
        limit: int = 200,
    ) -> list[ArtifactOut]:
        if task_id:
            self.scan_task(task_id)
        elif pipeline_id:
            with self.db.session() as session:
                ids = session.scalars(
                    select(PipelineStage.task_id).where(
                        PipelineStage.pipeline_id == pipeline_id,
                        PipelineStage.task_id.is_not(None),
                    )
                ).all()
            for stage_task_id in ids:
                if stage_task_id:
                    self.scan_task(stage_task_id)
        else:
            self.scan_recent()
        with self.db.session() as session:
            stmt = select(Artifact).order_by(Artifact.created_at.desc()).limit(limit)
            if task_id:
                stmt = stmt.where(Artifact.task_id == task_id)
            if pipeline_id:
                stmt = stmt.where(Artifact.pipeline_id == pipeline_id)
            if kind:
                stmt = stmt.where(Artifact.kind == kind)
            rows = session.scalars(stmt).all()
            return [self._to_schema(row) for row in rows]

    def find_model(self, task_id: str) -> ArtifactOut | None:
        for item in self.scan_task(task_id):
            if item.kind == "model" and item.name == "best.pt":
                return item
        return None

    def find_runs_dir(self, task_id: str) -> Path:
        return self.settings.runs_dir / task_id

    def get_file(self, artifact_id: str) -> tuple[Path, str]:
        with self.db.session() as session:
            record = session.get(Artifact, artifact_id)
            if record is None:
                raise FileNotFoundError(artifact_id)
            path = (self.settings.data_dir / record.path).resolve()
            root = self.settings.data_dir.resolve()
            if root not in path.parents or not path.is_file():
                raise FileNotFoundError(artifact_id)
            return path, record.name

    def count(self, task_ids: list[str]) -> int:
        if not task_ids:
            return 0
        with self.db.session() as session:
            return int(
                session.scalar(select(func.count(Artifact.id)).where(Artifact.task_id.in_(task_ids))) or 0
            )
