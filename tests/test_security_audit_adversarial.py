"""Adversarial regressions from the PR 141 review; no real provider I/O."""

import json
import threading
from dataclasses import asdict
from unittest.mock import patch

import pytest

from solvent import nemotron
from solvent.agent import Solvent
from solvent.chat import _make_executor
from solvent.checkout import CheckoutPolicy, sweep
from solvent.pricing import quote


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("solvent.treasury.DB_PATH", tmp_path / "treasury.db")
    monkeypatch.setattr("solvent.delivery.OUTBOX_DIR", tmp_path / "outbox")
    for name in (
        "STRIPE_SECRET_KEY",
        "NVIDIA_API_KEY",
        "SOLVENT_LLM_BASE_URL",
        "SOLVENT_NANO_RPC",
        "SOLVENT_NANO_ADDRESS",
        "SOLVENT_FORCE_NANO_SIMULATE",
    ):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("SOLVENT_NANO", "")
    monkeypatch.setenv("SOLVENT_DELIVERY_SECRET", "x" * 32)
    monkeypatch.setenv("SOLVENT_DASHBOARD_TOKEN", "d" * 32)
    yield


def test_force_offline_is_request_local_and_restored():
    first_entered = threading.Event()
    second_entered = threading.Event()
    first_exited = threading.Event()
    replies = []

    def first():
        with nemotron.force_offline():
            first_entered.set()
            assert second_entered.wait(3)
        first_exited.set()

    def second():
        assert first_entered.wait(3)
        with nemotron.force_offline():
            second_entered.set()
            assert first_exited.wait(3)
            replies.append(nemotron.complete("sys", "customer request")[0])

    with (
        patch.object(nemotron.llm_backend, "enabled", return_value=True),
        patch.object(
            nemotron.llm_backend, "complete", return_value=("METERED BACKEND", {})
        ) as backend,
    ):
        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        print(
            "customer replies",
            replies,
            "backend_calls",
            backend.call_count,
            "global afterward",
            nemotron._block_metered.get(),
        )
        assert backend.call_count == 0, "Overlapping customer contexts enable metered inference"
        assert not nemotron._block_metered.get(), (
            "Offline mode leaks into later operator/fulfillment calls"
        )


def _stripe_pending():
    agent = Solvent(fresh=True, sync_payment=False)
    job = {
        "id": "audit-stripe",
        "topic": "Battery markets",
        "budget_cents": 5000,
        "customer_email": "buyer@example.com",
        "est_tokens": 8000,
        "market_data_calls": 2,
        "web_search_calls": 6,
    }
    q = quote(job, agent.pricing)
    agent.t.upsert_job(
        job["id"],
        "awaiting_payment",
        topic=job["topic"],
        budget_cents=5000,
        customer_email=job["customer_email"],
        quote_json=json.dumps(asdict(q)),
    )
    agent.t.upsert_checkout(job["id"], "cs_test", "https://example.invalid/checkout", "open")
    checkout = {
        "session_id": "cs_test",
        "url": "https://example.invalid/checkout",
        "amount_cents": q.price_cents,
    }
    return agent, job, q, checkout


def test_stale_payment_pending_cannot_reopen_completed_job():
    agent, job, q, checkout = _stripe_pending()

    def other_request_completes_then_stale_cache_returns(_job_id):
        agent.t.earn(5000, "other request", job_id=job["id"], stripe_ref="pi_paid")
        agent.t.upsert_job(job["id"], "completed")
        return None

    with patch.object(
        agent.stripe,
        "get_cached_payment",
        side_effect=other_request_completes_then_stale_cache_returns,
    ):
        result = agent._runner._stage_paid(job, checkout, q)
    print("stale result", result, "current job", agent.t.get_job(job["id"])["status"])
    assert agent.t.get_job(job["id"])["status"] == "completed"


def test_earned_payment_is_not_expired_before_paid_state_update():
    agent, job, q, checkout = _stripe_pending()
    agent.stripe._webhook_payments["job:" + job["id"]] = {
        "paid": True,
        "stripe_ref": "pi_paid",
        "checkout_session_id": "cs_test",
        "amount_cents": 5000,
        "currency": "usd",
        "job_id": job["id"],
    }
    orig = agent.t.complete_stage
    states = []

    def pause_after_earn(job_id, stage, key, result):
        if stage == "paid":
            assert agent.t.job_has_revenue(job_id)
            result_sweep = sweep(
                agent.t,
                policy=CheckoutPolicy(
                    reminder_after_hours=999, max_reminders=0, expire_after_hours=0
                ),
            )
            states.append((result_sweep, agent.t.get_job(job_id)["status"]))
        return orig(job_id, stage, key, result)

    with patch.object(agent.t, "complete_stage", side_effect=pause_after_earn):
        agent._runner._stage_paid(job, checkout, q)
    print("interleaved sweeper after earn", states)
    assert states[0][0]["expired"] == [], (
        "The newly added lock still allows expiring a revenue-backed job"
    )


def test_customer_quote_reason_contains_no_margin():
    agent = Solvent(fresh=True, sync_payment=False)
    execute = _make_executor(agent, "customer", False, operator=False)
    # Raise COGS enough to make an above-minimum order miss the margin floor.
    agent.pricing.cost_calibration = 10
    found = None
    for budget in range(1000, 10000, 100):
        payload = json.loads(
            execute("quote_brief", {"topic": "Battery markets", "budget_cents": budget})
        )
        if "projected margin" in payload["reason"]:
            found = payload
            break
    print("customer-visible quote", found)
    assert found is None, "Removed numeric fields still leak through the quote reason string"


def test_customer_error_channels_hide_internal_margin():
    from solvent.chat import format_job_notification

    agent = Solvent(fresh=True, sync_payment=False)
    reason = "projected margin 1.2% below floor 35.0%"
    agent.t.upsert_job("declined", "failed", job_owner_session_id="customer", error_reason=reason)
    execute = _make_executor(agent, "customer", False, operator=False)
    status = execute("job_status", {"job_id": "declined"})
    with patch.object(agent, "enqueue_job", return_value={"stage": "declined", "reason": reason}):
        submitted = execute("submit_brief", {"topic": "Battery markets", "budget_cents": 5800})
    notification = format_job_notification(
        {"stage": "declined", "job_id": "declined", "reason": reason}
    )
    assert all(
        "35.0" not in reply and "1.2" not in reply for reply in (status, submitted, notification)
    )
    operator = _make_executor(agent, "customer", False, operator=True)
    assert reason in operator("job_status", {"job_id": "declined"})
