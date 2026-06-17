"""Tests for SOLVENT Stripe client (payment verification, catalog, refunds, issuing)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from solvent.stripe_client import (
    CATALOG_PATH,
    StripeClient,
    WEBHOOK_CACHE_PATH,
)


class TestStripeClientSimulate(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(
            os.environ,
            {"STRIPE_API_KEY": "", "SOLVENT_FORCE_STRIPE_SIMULATE": "1"},
            clear=False,
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_simulate_create_and_confirm_instant(self):
        client = StripeClient()
        self.assertFalse(client.live)
        link = client.create_payment_link("Brief", 4900, "a@b.com")
        self.assertTrue(link["simulated"])
        self.assertTrue(link["id"].startswith("plink_sim_"))

        payment = client.confirm_payment(link)
        self.assertTrue(payment["paid"])
        self.assertTrue(payment["stripe_ref"].startswith("pi_sim_"))
        self.assertTrue(payment["checkout_session_id"].startswith("cs_sim_"))
        self.assertEqual(payment["payment_link_id"], link["id"])

    def test_simulate_refund(self):
        client = StripeClient()
        refund = client.refund_payment("pi_sim_abc123", 1000)
        self.assertTrue(refund["simulated"])
        self.assertTrue(refund["id"].startswith("ref_sim_"))

    def test_simulate_pay_vendor(self):
        client = StripeClient()
        pay = client.pay_vendor("nvidia-nemotron", 500, "tokens")
        self.assertTrue(pay["simulated"])
        self.assertFalse(pay["issuing"])
        self.assertTrue(pay["id"].startswith("pay_sim_"))


class TestStripeClientLive(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._catalog = Path(self._tmpdir.name) / "stripe_catalog.json"
        self._webhook_cache = Path(self._tmpdir.name) / "stripe_payments.json"
        self._patch_paths = mock.patch.multiple(
            "solvent.stripe_client",
            CATALOG_PATH=self._catalog,
            WEBHOOK_CACHE_PATH=self._webhook_cache,
        )
        self._patch_paths.start()
        self._has_stripe = mock.patch("solvent.stripe_client._HAS_STRIPE", True)
        self._has_stripe.start()
        self._env = mock.patch.dict(
            os.environ,
            {
                "STRIPE_API_KEY": "sk_test_fake",
                "SOLVENT_FORCE_STRIPE_SIMULATE": "",
                "STRIPE_PAYMENT_POLL_TIMEOUT": "0.1",
                "STRIPE_PAYMENT_POLL_INTERVAL": "0.01",
            },
            clear=False,
        )
        self._env.start()
        self._mock_stripe = mock.MagicMock()
        self._mock_stripe.api_key = None
        self._stripe_mod = mock.patch("solvent.stripe_client.stripe", self._mock_stripe)
        self._stripe_mod.start()

    def tearDown(self):
        self._stripe_mod.stop()
        self._has_stripe.stop()
        self._env.stop()
        self._patch_paths.stop()
        self._tmpdir.cleanup()

    def test_refuses_live_key(self):
        with mock.patch.dict(os.environ, {"STRIPE_API_KEY": "sk_live_bad"}, clear=False):
            with self.assertRaises(RuntimeError):
                StripeClient()

    def test_catalog_reuses_product_and_price(self):
        self._mock_stripe.Product.create.return_value = mock.Mock(id="prod_abc")
        self._mock_stripe.Price.create.return_value = mock.Mock(id="price_4900")
        self._mock_stripe.PaymentLink.create.return_value = mock.Mock(
            id="plink_1", url="https://buy.stripe.test/plink_1"
        )

        client = StripeClient()
        client.create_payment_link("Brief A", 4900, "a@b.com")
        client.create_payment_link("Brief B", 4900, "b@b.com")

        self._mock_stripe.Product.create.assert_called_once()
        self._mock_stripe.Price.create.assert_called_once()
        self.assertEqual(self._mock_stripe.PaymentLink.create.call_count, 2)

        catalog = json.loads(self._catalog.read_text())
        self.assertEqual(catalog["product_id"], "prod_abc")
        self.assertEqual(catalog["prices"]["4900"], "price_4900")

    def test_confirm_payment_polls_until_paid(self):
        unpaid = mock.Mock(payment_status="unpaid", payment_intent="pi_pending", id="cs_1", payment_link="plink_x")
        paid = mock.Mock(
            payment_status="paid",
            payment_intent="pi_paid123",
            id="cs_paid",
            payment_link="plink_x",
            amount_total=4900,
        )
        self._mock_stripe.checkout.Session.list.side_effect = [
            mock.Mock(data=[unpaid]),
            mock.Mock(data=[paid]),
        ]

        client = StripeClient()
        link = {"id": "plink_x", "amount_cents": 4900, "simulated": False}
        payment = client.confirm_payment(link)

        self.assertTrue(payment["paid"])
        self.assertEqual(payment["stripe_ref"], "pi_paid123")
        self.assertEqual(payment["checkout_session_id"], "cs_paid")
        self.assertGreaterEqual(self._mock_stripe.checkout.Session.list.call_count, 2)

    def test_confirm_payment_timeout(self):
        unpaid = mock.Mock(payment_status="unpaid")
        self._mock_stripe.checkout.Session.list.return_value = mock.Mock(data=[unpaid])

        client = StripeClient()
        link = {"id": "plink_x", "amount_cents": 4900, "simulated": False}
        payment = client.confirm_payment(link)

        self.assertFalse(payment["paid"])
        self.assertIn("not received", payment["reason"])

    def test_refund_requires_payment_intent(self):
        client = StripeClient()
        with self.assertRaises(ValueError):
            client.refund_payment("plink_bad", 1000)

    def test_refund_live_no_silent_fallback(self):
        self._mock_stripe.Refund.create.side_effect = Exception("stripe error")
        client = StripeClient()
        with self.assertRaises(Exception):
            client.refund_payment("pi_real123", 1000)

    def test_refund_live_success(self):
        self._mock_stripe.Refund.create.return_value = mock.Mock(id="re_123")
        client = StripeClient()
        refund = client.refund_payment("pi_real123", 1000)
        self.assertFalse(refund["simulated"])
        self.assertEqual(refund["id"], "re_123")
        self._mock_stripe.Refund.create.assert_called_once_with(
            payment_intent="pi_real123", amount=1000
        )

    def test_webhook_caches_payment(self):
        session = {
            "id": "cs_wh",
            "payment_status": "paid",
            "payment_intent": "pi_wh",
            "payment_link": "plink_wh",
            "amount_total": 7500,
        }
        self._mock_stripe.Webhook.construct_event.return_value = {
            "type": "checkout.session.completed",
            "data": {"object": session},
        }

        with mock.patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET": "whsec_test"}, clear=False):
            client = StripeClient()
            result = client.process_webhook(b"{}", "sig")
            self.assertTrue(result["paid"])
            self.assertEqual(result["stripe_ref"], "pi_wh")

            link = {"id": "plink_wh", "amount_cents": 7500, "simulated": False}
            payment = client.confirm_payment(link)
            self.assertTrue(payment["paid"])
            self.assertEqual(payment["stripe_ref"], "pi_wh")
            self._mock_stripe.checkout.Session.list.assert_not_called()

    def test_issuing_card_when_available(self):
        self._mock_stripe.issuing.Cardholder.list.return_value = mock.Mock(data=[])
        self._mock_stripe.issuing.Cardholder.create.return_value = mock.Mock(id="ich_1")
        self._mock_stripe.issuing.Card.create.return_value = mock.Mock(id="ic_1", last4="4242")

        client = StripeClient()
        pay = client.pay_vendor("market-data-api", 240, "2 pulls")

        self.assertFalse(pay["simulated"])
        self.assertTrue(pay["issuing"])
        self.assertEqual(pay["id"], "ic_1")
        limits = self._mock_stripe.issuing.Card.create.call_args.kwargs["spending_controls"][
            "spending_limits"
        ]
        self.assertEqual(limits[0]["amount"], 240)

    def test_issuing_fallback_on_error(self):
        self._mock_stripe.issuing.Cardholder.list.side_effect = Exception("issuing disabled")
        client = StripeClient()
        pay = client.pay_vendor("market-data-api", 240, "2 pulls")
        self.assertTrue(pay["simulated"])
        self.assertFalse(pay["issuing"])


class TestAgentPaymentFlow(unittest.TestCase):
    def test_agent_waits_for_payment_before_fulfil(self):
        from solvent.agent import Solvent

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            agent = Solvent(seed_cents=10_000, fresh=True)
            agent.t.path = db_path
            agent.t.lock_path = db_path.with_suffix(".lock")
            agent.t._init_db()

            link = {
                "id": "plink_test",
                "url": "https://pay.test/link",
                "amount_cents": 4900,
                "simulated": True,
            }
            pending = {"paid": False, "reason": "payment not received within 120s"}

            with mock.patch.object(agent.stripe, "create_payment_link", return_value=link):
                with mock.patch.object(agent.stripe, "confirm_payment", return_value=pending):
                    with mock.patch("solvent.service.fulfill") as mock_fulfill:
                        result = agent.handle_job(
                            {
                                "id": "J-test",
                                "topic": "Test topic",
                                "budget_cents": 5000,
                                "est_tokens": 1000,
                                "market_data_calls": 1,
                                "web_search_calls": 1,
                            }
                        )
                        mock_fulfill.assert_not_called()
                        self.assertEqual(result["stage"], "payment_pending")

    def test_agent_records_payment_intent_in_ledger(self):
        from solvent.agent import Solvent

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            agent = Solvent(seed_cents=10_000, fresh=True)
            agent.t.path = db_path
            agent.t.lock_path = db_path.with_suffix(".lock")
            agent.t._init_db()

            link = {
                "id": "plink_test",
                "url": "https://pay.test/link",
                "amount_cents": 4900,
                "simulated": True,
            }
            payment = {
                "paid": True,
                "stripe_ref": "pi_test123",
                "checkout_session_id": "cs_test123",
                "payment_link_id": "plink_test",
                "amount_cents": 4900,
            }

            with mock.patch.object(agent.stripe, "create_payment_link", return_value=link):
                with mock.patch.object(agent.stripe, "confirm_payment", return_value=payment):
                    with mock.patch("solvent.service.fulfill") as mock_fulfill:
                        mock_fulfill.return_value = {
                            "deliverable_path": "/tmp/x.md",
                            "tokens": 100,
                            "resources_used": [],
                        }
                        agent.handle_job(
                            {
                                "id": "J-test",
                                "topic": "Test topic",
                                "budget_cents": 5000,
                                "est_tokens": 1000,
                                "market_data_calls": 1,
                                "web_search_calls": 1,
                            }
                        )
            rev = [e for e in agent.t.entries if e.kind == "revenue" and e.job_id == "J-test"]
            self.assertEqual(len(rev), 1)
            self.assertEqual(rev[0].stripe_ref, "pi_test123")
            self.assertEqual(rev[0].stripe_session_id, "cs_test123")
            self.assertEqual(rev[0].stripe_link_id, "plink_test")


if __name__ == "__main__":
    unittest.main()
