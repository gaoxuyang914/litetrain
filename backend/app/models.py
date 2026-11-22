"""Database models for the platform."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, utcnow


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}


class AllocationState(str, enum.Enum):
    ACTIVE = "active"
    RELEASED = "released"


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    owner: Mapped[str] = mapped_column(String(64), nullable=False, default="demo", index=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TaskStatus.PENDING.value, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50, index=True)

    # Training configuration
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    dataset: Mapped[str] = mapped_column(String(240), nullable=False)
    epochs: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    imgsz: Mapped[int] = mapped_column(Integer, nullable=False, default=160)
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    workers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    device: Mapped[str] = mapped_column(String(16), nullable=False, default="cpu")
    command: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Resource / lifecycle configuration
    cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False)
    compute_threads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_mode: Mapped[str] = mapped_column(String(8), nullable=False, default="none")
    seed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    patience: Mapped[int | None] = mapped_column(Integer, nullable=True)
    save_period: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    max_runtime_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Dataset / reproducibility
    dataset_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    dataset_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Resume lineage (implemented in the checkpoint work package)
    resume_from_task_id: Mapped[str | None] = mapped_column(String(24), nullable=True)
    resume_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    checkpoint_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    preempted_by_task_id: Mapped[str | None] = mapped_column(String(24), nullable=True)
    preempted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    preempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Lifecycle timestamps
    submitted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    queued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Execution state
    executor: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ray_actor_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pid_create_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    log_path: Mapped[str] = mapped_column(String(512), nullable=False)
    result_path: Mapped[str] = mapped_column(String(512), nullable=False)
    worker_token: Mapped[str] = mapped_column(String(128), nullable=False)

    # Outcome and live telemetry
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latest_cpu_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_memory_mb: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("ix_tasks_status_priority_submitted", "status", "priority", "submitted_at"),
        Index("ix_tasks_owner_status", "owner", "status"),
    )

    @property
    def attempt(self) -> int:
        return self.retry_count + 1

    @property
    def status_enum(self) -> TaskStatus:
        return TaskStatus(self.status)


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    hostname: Mapped[str] = mapped_column(String(160), nullable=False)
    total_cpus: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="online")
    last_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class ResourceAllocation(Base):
    __tablename__ = "resource_allocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cpus: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AllocationState.ACTIVE.value, index=True
    )
    allocated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TaskMetric(Base):
    __tablename__ = "task_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("task_id", "name", "epoch", "step", name="uq_task_metric_point"),
        Index("ix_task_metrics_task_epoch", "task_id", "epoch"),
    )


class ResourceSample(Base):
    __tablename__ = "resource_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cpu_percent: Mapped[float] = mapped_column(Float, nullable=False)
    memory_mb: Mapped[float] = mapped_column(Float, nullable=False)
    process_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sampled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class TaskEvent(Base):
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (Index("ix_task_events_task_created", "task_id", "created_at"),)


# ---------------------------------------------------------------------------
# P2: dataset registry, plans/pipelines, artifacts and webhook outbox
# ---------------------------------------------------------------------------


class DatasetRecord(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False, default="v1")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    yaml_path: Mapped[str] = mapped_column(String(512), nullable=False)
    archive_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    label_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    class_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")
    quality_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_report_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    quality_issue_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    quality_engine_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quality_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    quality_acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_dataset_name_version"),
        Index("ix_datasets_created", "created_at"),
    )


class DatasetQualityReport(Base):
    __tablename__ = "dataset_quality_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    report_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    issue_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_quality_dataset_created", "dataset_id", "created_at"),)


class TaskCheckpoint(Base):
    __tablename__ = "task_checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_best: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("task_id", "epoch", name="uq_task_checkpoint_epoch"),)


class RunManifest(Base):
    __tablename__ = "run_manifests"

    task_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    git_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class BenchmarkRun(Base):
    __tablename__ = "benchmark_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    dataset: Mapped[str | None] = mapped_column(String(240), nullable=True)
    host_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    matrix_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    selected_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    owner: Mapped[str] = mapped_column(String(64), nullable=False, default="demo")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TaskStatus.PENDING.value, index=True)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_pipelines_status_submitted", "status", "submitted_at"),)


class PipelineStage(Base):
    __tablename__ = "pipeline_stages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    pipeline_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_key: Mapped[str] = mapped_column(String(24), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="waiting")
    task_id: Mapped[str | None] = mapped_column(
        String(24), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("pipeline_id", "stage_key", name="uq_pipeline_stage_key"),
        Index("ix_pipeline_stages_pipeline_sequence", "pipeline_id", "sequence"),
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pipeline_id: Mapped[str | None] = mapped_column(
        String(24), ForeignKey("pipelines.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="model", index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("path", name="uq_artifact_path"),
        Index("ix_artifacts_pipeline_created", "pipeline_id", "created_at"),
    )


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    secret: Mapped[str] = mapped_column(String(128), nullable=False)
    events_json: Mapped[str] = mapped_column(Text, nullable=False, default='["*"]')
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    endpoint_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String(24), nullable=True)
    pipeline_id: Mapped[str | None] = mapped_column(String(24), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_webhook_deliveries_pending", "status", "next_attempt_at"),
    )
