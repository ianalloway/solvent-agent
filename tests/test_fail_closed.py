"""Spends must deny (fail closed) when state, counters or policy are unavailable,
and amounts must be integer cents everywhere money crosses a boundary."""

from __future__ import annotations

import json

import pytest

from solvent.guardrails import Guardrails, SpendPolicy, load_spend_policy


class _Ledger:
    def __init__(self, balance=100_000):
        self._balance = balance
        self.entries = []

    def balance_cents(self):
        return self._balance


class _BrokenState:
    """Treasury whose balance (state store) is unreadable."""

    entries: list = []

    def balance_cents(self):
        raise OSError("treasury unavailable")


class _BrokenCounters:
    """Treasury whose ledger (spend counters) is unreadable."""

    @property
    def entries(self):
        raise OSError("ledger unavailable")

    def balance_cents(self):
        return 100_000


VENDOR = "web-search-api"


def test_baseline_spend_allowed():
    assert Guardrails(_Ledger(), SpendPolicy()).evaluate(100, VENDOR).allowed


@pytest.mark.parametrize("treasury", [None, _BrokenState(), _BrokenCounters()])
def test_missing_or_broken_state_denies(treasury):
    d = Guardrails(treasury, SpendPolicy()).evaluate(100, VENDOR)
    assert not d.allowed
    assert d.rule == "state_unavailable"


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[1, 2]",
        json.dumps({"max_txn_cents": "lots"}),
        json.dumps({"daily_budget_cents": -5}),
        json.dumps({"job_budget_cents": 0.5}),  # truncated to 0 used to DISABLE the cap
        json.dumps({"max_txn_cents": 12.5}),  # dollars-looking / fractional cents
        json.dumps({"vendor_daily_overrides": {"web-search-api": "x"}}),
        json.dumps({"vendor_allowlist": "web-search-api"}),
        json.dumps({"approval_threshold_cents": float("nan")}, allow_nan=True),
    ],
)
def test_invalid_policy_file_denies(tmp_path, content):
    p = tmp_path / "spend_policy.json"
    p.write_text(content, encoding="utf-8")
    policy = load_spend_policy(p)
    assert policy.invalid_reason
    d = Guardrails(_Ledger(), policy).evaluate(100, VENDOR)
    assert not d.allowed
    assert d.rule == "policy_unavailable"


def test_unreadable_policy_file_denies(tmp_path):
    p = tmp_path / "spend_policy.json"
    p.mkdir()  # exists but cannot be read as a file
    policy = load_spend_policy(p)
    d = Guardrails(_Ledger(), policy).evaluate(100, VENDOR)
    assert not d.allowed and d.rule == "policy_unavailable"


def test_valid_policy_file_still_applies(tmp_path):
    p = tmp_path / "spend_policy.json"
    p.write_text(json.dumps({"max_txn_cents": 200, "job_budget_cents": 1000.0}), encoding="utf-8")
    policy = load_spend_policy(p)
    assert policy.invalid_reason is None
    assert policy.max_txn_cents == 200 and policy.job_budget_cents == 1000
    g = Guardrails(_Ledger(), policy)
    assert g.evaluate(100, VENDOR).allowed
    assert g.evaluate(300, VENDOR).rule == "max_txn_cap"


def test_missing_policy_file_uses_defaults(tmp_path):
    policy = load_spend_policy(tmp_path / "absent.json")
    assert policy.invalid_reason is None
    assert Guardrails(_Ledger(), policy).evaluate(100, VENDOR).allowed


@pytest.mark.parametrize("amount", [4.99, 100.0, "100", True, None])
def test_non_integer_cents_amount_denied(amount):
    d = Guardrails(_Ledger(), SpendPolicy()).evaluate(amount, VENDOR)
    assert not d.allowed
    assert d.rule == "amount_type"


def test_broken_approver_still_fails_closed():
    def boom(_req):
        raise RuntimeError("approver down")

    g = Guardrails(_Ledger(), SpendPolicy(approval_threshold_cents=50), approver=boom)
    d = g.evaluate(100, VENDOR)
    assert not d.allowed and d.rule == "approval_pending"


@pytest.mark.parametrize("amount", [19.99, "1999", True, 0, -5])
def test_stripe_checkout_rejects_non_cents(monkeypatch, amount):
    monkeypatch.setenv("SOLVENT_FORCE_STRIPE_SIMULATE", "1")
    from solvent.stripe_client import StripeClient

    with pytest.raises(ValueError):
        StripeClient().create_checkout_session(
            "j1", amount, "a@example.com", "x", "https://e/s", "https://e/c"
        )
