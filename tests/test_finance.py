"""Tests for the finance analytics module."""

import unittest

from solvent.finance import (
    income_statement,
    unit_economics,
    runway,
    balance_series,
    sparkline,
    period_pnl,
    forecast,
    build_report,
    format_report,
)
from solvent.treasury import LedgerEntry

DAY = 86_400.0
# 2021-01-01 00:00:00 UTC and 2021-01-02 00:00:00 UTC
T_JAN1 = 1_609_459_200.0
T_JAN2 = T_JAN1 + DAY


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


class TestPeriodPnl(unittest.TestCase):
    def test_buckets_by_day_with_running_balance(self):
        entries = [
            _e("capital", 10000, ts=T_JAN1),
            _e("revenue", 5000, job_id="J1", ts=T_JAN1),
            _e("expense", 1000, job_id="J1", vendor="v", ts=T_JAN1),
            _e("revenue", 3000, job_id="J2", ts=T_JAN2),
            _e("expense", 2000, job_id="J2", vendor="v", ts=T_JAN2),
        ]
        rows = period_pnl(entries, "day")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["period"], "2021-01-01")
        self.assertEqual(rows[0]["net_cents"], 4000)        # 5000 - 1000
        self.assertEqual(rows[0]["ending_balance_cents"], 14000)  # +10000 capital
        self.assertEqual(rows[1]["net_cents"], 1000)        # 3000 - 2000
        self.assertEqual(rows[1]["ending_balance_cents"], 15000)

    def test_month_bucketing(self):
        rows = period_pnl([_e("revenue", 100, ts=T_JAN1)], "month")
        self.assertEqual(rows[0]["period"], "2021-01")

    def test_invalid_period_raises(self):
        with self.assertRaises(ValueError):
            period_pnl([], "fortnight")

    def test_empty(self):
        self.assertEqual(period_pnl([], "day"), [])


class TestForecast(unittest.TestCase):
    def test_insufficient_history(self):
        fc = forecast([_e("revenue", 100, ts=T_JAN1)])  # one active day
        self.assertEqual(fc["status"], "insufficient_history")
        self.assertIsNone(fc["projected_balance_cents"])

    def test_projects_from_steady_daily_net(self):
        # +$10/day for two days, zero volatility -> linear projection, tight band
        entries = [
            _e("capital", 10000, ts=T_JAN1),
            _e("revenue", 1000, job_id="J1", ts=T_JAN1),
            _e("revenue", 1000, job_id="J2", ts=T_JAN2),
        ]
        fc = forecast(entries, horizon_days=10)
        self.assertEqual(fc["status"], "ok")
        self.assertEqual(fc["daily_mean_cents"], 1000)
        self.assertEqual(fc["daily_volatility_cents"], 0)
        # balance 12000 + 1000*10 = 22000, no volatility -> best==worst==projected
        self.assertEqual(fc["projected_balance_cents"], 22000)
        self.assertEqual(fc["best_cents"], 22000)
        self.assertEqual(fc["worst_cents"], 22000)

    def test_volatility_widens_band(self):
        entries = [
            _e("capital", 10000, ts=T_JAN1),
            _e("revenue", 2000, job_id="J1", ts=T_JAN1),
            _e("expense", 1000, job_id="J2", vendor="v", ts=T_JAN2),
        ]
        fc = forecast(entries, horizon_days=9)
        self.assertEqual(fc["status"], "ok")
        self.assertGreater(fc["best_cents"], fc["projected_balance_cents"])
        self.assertLess(fc["worst_cents"], fc["projected_balance_cents"])

    def test_nonpositive_horizon(self):
        entries = [_e("revenue", 100, ts=T_JAN1), _e("revenue", 100, ts=T_JAN2)]
        self.assertEqual(forecast(entries, horizon_days=0)["status"], "insufficient_history")


class TestReport(unittest.TestCase):
    def test_build_and_format(self):
        entries = [
            _e("capital", 10000, ts=0),
            _e("revenue", 5000, job_id="J1", ts=0),
            _e("expense", 1000, job_id="J1", vendor="nvidia-nemotron", ts=2 * DAY),
        ]
        report = build_report(entries, reserve_cents=2000, period="day")
        self.assertIn("income_statement", report)
        self.assertIn("unit_economics", report)
        self.assertIn("runway", report)
        self.assertIn("period_pnl", report)
        self.assertIn("forecast", report)
        self.assertEqual(report["period"], "day")
        text = format_report(report)
        self.assertIn("Financial Report", text)
        self.assertIn("Net profit", text)
        self.assertIn("Unit economics", text)
        self.assertIn("Net P&L by day", text)
        self.assertIn("Forecast", text)


if __name__ == "__main__":
    unittest.main()
