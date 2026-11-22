"""Serial preprocess → train → evaluate pipelines.

The pipeline coordinator is driven by the existing scheduler tick, so a stage
task is created only after the previous stage's Task reached a terminal success
state. All stage tasks use the normal CPU scheduler, heartbeat and retry paths.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select

from app.config import Settings
from app.database import Database, utcnow
from app.models import DatasetRecord, Node, Pipeline, PipelineStage, Task, TaskStatus
from app.schemas import (
    ArtifactOut,
    PipelineCreate,
    PipelineListResponse,
    PipelineOut,
    PipelineStageOut,
    PipelineStageTaskSummary,
)
from app.services.artifacts import ArtifactService
from app.services.event_bus import EventBroker
from app.services.logs import LogStore
from app.services.manifests import RunManifestService
from app.services.tasks import add_event, runtime_seconds
from app.services.webhooks import enqueue_pipeline_event
from app.training.config import (
    TrainingAssetError,
    _display_path,
    list_datasets,
    resolve_dataset_path,
    resolve_model_path,
)

logger = logging.getLogger(__name__)

STAGE_KEYS = ("preprocess", "train", "evaluate")
STAGE_LABELS = {"preprocess": "预处理", "train": "训练", "evaluate": "评估"}


class PipelineError(ValueError):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class PipelineNotFound(LookupError):
    pass


class PipelineService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        log_store: LogStore,
        broker: EventBroker,
        artifacts: ArtifactService,
    ) -> None:
        self.settings = settings
        self.db = db
        self.log_store = log_store
        self.broker = broker
        self.artifacts = artifacts
        self.manifests = RunManifestService(settings)

    # ------------------------------------------------------------------ helpers
    def _id(self, session, prefix: str = "pl") -> str:
        for _ in range(8):
            candidate = prefix + uuid.uuid4().hex[:12]
            if prefix == "pl":
                exists = session.get(Pipeline, candidate)
            elif prefix == "ps":
                exists = session.get(PipelineStage, candidate)
            else:
                exists = session.get(Task, candidate)
            if exists is None:
                return candidate
        raise PipelineError("could not allocate an id", 500)

    def _task_id(self, session) -> str:
        for _ in range(8):
            candidate = "t" + uuid.uuid4().hex[:12]
            if session.get(Task, candidate) is None:
                return candidate
        raise PipelineError("could not allocate a task id", 500)

    def _dataset_relative_path(self, pipeline_id: str) -> str:
        return f"pipelines/{pipeline_id}/dataset.yaml"

    def _dataset_output_dir(self, pipeline_id: str) -> Path:
        return self.settings.datasets_dir / "pipelines" / pipeline_id

    def _display(self, path: Path) -> str:
        return _display_path(path, self.settings)

    def _stage_command(
        self,
        *,
        stage_key: str,
        task_id: str,
        pipeline_id: str,
        config: dict[str, Any],
        pipeline_cfg: dict[str, Any],
    ) -> str:
        if stage_key == "preprocess":
            source = self._display(resolve_dataset_path(self.settings, pipeline_cfg["source_dataset"]))
            output = self._display(self._dataset_output_dir(pipeline_id))
            dedupe_flag = "--dedupe" if pipeline_cfg.get("dedupe", True) else "--no-dedupe"
            return (
                f"python -m app.training.worker --kind preprocess --task-id {task_id} "
                f"--source-data {source} --output-dir {output} "
                f"--max-images {config.get('max_images', pipeline_cfg.get('max_images', 24))} "
                f"--val-ratio {pipeline_cfg.get('val_ratio', 0.2)} "
                f"--seed {pipeline_cfg.get('seed', 42)} {dedupe_flag} "
                f"--cpu-cores {pipeline_cfg['cpu_cores']}"
            )
        if stage_key == "evaluate":
            weights = config.get("weights_path") or ""
            data = self._dataset_relative_path(pipeline_id)
            return (
                f"python -m app.training.worker --kind evaluate --task-id {task_id} "
                f"--weights {weights} --data {data} --imgsz {pipeline_cfg['imgsz']} "
                f"--batch {pipeline_cfg['batch_size']} --split {pipeline_cfg.get('eval_split', 'val')} "
                f"--conf {pipeline_cfg.get('eval_conf', 0.001)} --iou {pipeline_cfg.get('eval_iou', 0.6)} "
                f"--max-det {pipeline_cfg.get('eval_max_det', 300)} --cpu-cores {pipeline_cfg['cpu_cores']} "
                f"--project {self._display(self.settings.runs_dir)} --name {task_id}"
            )
        # train stage: keep the familiar YOLO CLI shape in the log preview
        data = self._dataset_relative_path(pipeline_id)
        return (
            f"python -m app.training.worker --kind train --task-id {task_id} "
            f"--model {pipeline_cfg['model']} --data {data} "
            f"--epochs {pipeline_cfg['epochs']} --imgsz {pipeline_cfg['imgsz']} "
            f"--batch {pipeline_cfg['batch_size']} --workers {pipeline_cfg['workers']} "
            f"--device cpu --cpu-cores {pipeline_cfg['cpu_cores']}"
        )

    def _create_stage_task(
        self,
        session,
        *,
        pipeline: Pipeline,
        stage: PipelineStage,
        stage_config: dict[str, Any],
        pipeline_cfg: dict[str, Any],
    ) -> Task:
        task_id = self._task_id(session)
        run_dir = self.settings.runs_dir / task_id
        now = utcnow()
        dataset = (
            pipeline_cfg["source_dataset"]
            if stage.stage_key == "preprocess"
            else self._dataset_relative_path(pipeline.id)
        )
        command = self._stage_command(
            stage_key=stage.stage_key,
            task_id=task_id,
            pipeline_id=pipeline.id,
            config=stage_config,
            pipeline_cfg=pipeline_cfg,
        )
        task = Task(
            id=task_id,
            name=f"[{pipeline.name}] {STAGE_LABELS[stage.stage_key]}",
            owner=pipeline.owner,
            status=TaskStatus.PENDING.value,
            priority=int(pipeline_cfg["priority"]),
            model=pipeline_cfg["model"],
            dataset=dataset,
            epochs=int(pipeline_cfg["epochs"]),
            imgsz=int(pipeline_cfg["imgsz"]),
            batch_size=int(pipeline_cfg["batch_size"]),
            workers=int(pipeline_cfg.get("workers", 0)),
            device="cpu",
            command=command,
            notes=f"pipeline {pipeline.id} · stage {stage.stage_key}",
            cpu_cores=int(pipeline_cfg["cpu_cores"]),
            max_runtime_seconds=int(pipeline_cfg["max_runtime_seconds"]),
            max_retries=int(pipeline_cfg.get("max_retries", 0)),
            retry_count=0,
            submitted_at=now,
            queued_at=now,
            log_path=str(self.log_store.path_for(task_id)),
            result_path=str(run_dir / "result.json"),
            worker_token=secrets.token_urlsafe(32),
            config_json=json.dumps(
                {
                    **pipeline_cfg,
                    "pipeline_id": pipeline.id,
                    "stage": stage.stage_key,
                    "stage_config": stage_config,
                    "dataset": dataset,
                },
                ensure_ascii=False,
            ),
        )
        session.add(task)
        # Insert the Task first so the PipelineStage FK can reference it; the
        # models intentionally have no ORM relationship, so the unit of work
        # cannot infer ordering unless we flush explicitly.
        session.flush()
        source_record = None
        source_dataset_id = pipeline_cfg.get("source_dataset_id")
        if source_dataset_id:
            source_record = session.get(DatasetRecord, source_dataset_id)
        if source_record is not None:
            task.dataset_id = source_record.id
            task.dataset_sha256 = source_record.content_sha256
        stage.task_id = task.id
        stage.status = TaskStatus.PENDING.value
        stage.config_json = json.dumps(stage_config, ensure_ascii=False)
        session.flush()
        try:
            model_path = resolve_model_path(self.settings, task.model)
            self.manifests.persist(session, task, model_path=model_path, dataset=source_record)
        except Exception:
            logger.exception("could not create manifest for pipeline task %s", task.id)
        add_event(
            session,
            task.id,
            "submitted",
            f"Pipeline {pipeline.name} submitted stage {stage.stage_key} "
            f"with {task.cpu_cores} CPU core(s)",
        )
        return task

    def _log_task_start(self, task: Task, pipeline: Pipeline | None = None) -> None:
        self.log_store.header(
            task.id,
            name=task.name,
            command=task.command,
            cpu_cores=task.cpu_cores,
            owner=task.owner,
            submitted_at=task.submitted_at,
        )
        suffix = f" · pipeline {pipeline.id}" if pipeline else ""
        self.log_store.append(
            task.id,
            f"[platform] pipeline stage queued{suffix} · requested {task.cpu_cores} CPU core(s)",
        )
        self.broker.publish("task.created", task.id, {"status": "pending", "name": task.name})

    # ----------------------------------------------------------------- creation
    def create(self, payload: PipelineCreate) -> PipelineOut:
        runtime_cap = min(
            self.settings.default_max_runtime_seconds,
            self.settings.max_runtime_seconds_limit,
        )
        retry_cap = max(0, min(5, self.settings.default_max_retries))
        if payload.eval_split != "val":
            raise PipelineError("评估必须使用验证集 val，避免训练集指标失真")
        if payload.max_runtime_seconds > runtime_cap:
            raise PipelineError(
                f"max_runtime_seconds must be <= platform limit ({runtime_cap}s configured in admin)"
            )
        if payload.max_retries > retry_cap:
            raise PipelineError(
                f"max_retries must be <= platform limit ({retry_cap} configured in admin)"
            )
        if payload.cpu_cores > self.settings.total_cpus:
            raise PipelineError(f"cpu_cores must be <= {self.settings.total_cpus}")
        if payload.cpu_cores > self.settings.user_cpu_quota:
            raise PipelineError(f"cpu_cores must be <= user quota {self.settings.user_cpu_quota}")
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
            raise PipelineError("no online node is available")
        if payload.cpu_cores > max_node_cpus:
            raise PipelineError(
                f"cpu_cores must fit on a single node (largest online node: {max_node_cpus})"
            )
        try:
            source_path = resolve_dataset_path(self.settings, payload.source_dataset)
            model_path = resolve_model_path(self.settings, payload.model)
        except TrainingAssetError as exc:
            raise PipelineError(str(exc)) from exc
        source_name = Path(payload.source_dataset).name
        with self.db.session() as session:
            records = session.scalars(
                select(DatasetRecord).where(DatasetRecord.status != "deleted")
            ).all()
        record = next(
            (item for item in records if Path(item.yaml_path).name == source_name),
            None,
        )
        if record is not None:
            if record.quality_status in {"pending", "auditing"}:
                raise PipelineError("source dataset quality audit is still running", 409)
            if record.quality_status in {"blocked", "failed"}:
                raise PipelineError("source dataset is blocked by quality inspection", 422)
            if record.quality_status == "warning" and not record.quality_acknowledged:
                raise PipelineError("source dataset has unacknowledged quality warnings", 422)

        pipeline_id = "pl" + uuid.uuid4().hex[:12]
        name = payload.name or f"Pipeline · {Path(payload.source_dataset).stem} · {payload.epochs}ep"
        config = {
            "source_dataset": payload.source_dataset,
            "source_dataset_path": self._display(source_path),
            "source_dataset_id": record.id if record else None,
            "source_dataset_sha256": record.content_sha256 if record else None,
            "source_quality_status": record.quality_status if record else None,
            "model": Path(model_path).name,
            "epochs": payload.epochs,
            "imgsz": payload.imgsz,
            "batch_size": payload.batch_size,
            "workers": payload.workers,
            "cpu_cores": payload.cpu_cores,
            "priority": payload.priority,
            "max_runtime_seconds": payload.max_runtime_seconds,
            "max_retries": payload.max_retries,
            "max_images": payload.max_images,
            "val_ratio": payload.val_ratio,
            "seed": payload.seed,
            "dedupe": payload.dedupe,
            "eval_split": payload.eval_split,
            "eval_conf": payload.eval_conf,
            "eval_iou": payload.eval_iou,
            "eval_max_det": payload.eval_max_det,
            "notes": payload.notes,
        }
        now = utcnow()
        with self.db.session() as session:
            pipeline = Pipeline(
                id=pipeline_id,
                name=name,
                owner="demo",
                status=TaskStatus.PENDING.value,
                config_json=json.dumps(config, ensure_ascii=False),
                submitted_at=now,
            )
            session.add(pipeline)
            stages: dict[str, PipelineStage] = {}
            for index, key in enumerate(STAGE_KEYS):
                stage_config: dict[str, Any]
                if key == "preprocess":
                    stage_config = {
                        "source_dataset": payload.source_dataset,
                        "output_dir": str(self._dataset_output_dir(pipeline_id)),
                        "dataset_yaml": self._dataset_relative_path(pipeline_id),
                        "max_images": payload.max_images,
                        "val_ratio": payload.val_ratio,
                        "seed": payload.seed,
                        "dedupe": payload.dedupe,
                    }
                    status = TaskStatus.PENDING.value
                elif key == "train":
                    stage_config = {"dataset": self._dataset_relative_path(pipeline_id)}
                    status = "waiting"
                else:
                    stage_config = {
                        "eval_split": payload.eval_split,
                        "eval_conf": payload.eval_conf,
                        "eval_iou": payload.eval_iou,
                        "eval_max_det": payload.eval_max_det,
                    }
                    status = "waiting"
                stage = PipelineStage(
                    id="ps" + uuid.uuid4().hex[:12],
                    pipeline_id=pipeline_id,
                    stage_key=key,
                    sequence=index,
                    status=status,
                    config_json=json.dumps(stage_config, ensure_ascii=False),
                )
                session.add(stage)
                stages[key] = stage
            session.flush()
            first_task = self._create_stage_task(
                session,
                pipeline=pipeline,
                stage=stages["preprocess"],
                stage_config=json.loads(stages["preprocess"].config_json),
                pipeline_cfg=config,
            )
            enqueue_pipeline_event(
                session,
                pipeline,
                "pipeline.created",
                f"Pipeline created with {payload.cpu_cores} CPU core(s)",
            )
            session.flush()
            task_id = first_task.id
            # Write logs after the transaction commits.
        task = self.tasks_raw(task_id)
        self._log_task_start(task, self._pipeline_raw(pipeline_id))
        self.wake_up()
        logger.info("pipeline %s created with %s", pipeline_id, name)
        return self.get(pipeline_id)

    def _pipeline_raw(self, pipeline_id: str) -> Pipeline:
        with self.db.session() as session:
            pipeline = session.get(Pipeline, pipeline_id)
            if pipeline is None:
                raise PipelineNotFound(pipeline_id)
            session.expunge(pipeline)
            return pipeline

    def tasks_raw(self, task_id: str) -> Task:
        with self.db.session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise PipelineNotFound(task_id)
            session.expunge(task)
            return task

    def wake_up(self) -> None:
        # The actual scheduler is wired after construction; this lightweight hook
        # is replaced by AppContext.
        callback = getattr(self, "wake_callback", None)
        if callback:
            callback()

    # ---------------------------------------------------------------- coordinator
    def advance(self) -> None:
        created_logs: list[tuple[str, str | None]] = []
        published: list[tuple[str, str, str]] = []
        with self.db.session() as session:
            pipelines = session.scalars(
                select(Pipeline)
                .where(Pipeline.status.in_([TaskStatus.PENDING.value, TaskStatus.RUNNING.value]))
                .order_by(Pipeline.submitted_at.asc())
            ).all()
            for pipeline in pipelines:
                try:
                    result = self._advance_pipeline(session, pipeline)
                    created_logs.extend(result["created_logs"])
                    published.extend(result["published"])
                except Exception:
                    logger.exception("pipeline advance failed for %s", pipeline.id)
            session.flush()
        for task_id, pipeline_id in created_logs:
            try:
                with self.db.session() as session:
                    task = session.get(Task, task_id)
                    pipeline = session.get(Pipeline, pipeline_id) if pipeline_id else None
                    if task is not None:
                        self._log_task_start(task, pipeline)
            except Exception:
                logger.exception("failed to write pipeline stage log header for %s", task_id)
        for event, pipeline_id, message in published:
            self.broker.publish(event, pipeline_id, {"message": message})

    def _advance_pipeline(self, session, pipeline: Pipeline) -> dict[str, Any]:
        created_logs: list[tuple[str, str | None]] = []
        published: list[tuple[str, str, str]] = []
        stages = session.scalars(
            select(PipelineStage)
            .where(PipelineStage.pipeline_id == pipeline.id)
            .order_by(PipelineStage.sequence.asc())
        ).all()
        if not stages:
            return {"created_logs": created_logs, "published": published}
        old_status = pipeline.status
        for stage in stages:
            if stage.task_id:
                task = session.get(Task, stage.task_id)
                if task is None:
                    continue
                stage.status = task.status
                if task.started_at and stage.started_at is None:
                    stage.started_at = task.started_at
                if task.finished_at:
                    stage.finished_at = task.finished_at
                if task.status == TaskStatus.FAILED.value:
                    stage.message = task.failure_reason
                if task.status == TaskStatus.RUNNING.value:
                    pipeline.started_at = pipeline.started_at or task.started_at or utcnow()
                    if pipeline.status == TaskStatus.PENDING.value:
                        pipeline.status = TaskStatus.RUNNING.value
                if task.status == TaskStatus.SUCCEEDED.value:
                    stage.status = TaskStatus.SUCCEEDED.value
                    stage.finished_at = stage.finished_at or task.finished_at or utcnow()
                    next_stage = next((item for item in stages if item.sequence > stage.sequence), None)
                    if next_stage is None:
                        if pipeline.status != TaskStatus.SUCCEEDED.value:
                            pipeline.status = TaskStatus.SUCCEEDED.value
                            pipeline.finished_at = utcnow()
                            pipeline.failure_reason = None
                            enqueue_pipeline_event(
                                session,
                                pipeline,
                                "pipeline.succeeded",
                                f"Pipeline {pipeline.name} completed all 3 stages",
                            )
                            published.append(("pipeline.succeeded", pipeline.id, "all stages completed"))
                    elif next_stage.task_id is None:
                        cfg = json.loads(pipeline.config_json)
                        stage_cfg = json.loads(next_stage.config_json or "{}")
                        if next_stage.stage_key == "train":
                            expected = self._dataset_output_dir(pipeline.id) / "dataset.yaml"
                            if not expected.is_file():
                                self._fail_pipeline(
                                    session,
                                    pipeline,
                                    stages,
                                    "preprocess_output_missing",
                                    "Preprocessing finished but dataset.yaml was not produced",
                                )
                                break
                            stage_cfg = {"dataset": self._dataset_relative_path(pipeline.id)}
                        elif next_stage.stage_key == "evaluate":
                            weights_task = task
                            if weights_task is None:
                                self._fail_pipeline(
                                    session, pipeline, stages, "train_task_missing",
                                    "Training task reference missing",
                                )
                                break
                            weights = Path(weights_task.result_path).parent / "weights" / "best.pt"
                            if not weights.is_file():
                                self._fail_pipeline(
                                    session,
                                    pipeline,
                                    stages,
                                    "weights_missing",
                                    f"Training finished but best.pt was not found at {weights}",
                                )
                                break
                            try:
                                existing_cfg = json.loads(next_stage.config_json or "{}")
                            except json.JSONDecodeError:
                                existing_cfg = {}
                            stage_cfg = {
                                **existing_cfg,
                                "weights_task_id": weights_task.id,
                                "weights_path": str(weights),
                            }
                        next_stage.config_json = json.dumps(stage_cfg, ensure_ascii=False)
                        new_task = self._create_stage_task(
                            session,
                            pipeline=pipeline,
                            stage=next_stage,
                            stage_config=stage_cfg,
                            pipeline_cfg=cfg,
                        )
                        created_logs.append((new_task.id, pipeline.id))
                elif task.status in (TaskStatus.FAILED.value, TaskStatus.CANCELLED.value):
                    if pipeline.status not in (
                        TaskStatus.FAILED.value,
                        TaskStatus.CANCELLED.value,
                        TaskStatus.SUCCEEDED.value,
                    ):
                        final = task.status
                        pipeline.status = final
                        pipeline.failure_reason = task.failure_reason
                        pipeline.finished_at = utcnow()
                        for later in stages:
                            if later.sequence > stage.sequence and later.status != TaskStatus.SUCCEEDED.value:
                                later.status = TaskStatus.CANCELLED.value
                                later.finished_at = later.finished_at or utcnow()
                        event = f"pipeline.{final}"
                        enqueue_pipeline_event(
                            session,
                            pipeline,
                            event,
                            f"Pipeline {pipeline.name} {final} at {stage.stage_key}: "
                            f"{task.failure_reason or task.status}",
                        )
                        published.append((event, pipeline.id, task.failure_reason or final))
                        break
        if pipeline.status == TaskStatus.RUNNING.value and old_status == TaskStatus.PENDING.value:
            enqueue_pipeline_event(session, pipeline, "pipeline.running", "Pipeline started")
            published.append(("pipeline.running", pipeline.id, "started"))
        session.flush()
        return {"created_logs": created_logs, "published": published}

    def _fail_pipeline(self, session, pipeline: Pipeline, stages: list[PipelineStage], code: str, message: str) -> None:
        pipeline.status = TaskStatus.FAILED.value
        pipeline.failure_reason = code
        pipeline.finished_at = utcnow()
        for stage in stages:
            if stage.status not in (TaskStatus.SUCCEEDED.value,):
                stage.status = TaskStatus.CANCELLED.value
                stage.finished_at = stage.finished_at or utcnow()
        enqueue_pipeline_event(session, pipeline, "pipeline.failed", f"{code}: {message}")

    def resume_pipeline(self, pipeline_id: str) -> PipelineOut:
        """Reconcile a terminal pipeline with completed stage tasks and resume it."""
        with self.db.session() as session:
            pipeline = session.get(Pipeline, pipeline_id)
            if pipeline is None:
                raise PipelineNotFound(pipeline_id)
            if pipeline.status not in (TaskStatus.FAILED.value, TaskStatus.CANCELLED.value):
                raise PipelineError("only failed or cancelled pipelines can be resumed", 409)
            stages = session.scalars(
                select(PipelineStage)
                .where(PipelineStage.pipeline_id == pipeline.id)
                .order_by(PipelineStage.sequence.asc())
            ).all()
            last_succeeded = -1
            for stage in stages:
                if stage.task_id:
                    task = session.get(Task, stage.task_id)
                    if task is not None:
                        stage.status = task.status
                        stage.started_at = stage.started_at or task.started_at
                        stage.finished_at = task.finished_at
                        stage.message = task.failure_reason if task.status == TaskStatus.FAILED.value else None
                        if task.status == TaskStatus.SUCCEEDED.value:
                            last_succeeded = max(last_succeeded, stage.sequence)
            if last_succeeded < 0:
                raise PipelineError("pipeline has no successful stage to resume from", 409)
            for stage in stages:
                if stage.sequence > last_succeeded:
                    stage.task_id = None
                    stage.status = "waiting"
                    stage.started_at = None
                    stage.finished_at = None
                    stage.message = None
            pipeline.status = TaskStatus.PENDING.value
            pipeline.started_at = None
            pipeline.finished_at = None
            pipeline.failure_reason = None
            enqueue_pipeline_event(
                session,
                pipeline,
                "pipeline.reopened",
                f"Pipeline resumed after stage {last_succeeded + 1}",
            )
            session.flush()
            return self._to_schema(session, pipeline, include_artifacts=True)

    def reopen_for_task(self, task_id: str) -> PipelineOut | None:
        """Reopen a terminal pipeline when one of its stage tasks is rerun.

        The rerun keeps the same task id. Later stages are reset to waiting so the
        coordinator can create fresh evaluate/train tasks after this attempt.
        """
        with self.db.session() as session:
            stage = session.scalar(
                select(PipelineStage).where(PipelineStage.task_id == task_id)
            )
            if stage is None:
                return None
            pipeline = session.get(Pipeline, stage.pipeline_id)
            if pipeline is None:
                return None
            if pipeline.status not in (
                TaskStatus.SUCCEEDED.value,
                TaskStatus.FAILED.value,
                TaskStatus.CANCELLED.value,
            ):
                return None
            stages = session.scalars(
                select(PipelineStage)
                .where(PipelineStage.pipeline_id == pipeline.id)
                .order_by(PipelineStage.sequence.asc())
            ).all()
            pipeline.status = TaskStatus.PENDING.value
            pipeline.started_at = None
            pipeline.finished_at = None
            pipeline.failure_reason = None
            stage.status = TaskStatus.PENDING.value
            stage.started_at = None
            stage.finished_at = None
            stage.message = None
            for later in stages:
                if later.sequence > stage.sequence:
                    later.task_id = None
                    later.status = "waiting"
                    later.started_at = None
                    later.finished_at = None
                    later.message = None
            enqueue_pipeline_event(
                session,
                pipeline,
                "pipeline.reopened",
                f"Pipeline reopened by rerun of {stage.stage_key} task {task_id}",
            )
            session.flush()
            return self._to_schema(session, pipeline, include_artifacts=True)

    # -------------------------------------------------------------------- reads
    def _stage_out(self, session, stage: PipelineStage) -> PipelineStageOut:
        task_summary = None
        metrics: dict[str, float] = {}
        details: dict = {}
        if stage.task_id:
            task = session.get(Task, stage.task_id)
            if task is not None:
                task_summary = PipelineStageTaskSummary(
                    id=task.id,
                    name=task.name,
                    status=TaskStatus(task.status),
                    progress=task.progress or 0.0,
                    runtime_seconds=runtime_seconds(task),
                    failure_reason=task.failure_reason,
                    log_size_bytes=self.log_store.size(task.id),
                )
                result_path = Path(task.result_path)
                if result_path.is_file():
                    try:
                        result = json.loads(result_path.read_text(encoding="utf-8"))
                        metrics = {
                            str(key): float(value)
                            for key, value in (result.get("metrics") or {}).items()
                            if isinstance(value, (int, float))
                        }
                    except (OSError, json.JSONDecodeError, TypeError, ValueError):
                        metrics = {}
                report_path: Path | None = None
                if stage.stage_key == "preprocess":
                    try:
                        stage_config = json.loads(stage.config_json or "{}")
                        if stage_config.get("output_dir"):
                            report_path = Path(stage_config["output_dir"]) / "preprocess.json"
                    except json.JSONDecodeError:
                        report_path = None
                elif stage.stage_key == "evaluate":
                    report_path = result_path.parent / "evaluation.json"
                if report_path is not None and report_path.is_file():
                    try:
                        report = json.loads(report_path.read_text(encoding="utf-8"))
                        if isinstance(report, dict):
                            details = report
                    except (OSError, json.JSONDecodeError):
                        details = {}
        return PipelineStageOut(
            id=stage.id,
            stage_key=stage.stage_key,
            sequence=stage.sequence,
            status=stage.status,
            message=stage.message,
            task=task_summary,
            metrics=metrics,
            details=details,
            started_at=stage.started_at,
            finished_at=stage.finished_at,
        )

    def _to_schema(self, session, pipeline: Pipeline, *, include_artifacts: bool = False) -> PipelineOut:
        stages = session.scalars(
            select(PipelineStage)
            .where(PipelineStage.pipeline_id == pipeline.id)
            .order_by(PipelineStage.sequence.asc())
        ).all()
        stage_outs = [self._stage_out(session, stage) for stage in stages]
        done = sum(1 for stage in stages if stage.status == TaskStatus.SUCCEEDED.value)
        running = sum(1 for stage in stages if stage.status == TaskStatus.RUNNING.value)
        progress = min(100.0, ((done + 0.5 * running) / max(1, len(stages))) * 100)
        current = next(
            (
                stage.stage_key
                for stage in stages
                if stage.status not in (TaskStatus.SUCCEEDED.value, "cancelled")
            ),
            None,
        )
        artifacts: list[ArtifactOut] = []
        if include_artifacts:
            artifacts = self.artifacts.list(pipeline_id=pipeline.id)
        return PipelineOut(
            id=pipeline.id,
            name=pipeline.name,
            owner=pipeline.owner,
            status=TaskStatus(pipeline.status),
            config=json.loads(pipeline.config_json),
            failure_reason=pipeline.failure_reason,
            submitted_at=pipeline.submitted_at,
            started_at=pipeline.started_at,
            finished_at=pipeline.finished_at,
            progress=round(progress, 1),
            current_stage=current,
            stages=stage_outs,
            artifacts=artifacts,
        )

    def get(self, pipeline_id: str) -> PipelineOut:
        with self.db.session() as session:
            pipeline = session.get(Pipeline, pipeline_id)
            if pipeline is None:
                raise PipelineNotFound(pipeline_id)
            return self._to_schema(session, pipeline, include_artifacts=True)

    def list(
        self,
        page: int = 1,
        page_size: int = 10,
        *,
        status: str | None = None,
        query: str | None = None,
    ) -> PipelineListResponse:
        page = max(1, page)
        page_size = max(1, min(50, page_size))
        conditions = []
        if status and status != "all":
            conditions.append(Pipeline.status == status)
        needle = (query or "").strip()
        if needle:
            pattern = f"%{needle}%"
            conditions.append(
                or_(
                    Pipeline.id.ilike(pattern),
                    Pipeline.name.ilike(pattern),
                    Pipeline.config_json.ilike(pattern),
                )
            )
        with self.db.session() as session:
            total = int(
                session.scalar(
                    select(func.count(Pipeline.id)).where(*conditions)
                )
                or 0
            )
            rows = session.scalars(
                select(Pipeline)
                .where(*conditions)
                .order_by(Pipeline.submitted_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            items = [self._to_schema(session, pipeline) for pipeline in rows]
        pages = (total + page_size - 1) // page_size if total else 0
        return PipelineListResponse(items=items, total=total, page=page, page_size=page_size, pages=pages)

    def active_task_id(self, pipeline_id: str) -> str | None:
        with self.db.session() as session:
            pipeline = session.get(Pipeline, pipeline_id)
            if pipeline is None:
                raise PipelineNotFound(pipeline_id)
            stages = session.scalars(
                select(PipelineStage)
                .where(PipelineStage.pipeline_id == pipeline_id)
                .order_by(PipelineStage.sequence.asc())
            ).all()
            for stage in stages:
                if stage.task_id:
                    task = session.get(Task, stage.task_id)
                    if task and task.status in (TaskStatus.PENDING.value, TaskStatus.RUNNING.value):
                        return task.id
            return None

    def mark_cancelled(self, pipeline_id: str, reason: str = "cancelled_by_user") -> PipelineOut:
        with self.db.session() as session:
            pipeline = session.get(Pipeline, pipeline_id)
            if pipeline is None:
                raise PipelineNotFound(pipeline_id)
            if pipeline.status in (TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value):
                raise PipelineError(f"pipeline is already {pipeline.status}", 409)
            pipeline.status = TaskStatus.CANCELLED.value
            pipeline.failure_reason = reason
            pipeline.finished_at = utcnow()
            stages = session.scalars(
                select(PipelineStage).where(PipelineStage.pipeline_id == pipeline_id)
            ).all()
            for stage in stages:
                if stage.status not in (TaskStatus.SUCCEEDED.value,):
                    stage.status = TaskStatus.CANCELLED.value
                    stage.finished_at = stage.finished_at or utcnow()
            enqueue_pipeline_event(session, pipeline, "pipeline.cancelled", "Pipeline cancelled")
            session.flush()
            return self._to_schema(session, pipeline, include_artifacts=True)
