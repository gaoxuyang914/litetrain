"""Composition root for the backend services."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from app.config import Settings
from app.database import Database, utcnow
from app.services.event_bus import EventBroker
from app.services.executors import ExecutionManager
from app.services.logs import LogStore
from app.services.resources import ResourceService
from app.services.scheduler import Scheduler
from app.services.tasks import TaskService

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
        execution = ExecutionManager(settings)
        tasks = TaskService(settings, db, log_store, broker)
        scheduler = Scheduler(settings, db, log_store, broker, resources, execution, tasks)
        return cls(
            settings=settings,
            db=db,
            broker=broker,
            log_store=log_store,
            resources=resources,
            execution=execution,
            tasks=tasks,
            scheduler=scheduler,
            started_at=utcnow(),
        )

    def initialize(self) -> None:
        self.db.init()
        self.broker.connect()
        self.execution.connect()
        self.resources.ensure_node()
        self.scheduler.recover()

    def shutdown(self) -> None:
        try:
            self.scheduler.stop()
        except Exception:
            logger.exception("scheduler shutdown failed")
        self.execution.shutdown()
        self.broker.close()
        self.db.dispose()
