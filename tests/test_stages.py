"""Tests for idempotent job stages."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from solvent.agent import Solvent


class TestStagesIdempotency(unittest.TestCase):
    def test_double_paid_stage_does_not_double_earn(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            agent = Solvent(seed_cents=10_000, fresh=True)
            agent.t.path = db
            agent.t.lock_path = db.with_suffix(".lock")
            agent.t._init_db()

            job = {
                "id": "J-idem",
                "topic": "Idempotency test",
                "budget_cents": 5000,
                "est_tokens": 1000,
                "market_data_calls": 1,
                "web_search_calls": 1,
            }
            link = {
                "id": "cs_sim",
                "url": "http://x",
                "amount_cents": 5000,
                "simulated": True,
                "job_id": "J-idem",
            }
            payment = {
                "paid": True,
                "stripe_ref": "pi_idem",
                "checkout_session_id": "cs_idem",
                "amount_cents": 5000,
            }
            fulfill = {
                "deliverable_path": str(Path(tmp) / "J-idem.md"),
                "tokens": 50,
                "resources_used": [],
                "actual_cost_cents": 0,
                "fulfillment_seconds": 0.5,
                "tool_ctx": type(
                    "C",
                    (),
                    {"total_calls": 0, "market_data_calls": 0, "web_search_calls": 0},
                )(),
                "usage": {"total_tokens": 50},
            }
            Path(fulfill["deliverable_path"]).write_text("# brief")

            with mock.patch.dict("os.environ", {"SOLVENT_DELIVERY_SECRET": "x" * 32}, clear=False):
                with mock.patch.object(agent.stripe, "create_checkout_session", return_value=link):
                    with mock.patch.object(agent.stripe, "confirm_payment", return_value=payment):
                        with mock.patch("solvent.service.fulfill", return_value=fulfill):
                            with mock.patch(
                                "solvent.delivery.send_brief_email",
                                return_value={"simulated": True},
                            ):
                                agent.handle_job(job)
                                rev1 = len(
                                    [
                                        e
                                        for e in agent.t.entries
                                        if e.kind == "revenue" and e.job_id == "J-idem"
                                    ]
                                )
                                agent._runner._stage_paid(
                                    job,
                                    link,
                                    agent._runner._stage_quote(job).get("_quote")
                                    or mock.Mock(
                                        price_cents=5000,
                                        est_cost_cents=100,
                                        margin_cents=4900,
                                        margin_pct=90,
                                    ),
                                )
                                rev2 = len(
                                    [
                                        e
                                        for e in agent.t.entries
                                        if e.kind == "revenue" and e.job_id == "J-idem"
                                    ]
                                )
            self.assertEqual(rev1, 1)
            self.assertEqual(rev2, 1)


if __name__ == "__main__":
    unittest.main()


def test_validation_rejects_job_ids_unsafe_for_html_or_paths(tmp_path):
    from solvent.stages import validate_and_coerce_job
    from solvent.treasury import Treasury

    t = Treasury(path=tmp_path / "t.db")
    for bad in ('<img src=x onerror="alert(1)">', "../../etc/passwd", "a b", ""):
        job, err = validate_and_coerce_job(
            {"id": bad, "topic": "t", "budget_cents": 4900, "customer_email": "a@x.example"}, t
        )
        assert job is None
        assert err == "missing or invalid job ID"
    assert t.list_jobs() == []


class TestAdvanceJobTerminalStatuses(unittest.TestCase):
    def test_expired_job_is_not_resurrected(self):
        """Checkout sweep marks jobs expired; advance_job must not reopen them."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            agent = Solvent(seed_cents=10_000, fresh=True)
            agent.t.path = db
            agent.t.lock_path = db.with_suffix(".lock")
            agent.t._init_db()
            agent.t.upsert_job(
                "J-exp",
                "expired",
                topic="gone",
                budget_cents=4900,
                job_payload_json={
                    "id": "J-exp",
                    "topic": "gone",
                    "budget_cents": 4900,
                    "est_tokens": 1000,
                    "market_data_calls": 0,
                    "web_search_calls": 1,
                },
                quote_json='{"price_cents": 4900, "est_cost_cents": 100, "margin_cents": 4800, '
                '"margin_pct": 98.0, "accept": true, "reason": "accepted", '
                '"cost_breakdown": {}, "counter_offer": null}',
                error_reason="checkout expired",
            )
            event = agent._runner.advance_job("J-exp")
            self.assertEqual(event.get("status"), "expired")
            self.assertEqual(agent.t.get_job("J-exp")["status"], "expired")


class TestFulfillmentRefundRails(unittest.TestCase):
    def test_nano_payment_ref_still_marks_job_failed_on_fulfill_error(self):
        """A Nano block hash must not crash refund handling and leave revenue booked."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            agent = Solvent(seed_cents=50_000, fresh=True)
            agent.t.path = db
            agent.t.lock_path = db.with_suffix(".lock")
            agent.t._init_db()

            job = {
                "id": "J-nano-fail",
                "topic": "Nano refund path",
                "budget_cents": 5000,
                "customer_email": "a@x.example",
                "est_tokens": 1000,
                "market_data_calls": 0,
                "web_search_calls": 1,
            }
            from solvent.pricing import Quote

            q = Quote(
                price_cents=5000,
                est_cost_cents=100,
                margin_cents=4900,
                margin_pct=98.0,
                accept=True,
                reason="accepted",
                cost_breakdown={},
            )
            paid = {
                "paid": True,
                "stripe_ref": "B" * 64,
                "payment_ref": "B" * 64,
                "checkout_session_id": "nano_inv_1",
                "amount_cents": 5000,
                "rail": "nano",
            }
            agent.t.earn(
                5000,
                "Payment for J-nano-fail",
                job_id="J-nano-fail",
                stripe_ref="B" * 64,
                payment_ref="B" * 64,
            )
            agent.t.upsert_job("J-nano-fail", "paid_pending_fulfill", **{
                k: job[k] for k in ("topic", "budget_cents", "customer_email", "est_tokens",
                                     "market_data_calls", "web_search_calls")
            })

            with mock.patch("solvent.service.fulfill", side_effect=RuntimeError("fulfill boom")):
                with mock.patch.dict("os.environ", {"SOLVENT_DELIVERY_SECRET": "x" * 32}, clear=False):
                    event = agent._runner._stage_fulfill_and_book(job, q, paid)

            self.assertEqual(event.get("status"), "failed")
            row = agent.t.get_job("J-nano-fail")
            self.assertEqual(row["status"], "failed")
            refunds = [
                e for e in agent.t.entries
                if e.kind == "expense" and e.job_id == "J-nano-fail"
            ]
            self.assertTrue(refunds, "ledger refund must still be booked for Nano")
            self.assertTrue(any(e.stripe_ref.startswith("ref_ledger_") for e in refunds))
            metrics = agent.t.get_metrics("J-nano-fail")
            self.assertEqual(metrics.get("refunded"), 1)

    def test_refund_metric_stays_zero_when_stripe_refund_fails(self):
        """refunded=1 and refund receipts only after a successful booking."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            agent = Solvent(seed_cents=50_000, fresh=True)
            agent.t.path = db
            agent.t.lock_path = db.with_suffix(".lock")
            agent.t._init_db()

            job = {
                "id": "J-ref-fail",
                "topic": "Stripe refund failure",
                "budget_cents": 5000,
                "customer_email": "a@x.example",
                "est_tokens": 1000,
                "market_data_calls": 0,
                "web_search_calls": 1,
            }
            from solvent.pricing import Quote

            q = Quote(
                price_cents=5000,
                est_cost_cents=100,
                margin_cents=4900,
                margin_pct=98.0,
                accept=True,
                reason="accepted",
                cost_breakdown={},
            )
            paid = {
                "paid": True,
                "stripe_ref": "pi_will_fail",
                "checkout_session_id": "cs_1",
                "amount_cents": 5000,
            }
            agent.t.earn(
                5000,
                "Payment for J-ref-fail",
                job_id="J-ref-fail",
                stripe_ref="pi_will_fail",
            )
            agent.t.upsert_job(
                "J-ref-fail",
                "paid_pending_fulfill",
                **{
                    k: job[k]
                    for k in (
                        "topic",
                        "budget_cents",
                        "customer_email",
                        "est_tokens",
                        "market_data_calls",
                        "web_search_calls",
                    )
                },
            )

            events = []
            agent._runner.on_event = events.append
            with mock.patch("solvent.service.fulfill", side_effect=RuntimeError("boom")):
                with mock.patch.object(
                    agent.stripe, "refund_payment", side_effect=RuntimeError("stripe down")
                ):
                    with mock.patch.dict(
                        "os.environ", {"SOLVENT_DELIVERY_SECRET": "x" * 32}, clear=False
                    ):
                        event = agent._runner._stage_fulfill_and_book(job, q, paid)

            self.assertEqual(event.get("status"), "failed")
            self.assertTrue(any(e.get("stage") == "refund_failed" for e in events))
            self.assertFalse(any(e.get("stage") == "refund_receipt_ready" for e in events))
            metrics = agent.t.get_metrics("J-ref-fail") or {}
            self.assertEqual(metrics.get("refunded") or 0, 0)
            self.assertEqual(agent.t.job_refund_cents("J-ref-fail"), 0)

    def test_vendor_spend_skips_when_ledger_already_has_expense(self):
        """Crash between spend() and complete_stage must not double-pay."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            agent = Solvent(seed_cents=50_000, fresh=True)
            agent.t.path = db
            agent.t.lock_path = db.with_suffix(".lock")
            agent.t._init_db()

            job = {
                "id": "J-spend-crash",
                "topic": "Spend crash recovery",
                "budget_cents": 5000,
                "customer_email": "a@x.example",
                "est_tokens": 1000,
                "market_data_calls": 0,
                "web_search_calls": 1,
            }
            from solvent.pricing import Quote

            q = Quote(
                price_cents=5000,
                est_cost_cents=100,
                margin_cents=4900,
                margin_pct=98.0,
                accept=True,
                reason="accepted",
                cost_breakdown={},
            )
            paid = {
                "paid": True,
                "stripe_ref": "pi_spend",
                "checkout_session_id": "cs_spend",
                "amount_cents": 5000,
            }
            agent.t.earn(
                5000,
                "Payment",
                job_id="J-spend-crash",
                stripe_ref="pi_spend",
            )
            # Simulate prior crash: vendor paid + ledger written, stage incomplete.
            agent.t.spend(
                200,
                "prior vendor pay",
                job_id="J-spend-crash",
                vendor="openai",
                stripe_ref="po_prior",
            )
            agent.t.upsert_job(
                "J-spend-crash",
                "paid_pending_fulfill",
                **{
                    k: job[k]
                    for k in (
                        "topic",
                        "budget_cents",
                        "customer_email",
                        "est_tokens",
                        "market_data_calls",
                        "web_search_calls",
                    )
                },
            )

            deliverable = Path(tmp) / "brief.md"
            deliverable.write_text("# ok")
            fulfill = {
                "deliverable_path": str(deliverable),
                "pdf_path": None,
                "tokens": 10,
                "resources_used": [("openai", 200, "api")],
                "actual_cost_cents": 200,
                "fulfillment_seconds": 0.1,
                "tool_ctx": type(
                    "C", (), {"total_calls": 0, "market_data_calls": 0, "web_search_calls": 0}
                )(),
                "usage": {"total_tokens": 10},
            }

            with mock.patch("solvent.service.fulfill", return_value=fulfill):
                with mock.patch(
                    "solvent.delivery.send_brief_email", return_value={"simulated": True}
                ):
                    with mock.patch.object(agent.stripe, "pay_vendor") as pay:
                        with mock.patch.dict(
                            "os.environ", {"SOLVENT_DELIVERY_SECRET": "x" * 32}, clear=False
                        ):
                            event = agent._runner._stage_fulfill_and_book(job, q, paid)

            self.assertEqual(event.get("status"), "completed")
            pay.assert_not_called()
            openai_spends = [
                e
                for e in agent.t.entries
                if e.job_id == "J-spend-crash" and e.vendor == "openai"
            ]
            self.assertEqual(len(openai_spends), 1)

