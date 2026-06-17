"""
treasury.py — SOLVENT's balance sheet.

Every dollar the agent earns or spends is written here as a ledger entry.
The treasury is the agent's economic memory: it is what lets SOLVENT know
whether it is solvent, what each job actually cost, and whether the business
as a whole is profitable.

Storage is a SQLite database so it is thread-safe, concurrent, and survives restarts.
"""

from __future__ import annotations

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
    stripe_ref: Optional[str] = None   # payment link / invoice / payment id
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
                        ts REAL NOT NULL
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
                "SELECT id, kind, amount_cents, memo, job_id, vendor, stripe_ref, ts "
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
                    ts=row["ts"]
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

    # ---- writes ------------------------------------------------------
    def record(self, kind: EntryKind, amount_cents: int, memo: str, **kw) -> LedgerEntry:
        with self.lock():
            entry = LedgerEntry(kind=kind, amount_cents=int(amount_cents), memo=memo, **kw)
            with self._conn() as conn:
                with conn:
                    conn.execute("""
                        INSERT INTO ledger (id, kind, amount_cents, memo, job_id, vendor, stripe_ref, ts)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        entry.id,
                        entry.kind,
                        entry.amount_cents,
                        entry.memo,
                        entry.job_id,
                        entry.vendor,
                        entry.stripe_ref,
                        entry.ts
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
                        for col in ("topic", "budget_cents", "customer_email", "est_tokens", "market_data_calls", "web_search_calls", "error_reason"):
                            if col in kwargs:
                                fields.append(f"{col} = ?")
                                params.append(kwargs[col])
                        params.append(job_id)
                        conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", params)
                    else:
                        cols = ["id", "status", "created_at", "updated_at"]
                        vals = [job_id, status, ts, ts]
                        for col in ("topic", "budget_cents", "customer_email", "est_tokens", "market_data_calls", "web_search_calls", "error_reason"):
                            cols.append(col)
                            vals.append(kwargs.get(col))
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


def fmt(cents: int) -> str:
    return f"${cents/100:,.2f}"
