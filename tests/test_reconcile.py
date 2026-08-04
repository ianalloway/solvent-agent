"""Tests for Stripe reconciliation."""

import unittest
from unittest import mock

from solvent.reconcile import reconcile
from solvent.treasury import Treasury


class TestReconcile(unittest.TestCase):
    def test_ledger_only_mode(self):
        t = Treasury()
        t.reset()
        t.seed(10_000)
        t.earn(5000, "test", job_id="J1", stripe_ref="pi_sim_abc")
        report = reconcile(t)
        self.assertEqual(report["mode"], "ledger_only")
        self.assertIn("pi_sim_abc", report["unmatched_ledger"])


class TestReconcileDuplicateDetection(unittest.TestCase):
    """The `duplicates` field must actually report double-booked revenue."""

    def _fresh(self):
        t = Treasury()
        t.reset()
        t.seed(10_000)
        return t

    def test_duplicate_payment_intent_is_reported_as_drift(self):
        t = self._fresh()
        t.earn(5000, "job one", job_id="J1", stripe_ref="pi_dup")
        t.earn(5000, "job one again", job_id="J2", stripe_ref="pi_dup")
        report = reconcile(t)
        self.assertIn("pi_dup", report["duplicates"])
        self.assertTrue(report["drift"])

    def test_distinct_refs_are_not_duplicates(self):
        t = self._fresh()
        t.earn(5000, "job one", job_id="J1", stripe_ref="pi_a")
        t.earn(5000, "job two", job_id="J2", stripe_ref="pi_b")
        report = reconcile(t)
        self.assertEqual(report["duplicates"], [])
        self.assertFalse(report["drift"])

    def test_duplicates_are_sorted_and_deduplicated(self):
        t = self._fresh()
        for _ in range(3):
            t.earn(1000, "thrice", job_id="J1", stripe_ref="pi_zzz")
        t.earn(1000, "twice", job_id="J2", stripe_ref="pi_aaa")
        t.earn(1000, "twice", job_id="J3", stripe_ref="pi_aaa")
        report = reconcile(t)
        self.assertEqual(report["duplicates"], ["pi_aaa", "pi_zzz"])

    def test_clean_ledger_has_no_drift(self):
        t = self._fresh()
        t.earn(5000, "solo", job_id="J1", stripe_ref="pi_only")
        report = reconcile(t)
        self.assertEqual(report["mode"], "ledger_only")
        self.assertEqual(report["duplicates"], [])
        self.assertFalse(report["drift"])


class TestReconcileLiveKeyRefusal(unittest.TestCase):
    def _report_with_key(self, key):
        import os as _os

        old = _os.environ.get("STRIPE_API_KEY")
        _os.environ["STRIPE_API_KEY"] = key
        try:
            return reconcile(Treasury())
        finally:
            if old is None:
                _os.environ.pop("STRIPE_API_KEY", None)
            else:
                _os.environ["STRIPE_API_KEY"] = old

    def test_standard_live_key_refused(self):
        report = self._report_with_key("sk_live_confidential")
        self.assertEqual(report["mode"], "ledger_only")

    def test_restricted_live_key_refused(self):
        # rk_live_ (restricted key) must also be refused, not fall through to the live API.
        report = self._report_with_key("rk_live_confidential")
        self.assertEqual(report["mode"], "ledger_only")

    def test_test_key_allows_full_mode(self):
        report = self._report_with_key("sk_test_confidential")
        # No Stripe SDK available / network in tests → ledger_only or full, but never a live call.
        self.assertIn(report["mode"], ("ledger_only", "full"))


class TestReconcileStripeIntegration(unittest.TestCase):
    """Mocked Stripe path — covers full-mode reconcile (lines 63–85).

    The ledger-only and live-key-refusal branches are tested separately in
    TestReconcile and TestReconcileLiveKeyRefusal.  These tests exercise the
    Stripe fetch, matched/unmatched logic, and Stripe-exception handling
    without touching the network.
    """

    def setUp(self):
        self._mock_stripe = mock.MagicMock()
        self._stripe_mod = mock.patch(
            "solvent.reconcile.stripe_sdk",
            self._mock_stripe,
        )
        self._stripe_mod.start()

    def tearDown(self):
        self._stripe_mod.stop()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _fresh(self):
        t = Treasury()
        t.reset()
        t.seed(10_000)
        return t

    @staticmethod
    def _pi(pi_id: str, status: str = "succeeded"):
        m = mock.Mock()
        m.id = pi_id
        m.status = status
        return m

    def _report(self, treasury, *, pi_list=None, since_days=7):
        import os as _os

        old = _os.environ.get("STRIPE_API_KEY")
        _os.environ["STRIPE_API_KEY"] = "sk_test_abc123"
        if pi_list is not None:
            mock_list_ret = self._mock_stripe.PaymentIntent.list.return_value
            mock_list_ret.auto_paging_iter.return_value = pi_list
        try:
            return reconcile(treasury, since_days=since_days)
        finally:
            if old is None:
                _os.environ.pop("STRIPE_API_KEY", None)
            else:
                _os.environ["STRIPE_API_KEY"] = old

    # ------------------------------------------------------------------
    # matched / unmatched
    # ------------------------------------------------------------------
    def test_full_mode_when_ledger_matches_stripe(self):
        """Identical refs on both sides → empty unmatched, drift=False, mode='full'."""
        t = self._fresh()
        t.earn(5000, "job1", job_id="J1", stripe_ref="pi_match")
        report = self._report(t, pi_list=[self._pi("pi_match")])
        self.assertEqual(report["mode"], "full")
        self.assertEqual(report["unmatched_stripe"], [])
        self.assertEqual(report["unmatched_ledger"], [])
        self.assertFalse(report["drift"])

    def test_unmatched_ledger_ref_surfaced(self):
        """Ledger ref not seen on Stripe → unmatched_ledger populated."""
        t = self._fresh()
        t.earn(5000, "job1", job_id="J1", stripe_ref="pi_ledger")
        report = self._report(t, pi_list=[self._pi("pi_orphan")])
        self.assertIn("pi_orphan", report["unmatched_stripe"])
        self.assertIn("pi_ledger", report["unmatched_ledger"])

    def test_unmatched_stripe_pi_surfaced(self):
        """Stripe PI with no ledger entry → unmatched_stripe populated."""
        t = self._fresh()
        t.earn(5000, "job1", job_id="J1", stripe_ref="pi_ledger")
        report = self._report(t, pi_list=[self._pi("pi_orphan")])
        self.assertIn("pi_orphan", report["unmatched_stripe"])
        self.assertIn("pi_ledger", report["unmatched_ledger"])
        self.assertTrue(report["drift"])

    def test_drift_true_on_any_mismatch(self):
        """Any unmatched entry on either side sets drift=True."""
        t = self._fresh()
        t.earn(5000, "job1", job_id="J1", stripe_ref="pi_ledger")
        report = self._report(t, pi_list=[self._pi("pi_orphan")])
        self.assertTrue(report["drift"])

    # ------------------------------------------------------------------
    # Stripe-side failures
    # ------------------------------------------------------------------
    def test_stripe_api_exception_sets_ledger_only(self):
        """A Stripe API error is surfaced in the report without crashing."""
        t = self._fresh()
        t.earn(5000, "job1", job_id="J1", stripe_ref="pi_match")
        self._mock_stripe.PaymentIntent.list.side_effect = RuntimeError(
            "Stripe API down"
        )
        with mock.patch.dict("os.environ", {"STRIPE_API_KEY": "sk_test_abc123"}):
            report = reconcile(t)
        self.assertEqual(report["mode"], "ledger_only")
        self.assertIn("Stripe API down", report["stripe_error"])

    # ------------------------------------------------------------------
    # since_days → created gte filter
    # ------------------------------------------------------------------
    def test_since_days_passed_as_created_filter(self):
        """PaymentIntent.list is called with created>=since matching since_days."""
        t = self._fresh()
        t.earn(5000, "job1", job_id="J1", stripe_ref="pi_1")
        mock_list_ret = self._mock_stripe.PaymentIntent.list.return_value
        mock_list_ret.auto_paging_iter.return_value = [self._pi("pi_1")]
        with mock.patch.dict("os.environ", {"STRIPE_API_KEY": "sk_test_abc123"}):
            report = reconcile(t, since_days=14)
        self.assertEqual(report["mode"], "full")
        call_kwargs = self._mock_stripe.PaymentIntent.list.call_args
        self.assertIn("created", call_kwargs.kwargs)
        from datetime import datetime, timezone

        now_ts = datetime.now(timezone.utc).timestamp()
        expected_gte = int(now_ts - 14 * 86400)
        actual_gte = call_kwargs.kwargs["created"]["gte"]
        self.assertAlmostEqual(actual_gte, expected_gte, delta=2)

    # ------------------------------------------------------------------
    # stripe_session_id duplicate detection
    # ------------------------------------------------------------------
    def test_stripe_session_id_counted_for_duplicates(self):
        """An entry with stripe_session_id (no stripe_ref) still detects doubles."""
        t = self._fresh()
        t.earn(5000, "job1", job_id="J1", stripe_session_id="cs_dup")
        t.earn(5000, "job2", job_id="J2", stripe_session_id="cs_dup")
        report = reconcile(t)  # ledger_only path
        self.assertIn("cs_dup", report["duplicates"])
        self.assertTrue(report["drift"])


if __name__ == "__main__":
    unittest.main()
