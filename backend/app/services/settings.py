"""Runtime policy settings persisted in SQLite.

Environment variables remain the bootstrap defaults.  Values saved here are
applied to the shared Settings object on startup and can be changed from the
settings panel while the scheduler is running.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.config import Settings
from app.database import Database, utcnow
from app.models import AllocationState, Node, ResourceAllocation, RuntimeSetting, Task
from app.schemas import RuntimeSettingsOut, RuntimeSettingsUpdate


class RuntimeSettingsService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db

    def _limits(self) -> dict[str, list[int]]:
        max_default_cpu = max(1, min(self.settings.total_cpus, self.settings.user_cpu_quota))
        return {
            "default_max_runtime_seconds": [10, self.settings.max_runtime_seconds_limit],
            "heartbeat_timeout_seconds": [5, 600],
            "default_max_retries": [0, 5],
            "default_cpu_cores": [1, max_default_cpu],
            "user_cpu_quota": [1, self.settings.total_cpus],
        }

    def load(self) -> None:
        with self.db.session() as session:
            rows = session.scalars(select(RuntimeSetting)).all()
        limits = self._limits()
        for row in rows:
            key = row.key
            if key not in limits:
                continue
            try:
                value = int(row.value)
            except (TypeError, ValueError):
                continue
            low, high = limits[key]
            if not low <= value <= high:
                continue
            setattr(self.settings, key, value)
        self.settings.user_cpu_quota = min(self.settings.user_cpu_quota, self.settings.total_cpus)
        self.settings.default_cpu_cores = min(
            self.settings.default_cpu_cores,
            self.settings.total_cpus,
            self.settings.user_cpu_quota,
        )

    def snapshot(self) -> RuntimeSettingsOut:
        with self.db.session() as session:
            updated_at = session.scalar(select(func.max(RuntimeSetting.updated_at)))
        return RuntimeSettingsOut(
            default_max_runtime_seconds=self.settings.default_max_runtime_seconds,
            heartbeat_timeout_seconds=self.settings.heartbeat_timeout_seconds,
            default_max_retries=self.settings.default_max_retries,
            default_cpu_cores=self.settings.default_cpu_cores,
            user_cpu_quota=self.settings.user_cpu_quota,
            total_cpus=self.settings.total_cpus,
            max_runtime_seconds_limit=self.settings.max_runtime_seconds_limit,
            limits=self._limits(),
            updated_at=updated_at,
        )

    def update(self, payload: RuntimeSettingsUpdate) -> RuntimeSettingsOut:
        values = payload.model_dump(exclude_none=True)
        if not values:
            return self.snapshot()

        limits = self._limits()
        for key, value in values.items():
            low, high = limits[key]
            if not low <= value <= high:
                raise ValueError(f"{key} must be between {low} and {high}")

        total_cpus = self.settings.total_cpus
        quota = values.get("user_cpu_quota", self.settings.user_cpu_quota)
        default_cpu = values.get("default_cpu_cores", self.settings.default_cpu_cores)
        if quota > total_cpus:
            raise ValueError(f"user_cpu_quota must be <= {total_cpus}")
        if default_cpu > quota:
            raise ValueError("default_cpu_cores cannot exceed user_cpu_quota")

        with self.db.session() as session:
            used = int(
                session.scalar(
                    select(func.coalesce(func.sum(ResourceAllocation.cpus), 0))
                    .join(Task, Task.id == ResourceAllocation.task_id)
                    .join(Node, Node.id == ResourceAllocation.node_id)
                    .where(
                        Task.owner == "demo",
                        ResourceAllocation.state == AllocationState.ACTIVE.value,
                    )
                )
                or 0
            )
            if quota < used:
                raise ValueError(
                    f"user_cpu_quota cannot be lower than the {used} CPUs currently allocated"
                )
            for key, value in values.items():
                row = session.get(RuntimeSetting, key)
                if row is None:
                    session.add(RuntimeSetting(key=key, value=str(value)))
                else:
                    row.value = str(value)
                    row.updated_at = utcnow()
            session.flush()

        for key, value in values.items():
            setattr(self.settings, key, value)
        return self.snapshot()
