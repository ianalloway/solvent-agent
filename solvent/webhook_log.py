"""Durable SQLite log and CLI for received Stripe webhook events."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from .paths import data_dir


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

    def __init__(self, db_path: str | Path | None = None) -> None:
        db_path = db_path or (data_dir() / "webhooks.db")
        if str(db_path) == ":memory:":
            self._db_path = ":memory:"
        else:
            path = Path(db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db_path = path
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        for statement in self._DDL:
            self._conn.execute(statement)
        self._conn.commit()

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
        """Update status to processed and clear any previous error."""
        self._conn.execute(
            "UPDATE webhook_events SET status = 'processed', error = '' WHERE event_id = ?",
            (event_id,),
        )
        self._conn.commit()

    def mark_error(self, event_id: str, error: str) -> None:
        """Update status to error and store the error message."""
        self._conn.execute(
            "UPDATE webhook_events SET status = 'error', error = ? WHERE event_id = ?",
            (error, event_id),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        import datetime

        data = dict(row)
        try:
            data["received_at_fmt"] = datetime.datetime.fromtimestamp(
                data["received_at"]
            ).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            data["received_at_fmt"] = ""
        return data

    def list_recent(self, limit: int = 50) -> list[dict]:
        """Return up to *limit* events sorted by received_at descending."""
        cursor = self._conn.execute(
            "SELECT * FROM webhook_events ORDER BY received_at DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_dict(row) for row in cursor.fetchall()]

    def list_failed(self) -> list[dict]:
        """Return all events whose status is error."""
        cursor = self._conn.execute(
            "SELECT * FROM webhook_events WHERE status = 'error' ORDER BY received_at DESC"
        )
        return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_payload(self, event_id: str) -> bytes | None:
        """Retrieve the stored raw payload, or None if it is missing."""
        cursor = self._conn.execute(
            "SELECT payload FROM webhook_events WHERE event_id = ?",
            (event_id,),
        )
        row = cursor.fetchone()
        return bytes(row["payload"]) if row else None

    def stats(self) -> dict[str, Any]:
        """Return aggregate event counts."""
        cursor = self._conn.execute(
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
        row = cursor.fetchone()
        return {
            "total": row["total"] or 0,
            "processed": row["processed"] or 0,
            "error": row["error"] or 0,
            "skipped": row["skipped"] or 0,
            "last_24h": row["last_24h"] or 0,
        }


def main(log: WebhookLog | None = None) -> None:
    """Inspect webhook records from the command line."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="solvent webhooks",
        description="Inspect the durable Stripe webhook log.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="stats",
        choices=("stats", "list", "failed"),
    )
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    webhook_log = log or WebhookLog()
    if args.command == "stats":
        print(json.dumps(webhook_log.stats(), indent=2))
        return

    rows = (
        webhook_log.list_recent(args.limit)
        if args.command == "list"
        else webhook_log.list_failed()[: args.limit]
    )
    for row in rows:
        if args.command == "list":
            print(
                f"{row['received_at_fmt']} [{row['status']}] "
                f"{row['event_type']} {row['event_id'][:16]}"
            )
        else:
            print(
                f"{row['event_id'][:16]} {row['event_type']} "
                f"err={row['error'][:60]}"
            )


if __name__ == "__main__":
    main()
