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
from solvent.nano_client import NanoClient
from solvent.pricing import quote
from solvent.server import create_app


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


def test_verified_nano_payment_reaches_ledger(monkeypatch):
    try:
        from fastapi.testclient import TestClient
    except (ImportError, RuntimeError):
        pytest.skip("FastAPI TestClient optional dependencies are not installed")

    monkeypatch.setenv("SOLVENT_NANO", "sim")
    client = TestClient(create_app(fresh=True))
    agent = client.app.state.agent
    result = agent.enqueue_job(
        {
            "id": "audit-nano",
            "topic": "Battery markets",
            "budget_cents": 5000,
            "customer_email": "buyer@example.com",
            "est_tokens": 8000,
            "market_data_calls": 2,
            "web_search_calls": 6,
        }
    )
    assert result.get("rail") == "nano", result
    inv = agent.t.get_nano_invoice("audit-nano")
    block = agent.nano.mint_sim_block(inv)
    response = client.post(
        "/payments/nano/verify",
        headers={"X-Solvent-Dashboard-Token": "d" * 32},
        json={"job_id": "audit-nano", "block_hash": block},
    )
    retry = client.post(
        "/payments/nano/verify",
        headers={"X-Solvent-Dashboard-Token": "d" * 32},
        json={"job_id": "audit-nano", "block_hash": block},
    )
    print(
        "first",
        response.status_code,
        response.json(),
        "retry",
        retry.status_code,
        retry.json(),
        "job",
        agent.t.get_job("audit-nano")["status"],
        "invoice",
        agent.t.get_nano_invoice("audit-nano")["status"],
    )
    assert agent.t.job_has_revenue("audit-nano"), (
        "Verified block is consumed but revenue is never booked"
    )


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


def test_nano_hash_case_variants_cannot_replay(monkeypatch):
    address = "nano_1qato4k7z3spc8gq1zyd8xeqfbzsoxwo36a45ozbrxcatut7up8ohyardu1z"
    monkeypatch.setenv("SOLVENT_NANO", "live")
    monkeypatch.setenv("SOLVENT_NANO_RPC", "https://example.invalid/rpc")
    monkeypatch.setenv("SOLVENT_NANO_ADDRESS", address)
    monkeypatch.setenv("SOLVENT_NANO_RATE_USD", "1")

    def rpc(_url, payload):
        assert payload["action"] == "block_info"
        return {
            "amount": str(10 * 10**30),
            "confirmed": "true",
            "subtype": "send",
            "contents": {"link_as_account": address, "type": "state"},
        }

    nano = NanoClient(rpc_post=rpc)
    p1 = nano.verify_nano_block(nano.create_nano_invoice("first", 1000), "AB" * 32)
    p2 = nano.verify_nano_block(nano.create_nano_invoice("second", 1000), "ab" * 32)
    print(
        "same 256-bit hash accepted",
        p1["paid"],
        p2["paid"],
        "booked entries",
        len(nano._booked_blocks),
    )
    assert p1["paid"] and not p2["paid"], "Case variants bypass the new atomic-booking check"


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


def _nano_http(monkeypatch, *, raise_server_exceptions=True):
    try:
        from fastapi.testclient import TestClient
    except (ImportError, RuntimeError):
        pytest.skip("FastAPI TestClient optional dependencies are not installed")

    monkeypatch.setenv("SOLVENT_NANO", "sim")
    client = TestClient(create_app(fresh=True), raise_server_exceptions=raise_server_exceptions)
    agent = client.app.state.agent
    agent.enqueue_job(
        {
            "id": "nano-recovery",
            "topic": "Battery markets",
            "budget_cents": 5000,
            "customer_email": "buyer@example.com",
            "est_tokens": 8000,
            "market_data_calls": 2,
            "web_search_calls": 6,
        }
    )
    invoice = agent.t.get_nano_invoice("nano-recovery")
    block = agent.nano.mint_sim_block(invoice)

    def submit():
        return client.post(
            "/payments/nano/verify",
            headers={"X-Solvent-Dashboard-Token": "d" * 32},
            json={"job_id": "nano-recovery", "block_hash": block},
        )

    return agent, block, submit


def test_expiry_during_nano_verification_does_not_consume_proof(monkeypatch):
    agent, block, submit = _nano_http(monkeypatch)
    original = agent.nano.verify_nano_block

    def expire_during_verification(*args, **kwargs):
        payment = original(*args, **kwargs)
        agent.t.upsert_job("nano-recovery", "expired")
        agent.t.mark_nano_invoice("nano-recovery", status="expired")
        return payment

    with patch.object(agent.nano, "verify_nano_block", side_effect=expire_during_verification):
        response = submit()
    assert response.status_code == 409
    assert not agent.nano.is_block_booked(block)
    assert not agent.t.job_has_revenue("nano-recovery")
    assert agent.t.get_job("nano-recovery")["status"] == "expired"


def test_nano_proof_reservation_recovers_after_earn_failure(monkeypatch):
    agent, block, submit = _nano_http(monkeypatch, raise_server_exceptions=False)
    with patch.object(
        agent.t, "earn", side_effect=RuntimeError("interrupted before ledger commit")
    ):
        assert submit().status_code == 500
    assert agent.nano.is_block_booked(block)
    assert not agent.t.job_has_revenue("nano-recovery")
    # A new process loads the durable reservation rather than relying on memory.
    agent.nano = agent._runner.nano = NanoClient()
    response = submit()
    assert response.status_code == 200, response.text
    assert response.json()["paid"]
    assert sum(e.kind == "revenue" and e.job_id == "nano-recovery" for e in agent.t.entries) == 1
    assert agent.t.get_nano_invoice("nano-recovery")["status"] == "paid"


def test_nano_recovery_repairs_invoice_and_checkout_after_earn(monkeypatch):
    agent, block, submit = _nano_http(monkeypatch, raise_server_exceptions=False)
    with patch.object(agent.t, "mark_nano_invoice", side_effect=RuntimeError("crash after earn")):
        assert submit().status_code == 500
    assert agent.t.job_has_revenue("nano-recovery")
    assert agent.t.get_nano_invoice("nano-recovery")["status"] == "open"
    assert submit().status_code == 200
    assert agent.t.get_nano_invoice("nano-recovery")["status"] == "paid"
    assert agent.t.get_checkout("nano-recovery")["status"] == "paid"
    assert agent.t.get_job("nano-recovery")["status"] == "completed"
    assert sum(e.kind == "revenue" and e.job_id == "nano-recovery" for e in agent.t.entries) == 1


def test_nano_deadline_is_rechecked_after_network_verification(monkeypatch):
    agent, block, submit = _nano_http(monkeypatch)
    original = agent.nano.verify_nano_block

    def cross_expiry(*args, **kwargs):
        payment = original(*args, **kwargs)
        invoice = agent.t.get_nano_invoice("nano-recovery")
        invoice["expires_at"] = 1
        agent.t.upsert_nano_invoice("nano-recovery", invoice)
        return payment

    with patch.object(agent.nano, "verify_nano_block", side_effect=cross_expiry):
        response = submit()
    assert response.status_code == 409
    assert not agent.nano.is_block_booked(block)
    assert not agent.t.job_has_revenue("nano-recovery")


def test_failed_first_journal_write_cannot_create_memory_only_reservation(monkeypatch):
    monkeypatch.setenv("SOLVENT_NANO", "sim")
    nano = NanoClient()
    payment = {"job_id": "first", "invoice_id": "inv-first", "amount_raw": "100"}
    with patch.object(nano, "_save_booked", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            nano._try_book_block("block_sim_fail", payment, resume_invoice=True)
    assert not nano.is_block_booked("block_sim_fail")
    assert nano._try_book_block("block_sim_fail", payment, resume_invoice=True)
    assert nano._booked_path().is_file()
    assert not NanoClient()._try_book_block(
        "block_sim_fail",
        {**payment, "job_id": "second", "invoice_id": "inv-second"},
        resume_invoice=True,
    )


def test_corrupt_journal_fails_closed(monkeypatch):
    from solvent.nano_client import NanoClientError

    monkeypatch.setenv("SOLVENT_NANO", "sim")
    nano = NanoClient()
    path = nano._booked_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{partial-write")
    with pytest.raises(NanoClientError):
        NanoClient()
    with pytest.raises(NanoClientError):
        nano._try_book_block("block_sim_any", {"job_id": "new"})
    assert path.read_text() == "{partial-write"


def test_nano_resume_cannot_change_invoice_binding(monkeypatch):
    monkeypatch.setenv("SOLVENT_NANO", "sim")
    nano = NanoClient()
    payment = {"job_id": "first", "invoice_id": "inv-first", "amount_raw": "100"}
    assert nano._try_book_block("block_sim_bound", payment)
    for change in ({"job_id": "other"}, {"invoice_id": "other"}, {"amount_raw": "101"}):
        assert not nano._try_book_block(
            "block_sim_bound", {**payment, **change}, resume_invoice=True
        )
    assert nano._try_book_block("block_sim_bound", payment, resume_invoice=True)


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
