"""Malformed policy files must fall back to defaults, never crash with a traceback."""

from __future__ import annotations

import pytest

from solvent.checkout import CheckoutPolicy, load_policy
from solvent.guardrails import SpendPolicy, load_spend_policy

BAD_SPEND = [
    '{"max_txn_cents": 1e400}',
    '{"max_txn_cents": Infinity}',
    '{"daily_budget_cents": -Infinity}',
    '{"max_txn_cents": NaN}',
    '{"vendor_daily_overrides": {"web-search-api": 1e400}}',
    '{"vendor_daily_overrides": {"web-search-api": NaN}}',
    "{not json",
    "[1, 2]",
]


@pytest.mark.parametrize("text", BAD_SPEND)
def test_spend_policy_bad_values_fall_back(tmp_path, text):
    p = tmp_path / "spend_policy.json"
    p.write_text(text, encoding="utf-8")
    policy = load_spend_policy(p)
    default = SpendPolicy()
    assert policy.max_txn_cents == default.max_txn_cents
    assert policy.daily_budget_cents == default.daily_budget_cents
    assert all(isinstance(v, int) for v in policy.vendor_daily_overrides.values())


def test_spend_policy_invalid_utf8_falls_back(tmp_path):
    p = tmp_path / "spend_policy.json"
    p.write_bytes(b"\xff\xfe{")
    assert load_spend_policy(p).max_txn_cents == SpendPolicy().max_txn_cents


def test_spend_policy_valid_override_still_applies(tmp_path):
    p = tmp_path / "spend_policy.json"
    p.write_text('{"max_txn_cents": 123, "vendor_daily_overrides": {"a": 50}}', encoding="utf-8")
    policy = load_spend_policy(p)
    assert policy.max_txn_cents == 123
    assert policy.vendor_daily_overrides == {"a": 50}


@pytest.mark.parametrize(
    "payload",
    [
        b'{"max_reminders": 1e400}',
        b'{"expire_after_hours": Infinity}',
        b'{"max_reminders": NaN}',
        b"\xff{",
    ],
)
def test_checkout_policy_bad_values_fall_back(tmp_path, payload):
    p = tmp_path / "checkout_policy.json"
    p.write_bytes(payload)
    assert load_policy(p) == CheckoutPolicy()
