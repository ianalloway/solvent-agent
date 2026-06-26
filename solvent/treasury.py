"""
treasury.py — SOLVENT's balance sheet.

Every dollar the agent earns or spends is written here as a ledger entry.
The treasury is the agent's economic memory: it is what lets SOLVENT know
whether it is solvent, what each job actually cost, and whether the business
as a whole is profitable.

Storage is a SQLite database so it is thread-safe, concurrent, and survives restarts.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Literal, Optional
import fcntl
import threading
from contextlib import contextmanager

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "solvent.db"

EntryKind = Literal["revenue", "expense", "capital"]


@dataclass
class LedgerEntry:
    kind: EntryKind            # revenue (money in), expense (money out), capital (seed)
    amount_cents: int          # always positive; `kind` carries the sign
    memo: str                  # human-readable description
    job_id: Optional[str] = None
    vendor: Optional[str] = None
    stripe_ref: Optional[str] = None           # PaymentIntent / issuing card / refund id
    stripe_session_id: Optional[str] = None    # Checkout Session (cs_...)
    stripe_link_id: Optional[str] = None       # Payment Link (plink_...)
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: "le_" + uuid.uuid4().hex[:12])

    def signed_cents(self) -> int:
        return self.amount_cents if self.kind in ("revenue", "capital") else -self.amount_cents


class Treasury:
    _thread_local = threading.local()

    def __init__(self, path: Path = None):
        if path is None:
            self.path = DB_PATH
        else:
            self.path = Path(path)
            # Support transition from legacy JSON paths to DB paths transparently
            if self.path.suffix == ".json":
                self.path = self.path.with_suffix(".db")
        
        self.lock_path = self.path.with_suffix(".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        """Open a connection to the SQLite database with a busy timeout for concurrency safety."""
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, col_type: str) -> None:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    def _init_db(self) -> None:
        """Create the ledger and job queue tables if they do not exist."""
        with self._conn() as conn:
            with conn:
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
                ):
                    self._ensure_column(conn, "jobs", col, ctype)
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
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS stripe_checkout (
                        job_id TEXT PRIMARY KEY,
                        session_id TEXT,
                        checkout_url TEXT,
                        status TEXT,
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
                self._ensure_column(conn, "chat_sessions", "pending_job_json", "TEXT")
                self._ensure_column(conn, "chat_sessions", "notify_job_id", "TEXT")

    @contextmanager
    def lock(self):
        """Re-entrant file lock (across processes and threads) to guarantee absolute atomicity."""
        if not hasattr(self._thread_local, "lock_count"):
            self._thread_local.lock_count = 0
            self._thread_local.lock_file = None

        if self._thread_local.lock_count > 0:
            self._thread_local.lock_count += 1
            try:
                yield
            finally:
                self._thread_local.lock_count -= 1
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            f = open(self.lock_path, "w")
            try:
                fcntl.flock(f, fcntl.LOCK_EX)
                self._thread_local.lock_file = f
                self._thread_local.lock_count = 1
                yield
            finally:
                self._thread_local.lock_count -= 1
                if self._thread_local.lock_count == 0:
                    fcntl.flock(f, fcntl.LOCK_UN)
                    f.close()
                    self._thread_local.lock_file = None

    # ---- persistence (legacy compatibility stubs) --------------------
    def _load(self) -> None:
        pass

    def _load_locked(self) -> list[LedgerEntry]:
        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT id, kind, amount_cents, memo, job_id, vendor, stripe_ref, "
                "stripe_session_id, stripe_link_id, ts "
                "FROM ledger ORDER BY ts ASC, id ASC"
            )
            rows = cursor.fetchall()
            return [
                LedgerEntry(
                    id=row["id"],
                    kind=row["kind"],
                    amount_cents=row["amount_cents"],
                    memo=row["memo"],
                    job_id=row["job_id"],
                    vendor=row["vendor"],
                    stripe_ref=row["stripe_ref"],
                    stripe_session_id=row["stripe_session_id"],
                    stripe_link_id=row["stripe_link_id"],
                    ts=row["ts"],
                )
                for row in rows
            ]

    def _save(self) -> None:
        pass

    @property
    def entries(self) -> list[LedgerEntry]:
        with self.lock():
            return self._load_locked()

    def reset(self) -> None:
        """Clear the ledger and jobs table."""
        with self.lock():
            with self._conn() as conn:
                with conn:
                    conn.execute("DELETE FROM ledger")
                    conn.execute("DELETE FROM jobs")
                    conn.execute("DELETE FROM job_stages")
                    conn.execute("DELETE FROM job_metrics")
                    conn.execute("DELETE FROM stripe_checkout")
                    conn.execute("DELETE FROM events")
                    conn.execute("DELETE FROM chat_messages")
                    conn.execute("DELETE FROM chat_sessions")
                    conn.execute("DELETE FROM telegram_pairing")

    # ---- writes ------------------------------------------------------
    def record(self, kind: EntryKind, amount_cents: int, memo: str, **kw) -> LedgerEntry:
        with self.lock():
            entry = LedgerEntry(kind=kind, amount_cents=int(amount_cents), memo=memo, **kw)
            with self._conn() as conn:
                with conn:
                    conn.execute("""
                        INSERT INTO ledger (
                            id, kind, amount_cents, memo, job_id, vendor,
                            stripe_ref, stripe_session_id, stripe_link_id, ts
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        entry.id,
                        entry.kind,
                        entry.amount_cents,
                        entry.memo,
                        entry.job_id,
                        entry.vendor,
                        entry.stripe_ref,
                        entry.stripe_session_id,
                        entry.stripe_link_id,
                        entry.ts,
                    ))
            return entry

    def seed(self, amount_cents: int, memo: str = "Initial operating capital") -> LedgerEntry:
        return self.record("capital", amount_cents, memo)

    def earn(self, amount_cents: int, memo: str, **kw) -> LedgerEntry:
        return self.record("revenue", amount_cents, memo, **kw)

    def spend(self, amount_cents: int, memo: str, **kw) -> LedgerEntry:
        return self.record("expense", amount_cents, memo, **kw)

    # ---- reads -------------------------------------------------------
    def balance_cents(self) -> int:
        with self.lock():
            with self._conn() as conn:
                row = conn.execute("""
                    SELECT SUM(
                        CASE WHEN kind IN ('revenue', 'capital') THEN amount_cents
                             ELSE -amount_cents
                        END
                    ) as bal
                    FROM ledger
                """).fetchone()
                return row["bal"] or 0

    def revenue_cents(self) -> int:
        with self.lock():
            with self._conn() as conn:
                row = conn.execute("SELECT SUM(amount_cents) as s FROM ledger WHERE kind = 'revenue'").fetchone()
                return row["s"] or 0

    def expense_cents(self) -> int:
        with self.lock():
            with self._conn() as conn:
                row = conn.execute("SELECT SUM(amount_cents) as s FROM ledger WHERE kind = 'expense'").fetchone()
                return row["s"] or 0

    def capital_cents(self) -> int:
        with self.lock():
            with self._conn() as conn:
                row = conn.execute("SELECT SUM(amount_cents) as s FROM ledger WHERE kind = 'capital'").fetchone()
                return row["s"] or 0

    def net_profit_cents(self) -> int:
        """Profit excludes seed capital — it is revenue minus operating spend."""
        return self.revenue_cents() - self.expense_cents()

    def margin_pct(self) -> float:
        rev = self.revenue_cents()
        return 0.0 if rev == 0 else round(100 * self.net_profit_cents() / rev, 1)

    def job_pnl_cents(self, job_id: str) -> int:
        with self.lock():
            with self._conn() as conn:
                rev = conn.execute("SELECT SUM(amount_cents) as s FROM ledger WHERE job_id = ? AND kind = 'revenue'", (job_id,)).fetchone()["s"] or 0
                exp = conn.execute("SELECT SUM(amount_cents) as s FROM ledger WHERE job_id = ? AND kind = 'expense'", (job_id,)).fetchone()["s"] or 0
                return rev - exp

    def snapshot(self) -> dict:
        with self.lock():
            entries = self._load_locked()
            balance = self.balance_cents()
            capital = self.capital_cents()
            revenue = self.revenue_cents()
            expense = self.expense_cents()
            net_profit = revenue - expense
            margin = 0.0 if revenue == 0 else round(100 * net_profit / revenue, 1)
            return {
                "balance_cents": balance,
                "capital_cents": capital,
                "revenue_cents": revenue,
                "expense_cents": expense,
                "net_profit_cents": net_profit,
                "margin_pct": margin,
                "entries": [asdict(e) for e in entries],
            }

    # ---- job queue ----------------------------------------------------
    _JOB_COLS = (
        "topic", "budget_cents", "customer_email", "est_tokens", "market_data_calls",
        "web_search_calls", "error_reason", "checkout_session_id", "checkout_url",
        "deliverable_url", "current_stage", "quote_json", "locked_until", "job_payload_json",
        "retry_count",
    )

    def upsert_job(self, job_id: str, status: str, **kwargs) -> None:
        """Upsert a job's status and other fields in the persistent job queue table."""
        with self.lock():
            with self._conn() as conn:
                with conn:
                    existing = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
                    ts = time.time()
                    if existing:
                        fields = ["status = ?", "updated_at = ?"]
                        params = [status, ts]
                        for col in self._JOB_COLS:
                            if col in kwargs:
                                val = kwargs[col]
                                if col == "job_payload_json" and isinstance(val, dict):
                                    val = json.dumps(val)
                                fields.append(f"{col} = ?")
                                params.append(val)
                        params.append(job_id)
                        conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", params)
                    else:
                        cols = ["id", "status", "created_at", "updated_at"]
                        vals = [job_id, status, ts, ts]
                        for col in self._JOB_COLS:
                            cols.append(col)
                            val = kwargs.get(col)
                            if col == "job_payload_json" and isinstance(val, dict):
                                val = json.dumps(val)
                            vals.append(val)
                        placeholders = ", ".join(["?"] * len(cols))
                        conn.execute(f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({placeholders})", vals)

    def get_job(self, job_id: str) -> Optional[dict]:
        with self.lock():
            with self._conn() as conn:
                row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
                return dict(row) if row else None

    def list_jobs(self) -> list[dict]:
        with self.lock():
            with self._conn() as conn:
                rows = conn.execute("SELECT * FROM jobs ORDER BY created_at ASC").fetchall()
                return [dict(row) for row in rows]

    def list_jobs_by_status(self, statuses: list[str]) -> list[dict]:
        if not statuses:
            return []
        placeholders = ", ".join("?" * len(statuses))
        with self.lock():
            with self._conn() as conn:
                rows = conn.execute(
                    f"SELECT * FROM jobs WHERE status IN ({placeholders}) ORDER BY created_at ASC",
                    statuses,
                ).fetchall()
                return [dict(row) for row in rows]

    def claim_job(self, job_id: str, lease_seconds: float = 60.0) -> bool:
        """Acquire a short lease on a job for worker processing."""
        now = time.time()
        until = now + lease_seconds
        with self.lock():
            with self._conn() as conn:
                with conn:
                    row = conn.execute(
                        "SELECT locked_until FROM jobs WHERE id = ?", (job_id,)
                    ).fetchone()
                    if not row:
                        return False
                    locked = row["locked_until"]
                    if locked and locked > now:
                        return False
                    conn.execute(
                        "UPDATE jobs SET locked_until = ?, updated_at = ? WHERE id = ?",
                        (until, now, job_id),
                    )
                    return True

    def release_job(self, job_id: str) -> None:
        with self.lock():
            with self._conn() as conn:
                with conn:
                    conn.execute(
                        "UPDATE jobs SET locked_until = NULL, updated_at = ? WHERE id = ?",
                        (time.time(), job_id),
                    )

    def get_stage(self, idempotency_key: str) -> Optional[dict]:
        with self.lock():
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM job_stages WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                return dict(row) if row else None

    def complete_stage(
        self,
        job_id: str,
        stage: str,
        idempotency_key: str,
        result: dict | None = None,
        payload: dict | None = None,
    ) -> dict:
        with self.lock():
            existing = self.get_stage(idempotency_key)
            if existing and existing["status"] == "completed":
                return json.loads(existing["result_json"] or "{}")
            stage_id = "st_" + uuid.uuid4().hex[:12]
            ts = time.time()
            with self._conn() as conn:
                with conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO job_stages
                        (id, job_id, stage, idempotency_key, status, payload_json, result_json, ts)
                        VALUES (?, ?, ?, ?, 'completed', ?, ?, ?)
                    """, (
                        stage_id,
                        job_id,
                        stage,
                        idempotency_key,
                        json.dumps(payload or {}),
                        json.dumps(result or {}),
                        ts,
                    ))
            return result or {}

    def record_event(self, job_id: str, stage: str, payload: dict) -> None:
        with self.lock():
            with self._conn() as conn:
                with conn:
                    conn.execute(
                        "INSERT INTO events (id, job_id, stage, payload_json, ts) VALUES (?, ?, ?, ?, ?)",
                        ("ev_" + uuid.uuid4().hex[:12], job_id, stage, json.dumps(payload), time.time()),
                    )

    def list_events(self, job_id: str | None = None, limit: int = 500) -> list[dict]:
        with self.lock():
            with self._conn() as conn:
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
                        for col in (
                            "est_cost_cents", "actual_cost_cents", "est_margin_pct",
                            "actual_margin_pct", "margin_drift_cents", "fulfillment_seconds",
                            "refunded", "tool_calls", "decline_reason",
                            "block_rule", "block_reason",
                        ):
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
                        for col in (
                            "est_cost_cents", "actual_cost_cents", "est_margin_pct",
                            "actual_margin_pct", "margin_drift_cents", "fulfillment_seconds",
                            "refunded", "tool_calls", "decline_reason",
                            "block_rule", "block_reason",
                        ):
                            if col in kwargs:
                                cols.append(col)
                                vals.append(kwargs[col])
                        placeholders = ", ".join(["?"] * len(cols))
                        conn.execute(
                            f"INSERT INTO job_metrics ({', '.join(cols)}) VALUES ({placeholders})",
                            vals,
                        )

    def get_metrics(self, job_id: str) -> Optional[dict]:
        with self.lock():
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM job_metrics WHERE job_id = ?", (job_id,)
                ).fetchone()
                return dict(row) if row else None

    def list_metrics(self) -> list[dict]:
        with self.lock():
            with self._conn() as conn:
                rows = conn.execute("SELECT * FROM job_metrics ORDER BY ts ASC").fetchall()
                return [dict(r) for r in rows]

    def upsert_checkout(self, job_id: str, session_id: str, checkout_url: str, status: str) -> None:
        with self.lock():
            with self._conn() as conn:
                with conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO stripe_checkout
                        (job_id, session_id, checkout_url, status, ts)
                        VALUES (?, ?, ?, ?, ?)
                    """, (job_id, session_id, checkout_url, status, time.time()))

    def get_checkout(self, job_id: str) -> Optional[dict]:
        with self.lock():
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM stripe_checkout WHERE job_id = ?", (job_id,)
                ).fetchone()
                return dict(row) if row else None

    def job_has_revenue(self, job_id: str) -> bool:
        with self.lock():
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) as c FROM ledger WHERE job_id = ? AND kind = 'revenue'",
                    (job_id,),
                ).fetchone()
                return (row["c"] or 0) > 0

    def stage_completed(self, job_id: str, stage: str) -> bool:
        with self.lock():
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) as c FROM job_stages WHERE job_id = ? AND stage = ? AND status = 'completed'",
                    (job_id, stage),
                ).fetchone()
                return (row["c"] or 0) > 0

    def increment_retry_count(self, job_id: str) -> int:
        """Atomically increment retry_count for a job and return the new value."""
        with self.lock():
            with self._conn() as conn:
                with conn:
                    conn.execute(
                        "UPDATE jobs SET retry_count = COALESCE(retry_count, 0) + 1, updated_at = ? WHERE id = ?",
                        (time.time(), job_id),
                    )
                    row = conn.execute(
                        "SELECT retry_count FROM jobs WHERE id = ?", (job_id,)
                    ).fetchone()
                    return row["retry_count"] if row else 0

    # ---- chat sessions (gateway / Hermes memory) -----------------------
    def get_or_create_chat_session(
        self, channel: str, external_id: str, user_label: str | None = None
    ) -> dict:
        sid = f"{channel}:{external_id}"
        with self.lock():
            with self._conn() as conn:
                with conn:
                    row = conn.execute(
                        "SELECT * FROM chat_sessions WHERE channel = ? AND external_id = ?",
                        (channel, external_id),
                    ).fetchone()
                    ts = time.time()
                    if row:
                        return dict(row)
                    conn.execute("""
                        INSERT INTO chat_sessions
                        (id, channel, external_id, user_label, paired, created_at, updated_at)
                        VALUES (?, ?, ?, ?, 0, ?, ?)
                    """, (sid, channel, external_id, user_label, ts, ts))
                    return {
                        "id": sid,
                        "channel": channel,
                        "external_id": external_id,
                        "user_label": user_label,
                        "paired": 0,
                        "created_at": ts,
                        "updated_at": ts,
                    }

    def update_chat_session(self, session_id: str, **kwargs) -> None:
        with self.lock():
            with self._conn() as conn:
                with conn:
                    fields = ["updated_at = ?"]
                    params: list = [time.time()]
                    for col in ("user_label", "paired", "pending_job_json", "notify_job_id"):
                        if col in kwargs:
                            val = kwargs[col]
                            if col == "pending_job_json" and isinstance(val, dict):
                                val = json.dumps(val)
                            fields.append(f"{col} = ?")
                            params.append(val)
                    params.append(session_id)
                    conn.execute(
                        f"UPDATE chat_sessions SET {', '.join(fields)} WHERE id = ?",
                        params,
                    )

    def get_chat_session(self, session_id: str) -> Optional[dict]:
        with self.lock():
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
                ).fetchone()
                return dict(row) if row else None

    def list_chat_sessions_by_channel(self, channel: str) -> list[dict]:
        with self.lock():
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM chat_sessions WHERE channel = ? ORDER BY updated_at DESC",
                    (channel,),
                ).fetchall()
                return [dict(r) for r in rows]

    def add_chat_message(self, session_id: str, role: str, content: str) -> None:
        with self.lock():
            with self._conn() as conn:
                with conn:
                    conn.execute(
                        "INSERT INTO chat_messages (id, session_id, role, content, ts) VALUES (?, ?, ?, ?, ?)",
                        ("cm_" + uuid.uuid4().hex[:12], session_id, role, content, time.time()),
                    )

    def get_chat_messages(self, session_id: str, limit: int = 20) -> list[dict]:
        with self.lock():
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT role, content, ts FROM chat_messages WHERE session_id = ? "
                    "ORDER BY ts DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
                return [dict(r) for r in reversed(rows)]

    def create_pairing_code(self, user_id: str, username: str | None = None, ttl: float = 3600) -> str:
        code = "TG-" + uuid.uuid4().hex[:6].upper()
        now = time.time()
        with self.lock():
            with self._conn() as conn:
                with conn:
                    conn.execute("""
                        INSERT INTO telegram_pairing (code, user_id, username, expires_at, approved, created_at)
                        VALUES (?, ?, ?, ?, 0, ?)
                    """, (code, user_id, username, now + ttl, now))
        return code

    def approve_pairing(self, code: str) -> Optional[dict]:
        with self.lock():
            with self._conn() as conn:
                with conn:
                    row = conn.execute(
                        "SELECT * FROM telegram_pairing WHERE code = ?", (code.upper(),)
                    ).fetchone()
                    if not row:
                        return None
                    if row["expires_at"] < time.time():
                        return None
                    conn.execute(
                        "UPDATE telegram_pairing SET approved = 1 WHERE code = ?",
                        (code.upper(),),
                    )
                    conn.execute(
                        "UPDATE chat_sessions SET paired = 1, updated_at = ? "
                        "WHERE channel = 'telegram' AND external_id = ?",
                        (time.time(), row["user_id"]),
                    )
                    return dict(row)

    def list_pending_pairings(self) -> list[dict]:
        now = time.time()
        with self.lock():
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM telegram_pairing WHERE approved = 0 AND expires_at > ? ORDER BY created_at",
                    (now,),
                ).fetchall()
                return [dict(r) for r in rows]

    def is_user_paired(self, user_id: str) -> bool:
        with self.lock():
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT paired FROM chat_sessions WHERE channel = 'telegram' AND external_id = ?",
                    (user_id,),
                ).fetchone()
                if row and row["paired"]:
                    return True
                prow = conn.execute(
                    "SELECT approved FROM telegram_pairing WHERE user_id = ? AND approved = 1",
                    (user_id,),
                ).fetchone()
                return bool(prow)


def fmt(cents: int) -> str:
    return f"${cents/100:,.2f}"
