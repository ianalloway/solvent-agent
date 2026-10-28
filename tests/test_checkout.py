"""Tests for the checkout lifecycle: reminders and expiry (solvent.checkout)."""

from __future__ import annotations

import json
import time
from unittest import mock

import pytest

from solvent.checkout import (
    EXPIRED_STATUS,
    CheckoutPolicy,
    format_checkouts,
    load_policy,
    open_checkouts,
    sweep,
)
from solvent.treasury import Treasury

HOUR = 3_600


@pytest.fixture
def policy():
    return CheckoutPolicy(reminder_after_hours=4, max_reminders=2, expire_after_hours=48)


@pytest.fixture
def treasury(tmp_path):
    t = Treasury(path=tmp_path / "ledger.db")
    t.seed(50_000)
    return t


def _awaiting(treasury, job_id="J1", *, email="buyer@x.example", budget=4_900, topic="a brief"):
    treasury.upsert_job(
        job_id,
        "awaiting_payment",
        topic=topic,
        budget_cents=budget,
        customer_email=email,
    )
    treasury.upsert_checkout(job_id, f"cs_{job_id}", f"https://pay.example/{job_id}", "open")


@pytest.fixture(autouse=True)
def _outbox(tmp_path, monkeypatch):
    """Keep reminder emails inside the test's own directory."""
    monkeypatch.setattr("solvent.delivery.OUTBOX_DIR", tmp_path / "outbox")


def test_a_fresh_checkout_is_left_alone(treasury, policy):
    _awaiting(treasury)
    rows = open_checkouts(treasury, policy=policy)
    assert len(rows) == 1
    assert rows[0]["next_action"] == "wait"
    assert sweep(treasury, policy=policy) == {"reminded": [], "expired": []}


def test_reminder_goes_out_once_the_threshold_passes(treasury, policy):
    _awaiting(treasury)
    result = sweep(treasury, policy=policy, now=time.time() + 5 * HOUR)
    assert result["reminded"] == ["J1"]
    assert treasury.get_checkout("J1")["reminders_sent"] == 1
    assert treasury.get_job("J1")["status"] == "awaiting_payment"


def test_reminders_are_spaced_not_repeated_every_sweep(treasury, policy):
    _awaiting(treasury)
    now = time.time() + 5 * HOUR
    assert sweep(treasury, policy=policy, now=now)["reminded"] == ["J1"]
    # Same moment again: the next nudge is not due yet.
    assert sweep(treasury, policy=policy, now=now)["reminded"] == []
    assert sweep(treasury, policy=policy, now=time.time() + 9 * HOUR)["reminded"] == ["J1"]


def test_reminders_stop_at_the_cap(treasury, policy):
    _awaiting(treasury)
    for hours in (5, 9, 13, 20):
        sweep(treasury, policy=policy, now=time.time() + hours * HOUR)
    assert treasury.get_checkout("J1")["reminders_sent"] == policy.max_reminders


def test_expiry_closes_the_job_and_the_link(treasury, policy):
    _awaiting(treasury)
    result = sweep(treasury, policy=policy, now=time.time() + 49 * HOUR)

    assert result["expired"] == ["J1"]
    job = treasury.get_job("J1")
    assert job["status"] == EXPIRED_STATUS
    assert "unpaid after" in job["error_reason"]
    assert treasury.get_checkout("J1")["status"] == EXPIRED_STATUS


def test_expiry_is_idempotent(treasury, policy):
    _awaiting(treasury)
    later = time.time() + 49 * HOUR
    assert sweep(treasury, policy=policy, now=later)["expired"] == ["J1"]
    assert sweep(treasury, policy=policy, now=later)["expired"] == []


def test_expiry_asks_stripe_to_close_the_session(treasury, policy):
    _awaiting(treasury)
    stripe = mock.MagicMock()
    sweep(treasury, stripe=stripe, policy=policy, now=time.time() + 49 * HOUR)
    stripe.expire_checkout_session.assert_called_once_with("cs_J1")


def test_a_failing_stripe_expiry_does_not_stop_the_sweep(treasury, policy):
    _awaiting(treasury)
    stripe = mock.MagicMock()
    stripe.expire_checkout_session.side_effect = RuntimeError("stripe down")
    assert sweep(treasury, stripe=stripe, policy=policy, now=time.time() + 49 * HOUR)[
        "expired"
    ] == ["J1"]


def test_a_failing_mailbox_does_not_stop_the_sweep(treasury, policy):
    _awaiting(treasury, "J1")
    _awaiting(treasury, "J2")
    with mock.patch(
        "solvent.delivery.send_payment_reminder", side_effect=RuntimeError("no mailbox")
    ):
        result = sweep(treasury, policy=policy, now=time.time() + 5 * HOUR)
    assert result["reminded"] == []
    # Both were attempted; neither was silently marked as reminded.
    assert treasury.get_checkout("J1")["reminders_sent"] == 0
    assert treasury.get_checkout("J2")["reminders_sent"] == 0


def test_paid_jobs_drop_out_of_the_pipeline(treasury, policy):
    _awaiting(treasury)
    treasury.upsert_job("J1", "paid_pending_fulfill")
    assert open_checkouts(treasury, policy=policy) == []
    assert sweep(treasury, policy=policy, now=time.time() + 99 * HOUR)["expired"] == []


def test_expiry_never_moves_money(treasury, policy):
    _awaiting(treasury)
    before = treasury.balance_cents()
    sweep(treasury, policy=policy, now=time.time() + 49 * HOUR)
    assert treasury.balance_cents() == before


def test_checkout_age_survives_a_status_update(treasury):
    _awaiting(treasury)
    created = treasury.get_checkout("J1")["created_at"]
    treasury.upsert_checkout("J1", "cs_J1", "https://pay.example/J1", "open")
    assert treasury.get_checkout("J1")["created_at"] == created


def test_reminder_count_survives_a_status_update(treasury):
    _awaiting(treasury)
    treasury.record_checkout_reminder("J1")
    treasury.upsert_checkout("J1", "cs_J1", "https://pay.example/J1", "open")
    assert treasury.get_checkout("J1")["reminders_sent"] == 1


def test_sweep_records_events(treasury, policy):
    _awaiting(treasury)
    sweep(treasury, policy=policy, now=time.time() + 5 * HOUR)
    stages = [e["stage"] for e in treasury.list_events(job_id="J1")]
    assert "payment_reminder" in stages


def test_policy_file_overrides_defaults(tmp_path):
    path = tmp_path / "checkout_policy.json"
    path.write_text(json.dumps({"reminder_after_hours": 1, "expire_after_hours": 6}))
    loaded = load_policy(path)
    assert loaded.reminder_after_hours == 1
    assert loaded.expire_after_hours == 6
    assert loaded.max_reminders == CheckoutPolicy().max_reminders


@pytest.mark.parametrize("content", ["{oops", json.dumps({"expire_after_hours": 0})])
def test_a_broken_policy_file_falls_back_to_defaults(tmp_path, content):
    path = tmp_path / "checkout_policy.json"
    path.write_text(content)
    assert load_policy(path) == CheckoutPolicy()


def test_format_lists_waiting_revenue(treasury, policy):
    _awaiting(treasury, budget=9_900)
    rendered = format_checkouts(open_checkouts(treasury, policy=policy), policy)
    assert "OPEN CHECKOUTS" in rendered
    assert "$99.00" in rendered


def test_format_handles_an_empty_pipeline(policy):
    assert "Nothing waiting on payment" in format_checkouts([], policy)


def test_worker_sweeps_before_taking_work():
    from solvent.worker import run_worker

    with (
        mock.patch("solvent.worker.Solvent") as solvent_cls,
        mock.patch("solvent.worker.resume_incomplete_jobs", return_value=[]),
        mock.patch("solvent.worker.list_claimable", return_value=[]),
        mock.patch("solvent.worker.sweep_checkouts") as mock_sweep,
    ):
        agent = mock.MagicMock()
        solvent_cls.return_value = agent
        run_worker(once=True)

    mock_sweep.assert_called_once_with(agent.t, stripe=agent.stripe, nano=agent.nano)


def _awaiting_nano(treasury, job_id="JN1", *, email="buyer@x.example", budget=4_900, topic="nano brief", expires_at=None):
    treasury.upsert_job(
        job_id,
        "awaiting_payment",
        topic=topic,
        budget_cents=budget,
        customer_email=email,
    )
    treasury.upsert_checkout(
        job_id, f"inv_sim_{job_id}", f"nano:nano_sim_{job_id}?amount=1", "open", rail="nano"
    )
    invoice = {
        "id": f"inv_sim_{job_id}",
        "destination": f"nano_sim_{job_id}",
        "amount_cents": budget,
        "amount_raw": "1000",
        "xno_rate_usd": "0.85",
        "status": "open",
        "expires_at": expires_at,
        "url": f"nano:nano_sim_{job_id}?amount=1",
    }
    treasury.upsert_nano_invoice(job_id, invoice)


def test_nano_invoice_expires_via_policy_window(treasury, policy):
    _awaiting_nano(treasury)
    result = sweep(treasury, policy=policy, now=time.time() + 49 * HOUR)
    assert result["expired"] == ["JN1"]
    assert treasury.get_job("JN1")["status"] == EXPIRED_STATUS
    assert treasury.get_checkout("JN1")["status"] == EXPIRED_STATUS
    assert treasury.get_nano_invoice("JN1")["status"] == EXPIRED_STATUS


def test_nano_invoice_expires_on_its_own_expires_at(treasury, policy):
    now = time.time()
    _awaiting_nano(treasury, expires_at=now + 60)
    # Still well inside the 48h chase window, but invoice TTL elapsed.
    result = sweep(treasury, policy=policy, now=now + 120)
    assert result["expired"] == ["JN1"]
    assert "nano invoice expired" in treasury.get_job("JN1")["error_reason"]
    assert treasury.get_nano_invoice("JN1")["status"] == EXPIRED_STATUS


def test_nano_expiry_does_not_call_stripe(treasury, policy):
    _awaiting_nano(treasury)
    stripe = mock.MagicMock()
    sweep(treasury, stripe=stripe, policy=policy, now=time.time() + 49 * HOUR)
    stripe.expire_checkout_session.assert_not_called()


def test_open_checkouts_reports_nano_rail(treasury, policy):
    _awaiting_nano(treasury)
    rows = open_checkouts(treasury, policy=policy)
    assert rows[0]["rail"] == "nano"
