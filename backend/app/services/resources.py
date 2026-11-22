"""CPU resource accounting and cluster snapshots."""

from __future__ import annotations

import logging
import platform
import socket
import uuid
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
    NodeTaskInfo,
    QuotaInfo,
    ServicesInfo,
    TaskCounts,
)

if TYPE_CHECKING:
    from app.services.event_bus import EventBroker
    from app.services.executors import ExecutionManager

logger = logging.getLogger(__name__)


class ResourceError(ValueError):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ResourceService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.owner = "demo"
        # Prime the non-blocking psutil measurement.
        psutil.cpu_percent(interval=None)

    def ensure_node(self) -> Node | None:
        """Create the default node only on a fresh database.

        Simulated topologies must survive API restarts, so existing nodes are
        never overwritten back to a single-node view here.
        """
        with self.db.session() as session:
            nodes = session.scalars(select(Node).order_by(Node.created_at.asc())).all()
            if not nodes:
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
                session.refresh(node)
                session.expunge(node)
                return node
            primary = session.get(Node, self.settings.node_id)
            return primary

    def ensure_default_node(self, session: Session) -> Node | None:
        nodes = session.scalars(select(Node).order_by(Node.created_at.asc())).all()
        if not nodes:
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
        return session.get(Node, self.settings.node_id) or nodes[0]

    def get_node(self, session: Session) -> Node | None:
        return self.ensure_default_node(session)

    def online_nodes(self, session: Session) -> list[Node]:
        return list(
            session.scalars(
                select(Node).where(Node.status == "online").order_by(Node.created_at.asc(), Node.name.asc())
            ).all()
        )

    def cluster_total_cpus(self, session: Session) -> int:
        return int(sum(node.total_cpus for node in self.online_nodes(session)))

    def allocated_by_node(self, session: Session) -> dict[str, int]:
        rows = session.execute(
            select(ResourceAllocation.node_id, func.sum(ResourceAllocation.cpus))
            .where(ResourceAllocation.state == AllocationState.ACTIVE.value)
            .group_by(ResourceAllocation.node_id)
        ).all()
        return {str(node_id): int(total or 0) for node_id, total in rows}

    def allocated_cpus(self, session: Session, node_id: str | None = None) -> int:
        stmt = select(func.coalesce(func.sum(ResourceAllocation.cpus), 0)).where(
            ResourceAllocation.state == AllocationState.ACTIVE.value
        )
        if node_id:
            stmt = stmt.where(ResourceAllocation.node_id == node_id)
        return int(session.scalar(stmt) or 0)

    def owner_allocated_cpus(self, session: Session, owner: str | None = None) -> int:
        owner_name = owner or self.owner
        stmt = (
            select(func.coalesce(func.sum(ResourceAllocation.cpus), 0))
            .join(Task, Task.id == ResourceAllocation.task_id)
            .where(
                ResourceAllocation.state == AllocationState.ACTIVE.value,
                Task.owner == owner_name,
            )
        )
        return int(session.scalar(stmt) or 0)

    def choose_node(self, session: Session, owner: str, requested: int) -> Node | None:
        if requested <= 0:
            return None
        owner_used = self.owner_allocated_cpus(session, owner)
        if requested > max(0, self.settings.user_cpu_quota - owner_used):
            return None
        allocated = self.allocated_by_node(session)
        nodes = self.online_nodes(session)
        candidates = [
            node
            for node in nodes
            if requested <= max(0, node.total_cpus - allocated.get(node.id, 0))
        ]
        if not candidates:
            return None
        # Load-balance: prefer the node with the lowest current utilization.
        return min(
            candidates,
            key=lambda node: (
                (allocated.get(node.id, 0) / node.total_cpus) if node.total_cpus else 1.0,
                node.created_at,
                node.id,
            ),
        )

    def available_for(self, session: Session, owner: str, requested: int) -> bool:
        return self.choose_node(session, owner, requested) is not None

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

    def create_allocation(self, session: Session, task: Task, node: Node | None = None) -> ResourceAllocation:
        selected = node or self.choose_node(session, task.owner, task.cpu_cores)
        if selected is None:
            raise ResourceError("no node has enough free CPU for this task")
        allocation = ResourceAllocation(
            task_id=task.id,
            node_id=selected.id,
            cpus=task.cpu_cores,
            state=AllocationState.ACTIVE.value,
            allocated_at=utcnow(),
        )
        session.add(allocation)
        return allocation

    def apply_topology(self, topology: list[tuple[str, int]]) -> list[Node]:
        if not topology:
            raise ResourceError("at least one node is required", 422)
        names = [name.strip() for name, _ in topology]
        normalized = [name.lower() for name in names]
        if len(set(normalized)) != len(normalized):
            raise ResourceError("node names must be unique", 422)
        total = sum(cpus for _, cpus in topology)
        max_cpus = self.settings.total_cpus
        if total > max_cpus:
            raise ResourceError(
                f"simulated topology capacity {total} exceeds platform CPU budget {max_cpus}",
                422,
            )
        if any(cpus < 1 or cpus > max_cpus for _, cpus in topology):
            raise ResourceError("each node must have between 1 and platform CPU budget cores", 422)
        with self.db.session() as session:
            active = session.scalar(
                select(func.count(ResourceAllocation.id)).where(
                    ResourceAllocation.state == AllocationState.ACTIVE.value
                )
            ) or 0
            if active:
                raise ResourceError("cannot change topology while tasks have active allocations", 409)
            all_existing = session.scalars(select(Node)).all()
            for node in all_existing:
                historical = session.scalar(
                    select(func.count(ResourceAllocation.id)).where(
                        ResourceAllocation.node_id == node.id
                    )
                ) or 0
                if historical == 0:
                    session.delete(node)
                else:
                    node.status = "offline"
            session.flush()
            created: list[Node] = []
            for index, (name, cpus) in enumerate(topology, start=1):
                node = Node(
                    id="node-" + uuid.uuid4().hex[:10],
                    name=name.strip() or f"sim-node-{index}",
                    hostname=f"{socket.gethostname()} (sim {index})",
                    total_cpus=cpus,
                    status="online",
                    last_seen=utcnow(),
                )
                session.add(node)
                created.append(node)
            session.flush()
            for node in created:
                session.expunge(node)
            return created

    def set_node_status(self, node_id: str, status: str) -> Node:
        if status not in {"online", "offline"}:
            raise ResourceError("node status must be online or offline", 422)
        with self.db.session() as session:
            node = session.get(Node, node_id)
            if node is None:
                raise ResourceError("node not found", 404)
            active = session.scalar(
                select(func.count(ResourceAllocation.id)).where(
                    ResourceAllocation.node_id == node_id,
                    ResourceAllocation.state == AllocationState.ACTIVE.value,
                )
            ) or 0
            if status == "offline" and active:
                raise ResourceError("cannot take a node offline while tasks are allocated on it", 409)
            if status == "online" and node.status != "online":
                online_total = int(
                    session.scalar(
                        select(func.coalesce(func.sum(Node.total_cpus), 0)).where(
                            Node.status == "online",
                            Node.id != node_id,
                        )
                    )
                    or 0
                )
                if online_total + node.total_cpus > self.settings.total_cpus:
                    raise ResourceError(
                        f"bringing {node.name} online would exceed the {self.settings.total_cpus}-CPU platform budget",
                        409,
                    )
            node.status = status
            node.last_seen = utcnow()
            session.flush()
            session.expunge(node)
            return node

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
            self.ensure_default_node(session)
            total = self.cluster_total_cpus(session)
            allocated = self.allocated_cpus(session)
            counts = self.task_counts(session)
            owner_used = self.owner_allocated_cpus(session)
            allocated_by_node = self.allocated_by_node(session)
            nodes: list[NodeInfo] = []
            all_nodes = session.scalars(select(Node).order_by(Node.status.desc(), Node.name)).all()
            for node in all_nodes:
                if node.status == "online":
                    node.last_seen = utcnow()
                node_allocated = allocated_by_node.get(node.id, 0)
                active_tasks = [
                    NodeTaskInfo(
                        id=task_id,
                        name=task_name,
                        progress=float(progress or 0.0),
                        cpu_cores=int(cpu_cores),
                    )
                    for task_id, task_name, progress, cpu_cores in session.execute(
                        select(Task.id, Task.name, Task.progress, Task.cpu_cores)
                        .join(ResourceAllocation, ResourceAllocation.task_id == Task.id)
                        .where(
                            ResourceAllocation.node_id == node.id,
                            ResourceAllocation.state == AllocationState.ACTIVE.value,
                            Task.status == TaskStatus.RUNNING.value,
                        )
                        .order_by(Task.priority.desc(), Task.started_at.asc())
                    ).all()
                ]
                running = len(active_tasks)
                nodes.append(
                    NodeInfo(
                        id=node.id,
                        name=node.name,
                        hostname=node.hostname,
                        status=node.status,
                        total_cpus=node.total_cpus,
                        allocated_cpus=node_allocated,
                        free_cpus=max(0, node.total_cpus - node_allocated),
                        running_tasks=running,
                        active_tasks=active_tasks,
                        cpu_utilization_percent=round(
                            (node_allocated / node.total_cpus * 100) if node.total_cpus else 0.0, 1
                        ),
                        host_percent=host_percent,
                        last_seen=node.last_seen,
                    )
                )
            session.flush()
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
            budget=self.settings.total_cpus,
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

    def ensure_node_in_session(self, session: Session) -> Node | None:
        return self.ensure_default_node(session)

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
