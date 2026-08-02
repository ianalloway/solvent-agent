"""Tests for solvent.rate_limit — configurable per-user rate limiter."""

from __future__ import annotations

import time
import unittest

from solvent.rate_limit import RateLimiter


def _limiter(**kwargs) -> RateLimiter:
    """Return a RateLimiter backed by an in-memory SQLite DB."""
    defaults = {
        "db_path": ":memory:",
        "burst_limit": 5,
        "burst_window": 60,
        "hourly_limit": 30,
        "daily_limit": 200,
    }
    defaults.update(kwargs)
    return RateLimiter(**defaults)


class TestRateLimiterBasic(unittest.TestCase):
    def test_first_request_allowed(self):
        rl = _limiter()
        allowed, reason = rl.check("user:1")
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_stats_reflect_count(self):
        rl = _limiter()
        rl.check("user:1")
        rl.check("user:1")
        s = rl.stats("user:1")
        self.assertEqual(s["burst_count"], 2)
        self.assertEqual(s["hourly_count"], 2)
        self.assertEqual(s["daily_count"], 2)
        self.assertFalse(s["is_banned"])
        self.assertIsNone(s["ban_expires"])

    def test_different_users_are_independent(self):
        rl = _limiter(burst_limit=2)
        rl.check("a")
        rl.check("a")
        # 'a' is at limit, 'b' should still be fine
        ok_a, _ = rl.check("a")
        ok_b, _ = rl.check("b")
        self.assertFalse(ok_a)
        self.assertTrue(ok_b)


class TestBurstLimit(unittest.TestCase):
    def test_burst_blocks_after_n_rapid_requests(self):
        rl = _limiter(burst_limit=3)
        for _ in range(3):
            ok, _ = rl.check("user:burst")
            self.assertTrue(ok)

        blocked, reason = rl.check("user:burst")
        self.assertFalse(blocked)
        self.assertIn("Burst", reason)

    def test_burst_window_expiry_allows_again(self):
        """Requests outside the burst window should not count against the burst."""
        rl = _limiter(burst_limit=2, burst_window=60)
        now = time.time()
        # Simulate 2 old events (just outside the burst window)
        old_ts = now - 61
        rl._conn.executemany(
            "INSERT INTO rate_events (user_key, ts) VALUES (?, ?)",
            [("user:x", old_ts), ("user:x", old_ts)],
        )
        rl._conn.commit()

        # Both old events fall outside the burst window — fresh request OK
        ok, reason = rl.check("user:x")
        self.assertTrue(ok, reason)


class TestHourlyLimit(unittest.TestCase):
    def test_hourly_limit_blocks_correctly(self):
        """Insert events via the DB to simulate approaching the hourly limit."""
        rl = _limiter(burst_limit=1000, hourly_limit=10, daily_limit=10000)
        now = time.time()
        # Pre-populate 10 events within the last hour
        rows = [("user:h", now - i * 60) for i in range(10)]
        rl._conn.executemany(
            "INSERT INTO rate_events (user_key, ts) VALUES (?, ?)", rows
        )
        rl._conn.commit()

        ok, reason = rl.check("user:h")
        self.assertFalse(ok)
        self.assertIn("Hourly", reason)

    def test_events_older_than_hour_not_counted(self):
        rl = _limiter(burst_limit=1000, hourly_limit=5, daily_limit=10000)
        now = time.time()
        # 5 events just outside the 1-hour window
        rows = [("user:h2", now - 3601 - i) for i in range(5)]
        rl._conn.executemany(
            "INSERT INTO rate_events (user_key, ts) VALUES (?, ?)", rows
        )
        rl._conn.commit()

        ok, _ = rl.check("user:h2")
        self.assertTrue(ok)


class TestDailyLimit(unittest.TestCase):
    def test_daily_limit_blocks_correctly(self):
        """Pre-populating the daily quota must trip the daily cap, not hourly/burst.

        Mirrors ``TestHourlyLimit``: events are placed outside the 60s burst
        window but inside the 24h rolling day so only the daily counter trips.
        """
        rl = _limiter(burst_limit=1000, hourly_limit=1000, daily_limit=5)
        now = time.time()
        # 5 events spread within the last day, all older than the burst window.
        rows = [("user:d", now - 300 - i * 600) for i in range(5)]
        rl._conn.executemany(
            "INSERT INTO rate_events (user_key, ts) VALUES (?, ?)", rows
        )
        rl._conn.commit()

        ok, reason = rl.check("user:d")
        self.assertFalse(ok)
        self.assertIn("Daily", reason)

    def test_daily_events_older_than_24h_not_counted(self):
        """Events older than the 24h window must not count against the daily cap."""
        rl = _limiter(burst_limit=1000, hourly_limit=1000, daily_limit=2)
        now = time.time()
        # 2 events just outside the 24h rolling window
        rows = [("user:d2", now - 86401 - i) for i in range(2)]
        rl._conn.executemany(
            "INSERT INTO rate_events (user_key, ts) VALUES (?, ?)", rows
        )
        rl._conn.commit()

        ok, _ = rl.check("user:d2")
        self.assertTrue(ok)


class TestBanUnban(unittest.TestCase):
    def test_ban_blocks_immediately(self):
        rl = _limiter()
        rl.ban("user:bad", duration_seconds=3600, reason="spam")
        ok, reason = rl.check("user:bad")
        self.assertFalse(ok)
        self.assertIn("spam", reason)

    def test_unban_restores_access(self):
        rl = _limiter()
        rl.ban("user:bad")
        rl.unban("user:bad")
        ok, _ = rl.check("user:bad")
        self.assertTrue(ok)

    def test_is_banned_true_while_active(self):
        rl = _limiter()
        rl.ban("user:b", duration_seconds=60)
        self.assertTrue(rl.is_banned("user:b"))

    def test_is_banned_false_after_expiry(self):
        rl = _limiter()
        # Ban that has already expired
        rl._conn.execute(
            "INSERT INTO rate_bans (user_key, expires_at, reason) VALUES (?, ?, ?)",
            ("user:old", time.time() - 1, ""),
        )
        rl._conn.commit()
        self.assertFalse(rl.is_banned("user:old"))

    def test_stats_shows_ban_info(self):
        rl = _limiter()
        rl.ban("user:c", duration_seconds=300)
        s = rl.stats("user:c")
        self.assertTrue(s["is_banned"])
        self.assertIsNotNone(s["ban_expires"])
        self.assertGreater(s["ban_expires"], time.time())


class TestCleanup(unittest.TestCase):
    def test_cleanup_removes_old_records(self):
        rl = _limiter()
        now = time.time()
        # Insert 5 events older than 24 h and 2 recent events
        old = now - 86401
        rl._conn.executemany(
            "INSERT INTO rate_events (user_key, ts) VALUES (?, ?)",
            [("user:c", old)] * 5 + [("user:c", now)] * 2,
        )
        rl._conn.commit()

        rl.cleanup()

        row = rl._conn.execute(
            "SELECT COUNT(*) FROM rate_events WHERE user_key = ?", ("user:c",)
        ).fetchone()
        # Only the 2 recent events should remain
        self.assertEqual(row[0], 2)

    def test_cleanup_removes_expired_bans(self):
        rl = _limiter()
        rl._conn.execute(
            "INSERT INTO rate_bans (user_key, expires_at, reason) VALUES (?, ?, ?)",
            ("user:expired", time.time() - 10, "old ban"),
        )
        rl._conn.commit()

        rl.cleanup()

        row = rl._conn.execute(
            "SELECT COUNT(*) FROM rate_bans WHERE user_key = ?", ("user:expired",)
        ).fetchone()
        self.assertEqual(row[0], 0)


class TestPersistence(unittest.TestCase):
    def test_db_persists_across_restarts(self):
        """State written by one RateLimiter instance must be visible to the next."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "rl.db")

            # First instance — make 3 requests
            rl1 = RateLimiter(
                db_path=db_path, burst_limit=10, hourly_limit=100, daily_limit=1000
            )
            for _ in range(3):
                rl1.check("user:persist")

            # Second instance pointing at the same DB
            rl2 = RateLimiter(
                db_path=db_path, burst_limit=10, hourly_limit=100, daily_limit=1000
            )
            s = rl2.stats("user:persist")
            self.assertEqual(s["burst_count"], 3)
            self.assertEqual(s["hourly_count"], 3)

    def test_ban_persists_across_restarts(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "rl2.db")

            rl1 = RateLimiter(db_path=db_path)
            rl1.ban("user:p2", duration_seconds=3600, reason="persistent ban")

            rl2 = RateLimiter(db_path=db_path)
            self.assertTrue(rl2.is_banned("user:p2"))
            ok, reason = rl2.check("user:p2")
            self.assertFalse(ok)
            self.assertIn("persistent ban", reason)


if __name__ == "__main__":
    unittest.main()
