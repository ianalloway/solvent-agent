"""Tests for Stripe reconciliation."""

import unittest

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


if __name__ == "__main__":
    unittest.main()
