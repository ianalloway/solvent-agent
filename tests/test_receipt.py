"""Tests for receipt.py and the /api/receipt/{job_id} HTTP endpoint."""

from __future__ import annotations

import os
import unittest
from unittest import mock


# ---------------------------------------------------------------------------
# Receipt builder unit tests
# ---------------------------------------------------------------------------

class TestBuildReceipt(unittest.TestCase):
    def _make_job(self, **overrides):
        base = {
            "id": "Jabc12345678",
            "topic": "AI trends 2025",
            "status": "completed",
            "budget_cents": 5000,
            "revenue_cents": 5000,
        }
        return {**base, **overrides}

    def test_contains_job_id(self):
        from solvent.receipt import build_receipt
        job = self._make_job()
        receipt = build_receipt(job, pnl_cents=2000, balance_cents=12000)
        self.assertIn("Jabc1234", receipt)

    def test_contains_full_job_id_in_ref_line(self):
        from solvent.receipt import build_receipt
        job = self._make_job()
        receipt = build_receipt(job, pnl_cents=2000, balance_cents=12000)
        self.assertIn("Jabc12345678", receipt)

    def test_contains_topic(self):
        from solvent.receipt import build_receipt
        job = self._make_job()
        receipt = build_receipt(job, pnl_cents=2000, balance_cents=12000)
        self.assertIn("AI trends 2025", receipt)

    def test_formatted_dollar_amounts(self):
        from solvent.receipt import build_receipt
        job = self._make_job(revenue_cents=5000)
        receipt = build_receipt(job, pnl_cents=2000, balance_cents=12000)
        # Revenue
        self.assertIn("$50.00", receipt)
        # P&L
        self.assertIn("$20.00", receipt)
        # Balance
        self.assertIn("$120.00", receipt)

    def test_status_present(self):
        from solvent.receipt import build_receipt
        job = self._make_job(status="completed")
        receipt = build_receipt(job, pnl_cents=500, balance_cents=8000)
        self.assertIn("completed", receipt)

    def test_zero_revenue_does_not_crash(self):
        from solvent.receipt import build_receipt
        job = self._make_job(revenue_cents=0, budget_cents=0)
        receipt = build_receipt(job, pnl_cents=0, balance_cents=0)
        self.assertIn("$0.00", receipt)

    def test_negative_pnl(self):
        from solvent.receipt import build_receipt
        job = self._make_job(revenue_cents=1000)
        receipt = build_receipt(job, pnl_cents=-200, balance_cents=5000)
        self.assertIn("$-2.00", receipt)


# ---------------------------------------------------------------------------
# Refund receipt tests
# ---------------------------------------------------------------------------

class TestBuildRefundReceipt(unittest.TestCase):
    def test_mentions_refund(self):
        from solvent.receipt import build_refund_receipt
        job = {"id": "Jrefund001", "topic": "Market analysis", "status": "failed"}
        receipt = build_refund_receipt(job, refund_cents=3000)
        self.assertIn("Refund", receipt)

    def test_refund_amount_formatted(self):
        from solvent.receipt import build_refund_receipt
        job = {"id": "Jrefund001", "topic": "Market analysis"}
        receipt = build_refund_receipt(job, refund_cents=3000)
        self.assertIn("$30.00", receipt)

    def test_contains_job_id(self):
        from solvent.receipt import build_refund_receipt
        job = {"id": "Jrefund001", "topic": "Market analysis"}
        receipt = build_refund_receipt(job, refund_cents=3000)
        self.assertIn("Jrefund001", receipt)

    def test_contains_reason_if_present(self):
        from solvent.receipt import build_refund_receipt
        job = {"id": "J001", "topic": "T", "error_reason": "guardrail triggered"}
        receipt = build_refund_receipt(job, refund_cents=500)
        self.assertIn("guardrail triggered", receipt)


# ---------------------------------------------------------------------------
# HTML receipt tests
# ---------------------------------------------------------------------------

class TestBuildHtmlReceipt(unittest.TestCase):
    def test_is_html(self):
        from solvent.receipt import build_html_receipt
        job = {
            "id": "Jhtml001234",
            "topic": "Quantum computing",
            "status": "completed",
            "revenue_cents": 4000,
        }
        html = build_html_receipt(job, pnl_cents=1500, balance_cents=9000)
        self.assertIn("<html>", html)

    def test_contains_dollar_amounts(self):
        from solvent.receipt import build_html_receipt
        job = {
            "id": "Jhtml001234",
            "topic": "Quantum computing",
            "status": "completed",
            "revenue_cents": 4000,
        }
        html = build_html_receipt(job, pnl_cents=1500, balance_cents=9000)
        self.assertIn("$40.00", html)
        self.assertIn("$15.00", html)
        self.assertIn("$90.00", html)

    def test_contains_job_id(self):
        from solvent.receipt import build_html_receipt
        job = {"id": "JhtmlABC", "topic": "Topic", "status": "completed", "revenue_cents": 0}
        html = build_html_receipt(job, pnl_cents=0, balance_cents=0)
        self.assertIn("JhtmlABC", html)


# ---------------------------------------------------------------------------
# Server endpoint tests
# ---------------------------------------------------------------------------

class TestReceiptEndpoint(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(
            os.environ,
            {"SOLVENT_DELIVERY_SECRET": "x" * 32},
            clear=False,
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def _make_client(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI test client is not installed")
        from solvent.server import create_app
        return TestClient(create_app(fresh=True))

    def _seed_job(self, client, job_id: str) -> None:
        """Insert a minimal completed job directly into the app's treasury."""
        # POST a job via the API so the treasury knows about it.
        client.post(
            "/api/job",
            json={
                "id": job_id,
                "topic": "Test receipt topic",
                "budget_cents": 1000,
            },
        )

    def test_404_for_unknown_job(self):
        client = self._make_client()
        # TestClient uses 127.0.0.1 — localhost is allowed without token.
        response = client.get("/api/receipt/NONEXISTENT-JOB-XYZ")
        self.assertEqual(response.status_code, 404)

    def test_200_for_valid_job_from_localhost(self):
        """TestClient sends from 127.0.0.1 so no token is needed."""
        from solvent.server import create_app
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI test client is not installed")

        # We need a job in the treasury. Directly seed via the agent.
        app = create_app(fresh=True)
        client = TestClient(app)

        # Create a minimal job through the job API so Treasury knows it.
        job_id = "Jrectest001"
        resp = client.post(
            "/api/job",
            json={
                "id": job_id,
                "topic": "Receipt endpoint integration test",
                "budget_cents": 2000,
            },
        )
        # Job may succeed, be declined, or pending — either way the row exists.
        # Now fetch the receipt.
        response = client.get(f"/api/receipt/{job_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])
        body = response.text
        # Should contain key receipt fields.
        self.assertIn(job_id[:8], body)

    def test_valid_token_grants_access(self):
        from solvent.server import create_app
        from solvent.delivery import make_delivery_token
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI test client is not installed")

        app = create_app(fresh=True)
        client = TestClient(app, raise_server_exceptions=True)

        job_id = "Jtokentest1"
        client.post(
            "/api/job",
            json={
                "id": job_id,
                "topic": "Token-gated receipt test",
                "budget_cents": 1500,
            },
        )
        token = make_delivery_token(job_id)
        response = client.get(f"/api/receipt/{job_id}?token={token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])


if __name__ == "__main__":
    unittest.main()
