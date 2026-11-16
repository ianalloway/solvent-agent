"""End-of-run summary labels simulated money as simulated, and only then."""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import Mock, patch

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


class SessionSummaryTest(unittest.TestCase):
    def test_both_modes_render_one_summary_and_dashboard(self):
        for mode in ("batch", "interactive"):
            for stripe_live in (False, True):
                with self.subTest(mode=mode, stripe_live=stripe_live):
                    snap = _snap(14_000)
                    agent = Mock()
                    agent.t.snapshot.return_value = snap
                    agent.t.capital_cents.return_value = snap["capital_cents"]
                    agent.t.balance_cents.return_value = snap["balance_cents"]
                    agent.stripe.live = stripe_live
                    agent.log = [{"stage": "booked", "job_id": "summary-test"}]
                    buf = io.StringIO()

                    with (
                        patch.object(cli, "Solvent", return_value=agent) as make_agent,
                        patch.object(cli, "show_spinner"),
                        patch.object(cli, "print_results", wraps=cli.print_results) as summary,
                        patch.object(
                            cli.dashboard, "render", return_value="test-dashboard.html"
                        ) as render,
                        patch("builtins.input", side_effect=["Test topic", "50", "n"]),
                        contextlib.redirect_stdout(buf),
                    ):
                        if mode == "batch":
                            cli.run_batch_demo(seed_cents=20_000, fresh=False)
                        else:
                            cli.run_interactive_mode(seed_cents=20_000, fresh=False)

                    make_agent.assert_called_once_with(
                        seed_cents=20_000, fresh=False, on_event=cli.print_event
                    )
                    self.assertEqual(
                        agent.handle_job.call_count, len(cli.SAMPLE_JOBS) if mode == "batch" else 1
                    )
                    agent.t.snapshot.assert_called_once_with()
                    summary.assert_called_once_with(snap, simulated=not stripe_live)
                    render.assert_called_once_with(snap, agent.log)
                    expected_summary = _render(snap, simulated=not stripe_live)
                    expected_footer = f"\n   Dashboard: {cli.C_BLUE}test-dashboard.html{cli.C_RESET}\n{cli.BAR}\n\n"
                    self.assertTrue(buf.getvalue().endswith(expected_summary + expected_footer))


if __name__ == "__main__":
    unittest.main()
