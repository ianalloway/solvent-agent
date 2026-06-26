"""Tests for the dashboard Financial Statement panel."""

import unittest

from solvent import dashboard
from solvent.finance import build_report
from solvent.treasury import LedgerEntry


class TestLedgerFromSnapshot(unittest.TestCase):
    def test_tolerates_partial_and_bad_rows(self):
        rows = [
            {"kind": "revenue", "amount_cents": 5000, "memo": "x", "ts": 1},
            {"kind": "expense", "amount_cents": 1000},          # missing memo/ts
            {"kind": "capital"},                                  # missing amount
            "not-a-dict",                                          # junk
            {"kind": "expense", "amount_cents": "bad"},          # uncastable
        ]
        entries = dashboard._ledger_from_snapshot(rows)
        self.assertTrue(all(isinstance(e, LedgerEntry) for e in entries))
        # the junk string is skipped; the rest are coerced
        self.assertGreaterEqual(len(entries), 3)


class TestFinancialsHtml(unittest.TestCase):
    def _report(self):
        entries = [
            LedgerEntry("capital", 10000, "seed", ts=0),
            LedgerEntry("revenue", 5000, "job", job_id="J1", ts=0),
            LedgerEntry("expense", 1000, "vendor", job_id="J1", vendor="nvidia", ts=0),
        ]
        return build_report(entries)

    def test_contains_key_lines(self):
        html = dashboard._financials_html(self._report())
        self.assertIn("Revenue", html)
        self.assertIn("Net profit", html)
        self.assertIn("Avg profit / job", html)
        self.assertIn("Runway", html)

    def test_escapes_period_and_uses_color_classes(self):
        html = dashboard._financials_html(self._report())
        self.assertIn("green-txt", html)  # positive net profit
        self.assertNotIn("<script", html)

    def test_forecast_rendered_when_enough_history(self):
        from solvent.treasury import LedgerEntry
        DAY = 86_400.0
        entries = [
            LedgerEntry("capital", 10000, "seed", ts=0),
            LedgerEntry("revenue", 1000, "j1", job_id="J1", ts=0),
            LedgerEntry("revenue", 1000, "j2", job_id="J2", ts=DAY),
        ]
        html = dashboard._financials_html(build_report(entries, horizon_days=10))
        self.assertIn("Projected balance", html)
        self.assertIn("Forecast best / worst", html)


class TestRenderIncludesPanel(unittest.TestCase):
    def test_render_emits_financials_panel(self):
        import tempfile
        from pathlib import Path

        snapshot = {
            "capital_cents": 10000, "balance_cents": 14000,
            "revenue_cents": 5000, "expense_cents": 1000,
            "net_profit_cents": 4000, "margin_pct": 80.0,
            "entries": [
                {"kind": "capital", "amount_cents": 10000, "memo": "seed", "ts": 0},
                {"kind": "revenue", "amount_cents": 5000, "memo": "j", "job_id": "J1", "ts": 0},
                {"kind": "expense", "amount_cents": 1000, "memo": "v", "vendor": "nvidia", "job_id": "J1", "ts": 0},
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "treasury_dashboard.html"
            orig = dashboard.OUT
            dashboard.OUT = out
            try:
                path = dashboard.render(snapshot, [])
            finally:
                dashboard.OUT = orig
            html = Path(path).read_text()
        self.assertIn("Financial Statement", html)
        self.assertIn('id="financials-panel"', html)


if __name__ == "__main__":
    unittest.main()
