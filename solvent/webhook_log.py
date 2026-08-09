"""
webhook_log.py — durable log of received Stripe webhook events.

Stores every event received with: event_id, type, received_at, status
(processed/error/skipped), error_message.  Allows replaying failed events.

Schema:
    webhook_events(
        event_id   TEXT PRIMARY KEY,
        event_type TEXT,
        payload    BLOB,
        received_at REAL,
        status     TEXT,
        error      TEXT
    )
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any


class WebhookLog:
    """SQLite-backed durable log of Stripe webhook events."""

    _DDL = [
        """
        CREATE TABLE IF NOT EXISTS webhook_events (
            event_id    TEXT PRIMARY KEY,
            event_type  TEXT NOT NULL DEFAULT '',
            payload     BLOB NOT NULL DEFAULT (x''),
            received_at REAL NOT NULL DEFAULT 0.0,
            status      TEXT NOT NULL DEFAULT 'received',
            error       TEXT NOT NULL DEFAULT ''
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_wh_status      ON webhook_events (status)",
        "CREATE INDEX IF NOT EXISTS idx_wh_received_at ON webhook_events (received_at)",
    ]

    def __init__(self, db_path: str = ".solvent/webhooks.db") -> None:
        if db_path == ":memory:":
            self._db_path = ":memory:"
        else:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._db_path = db_path
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        for stmt in self._DDL:
            self._conn.execute(stmt)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def record(
        self,
        event_id: str,
        event_type: str,
        payload: bytes,
        status: str,
        error: str = "",
    ) -> None:
        """Insert or replace an event record."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO webhook_events
                (event_id, event_type, payload, received_at, status, error)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, event_type, payload, time.time(), status, error),
        )
        self._conn.commit()

    def mark_processed(self, event_id: str) -> None:
        """Update status → 'processed'."""
        self._conn.execute(
            "UPDATE webhook_events SET status = 'processed', error = '' WHERE event_id = ?",
            (event_id,),
        )
        self._conn.commit()

    def mark_error(self, event_id: str, error: str) -> None:
        """Update status → 'error' with an error message."""
        self._conn.execute(
            "UPDATE webhook_events SET status = 'error', error = ? WHERE event_id = ?",
            (error, event_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        # Add a human-readable timestamp field
        import datetime

        try:
            d["received_at_fmt"] = datetime.datetime.fromtimestamp(d["received_at"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except Exception:
            d["received_at_fmt"] = ""
        return d

    def list_recent(self, limit: int = 50) -> list[dict]:
        """Return up to *limit* events sorted by received_at DESC."""
        cur = self._conn.execute(
            "SELECT * FROM webhook_events ORDER BY received_at DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_dict(r) for r in cur.fetchall()]

    def list_failed(self) -> list[dict]:
        """Return all events where status = 'error'."""
        cur = self._conn.execute(
            "SELECT * FROM webhook_events WHERE status = 'error' ORDER BY received_at DESC"
        )
        return [self._row_to_dict(r) for r in cur.fetchall()]

    def get_payload(self, event_id: str) -> bytes | None:
        """Retrieve the raw stored payload for replay, or None if not found."""
        cur = self._conn.execute(
            "SELECT payload FROM webhook_events WHERE event_id = ?",
            (event_id,),
        )
        row = cur.fetchone()
        return bytes(row["payload"]) if row else None

    def stats(self) -> dict[str, Any]:
        """Return aggregate counts: total, processed, error, skipped, last_24h."""
        cur = self._conn.execute(
            """
            SELECT
                COUNT(*)                                          AS total,
                SUM(CASE WHEN status = 'processed' THEN 1 END)   AS processed,
                SUM(CASE WHEN status = 'error'     THEN 1 END)   AS error,
                SUM(CASE WHEN status = 'skipped'   THEN 1 END)   AS skipped,
                SUM(CASE WHEN received_at >= ?     THEN 1 END)   AS last_24h
            FROM webhook_events
            """,
            (time.time() - 86400,),
        )
        row = cur.fetchone()
        return {
            "total": row["total"] or 0,
            "processed": row["processed"] or 0,
            "error": row["error"] or 0,
            "skipped": row["skipped"] or 0,
            "last_24h": row["last_24h"] or 0,
        }
