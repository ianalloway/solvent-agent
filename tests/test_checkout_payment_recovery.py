"""Checkout expiry and delayed settlement never strand verified customer funds."""

import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from solvent.checkout import sweep
from solvent.stripe_client import StripeClient
from solvent.treasury import Treasury
from tests import test_stripe_webhook_booking as booking
from tests.test_stripe_webhook_booking import (
    AUTH,
    SESSION,
    _event,
    _open_checkout,
    _post,
    _revenue,
)


client = booking.client


def _delayed(event_id, job_id, amount, *, succeeded=None, session=SESSION):
    event = json.loads(_event(event_id, job_id, amount, session=session))
    if succeeded is not None:
        event["type"] = "checkout.session.async_payment_" + ("succeeded" if succeeded else "failed")
    event["data"]["object"]["payment_status"] = "paid" if succeeded else "unpaid"
    event["data"]["object"]["payment_intent"] = f"pi_{job_id}"
    return json.dumps(event).encode()


def test_delayed_success_books_once_and_replay_cannot_downgrade_it(client):
    price = _open_checkout(client, "delayed")
    t = client.app.state.agent.t
    pending = _delayed("evt_pending", "delayed", price)
    assert _post(client, pending).status_code == 200
    assert _revenue(client, "delayed") == []
    assert t.get_checkout("delayed")["status"] == "processing"
    assert sweep(t, now=time.time() + 99 * 3600)["expired"] == []

    paid = _delayed("evt_succeeded", "delayed", price, succeeded=True)
    assert _post(client, paid).status_code == 200
    assert _post(client, paid).json()["duplicate"] is True
    assert client.post("/api/webhooks/evt_succeeded/replay", headers=AUTH).status_code == 200
    status = t.get_job("delayed")["status"]
    assert (
        _post(client, _delayed("evt_stale_failed", "delayed", price, succeeded=False)).status_code
        == 200
    )
    assert client.post("/api/webhooks/evt_pending/replay", headers=AUTH).status_code == 200
    assert _revenue(client, "delayed") == [price]
    assert t.get_job("delayed")["status"] == status
    assert t.get_checkout("delayed")["status"] == "paid"


def test_delayed_failure_stops_fulfillment_and_later_success_requires_refund(client):
    price = _open_checkout(client, "failed")
    t = client.app.state.agent.t
    assert _post(client, _delayed("evt_processing", "failed", price)).status_code == 200
    assert (
        _post(client, _delayed("evt_failed", "failed", price, succeeded=False)).status_code == 200
    )
    assert _revenue(client, "failed") == []
    assert t.get_job("failed")["status"] == "failed"
    assert t.get_checkout("failed")["status"] == "failed"
    assert t.pending_refunds("failed") == []
    # Out-of-order provider events must still account for money, never silently drop it.
    assert (
        _post(client, _delayed("evt_late_success", "failed", price, succeeded=True)).status_code
        == 200
    )
    assert _revenue(client, "failed") == [price]
    assert t.pending_refunds("failed")[0]["amount_cents"] == price
    assert t.get_job("failed")["status"] == "failed"


@pytest.mark.parametrize("succeeded", [None, False, True])
def test_delayed_event_for_another_session_leaves_original_checkout_open(client, succeeded):
    price = _open_checkout(client, "mismatched")
    assert (
        _post(
            client,
            _delayed(
                "evt_wrong", "mismatched", price, succeeded=succeeded, session="cs_someone_else"
            ),
        ).status_code
        == 200
    )
    t = client.app.state.agent.t
    assert t.get_job("mismatched")["status"] == "awaiting_payment"
    assert t.get_checkout("mismatched")["status"] == "open"
    assert t.get_checkout("mismatched")["session_id"] == SESSION
    assert _revenue(client, "mismatched") == []


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {"status": "open"},
        {"status": "complete"},
        {"status": "error", "error": "already paid"},
    ],
)
def test_unconfirmed_expiry_keeps_late_webhook_fulfillable(client, result):
    price = _open_checkout(client, "expiry")
    t = client.app.state.agent.t
    stripe = SimpleNamespace(expire_checkout_session=lambda _: result)
    assert sweep(t, stripe=stripe, now=time.time() + 49 * 3600)["expired"] == []
    assert t.get_job("expiry")["status"] == "awaiting_payment"
    assert t.get_checkout("expiry")["session_id"] == SESSION
    assert _post(client, _event("evt_after_expiry_error", "expiry", price)).status_code == 200
    assert _revenue(client, "expiry") == [price]


@pytest.mark.parametrize("status", ["expired", "cancelled", "failed"])
def test_terminal_payment_is_reserved_once_survives_restart_and_can_be_reconciled(client, status):
    price = _open_checkout(client, "terminal")
    agent = client.app.state.agent
    t = agent.t
    t.upsert_job("terminal", status)
    before = t.balance_cents()
    payload = _delayed("evt_late", "terminal", price, succeeded=True)
    assert _post(client, payload).status_code == 200
    assert _post(client, payload).json()["duplicate"] is True
    assert client.post("/api/webhooks/evt_late/replay", headers=AUTH).status_code == 200
    assert _revenue(client, "terminal") == [price]
    assert t.get_job("terminal")["status"] == status
    assert t.snapshot()["available_balance_cents"] == before  # Fully reserved.
    assert not t.stage_completed("terminal", "fulfill")
    restored = Treasury(path=t.path)
    assert len(restored.pending_refunds("terminal")) == 1
    assert restored.pending_refunds("terminal")[0]["amount_cents"] == price
    restored.complete_refund("terminal", "pi_terminal", "re_verified")
    assert client.post("/api/webhooks/evt_late/replay", headers=AUTH).status_code == 200
    assert _revenue(client, "terminal") == [price]
    assert restored.pending_refunds("terminal") == []
    assert restored.job_refund_cents("terminal") == price
    assert restored.get_job("terminal")["status"] == status


@pytest.mark.parametrize(
    "change",
    [
        {"amount_total": 1},
        {"currency": "eur"},
        {"payment_intent": "bad"},
        {"id": "cs_another"},
    ],
)
def test_invalid_terminal_payment_does_not_book_or_reserve(client, change):
    price = _open_checkout(client, "terminal-invalid")
    t = client.app.state.agent.t
    t.upsert_job("terminal-invalid", "expired")
    event = json.loads(_delayed("evt_bad", "terminal-invalid", price, succeeded=True))
    event["data"]["object"].update(change)
    assert _post(client, json.dumps(event).encode()).status_code == 200
    assert _revenue(client, "terminal-invalid") == []
    assert t.pending_refunds("terminal-invalid") == []


def test_terminal_transition_during_payment_verification_quarantines_funds(client, monkeypatch):
    price = _open_checkout(client, "race")
    agent = client.app.state.agent
    accept = agent.stripe.accept_cached_payment

    def expire_during_verification(payment, link):
        verified = accept(payment, link)
        agent.t.upsert_job("race", "expired")
        return verified

    monkeypatch.setattr(agent.stripe, "accept_cached_payment", expire_during_verification)
    assert _post(client, _event("evt_race", "race", price)).status_code == 200
    assert _revenue(client, "race") == [price]
    assert agent.t.pending_refunds("race")[0]["amount_cents"] == price
    assert agent.t.get_job("race")["status"] == "expired"
    assert not agent.t.stage_completed("race", "fulfill")


def test_late_payment_and_liability_roll_back_together_on_interruption(client):
    price = _open_checkout(client, "atomic")
    t = client.app.state.agent.t
    t.upsert_job("atomic", "expired")
    with sqlite3.connect(t.path) as conn:
        conn.execute(
            "CREATE TRIGGER interrupt_refund BEFORE INSERT ON spend_reservations "
            "BEGIN SELECT RAISE(ABORT, 'interrupted'); END"
        )
    payload = _delayed("evt_atomic", "atomic", price, succeeded=True)
    with pytest.raises(sqlite3.IntegrityError, match="interrupted"):
        _post(client, payload)
    assert _revenue(client, "atomic") == []
    assert t.pending_refunds("atomic") == []
    with sqlite3.connect(t.path) as conn:
        conn.execute("DROP TRIGGER interrupt_refund")
    assert _post(client, payload).status_code == 200
    assert _revenue(client, "atomic") == [price]
    assert t.pending_refunds("atomic")[0]["amount_cents"] == price


def test_unconfigured_stripe_cannot_claim_real_checkout_is_expired(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    stripe = StripeClient()
    assert stripe.expire_checkout_session("cs_real")["status"] == "error"
    assert stripe.expire_checkout_session("cs_sim_example")["status"] == "expired"


@pytest.mark.parametrize("cancel_after_failure", [False, True])
def test_payment_replay_never_refunds_published_brief_after_delivery_failure(
    client, monkeypatch, cancel_after_failure
):
    job_id = "published-email-failed"
    price = _open_checkout(client, job_id)
    t = client.app.state.agent.t

    def fail_email(*args, **kwargs):
        raise OSError("SMTP outage")

    monkeypatch.setattr("solvent.delivery.send_brief_email", fail_email)
    payload = _event("evt_publish_then_fail", job_id, price)
    assert _post(client, payload).status_code == 200
    assert t.get_job(job_id)["status"] == "failed"
    assert t.stage_completed(job_id, "publish")
    assert not t.pending_refunds(job_id)
    if cancel_after_failure:
        from solvent.job_cmd import cmd_cancel

        cmd_cancel(t, job_id)
    assert (
        client.post("/api/webhooks/evt_publish_then_fail/replay", headers=AUTH).status_code == 200
    )
    assert not t.pending_refunds(job_id)
    assert _revenue(client, job_id) == [price]
    assert t.get_job(job_id)["status"] == ("cancelled" if cancel_after_failure else "failed")


def test_already_booked_failed_payment_is_not_treated_as_new_late_money(client):
    price = _open_checkout(client, "previous-failure")
    t = client.app.state.agent.t
    t.earn(
        price,
        "Previously booked",
        job_id="previous-failure",
        stripe_ref="pi_previous-failure",
        stripe_session_id=SESSION,
    )
    t.upsert_job("previous-failure", "failed", error_reason="existing operator review")
    assert (
        _post(
            client, _delayed("evt_replayed", "previous-failure", price, succeeded=True)
        ).status_code
        == 200
    )
    assert t.pending_refunds("previous-failure") == []
    assert t.get_job("previous-failure")["error_reason"] == "existing operator review"


def test_cancelled_stage_returns_have_dashboard_compatible_booked_events(client):
    from solvent.job_cmd import cmd_cancel

    _open_checkout(client, "cancelled-stages")
    agent = client.app.state.agent
    cmd_cancel(agent.t, "cancelled-stages")
    job = {"id": "cancelled-stages"}
    runner = agent._runner
    events = [
        runner.run_job(job),
        runner._stage_quote(job),
        runner._stage_checkout(job, {}),
        runner._stage_fulfill_and_book(job, None, {}),
    ]
    assert all(event["status"] == "cancelled" and "job_pnl" in event for event in events)


@pytest.mark.parametrize(
    "session, expected",
    [
        ({"id": "cs_real", "status": "expired"}, "expired"),
        ({"id": "cs_real"}, "error"),
        ({"status": "expired"}, "error"),
        ({"id": "cs_another", "status": "expired"}, "error"),
        ({"id": "cs_real", "status": "complete"}, "error"),
    ],
)
def test_stripe_expiry_adapter_requires_explicit_matching_confirmation(
    tmp_path, monkeypatch, session, expected
):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    client = StripeClient()
    client.live = True
    monkeypatch.setattr(
        "solvent.stripe_client.stripe",
        SimpleNamespace(
            checkout=SimpleNamespace(Session=SimpleNamespace(expire=lambda _: session))
        ),
    )
    assert client.expire_checkout_session("cs_real")["status"] == expected


@pytest.mark.parametrize("status", ["awaiting_payment", "expired", "cancelled", "failed"])
@pytest.mark.parametrize("succeeded", [None, False, True])
def test_delayed_and_late_events_leave_retired_payment_history_unchanged(client, status, succeeded):
    from tests.test_nano_retirement import JOB, accounting_snapshot, historical_job

    agent = client.app.state.agent
    historical_job(agent, "invoice", status)
    job_id = JOB["id"]
    agent.t.upsert_checkout(job_id, SESSION, "https://example.invalid/old", "open")
    agent.t.upsert_job(job_id, status, checkout_session_id=SESSION)
    before = accounting_snapshot(agent.t)
    row = agent.t.get_job(job_id)
    payload = _delayed("evt_retired", job_id, JOB["budget_cents"], succeeded=succeeded)
    assert _post(client, payload).status_code == 200
    assert accounting_snapshot(agent.t) == before
    assert agent.t.get_job(job_id) == row
    assert agent.stripe.get_cached_payment(job_id) is None
