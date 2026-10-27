"""End-of-run summary labels simulated money as simulated, and only then."""

from __future__ import annotations

import contextlib
import io
import unittest

from solvent import cli


def _snap(balance: int) -> dict:
    return {
        "revenue_cents": 5_000,
        "expense_cents": 1_000,
        "net_profit_cents": balance - 10_000,
        "margin_pct": 20,
        "balance_cents": balance,
        "capital_cents": 10_000,
    }


def _render(snap: dict, simulated: bool) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.print_results(snap, simulated=simulated)
    return buf.getvalue()


class SummaryLabelTest(unittest.TestCase):
    def test_simulated_growth_and_balance_are_labelled(self):
        out = _render(_snap(14_000), simulated=True)
        self.assertIn("grew its own simulated treasury by", out)
        self.assertIn("Cash balance (simulated)", out)

    def test_simulated_loss_and_even_are_labelled(self):
        self.assertIn("ran at a simulated loss of", _render(_snap(9_000), simulated=True))
        self.assertIn("broke perfectly even (simulated).", _render(_snap(10_000), simulated=True))

    def test_live_results_are_not_labelled(self):
        for bal in (14_000, 9_000, 10_000):
            out = _render(_snap(bal), simulated=False)
            self.assertNotIn("simulated", out)
        self.assertIn("grew its own treasury by", _render(_snap(14_000), simulated=False))


if __name__ == "__main__":
    unittest.main()
