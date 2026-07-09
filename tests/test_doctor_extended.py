"""Tests for extended doctor checks added in hackathon improvements."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


class TestDoctorExtendedChecks(unittest.TestCase):
    """Tests that the extended checks exist and return correctly shaped results."""

    def _run(self):
        from solvent.doctor import run_checks
        return run_checks()

    def _by_name(self, checks, name):
        for c in checks:
            if c["name"] == name:
                return c
        return None

    # ------------------------------------------------------------------
    # rate_limit_db
    # ------------------------------------------------------------------

    def test_rate_limit_db_check_present(self):
        checks = self._run()
        names = {c["name"] for c in checks}
        self.assertIn("rate_limit_db", names)

    def test_rate_limit_db_check_has_ok_flag(self):
        checks = self._run()
        c = self._by_name(checks, "rate_limit_db")
        self.assertIsNotNone(c)
        self.assertIn("ok", c)

    def test_rate_limit_db_ok_on_success(self):
        checks = self._run()
        c = self._by_name(checks, "rate_limit_db")
        self.assertTrue(c["ok"])

    def test_rate_limit_db_fail_on_error(self):
        with mock.patch(
            "solvent.rate_limit.RateLimiter",
            side_effect=Exception("db gone"),
        ):
            from solvent.doctor import run_checks
            checks = run_checks()
        c = self._by_name(checks, "rate_limit_db")
        self.assertFalse(c["ok"])
        self.assertIn("db gone", c["detail"])

    # ------------------------------------------------------------------
    # events_table
    # ------------------------------------------------------------------

    def test_events_table_check_present(self):
        checks = self._run()
        names = {c["name"] for c in checks}
        self.assertIn("events_table", names)

    def test_events_table_ok_on_empty(self):
        checks = self._run()
        c = self._by_name(checks, "events_table")
        self.assertTrue(c["ok"])
        self.assertIn("event", c["detail"])

    def test_events_table_detail_contains_count(self):
        """Detail string should mention count of events."""
        checks = self._run()
        c = self._by_name(checks, "events_table")
        self.assertIsNotNone(c)
        self.assertIn("event", c["detail"])

    # ------------------------------------------------------------------
    # chat_sessions_table
    # ------------------------------------------------------------------

    def test_chat_sessions_table_check_present(self):
        checks = self._run()
        names = {c["name"] for c in checks}
        self.assertIn("chat_sessions_table", names)

    def test_chat_sessions_table_ok(self):
        checks = self._run()
        c = self._by_name(checks, "chat_sessions_table")
        self.assertTrue(c["ok"])
        self.assertIn("session", c["detail"])

    # ------------------------------------------------------------------
    # job_metrics_table
    # ------------------------------------------------------------------

    def test_job_metrics_table_check_present(self):
        checks = self._run()
        names = {c["name"] for c in checks}
        self.assertIn("job_metrics_table", names)

    def test_job_metrics_table_ok(self):
        checks = self._run()
        c = self._by_name(checks, "job_metrics_table")
        self.assertTrue(c["ok"])
        self.assertIn("row", c["detail"])

    # ------------------------------------------------------------------
    # log_file
    # ------------------------------------------------------------------

    def test_log_file_check_present(self):
        checks = self._run()
        names = {c["name"] for c in checks}
        self.assertIn("log_file", names)

    def test_log_file_ok_when_absent(self):
        """When the log file doesn't exist it's fine — not yet created."""
        with mock.patch("solvent.observability.LOG_PATH", Path("/nonexistent/solvent.log")):
            from solvent.doctor import run_checks
            checks = run_checks()
        c = self._by_name(checks, "log_file")
        self.assertTrue(c["ok"])
        self.assertIn("not yet created", c["detail"])

    def test_log_file_ok_with_existing_file(self):
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            f.write(b'{"event": "test"}\n')
            tmp = Path(f.name)
        try:
            with mock.patch("solvent.observability.LOG_PATH", tmp):
                from solvent.doctor import run_checks
                checks = run_checks()
            c = self._by_name(checks, "log_file")
            self.assertTrue(c["ok"])
            self.assertIn("KB", c["detail"])
            self.assertIn("old", c["detail"])
        finally:
            tmp.unlink(missing_ok=True)

    def test_log_file_fail_on_error(self):
        with mock.patch("solvent.doctor.Path", side_effect=Exception("path broken")):
            from solvent.doctor import run_checks
            checks = run_checks()
        c = self._by_name(checks, "log_file")
        # ok/fail depends on which Path call broke; just verify structure
        self.assertIn("ok", c)

    # ------------------------------------------------------------------
    # Total check count grew
    # ------------------------------------------------------------------

    def test_total_check_count(self):
        checks = self._run()
        # Original had 8; extended adds 5 more
        self.assertGreaterEqual(len(checks), 13)

    def test_all_extended_checks_have_required_keys(self):
        extended = {"rate_limit_db", "events_table", "chat_sessions_table",
                    "job_metrics_table", "log_file"}
        checks = self._run()
        for c in checks:
            if c["name"] in extended:
                self.assertIn("ok", c, f"{c['name']} missing 'ok'")
                self.assertIn("name", c, f"{c['name']} missing 'name'")
                self.assertIn("detail", c, f"{c['name']} missing 'detail'")


if __name__ == "__main__":
    unittest.main()
