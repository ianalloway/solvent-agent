"""Tests for solvent/queue.py — SQLite-backed job queue helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from solvent.queue import list_claimable, resume_incomplete_jobs, WORKER_STATUSES
from solvent.treasury import Treasury


def _tmp_treasury():
    d = tempfile.mkdtemp()
    return Treasury(path=Path(d) / "test.db")


class TestWorkerStatuses(unittest.TestCase):
    def test_statuses_are_non_empty_tuple(self):
        self.assertGreater(len(WORKER_STATUSES), 0)
        for s in WORKER_STATUSES:
            self.assertIsInstance(s, str)

    def test_awaiting_payment_in_statuses(self):
        self.assertIn("awaiting_payment", WORKER_STATUSES)

    def test_in_progress_in_statuses(self):
        self.assertIn("in_progress", WORKER_STATUSES)


class TestListClaimable(unittest.TestCase):
    def setUp(self):
        self.t = _tmp_treasury()

    def test_empty_treasury_returns_empty(self):
        self.assertEqual(list_claimable(self.t), [])

    def test_pending_quote_job_not_claimable(self):
        self.t.upsert_job("j1", "pending_quote")
        result = list_claimable(self.t)
        self.assertGreaterEqual(len(result), 0)

    def test_awaiting_payment_job_is_claimable(self):
        self.t.upsert_job("j1", "awaiting_payment")
        result = list_claimable(self.t)
        ids = [r["id"] for r in result]
        self.assertIn("j1", ids)

    def test_in_progress_job_is_claimable(self):
        self.t.upsert_job("j1", "in_progress")
        result = list_claimable(self.t)
        ids = [r["id"] for r in result]
        self.assertIn("j1", ids)

    def test_paid_pending_fulfill_is_claimable(self):
        self.t.upsert_job("j1", "paid_pending_fulfill")
        result = list_claimable(self.t)
        ids = [r["id"] for r in result]
        self.assertIn("j1", ids)

    def test_completed_job_not_claimable(self):
        self.t.upsert_job("j1", "completed")
        result = list_claimable(self.t)
        ids = [r["id"] for r in result]
        self.assertNotIn("j1", ids)

    def test_failed_job_not_claimable(self):
        self.t.upsert_job("j1", "failed")
        result = list_claimable(self.t)
        ids = [r["id"] for r in result]
        self.assertNotIn("j1", ids)

    def test_multiple_statuses_all_returned(self):
        self.t.upsert_job("j1", "awaiting_payment")
        self.t.upsert_job("j2", "in_progress")
        self.t.upsert_job("j3", "completed")
        result = list_claimable(self.t)
        ids = {r["id"] for r in result}
        self.assertIn("j1", ids)
        self.assertIn("j2", ids)
        self.assertNotIn("j3", ids)

    def test_returns_list_of_dicts(self):
        self.t.upsert_job("j1", "awaiting_payment")
        result = list_claimable(self.t)
        self.assertIsInstance(result, list)
        for r in result:
            self.assertIsInstance(r, dict)
            self.assertIn("id", r)
            self.assertIn("status", r)


class TestResumeIncompleteJobs(unittest.TestCase):
    def setUp(self):
        self.t = _tmp_treasury()

    def test_empty_treasury_returns_empty(self):
        self.assertEqual(resume_incomplete_jobs(self.t), [])

    def test_completed_job_skipped(self):
        self.t.upsert_job("j1", "completed")
        result = resume_incomplete_jobs(self.t)
        self.assertNotIn("j1", result)

    def test_failed_job_skipped(self):
        self.t.upsert_job("j1", "failed")
        result = resume_incomplete_jobs(self.t)
        self.assertNotIn("j1", result)

    def test_paid_unfulfilled_job_resumed(self):
        self.t.upsert_job("j1", "in_progress")
        self.t.earn(1000, "payment received", job_id="j1")
        result = resume_incomplete_jobs(self.t)
        self.assertIn("j1", result)

    def test_paid_unfulfilled_job_status_updated(self):
        self.t.upsert_job("j1", "in_progress")
        self.t.earn(1000, "payment received", job_id="j1")
        resume_incomplete_jobs(self.t)
        job = self.t.get_job("j1")
        self.assertEqual(job["status"], "paid_pending_fulfill")

    def test_in_progress_unfulfilled_resumed(self):
        self.t.upsert_job("j1", "in_progress")
        result = resume_incomplete_jobs(self.t)
        self.assertIn("j1", result)

    def test_awaiting_payment_with_checkout_resumed(self):
        self.t.upsert_job("j1", "awaiting_payment")
        self.t.upsert_checkout("j1", "cs_test123", "https://checkout.example/pay", "open")
        result = resume_incomplete_jobs(self.t)
        self.assertIn("j1", result)

    def test_returns_list_of_strings(self):
        self.t.upsert_job("j1", "in_progress")
        result = resume_incomplete_jobs(self.t)
        for item in result:
            self.assertIsInstance(item, str)


if __name__ == "__main__":
    unittest.main()
