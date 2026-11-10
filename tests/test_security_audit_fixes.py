"""Regression tests for confirmed security-audit findings (V-01 … V-06)."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock

from solvent.checkout import CheckoutPolicy, sweep
from solvent.guardrails import Guardrails
from solvent.pricing import PricingPolicy, quote as _quote
from solvent.receipt import build_receipt
from solvent.stages import StageRunner
from solvent.stripe_client import StripeClient
from solvent.treasury import Treasury


class TestExpireDoesNotDowngradePaid(unittest.TestCase):
    def test_sweep_skips_expire_when_job_already_paid(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Treasury(path=Path(tmp) / "t.db")
            t.seed(50_000)
            t.upsert_job(
                "J1",
                "completed",
                topic="t",
                budget_cents=5000,
                customer_email="a@b.com",
            )
            t.earn(5000, "paid", job_id="J1", stripe_ref="pi_1")
            t.upsert_checkout("J1", "cs_1", "http://x", "open", rail="stripe")
            policy = CheckoutPolicy(reminder_after_hours=999, max_reminders=0, expire_after_hours=0)
            # Simulate open_checkouts listing the job after a stale snapshot
            # while payment already completed the job.
            stale_row = {
                "job_id": "J1",
                "next_action": "expire",
                "rail": "stripe",
                "checkout_url": "http://x",
                "age_hours": 50.0,
                "reminders_sent": 0,
                "invoice_expired": False,
            }
            with mock.patch("solvent.checkout.open_checkouts", return_value=[stale_row]):
                result = sweep(t, policy=policy, now=time.time())
            self.assertEqual(t.get_job("J1")["status"], "completed")
            self.assertEqual(result["expired"], [])
            self.assertTrue(t.job_has_revenue("J1"))


class TestAsyncWebhookAmountValidation(unittest.TestCase):
    def test_underpay_cache_does_not_earn(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Treasury(path=Path(tmp) / "t.db")
            t.seed(50_000)
            runner = StageRunner(
                treasury=t,
                guard=Guardrails(t),
                stripe=StripeClient(),
                pricing=PricingPolicy(),
                sync_payment=False,
            )
            job = {
                "id": "J-under",
                "topic": "underpay",
                "budget_cents": 5000,
                "customer_email": "a@b.com",
                "est_tokens": 500,
                "market_data_calls": 0,
                "web_search_calls": 1,
            }
            q = _quote(job, runner.pricing)
            t.upsert_job(
                "J-under",
                "awaiting_payment",
                quote_json=json.dumps(asdict(q)),
                checkout_session_id="cs_u",
                checkout_url="http://u",
                topic=job["topic"],
                budget_cents=job["budget_cents"],
                customer_email=job["customer_email"],
                est_tokens=job["est_tokens"],
                market_data_calls=0,
                web_search_calls=1,
            )
            runner.stripe._webhook_payments["job:J-under"] = {
                "paid": True,
                "stripe_ref": "pi_under",
                "checkout_session_id": "cs_u",
                "amount_cents": 100,
                "currency": "usd",
                "job_id": "J-under",
            }
            checkout = {
                "session_id": "cs_u",
                "url": "http://u",
                "amount_cents": q.price_cents,
                "simulated": False,
            }
            paid = runner._stage_paid(job, checkout, q)
            self.assertNotEqual(paid.get("stage"), "paid")
            self.assertFalse(t.job_has_revenue("J-under"))
            self.assertIn("below expected", paid.get("reason", "").lower())


class TestCustomerReceiptRedaction(unittest.TestCase):
    def test_public_receipt_hides_treasury(self):
        text = build_receipt(
            {"id": "Jabc", "topic": "t", "budget_cents": 5000, "status": "completed"},
            pnl_cents=1000,
            balance_cents=99_999,
        )
        self.assertNotIn("Balance:", text)
        self.assertNotIn("COGS:", text)
        self.assertIn("Earned:", text)


class TestCustomerChatForceOffline(unittest.TestCase):
    def test_force_offline_blocks_live_complete(self):
        from solvent import nemotron

        with mock.patch.dict("os.environ", {"NVIDIA_API_KEY": "nvapi-test"}, clear=False):
            with mock.patch.object(nemotron, "_live_complete") as live:
                with nemotron.force_offline():
                    text, usage = nemotron.complete("sys", "user")
                live.assert_not_called()
        self.assertIn("SOLVENT", text)
        self.assertTrue(usage.get("estimated"))


if __name__ == "__main__":
    unittest.main()
