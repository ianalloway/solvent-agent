"""Tests for StageRunner.retry_job (job retry / resumability)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from solvent.guardrails import Guardrails
from solvent.pricing import PricingPolicy
from solvent.stages import StageRunner
from solvent.stripe_client import StripeClient
from solvent.treasury import Treasury

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner(tmp_dir: str, seed_cents: int = 50_000) -> StageRunner:
    """Create a fresh StageRunner backed by an isolated in-process Treasury."""
    db = Path(tmp_dir) / "retry_test.db"
    t = Treasury(path=db)
    t.seed(seed_cents)
    guard = Guardrails(t)
    stripe = StripeClient()
    return StageRunner(treasury=t, guard=guard, stripe=stripe, pricing=PricingPolicy())


def _sample_job(job_id: str = "J-retry") -> dict:
    return {
        "id": job_id,
        "topic": "Retry test topic",
        "budget_cents": 5000,
        "customer_email": "retry@test.example",
        "est_tokens": 500,
        "market_data_calls": 1,
        "web_search_calls": 1,
    }


def _mock_fulfill(tmp_dir: str, job_id: str) -> dict:
    path = str(Path(tmp_dir) / f"{job_id}.md")
    Path(path).write_text("# deliverable")
    return {
        "deliverable_path": path,
        "tokens": 100,
        "resources_used": [],
        "actual_cost_cents": 0,
        "fulfillment_seconds": 0.1,
        "tool_ctx": type(
            "C", (), {"total_calls": 0, "market_data_calls": 0, "web_search_calls": 0}
        )(),
        "usage": {"total_tokens": 100},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRetryPrePaymentFailed(unittest.TestCase):
    """Retry of a failed pre-payment job should restart from quote."""

    def test_retry_restarts_from_quote(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _make_runner(tmp)
            job = _sample_job("J-pre-fail")

            # Manually insert a failed pre-payment job (no revenue in ledger)
            runner.t.upsert_job(
                "J-pre-fail",
                "failed",
                topic=job["topic"],
                budget_cents=job["budget_cents"],
                customer_email=job["customer_email"],
                est_tokens=job["est_tokens"],
                market_data_calls=job["market_data_calls"],
                web_search_calls=job["web_search_calls"],
                error_reason="network error before checkout",
                job_payload_json=job,
            )

            checkout_link = {
                "id": "cs_pre",
                "url": "http://pay.example/pre",
                "amount_cents": 5000,
                "simulated": True,
                "job_id": "J-pre-fail",
            }
            payment = {
                "paid": True,
                "stripe_ref": "pi_pre",
                "checkout_session_id": "cs_pre",
                "amount_cents": 5000,
            }
            fulfill = _mock_fulfill(tmp, "J-pre-fail")

            events = []
            runner.on_event = events.append

            with mock.patch.dict("os.environ", {"SOLVENT_DELIVERY_SECRET": "x" * 32}):
                with mock.patch.object(
                    runner.stripe, "create_checkout_session", return_value=checkout_link
                ):
                    with mock.patch.object(runner.stripe, "confirm_payment", return_value=payment):
                        with mock.patch("solvent.service.fulfill", return_value=fulfill):
                            with mock.patch(
                                "solvent.delivery.send_brief_email",
                                return_value={"simulated": True},
                            ):
                                result = runner.retry_job("J-pre-fail")

            # Job should now be completed
            stage_names = [e.get("stage") for e in events]
            self.assertIn("retry_started", stage_names)
            self.assertIn("quote", stage_names)
            self.assertEqual(result.get("stage"), "booked")
            self.assertEqual(result.get("status"), "completed")

            # retry_count must have been incremented
            row = runner.t.get_job("J-pre-fail")
            self.assertEqual(row["retry_count"], 1)


class TestRetryPostPaymentFailed(unittest.TestCase):
    """Retry of a failed post-payment job should re-run fulfill without charging again."""

    def test_retry_reruns_fulfillment_no_new_charge(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _make_runner(tmp)
            job = _sample_job("J-post-fail")

            from dataclasses import asdict

            from solvent.pricing import quote as _quote

            q = _quote(job, runner.pricing)

            # Insert the job as failed-after-payment
            runner.t.upsert_job(
                "J-post-fail",
                "failed",
                topic=job["topic"],
                budget_cents=job["budget_cents"],
                customer_email=job["customer_email"],
                est_tokens=job["est_tokens"],
                market_data_calls=job["market_data_calls"],
                web_search_calls=job["web_search_calls"],
                error_reason="fulfillment network timeout",
                quote_json=json.dumps(asdict(q)),
                job_payload_json=job,
            )
            # Simulate that revenue was already collected
            runner.t.earn(
                q.price_cents,
                "Prior payment",
                job_id="J-post-fail",
                stripe_ref="pi_prior",
            )

            initial_revenue = runner.t.revenue_cents()

            fulfill = _mock_fulfill(tmp, "J-post-fail")
            events = []
            runner.on_event = events.append

            with mock.patch.dict("os.environ", {"SOLVENT_DELIVERY_SECRET": "x" * 32}):
                with mock.patch.object(runner.stripe, "create_checkout_session") as mock_checkout:
                    with mock.patch("solvent.service.fulfill", return_value=fulfill):
                        with mock.patch(
                            "solvent.delivery.send_brief_email",
                            return_value={"simulated": True},
                        ):
                            result = runner.retry_job("J-post-fail")

            # Stripe checkout must NOT have been called again
            mock_checkout.assert_not_called()

            # Revenue must not have increased
            self.assertEqual(runner.t.revenue_cents(), initial_revenue)

            stage_names = [e.get("stage") for e in events]
            self.assertIn("retry_started", stage_names)
            self.assertEqual(result.get("stage"), "booked")


class TestRetryCompletedJobRaises(unittest.TestCase):
    """Retry of a completed job must raise ValueError."""

    def test_completed_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _make_runner(tmp)
            runner.t.upsert_job("J-done", "completed")

            with self.assertRaises(ValueError) as ctx:
                runner.retry_job("J-done")
            self.assertIn("completed", str(ctx.exception))

    def test_in_progress_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _make_runner(tmp)
            runner.t.upsert_job("J-ip", "in_progress")

            with self.assertRaises(ValueError):
                runner.retry_job("J-ip")

    def test_unknown_job_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _make_runner(tmp)
            with self.assertRaises(ValueError) as ctx:
                runner.retry_job("J-nonexistent")
            self.assertIn("not found", str(ctx.exception))


class TestRetryMaxLimit(unittest.TestCase):
    """retry_count cap of 3 must be enforced."""

    def test_max_retry_limit_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _make_runner(tmp)
            runner.t.upsert_job("J-cap", "failed", error_reason="simulated failure")

            # Manually set retry_count to 3 (already at cap)
            with runner.t.lock(), runner.t._conn() as conn, conn:
                conn.execute("UPDATE jobs SET retry_count = 3 WHERE id = ?", ("J-cap",))

            with self.assertRaises(ValueError) as ctx:
                runner.retry_job("J-cap")
            self.assertIn("exceeded", str(ctx.exception))

    def test_exactly_three_retries_allowed(self):
        """The 3rd retry should succeed; the 4th should fail."""
        with tempfile.TemporaryDirectory() as tmp:
            runner = _make_runner(tmp)
            job = _sample_job("J-limit")

            from dataclasses import asdict

            from solvent.pricing import quote as _quote

            q = _quote(job, runner.pricing)

            runner.t.upsert_job(
                "J-limit",
                "failed",
                topic=job["topic"],
                budget_cents=job["budget_cents"],
                customer_email=job["customer_email"],
                est_tokens=job["est_tokens"],
                market_data_calls=job["market_data_calls"],
                web_search_calls=job["web_search_calls"],
                error_reason="keep failing",
                quote_json=json.dumps(asdict(q)),
                job_payload_json=job,
            )
            # Simulate revenue already collected so we only test the fulfill path
            runner.t.earn(q.price_cents, "Payment", job_id="J-limit", stripe_ref="pi_limit")

            # Manually set retry_count to 2
            with runner.t.lock():
                with runner.t._conn() as conn:
                    with conn:
                        conn.execute("UPDATE jobs SET retry_count = 2 WHERE id = ?", ("J-limit",))

            fulfill = _mock_fulfill(tmp, "J-limit")
            with mock.patch.dict("os.environ", {"SOLVENT_DELIVERY_SECRET": "x" * 32}):
                with mock.patch("solvent.service.fulfill", return_value=fulfill):
                    with mock.patch(
                        "solvent.delivery.send_brief_email",
                        return_value={"simulated": True},
                    ):
                        # 3rd retry (count goes to 3) — should succeed
                        result = runner.retry_job("J-limit")
            self.assertEqual(result.get("stage"), "booked")
            self.assertEqual(runner.t.get_job("J-limit")["retry_count"], 3)

            # Reset to failed so we can try a 4th
            runner.t.upsert_job("J-limit", "failed")

            with self.assertRaises(ValueError):
                runner.retry_job("J-limit")


class TestRetryCountIncrements(unittest.TestCase):
    """retry_count must increment correctly across successive retries."""

    def test_retry_count_increments(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _make_runner(tmp)
            job = _sample_job("J-cnt")

            from dataclasses import asdict

            from solvent.pricing import quote as _quote

            q = _quote(job, runner.pricing)

            runner.t.upsert_job(
                "J-cnt",
                "failed",
                topic=job["topic"],
                budget_cents=job["budget_cents"],
                customer_email=job["customer_email"],
                est_tokens=job["est_tokens"],
                market_data_calls=job["market_data_calls"],
                web_search_calls=job["web_search_calls"],
                error_reason="transient error",
                quote_json=json.dumps(asdict(q)),
                job_payload_json=job,
            )
            runner.t.earn(q.price_cents, "Payment", job_id="J-cnt", stripe_ref="pi_cnt")

            for expected_count in (1, 2, 3):
                runner.t.upsert_job("J-cnt", "failed")
                fulfill = _mock_fulfill(tmp, "J-cnt")
                with mock.patch.dict("os.environ", {"SOLVENT_DELIVERY_SECRET": "x" * 32}):
                    with mock.patch("solvent.service.fulfill", return_value=fulfill):
                        with mock.patch(
                            "solvent.delivery.send_brief_email",
                            return_value={"simulated": True},
                        ):
                            runner.retry_job("J-cnt")
                row = runner.t.get_job("J-cnt")
                self.assertEqual(row["retry_count"], expected_count)


if __name__ == "__main__":
    unittest.main()
