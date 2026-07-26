"""tests/test_status.py — unit tests for solvent status command."""

import io
import json
import os
import sys
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from solvent.status import _fmt_cents, _time_ago, format_status, gather


class TestHelpers(unittest.TestCase):
    def test_fmt_cents_whole_dollar(self):
        self.assertEqual(_fmt_cents(100), "$1.00")

    def test_fmt_cents_large(self):
        self.assertEqual(_fmt_cents(123456), "$1,234.56")

    def test_fmt_cents_zero(self):
        self.assertEqual(_fmt_cents(0), "$0.00")

    def test_time_ago_seconds(self):
        result = _time_ago(time.time() - 30)
        self.assertIn("s ago", result)

    def test_time_ago_minutes(self):
        result = _time_ago(time.time() - 120)
        self.assertIn("m ago", result)

    def test_time_ago_hours(self):
        result = _time_ago(time.time() - 7200)
        self.assertIn("h ago", result)

    def test_time_ago_days(self):
        result = _time_ago(time.time() - 172800)
        self.assertIn("d ago", result)


class TestGather(unittest.TestCase):
    def _make_treasury(self):
        from solvent.treasury import Treasury

        return Treasury(path=f"/tmp/solvent_status_test_{id(self)}.db")

    def test_gather_returns_dict(self):
        t = self._make_treasury()
        data = gather(treasury=t)
        self.assertIsInstance(data, dict)

    def test_gather_has_required_keys(self):
        t = self._make_treasury()
        data = gather(treasury=t)
        for key in (
            "timestamp",
            "balance_cents",
            "revenue_cents",
            "total_jobs",
            "status_counts",
            "last_job",
            "api_keys",
        ):
            self.assertIn(key, data)

    def test_gather_balance_is_int(self):
        t = self._make_treasury()
        data = gather(treasury=t)
        self.assertIsInstance(data["balance_cents"], int)

    def test_gather_total_jobs_zero_on_empty(self):
        t = self._make_treasury()
        data = gather(treasury=t)
        self.assertEqual(data["total_jobs"], 0)
        self.assertIsNone(data["last_job"])

    def test_gather_api_keys_dict(self):
        t = self._make_treasury()
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-test"}):
            data = gather(treasury=t)
        self.assertTrue(data["api_keys"]["nvidia"])
        self.assertFalse(data["api_keys"]["stripe"])

    def test_gather_counts_jobs_by_status(self):
        t = self._make_treasury()
        t.upsert_job("job_a", "awaiting_payment", topic="Topic A", budget_cents=1000)
        t.upsert_job("job_b", "completed", topic="Topic B", budget_cents=2000)
        data = gather(treasury=t)
        self.assertEqual(data["total_jobs"], 2)
        self.assertIn("awaiting_payment", data["status_counts"])
        self.assertIn("completed", data["status_counts"])

    def test_gather_last_job_present_after_create(self):
        t = self._make_treasury()
        t.upsert_job("job_x", "awaiting_payment", topic="Test topic", budget_cents=500)
        data = gather(treasury=t)
        self.assertIsNotNone(data["last_job"])
        self.assertIn("Test topic", data["last_job"]["topic"])


class TestFormatStatus(unittest.TestCase):
    def _sample_data(self, **overrides) -> dict:
        base = {
            "timestamp": "2026-01-01T12:00:00+00:00",
            "balance_cents": 5000,
            "revenue_cents": 10000,
            "net_profit_cents": 5000,
            "margin_pct": 50.0,
            "total_jobs": 3,
            "status_counts": {"completed": 2, "awaiting_payment": 1},
            "last_job": {
                "id": "job_001",
                "topic": "Nvidia earnings brief",
                "status": "completed",
                "updated_at": time.time() - 300,
            },
            "last_event_ts": time.time() - 60,
            "api_keys": {"nvidia": True, "stripe": False, "telegram": False},
        }
        base.update(overrides)
        return base

    def test_format_contains_balance(self):
        out = format_status(self._sample_data())
        self.assertIn("$50.00", out)

    def test_format_contains_revenue(self):
        out = format_status(self._sample_data())
        self.assertIn("$100.00", out)

    def test_format_contains_margin(self):
        out = format_status(self._sample_data())
        self.assertIn("50.0%", out)

    def test_format_contains_job_count(self):
        out = format_status(self._sample_data())
        self.assertIn("3 total", out)

    def test_format_contains_last_job_topic(self):
        out = format_status(self._sample_data())
        self.assertIn("Nvidia earnings brief", out)

    def test_format_shows_api_key_presence(self):
        out = format_status(self._sample_data())
        self.assertIn("nvidia ✓", out)
        self.assertIn("stripe ✗", out)

    def test_format_handles_no_jobs(self):
        data = self._sample_data(total_jobs=0, status_counts={}, last_job=None)
        out = format_status(data)
        self.assertIn("0 total", out)

    def test_format_handles_no_events(self):
        data = self._sample_data(last_event_ts=None)
        out = format_status(data)
        self.assertIsInstance(out, str)


class TestStatusCLI(unittest.TestCase):
    def _make_treasury(self):
        from solvent.treasury import Treasury

        return Treasury(path=f"/tmp/solvent_status_cli_test_{id(self)}.db")

    def test_json_output_is_valid(self):
        from solvent.status import main

        buf = io.StringIO()
        with (
            mock.patch(
                "solvent.status.gather",
                return_value={
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "balance_cents": 0,
                    "revenue_cents": 0,
                    "net_profit_cents": 0,
                    "margin_pct": 0.0,
                    "total_jobs": 0,
                    "status_counts": {},
                    "last_job": None,
                    "last_event_ts": None,
                    "api_keys": {},
                },
            ),
            mock.patch.object(sys, "argv", ["solvent-status", "--json"]),
            redirect_stdout(buf),
        ):
            main()
        data = json.loads(buf.getvalue())
        self.assertIn("balance_cents", data)

    def test_human_output_contains_status_header(self):
        from solvent.status import main

        buf = io.StringIO()
        with (
            mock.patch(
                "solvent.status.gather",
                return_value={
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "balance_cents": 100,
                    "revenue_cents": 200,
                    "net_profit_cents": 100,
                    "margin_pct": 50.0,
                    "total_jobs": 0,
                    "status_counts": {},
                    "last_job": None,
                    "last_event_ts": None,
                    "api_keys": {"nvidia": False, "stripe": False, "telegram": False},
                },
            ),
            mock.patch.object(sys, "argv", ["solvent-status"]),
            redirect_stdout(buf),
        ):
            main()
        self.assertIn("SOLVENT status", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
