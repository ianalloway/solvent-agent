"""tests/test_job_cmd.py — unit tests for the solvent jobs CLI."""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

from solvent.treasury import Treasury
from solvent.job_cmd import (
    cmd_list, cmd_show, cmd_events, cmd_cancel,
    _fmt_cents, _ago, _fmt_ts, _col,
)


def _fresh_treasury():
    return Treasury(path=f"/tmp/solvent_jobcmd_{id(object())}.db")


def _seed(t: Treasury, n: int = 3) -> list[str]:
    """Insert n jobs and return their IDs."""
    ids = []
    statuses = ["awaiting_payment", "in_progress", "completed", "failed"]
    for i in range(n):
        jid = f"job_{i:03d}"
        t.upsert_job(jid, statuses[i % len(statuses)], topic=f"Topic {i}", budget_cents=(i + 1) * 1000)
        ids.append(jid)
    return ids


class TestHelpers(unittest.TestCase):

    def test_fmt_cents_basic(self):
        self.assertEqual(_fmt_cents(500), "$5.00")

    def test_fmt_cents_none(self):
        self.assertEqual(_fmt_cents(None), "—")

    def test_fmt_ts_none(self):
        self.assertEqual(_fmt_ts(None), "—")

    def test_fmt_ts_valid(self):
        import time
        result = _fmt_ts(time.time())
        self.assertRegex(result, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")

    def test_ago_seconds(self):
        import time
        self.assertIn("s", _ago(time.time() - 10))

    def test_ago_none(self):
        self.assertEqual(_ago(None), "")

    def test_col_truncates(self):
        self.assertEqual(len(_col("A" * 30, 10)), 10)
        self.assertTrue(_col("A" * 30, 10).endswith("…"))

    def test_col_pads(self):
        self.assertEqual(len(_col("hi", 10)), 10)


class TestCmdList(unittest.TestCase):

    def test_empty_treasury_prints_no_jobs(self):
        t = _fresh_treasury()
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_list(t)
        self.assertIn("No jobs", buf.getvalue())

    def test_lists_jobs(self):
        t = _fresh_treasury()
        _seed(t, 3)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_list(t)
        out = buf.getvalue()
        self.assertIn("job_000", out)
        self.assertIn("Topic 0", out)

    def test_status_filter(self):
        t = _fresh_treasury()
        _seed(t, 4)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_list(t, status_filter="completed")
        out = buf.getvalue()
        # Only completed jobs should appear
        self.assertNotIn("awaiting_payment", out)

    def test_json_output_is_list(self):
        t = _fresh_treasury()
        _seed(t, 2)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_list(t, as_json=True)
        data = json.loads(buf.getvalue())
        self.assertIsInstance(data, list)

    def test_limit_respected(self):
        t = _fresh_treasury()
        _seed(t, 10)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_list(t, limit=3)
        # Should not contain all 10 job IDs in output
        out = buf.getvalue()
        # Count distinct job_ occurrences
        count = sum(1 for i in range(10) if f"job_{i:03d}" in out)
        self.assertLessEqual(count, 3)

    def test_no_jobs_with_status_filter_message(self):
        t = _fresh_treasury()
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_list(t, status_filter="failed")
        self.assertIn("failed", buf.getvalue())


class TestCmdShow(unittest.TestCase):

    def test_show_missing_job_exits(self):
        t = _fresh_treasury()
        with self.assertRaises(SystemExit):
            cmd_show(t, "nonexistent_id")

    def test_show_existing_job(self):
        t = _fresh_treasury()
        t.upsert_job("job_show", "completed", topic="My topic", budget_cents=5000)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_show(t, "job_show")
        out = buf.getvalue()
        self.assertIn("job_show", out)
        self.assertIn("My topic", out)
        self.assertIn("completed", out)

    def test_show_json(self):
        t = _fresh_treasury()
        t.upsert_job("job_json", "in_progress", topic="JSON topic", budget_cents=1000)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_show(t, "job_json", as_json=True)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["id"], "job_json")
        self.assertIn("pnl_cents", data)


class TestCmdEvents(unittest.TestCase):

    def test_events_missing_job_exits(self):
        t = _fresh_treasury()
        with self.assertRaises(SystemExit):
            cmd_events(t, "nope")

    def test_events_no_events_message(self):
        t = _fresh_treasury()
        t.upsert_job("ev_job", "awaiting_payment")
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_events(t, "ev_job")
        self.assertIn("No events", buf.getvalue())

    def test_events_lists_recorded(self):
        t = _fresh_treasury()
        t.upsert_job("ev_job2", "awaiting_payment")
        t.record_event("ev_job2", "quote", {"amount": 500})
        t.record_event("ev_job2", "paid", {})
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_events(t, "ev_job2")
        out = buf.getvalue()
        self.assertIn("quote", out)
        self.assertIn("paid", out)

    def test_events_json(self):
        t = _fresh_treasury()
        t.upsert_job("ev_j3", "in_progress")
        t.record_event("ev_j3", "started", {})
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_events(t, "ev_j3", as_json=True)
        data = json.loads(buf.getvalue())
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)


class TestCmdCancel(unittest.TestCase):

    def test_cancel_missing_exits(self):
        t = _fresh_treasury()
        with self.assertRaises(SystemExit):
            cmd_cancel(t, "nope")

    def test_cancel_sets_status(self):
        t = _fresh_treasury()
        t.upsert_job("cancel_me", "in_progress")
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_cancel(t, "cancel_me")
        job = t.get_job("cancel_me")
        self.assertEqual(job["status"], "cancelled")
        self.assertIn("cancelled", buf.getvalue())

    def test_cancel_already_done_is_noop(self):
        t = _fresh_treasury()
        t.upsert_job("done_job", "completed")
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_cancel(t, "done_job")
        job = t.get_job("done_job")
        self.assertEqual(job["status"], "completed")
        self.assertIn("already", buf.getvalue())


class TestJobsCLI(unittest.TestCase):

    def test_main_list_no_args(self):
        from solvent.job_cmd import main
        buf = io.StringIO()
        with mock.patch("solvent.job_cmd.cmd_list") as mock_list, \
             mock.patch.object(sys, "argv", ["solvent-jobs"]):
            # cmd_list is called; it should receive a Treasury and no filter
            mock_list.side_effect = lambda t, **kw: print("No jobs", file=sys.stdout)
            with redirect_stdout(buf):
                main()
        mock_list.assert_called_once()

    def test_main_list_subcommand(self):
        from solvent.job_cmd import main
        buf = io.StringIO()
        with mock.patch("solvent.job_cmd.cmd_list") as mock_list, \
             mock.patch.object(sys, "argv", ["solvent-jobs", "list"]):
            mock_list.side_effect = lambda t, **kw: print("No jobs", file=sys.stdout)
            with redirect_stdout(buf):
                main()
        mock_list.assert_called_once()


if __name__ == "__main__":
    unittest.main()
