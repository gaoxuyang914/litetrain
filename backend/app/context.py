"""Composition root for the backend services."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from app.config import Settings
from app.database import Database, utcnow
from app.services.artifacts import ArtifactService
from app.services.datasets import DatasetService
from app.services.event_bus import EventBroker
from app.services.executors import ExecutionManager
from app.services.logs import LogStore
from app.services.pipelines import PipelineService
from app.services.resources import ResourceService
from app.services.scheduler import Scheduler
from app.services.tasks import TaskService
from app.services.webhooks import (
    WebhookDispatcher,
    WebhookService,
    configure_max_attempts,
)

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    settings: Settings
    db: Database
    broker: EventBroker
    log_store: LogStore
    resources: ResourceService
    execution: ExecutionManager
    tasks: TaskService
    scheduler: Scheduler
    datasets: DatasetService
    artifacts: ArtifactService
    pipelines: PipelineService
    webhooks: WebhookService
    webhook_dispatcher: WebhookDispatcher
    started_at: datetime

    @classmethod
    def build(cls, settings: Settings) -> AppContext:
        settings.ensure_directories()
        db = Database(settings.database_url)
        log_store = LogStore(settings.logs_dir)
        broker = EventBroker(
            settings.redis_url,
            protocol=settings.redis_protocol,
            socket_timeout=settings.redis_socket_timeout,
            enabled=settings.redis_enabled,
        )
        resources = ResourceService(settings, db)
        execution = ExecutionManager(settings, db)
        tasks = TaskService(settings, db, log_store, broker)
        datasets = DatasetService(settings, db)
        artifacts = ArtifactService(settings, db)
        pipelines = PipelineService(settings, db, log_store, broker, artifacts)
        webhooks = WebhookService(settings, db)
        configure_max_attempts(settings.webhook_max_attempts)
        webhook_dispatcher = WebhookDispatcher(settings, db)
        scheduler = Scheduler(
            settings, db, log_store, broker, resources, execution, tasks, pipelines
        )
        pipelines.wake_callback = scheduler.wake
        return cls(
            settings=settings,
            db=db,
            broker=broker,
            log_store=log_store,
            resources=resources,
            execution=execution,
            tasks=tasks,
            scheduler=scheduler,
            datasets=datasets,
            artifacts=artifacts,
            pipelines=pipelines,
            webhooks=webhooks,
            webhook_dispatcher=webhook_dispatcher,
            started_at=utcnow(),
        )

    def initialize(self) -> None:
        self.db.init()
        self.broker.connect()
        self.execution.connect()
        self.resources.ensure_node()
        self.datasets.seed_builtin()
        self.scheduler.recover()
        self.scheduler.pipelines = self.pipelines

    def shutdown(self) -> None:
        try:
            self.scheduler.stop()
        except Exception:
            logger.exception("scheduler shutdown failed")
        try:
            self.webhook_dispatcher.stop()
        except Exception:
            logger.exception("webhook dispatcher shutdown failed")
        self.execution.shutdown()
        self.broker.close()
        self.db.dispose()
