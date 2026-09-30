"""Tests for the analytic throughput ceiling (solvent.capacity)."""

from __future__ import annotations

import pytest

from solvent.capacity import (
    REFERENCE_JOB,
    binding_hint,
    format_capacity,
    limits,
    report,
    unit_costs,
)
from solvent.guardrails import SpendPolicy
from solvent.pricing import estimate_cost
from solvent.treasury import REFUND_VENDOR, Treasury


@pytest.fixture
def treasury(tmp_path):
    t = Treasury(path=tmp_path / "ledger.db")
    t.seed(100_000)
    return t


def _fulfilled(treasury, job_id, spends):
    for vendor, cents in spends.items():
        treasury.spend(cents, f"{vendor} for {job_id}", job_id=job_id, vendor=vendor)


def test_unit_costs_fall_back_to_the_pricing_model(treasury):
    costs = unit_costs(treasury)
    expected, _ = estimate_cost(REFERENCE_JOB)
    assert costs["source"] == "estimated"
    assert costs["cost_per_job_cents"] == expected
    assert costs["sample_jobs"] == 0


def test_unit_costs_use_realized_spend_when_there_is_history(treasury):
    _fulfilled(treasury, "J1", {"nvidia-nemotron": 300, "pdf-render-saas": 40})
    _fulfilled(treasury, "J2", {"nvidia-nemotron": 500, "pdf-render-saas": 40})

    costs = unit_costs(treasury)
    assert costs["source"] == "realized"
    assert costs["sample_jobs"] == 2
    assert costs["cost_per_job_cents"] == 440  # (340 + 540) / 2
    assert costs["per_vendor_cents"]["nvidia-nemotron"] == 400
    assert costs["payments_per_job"] == 2.0


def test_refunds_are_not_part_of_the_unit_cost(treasury):
    _fulfilled(treasury, "J1", {"nvidia-nemotron": 300})
    treasury.spend(9_900, "refund for J1", job_id="J1", vendor=REFUND_VENDOR)
    assert unit_costs(treasury)["cost_per_job_cents"] == 300


def test_each_rule_yields_a_ceiling():
    costs = {
        "cost_per_job_cents": 1_000,
        "per_vendor_cents": {"nvidia-nemotron": 800, "pdf-render-saas": 200},
        "payments_per_job": 2.0,
    }
    found = {
        limit.rule: limit.jobs_per_day
        for limit in limits(costs, SpendPolicy(), balance_cents=100_000)
    }
    assert found["daily_budget"] == pytest.approx(25.0)  # $250 / $10
    assert found["spend_velocity"] == pytest.approx(60 * 24 / 2)
    assert found["min_reserve"] == pytest.approx((100_000 - 2_000) / 1_000)
    assert found["vendor_daily_budget:nvidia-nemotron"] == pytest.approx(12.5)


def test_the_tightest_rule_binds():
    costs = {
        "cost_per_job_cents": 1_000,
        "per_vendor_cents": {"nvidia-nemotron": 800, "pdf-render-saas": 200},
        "payments_per_job": 2.0,
    }
    found = limits(costs, SpendPolicy(), balance_cents=100_000)
    assert found[0].rule == "vendor_daily_budget:nvidia-nemotron"
    assert found == sorted(found, key=lambda limit: limit.jobs_per_day)


def test_a_per_job_payment_over_the_transaction_cap_blocks_everything():
    costs = {
        "cost_per_job_cents": 9_000,
        "per_vendor_cents": {"nvidia-nemotron": 9_000},
        "payments_per_job": 1.0,
    }
    found = limits(costs, SpendPolicy(max_txn_cents=5_000), balance_cents=1_000_000)
    assert found[0].rule == "max_txn_cap:nvidia-nemotron"
    assert found[0].jobs_per_day == 0.0
    assert "every job blocks" in found[0].detail


def test_an_empty_treasury_is_bound_by_cash(tmp_path):
    empty = Treasury(path=tmp_path / "empty.db")
    data = report(empty)
    assert data["binding_rule"] == "min_reserve"
    assert data["ceiling_jobs_per_day"] == 0.0
    assert "capital" in data["binding_hint"]


def test_report_counts_todays_headroom(treasury):
    _fulfilled(treasury, "J1", {"nvidia-nemotron": 500, "pdf-render-saas": 40})
    data = report(treasury, policy=SpendPolicy())

    assert data["spent_24h_cents"] == 540
    assert data["budget_headroom_cents"] == SpendPolicy().daily_budget_cents - 540
    assert data["jobs_left_today"] == pytest.approx(
        data["budget_headroom_cents"] / data["unit_costs"]["cost_per_job_cents"], abs=0.1
    )


@pytest.mark.parametrize(
    "rule,expected",
    [
        ("min_reserve", "capital"),
        ("daily_budget", "daily_budget_cents"),
        ("spend_velocity", "max_txns_per_hour"),
        ("vendor_daily_budget:market-data-api", "vendor_daily_overrides"),
        ("max_txn_cap:nvidia-nemotron", "max_txn_cents"),
    ],
)
def test_every_binding_rule_says_what_to_change(rule, expected):
    assert expected in binding_hint(rule)


def test_format_marks_the_binding_rule(treasury):
    _fulfilled(treasury, "J1", {"nvidia-nemotron": 500})
    rendered = format_capacity(report(treasury))
    assert "THROUGHPUT CAPACITY" in rendered
    assert "←" in rendered
    assert "jobs/day" in rendered
