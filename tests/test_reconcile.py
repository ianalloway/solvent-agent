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


if __name__ == "__main__":
    unittest.main()
