"""Unit tests for the SOLVENT Treasury ledger (solvent.treasury).

These cover the core economic-memory contract: how revenue, expense, and
capital entries combine into balances, profit, per-job PnL, and snapshots.
"""

from __future__ import annotations

import pytest

from solvent.treasury import LedgerEntry, Treasury


@pytest.fixture
def treasury(tmp_path):
    return Treasury(path=tmp_path / "ledger.db")


def test_seed_capital_records_positive_balance(treasury):
    treasury.seed(50_000)
    assert treasury.balance_cents() == 50_000
    assert treasury.capital_cents() == 50_000
    assert treasury.revenue_cents() == 0
    assert treasury.expense_cents() == 0


def test_earn_and_spend_update_ledger_totals(treasury):
    treasury.seed(10_000)
    treasury.earn(4_000, "job A", job_id="job_a")
    treasury.spend(1_000, "api call", job_id="job_a")

    assert treasury.revenue_cents() == 4_000
    assert treasury.expense_cents() == 1_000
    assert treasury.net_profit_cents() == 3_000
    # balance = capital + revenue - expense
    assert treasury.balance_cents() == 10_000 + 4_000 - 1_000
    assert treasury.margin_pct() == 75.0  # 3000 / 4000 * 100


def test_ledger_entry_signed_cents_carries_sign_by_kind():
    rev = LedgerEntry(kind="revenue", amount_cents=500, memo="r")
    cap = LedgerEntry(kind="capital", amount_cents=500, memo="c")
    exp = LedgerEntry(kind="expense", amount_cents=500, memo="e")
    assert rev.signed_cents() == 500
    assert cap.signed_cents() == 500
    assert exp.signed_cents() == -500


def test_job_pnl_is_revenue_minus_expense_for_that_job(treasury):
    treasury.earn(2_000, "deliverable", job_id="j1")
    treasury.spend(800, "compute", job_id="j1")
    treasury.earn(1_000, "deliverable", job_id="j2")

    assert treasury.job_pnl_cents("j1") == 1_200
    assert treasury.job_pnl_cents("j2") == 1_000
    assert treasury.job_pnl_cents("no-such-job") == 0


def test_snapshot_reports_consistent_ledger_summary(treasury):
    treasury.seed(10_000)
    treasury.earn(2_000, "sale", job_id="j")
    treasury.spend(500, "cost", job_id="j")

    snap = treasury.snapshot()
    assert snap["balance_cents"] == 10_000 + 2_000 - 500
    assert snap["capital_cents"] == 10_000
    assert snap["revenue_cents"] == 2_000
    assert snap["expense_cents"] == 500
    assert snap["net_profit_cents"] == 1_500
    assert snap["margin_pct"] == 75.0
    assert len(snap["entries"]) == 3
    assert all("id" in e and "kind" in e for e in snap["entries"])


def test_reset_clears_all_ledger_entries(treasury):
    treasury.seed(10_000)
    treasury.earn(1_000, "sale")
    treasury.reset()
    assert treasury.balance_cents() == 0
    assert treasury.entries == []
    assert treasury.snapshot()["entries"] == []


def test_margin_pct_is_zero_when_no_revenue(treasury):
    treasury.seed(10_000)
    assert treasury.revenue_cents() == 0
    assert treasury.margin_pct() == 0.0
