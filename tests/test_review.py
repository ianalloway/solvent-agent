"""Tests for the operator review queue (solvent.review)."""

from __future__ import annotations

import json

import pytest

from solvent.intake import IntakePolicy, screen_job
from solvent.review import (
    APPROVAL_FLAG,
    approve,
    format_queue,
    job_payload,
    pending,
    reject,
)
from solvent.treasury import Treasury

BIG_JOB = {
    "id": "BIG",
    "topic": "Whole-market teardown",
    "customer_email": "whale@fund.example",
    "budget_cents": 500_000,
}


@pytest.fixture
def treasury(tmp_path):
    return Treasury(path=tmp_path / "ledger.db")


def _held(treasury, job_id="BIG", rule="oversized_order", **overrides):
    job = {**BIG_JOB, "id": job_id, **overrides}
    treasury.upsert_job(
        job_id,
        "failed",
        topic=job["topic"],
        budget_cents=job["budget_cents"],
        customer_email=job["customer_email"],
        job_payload_json=job,
        error_reason=f"intake: {rule} — held for a human",
    )
    return job


def test_an_empty_queue(treasury):
    assert pending(treasury) == []
    assert "Nothing waiting on an operator" in format_queue([])


def test_the_queue_shows_held_jobs_with_their_rule(treasury):
    _held(treasury)
    rows = pending(treasury)
    assert len(rows) == 1
    assert rows[0]["job_id"] == "BIG"
    assert rows[0]["rule"] == "oversized_order"
    assert rows[0]["budget_cents"] == 500_000


def test_jobs_declined_for_other_reasons_are_not_in_the_queue(treasury):
    treasury.upsert_job("J1", "failed", topic="t", error_reason="projected margin 2% below floor")
    assert pending(treasury) == []


def test_approval_requeues_the_job_with_a_one_off_exemption(treasury):
    _held(treasury)
    result = approve(treasury, "BIG")

    assert result["overrode"] == "oversized_order"
    assert result["queued"] is True
    row = treasury.get_job("BIG")
    assert row["status"] == "pending_quote"
    assert not row["error_reason"]
    assert json.loads(row["job_payload_json"])[APPROVAL_FLAG] is True
    assert pending(treasury) == []


def test_an_approved_job_passes_the_screen_that_held_it(treasury):
    _held(treasury)
    approve(treasury, "BIG")
    job = job_payload(treasury.get_job("BIG"))

    screen = screen_job(job, treasury)
    assert screen.allowed
    assert "operator" in screen.reason


def test_the_exemption_covers_only_that_job(treasury):
    _held(treasury)
    approve(treasury, "BIG")
    # A second oversized order from the same customer is still held.
    other = {**BIG_JOB, "id": "BIG2", "topic": "Another teardown"}
    assert not screen_job(other, treasury).allowed


def test_approval_runs_the_job_when_asked(treasury):
    _held(treasury)
    ran: list[dict] = []
    result = approve(treasury, "BIG", runner=ran.append)
    assert result["queued"] is False
    assert ran and ran[0][APPROVAL_FLAG] is True


def test_approval_is_recorded_for_the_audit_trail(treasury):
    _held(treasury)
    approve(treasury, "BIG", operator="ian")
    event = next(e for e in treasury.list_events(job_id="BIG") if e["stage"] == "intake_approved")
    payload = json.loads(event["payload_json"])
    assert payload["operator"] == "ian"
    assert payload["overrode"] == "oversized_order"


def test_rejection_cancels_the_job_with_the_reason(treasury):
    _held(treasury)
    reject(treasury, "BIG", reason="customer could not verify funds", operator="ian")

    row = treasury.get_job("BIG")
    assert row["status"] == "cancelled"
    assert "rejected by ian" in row["error_reason"]
    assert "verify funds" in row["error_reason"]
    assert pending(treasury) == []


def test_rejection_without_a_reason_still_records_one(treasury):
    _held(treasury)
    reject(treasury, "BIG")
    assert "no reason given" in treasury.get_job("BIG")["error_reason"]


def test_rejection_is_recorded_for_the_audit_trail(treasury):
    _held(treasury)
    reject(treasury, "BIG", reason="out of scope")
    stages = [e["stage"] for e in treasury.list_events(job_id="BIG")]
    assert "intake_rejected" in stages


def test_approving_an_unknown_job_is_an_error(treasury):
    with pytest.raises(ValueError, match="not found"):
        approve(treasury, "NOPE")
    with pytest.raises(ValueError, match="not found"):
        reject(treasury, "NOPE")


def test_approving_a_job_that_was_not_held_is_an_error(treasury):
    treasury.upsert_job("J1", "completed", topic="t")
    with pytest.raises(ValueError, match="not waiting on review"):
        approve(treasury, "J1")


def test_job_payload_falls_back_to_columns(treasury):
    treasury.upsert_job(
        "J1",
        "failed",
        topic="t",
        budget_cents=4_900,
        customer_email="a@x.example",
        error_reason="intake: duplicate — x",
    )
    job = job_payload(treasury.get_job("J1"))
    assert job["id"] == "J1"
    assert job["budget_cents"] == 4_900


def test_queue_rendering_totals_the_orders_held(treasury):
    _held(treasury, "BIG")
    _held(treasury, "BIG2", budget_cents=200_000)
    rendered = format_queue(pending(treasury))
    assert "REVIEW QUEUE" in rendered
    assert "2 job(s) held" in rendered
    assert "$7,000.00" in rendered


def test_burst_and_duplicate_holds_are_reviewable_too(treasury):
    """Any intake rule can be overridden by a human, not just oversized orders."""
    _held(treasury, "DUP", rule="duplicate")
    _held(treasury, "BURST", rule="customer_burst", customer_email="spammer@x.example")
    assert {row["rule"] for row in pending(treasury)} == {"duplicate", "customer_burst"}


def test_intake_approval_overrides_a_blocked_domain(treasury):
    policy = IntakePolicy(blocked_email_domains=("burner.example",))
    job = {**BIG_JOB, "customer_email": "a@burner.example", APPROVAL_FLAG: True}
    assert screen_job(job, treasury, policy).allowed
