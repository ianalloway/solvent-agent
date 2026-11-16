"""SQLite schema for the treasury: table creation and additive column migrations.

Mixed into :class:`solvent.treasury.Treasury`; methods use its ``lock`` and
``_conn``. Import ``Treasury``, not these classes.
"""

from __future__ import annotations

import sqlite3


class _SchemaMixin:
    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, col_type: str) -> None:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    def _init_db(self) -> None:
        """Create the ledger and job queue tables if they do not exist."""
        with self._conn() as conn, conn:
            conn.execute("""
                    CREATE TABLE IF NOT EXISTS ledger (
                        id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL,
                        amount_cents INTEGER NOT NULL,
                        memo TEXT NOT NULL,
                        job_id TEXT,
                        vendor TEXT,
                        stripe_ref TEXT,
                        stripe_session_id TEXT,
                        stripe_link_id TEXT,
                        ts REAL NOT NULL
                    )
                """)
            self._ensure_column(conn, "ledger", "stripe_session_id", "TEXT")
            self._ensure_column(conn, "ledger", "stripe_link_id", "TEXT")
            self._ensure_column(conn, "ledger", "payment_ref", "TEXT")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS spend_reservations (
                    id TEXT PRIMARY KEY,
                    job_id TEXT,
                    vendor TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
                    memo TEXT NOT NULL
                )
            """)
            conn.execute("""
                    CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY,
                        topic TEXT,
                        budget_cents INTEGER,
                        status TEXT,
                        customer_email TEXT,
                        est_tokens INTEGER,
                        market_data_calls INTEGER,
                        web_search_calls INTEGER,
                        created_at REAL,
                        updated_at REAL,
                        error_reason TEXT
                    )
                """)
            for col, ctype in (
                ("checkout_session_id", "TEXT"),
                ("checkout_url", "TEXT"),
                ("deliverable_url", "TEXT"),
                ("current_stage", "TEXT"),
                ("quote_json", "TEXT"),
                ("locked_until", "REAL"),
                ("job_payload_json", "TEXT"),
                ("retry_count", "INTEGER DEFAULT 0"),
                ("job_owner_session_id", "TEXT"),
                ("public_intake", "INTEGER"),
            ):
                self._ensure_column(conn, "jobs", col, ctype)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS public_admission (
                    name TEXT PRIMARY KEY,
                    window INTEGER NOT NULL,
                    used INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS jobs_customer_recent
                ON jobs (lower(trim(customer_email)), created_at)
            """)
            conn.execute("""
                    CREATE TABLE IF NOT EXISTS job_stages (
                        id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        payload_json TEXT,
                        result_json TEXT,
                        ts REAL NOT NULL
                    )
                """)
            conn.execute("""
                    CREATE TABLE IF NOT EXISTS job_metrics (
                        job_id TEXT PRIMARY KEY,
                        est_cost_cents INTEGER,
                        actual_cost_cents INTEGER,
                        est_margin_pct REAL,
                        actual_margin_pct REAL,
                        margin_drift_cents INTEGER,
                        fulfillment_seconds REAL,
                        refunded INTEGER DEFAULT 0,
                        tool_calls INTEGER DEFAULT 0,
                        decline_reason TEXT,
                        block_rule TEXT,
                        block_reason TEXT,
                        ts REAL NOT NULL
                    )
                """)
            self._ensure_column(conn, "job_metrics", "block_rule", "TEXT")
            self._ensure_column(conn, "job_metrics", "block_reason", "TEXT")
            self._ensure_column(conn, "job_metrics", "quality_score", "REAL")
            self._ensure_column(conn, "job_metrics", "quality_grade", "TEXT")
            self._ensure_column(conn, "job_metrics", "quality_flags", "TEXT")
            conn.execute("""
                    CREATE TABLE IF NOT EXISTS stripe_checkout (
                        job_id TEXT PRIMARY KEY,
                        session_id TEXT,
                        checkout_url TEXT,
                        status TEXT,
                        ts REAL NOT NULL
                    )
                """)
            # Payment chasing needs the age of the *original* link and how many
            # times the customer has been nudged, neither of which survives a
            # status update on its own.
            for col, ctype in (
                ("created_at", "REAL"),
                ("reminders_sent", "INTEGER DEFAULT 0"),
                ("last_reminder_at", "REAL"),
                ("rail", "TEXT"),
            ):
                self._ensure_column(conn, "stripe_checkout", col, ctype)
            # Retain the legacy schema and rows for audit/reconciliation. No active writer.
            conn.execute("""
                    CREATE TABLE IF NOT EXISTS nano_invoices (
                        job_id TEXT PRIMARY KEY,
                        invoice_id TEXT,
                        destination TEXT,
                        amount_cents INTEGER,
                        amount_raw TEXT,
                        xno_rate_usd TEXT,
                        rate_ts REAL,
                        block_hash TEXT,
                        status TEXT,
                        expires_at REAL,
                        pay_uri TEXT,
                        ts REAL NOT NULL
                    )
                """)
            conn.execute("""
                    CREATE TABLE IF NOT EXISTS events (
                        id TEXT PRIMARY KEY,
                        job_id TEXT,
                        stage TEXT,
                        payload_json TEXT,
                        ts REAL NOT NULL
                    )
                """)
            conn.execute("""
                    CREATE TABLE IF NOT EXISTS chat_sessions (
                        id TEXT PRIMARY KEY,
                        channel TEXT NOT NULL,
                        external_id TEXT NOT NULL,
                        user_label TEXT,
                        paired INTEGER DEFAULT 0,
                        pending_job_json TEXT,
                        notify_job_id TEXT,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        UNIQUE(channel, external_id)
                    )
                """)
            conn.execute("""
                    CREATE TABLE IF NOT EXISTS chat_messages (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        ts REAL NOT NULL
                    )
                """)
            conn.execute("""
                    CREATE TABLE IF NOT EXISTS telegram_pairing (
                        code TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        username TEXT,
                        expires_at REAL NOT NULL,
                        approved INTEGER DEFAULT 0,
                        created_at REAL NOT NULL
                    )
                """)
            conn.execute("""
                    CREATE TABLE IF NOT EXISTS openclaw_tokens (
                        token TEXT PRIMARY KEY,
                        expires_at REAL NOT NULL,
                        used INTEGER DEFAULT 0,
                        created_at REAL NOT NULL
                    )
                """)
            self._ensure_column(conn, "chat_sessions", "pending_job_json", "TEXT")
            self._ensure_column(conn, "chat_sessions", "notify_job_id", "TEXT")
