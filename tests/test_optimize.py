"""Tests for the policy search (solvent.optimize)."""

from __future__ import annotations

import pytest

from solvent.guardrails import SpendPolicy
from solvent.optimize import (
    DEFAULT_MAX_BELOW_RESERVE_PCT,
    format_sweep,
    recommend,
    sweep,
)

SMALL = dict(trials=6, jobs=5, seed=3)


def test_sweep_covers_every_cell_of_the_grid():
    rows = sweep((25.0, 45.0), (1_000, 2_000), **SMALL)
    assert len(rows) == 4
    assert {(r["margin_floor_pct"], r["min_price_cents"]) for r in rows} == {
        (25.0, 1_000),
        (25.0, 2_000),
        (45.0, 1_000),
        (45.0, 2_000),
    }


def test_repeating_the_same_sweep_is_deterministic():
    """Repeatability is separate from matching demand across different cells."""
    first = sweep((35.0,), (1_500,), **SMALL)
    second = sweep((35.0,), (1_500,), **SMALL)
    assert first == second


def test_a_higher_minimum_order_accepts_less_work():
    rows = sweep((35.0,), (1_000, 5_000), **SMALL)
    cheap, dear = rows
    assert dear["acceptance_rate_pct"] < cheap["acceptance_rate_pct"]


def test_a_punishing_floor_accepts_nothing():
    row = sweep((99.9,), (1_500,), **SMALL)[0]
    assert row["acceptance_rate_pct"] == 0.0


def test_recommendation_maximises_net_inside_the_risk_budget():
    rows = [
        {
            "margin_floor_pct": 15.0,
            "min_price_cents": 1_000,
            "mean_net_cents": 900,
            "below_reserve_pct": 40.0,
            "acceptance_rate_pct": 99.0,
            "balance_p10_cents": -100,
        },
        {
            "margin_floor_pct": 35.0,
            "min_price_cents": 1_500,
            "mean_net_cents": 500,
            "below_reserve_pct": 1.0,
            "acceptance_rate_pct": 80.0,
            "balance_p10_cents": 5_000,
        },
    ]
    best = recommend(rows)
    assert best["margin_floor_pct"] == 35.0  # the reckless cell earns more and is excluded
    assert best["within_risk_budget"] is True


def test_ties_go_to_the_more_conservative_policy():
    rows = [
        {
            "margin_floor_pct": 15.0,
            "min_price_cents": 1_500,
            "mean_net_cents": 500,
            "below_reserve_pct": 0.0,
            "acceptance_rate_pct": 90.0,
            "balance_p10_cents": 100,
        },
        {
            "margin_floor_pct": 55.0,
            "min_price_cents": 1_500,
            "mean_net_cents": 500,
            "below_reserve_pct": 0.0,
            "acceptance_rate_pct": 90.0,
            "balance_p10_cents": 100,
        },
    ]
    assert recommend(rows)["margin_floor_pct"] == 55.0


def test_when_nothing_is_safe_the_safest_cell_is_flagged():
    rows = [
        {
            "margin_floor_pct": 15.0,
            "min_price_cents": 1_000,
            "mean_net_cents": 900,
            "below_reserve_pct": 40.0,
            "acceptance_rate_pct": 99.0,
            "balance_p10_cents": -100,
        },
        {
            "margin_floor_pct": 55.0,
            "min_price_cents": 2_500,
            "mean_net_cents": 100,
            "below_reserve_pct": 12.0,
            "acceptance_rate_pct": 20.0,
            "balance_p10_cents": 50,
        },
    ]
    best = recommend(rows, max_below_reserve_pct=DEFAULT_MAX_BELOW_RESERVE_PCT)
    assert best["within_risk_budget"] is False
    assert best["below_reserve_pct"] == 12.0
    assert "safest" in format_sweep(rows, best)


def test_recommend_on_an_empty_grid():
    assert recommend([]) is None
    assert "grid was empty" in format_sweep([], None)


def test_a_vendor_price_shock_changes_the_answer():
    calm = recommend(sweep((25.0, 45.0), (1_000, 2_500), **SMALL))
    shocked = recommend(sweep((25.0, 45.0), (1_000, 2_500), cost_multiplier=8.0, **SMALL))
    assert calm is not None and shocked is not None
    assert shocked["mean_net_cents"] < calm["mean_net_cents"]


def test_spend_policy_is_honoured_by_the_search():
    tight = sweep((35.0,), (1_500,), spend_policy=SpendPolicy(max_txn_cents=1), **SMALL)[0]
    normal = sweep((35.0,), (1_500,), **SMALL)[0]
    assert tight["mean_net_cents"] < normal["mean_net_cents"]


def test_format_marks_the_recommended_cell_and_compares_to_baseline():
    rows = sweep((25.0, 45.0), (1_500,), **SMALL)
    best = recommend(rows)
    rendered = format_sweep(rows, best, baseline=rows[0])
    assert "POLICY SEARCH" in rendered
    assert "←" in rendered
    assert "vs the policy in force" in rendered


@pytest.mark.parametrize("floors,prices", [((35.0,), (1_500,)), ((25.0, 35.0), (1_000,))])
def test_every_row_carries_the_fields_the_report_prints(floors, prices):
    for row in sweep(floors, prices, **SMALL):
        for key in (
            "acceptance_rate_pct",
            "mean_net_cents",
            "balance_p10_cents",
            "below_reserve_pct",
            "insolvent_pct",
        ):
            assert key in row
