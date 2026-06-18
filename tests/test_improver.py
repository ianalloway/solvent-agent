"""Tests for auto-improvement tuner."""

import unittest

from solvent.improver import analyze_metrics, propose_changes
from solvent.treasury import Treasury


class TestImprover(unittest.TestCase):
    def test_not_ready_with_few_jobs(self):
        t = Treasury()
        t.reset()
        analysis = analyze_metrics(t)
        self.assertFalse(analysis["ready"])

    def test_proposes_pricing_when_margin_negative(self):
        proposals = propose_changes({
            "ready": True,
            "avg_margin_error": -8,
            "refund_rate": 0.05,
            "avg_fulfillment_seconds": 60,
        })
        kinds = [p["type"] for p in proposals]
        self.assertIn("pricing", kinds)


if __name__ == "__main__":
    unittest.main()
