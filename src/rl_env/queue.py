"""Lease-based work queue for horizontally scaled episode workers."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import JsonValue


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: str
    payload: dict[str, JsonValue]
    attempt: int
    lease_owner: str


class SQLiteJobQueue:
    """Runnable queue backend with at-least-once delivery.

    SQLite is suitable for one host. The schema and lease protocol map directly
    to PostgreSQL ``FOR UPDATE SKIP LOCKED`` or a managed queue in production.
    """

    def __init__(self, path: str | Path, *, max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.path = str(path)
        self.max_attempts = max_attempts
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_until REAL,
                    result TEXT,
                    last_error TEXT,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS jobs_claimable "
                "ON jobs(status, lease_until, created_at)"
            )

    def enqueue(self, job_id: str, payload: dict[str, JsonValue]) -> bool:
        """Insert once; duplicate IDs are intentionally idempotent."""
        encoded = json.dumps(payload, sort_keys=True)
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO jobs(id, payload, created_at) VALUES (?, ?, ?)",
                (job_id, encoded, time.time()),
            )
            return cursor.rowcount == 1

    def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: float = 300,
        now: float | None = None,
    ) -> ClaimedJob | None:
        if not worker_id or lease_seconds <= 0:
            raise ValueError("worker id and positive lease duration are required")
        timestamp = time.time() if now is None else now
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id, payload, attempts
                FROM jobs
                WHERE attempts < ?
                  AND (
                    status = 'pending'
                    OR (status = 'leased' AND lease_until <= ?)
                  )
                ORDER BY created_at, id
                LIMIT 1
                """,
                (self.max_attempts, timestamp),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            attempt = int(row["attempts"]) + 1
            connection.execute(
                """
                UPDATE jobs
                SET status = 'leased', attempts = ?, lease_owner = ?, lease_until = ?
                WHERE id = ?
                """,
                (attempt, worker_id, timestamp + lease_seconds, row["id"]),
            )
            connection.commit()
            return ClaimedJob(
                id=row["id"],
                payload=json.loads(row["payload"]),
                attempt=attempt,
                lease_owner=worker_id,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def heartbeat(
        self,
        job: ClaimedJob,
        *,
        lease_seconds: float = 300,
        now: float | None = None,
    ) -> bool:
        timestamp = time.time() if now is None else now
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET lease_until = ?
                WHERE id = ? AND status = 'leased' AND lease_owner = ?
                """,
                (timestamp + lease_seconds, job.id, job.lease_owner),
            )
            return cursor.rowcount == 1

    def complete(self, job: ClaimedJob, result: dict[str, Any]) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'completed', result = ?, lease_owner = NULL,
                    lease_until = NULL
                WHERE id = ? AND status = 'leased' AND lease_owner = ?
                """,
                (json.dumps(result, sort_keys=True), job.id, job.lease_owner),
            )
            return cursor.rowcount == 1

    def fail(self, job: ClaimedJob, error: str) -> bool:
        terminal = job.attempt >= self.max_attempts
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = ?, last_error = ?, lease_owner = NULL,
                    lease_until = NULL
                WHERE id = ? AND status = 'leased' AND lease_owner = ?
                """,
                ("failed" if terminal else "pending", error, job.id, job.lease_owner),
            )
            return cursor.rowcount == 1

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return dict(row) if row else None
