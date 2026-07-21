"""Tests for the SQLite-backed job queue helpers in solvent/queue.py."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from solvent.queue import list_claimable, resume_incomplete_jobs
from solvent.treasury import Treasury


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_treasury() -> Treasury:
    fd, path = tempfile.mkstemp(suffix=".db", prefix="solvent_queue_")
    import os
    os.close(fd)
    return Treasury(path=Path(path))


def _insert_job_stage(t: Treasury, job_id: str, stage: str) -> None:
    """Insert a completed job_stage row directly via SQLite."""
    stage_id = f"st_{job_id}_{stage}"
    ikey = f"ikey_{stage_id}"
    with t.lock():
        with t._conn() as conn:
            with conn:
                conn.execute(
                    "INSERT INTO job_stages (id, job_id, stage, idempotency_key, status, payload_json, result_json, ts) "
                    "VALUES (?, ?, ?, ?, 'completed', '{}', '{}', ?)",
                    (stage_id, job_id, stage, ikey, time.time()),
                )


def _set_checkout(t: Treasury, job_id: str, url: str = "https://pay.example/x") -> None:
    with t.lock():
        with t._conn() as conn:
            with conn:
                conn.execute(
                    "INSERT INTO stripe_checkout (job_id, session_id, checkout_url, status, ts) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (job_id, f"sess_{job_id}", url, "open", time.time()),
                )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestListClaimable(unittest.TestCase):
    """Only jobs in worker-relevant statuses should be returned."""

    def test_returns_all_worker_statuses(self):
        t = _fresh_treasury()
        ids = ["j_await", "j_progress", "j_paid", "j_pending_q"]
        for jid, status in zip(ids, ("awaiting_payment", "in_progress", "paid_pending_fulfill", "pending_quote")):
            t.upsert_job(jid, status)

        claimable = list_claimable(t)
        found = {row["id"] for row in claimable}
        self.assertEqual(found, set(ids))

    def test_excludes_completed_and_failed(self):
        t = _fresh_treasury()
        t.upsert_job("j_ok", "completed")
        t.upsert_job("j_bad", "failed")

        claimable = list_claimable(t)
        self.assertEqual(claimable, [])


class TestResumeIncompleteJobs(unittest.TestCase):
    """resume_incomplete_jobs must identify and resume resumable jobs."""

    def test_skips_completed(self):
        t = _fresh_treasury()
        t.upsert_job("j_done", "completed")
        resumed = resume_incomplete_jobs(t)
        self.assertEqual(resumed, [])

    def test_skips_failed(self):
        t = _fresh_treasury()
        t.upsert_job("j_fail", "failed", error_reason="oops")
        resumed = resume_incomplete_jobs(t)
        self.assertEqual(resumed, [])

    def test_resumes_job_with_revenue_no_fulfill_stage(self):
        t = _fresh_treasury()
        t.upsert_job("j_revenue", "awaiting_payment")
        t.earn(5000, "Test payment", job_id="j_revenue")
        # No fulfill stage inserted

        resumed = resume_incomplete_jobs(t)
        self.assertIn("j_revenue", resumed)

        row = t.get_job("j_revenue")
        self.assertEqual(row["status"], "paid_pending_fulfill")

    def test_resumes_in_progress_without_fulfill_stage(self):
        t = _fresh_treasury()
        t.upsert_job("j_ip", "in_progress")
        resumed = resume_incomplete_jobs(t)
        self.assertIn("j_ip", resumed)

        row = t.get_job("j_ip")
        self.assertEqual(row["status"], "in_progress")  # status unchanged

    def test_resumes_awaiting_payment_with_checkout(self):
        t = _fresh_treasury()
        t.upsert_job("j_checkout", "awaiting_payment")
        _set_checkout(t, "j_checkout")
        resumed = resume_incomplete_jobs(t)
        self.assertIn("j_checkout", resumed)

    def test_does_not_resume_job_with_completed_fulfill_stage(self):
        t = _fresh_treasury()
        t.upsert_job("j_done_fulfill", "awaiting_payment")
        t.earn(5000, "Paid", job_id="j_done_fulfill")
        _insert_job_stage(t, "j_done_fulfill", "fulfill")

        resumed = resume_incomplete_jobs(t)
        self.assertNotIn("j_done_fulfill", resumed)

        row = t.get_job("j_done_fulfill")
        self.assertEqual(row["status"], "awaiting_payment")  # unchanged

    def test_does_not_resume_job_with_no_revenue_and_no_checkout(self):
        t = _fresh_treasury()
        t.upsert_job("j_lost", "awaiting_payment")
        resumed = resume_incomplete_jobs(t)
        self.assertNotIn("j_lost", resumed)


if __name__ == "__main__":
    unittest.main()
