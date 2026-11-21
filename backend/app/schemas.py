"""Pydantic request/response contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
)

from app.models import TaskStatus


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


UtcDateTime = Annotated[
    datetime, PlainSerializer(iso_utc, return_type=str, when_used="json")
]
OptionalUtcDateTime = Annotated[
    datetime | None,
    PlainSerializer(lambda value: iso_utc(value) if value is not None else None, return_type=str | None, when_used="json"),
]


class TaskCreate(BaseModel):
    name: str = Field(default="", max_length=100)
    model: str = Field(default="yolov8n.pt", min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._\-/]+\.pt$")
    dataset: str = Field(default="coco128.yaml", min_length=1, max_length=240, pattern=r"^[A-Za-z0-9._\-/]+\.yaml$")
    epochs: int = Field(default=3, ge=1, le=50)
    imgsz: int = Field(default=160, ge=64, le=640)
    batch_size: int = Field(default=4, ge=1, le=64)
    workers: int = Field(default=0, ge=0, le=8)
    cpu_cores: int = Field(default=4, ge=1, le=128)
    priority: int = Field(default=50, ge=0, le=100)
    max_runtime_seconds: int = Field(default=1800, ge=10, le=604_800)
    max_retries: int = Field(default=0, ge=0, le=5)
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("model", "dataset")
    @classmethod
    def no_traversal(cls, value: str) -> str:
        cleaned = value.strip().replace("\\", "/")
        if cleaned.startswith("/") or ".." in cleaned.split("/"):
            raise ValueError("Path traversal is not allowed")
        return cleaned

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str | None) -> str | None:
        if not value:
            return None
        return value.strip() or None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    owner: str
    status: TaskStatus
    priority: int

    model: str
    dataset: str
    epochs: int
    imgsz: int
    batch_size: int
    workers: int
    device: str
    command: str
    notes: str | None = None

    cpu_cores: int
    max_runtime_seconds: int
    max_retries: int
    retry_count: int
    attempt: int = 1

    submitted_at: UtcDateTime
    queued_at: UtcDateTime
    started_at: OptionalUtcDateTime = None
    finished_at: OptionalUtcDateTime = None
    last_heartbeat_at: OptionalUtcDateTime = None
    cancel_requested_at: OptionalUtcDateTime = None

    executor: str | None = None
    pid: int | None = None
    exit_code: int | None = None
    failure_reason: str | None = None
    progress: float = 0.0
    latest_cpu_percent: float | None = None
    latest_memory_mb: float | None = None

    runtime_seconds: int = 0
    queue_position: int | None = None
    log_size_bytes: int = 0


class TaskListResponse(BaseModel):
    items: list[TaskOut]
    total: int
    page: int
    page_size: int
    pages: int
    status_counts: dict[str, int] = Field(default_factory=dict)


class TaskOptions(BaseModel):
    models: list[str]
    datasets: list[str]
    total_cpus: int
    user_cpu_quota: int
    execution_backend: str
    defaults: dict
    limits: dict


class TaskPreviewResponse(BaseModel):
    command: str
    equivalent_yolo_command: str
    estimated_note: str


class LogChunk(BaseModel):
    task_id: str
    start_offset: int
    next_offset: int
    content: str
    eof: bool
    file_size: int
    task_status: TaskStatus


class TaskEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: str
    event_type: str
    level: str
    message: str
    created_at: UtcDateTime


class TaskEventsResponse(BaseModel):
    items: list[TaskEventOut]


class CancelResponse(BaseModel):
    task: TaskOut
    message: str


class MetricPoint(BaseModel):
    epoch: int
    step: int
    value: float


class MetricSeries(BaseModel):
    name: str
    points: list[MetricPoint]


class ResourcePoint(BaseModel):
    cpu_percent: float
    memory_mb: float
    process_count: int
    sampled_at: UtcDateTime


class TaskMetricsResponse(BaseModel):
    task_id: str
    series: list[MetricSeries]
    resources: list[ResourcePoint]
    final_metrics: dict[str, float] = Field(default_factory=dict)


class HeartbeatPayload(BaseModel):
    pid: int
    cpu_percent: float = Field(default=0, ge=0, le=100 * 1024)
    memory_mb: float = Field(default=0, ge=0)
    process_count: int = Field(default=1, ge=1, le=1024)
    progress: float | None = Field(default=None, ge=0, le=100)
    message: str | None = Field(default=None, max_length=300)


class HeartbeatResponse(BaseModel):
    accepted: bool
    task_status: TaskStatus
    server_time: UtcDateTime


class MetricsIngestRequest(BaseModel):
    epoch: int = Field(ge=0, le=100_000)
    step: int = Field(ge=0, le=100_000)
    values: dict[str, float] = Field(min_length=1, max_length=64)
    progress: float | None = Field(default=None, ge=0, le=100)

    @field_validator("values")
    @classmethod
    def finite_values(cls, values: dict[str, float]) -> dict[str, float]:
        cleaned: dict[str, float] = {}
        for name, value in values.items():
            if not name or len(name) > 96:
                continue
            if value != value or value in (float("inf"), float("-inf")):
                continue
            cleaned[name] = float(value)
        if not cleaned:
            raise ValueError("No finite metrics supplied")
        return cleaned


class MetricsIngestResponse(BaseModel):
    accepted: bool
    stored: int


class CpuStats(BaseModel):
    total: int
    budget: int = 0
    allocated: int
    available: int
    utilization_percent: float
    host_percent: float
    host_logical_cores: int
    process_percent: float


class MemoryStats(BaseModel):
    total_mb: float
    used_mb: float
    available_mb: float
    percent: float


class TaskCounts(BaseModel):
    pending: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    total: int = 0


class NodeInfo(BaseModel):
    id: str
    name: str
    hostname: str
    status: str
    total_cpus: int
    allocated_cpus: int
    free_cpus: int
    running_tasks: int
    cpu_utilization_percent: float
    host_percent: float
    last_seen: UtcDateTime


class QuotaInfo(BaseModel):
    owner: str
    limit_cpus: int
    used_cpus: int
    available_cpus: int


class ServicesInfo(BaseModel):
    execution_backend: str
    execution_mode: str
    ray_connected: bool
    ray_address: str
    ray_version: str | None = None
    ray_cpus: float | None = None
    redis_connected: bool
    redis_version: str | None = None
    scheduler_leader: bool = True


class ClusterOverview(BaseModel):
    cpu: CpuStats
    memory: MemoryStats
    tasks: TaskCounts
    nodes: list[NodeInfo]
    quota: QuotaInfo
    services: ServicesInfo
    uptime_seconds: int
    updated_at: UtcDateTime


class HealthResponse(BaseModel):
    status: str
    version: str
    execution_backend: str
    redis_connected: bool
    database: str


# ---------------------------------------------------------------------------
# P2 contracts
# ---------------------------------------------------------------------------


class NodeTopologyItem(BaseModel):
    name: str = Field(default="", max_length=120)
    total_cpus: int = Field(ge=1, le=256)


class NodeTopologyRequest(BaseModel):
    nodes: list[NodeTopologyItem] = Field(min_length=1, max_length=8)

    @field_validator("nodes")
    @classmethod
    def unique_names(cls, nodes: list[NodeTopologyItem]) -> list[NodeTopologyItem]:
        names = [item.name.strip() for item in nodes if item.name.strip()]
        if len(names) != len(set(names)):
            raise ValueError("node names must be unique")
        return nodes


class NodeTopologyResponse(BaseModel):
    nodes: list[NodeInfo]
    total_cpus: int


class NodeStatusUpdate(BaseModel):
    status: str = Field(pattern="^(online|offline)$")


class DatasetOut(BaseModel):
    id: str
    name: str
    version: str
    description: str | None = None
    yaml_path: str
    source_filename: str | None = None
    image_count: int
    label_count: int
    class_count: int
    size_bytes: int
    status: str
    is_builtin: bool
    created_at: UtcDateTime


class DatasetListResponse(BaseModel):
    items: list[DatasetOut]
    total: int


class ArtifactOut(BaseModel):
    id: str
    task_id: str
    pipeline_id: str | None = None
    kind: str
    name: str
    size_bytes: int
    sha256: str
    created_at: UtcDateTime
    download_url: str


class ArtifactListResponse(BaseModel):
    items: list[ArtifactOut]
    total: int


class PipelineCreate(BaseModel):
    name: str = Field(default="", max_length=100)
    source_dataset: str = Field(default="coco128.yaml", min_length=1, max_length=240)
    model: str = Field(default="yolov8n.pt", min_length=1, max_length=160)
    epochs: int = Field(default=1, ge=1, le=20)
    imgsz: int = Field(default=96, ge=64, le=640)
    batch_size: int = Field(default=4, ge=1, le=64)
    workers: int = Field(default=0, ge=0, le=8)
    cpu_cores: int = Field(default=4, ge=1, le=128)
    priority: int = Field(default=50, ge=0, le=100)
    max_runtime_seconds: int = Field(default=600, ge=10, le=604_800)
    max_retries: int = Field(default=0, ge=0, le=5)
    max_images: int = Field(default=24, ge=1, le=1024)
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("source_dataset", "model")
    @classmethod
    def safe_asset_names(cls, value: str) -> str:
        cleaned = value.strip().replace("\\", "/")
        if cleaned.startswith("/") or ".." in cleaned.split("/"):
            raise ValueError("Path traversal is not allowed")
        return cleaned


class PipelineStageTaskSummary(BaseModel):
    id: str
    name: str
    status: TaskStatus
    progress: float = 0.0
    runtime_seconds: int = 0
    failure_reason: str | None = None
    log_size_bytes: int = 0


class PipelineStageOut(BaseModel):
    id: str
    stage_key: str
    sequence: int
    status: str
    message: str | None = None
    task: PipelineStageTaskSummary | None = None
    started_at: OptionalUtcDateTime = None
    finished_at: OptionalUtcDateTime = None


class PipelineOut(BaseModel):
    id: str
    name: str
    owner: str
    status: TaskStatus
    config: dict
    failure_reason: str | None = None
    submitted_at: UtcDateTime
    started_at: OptionalUtcDateTime = None
    finished_at: OptionalUtcDateTime = None
    progress: float = 0.0
    current_stage: str | None = None
    stages: list[PipelineStageOut] = Field(default_factory=list)
    artifacts: list[ArtifactOut] = Field(default_factory=list)


class PipelineListResponse(BaseModel):
    items: list[PipelineOut]
    total: int
    page: int
    page_size: int
    pages: int


class WebhookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=8, max_length=500)
    secret: str | None = Field(default=None, max_length=120)
    events: list[str] = Field(
        default_factory=lambda: [
            "task.succeeded",
            "task.failed",
            "task.cancelled",
            "pipeline.succeeded",
            "pipeline.failed",
        ],
        max_length=32,
    )
    active: bool = True

    @field_validator("url")
    @classmethod
    def valid_http_url(cls, value: str) -> str:
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return value.strip()


class WebhookUpdate(BaseModel):
    active: bool


class WebhookEndpointOut(BaseModel):
    id: str
    name: str
    url: str
    secret: str
    events: list[str]
    active: bool
    created_at: UtcDateTime
    delivery_count: int = 0
    pending_count: int = 0
    success_count: int = 0
    failed_count: int = 0


class WebhookDeliveryOut(BaseModel):
    id: str
    endpoint_id: str
    event_type: str
    task_id: str | None = None
    pipeline_id: str | None = None
    status: str
    attempts: int
    max_attempts: int
    next_attempt_at: UtcDateTime
    response_code: int | None = None
    response_excerpt: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class WebhookListResponse(BaseModel):
    items: list[WebhookEndpointOut]
    total: int


class WebhookDeliveryList(BaseModel):
    items: list[WebhookDeliveryOut]
    total: int