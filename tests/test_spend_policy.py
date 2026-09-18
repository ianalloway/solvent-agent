"""Tests for the extended spend policy: per-vendor caps, velocity, overrides.

Covers rules 4 and 5 of the guardrails (how spend is *distributed*), the
operator override file, and the `solvent guardrails` report built on them.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from solvent.guardrail_cmd import format_guardrails, gather
from solvent.guardrails import Guardrails, SpendPolicy, load_spend_policy
from solvent.treasury import LedgerEntry, Treasury


def _expense(vendor: str, cents: int, *, age_seconds: float = 0.0) -> LedgerEntry:
    return LedgerEntry(
        kind="expense",
        amount_cents=cents,
        memo=f"{vendor} spend",
        vendor=vendor,
        ts=time.time() - age_seconds,
    )


@pytest.fixture
def guard():
    treasury = MagicMock()
    treasury.balance_cents.return_value = 100_000
    treasury.entries = []
    return Guardrails(treasury, SpendPolicy())


def test_vendor_daily_cap_blocks_one_vendor_without_blocking_the_rest(guard):
    guard.t.entries = [_expense("nvidia-nemotron", 9_800)]

    denied = guard.evaluate(500, "nvidia-nemotron")
    assert not denied.allowed
    assert denied.rule == "vendor_daily_budget"
    assert denied.vendor_spent_24h_cents == 9_800

    # A different vendor still has its own full ceiling.
    assert guard.evaluate(500, "market-data-api").allowed


def test_vendor_cap_ignores_spend_outside_the_24h_window(guard):
    guard.t.entries = [_expense("nvidia-nemotron", 9_800, age_seconds=86_400 + 60)]
    assert guard.evaluate(500, "nvidia-nemotron").allowed


def test_vendor_daily_override_replaces_the_default_cap():
    treasury = MagicMock()
    treasury.balance_cents.return_value = 100_000
    treasury.entries = [_expense("web-search-api", 400)]
    policy = SpendPolicy(vendor_daily_overrides={"web-search-api": 500})
    guard = Guardrails(treasury, policy)

    assert policy.vendor_daily_cap_cents("web-search-api") == 500
    assert policy.vendor_daily_cap_cents("nvidia-nemotron") == policy.per_vendor_daily_cents
    assert guard.evaluate(100, "web-search-api").allowed
    assert not guard.evaluate(101, "web-search-api").allowed


def test_velocity_rule_stops_a_runaway_payment_loop():
    treasury = MagicMock()
    treasury.balance_cents.return_value = 100_000
    treasury.entries = [_expense("web-search-api", 1) for _ in range(5)]
    guard = Guardrails(treasury, SpendPolicy(max_txns_per_hour=5))

    denied = guard.evaluate(1, "web-search-api")
    assert not denied.allowed
    assert denied.rule == "spend_velocity"
    assert denied.txns_last_hour == 5


def test_velocity_window_is_one_rolling_hour():
    treasury = MagicMock()
    treasury.balance_cents.return_value = 100_000
    treasury.entries = [_expense("web-search-api", 1, age_seconds=3_700) for _ in range(9)]
    guard = Guardrails(treasury, SpendPolicy(max_txns_per_hour=5))
    assert guard.evaluate(1, "web-search-api").allowed


def test_total_daily_budget_is_checked_before_the_vendor_cap(guard):
    guard.policy = SpendPolicy(daily_budget_cents=1_000, per_vendor_daily_cents=100)
    guard.t.entries = []
    denied = guard.evaluate(2_000, "nvidia-nemotron")
    assert denied.rule == "daily_budget"


def test_decision_dict_carries_the_new_context(guard):
    payload = json.loads(json.dumps(guard.evaluate(100, "nvidia-nemotron").as_dict()))
    assert payload["vendor_spent_24h_cents"] == 0
    assert payload["txns_last_hour"] == 0


def test_vendor_exposure_reports_headroom_worst_first(guard):
    guard.t.entries = [_expense("nvidia-nemotron", 5_000), _expense("web-search-api", 100)]
    rows = guard.vendor_exposure()

    assert [r["vendor"] for r in rows][:2] == ["nvidia-nemotron", "web-search-api"]
    top = rows[0]
    assert top["spent_24h_cents"] == 5_000
    assert top["headroom_cents"] == top["cap_cents"] - 5_000
    assert top["used_pct"] == pytest.approx(50.0)
    # Every allowlisted vendor is accounted for, spent or not.
    assert {r["vendor"] for r in rows} == set(guard.policy.vendor_allowlist)


# --- operator override file -------------------------------------------------


def test_load_spend_policy_without_a_file_returns_defaults(tmp_path):
    assert load_spend_policy(tmp_path / "missing.json") == SpendPolicy()


def test_load_spend_policy_applies_overrides(tmp_path):
    path = tmp_path / "spend_policy.json"
    path.write_text(
        json.dumps(
            {
                "max_txn_cents": 250,
                "max_txns_per_hour": 4,
                "vendor_allowlist": ["nvidia-nemotron"],
                "vendor_daily_overrides": {"nvidia-nemotron": 900},
                "unknown_key": "ignored",
            }
        ),
        encoding="utf-8",
    )
    policy = load_spend_policy(path)

    assert policy.max_txn_cents == 250
    assert policy.max_txns_per_hour == 4
    assert policy.vendor_allowlist == ("nvidia-nemotron",)
    assert policy.vendor_daily_cap_cents("nvidia-nemotron") == 900
    # Untouched limits keep their defaults.
    assert policy.daily_budget_cents == SpendPolicy().daily_budget_cents


@pytest.mark.parametrize(
    "content",
    ["not json at all", json.dumps(["a", "list"]), json.dumps({"max_txn_cents": "lots"})],
)
def test_broken_override_file_never_widens_the_policy(tmp_path, content):
    path = tmp_path / "spend_policy.json"
    path.write_text(content, encoding="utf-8")
    assert load_spend_policy(path).max_txn_cents == SpendPolicy().max_txn_cents




def test_load_spend_policy_ignores_negative_vendor_overrides(tmp_path):
    """Negative per-vendor caps are malformed, not a way to widen/break policy."""
    path = tmp_path / "spend_policy.json"
    path.write_text(
        json.dumps(
            {
                "vendor_daily_overrides": {
                    "nvidia-nemotron": -1,
                    "web-search-api": 500,
                }
            }
        ),
        encoding="utf-8",
    )
    policy = load_spend_policy(path)
    assert "nvidia-nemotron" not in policy.vendor_daily_overrides
    assert policy.vendor_daily_overrides["web-search-api"] == 500
    # Negative override must not shrink the effective cap below the default either.
    assert policy.vendor_daily_cap_cents("nvidia-nemotron") == policy.per_vendor_daily_cents

# --- `solvent guardrails` report -------------------------------------------


def test_gather_and_format_report_the_live_policy(tmp_path):
    treasury = Treasury(path=tmp_path / "ledger.db")
    treasury.seed(10_000)
    treasury.spend(1_200, "inference", job_id="J1", vendor="nvidia-nemotron")

    data = gather(treasury)
    assert data["spent_24h_cents"] == 1_200
    assert data["txns_last_hour"] == 1
    assert data["daily_headroom_cents"] == data["policy"]["daily_budget_cents"] - 1_200
    assert data["recent_blocks"] == []

    rendered = format_guardrails(data)
    assert "SPEND GUARDRAILS" in rendered
    assert "nvidia-nemotron" in rendered
    assert "No spends blocked" in rendered


def test_gather_lists_blocked_spends(tmp_path):
    treasury = Treasury(path=tmp_path / "ledger.db")
    treasury.seed(10_000)
    treasury.record_event(
        "J9",
        "spend_blocked",
        {"vendor": "market-data-api", "amount": 4_000, "rule": "vendor_daily_budget"},
    )

    data = gather(treasury)
    assert len(data["recent_blocks"]) == 1
    assert data["recent_blocks"][0]["payload"]["rule"] == "vendor_daily_budget"
    assert "vendor_daily_budget" in format_guardrails(data)
