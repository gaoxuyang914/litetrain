"""CPU resource accounting and cluster snapshots."""

from __future__ import annotations

import logging
import platform
import socket
from datetime import datetime
from typing import TYPE_CHECKING

import psutil
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Database, utcnow
from app.models import (
    AllocationState,
    Node,
    ResourceAllocation,
    Task,
    TaskStatus,
)
from app.schemas import (
    ClusterOverview,
    CpuStats,
    MemoryStats,
    NodeInfo,
    QuotaInfo,
    ServicesInfo,
    TaskCounts,
)

if TYPE_CHECKING:
    from app.services.event_bus import EventBroker
    from app.services.executors import ExecutionManager

logger = logging.getLogger(__name__)


class ResourceService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.owner = "demo"
        # Prime the non-blocking psutil measurement.
        psutil.cpu_percent(interval=None)

    def ensure_node(self) -> Node:
        with self.db.session() as session:
            node = session.get(Node, self.settings.node_id)
            if node is None:
                node = Node(
                    id=self.settings.node_id,
                    name=self.settings.node_name,
                    hostname=socket.gethostname(),
                    total_cpus=self.settings.total_cpus,
                    status="online",
                    last_seen=utcnow(),
                )
                session.add(node)
            else:
                node.total_cpus = self.settings.total_cpus
                node.hostname = socket.gethostname()
                node.status = "online"
                node.last_seen = utcnow()
            session.flush()
            session.refresh(node)
            session.expunge(node)
            return node

    def get_node(self, session: Session) -> Node:
        node = session.get(Node, self.settings.node_id)
        if node is None:
            node = Node(
                id=self.settings.node_id,
                name=self.settings.node_name,
                hostname=socket.gethostname(),
                total_cpus=self.settings.total_cpus,
                status="online",
                last_seen=utcnow(),
            )
            session.add(node)
            session.flush()
        return node

    def allocated_cpus(self, session: Session, node_id: str | None = None) -> int:
        stmt = select(func.coalesce(func.sum(ResourceAllocation.cpus), 0)).where(
            ResourceAllocation.state == AllocationState.ACTIVE.value
        )
        if node_id:
            stmt = stmt.where(ResourceAllocation.node_id == node_id)
        return int(session.scalar(stmt) or 0)

    def owner_allocated_cpus(self, session: Session) -> int:
        stmt = (
            select(func.coalesce(func.sum(ResourceAllocation.cpus), 0))
            .join(Task, Task.id == ResourceAllocation.task_id)
            .where(
                ResourceAllocation.state == AllocationState.ACTIVE.value,
                Task.owner == self.owner,
            )
        )
        return int(session.scalar(stmt) or 0)

    def available_for(self, session: Session, owner: str, requested: int) -> bool:
        if requested > self.settings.total_cpus:
            return False
        node_total = self.settings.total_cpus
        used = self.allocated_cpus(session)
        if requested > max(0, node_total - used):
            return False
        owner_used = self.owner_allocated_cpus(session)
        if requested > max(0, self.settings.user_cpu_quota - owner_used):
            return False
        return True

    def release_allocation(self, session: Session, task_id: str) -> int:
        allocations = session.scalars(
            select(ResourceAllocation).where(
                ResourceAllocation.task_id == task_id,
                ResourceAllocation.state == AllocationState.ACTIVE.value,
            )
        ).all()
        now = utcnow()
        for allocation in allocations:
            allocation.state = AllocationState.RELEASED.value
            allocation.released_at = now
        return sum(item.cpus for item in allocations)

    def create_allocation(self, session: Session, task: Task) -> ResourceAllocation:
        node = self.get_node(session)
        allocation = ResourceAllocation(
            task_id=task.id,
            node_id=node.id,
            cpus=task.cpu_cores,
            state=AllocationState.ACTIVE.value,
            allocated_at=utcnow(),
        )
        session.add(allocation)
        return allocation

    def task_counts(self, session: Session) -> TaskCounts:
        rows = session.execute(select(Task.status, func.count(Task.id)).group_by(Task.status)).all()
        counts = {status.value: 0 for status in TaskStatus}
        for status, count in rows:
            counts[str(status)] = int(count)
        return TaskCounts(
            pending=counts["pending"],
            running=counts["running"],
            succeeded=counts["succeeded"],
            failed=counts["failed"],
            cancelled=counts["cancelled"],
            total=sum(counts.values()),
        )

    def overview(
        self,
        broker: EventBroker,
        execution: ExecutionManager,
        started_at: datetime,
    ) -> ClusterOverview:
        host_percent = round(psutil.cpu_percent(interval=None), 1)
        with self.db.session() as session:
            self.ensure_node_in_session(session)
            total = self.settings.total_cpus
            allocated = self.allocated_cpus(session)
            counts = self.task_counts(session)
            owner_used = self.owner_allocated_cpus(session)
            nodes: list[NodeInfo] = []
            all_nodes = session.scalars(select(Node).order_by(Node.name)).all()
            for node in all_nodes:
                node_allocated = self.allocated_cpus(session, node.id)
                running = session.scalar(
                    select(func.count(ResourceAllocation.id))
                    .join(Task, Task.id == ResourceAllocation.task_id)
                    .where(
                        ResourceAllocation.node_id == node.id,
                        ResourceAllocation.state == AllocationState.ACTIVE.value,
                        Task.status == TaskStatus.RUNNING.value,
                    )
                ) or 0
                nodes.append(
                    NodeInfo(
                        id=node.id,
                        name=node.name,
                        hostname=node.hostname,
                        status=node.status,
                        total_cpus=node.total_cpus,
                        allocated_cpus=node_allocated,
                        free_cpus=max(0, node.total_cpus - node_allocated),
                        running_tasks=int(running),
                        cpu_utilization_percent=round(
                            (node_allocated / node.total_cpus * 100) if node.total_cpus else 0.0, 1
                        ),
                        host_percent=host_percent,
                        last_seen=node.last_seen,
                    )
                )
            process_percent = float(
                session.scalar(
                    select(func.coalesce(func.sum(Task.latest_cpu_percent), 0.0)).where(
                        Task.status == TaskStatus.RUNNING.value
                    )
                )
                or 0.0
            )

        memory = psutil.virtual_memory()
        cpus = CpuStats(
            total=total,
            allocated=allocated,
            available=max(0, total - allocated),
            utilization_percent=round((allocated / total * 100) if total else 0.0, 1),
            host_percent=host_percent,
            host_logical_cores=psutil.cpu_count(logical=True) or total,
            process_percent=round(process_percent, 1),
        )
        quota = QuotaInfo(
            owner=self.owner,
            limit_cpus=self.settings.user_cpu_quota,
            used_cpus=owner_used,
            available_cpus=max(0, self.settings.user_cpu_quota - owner_used),
        )
        services = ServicesInfo(
            execution_backend=execution.backend,
            execution_mode=self.settings.execution_mode,
            ray_connected=execution.ray_connected,
            ray_address=execution.ray_address,
            ray_version=execution.ray_version,
            ray_cpus=execution.ray_cpus,
            redis_connected=broker.connected,
            redis_version=broker.redis_version(),
            scheduler_leader=bool(broker.connected),
        )
        return ClusterOverview(
            cpu=cpus,
            memory=MemoryStats(
                total_mb=round(memory.total / (1024 * 1024), 1),
                used_mb=round((memory.total - memory.available) / (1024 * 1024), 1),
                available_mb=round(memory.available / (1024 * 1024), 1),
                percent=float(memory.percent),
            ),
            tasks=counts,
            nodes=nodes,
            quota=quota,
            services=services,
            uptime_seconds=max(0, int((utcnow() - started_at).total_seconds())),
            updated_at=utcnow(),
        )

    def ensure_node_in_session(self, session: Session) -> Node:
        return self.get_node(session)

    def host_summary(self) -> dict[str, str | int]:
        try:
            return {
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "logical_cores": psutil.cpu_count(logical=True) or 0,
                "physical_cores": psutil.cpu_count(logical=False) or 0,
            }
        except Exception:
            return {"hostname": socket.gethostname(), "platform": platform.platform()}
