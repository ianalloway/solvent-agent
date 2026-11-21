"""The spend guard must never authorize spend against a cancelled/expired job.

Regression: a job cancelled by the operator could still have spend (e.g. a
$2.40 inference charge) approved and booked against it, because the guard
evaluated the ledger and policy caps but never the job's own lifecycle state.
"""

from __future__ import annotations

import pytest

from solvent import spending
from solvent.guardrails import GuardrailError, Guardrails, SpendPolicy
from solvent.job_cmd import cmd_cancel
from solvent.treasury import Treasury

CANCELLED_SPEND_CENTS = 240  # the $2.40 reported against a cancelled job


@pytest.fixture
def treasury(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    t = Treasury(tmp_path / "treasury.db")
    t.seed(10_000)
    return t


def _expenses(t, job_id):
    return [e for e in t.entries if e.kind == "expense" and e.job_id == job_id]


def test_cancelled_job_spend_is_denied(treasury):
    treasury.upsert_job("J-cancel", "in_progress")
    cmd_cancel(treasury, "J-cancel")
    guard = Guardrails(treasury, SpendPolicy())

    decision = guard.evaluate(CANCELLED_SPEND_CENTS, "nvidia-nemotron", job_id="J-cancel")

    assert not decision.allowed
    assert decision.rule == "job_terminal"
    assert "cancelled" in decision.reason
    with pytest.raises(GuardrailError):
        guard.check_spend(CANCELLED_SPEND_CENTS, "nvidia-nemotron", job_id="J-cancel")


def test_cancelled_job_cannot_reserve_or_book_live_spend(treasury):
    treasury.upsert_job("J-cancel", "in_progress")
    cmd_cancel(treasury, "J-cancel")
    guard = Guardrails(treasury, SpendPolicy())

    with pytest.raises(GuardrailError):
        guard.reserve(CANCELLED_SPEND_CENTS, "nvidia-nemotron", "Live inference", job_id="J-cancel")
    with spending.scope(guard, "J-cancel", 1_000), pytest.raises(GuardrailError):
        spending._authorize(CANCELLED_SPEND_CENTS, "nvidia-nemotron", "Live inference")

    assert treasury.reserved_spends() == []
    assert _expenses(treasury, "J-cancel") == []
    assert treasury.get_job("J-cancel")["status"] == "cancelled"


def test_expired_job_spend_is_denied(treasury):
    treasury.upsert_job("J-exp", "expired")
    decision = Guardrails(treasury, SpendPolicy()).evaluate(
        CANCELLED_SPEND_CENTS, "nvidia-nemotron", job_id="J-exp"
    )
    assert not decision.allowed
    assert decision.rule == "job_terminal"


@pytest.mark.parametrize("status", ["in_progress", "paid", "failed"])
def test_live_or_retryable_job_spend_is_still_allowed(treasury, status):
    treasury.upsert_job("J-live", status)
    decision = Guardrails(treasury, SpendPolicy()).evaluate(
        CANCELLED_SPEND_CENTS, "nvidia-nemotron", job_id="J-live"
    )
    assert decision.allowed, decision.reason


def test_unknown_or_unscoped_job_is_unaffected(treasury):
    guard = Guardrails(treasury, SpendPolicy())
    assert guard.evaluate(CANCELLED_SPEND_CENTS, "nvidia-nemotron", job_id="no-such-job").allowed
    assert guard.evaluate(CANCELLED_SPEND_CENTS, "nvidia-nemotron").allowed
