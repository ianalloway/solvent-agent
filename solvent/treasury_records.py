"""Treasury records beside the ledger: events, job metrics, checkouts and
read-only lookups of retired Nano history.

Mixed into :class:`solvent.treasury.Treasury`; methods use its ``lock`` and
``_conn``. Import ``Treasury``, not these classes.
"""

from __future__ import annotations

import json
import time
import uuid


#: Columns `upsert_metrics` accepts, in one place rather than per code path.
METRIC_COLUMNS = (
    "est_cost_cents",
    "actual_cost_cents",
    "est_margin_pct",
    "actual_margin_pct",
    "margin_drift_cents",
    "fulfillment_seconds",
    "refunded",
    "tool_calls",
    "decline_reason",
    "block_rule",
    "block_reason",
    "quality_score",
    "quality_grade",
    "quality_flags",
)


class _RecordsMixin:
    def record_event(self, job_id: str, stage: str, payload: dict) -> None:
        with self.lock():
            with self._conn() as conn:
                with conn:
                    conn.execute(
                        "INSERT INTO events (id, job_id, stage, payload_json, ts) VALUES (?, ?, ?, ?, ?)",
                        (
                            "ev_" + uuid.uuid4().hex[:12],
                            job_id,
                            stage,
                            json.dumps(payload),
                            time.time(),
                        ),
                    )

    def list_events(self, job_id: str | None = None, limit: int = 500) -> list[dict]:
        with self.lock(), self._conn() as conn:
            if job_id:
                rows = conn.execute(
                    "SELECT * FROM events WHERE job_id = ? ORDER BY ts DESC LIMIT ?",
                    (job_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    def upsert_metrics(self, job_id: str, **kwargs) -> None:
        with self.lock():
            with self._conn() as conn:
                with conn:
                    existing = conn.execute(
                        "SELECT job_id FROM job_metrics WHERE job_id = ?", (job_id,)
                    ).fetchone()
                    ts = time.time()
                    if existing:
                        fields = ["ts = ?"]
                        params: list = [ts]
                        for col in METRIC_COLUMNS:
                            if col in kwargs:
                                fields.append(f"{col} = ?")
                                params.append(kwargs[col])
                        params.append(job_id)
                        conn.execute(
                            f"UPDATE job_metrics SET {', '.join(fields)} WHERE job_id = ?",
                            params,
                        )
                    else:
                        cols = ["job_id", "ts"]
                        vals: list = [job_id, ts]
                        for col in METRIC_COLUMNS:
                            if col in kwargs:
                                cols.append(col)
                                vals.append(kwargs[col])
                        placeholders = ", ".join(["?"] * len(cols))
                        conn.execute(
                            f"INSERT INTO job_metrics ({', '.join(cols)}) VALUES ({placeholders})",
                            vals,
                        )

    def get_metrics(self, job_id: str) -> dict | None:
        with self.lock(), self._conn() as conn:
            row = conn.execute("SELECT * FROM job_metrics WHERE job_id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    def list_metrics(self) -> list[dict]:
        with self.lock(), self._conn() as conn:
            rows = conn.execute("SELECT * FROM job_metrics ORDER BY ts ASC").fetchall()
            return [dict(r) for r in rows]

    def upsert_checkout(
        self,
        job_id: str,
        session_id: str,
        checkout_url: str,
        status: str,
        *,
        rail: str | None = None,
    ) -> None:
        """Record a checkout link, keeping the age and reminder count it already had."""
        now = time.time()
        rail_value = rail or "stripe"
        with self.lock(), self._conn() as conn, conn:
            conn.execute(
                """
                        INSERT INTO stripe_checkout
                        (job_id, session_id, checkout_url, status, ts, created_at, reminders_sent, rail)
                        VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                        ON CONFLICT(job_id) DO UPDATE SET
                            session_id = excluded.session_id,
                            checkout_url = excluded.checkout_url,
                            status = excluded.status,
                            ts = excluded.ts,
                            rail = COALESCE(excluded.rail, stripe_checkout.rail)
                    """,
                (job_id, session_id, checkout_url, status, now, now, rail_value),
            )

    def record_checkout_reminder(self, job_id: str, ts: float | None = None) -> int:
        """Count one payment reminder against a checkout; returns the new total."""
        stamp = time.time() if ts is None else ts
        with self.lock(), self._conn() as conn, conn:
            conn.execute(
                """
                        UPDATE stripe_checkout
                        SET reminders_sent = COALESCE(reminders_sent, 0) + 1,
                            last_reminder_at = ?
                        WHERE job_id = ?
                    """,
                (stamp, job_id),
            )
            row = conn.execute(
                "SELECT reminders_sent FROM stripe_checkout WHERE job_id = ?", (job_id,)
            ).fetchone()
            return (row["reminders_sent"] if row else 0) or 0

    def list_checkouts(self, status: str | None = None) -> list[dict]:
        """Every recorded checkout, oldest first, optionally filtered by status."""
        with self.lock(), self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM stripe_checkout WHERE status = ? ORDER BY COALESCE(created_at, ts) ASC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM stripe_checkout ORDER BY COALESCE(created_at, ts) ASC"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_checkout(self, job_id: str) -> dict | None:
        with self.lock(), self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM stripe_checkout WHERE job_id = ?", (job_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_nano_invoice(self, job_id: str) -> dict | None:
        """Read a retired invoice for manual reconciliation; never update it."""
        with self.lock(), self._conn() as conn:
            row = conn.execute("SELECT * FROM nano_invoices WHERE job_id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    def list_nano_invoices(self, status: str | None = None) -> list[dict]:
        """Historical invoices only; the Nano payment rail has been removed."""
        with self.lock(), self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM nano_invoices WHERE status = ? ORDER BY ts ASC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM nano_invoices ORDER BY ts ASC").fetchall()
            return [dict(r) for r in rows]

    def is_legacy_nano_job(self, job_id: str) -> bool:
        """Recognize retired payments, including interrupted pre-invoice writes.

        Read all durable projections rather than trusting only the invoice row:
        old versions wrote the checkout stage before the invoice, and may have
        crashed between them. Never convert those jobs into Stripe payments.
        """

        def nano_reference(value: object) -> bool:
            if not isinstance(value, str):
                return False
            value = value.lower()
            return value.startswith(("nano:", "nano_", "inv_sim_", "inv_live_", "block_sim_")) or (
                len(value) == 64 and all(c in "0123456789abcdef" for c in value)
            )

        def nano_record(record: dict) -> bool:
            return record.get("rail") == "nano" or any(
                nano_reference(record.get(key))
                for key in (
                    "session_id",
                    "checkout_session_id",
                    "stripe_session_id",
                    "invoice_id",
                    "checkout_url",
                    "url",
                    "pay_uri",
                    "payment_ref",
                    "stripe_ref",
                    "block_hash",
                )
            )

        with self.lock(), self._conn() as conn:
            if conn.execute("SELECT 1 FROM nano_invoices WHERE job_id = ?", (job_id,)).fetchone():
                return True
            for table, column in (
                ("jobs", "id"),
                ("stripe_checkout", "job_id"),
                ("ledger", "job_id"),
            ):
                for row in conn.execute(f"SELECT * FROM {table} WHERE {column} = ?", (job_id,)):
                    if nano_record(dict(row)):
                        return True
            for row in conn.execute(
                "SELECT result_json FROM job_stages WHERE job_id = ? AND stage IN ('checkout', 'paid')",
                (job_id,),
            ):
                try:
                    record = json.loads(row["result_json"] or "{}")
                except (TypeError, ValueError):
                    continue
                if isinstance(record, dict) and nano_record(record):
                    return True
        return False
