"""Cancelled jobs remain terminal across stale workers and payment recovery."""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from solvent.guardrails import Guardrails
from solvent.job_cmd import cmd_cancel
from solvent.pricing import quote
from solvent.queue import resume_incomplete_jobs
from solvent.stages import StageRunner
from solvent.stripe_client import StripeClient
from solvent.treasury import Treasury


@pytest.fixture
def cancelled_job(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    treasury = Treasury(tmp_path / "treasury.db")
    treasury.seed(10000)
    job = {
        "id": "cancelled-order",
        "topic": "Batteries",
        "budget_cents": 5000,
        "customer_email": "buyer@example.com",
        "est_tokens": 1000,
        "market_data_calls": 0,
        "web_search_calls": 1,
    }
    treasury.upsert_job(job["id"], "awaiting_payment", job_payload_json=job)
    treasury.upsert_checkout(job["id"], "cs_cancelled", "https://checkout.example", "open")
    runner = StageRunner(treasury, Guardrails(treasury), StripeClient(), sync_payment=False)
    # Isolate state-machine tests from notifications and dashboard rendering.
    monkeypatch.setattr(runner, "_emit", lambda **event: event)
    cmd_cancel(treasury, job["id"])
    return treasury, runner, job


@pytest.mark.parametrize("status", ["cancelled", "expired"])
def test_recovery_does_not_reopen_terminal_paid_jobs(tmp_path, status):
    treasury = Treasury(tmp_path / "treasury.db")
    treasury.upsert_job("closed", status)
    treasury.earn(5000, "Payment", job_id="closed", stripe_ref="pi_closed")
    assert resume_incomplete_jobs(treasury) == []
    assert treasury.get_job("closed")["status"] == status


def test_recovery_rechecks_status_after_listing(cancelled_job, monkeypatch):
    treasury, _, job = cancelled_job
    stale = {**treasury.get_job(job["id"]), "status": "awaiting_payment"}
    treasury.earn(5000, "Payment", job_id=job["id"], stripe_ref="pi_cancelled")
    monkeypatch.setattr(treasury, "list_jobs", lambda: [stale])
    assert resume_incomplete_jobs(treasury) == []
    assert treasury.get_job(job["id"])["status"] == "cancelled"


@pytest.mark.parametrize("entrypoint", ["advance", "run", "quote", "checkout", "fulfill"])
def test_cancelled_job_never_starts_work(cancelled_job, monkeypatch, entrypoint):
    treasury, runner, job = cancelled_job

    def unexpected(*args, **kwargs):
        pytest.fail("A cancelled job started provider work")

    monkeypatch.setattr(runner.stripe, "create_checkout_session", unexpected)
    monkeypatch.setattr(runner, "_fulfill_and_book_locked", unexpected)
    if entrypoint == "advance":
        result = runner.advance_job(job["id"])
    elif entrypoint == "run":
        result = runner.run_job(job)
    elif entrypoint == "quote":
        result = runner._stage_quote(job)
    elif entrypoint == "checkout":
        result = runner._stage_checkout(job, {})
    else:
        result = runner._stage_fulfill_and_book(job, quote(job), {})
    assert result["status"] == "cancelled"
    assert treasury.get_job(job["id"])["status"] == "cancelled"
    assert not treasury.stage_completed(job["id"], "fulfill")


def test_stale_stage_write_cannot_reopen_cancelled_job(cancelled_job):
    treasury, _, job = cancelled_job
    treasury.upsert_job(job["id"], "paid_pending_fulfill", current_stage="paid")
    assert treasury.get_job(job["id"])["status"] == "cancelled"
    assert treasury.get_job(job["id"])["current_stage"] is None


def test_existing_revenue_payment_projection_cannot_reopen_cancelled_job(cancelled_job):
    treasury, runner, job = cancelled_job
    treasury.earn(5000, "Prior payment", job_id=job["id"], stripe_ref="pi_cancelled")
    paid = runner._stage_paid(
        job, {"session_id": "cs_cancelled", "rail": "stripe"}, SimpleNamespace(price_cents=5000)
    )
    assert paid["paid"] is True
    assert treasury.get_job(job["id"])["status"] == "cancelled"
    assert len([e for e in treasury.entries if e.kind == "revenue"]) == 1


def test_refund_recovery_preserves_cancellation(cancelled_job):
    treasury, runner, job = cancelled_job
    treasury.begin_refund(job["id"], "pi_cancelled", 5000)
    assert runner.advance_job(job["id"])["status"] == "cancelled"
    assert treasury.get_job(job["id"])["status"] == "cancelled"
    assert treasury.pending_refunds(job["id"])


def test_cancel_waits_for_inflight_fulfillment_and_rechecks_result(tmp_path):
    treasury = Treasury(tmp_path / "treasury.db")
    treasury.upsert_job("active", "in_progress")
    started = threading.Event()

    def cancel():
        started.set()
        cmd_cancel(treasury, "active")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with treasury.fulfillment_lock("active"):
            future = executor.submit(cancel)
            assert started.wait(2)
            assert not future.done()
            treasury.upsert_job("active", "completed")
        future.result(timeout=5)
    assert treasury.get_job("active")["status"] == "completed"


def test_cancelled_job_with_invalid_payload_is_still_terminal(cancelled_job):
    treasury, runner, job = cancelled_job
    # Terminal status needs no quote or payload reconstruction.
    with treasury._conn() as conn, conn:
        conn.execute("UPDATE jobs SET job_payload_json = ? WHERE id = ?", ("{broken", job["id"]))
    assert runner.advance_job(job["id"])["status"] == "cancelled"


def test_resolved_refund_does_not_report_cancelled_job_as_failed(cancelled_job):
    treasury, runner, job = cancelled_job
    treasury.spend(5000, "Refund", job_id=job["id"], vendor="customer-refund")
    assert runner._refund_pending(job["id"])["status"] == "cancelled"
    assert treasury.get_job(job["id"])["status"] == "cancelled"
