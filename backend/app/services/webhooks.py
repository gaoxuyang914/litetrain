"""Webhook subscriptions, transactional enqueueing and signed delivery."""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Database, utcnow
from app.models import (
    Pipeline,
    PipelineStage,
    Task,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.schemas import (
    WebhookCreate,
    WebhookDeliveryOut,
    WebhookEndpointOut,
    WebhookListResponse,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 4


def configure_max_attempts(value: int) -> None:
    global DEFAULT_MAX_ATTEMPTS
    DEFAULT_MAX_ATTEMPTS = max(1, int(value))


def _json_events(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
        return [str(item) for item in value] if isinstance(value, list) else ["*"]
    except json.JSONDecodeError:
        return ["*"]


def _matches(events: list[str], event_type: str) -> bool:
    return any(item == "*" or item == event_type or fnmatch.fnmatch(event_type, item) for item in events)


def _task_payload(session: Session, task: Task) -> dict[str, Any]:
    stage = session.scalar(select(PipelineStage).where(PipelineStage.task_id == task.id))
    return {
        "id": task.id,
        "name": task.name,
        "status": task.status,
        "priority": task.priority,
        "cpu_cores": task.cpu_cores,
        "progress": round(float(task.progress or 0.0), 2),
        "exit_code": task.exit_code,
        "failure_reason": task.failure_reason,
        "submitted_at": task.submitted_at.isoformat() if task.submitted_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "pipeline_id": stage.pipeline_id if stage else None,
        "stage_key": stage.stage_key if stage else None,
    }


def enqueue_task_event(
    session: Session,
    task: Task,
    event_type: str,
    message: str,
    level: str = "info",
) -> int:
    """Insert one outbox delivery per matching endpoint in the caller's transaction."""
    endpoints = session.scalars(select(WebhookEndpoint).where(WebhookEndpoint.active.is_(True))).all()
    if not endpoints:
        return 0
    payload = {
        "event": event_type,
        "occurred_at": utcnow().isoformat() + "Z",
        "message": message,
        "level": level,
        "task": _task_payload(session, task),
    }
    now = utcnow()
    count = 0
    for endpoint in endpoints:
        if not _matches(_json_events(endpoint.events_json), event_type):
            continue
        session.add(
            WebhookDelivery(
                id="wh-" + uuid.uuid4().hex[:12],
                endpoint_id=endpoint.id,
                event_type=event_type,
                task_id=task.id,
                pipeline_id=payload["task"].get("pipeline_id"),
                payload_json=json.dumps(payload, ensure_ascii=False),
                status="pending",
                attempts=0,
                max_attempts=DEFAULT_MAX_ATTEMPTS,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        count += 1
    return count


def enqueue_pipeline_event(
    session: Session,
    pipeline: Pipeline,
    event_type: str,
    message: str,
    *,
    level: str = "info",
) -> int:
    endpoints = session.scalars(select(WebhookEndpoint).where(WebhookEndpoint.active.is_(True))).all()
    if not endpoints:
        return 0
    payload = {
        "event": event_type,
        "occurred_at": utcnow().isoformat() + "Z",
        "message": message,
        "level": level,
        "pipeline": {
            "id": pipeline.id,
            "name": pipeline.name,
            "status": pipeline.status,
            "failure_reason": pipeline.failure_reason,
            "submitted_at": pipeline.submitted_at.isoformat() if pipeline.submitted_at else None,
            "finished_at": pipeline.finished_at.isoformat() if pipeline.finished_at else None,
        },
    }
    now = utcnow()
    count = 0
    for endpoint in endpoints:
        if not _matches(_json_events(endpoint.events_json), event_type):
            continue
        session.add(
            WebhookDelivery(
                id="wh-" + uuid.uuid4().hex[:12],
                endpoint_id=endpoint.id,
                event_type=event_type,
                task_id=None,
                pipeline_id=pipeline.id,
                payload_json=json.dumps(payload, ensure_ascii=False),
                status="pending",
                attempts=0,
                max_attempts=DEFAULT_MAX_ATTEMPTS,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        count += 1
    return count


class WebhookService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db

    @staticmethod
    def _endpoint_out(session: Session, endpoint: WebhookEndpoint) -> WebhookEndpointOut:
        counts = dict(
            session.execute(
                select(WebhookDelivery.status, func.count(WebhookDelivery.id))
                .where(WebhookDelivery.endpoint_id == endpoint.id)
                .group_by(WebhookDelivery.status)
            ).all()
        )
        total = int(sum(counts.values()))
        return WebhookEndpointOut(
            id=endpoint.id,
            name=endpoint.name,
            url=endpoint.url,
            secret=endpoint.secret,
            events=_json_events(endpoint.events_json),
            active=endpoint.active,
            created_at=endpoint.created_at,
            delivery_count=total,
            pending_count=int(counts.get("pending", 0)),
            success_count=int(counts.get("success", 0)),
            failed_count=int(counts.get("failed", 0)),
        )

    def list(self) -> WebhookListResponse:
        with self.db.session() as session:
            endpoints = session.scalars(
                select(WebhookEndpoint).order_by(WebhookEndpoint.created_at.desc())
            ).all()
            items = [self._endpoint_out(session, endpoint) for endpoint in endpoints]
        return WebhookListResponse(items=items, total=len(items))

    def create(self, payload: WebhookCreate) -> WebhookEndpointOut:
        endpoint = WebhookEndpoint(
            id="wh-" + uuid.uuid4().hex[:10],
            name=payload.name.strip(),
            url=payload.url,
            secret=payload.secret or secrets.token_urlsafe(24),
            events_json=json.dumps(payload.events or ["*"]),
            active=payload.active,
            created_at=utcnow(),
        )
        with self.db.session() as session:
            session.add(endpoint)
            session.flush()
            return self._endpoint_out(session, endpoint)

    def update_active(self, endpoint_id: str, active: bool) -> WebhookEndpointOut:
        with self.db.session() as session:
            endpoint = session.get(WebhookEndpoint, endpoint_id)
            if endpoint is None:
                raise KeyError(endpoint_id)
            endpoint.active = active
            session.flush()
            return self._endpoint_out(session, endpoint)

    def delete(self, endpoint_id: str) -> None:
        with self.db.session() as session:
            endpoint = session.get(WebhookEndpoint, endpoint_id)
            if endpoint is None:
                raise KeyError(endpoint_id)
            session.delete(endpoint)

    def deliveries(
        self, endpoint_id: str | None = None, limit: int = 100
    ) -> list[WebhookDeliveryOut]:
        with self.db.session() as session:
            stmt = select(WebhookDelivery).order_by(WebhookDelivery.created_at.desc()).limit(limit)
            if endpoint_id:
                stmt = stmt.where(WebhookDelivery.endpoint_id == endpoint_id)
            rows = session.scalars(stmt).all()
            return [
                WebhookDeliveryOut(
                    id=row.id,
                    endpoint_id=row.endpoint_id,
                    event_type=row.event_type,
                    task_id=row.task_id,
                    pipeline_id=row.pipeline_id,
                    status=row.status,
                    attempts=row.attempts,
                    max_attempts=row.max_attempts,
                    next_attempt_at=row.next_attempt_at,
                    response_code=row.response_code,
                    response_excerpt=row.response_excerpt,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in rows
            ]

    def enqueue_test(self, endpoint_id: str) -> WebhookDeliveryOut:
        with self.db.session() as session:
            endpoint = session.get(WebhookEndpoint, endpoint_id)
            if endpoint is None:
                raise KeyError(endpoint_id)
            now = utcnow()
            delivery = WebhookDelivery(
                id="wh-" + uuid.uuid4().hex[:12],
                endpoint_id=endpoint_id,
                event_type="webhook.test",
                payload_json=json.dumps(
                    {
                        "event": "webhook.test",
                        "occurred_at": now.isoformat() + "Z",
                        "message": "LiteTrain webhook connectivity test",
                    },
                    ensure_ascii=False,
                ),
                status="pending",
                attempts=0,
                max_attempts=self.settings.webhook_max_attempts,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(delivery)
            session.flush()
            return WebhookDeliveryOut(
                id=delivery.id,
                endpoint_id=delivery.endpoint_id,
                event_type=delivery.event_type,
                task_id=None,
                pipeline_id=None,
                status=delivery.status,
                attempts=0,
                max_attempts=delivery.max_attempts,
                next_attempt_at=delivery.next_attempt_at,
                response_code=None,
                response_excerpt=None,
                created_at=delivery.created_at,
                updated_at=delivery.updated_at,
            )

    def pending_count(self) -> int:
        with self.db.session() as session:
            return int(
                session.scalar(
                    select(func.count(WebhookDelivery.id)).where(WebhookDelivery.status == "pending")
                )
                or 0
            )


class WebhookDispatcher:
    """Small durable outbox worker with HMAC signatures and exponential retries."""

    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.last_error: str | None = None
        self.last_delivery_at = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="litetrain-webhooks", daemon=True)
        self._thread.start()
        logger.info("webhook dispatcher started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("webhook dispatcher stopped")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.process_once()
                self.last_error = None
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("webhook dispatcher tick failed")
            self._stop.wait(self.settings.webhook_dispatcher_interval_seconds)

    def _next_delivery(self) -> tuple[WebhookDelivery, WebhookEndpoint] | None:
        now = utcnow()
        with self.db.session() as session:
            delivery = session.scalar(
                select(WebhookDelivery)
                .where(
                    WebhookDelivery.status == "pending",
                    WebhookDelivery.next_attempt_at <= now,
                )
                .order_by(WebhookDelivery.created_at.asc())
                .limit(1)
            )
            if delivery is None:
                return None
            endpoint = session.get(WebhookEndpoint, delivery.endpoint_id)
            if endpoint is None or not endpoint.active:
                delivery.status = "failed"
                delivery.updated_at = now
                delivery.response_excerpt = "endpoint deleted or inactive"
                return None
            session.expunge(delivery)
            session.expunge(endpoint)
            return delivery, endpoint

    @staticmethod
    def _sign(secret: str, timestamp: str, body: bytes) -> str:
        message = timestamp.encode("utf-8") + b"." + body
        return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

    def process_once(self) -> bool:
        """Process at most one delivery. Returns True if a delivery was handled."""
        if not self._lock.acquire(blocking=False):
            return False
        try:
            picked = self._next_delivery()
            if picked is None:
                return False
            delivery, endpoint = picked
            body = delivery.payload_json.encode("utf-8")
            timestamp = str(int(time.time()))
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "LiteTrain-Webhook/1.0",
                "X-LiteTrain-Event": delivery.event_type,
                "X-LiteTrain-Delivery": delivery.id,
                "X-LiteTrain-Timestamp": timestamp,
                "X-LiteTrain-Signature": self._sign(endpoint.secret, timestamp, body),
            }
            response_code: int | None = None
            excerpt: str | None = None
            ok = False
            try:
                response = requests.post(
                    endpoint.url,
                    data=body,
                    headers=headers,
                    timeout=self.settings.webhook_timeout_seconds,
                )
                response_code = response.status_code
                excerpt = (response.text or "")[:400]
                ok = 200 <= response.status_code < 300
            except requests.RequestException as exc:
                excerpt = f"{type(exc).__name__}: {exc}"[:400]
            with self.db.session() as session:
                live = session.get(WebhookDelivery, delivery.id)
                if live is None:
                    return True
                live.attempts += 1
                live.last_attempt_at = utcnow()
                live.response_code = response_code
                live.response_excerpt = excerpt
                live.updated_at = utcnow()
                if ok:
                    live.status = "success"
                elif live.attempts >= live.max_attempts:
                    live.status = "failed"
                else:
                    delay = self.settings.webhook_retry_base_seconds * (2 ** (live.attempts - 1))
                    live.next_attempt_at = utcnow() + timedelta(seconds=min(delay, 300))
                session.flush()
            self.last_delivery_at = utcnow()
            logger.info(
                "webhook delivery %s %s event=%s attempts=%s",
                delivery.id,
                "ok" if ok else "pending/failed",
                delivery.event_type,
                delivery.attempts + 1,
            )
            return True
        finally:
            self._lock.release()
