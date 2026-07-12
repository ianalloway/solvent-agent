"""Unit tests for the solvent jobs CLI."""

import io
import json
import sys
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from solvent.job_cmd import (
    _ago,
    _col,
    _fmt_cents,
    _fmt_ts,
    cmd_cancel,
    cmd_events,
    cmd_list,
    cmd_retry,
    cmd_show,
)
from solvent.treasury import Treasury


def _fresh_treasury():
    return Treasury(path=f"/tmp/solvent_jobcmd_{uuid.uuid4().hex}.db")


def _seed(treasury: Treasury, count: int = 3) -> list[str]:
    ids = []
    statuses = ["awaiting_payment", "in_progress", "completed", "failed"]
    for index in range(count):
        job_id = f"job_{index:03d}"
        treasury.upsert_job(
            job_id,
            statuses[index % len(statuses)],
            topic=f"Topic {index}",
            budget_cents=(index + 1) * 1000,
        )
        ids.append(job_id)
    return ids


class TestHelpers(unittest.TestCase):
    def test_fmt_cents(self):
        self.assertEqual(_fmt_cents(500), "$5.00")
        self.assertEqual(_fmt_cents(None), "—")

    def test_fmt_ts(self):
        import time

        self.assertEqual(_fmt_ts(None), "—")
        self.assertRegex(_fmt_ts(time.time()), r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")

    def test_ago(self):
        import time

        self.assertIn("s", _ago(time.time() - 10))
        self.assertEqual(_ago(None), "")

    def test_col(self):
        self.assertEqual(len(_col("hi", 10)), 10)
        self.assertEqual(len(_col("A" * 30, 10)), 10)
        self.assertTrue(_col("A" * 30, 10).endswith("…"))


class TestCmdList(unittest.TestCase):
    def test_empty_treasury_prints_no_jobs(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cmd_list(_fresh_treasury())
        self.assertIn("No jobs", buffer.getvalue())

    def test_lists_jobs(self):
        treasury = _fresh_treasury()
        _seed(treasury, 3)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cmd_list(treasury)
        self.assertIn("job_000", buffer.getvalue())
        self.assertIn("Topic 0", buffer.getvalue())

    def test_status_filter(self):
        treasury = _fresh_treasury()
        _seed(treasury, 4)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cmd_list(treasury, status_filter="completed")
        self.assertNotIn("awaiting_payment", buffer.getvalue())

    def test_json_output_is_list(self):
        treasury = _fresh_treasury()
        _seed(treasury, 2)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cmd_list(treasury, as_json=True)
        self.assertIsInstance(json.loads(buffer.getvalue()), list)

    def test_limit_respected(self):
        treasury = _fresh_treasury()
        _seed(treasury, 10)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cmd_list(treasury, limit=3)
        output = buffer.getvalue()
        count = sum(1 for index in range(10) if f"job_{index:03d}" in output)
        self.assertLessEqual(count, 3)


class TestCmdShow(unittest.TestCase):
    def test_show_missing_job_exits(self):
        with self.assertRaises(SystemExit):
            cmd_show(_fresh_treasury(), "missing")

    def test_show_existing_job(self):
        treasury = _fresh_treasury()
        treasury.upsert_job("job_show", "completed", topic="My topic", budget_cents=5000)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cmd_show(treasury, "job_show")
        output = buffer.getvalue()
        self.assertIn("job_show", output)
        self.assertIn("My topic", output)
        self.assertIn("completed", output)

    def test_show_json(self):
        treasury = _fresh_treasury()
        treasury.upsert_job("job_json", "in_progress", topic="JSON topic", budget_cents=1000)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cmd_show(treasury, "job_json", as_json=True)
        data = json.loads(buffer.getvalue())
        self.assertEqual(data["id"], "job_json")
        self.assertIn("pnl_cents", data)


class TestCmdEvents(unittest.TestCase):
    def test_events_missing_job_exits(self):
        with self.assertRaises(SystemExit):
            cmd_events(_fresh_treasury(), "missing")

    def test_events_lists_recorded_events(self):
        treasury = _fresh_treasury()
        treasury.upsert_job("events", "awaiting_payment")
        treasury.record_event("events", "quote", {"amount": 500})
        treasury.record_event("events", "paid", {})
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cmd_events(treasury, "events")
        self.assertIn("quote", buffer.getvalue())
        self.assertIn("paid", buffer.getvalue())

    def test_events_json(self):
        treasury = _fresh_treasury()
        treasury.upsert_job("events", "in_progress")
        treasury.record_event("events", "started", {})
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cmd_events(treasury, "events", as_json=True)
        self.assertIsInstance(json.loads(buffer.getvalue()), list)


class TestCmdRetry(unittest.TestCase):
    def test_retry_uses_supplied_runner_and_prints_json(self):
        runner = mock.Mock()
        runner.retry_job.return_value = {"stage": "booked", "status": "completed"}
        buffer = io.StringIO()

        with redirect_stdout(buffer):
            result = cmd_retry(_fresh_treasury(), "job_retry", runner=runner)

        runner.retry_job.assert_called_once_with("job_retry")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(json.loads(buffer.getvalue())["stage"], "booked")

    def test_retry_user_error_exits_cleanly(self):
        runner = mock.Mock()
        runner.retry_job.side_effect = ValueError("not retryable")
        stderr = io.StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as context:
            cmd_retry(_fresh_treasury(), "job_retry", runner=runner)

        self.assertEqual(context.exception.code, 1)
        self.assertIn("not retryable", stderr.getvalue())


class TestCmdCancel(unittest.TestCase):
    def test_cancel_sets_status(self):
        treasury = _fresh_treasury()
        treasury.upsert_job("cancel_me", "in_progress")
        with redirect_stdout(io.StringIO()):
            cmd_cancel(treasury, "cancel_me")
        self.assertEqual(treasury.get_job("cancel_me")["status"], "cancelled")

    def test_cancel_completed_job_is_noop(self):
        treasury = _fresh_treasury()
        treasury.upsert_job("done", "completed")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cmd_cancel(treasury, "done")
        self.assertEqual(treasury.get_job("done")["status"], "completed")
        self.assertIn("already", buffer.getvalue())


class TestJobsCLI(unittest.TestCase):
    def test_main_defaults_to_list(self):
        from solvent.job_cmd import main

        with (
            mock.patch("solvent.job_cmd.cmd_list") as command,
            mock.patch.object(sys, "argv", ["solvent-jobs"]),
        ):
            main()
        command.assert_called_once()

    def test_main_routes_retry(self):
        from solvent.job_cmd import main

        with (
            mock.patch("solvent.job_cmd.cmd_retry") as command,
            mock.patch.object(sys, "argv", ["solvent-jobs", "retry", "J-1"]),
        ):
            main()
        command.assert_called_once()
        self.assertEqual(command.call_args.args[1], "J-1")


if __name__ == "__main__":
    unittest.main()
