"""Tests for hosted brief serving."""

import os
import unittest
from pathlib import Path
from unittest import mock

from solvent.delivery import make_delivery_token
from solvent.server import create_app


class TestHostedBriefs(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"SOLVENT_DELIVERY_SECRET": "x" * 32}, clear=False)
        self._env.start()
        self.reports_dir = Path(__file__).resolve().parent.parent / "data" / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.report_path = self.reports_dir / "victim-job.md"
        self.report_path.write_text("# Private brief", encoding="utf-8")

    def tearDown(self):
        self.report_path.unlink(missing_ok=True)
        self._env.stop()

    def test_brief_serving_requires_exact_job_id_match(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI test client is not installed")

        client = TestClient(create_app())
        token = make_delivery_token("victim")
        response = client.get(f"/briefs/victim?token={token}")
        self.assertEqual(response.status_code, 404)

        token = make_delivery_token("victim-job")
        response = client.get(f"/briefs/victim-job?token={token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Private brief", response.text)


if __name__ == "__main__":
    unittest.main()
