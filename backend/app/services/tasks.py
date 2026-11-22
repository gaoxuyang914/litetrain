"""Task creation, querying and presentation helpers."""

from __future__ import annotations

import json
import logging
import re
import secrets
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Database, utcnow
from app.models import (
    DatasetRecord,
    Node,
    ResourceSample,
    RunManifest,
    Task,
    TaskAttempt,
    TaskCheckpoint,
    TaskEvent,
    TaskMetric,
    TaskStatus,
)
from app.schemas import (
    MetricPoint,
    MetricSeries,
    ResourcePoint,
    TaskAttemptListResponse,
    TaskAttemptOut,
    TaskCheckpointListResponse,
    TaskCheckpointOut,
    TaskCompareItem,
    TaskCompareResponse,
    TaskCreate,
    TaskEventOut,
    TaskEventsResponse,
    TaskListResponse,
    TaskMetricsResponse,
    TaskOptions,
    TaskOut,
    TaskPreviewResponse,
    RunManifestOut,
)
from app.services.event_bus import EventBroker
from app.services.logs import LogStore
from app.services.manifests import RunManifestService
from app.services.quality import QualityError
from app.training.config import (
    TrainingAssetError,
    build_display_command,
    build_equivalent_yolo_command,
    list_models,
    resolve_dataset_path,
    resolve_model_path,
)

logger = logging.getLogger(__name__)

_TASK_SUFFIX = re.compile(r"(?:\s*·\s*(?:rerun|resume))+$", re.IGNORECASE)


def _base_task_name(name: str | None) -> str:
    cleaned = _TASK_SUFFIX.sub("", name or "").strip()
    return cleaned or (name or "task")


class TaskValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class TaskNotFoundError(LookupError):
    pass


def add_event(
    session: Session,
    task_id: str,
    event_type: str,
    message: str,
    *,
    level: str = "info",
) -> TaskEvent:
    event = TaskEvent(
        task_id=task_id,
        event_type=event_type,
        level=level,
        message=message,
        created_at=utcnow(),
    )
    session.add(event)
    task = session.get(Task, task_id)
    if task is not None:
        from app.services.webhooks import enqueue_task_event

        external_type = {
            "submitted": "task.submitted",
            "scheduled": "task.running",
            "recovered": "task.running",
            "succeeded": "task.succeeded",
            "failed": "task.failed",
            "cancelled": "task.cancelled",
            "retry_scheduled": "task.retry_scheduled",
        }.get(event_type, f"task.{event_type}")
        enqueue_task_event(session, task, external_type, message, level)
    return event


def runtime_seconds(task: Task, now: datetime | None = None) -> int:
    if not task.started_at:
        return 0
    end = task.finished_at or now or utcnow()
    return max(0, int((end - task.started_at).total_seconds()))


class TaskService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        log_store: LogStore,
        broker: EventBroker,
        *,
        datasets=None,
        quality=None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.log_store = log_store
        self.broker = broker
        self.datasets = datasets
        self.quality = quality
        self.manifests = RunManifestService(settings)

    # --------------------------------------------------------------- creation
    def _id(self, session: Session) -> str:
        for _ in range(8):
            candidate = "t" + uuid.uuid4().hex[:12]
            if session.get(Task, candidate) is None:
                return candidate
        raise TaskValidationError("Unable to allocate a task id", 500)

    def _dataset_record(self, payload: TaskCreate) -> DatasetRecord | None:
        record: DatasetRecord | None = None
        if payload.dataset_id:
            with self.db.session() as session:
                record = session.get(DatasetRecord, payload.dataset_id)
            if record is None or record.status == "deleted":
                raise TaskValidationError("dataset not found", 422)
        elif self.datasets is not None:
            record = self.datasets.find_by_yaml(payload.dataset)
        if record is None:
            raise TaskValidationError(
                "dataset is not registered; import it and wait for the quality audit", 422
            )
        status = record.quality_status
        if status in {"pending", "auditing"}:
            raise TaskValidationError("dataset quality audit is still running", 409)
        if status in {"blocked", "failed"}:
            raise TaskValidationError(
                f"dataset quality check is {status}; open the quality report before training", 422
            )
        if status == "warning" and not (record.quality_acknowledged or payload.quality_ack):
            raise TaskValidationError(
                "dataset has quality warnings; acknowledge the report before training", 422
            )
        return record

    def _validate_request(
        self, payload: TaskCreate, dataset_value: str | None = None
    ) -> tuple[Path, Path, str]:
        with self.db.session() as session:
            max_node_cpus = int(
                session.scalar(
                    select(func.coalesce(func.max(Node.total_cpus), 0)).where(
                        Node.status == "online"
                    )
                )
                or 0
            )
        if max_node_cpus == 0:
            raise TaskValidationError("no online node is available")
        if payload.cpu_cores > max_node_cpus:
            raise TaskValidationError(
                f"cpu_cores must fit on a single node (largest online node: {max_node_cpus})"
            )
        if payload.cpu_cores > self.settings.user_cpu_quota:
            raise TaskValidationError(
                f"cpu_cores must be <= single-user quota ({self.settings.user_cpu_quota})"
            )
        if payload.max_runtime_seconds > self._runtime_cap():
            raise TaskValidationError(
                f"max_runtime_seconds must be <= platform limit ({self._runtime_cap()}s configured in admin)"
            )
        if payload.max_retries > self._retry_cap():
            raise TaskValidationError(
                f"max_retries must be <= platform limit ({self._retry_cap()} configured in admin)"
            )
        try:
            model_path = resolve_model_path(self.settings, payload.model)
            dataset_path = resolve_dataset_path(
                self.settings, dataset_value or payload.dataset
            )
        except TrainingAssetError as exc:
            raise TaskValidationError(str(exc)) from exc
        return model_path, dataset_path, Path(payload.model).name

    def preview(self, payload: TaskCreate) -> TaskPreviewResponse:
        record = self._dataset_record(payload)
        dataset_value = Path(record.yaml_path).name if record is not None else payload.dataset
        self._validate_request(payload, dataset_value)
        command = build_display_command(
            self.settings,
            task_id="<task-id>",
            model=payload.model,
            dataset=dataset_value,
            epochs=payload.epochs,
            imgsz=payload.imgsz,
            batch_size=payload.batch_size,
            workers=payload.workers,
            cpu_cores=payload.cpu_cores,
        )
        equivalent = build_equivalent_yolo_command(
            model=payload.model,
            dataset=dataset_value,
            epochs=payload.epochs,
            imgsz=payload.imgsz,
            batch_size=payload.batch_size,
            workers=payload.workers,
        )
        estimate = max(20, payload.epochs * max(8, payload.imgsz // 7)) + 15
        return TaskPreviewResponse(
            command=command,
            equivalent_yolo_command=equivalent,
            estimated_note=f"CPU 示例数据预计约 {estimate // 60}分{estimate % 60}秒（仅供参考）",
        )

    def _runtime_cap(self) -> int:
        return min(
            self.settings.default_max_runtime_seconds,
            self.settings.max_runtime_seconds_limit,
        )

    def _retry_cap(self) -> int:
        return max(0, min(5, self.settings.default_max_retries))

    def create(self, payload: TaskCreate) -> TaskOut:
        if "max_runtime_seconds" not in payload.model_fields_set:
            payload.max_runtime_seconds = self._runtime_cap()
        if "max_retries" not in payload.model_fields_set:
            payload.max_retries = self._retry_cap()
        record = self._dataset_record(payload)
        dataset_value = Path(record.yaml_path).name if record is not None else payload.dataset
        model_path, _, model_name = self._validate_request(payload, dataset_value)
        dataset_sha256 = record.content_sha256 if record is not None else None
        compute_threads = payload.compute_threads or min(payload.cpu_cores, 8)
        config = {
            "model": payload.model,
            "dataset": dataset_value,
            "dataset_id": record.id if record is not None else None,
            "dataset_sha256": dataset_sha256,
            "epochs": payload.epochs,
            "imgsz": payload.imgsz,
            "batch_size": payload.batch_size,
            "workers": payload.workers,
            "cpu_cores": payload.cpu_cores,
            "compute_threads": compute_threads,
            "cache_mode": payload.cache_mode,
            "seed": payload.seed,
            "patience": payload.patience,
            "save_period": payload.save_period,
            "priority": payload.priority,
            "max_runtime_seconds": payload.max_runtime_seconds,
            "max_retries": payload.max_retries,
        }
        with self.db.session() as session:
            task_id = self._id(session)
            display_command = build_display_command(
                self.settings,
                task_id=task_id,
                model=payload.model,
                dataset=dataset_value,
                epochs=payload.epochs,
                imgsz=payload.imgsz,
                batch_size=payload.batch_size,
                workers=payload.workers,
                cpu_cores=payload.cpu_cores,
            )
            name = payload.name or (
                f"YOLO {Path(dataset_value).stem} · {payload.epochs}ep · "
                f"{payload.imgsz}px · {task_id[-4:]}"
            )
            now = utcnow()
            log_path = self.log_store.path_for(task_id)
            run_dir = self.settings.runs_dir / task_id
            task = Task(
                id=task_id,
                name=name,
                owner="demo",
                status=TaskStatus.PENDING.value,
                priority=payload.priority,
                model=model_name,
                dataset=dataset_value,
                dataset_id=record.id if record is not None else None,
                dataset_sha256=dataset_sha256,
                epochs=payload.epochs,
                imgsz=payload.imgsz,
                batch_size=payload.batch_size,
                workers=payload.workers,
                device="cpu",
                command=display_command,
                notes=payload.notes,
                cpu_cores=payload.cpu_cores,
                compute_threads=compute_threads,
                cache_mode=payload.cache_mode,
                seed=payload.seed,
                patience=payload.patience,
                save_period=payload.save_period,
                max_runtime_seconds=payload.max_runtime_seconds,
                max_retries=payload.max_retries,
                retry_count=0,
                config_json=json.dumps(config, ensure_ascii=False),
                submitted_at=now,
                queued_at=now,
                log_path=str(log_path),
                result_path=str(run_dir / "result.json"),
                worker_token=secrets.token_urlsafe(32),
            )
            session.add(task)
            session.flush()
            try:
                self.manifests.persist(
                    session, task, model_path=model_path, dataset=record
                )
            except Exception:
                logger.exception("failed to persist run manifest for %s", task.id)
            add_event(
                session,
                task.id,
                "submitted",
                f"Task submitted with {task.cpu_cores} CPU core(s), priority {task.priority}",
            )
            self.record_attempt(session, task)
            session.flush()

        run_dir.mkdir(parents=True, exist_ok=True)
        self.log_store.header(
            task_id,
            name=name,
            command=display_command,
            cpu_cores=payload.cpu_cores,
            owner="demo",
            submitted_at=now,
        )
        self.log_store.append(
            task_id,
            f"[platform] queued · requested {payload.cpu_cores} CPU core(s) · "
            f"priority {payload.priority} · max runtime {payload.max_runtime_seconds}s",
        )
        self.broker.publish("task.created", task_id, {"status": TaskStatus.PENDING.value, "name": name})
        logger.info("task %s created (%s, %s CPUs)", task_id, name, payload.cpu_cores)
        return self.get(task_id)

    # ------------------------------------------------------------------ reads
    def _queue_positions(self, session: Session) -> dict[str, int]:
        pending = session.scalars(
            select(Task.id)
            .where(Task.status == TaskStatus.PENDING.value)
            .order_by(Task.priority.desc(), Task.submitted_at.asc())
        ).all()
        return {task_id: index + 1 for index, task_id in enumerate(pending)}

    def record_attempt(self, session: Session, task: Task) -> TaskAttempt:
        """Persist a snapshot of the current attempt without replacing its log."""
        row = session.scalar(
            select(TaskAttempt).where(
                TaskAttempt.task_id == task.id,
                TaskAttempt.attempt == task.attempt,
            )
        )
        if row is None:
            row = TaskAttempt(
                task_id=task.id,
                attempt=task.attempt,
                status=task.status,
                cpu_cores=task.cpu_cores,
            )
            session.add(row)
        row.status = task.status
        row.started_at = task.started_at
        row.finished_at = task.finished_at
        row.runtime_seconds = runtime_seconds(task)
        row.exit_code = task.exit_code
        row.failure_reason = task.failure_reason
        row.progress = task.progress
        row.cpu_cores = task.cpu_cores
        session.flush()
        return row

    def attempts(self, task_id: str) -> TaskAttemptListResponse:
        with self.db.session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            rows = session.scalars(
                select(TaskAttempt)
                .where(TaskAttempt.task_id == task_id)
                .order_by(TaskAttempt.attempt.asc())
            ).all()
            if not rows:
                rows = [
                    TaskAttempt(
                        task_id=task.id,
                        attempt=task.attempt,
                        status=task.status,
                        started_at=task.started_at,
                        finished_at=task.finished_at,
                        runtime_seconds=runtime_seconds(task),
                        exit_code=task.exit_code,
                        failure_reason=task.failure_reason,
                        progress=task.progress,
                        cpu_cores=task.cpu_cores,
                    )
                ]
            items = [
                TaskAttemptOut(
                    attempt=row.attempt,
                    status=TaskStatus(row.status),
                    started_at=row.started_at,
                    finished_at=row.finished_at,
                    runtime_seconds=row.runtime_seconds,
                    exit_code=row.exit_code,
                    failure_reason=row.failure_reason,
                    progress=row.progress,
                    cpu_cores=row.cpu_cores,
                )
                for row in rows
            ]
            return TaskAttemptListResponse(items=items, total=len(items))

    def _to_schema(self, task: Task, queue_position: int | None = None) -> TaskOut:
        serialized = TaskOut.model_validate(task)
        try:
            task_config = json.loads(task.config_json or "{}")
            task_kind = str(task_config.get("stage") or "train")
        except (json.JSONDecodeError, TypeError):
            task_kind = "train"
        return serialized.model_copy(
            update={
                "runtime_seconds": runtime_seconds(task),
                "queue_position": queue_position,
                "log_size_bytes": self.log_store.size(task.id),
                "kind": task_kind,
            }
        )

    def get(self, task_id: str) -> TaskOut:
        with self.db.session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            queue_position = None
            if task.status == TaskStatus.PENDING.value:
                queue_position = self._queue_positions(session).get(task.id)
            return self._to_schema(task, queue_position)

    def manifest(self, task_id: str) -> RunManifestOut:
        with self.db.session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            manifest = self.manifests.get(session, task_id)
            if manifest is None:
                raise TaskValidationError("manifest is not available for this task", 404)
            dataset = manifest.manifest.get("dataset") or {}
            if dataset.get("quality_status") == "unknown" or not manifest.dataset_sha256:
                record = None
                try:
                    task_config = json.loads(task.config_json or "{}")
                except json.JSONDecodeError:
                    task_config = {}
                record_id = task_config.get("source_dataset_id") or task.dataset_id
                if record_id:
                    record = session.get(DatasetRecord, record_id)
                if record is None:
                    source_name = Path(
                        str(task_config.get("source_dataset") or task.dataset or "")
                    ).name
                    record = next(
                        (
                            item
                            for item in session.scalars(
                                select(DatasetRecord).where(DatasetRecord.status != "deleted")
                            ).all()
                            if Path(item.yaml_path).name == source_name
                        ),
                        None,
                    )
                if record is not None:
                    data = dict(manifest.manifest)
                    data["dataset"] = {
                        **dataset,
                        "id": record.id,
                        "name": record.name,
                        "version": record.version,
                        "content_sha256": record.content_sha256,
                        "quality_status": record.quality_status,
                        "yaml": record.yaml_path,
                    }
                    return manifest.model_copy(
                        update={
                            "manifest": data,
                            "dataset_sha256": record.content_sha256,
                        }
                    )
            return manifest

    def checkpoints(self, task_id: str) -> TaskCheckpointListResponse:
        with self.db.session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            rows = session.scalars(
                select(TaskCheckpoint)
                .where(TaskCheckpoint.task_id == task_id)
                .order_by(TaskCheckpoint.epoch.asc())
            ).all()
            items = []
            for row in rows:
                try:
                    metrics = json.loads(row.metrics_json or "{}")
                except json.JSONDecodeError:
                    metrics = {}
                items.append(
                    TaskCheckpointOut(
                        epoch=row.epoch,
                        path=row.path,
                        size_bytes=row.size_bytes,
                        metrics=metrics,
                        is_best=row.is_best,
                        created_at=row.created_at,
                    )
                )
            return TaskCheckpointListResponse(
                items=items,
                total=len(items),
                fallback_available=(
                    Path(task.result_path).parent / "weights" / "last.pt"
                ).is_file(),
            )

    def rerun(self, task_id: str) -> TaskOut:
        """Rerun the same task in place, preserving its attempt history and log."""
        with self.db.session() as session:
            original = session.get(Task, task_id)
            if original is None:
                raise TaskNotFoundError(task_id)
            if original.status not in {
                TaskStatus.SUCCEEDED.value,
                TaskStatus.FAILED.value,
                TaskStatus.CANCELLED.value,
            }:
                raise TaskValidationError("only finished tasks can be rerun", 409)

            previous_attempt = original.attempt
            self.record_attempt(session, original)
            original.name = _base_task_name(original.name)
            try:
                Path(original.result_path).unlink(missing_ok=True)
            except OSError:
                logger.warning("could not reset result marker for %s", task_id)
            session.execute(delete(TaskMetric).where(TaskMetric.task_id == task_id))
            session.execute(delete(ResourceSample).where(ResourceSample.task_id == task_id))

            original.manual_rerun_count = previous_attempt
            original.retry_count = 0
            original.max_runtime_seconds = self._runtime_cap()
            original.max_retries = self._retry_cap()
            original.status = TaskStatus.PENDING.value
            original.queued_at = utcnow()
            original.started_at = None
            original.finished_at = None
            original.last_heartbeat_at = None
            original.cancel_requested_at = None
            original.exit_code = None
            original.failure_reason = None
            original.stop_reason = None
            original.progress = 0.0
            original.executor = None
            original.ray_actor_name = None
            original.pid = None
            original.pid_create_time = None
            original.latest_cpu_percent = None
            original.latest_memory_mb = None
            original.preempted_by_task_id = None
            original.preempted_at = None
            original.preempt_count = 0
            original.worker_token = secrets.token_urlsafe(32)
            try:
                config = json.loads(original.config_json or "{}")
            except json.JSONDecodeError:
                config = {}
            config["last_rerun_attempt"] = previous_attempt
            original.config_json = json.dumps(config, ensure_ascii=False)

            add_event(
                session,
                task_id,
                "rerun",
                f"Manual rerun of attempt {previous_attempt}; new attempt "
                f"{previous_attempt + 1} queued on the same task",
            )
            session.flush()
            self.record_attempt(session, original)
            session.flush()
        self.log_store.append(
            task_id,
            "=" * 78,
        )
        self.log_store.append(
            task_id,
            f"[platform] MANUAL RERUN · previous attempt {previous_attempt} · "
            f"new attempt {previous_attempt + 1} · same task id",
        )
        self.broker.publish("task.rerun", task_id, {"previous_attempt": previous_attempt})
        return self.get(task_id)

    def resume(
        self,
        task_id: str,
        mode: str = "full",
        checkpoint_epoch: int | None = None,
    ) -> TaskOut:
        original = self.get_model(task_id)
        if original.status not in {TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}:
            raise TaskValidationError("only failed or cancelled tasks can be resumed", 409)
        with self.db.session() as session:
            query = select(TaskCheckpoint).where(TaskCheckpoint.task_id == task_id)
            if checkpoint_epoch is not None:
                query = query.where(TaskCheckpoint.epoch == checkpoint_epoch)
            checkpoint = session.scalar(query.order_by(TaskCheckpoint.epoch.desc()))
            fallback = Path(original.result_path).parent / "weights" / "last.pt"
        if checkpoint_epoch is not None and checkpoint is None:
            raise TaskValidationError(f"checkpoint for epoch {checkpoint_epoch} was not found", 404)
        checkpoint_path = self.settings.data_dir / checkpoint.path if checkpoint is not None else fallback
        if not checkpoint_path.is_file():
            raise TaskValidationError(
                "no recoverable checkpoint: training stopped before the first epoch completed; use rerun instead",
                409,
            )
        if original.dataset_id and original.dataset_sha256 and self.datasets is not None:
            with self.db.session() as session:
                record = session.get(DatasetRecord, original.dataset_id)
            if (
                record is not None
                and record.content_sha256
                and record.content_sha256 != original.dataset_sha256
            ):
                raise TaskValidationError(
                    "dataset content changed since this run; rerun instead of resuming", 409
                )
        try:
            config = json.loads(original.config_json or "{}")
        except json.JSONDecodeError:
            config = {}
        dataset = config.get("dataset") or original.dataset
        epoch_label = checkpoint.epoch if checkpoint is not None else 0
        payload = TaskCreate(
            name=f"{_base_task_name(original.name)} · resume",
            model=original.model,
            dataset=Path(dataset).name,
            dataset_id=original.dataset_id,
            quality_ack=True,
            epochs=original.epochs,
            imgsz=original.imgsz,
            batch_size=original.batch_size,
            workers=original.workers,
            cpu_cores=original.cpu_cores,
            compute_threads=original.compute_threads,
            cache_mode=original.cache_mode,
            seed=original.seed,
            patience=original.patience,
            save_period=original.save_period,
            priority=original.priority,
            max_runtime_seconds=min(original.max_runtime_seconds, self._runtime_cap()),
            max_retries=min(original.max_retries, self._retry_cap()),
            notes=f"resume from {task_id} ({mode}, epoch {epoch_label})",
        )
        created = self.create(payload)
        with self.db.session() as session:
            task = session.get(Task, created.id)
            if task is None:
                raise TaskNotFoundError(created.id)
            task.resume_from_task_id = task_id
            task.resume_mode = mode
            task.checkpoint_epoch = epoch_label
            try:
                stored = json.loads(task.config_json or "{}")
            except json.JSONDecodeError:
                stored = {}
            stored["resume_from_task_id"] = task_id
            stored["resume_mode"] = mode
            stored["checkpoint_epoch"] = epoch_label
            task.config_json = json.dumps(stored, ensure_ascii=False)
            add_event(
                session,
                task.id,
                "resumed",
                f"Resume from {task_id} ({mode}) at checkpoint {epoch_label}",
            )
            session.flush()
        return self.get(created.id)

    def compare(self, task_ids: list[str]) -> TaskCompareResponse:
        unique = list(dict.fromkeys(task_ids))
        if len(unique) < 2 or len(unique) > 4:
            raise TaskValidationError("select 2–4 tasks to compare")
        items: list[TaskCompareItem] = []
        with self.db.session() as session:
            for task_id in unique:
                task = session.get(Task, task_id)
                if task is None:
                    raise TaskNotFoundError(task_id)
                manifest_row = self.manifests.get(session, task_id)
                try:
                    config = json.loads(task.config_json or "{}")
                except json.JSONDecodeError:
                    config = {}
                items.append(
                    TaskCompareItem(
                        task_id=task.id,
                        name=task.name,
                        status=task.status,
                        dataset_id=task.dataset_id,
                        dataset_sha256=task.dataset_sha256,
                        config=config,
                        runtime_seconds=runtime_seconds(task),
                        manifest=manifest_row.manifest if manifest_row else None,
                    )
                )
        for item in items:
            metrics = self.metrics(item.task_id)
            item.final_metrics = metrics.final_metrics
            item.peak_memory_mb = max(
                (point.memory_mb for point in metrics.resources), default=0.0
            ) or None

        hashes = {item.dataset_sha256 for item in items if item.dataset_sha256}
        names = {item.config.get("dataset") for item in items if item.config.get("dataset")}
        compatible = len(hashes) <= 1 and len(names) <= 1
        reason = None
        if not compatible:
            reason = "任务使用了不同数据集，不能直接比较指标排名"
        first = items[0].final_metrics
        deltas: dict[str, float] = {}
        for key, value in items[-1].final_metrics.items():
            if key in first:
                deltas[key] = round(value - first[key], 6)
        return TaskCompareResponse(
            compatible=compatible,
            reason=reason,
            items=items,
            metric_deltas=deltas,
        )

    def get_model(self, task_id: str) -> Task:
        with self.db.session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            session.expunge(task)
            return task

    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 10,
        status: str | None = None,
        query: str | None = None,
        sort_by: str = "submitted_at",
        sort_order: str = "desc",
    ) -> TaskListResponse:
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        with self.db.session() as session:
            conditions = []
            if status:
                statuses = [part.strip() for part in status.split(",") if part.strip()]
                valid = {item.value for item in TaskStatus}
                if any(item not in valid for item in statuses):
                    raise TaskValidationError(f"Invalid status filter: {status}")
                conditions.append(Task.status.in_(statuses))
            if query:
                needle = f"%{query.strip()}%"
                conditions.append(or_(Task.id.ilike(needle), Task.name.ilike(needle)))
            count_stmt = select(func.count(Task.id))
            if conditions:
                count_stmt = count_stmt.where(*conditions)
            total = int(session.scalar(count_stmt) or 0)
            sort_column = {
                "submitted_at": Task.submitted_at,
                "priority": Task.priority,
                "started_at": Task.started_at,
                "finished_at": Task.finished_at,
                "status": Task.status,
            }.get(sort_by, Task.submitted_at)
            order = sort_column.asc() if sort_order == "asc" else sort_column.desc()
            stmt = select(Task)
            if conditions:
                stmt = stmt.where(*conditions)
            stmt = stmt.order_by(order, Task.id.desc()).offset((page - 1) * page_size).limit(page_size)
            tasks = session.scalars(stmt).all()
            positions = self._queue_positions(session)
            items = [self._to_schema(task, positions.get(task.id)) for task in tasks]
            counts = self.status_counts(session)
        pages = (total + page_size - 1) // page_size if total else 0
        return TaskListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
            status_counts=counts,
        )

    def status_counts(self, session: Session | None = None) -> dict[str, int]:
        own = session is None
        session = session or self.db.session_factory()
        try:
            rows = session.execute(select(Task.status, func.count(Task.id)).group_by(Task.status)).all()
            counts = {status.value: 0 for status in TaskStatus}
            for status, count in rows:
                counts[str(status)] = int(count)
            return counts
        finally:
            if own:
                session.close()

    def options(self, execution_backend: str) -> TaskOptions:
        with self.db.session() as session:
            max_node_cpus = int(
                session.scalar(
                    select(func.coalesce(func.max(Node.total_cpus), 0)).where(
                        Node.status == "online"
                    )
                )
                or 0
            )
        max_cpu = max(1, min(self.settings.user_cpu_quota, self.settings.total_cpus, max_node_cpus))
        default_cpu = min(self.settings.default_cpu_cores, max_cpu)
        managed_datasets = []
        if self.datasets is not None:
            for item in self.datasets.list():
                if (self.settings.datasets_dir / item.yaml_path).is_file():
                    managed_datasets.append(Path(item.yaml_path).name)
        return TaskOptions(
            models=list_models(self.settings),
            datasets=managed_datasets,
            total_cpus=self.settings.total_cpus,
            user_cpu_quota=self.settings.user_cpu_quota,
            execution_backend=execution_backend,
            defaults={
                "model": self.settings.default_model,
                "dataset": self.settings.default_dataset,
                "epochs": 3,
                "imgsz": 160,
                "batch_size": 4,
                "workers": 0,
                "cpu_cores": default_cpu,
                "priority": 50,
                "max_runtime_seconds": self.settings.default_max_runtime_seconds,
                "max_retries": self.settings.default_max_retries,
            },
            limits={
                "epochs": [1, 50],
                "imgsz": [64, 640],
                "batch_size": [1, 64],
                "cpu_cores": [1, max_cpu],
                "priority": [0, 100],
                "max_runtime_seconds": [10, self._runtime_cap()],
                "max_retries": [0, self._retry_cap()],
            },
        )

    def events(self, task_id: str, limit: int = 100) -> TaskEventsResponse:
        with self.db.session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            rows = session.scalars(
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id)
                .order_by(TaskEvent.created_at.desc(), TaskEvent.id.desc())
                .limit(limit)
            ).all()
            return TaskEventsResponse(items=[TaskEventOut.model_validate(row) for row in rows])

    def metrics(self, task_id: str) -> TaskMetricsResponse:
        with self.db.session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            metric_rows = session.scalars(
                select(TaskMetric)
                .where(TaskMetric.task_id == task_id)
                .order_by(TaskMetric.name.asc(), TaskMetric.epoch.asc(), TaskMetric.step.asc())
            ).all()
            grouped: dict[str, list[MetricPoint]] = {}
            latest: dict[str, tuple[int, int, float]] = {}
            for row in metric_rows:
                grouped.setdefault(row.name, []).append(
                    MetricPoint(epoch=row.epoch, step=row.step, value=round(float(row.value), 6))
                )
                marker = (row.epoch, row.step, float(row.value))
                if row.name not in latest or marker[:2] >= latest[row.name][:2]:
                    latest[row.name] = marker
            samples = session.scalars(
                select(ResourceSample)
                .where(ResourceSample.task_id == task_id)
                .order_by(ResourceSample.sampled_at.asc())
                .limit(5000)
            ).all()
            resources = [
                ResourcePoint(
                    cpu_percent=round(sample.cpu_percent, 2),
                    memory_mb=round(sample.memory_mb, 2),
                    process_count=sample.process_count,
                    sampled_at=sample.sampled_at,
                )
                for sample in samples
            ]
        series = [
            MetricSeries(name=name, points=points)
            for name, points in sorted(grouped.items())
        ]
        final_metrics = {name: round(value[2], 6) for name, value in sorted(latest.items())}
        return TaskMetricsResponse(
            task_id=task_id,
            series=series,
            resources=resources,
            final_metrics=final_metrics,
        )


def append_task_event(
    db: Database,
    broker: EventBroker,
    task_id: str,
    event_type: str,
    message: str,
    *,
    level: str = "info",
) -> None:
    with db.session() as session:
        add_event(session, task_id, event_type, message, level=level)
    broker.publish(event_type, task_id, {"message": message, "level": level})
