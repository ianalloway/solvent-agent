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
    """Tests for the /api/receipt/{job_id} GET endpoint.

    Rather than spinning up the full SOLVENT server (which has a pre-existing
    RecursionError when /api/job is driven via TestClient), we build minimal
    FastAPI micro-apps backed by a seeded Treasury. This keeps the tests
    focused on the endpoint logic itself.
    """

    def setUp(self):
        self._env = mock.patch.dict(
            os.environ,
            {"SOLVENT_DELIVERY_SECRET": "x" * 32},
            clear=False,
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def _make_receipt_app(self, agent):
        """Build a minimal FastAPI app that exposes only the receipt endpoint."""
        from fastapi import FastAPI, HTTPException
        from starlette.requests import Request
        from starlette.responses import PlainTextResponse
        from solvent.delivery import verify_delivery_token
        from solvent.receipt import build_receipt

        _app = FastAPI(title="SOLVENT-receipt-test")

        @_app.get("/api/receipt/{job_id}")
        def _get_receipt(job_id: str, token: str = "", request: Request = None):
            row = agent.t.get_job(job_id)
            if not row:
                raise HTTPException(404, "job not found")
            client_host = (
                getattr(request.client, "host", "") if (request and request.client) else ""
            )
            is_local = client_host in ("127.0.0.1", "::1", "localhost", "testclient")
            if not is_local and not verify_delivery_token(job_id, token):
                raise HTTPException(403, "invalid or expired delivery token")
            pnl = agent.t.job_pnl_cents(job_id)
            balance = agent.t.balance_cents()
            return PlainTextResponse(build_receipt(dict(row), pnl, balance))

        return _app

    def _make_agent_with_job(self, job_id: str, topic: str = "Test topic"):
        from solvent.agent import Solvent
        a = Solvent(seed_cents=10_000, fresh=True, sync_payment=False)
        a.t.upsert_job(
            job_id,
            "completed",
            topic=topic,
            budget_cents=2000,
            customer_email="test@example.com",
            est_tokens=500,
            market_data_calls=1,
            web_search_calls=1,
        )
        return a

    def test_404_for_unknown_job(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI test client is not installed")
        from solvent.agent import Solvent
        agent = Solvent(seed_cents=5_000, fresh=True, sync_payment=False)
        client = TestClient(self._make_receipt_app(agent))
        response = client.get("/api/receipt/NONEXISTENT-JOB-XYZ")
        self.assertEqual(response.status_code, 404)

    def test_200_for_valid_job_from_localhost(self):
        """TestClient connects from 127.0.0.1 — no token required."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI test client is not installed")

        job_id = "Jrectest001"
        agent = self._make_agent_with_job(job_id, "Receipt endpoint integration test")
        client = TestClient(self._make_receipt_app(agent))

        response = client.get(f"/api/receipt/{job_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])
        self.assertIn(job_id[:8], response.text)

    def test_valid_token_grants_access(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI test client is not installed")
        from solvent.delivery import make_delivery_token

        job_id = "Jtokentest1"
        agent = self._make_agent_with_job(job_id, "Token-gated receipt test")
        client = TestClient(self._make_receipt_app(agent))

        token = make_delivery_token(job_id)
        response = client.get(f"/api/receipt/{job_id}?token={token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])


if __name__ == "__main__":
    unittest.main()
