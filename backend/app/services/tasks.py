"""Task creation, querying and presentation helpers."""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Database, utcnow
from app.models import (
    ResourceSample,
    Task,
    TaskEvent,
    TaskMetric,
    TaskStatus,
)
from app.schemas import (
    MetricPoint,
    MetricSeries,
    ResourcePoint,
    TaskCreate,
    TaskEventOut,
    TaskEventsResponse,
    TaskListResponse,
    TaskMetricsResponse,
    TaskOptions,
    TaskOut,
    TaskPreviewResponse,
)
from app.services.event_bus import EventBroker
from app.services.logs import LogStore
from app.training.config import (
    TrainingAssetError,
    build_display_command,
    build_equivalent_yolo_command,
    list_datasets,
    list_models,
    resolve_dataset_path,
    resolve_model_path,
)

logger = logging.getLogger(__name__)


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
    ) -> None:
        self.settings = settings
        self.db = db
        self.log_store = log_store
        self.broker = broker

    # --------------------------------------------------------------- creation
    def _id(self, session: Session) -> str:
        for _ in range(8):
            candidate = "t" + uuid.uuid4().hex[:12]
            if session.get(Task, candidate) is None:
                return candidate
        raise TaskValidationError("Unable to allocate a task id", 500)

    def _validate_request(self, payload: TaskCreate) -> tuple[Path, Path, str]:
        if payload.cpu_cores > self.settings.total_cpus:
            raise TaskValidationError(
                f"cpu_cores must be <= node capacity ({self.settings.total_cpus})"
            )
        if payload.cpu_cores > self.settings.user_cpu_quota:
            raise TaskValidationError(
                f"cpu_cores must be <= single-user quota ({self.settings.user_cpu_quota})"
            )
        if payload.max_runtime_seconds > self.settings.max_runtime_seconds_limit:
            raise TaskValidationError(
                f"max_runtime_seconds must be <= {self.settings.max_runtime_seconds_limit}"
            )
        try:
            model_path = resolve_model_path(self.settings, payload.model)
            dataset_path = resolve_dataset_path(self.settings, payload.dataset)
        except TrainingAssetError as exc:
            raise TaskValidationError(str(exc)) from exc
        return model_path, dataset_path, Path(payload.model).name

    def preview(self, payload: TaskCreate) -> TaskPreviewResponse:
        self._validate_request(payload)
        command = build_display_command(
            self.settings,
            task_id="<task-id>",
            model=payload.model,
            dataset=payload.dataset,
            epochs=payload.epochs,
            imgsz=payload.imgsz,
            batch_size=payload.batch_size,
            workers=payload.workers,
            cpu_cores=payload.cpu_cores,
        )
        equivalent = build_equivalent_yolo_command(
            model=payload.model,
            dataset=payload.dataset,
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

    def create(self, payload: TaskCreate) -> TaskOut:
        _, _, model_name = self._validate_request(payload)
        with self.db.session() as session:
            task_id = self._id(session)
            display_command = build_display_command(
                self.settings,
                task_id=task_id,
                model=payload.model,
                dataset=payload.dataset,
                epochs=payload.epochs,
                imgsz=payload.imgsz,
                batch_size=payload.batch_size,
                workers=payload.workers,
                cpu_cores=payload.cpu_cores,
            )
            name = payload.name or (
                f"YOLO {Path(payload.dataset).stem} · {payload.epochs}ep · "
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
                dataset=payload.dataset,
                epochs=payload.epochs,
                imgsz=payload.imgsz,
                batch_size=payload.batch_size,
                workers=payload.workers,
                device="cpu",
                command=display_command,
                notes=payload.notes,
                cpu_cores=payload.cpu_cores,
                max_runtime_seconds=payload.max_runtime_seconds,
                max_retries=payload.max_retries,
                retry_count=0,
                submitted_at=now,
                queued_at=now,
                log_path=str(log_path),
                result_path=str(run_dir / "result.json"),
                worker_token=secrets.token_urlsafe(32),
            )
            session.add(task)
            add_event(
                session,
                task.id,
                "submitted",
                f"Task submitted with {task.cpu_cores} CPU core(s), priority {task.priority}",
            )
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

    def _to_schema(self, task: Task, queue_position: int | None = None) -> TaskOut:
        serialized = TaskOut.model_validate(task)
        return serialized.model_copy(
            update={
                "runtime_seconds": runtime_seconds(task),
                "queue_position": queue_position,
                "log_size_bytes": self.log_store.size(task.id),
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
        default_cpu = min(self.settings.default_cpu_cores, self.settings.total_cpus)
        return TaskOptions(
            models=list_models(self.settings),
            datasets=list_datasets(self.settings),
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
                "cpu_cores": [1, min(self.settings.user_cpu_quota, self.settings.total_cpus)],
                "priority": [0, 100],
                "max_runtime_seconds": [10, self.settings.max_runtime_seconds_limit],
                "max_retries": [0, 5],
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
