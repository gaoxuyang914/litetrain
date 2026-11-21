"""Redis-backed coordination bus.

Redis has two jobs in LiteTrain:
* a short-lived distributed lock so only one API replica schedules per tick;
* a lightweight pub/sub channel for task lifecycle events (useful for SSE and
  for scaling the UI/API horizontally).

The platform remains usable without Redis by falling back to an in-process
lock, but the cluster panel makes the degraded mode explicit.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

EVENT_CHANNEL = "litetrain:events"
SCHEDULER_LOCK_KEY = "litetrain:lock:scheduler"
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class EventBroker:
    def __init__(
        self,
        url: str,
        *,
        protocol: int = 2,
        socket_timeout: float = 1.5,
        enabled: bool = True,
    ) -> None:
        self.url = url
        self.protocol = protocol
        self.socket_timeout = socket_timeout
        self.enabled = enabled
        self.client: Redis | None = None
        self.last_error: str | None = None
        self._local_lock = threading.Lock()

    def connect(self) -> bool:
        if not self.enabled:
            self.last_error = "Redis disabled by configuration"
            return False
        try:
            client = Redis.from_url(
                self.url,
                protocol=self.protocol,
                decode_responses=True,
                socket_connect_timeout=self.socket_timeout,
                socket_timeout=self.socket_timeout,
                health_check_interval=30,
            )
            client.ping()
            self.client = client
            self.last_error = None
            logger.info("Redis connected at %s", self.url)
            return True
        except Exception as exc:  # pragma: no cover - depends on local services
            self.client = None
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Redis unavailable, using local-only coordination: %s", self.last_error)
            return False

    @property
    def connected(self) -> bool:
        return self.client is not None

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

    def ping(self) -> bool:
        if self.client is None:
            return False
        try:
            return bool(self.client.ping())
        except RedisError:
            return False

    def redis_version(self) -> str | None:
        if self.client is None:
            return None
        try:
            info = self.client.info("server")
            return str(info.get("redis_version")) if info else None
        except RedisError:
            return None

    def publish(self, event: str, task_id: str, payload: dict[str, Any] | None = None) -> None:
        if self.client is None:
            return
        message = json.dumps(
            {
                "event": event,
                "task_id": task_id,
                "at": datetime.now(timezone.utc).isoformat(),
                "data": payload or {},
            },
            ensure_ascii=False,
        )
        try:
            self.client.publish(EVENT_CHANNEL, message)
        except RedisError as exc:
            logger.debug("Redis publish failed: %s", exc)

    @contextmanager
    def scheduler_lock(self, ttl_ms: int = 8_000) -> Iterator[bool]:
        """Best-effort mutual exclusion for one scheduling tick."""
        if self.client is None:
            acquired = self._local_lock.acquire(blocking=False)
            try:
                yield acquired
            finally:
                if acquired:
                    self._local_lock.release()
            return

        token = secrets.token_urlsafe(24)
        acquired = False
        try:
            acquired = bool(
                self.client.set(SCHEDULER_LOCK_KEY, token, nx=True, px=ttl_ms)
            )
        except RedisError as exc:
            logger.warning("Redis lock failed, falling back to local lock: %s", exc)
            acquired = self._local_lock.acquire(blocking=False)
            try:
                yield acquired
            finally:
                if acquired:
                    self._local_lock.release()
            return

        try:
            yield acquired
        finally:
            if acquired:
                try:
                    self.client.eval(_RELEASE_SCRIPT, 1, SCHEDULER_LOCK_KEY, token)
                except RedisError:
                    # TTL will clean it up.
                    pass

    def wait_for_lock_owner(self) -> str | None:
        if self.client is None:
            return None
        try:
            value = self.client.get(SCHEDULER_LOCK_KEY)
            return "lock-held" if value else None
        except RedisError:
            return None

    def publish_task_event(self, task_id: str, event: str, **data: Any) -> None:
        self.publish(event, task_id, data)


def sleep_until_next(interval: float, started: float) -> None:
    remaining = interval - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)
