"""Per-job budget envelopes and the human-approval hook (rules 8 and 9)."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from solvent.guardrails import (
    ApprovalRequest,
    GuardrailError,
    Guardrails,
    SpendPolicy,
    hold_for_approval,
    load_spend_policy,
)
from solvent.treasury import REFUND_VENDOR, LedgerEntry

VENDOR = "nvidia-nemotron"


def _spent(job_id: str, cents: int, vendor: str = VENDOR) -> LedgerEntry:
    return LedgerEntry(
        kind="expense", amount_cents=cents, memo="x", job_id=job_id, vendor=vendor, ts=time.time()
    )


def _guard(entries=(), approver=None, **policy) -> Guardrails:
    treasury = MagicMock()
    treasury.balance_cents.return_value = 1_000_000
    treasury.entries = list(entries)
    return Guardrails(treasury, SpendPolicy(**policy), approver=approver)


def test_job_budget_exhausted_fails_closed():
    g = _guard([_spent("job-1", 3_000)], job_budget_cents=3_000)
    d = g.evaluate(1, VENDOR, job_id="job-1")
    assert not d.allowed
    assert d.rule == "job_budget"
    assert "budget exhausted" in d.reason
    assert d.job_spent_cents == 3_000
    with pytest.raises(GuardrailError, match="budget exhausted"):
        g.check_spend(1, VENDOR, job_id="job-1")


def test_exact_cap_boundary_is_allowed_one_cent_over_is_not():
    g = _guard([_spent("job-1", 2_000)], job_budget_cents=3_000)
    assert g.evaluate(1_000, VENDOR, job_id="job-1").allowed
    assert g.evaluate(1_001, VENDOR, job_id="job-1").rule == "job_budget"


def test_job_budget_is_per_job_and_ignores_refunds():
    g = _guard(
        [_spent("job-1", 3_000), _spent("job-2", 2_900, vendor=REFUND_VENDOR)],
        job_budget_cents=3_000,
    )
    assert g.evaluate(500, VENDOR, job_id="job-2").allowed
    assert not g.evaluate(500, VENDOR, job_id="job-1").allowed


def test_approval_held_by_default():
    g = _guard(approval_threshold_cents=1_000)
    d = g.evaluate(1_001, VENDOR, job_id="job-1")
    assert not d.allowed
    assert d.rule == "approval_pending"
    assert d.pending_approval
    # At or below the threshold needs no approval.
    assert g.evaluate(1_000, VENDOR, job_id="job-1").allowed
    assert hold_for_approval(ApprovalRequest(1, VENDOR, None, 0)) == "held"


def test_approval_approved_executes():
    seen: list[ApprovalRequest] = []

    def approver(req: ApprovalRequest):
        seen.append(req)
        return "approved"

    g = _guard(approver=approver, approval_threshold_cents=1_000)
    assert g.evaluate(2_000, VENDOR, job_id="job-1").allowed
    assert seen == [ApprovalRequest(2_000, VENDOR, "job-1", 1_000)]


def test_approval_denied():
    g = _guard(approver=lambda req: "denied", approval_threshold_cents=1_000)
    d = g.evaluate(2_000, VENDOR)
    assert not d.allowed
    assert d.rule == "approval_denied"
    assert not d.pending_approval


def test_approver_that_raises_or_returns_garbage_holds():
    def boom(req):
        raise RuntimeError("approver down")

    assert _guard(approver=boom, approval_threshold_cents=0).evaluate(1, VENDOR).rule == (
        "approval_pending"
    )
    assert _guard(approver=lambda r: "yes", approval_threshold_cents=0).evaluate(
        1, VENDOR
    ).rule == ("approval_pending")


def test_approval_cannot_override_hard_rules():
    g = _guard(
        [_spent("job-1", 3_000)],
        approver=lambda r: "approved",
        job_budget_cents=3_000,
        approval_threshold_cents=0,
    )
    assert g.evaluate(100, VENDOR, job_id="job-1").rule == "job_budget"


def test_policy_file_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SOLVENT_JOB_BUDGET_CENTS", raising=False)
    monkeypatch.delenv("SOLVENT_APPROVAL_THRESHOLD_CENTS", raising=False)
    path = tmp_path / "spend_policy.json"
    path.write_text(json.dumps({"job_budget_cents": 4_000, "approval_threshold_cents": 2_500}))
    p = load_spend_policy(path)
    assert (p.job_budget_cents, p.approval_threshold_cents) == (4_000, 2_500)

    monkeypatch.setenv("SOLVENT_JOB_BUDGET_CENTS", "1200")
    monkeypatch.setenv("SOLVENT_APPROVAL_THRESHOLD_CENTS", "300")
    p = load_spend_policy(path)
    assert (p.job_budget_cents, p.approval_threshold_cents) == (1_200, 300)

    monkeypatch.setenv("SOLVENT_APPROVAL_THRESHOLD_CENTS", "not-a-number")
    assert load_spend_policy(path).approval_threshold_cents == 2_500


def test_defaults_without_config(tmp_path, monkeypatch):
    monkeypatch.delenv("SOLVENT_JOB_BUDGET_CENTS", raising=False)
    monkeypatch.delenv("SOLVENT_APPROVAL_THRESHOLD_CENTS", raising=False)
    p = load_spend_policy(tmp_path / "missing.json")
    assert p.job_budget_cents == 15_000
    assert p.approval_threshold_cents is None


def test_guardrails_report_shows_job_budget_and_approval(tmp_path, monkeypatch):
    from solvent.guardrail_cmd import format_guardrails, gather
    from solvent.treasury import Treasury

    monkeypatch.delenv("SOLVENT_JOB_BUDGET_CENTS", raising=False)
    monkeypatch.delenv("SOLVENT_APPROVAL_THRESHOLD_CENTS", raising=False)
    t = Treasury(path=tmp_path / "l.db")

    default = format_guardrails(gather(t, SpendPolicy()))
    assert "Per-job budget" in default and "job_budget_cents" in default
    assert "max $150.00 per job" in default
    assert "Human approval" in default
    assert "off (approval_threshold_cents unset)" in default

    custom = format_guardrails(
        gather(t, SpendPolicy(job_budget_cents=0, approval_threshold_cents=2_500))
    )
    assert "off (job_budget_cents=0)" in custom
    assert "spends above $25.00 held for approval" in custom
