"""CPU-aware task scheduler, watchdog and lifecycle state machine."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from app.config import Settings
from app.database import Database, utcnow
from app.models import (
    AllocationState,
    PipelineStage,
    ResourceAllocation,
    Task,
    TaskCheckpoint,
    TaskMetric,
    TaskStatus,
)
from app.schemas import CancelResponse
from app.services.event_bus import EventBroker
from app.services.executors import ExecutionManager
from app.services.logs import LogStore
from app.services.resources import ResourceService
from app.services.tasks import TaskNotFoundError, TaskService, add_event, runtime_seconds

if TYPE_CHECKING:
    from app.services.pipelines import PipelineService

logger = logging.getLogger(__name__)

PREEMPT_PRIORITY_GAP = 20
MAX_PREEMPTIONS = 2


def _short_reason(reason: str | None, limit: int = 600) -> str:
    text = (reason or "unknown_error").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


class TaskConflictError(RuntimeError):
    pass


class Scheduler:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        log_store: LogStore,
        broker: EventBroker,
        resources: ResourceService,
        execution: ExecutionManager,
        tasks: TaskService,
        pipelines: PipelineService | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.log_store = log_store
        self.broker = broker
        self.resources = resources
        self.execution = execution
        self.tasks = tasks
        self.pipelines = pipelines
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = utcnow()
        self.last_tick_at: datetime | None = None
        self.last_error: str | None = None

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="litetrain-scheduler", daemon=True)
        self._thread.start()
        logger.info("scheduler started (interval %.1fs)", self.settings.scheduler_interval_seconds)

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("scheduler stopped")

    def wake(self) -> None:
        self._wake_event.set()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self.tick()
                self.last_error = None
            except Exception as exc:  # pragma: no cover - defensive loop
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("scheduler tick failed")
            remaining = self.settings.scheduler_interval_seconds - (time.monotonic() - started)
            self._wake_event.wait(max(0.05, remaining))
            self._wake_event.clear()

    # ---------------------------------------------------------------- recovery
    def recover(self) -> None:
        """Reconcile durable state after a process restart.

        Running processes that are still alive are kept and their heartbeat
        window is refreshed. Finished workers are finalized from their durable
        result file. Everything else is failed (and retried if configured).
        """
        with self._state_lock:
            with self.db.session() as session:
                running = session.scalars(
                    select(Task).where(Task.status == TaskStatus.RUNNING.value)
                ).all()
                # Allocations for non-running tasks are leftovers.
                stale_allocations = session.scalars(
                    select(ResourceAllocation).where(
                        ResourceAllocation.state == AllocationState.ACTIVE.value
                    )
                ).all()
                running_ids = {task.id for task in running}
                for allocation in stale_allocations:
                    if allocation.task_id not in running_ids:
                        allocation.state = AllocationState.RELEASED.value
                        allocation.released_at = utcnow()
                session.flush()

            for task in running:
                self._recover_one(task.id)
            self.wake()

    def _recover_one(self, task_id: str) -> None:
        with self.db.session() as session:
            task = session.get(Task, task_id)
            if task is None or task.status != TaskStatus.RUNNING.value:
                return
            result = self.execution.result(task)
            if result is not None:
                exit_code = int(result.get("exit_code", 1))
                if exit_code == 0:
                    self._finish(task, TaskStatus.SUCCEEDED, exit_code, None, session=session)
                else:
                    reason = _short_reason(
                        str(result.get("error") or f"worker_exit_code_{exit_code}")
                    )
                    self._finish(task, TaskStatus.FAILED, exit_code, reason, session=session)
                return

            alive = self.execution.process_alive(task) or self.execution.actor_alive(task)
            if alive:
                task.last_heartbeat_at = utcnow()
                add_event(
                    session,
                    task.id,
                    "recovered",
                    "Platform restarted; reattached to the running worker",
                )
                self.log_store.append(
                    task.id,
                    "[platform] platform restarted — reattached to running worker; "
                    "heartbeat window refreshed",
                )
                self.broker.publish("task.recovered", task.id)
            else:
                reason = "orchestrator_restart_worker_lost"
                self.log_store.append(
                    task.id,
                    "[platform] platform restarted and the worker could not be found; "
                    "marking the attempt failed",
                )
                self._finish(task, TaskStatus.FAILED, None, reason, session=session)

    # ------------------------------------------------------------------- tick
    def tick(self) -> None:
        with self.broker.scheduler_lock(ttl_ms=self.settings.scheduler_lock_ttl_ms) as acquired:
            if not acquired:
                return
            with self._state_lock:
                self._watch_running()
                if self.pipelines is not None:
                    self.pipelines.advance()
                self._schedule_pending()
            self.last_tick_at = utcnow()

    # --------------------------------------------------------------- watchdog
    def _watch_running(self) -> None:
        with self.db.session() as session:
            running = session.scalars(
                select(Task).where(Task.status == TaskStatus.RUNNING.value)
            ).all()
            snapshots = [(task.id, task.status) for task in running]
        for task_id, _ in snapshots:
            self._inspect_running(task_id)

    def _inspect_running(self, task_id: str) -> None:
        now = utcnow()
        with self.db.session() as session:
            task = session.get(Task, task_id)
            if task is None or task.status != TaskStatus.RUNNING.value:
                return
            result = self.execution.result(task)
            if result is not None:
                exit_code = result.get("exit_code")
                try:
                    code = int(exit_code) if exit_code is not None else 1
                except (TypeError, ValueError):
                    code = 1
                if code == 0:
                    self._finish(task, TaskStatus.SUCCEEDED, code, None, session=session)
                else:
                    reason = _short_reason(
                        str(result.get("error") or f"worker_exit_code_{code}")
                    )
                    self._finish(task, TaskStatus.FAILED, code, reason, session=session)
                return

            if task.started_at:
                running_for = (now - task.started_at).total_seconds()
                runtime_limit = min(
                    task.max_runtime_seconds,
                    self.settings.default_max_runtime_seconds,
                )
                if running_for > runtime_limit:
                    self._terminate(
                        task,
                        TaskStatus.FAILED,
                        "runtime_timeout",
                        f"Task exceeded runtime limit ({runtime_limit}s; "
                        f"task={task.max_runtime_seconds}s, admin={self.settings.default_max_runtime_seconds}s)",
                        session=session,
                    )
                    return

            heartbeat_reference = task.last_heartbeat_at or task.started_at
            if heartbeat_reference:
                stale_for = (now - heartbeat_reference).total_seconds()
                if stale_for > self.settings.heartbeat_timeout_seconds:
                    self._terminate(
                        task,
                        TaskStatus.FAILED,
                        "heartbeat_timeout",
                        f"No worker heartbeat for {int(stale_for)}s "
                        f"(limit {self.settings.heartbeat_timeout_seconds}s)",
                        session=session,
                    )
                    return

            # Fast path: a launched process that disappeared without writing a
            # result is a failure rather than something to wait for forever.
            if task.pid and task.started_at:
                started_for = (now - task.started_at).total_seconds()
                if started_for > 2 and not self.execution.process_alive(task):
                    self._finish(
                        task,
                        TaskStatus.FAILED,
                        None,
                        "worker_exited_without_result",
                        session=session,
                    )
                    return

            # A worker that never produced a heartbeat is handled by the
            # heartbeat watchdog above. Do not fail a task merely because the
            # launch-result PID was lost in a control-plane crash window: the
            # detached actor may still be starting the worker.

    # --------------------------------------------------------------- dispatch
    def _checkpoint_epoch(self, task_id: str) -> int | None:
        with self.db.session() as session:
            row = session.scalar(
                select(TaskCheckpoint)
                .where(TaskCheckpoint.task_id == task_id)
                .order_by(TaskCheckpoint.epoch.desc())
            )
        if row is None:
            return None
        path = Path(row.path)
        if not path.is_absolute():
            path = self.settings.data_dir / path
        return row.epoch if path.is_file() and path.stat().st_size > 0 else None

    def _preempt_for(self, high: Task) -> bool:
        """Preempt a lower-priority run that has a recoverable checkpoint.

        We deliberately do not introduce a sixth task status.  The original
        attempt is finalised as ``cancelled/preempted`` and a new pending
        resume task is created from its latest checkpoint.  This keeps the
        original log immutable, preserves lineage, and reuses the tested
        resume path.
        """
        if high.priority < PREEMPT_PRIORITY_GAP:
            return False
        with self.db.session() as session:
            live_high = session.get(Task, high.id)
            if live_high is None or live_high.status != TaskStatus.PENDING.value:
                return False
            allocations = session.scalars(
                select(ResourceAllocation).where(
                    ResourceAllocation.state == AllocationState.ACTIVE.value
                )
            ).all()
            pipeline_task_ids = set(
                session.scalars(
                    select(PipelineStage.task_id).where(PipelineStage.task_id.is_not(None))
                ).all()
            )
            candidates_by_node: dict[str, list[Task]] = {}
            for allocation in allocations:
                low = session.get(Task, allocation.task_id)
                if low is None or low.status != TaskStatus.RUNNING.value:
                    continue
                if low.id in pipeline_task_ids:
                    continue
                if low.priority > high.priority - PREEMPT_PRIORITY_GAP:
                    continue
                if low.preempt_count >= MAX_PREEMPTIONS:
                    continue
                if self._checkpoint_epoch(low.id) is None:
                    continue
                candidates_by_node.setdefault(allocation.node_id, []).append(low)

            selected: list[Task] = []
            for tasks in candidates_by_node.values():
                tasks.sort(key=lambda item: (item.priority, item.started_at or item.queued_at))
                freed = 0
                for low in tasks:
                    selected.append(low)
                    freed += low.cpu_cores
                    if freed >= high.cpu_cores:
                        break
                if freed >= high.cpu_cores:
                    break
                selected.clear()

        if not selected:
            return False
        preempted = False
        for low in selected:
            preempted = self._preempt_task(low, high) or preempted
        return preempted

    def _preempt_task(self, low: Task, high: Task) -> bool:
        with self.db.session() as session:
            live_low = session.get(Task, low.id)
            live_high = session.get(Task, high.id)
            if (
                live_low is None
                or live_high is None
                or live_low.status != TaskStatus.RUNNING.value
                or live_high.status != TaskStatus.PENDING.value
            ):
                return False
            checkpoint_epoch = self._checkpoint_epoch(live_low.id)
            if checkpoint_epoch is None:
                return False
            try:
                self.execution.stop(live_low, reason="preempted")
            except Exception:
                logger.exception("failed to stop task %s for preemption", live_low.id)
            now = utcnow()
            self.resources.release_allocation(session, live_low.id)
            live_low.status = TaskStatus.CANCELLED.value
            live_low.failure_reason = "preempted"
            live_low.stop_reason = "preempted"
            live_low.preempted_by_task_id = live_high.id
            live_low.preempted_at = now
            live_low.cancel_requested_at = now
            live_low.finished_at = now
            live_low.exit_code = None
            add_event(
                session,
                live_low.id,
                "preempted",
                f"Preempted by higher-priority task {live_high.id}; "
                f"resuming from checkpoint epoch {checkpoint_epoch}",
                level="warning",
            )
            self.log_store.append(
                live_low.id,
                f"[platform] PREEMPTED by task {live_high.id} "
                f"(priority {live_high.priority}); latest checkpoint epoch {checkpoint_epoch}; "
                "creating a resume task",
            )
            session.flush()
            low_id = live_low.id
            high_id = live_high.id
            low_priority = live_low.priority
            low_preempt_count = live_low.preempt_count
        try:
            self.execution.cleanup(live_low)
        except Exception:
            logger.exception("cleanup after preemption failed for %s", low_id)

        try:
            child = self.tasks.resume(low_id, mode="full")
            with self.db.session() as session:
                child_model = session.get(Task, child.id)
                if child_model is not None:
                    child_model.priority = low_priority
                    child_model.preempt_count = low_preempt_count + 1
                    child_model.notes = f"auto-resume after preemption by {high_id}"
                    add_event(
                        session,
                        child.id,
                        "preempted_resume",
                        f"Created after preempting {low_id} for task {high_id}",
                        level="info",
                    )
                    session.flush()
            self.log_store.append(
                child.id,
                f"[platform] preempted resume of {low_id} (checkpoint epoch {checkpoint_epoch})",
            )
            self.broker.publish("task.preempted", low_id, {"by": high_id, "resume": child.id})
        except Exception:
            logger.exception("failed to create resume task after preempting %s", low_id)
            with self.db.session() as session:
                failed = session.get(Task, low_id)
                if failed is not None:
                    add_event(
                        session,
                        low_id,
                        "preempt_resume_failed",
                        "Preempted, but the resume task could not be created; see server logs",
                        level="error",
                    )
                    session.flush()
            return False
        return True

    def _schedule_pending(self) -> None:
        with self.db.session() as session:
            pending = session.scalars(
                select(Task)
                .where(Task.status == TaskStatus.PENDING.value)
                .order_by(Task.priority.desc(), Task.submitted_at.asc())
            ).all()
            candidate_ids = [task.id for task in pending]

        blocked_high_id: str | None = None
        for task_id in candidate_ids:
            if self._stop_event.is_set():
                return
            with self.db.session() as session:
                task = session.get(Task, task_id)
                if task is None or task.status != TaskStatus.PENDING.value:
                    continue
                node = self.resources.choose_node(session, task.owner, task.cpu_cores)
                if node is None:
                    blocked_high_id = task_id
                    break
                node_id = node.id
                node_name = node.name
                # Atomic claim inside this SQLite write transaction.
                task.status = TaskStatus.RUNNING.value
                task.started_at = utcnow()
                task.last_heartbeat_at = task.started_at
                task.finished_at = None
                task.exit_code = None
                task.progress = 0.0
                task.cancel_requested_at = None
                task.latest_cpu_percent = 0.0
                task.latest_memory_mb = 0.0
                # Persist the planned execution identity before launching, so a
                # control-plane crash in the launch window can still find (or
                # safely fail) a detached Ray actor.
                task.executor = self.execution.backend
                task.ray_actor_name = (
                    self.execution.actor_name(task) if self.execution.backend == "ray" else None
                )
                self.resources.create_allocation(session, task, node)
                add_event(
                    session,
                    task.id,
                    "scheduled",
                    f"Allocated {task.cpu_cores} CPU core(s) on {node_name} ({node_id})",
                )
                session.flush()
                claimed_task = task

            self.log_store.append(
                claimed_task.id,
                f"[platform] starting · {claimed_task.cpu_cores} CPU core(s) on {node_name} · "
                f"priority {claimed_task.priority} · attempt {claimed_task.attempt} "
                f"· executor {self.execution.backend}",
            )
            self.broker.publish("task.started", claimed_task.id, {"status": TaskStatus.RUNNING.value})
            try:
                launch = self.execution.launch(claimed_task)
            except Exception as exc:
                logger.exception("failed to launch task %s", claimed_task.id)
                self._fail_launch(claimed_task.id, exc)
                continue
            with self.db.session() as session:
                live = session.get(Task, claimed_task.id)
                if live is None:
                    continue
                live.executor = launch.executor
                live.ray_actor_name = launch.actor_name
                live.pid = launch.pid
                live.pid_create_time = launch.pid_create_time
                live.last_heartbeat_at = utcnow()
                session.flush()
            self.log_store.append(
                claimed_task.id,
                f"[platform] worker launched via {launch.executor} (pid {launch.pid})",
            )
            self.broker.publish(
                "task.worker_launched",
                claimed_task.id,
                {"executor": launch.executor, "pid": launch.pid},
            )

        if blocked_high_id is not None:
            high = self.tasks.get_model(blocked_high_id)
            self._preempt_for(high)

    def _fail_launch(self, task_id: str, exc: Exception) -> None:
        with self.db.session() as session:
            task = session.get(Task, task_id)
            if task is None:
                return
            task.exit_code = None
            self._finish(
                task,
                TaskStatus.FAILED,
                None,
                _short_reason(f"launch_error: {type(exc).__name__}: {exc}"),
                session=session,
            )

    # ----------------------------------------------------------- transitions
    def _finish(
        self,
        task: Task,
        status: TaskStatus,
        exit_code: int | None,
        failure_reason: str | None,
        *,
        session,
    ) -> None:
        """Finish one attempt, optionally requeuing it for a retry."""
        # Kill/release the executor while the original executor metadata is
        # still on the task. This is especially important before a retry resets
        # the actor name/pid fields.
        try:
            self.execution.cleanup(task)
        except Exception:
            logger.exception("executor cleanup failed for %s", task.id)
        now = utcnow()
        released = self.resources.release_allocation(session, task.id)
        retry = (
            status is TaskStatus.FAILED
            and task.retry_count < task.max_retries
            and self._retry_eligible(failure_reason)
        )
        duration = runtime_seconds(task, now)
        if status is TaskStatus.SUCCEEDED:
            task.status = TaskStatus.SUCCEEDED.value
            task.exit_code = exit_code if exit_code is not None else 0
            task.failure_reason = None
            task.progress = 100.0
            task.finished_at = now
            event_type = "succeeded"
            message = f"Training succeeded in {duration}s"
            level = "success"
        elif retry:
            # A new attempt must not inherit the previous attempt's result
            # marker or epoch metric points (unique by task/name/epoch).
            try:
                Path(task.result_path).unlink(missing_ok=True)
            except OSError:
                logger.warning("could not reset result marker for %s", task.id)
            session.execute(delete(TaskMetric).where(TaskMetric.task_id == task.id))
            task.status = TaskStatus.PENDING.value
            task.queued_at = now
            task.retry_count += 1
            task.executor = None
            task.ray_actor_name = None
            task.pid = None
            task.pid_create_time = None
            task.started_at = None
            task.finished_at = None
            task.last_heartbeat_at = None
            task.exit_code = None
            task.progress = 0.0
            task.latest_cpu_percent = None
            task.latest_memory_mb = None
            task.failure_reason = failure_reason
            event_type = "retry_scheduled"
            message = (
                f"Attempt failed ({failure_reason}); requeued as attempt "
                f"{task.attempt} of {task.max_retries + 1}"
            )
            level = "warning"
        else:
            task.status = TaskStatus.FAILED.value
            task.exit_code = exit_code
            task.failure_reason = failure_reason or "unknown_error"
            task.finished_at = now
            event_type = "failed"
            message = f"Training failed after {duration}s: {task.failure_reason}"
            level = "error"

        add_event(session, task.id, event_type, message, level=level)
        session.flush()
        if status is TaskStatus.SUCCEEDED:
            self.log_store.append(
                task.id,
                f"[platform] SUCCEEDED · exit_code=0 · duration={duration}s · "
                f"released {released} CPU core(s)",
            )
        elif retry:
            self.log_store.append(
                task.id,
                f"[platform] attempt failed: {failure_reason} · requeuing "
                f"(retry {task.retry_count}/{task.max_retries})",
            )
            self.log_store.append(task.id, "=" * 78)
        else:
            self.log_store.append(
                task.id,
                f"[platform] FAILED · exit_code={exit_code} · reason={task.failure_reason} "
                f"· duration={duration}s · released {released} CPU core(s)",
            )
        self.broker.publish(
            f"task.{event_type}",
            task.id,
            {
                "status": task.status,
                "exit_code": task.exit_code,
                "failure_reason": task.failure_reason,
                "retry": retry,
            },
        )
        logger.info(
            "task %s %s exit=%s reason=%s retry=%s",
            task.id,
            task.status,
            task.exit_code,
            task.failure_reason,
            retry,
        )
        self.wake()

    @staticmethod
    def _retry_eligible(reason: str | None) -> bool:
        if not reason:
            return True
        # Timeouts and explicit cancellation should not silently retry. Every
        # other failure class (worker exception/non-zero exit, lost worker,
        # launch error) is retried up to the task's configured attempt budget.
        return reason not in {
            "runtime_timeout",
            "heartbeat_timeout",
            "cancelled_by_user",
        }

    def _terminate(
        self,
        task: Task,
        final_status: TaskStatus,
        reason_code: str,
        message: str,
        *,
        session,
    ) -> None:
        task_id = task.id
        logger.warning("terminating task %s: %s", task_id, reason_code)
        self.log_store.append(task_id, f"[platform] stopping task: {message}")
        try:
            self.execution.stop(task, reason=reason_code)
        except Exception:
            logger.exception("failed to stop executor for task %s", task_id)
        self._finish(task, final_status, None, reason_code, session=session)

    # --------------------------------------------------------------- operator
    def cancel(self, task_id: str) -> CancelResponse:
        with self._state_lock:
            with self.db.session() as session:
                task = session.get(Task, task_id)
                if task is None:
                    raise TaskNotFoundError(task_id)
                if task.status in {item.value for item in TaskStatus if item.terminal}:
                    raise TaskConflictError(f"Task is already {task.status}")
                was_running = task.status == TaskStatus.RUNNING.value
                if was_running:
                    try:
                        self.execution.stop(task, reason="user_cancelled")
                    except Exception:
                        logger.exception("failed to stop task %s during cancel", task_id)
                task.cancel_requested_at = utcnow()
                task.finished_at = task.finished_at or utcnow()
                task.status = TaskStatus.CANCELLED.value
                task.failure_reason = "cancelled_by_user"
                task.exit_code = None
                self.resources.release_allocation(session, task.id)
                add_event(
                    session,
                    task.id,
                    "cancelled",
                    "Task cancelled by user"
                    + ("; running worker was terminated" if was_running else "; task left the queue"),
                    level="warning",
                )
                session.flush()
                self.log_store.append(
                    task.id,
                    "[platform] CANCELLED by user"
                    + (" — worker processes were terminated" if was_running else ""),
                )
                self.broker.publish(
                    "task.cancelled",
                    task.id,
                    {"status": task.status, "was_running": was_running},
                )
                try:
                    self.execution.cleanup(task)
                except Exception:
                    logger.exception("cleanup after cancel failed for %s", task.id)
                output = self.tasks._to_schema(task)
        self.wake()
        return CancelResponse(task=output, message="Task cancelled")

    def shutdown(self) -> None:
        self.stop()

    # ------------------------------------------------------------- P2 controls
    def apply_node_topology(self, topology: list[tuple[str, int]]):
        with self._state_lock:
            nodes = self.resources.apply_topology(topology)
            self.wake()
            return nodes

    def set_node_status(self, node_id: str, status: str):
        with self._state_lock:
            node = self.resources.set_node_status(node_id, status)
            self.wake()
            return node

    def cancel_pipeline(self, pipeline_id: str):
        if self.pipelines is None:
            raise RuntimeError("pipeline service is not configured")
        with self._state_lock:
            active_task_id = self.pipelines.active_task_id(pipeline_id)
            if active_task_id:
                try:
                    self.cancel(active_task_id)
                except TaskConflictError:
                    pass
            return self.pipelines.mark_cancelled(pipeline_id)

    def health(self) -> dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "last_error": self.last_error,
            "started_at": self._started_at.isoformat(),
        }
