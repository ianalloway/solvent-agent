"""Tests for the hosted HTTP surface."""

import os
import unittest
from unittest import mock

from solvent.delivery import make_delivery_token
from solvent.server import create_app
from solvent.paths import reports_dir


class TestHostedBriefs(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(
            os.environ,
            {
                "SOLVENT_DELIVERY_SECRET": "x" * 32,
                "SOLVENT_FORCE_STRIPE_SIMULATE": "1",
            },
            clear=False,
        )
        self._env.start()
        self.reports_dir = reports_dir()
        self.report_path = self.reports_dir / "victim-job.md"
        self.report_path.write_text("# Private brief", encoding="utf-8")

    def tearDown(self):
        self.report_path.unlink(missing_ok=True)
        self._env.stop()

    def _client(self, *, fresh: bool = True):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI test client is not installed")
        return TestClient(create_app(fresh=fresh))

    def test_brief_serving_requires_exact_job_id_match(self):
        client = self._client()
        response = client.get(f"/briefs/victim?token={make_delivery_token('victim')}")
        self.assertEqual(response.status_code, 404)

        response = client.get(
            f"/briefs/victim-job?token={make_delivery_token('victim-job')}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Private brief", response.text)

    def test_interactive_dashboard_routes(self):
        client = self._client()
        self.assertEqual(client.get("/health").status_code, 200)

        dashboard = client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Agent Chat", dashboard.text)

        status = client.get("/api/status")
        self.assertEqual(status.status_code, 200)
        self.assertIn("balance_cents", status.json())

    def test_api_chat_status_command(self):
        client = self._client()
        response = client.post("/api/chat", json={"message": "/status"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Balance", response.json()["reply"])

    def test_job_submission_publishes_once_without_event_recursion(self):
        client = self._client()
        response = client.post(
            "/jobs",
            json={
                "id": "J-server-event",
                "topic": "Server event wiring",
                "budget_cents": 5000,
                "customer_email": "server@test.example",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("session_id", response.json())

        stored = client.get("/jobs/J-server-event")
        self.assertEqual(stored.status_code, 200)
        self.assertEqual(stored.json()["job"]["status"], "awaiting_payment")

        agent = client.app.state.agent
        stages = [event.get("stage") for event in agent.log]
        self.assertEqual(stages.count("quote"), 1)
        self.assertEqual(stages.count("invoice"), 1)

    def test_pairing_endpoint_returns_png_when_qr_extra_is_installed(self):
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest("QR extra is not installed")

        response = self._client().get("/api/pair/qr")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
