"""
rate_limit.py — configurable sliding-window rate limiter with SQLite persistence.

Supports:
- Per-user sliding window counters (requests per N seconds)
- Short-term burst limit (e.g. 5/min)
- Long-term limit (e.g. 30/hour)
- Temporary bans (block user for a duration)
- Persistent across restarts via SQLite
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_events (
    user_key TEXT NOT NULL,
    ts       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rate_events_user_ts ON rate_events (user_key, ts);

CREATE TABLE IF NOT EXISTS rate_bans (
    user_key   TEXT PRIMARY KEY,
    expires_at REAL NOT NULL,
    reason     TEXT NOT NULL DEFAULT ''
);
"""


class RateLimiter:
    """Configurable sliding-window rate limiter backed by SQLite."""

    def __init__(
        self,
        db_path: str = ".solvent/rate_limits.db",
        burst_limit: int = 5,
        burst_window: int = 60,
        hourly_limit: int = 30,
        daily_limit: int = 200,
    ) -> None:
        self.burst_limit = burst_limit
        self.burst_window = burst_window
        self.hourly_limit = hourly_limit
        self.daily_limit = daily_limit

        if db_path == ":memory:":
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)

        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, user_key: str) -> tuple[bool, str]:
        """Check whether *user_key* is allowed to make a request.

        Records the attempt when allowed.  Returns ``(allowed, reason)``
        where *reason* is an empty string on success.
        """
        now = time.time()

        # 1. Ban check
        ban_info = self._get_ban(user_key)
        if ban_info is not None:
            remaining = max(0, int(ban_info["expires_at"] - now))
            reason = ban_info.get("reason") or "banned"
            return False, f"Banned: {reason} (expires in {remaining}s)"

        # 2. Count recent events in each window
        burst_count = self._count_since(user_key, now - self.burst_window)
        hourly_count = self._count_since(user_key, now - 3600)
        daily_count = self._count_since(user_key, now - 86400)

        if burst_count >= self.burst_limit:
            return False, (
                f"Burst limit exceeded ({burst_count}/{self.burst_limit} "
                f"in {self.burst_window}s)"
            )
        if hourly_count >= self.hourly_limit:
            return False, (
                f"Hourly limit exceeded ({hourly_count}/{self.hourly_limit})"
            )
        if daily_count >= self.daily_limit:
            return False, (f"Daily limit exceeded ({daily_count}/{self.daily_limit})")

        # 3. Record the event
        self._conn.execute(
            "INSERT INTO rate_events (user_key, ts) VALUES (?, ?)",
            (user_key, now),
        )
        self._conn.commit()
        return True, ""

    def ban(
        self,
        user_key: str,
        duration_seconds: int = 3600,
        reason: str = "",
    ) -> None:
        """Temporarily ban *user_key* for *duration_seconds*."""
        expires_at = time.time() + duration_seconds
        self._conn.execute(
            """
            INSERT INTO rate_bans (user_key, expires_at, reason)
            VALUES (?, ?, ?)
            ON CONFLICT(user_key) DO UPDATE SET
                expires_at = excluded.expires_at,
                reason     = excluded.reason
            """,
            (user_key, expires_at, reason),
        )
        self._conn.commit()

    def unban(self, user_key: str) -> None:
        """Remove an active ban for *user_key*."""
        self._conn.execute("DELETE FROM rate_bans WHERE user_key = ?", (user_key,))
        self._conn.commit()

    def is_banned(self, user_key: str) -> bool:
        """Return True if *user_key* has an active (not yet expired) ban."""
        now = time.time()
        row = self._conn.execute(
            "SELECT expires_at FROM rate_bans WHERE user_key = ? AND expires_at > ?",
            (user_key, now),
        ).fetchone()
        return row is not None

    def stats(self, user_key: str) -> dict:
        """Return current counters and ban status for *user_key*."""
        now = time.time()
        burst_count = self._count_since(user_key, now - self.burst_window)
        hourly_count = self._count_since(user_key, now - 3600)
        daily_count = self._count_since(user_key, now - 86400)

        ban_info = self._get_ban(user_key)
        if ban_info is not None:
            banned = ban_info["expires_at"] > now
            ban_expires = ban_info["expires_at"]
        else:
            banned = False
            ban_expires = None

        return {
            "burst_count": burst_count,
            "hourly_count": hourly_count,
            "daily_count": daily_count,
            "is_banned": banned,
            "ban_expires": ban_expires,
        }

    def cleanup(self) -> None:
        """Delete rate_events older than 24 hours and expired bans."""
        cutoff = time.time() - 86400
        self._conn.execute("DELETE FROM rate_events WHERE ts < ?", (cutoff,))
        now = time.time()
        self._conn.execute("DELETE FROM rate_bans WHERE expires_at <= ?", (now,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _count_since(self, user_key: str, since_ts: float) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM rate_events WHERE user_key = ? AND ts >= ?",
            (user_key, since_ts),
        ).fetchone()
        return row[0] if row else 0

    def _get_ban(self, user_key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT expires_at, reason FROM rate_bans WHERE user_key = ?",
            (user_key,),
        ).fetchone()
        if row is None:
            return None
        return {"expires_at": row[0], "reason": row[1]}
