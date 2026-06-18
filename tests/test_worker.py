"""Tests for worker resume logic."""

import json
import tempfile
import unittest
from pathlib import Path

from solvent.queue import resume_incomplete_jobs
from solvent.treasury import Treasury


class TestWorkerResume(unittest.TestCase):
    def test_resume_paid_unfulfilled(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "w.db"
            t = Treasury(path=db)
            t.reset()
            t.seed(10_000)
            t.upsert_job("J-stuck", "in_progress", topic="t", budget_cents=5000, job_payload_json={"id": "J-stuck"})
            t.earn(5000, "paid", job_id="J-stuck", stripe_ref="pi_x")
            resumed = resume_incomplete_jobs(t)
            self.assertIn("J-stuck", resumed)
            job = t.get_job("J-stuck")
            self.assertEqual(job["status"], "paid_pending_fulfill")


if __name__ == "__main__":
    unittest.main()
