"""SQLite persistence layer."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite stores naive datetimes consistently)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        kwargs: dict = {"echo": echo, "future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if ":memory:" in url:
                kwargs["poolclass"] = StaticPool
            # Ensure the parent directory exists for file-backed databases.
            if url.startswith("sqlite:///") and ":memory:" not in url:
                raw_path = url.removeprefix("sqlite:///")
                Path(raw_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(url, **kwargs)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            class_=Session,
        )
        if url.startswith("sqlite"):
            self._configure_sqlite(self.engine)

    @staticmethod
    def _configure_sqlite(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()

    def init(self) -> None:
        # Import models lazily so tables are registered before create_all.
        from app import models  # noqa: F401

        Base.metadata.create_all(self.engine)
        self._migrate_schema()
        logger.info("database ready (%s)", self.engine.url.render_as_string(hide_password=True))

    def _migrate_schema(self) -> None:
        """Apply the small additive migrations needed by this single-node app.

        ``create_all`` creates new tables but never adds columns to an existing
        SQLite table.  This explicit, idempotent migration keeps existing local
        databases usable without introducing a migration service dependency.
        """
        if not self.engine.url.drivername.startswith("sqlite"):
            return
        additions: dict[str, dict[str, str]] = {
            "datasets": {
                "content_sha256": "VARCHAR(64)",
                "quality_status": "VARCHAR(16) NOT NULL DEFAULT 'pending'",
                "quality_score": "FLOAT",
                "quality_report_path": "VARCHAR(512)",
                "quality_issue_path": "VARCHAR(512)",
                "quality_engine_version": "VARCHAR(32)",
                "quality_checked_at": "DATETIME",
                "quality_acknowledged": "BOOLEAN NOT NULL DEFAULT 0",
            },
            "tasks": {
                "compute_threads": "INTEGER",
                "cache_mode": "VARCHAR(8) NOT NULL DEFAULT 'none'",
                "seed": "INTEGER NOT NULL DEFAULT 0",
                "patience": "INTEGER",
                "save_period": "INTEGER NOT NULL DEFAULT 5",
                "dataset_id": "VARCHAR(32)",
                "dataset_sha256": "VARCHAR(64)",
                "config_json": "TEXT",
                "resume_from_task_id": "VARCHAR(24)",
                "resume_mode": "VARCHAR(16)",
                "checkpoint_epoch": "INTEGER",
                "stop_reason": "VARCHAR(40)",
            },
        }
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at DATETIME NOT NULL)"
            )
            applied = {
                int(row[0])
                for row in connection.exec_driver_sql("SELECT version FROM schema_migrations")
            }
            for table, columns in additions.items():
                existing = {
                    str(row[1])
                    for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")
                }
                if not existing:
                    continue
                for name, sql_type in columns.items():
                    if name not in existing:
                        connection.exec_driver_sql(
                            f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"
                        )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_tasks_dataset_id ON tasks (dataset_id)"
            )
            if 1 not in applied:
                connection.exec_driver_sql(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (1, CURRENT_TIMESTAMP)"
                )


    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
