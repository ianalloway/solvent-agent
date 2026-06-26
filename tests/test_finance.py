"""Tests for the finance analytics module."""

import unittest

from solvent.finance import (
    income_statement,
    unit_economics,
    runway,
    balance_series,
    sparkline,
    build_report,
    format_report,
)
from solvent.treasury import LedgerEntry

DAY = 86_400.0


def _e(kind, cents, *, job_id=None, vendor=None, ts=0.0):
    return LedgerEntry(kind=kind, amount_cents=cents, memo="x", job_id=job_id, vendor=vendor, ts=ts)


class TestIncomeStatement(unittest.TestCase):
    def test_totals_and_margin(self):
        entries = [
            _e("capital", 10000),
            _e("revenue", 5000, job_id="J1"),
            _e("expense", 1000, job_id="J1", vendor="nvidia-nemotron"),
            _e("expense", 500, job_id="J1", vendor="pdf-render-saas"),
        ]
        inc = income_statement(entries)
        self.assertEqual(inc["revenue_cents"], 5000)
        self.assertEqual(inc["operating_cost_cents"], 1500)
        self.assertEqual(inc["net_profit_cents"], 3500)
        self.assertEqual(inc["net_margin_pct"], 70.0)
        self.assertEqual(inc["balance_cents"], 10000 + 5000 - 1500)
        # vendor breakdown sorted by amount desc
        self.assertEqual(list(inc["expense_by_vendor"].keys())[0], "nvidia-nemotron")

    def test_empty(self):
        inc = income_statement([])
        self.assertEqual(inc["revenue_cents"], 0)
        self.assertEqual(inc["net_margin_pct"], 0.0)


class TestUnitEconomics(unittest.TestCase):
    def test_per_job_averages(self):
        entries = [
            _e("revenue", 6000, job_id="J1"), _e("expense", 1000, job_id="J1"),
            _e("revenue", 4000, job_id="J2"), _e("expense", 1000, job_id="J2"),
        ]
        ue = unit_economics(entries)
        self.assertEqual(ue["completed_jobs"], 2)
        self.assertEqual(ue["avg_revenue_cents"], 5000)
        self.assertEqual(ue["avg_cost_cents"], 1000)
        self.assertEqual(ue["avg_profit_cents"], 4000)
        self.assertEqual(ue["contribution_margin_pct"], 80.0)

    def test_no_completed_jobs(self):
        ue = unit_economics([_e("expense", 100, job_id="J1")])  # cost but no revenue
        self.assertEqual(ue["completed_jobs"], 0)
        self.assertEqual(ue["avg_profit_cents"], 0)


class TestRunway(unittest.TestCase):
    def test_idle_when_only_capital(self):
        rw = runway([_e("capital", 10000)])
        self.assertEqual(rw["status"], "idle")
        self.assertIsNone(rw["runway_days"])

    def test_insufficient_history(self):
        # two ops within a few minutes -> no reliable daily rate
        rw = runway([_e("revenue", 100, ts=0), _e("expense", 50, ts=120)])
        self.assertEqual(rw["status"], "insufficient_history")
        self.assertIsNone(rw["daily_net_cents"])

    def test_cashflow_positive(self):
        rw = runway([_e("revenue", 5000, ts=0), _e("expense", 1000, ts=2 * DAY)])
        self.assertEqual(rw["status"], "cashflow_positive")
        self.assertTrue(rw["cashflow_positive"])
        self.assertIsNone(rw["runway_days"])
        self.assertGreater(rw["daily_net_cents"], 0)

    def test_burning_computes_runway(self):
        # seed 10000, then net burn of 4000 over 2 days -> 2000/day burn.
        entries = [
            _e("capital", 10000, ts=0),
            _e("revenue", 1000, ts=0),
            _e("expense", 5000, ts=2 * DAY),
        ]
        rw = runway(entries, reserve_cents=2000)
        self.assertEqual(rw["status"], "burning")
        self.assertFalse(rw["cashflow_positive"])
        # balance = 6000, spendable above reserve = 4000, burn 2000/day -> 2.0 days
        self.assertEqual(rw["balance_cents"], 6000)
        self.assertEqual(rw["runway_days"], 2.0)


class TestSparkline(unittest.TestCase):
    def test_monotonic_series(self):
        spark = sparkline([0, 1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(len(spark), 8)
        self.assertEqual(spark[0], "▁")
        self.assertEqual(spark[-1], "█")

    def test_flat_and_empty(self):
        self.assertEqual(sparkline([5, 5, 5]), "▁▁▁")
        self.assertEqual(sparkline([]), "")

    def test_balance_series_downsamples(self):
        entries = [_e("revenue", 1, ts=i) for i in range(100)]
        self.assertLessEqual(len(balance_series(entries, buckets=10)), 10)


class TestReport(unittest.TestCase):
    def test_build_and_format(self):
        entries = [
            _e("capital", 10000, ts=0),
            _e("revenue", 5000, job_id="J1", ts=0),
            _e("expense", 1000, job_id="J1", vendor="nvidia-nemotron", ts=2 * DAY),
        ]
        report = build_report(entries, reserve_cents=2000)
        self.assertIn("income_statement", report)
        self.assertIn("unit_economics", report)
        self.assertIn("runway", report)
        text = format_report(report)
        self.assertIn("Financial Report", text)
        self.assertIn("Net profit", text)
        self.assertIn("Unit economics", text)


if __name__ == "__main__":
    unittest.main()
