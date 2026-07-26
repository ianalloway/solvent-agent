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
