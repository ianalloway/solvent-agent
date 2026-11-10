"""Retiring a payment rail must preserve historical records without spending money."""

import json
import time
from dataclasses import asdict
from unittest import mock

import pytest

from solvent.agent import Solvent
from solvent.checkout import CheckoutPolicy, open_checkouts, sweep
from solvent.pricing import quote

REASON = "Nano payment rail has been removed; reconcile this historical job manually"
JOB = {
    "id": "historical-nano",
    "topic": "Historical research brief",
    "budget_cents": 4900,
    "customer_email": "buyer@example.test",
    "est_tokens": 500,
    "market_data_calls": 0,
    "web_search_calls": 1,
}
MARKERS = (
    "invoice",
    "checkout_rail",
    "checkout_url",
    "checkout_session",
    "job_url",
    "job_session",
    "cached_checkout",
    "cached_paid",
)


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("SOLVENT_FORCE_STRIPE_SIMULATE", "1")
    monkeypatch.setenv("SOLVENT_DELIVERY_SECRET", "x" * 32)
    monkeypatch.setattr("solvent.treasury.DB_PATH", tmp_path / "ledger.db")
    monkeypatch.setattr("solvent.delivery.OUTBOX_DIR", tmp_path / "outbox")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    return Solvent(seed_cents=50_000, fresh=True, sync_payment=False)


def historical_job(agent, marker, status="awaiting_payment"):
    t = agent.t
    job_id = JOB["id"]
    t.upsert_job(
        job_id,
        status,
        job_payload_json=JOB,
        topic=JOB["topic"],
        budget_cents=JOB["budget_cents"],
        customer_email=JOB["customer_email"],
        quote_json=json.dumps(asdict(quote(JOB))),
    )
    if marker == "invoice":
        # Fixture represents a database written before retirement, not a supported writer.
        with t.lock(), t._conn() as conn, conn:
            conn.execute(
                "INSERT INTO nano_invoices "
                "(job_id, invoice_id, amount_cents, amount_raw, status, expires_at, pay_uri, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    "inv_sim_legacy",
                    4900,
                    "1000",
                    "open",
                    time.time() - 60,
                    "nano:nano_legacy?amount=1000",
                    time.time() - 3600,
                ),
            )
    elif marker.startswith("checkout_"):
        t.upsert_checkout(
            job_id,
            "inv_sim_legacy" if marker == "checkout_session" else "cs_legacy",
            "nano:nano_legacy?amount=1000"
            if marker == "checkout_url"
            else "https://pay.example/old",
            "open",
            rail="nano" if marker == "checkout_rail" else "stripe",
        )
    elif marker == "job_url":
        t.upsert_job(job_id, status, checkout_url="nano:nano_legacy?amount=1000")
    elif marker == "job_session":
        t.upsert_job(job_id, status, checkout_session_id="inv_sim_legacy")
    else:
        stage = "checkout" if marker == "cached_checkout" else "paid"
        t.complete_stage(
            job_id,
            stage,
            f"{stage}:{job_id}",
            result={
                "rail": "nano",
                "session_id": "inv_sim_legacy",
                "paid": stage == "paid",
                "url": "nano:nano_legacy?amount=1000",
                "stripe_ref": "B" * 64,
            },
        )
    if status in ("paid_pending_fulfill", "in_progress"):
        t.earn(4900, "Historical Nano payment", job_id=job_id, stripe_ref="B" * 64)


def accounting_snapshot(t):
    with t.lock(), t._conn() as conn:
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in ("ledger", "nano_invoices", "stripe_checkout", "job_stages")
        }


@pytest.mark.parametrize("marker", MARKERS)
@pytest.mark.parametrize(
    "status", ["pending_quote", "awaiting_payment", "paid_pending_fulfill", "in_progress"]
)
def test_historical_jobs_are_held_without_financial_or_fulfillment_actions(agent, marker, status):
    historical_job(agent, marker, status)
    before = accounting_snapshot(agent.t)
    with (
        mock.patch.object(agent._runner, "stripe") as stripe,
        mock.patch("solvent.service.fulfill") as fulfill,
    ):
        result = agent.advance_job(JOB["id"])
    assert result["stage"] == "payment_pending"
    assert result["reason"] == REASON
    assert stripe.mock_calls == []
    fulfill.assert_not_called()
    assert accounting_snapshot(agent.t) == before


@pytest.mark.parametrize("marker", MARKERS)
def test_retry_refuses_before_consuming_retry_or_mutating_history(agent, marker):
    historical_job(agent, marker, "failed")
    before = accounting_snapshot(agent.t)
    row = agent.t.get_job(JOB["id"])
    with (
        mock.patch.object(agent._runner, "stripe") as stripe,
        mock.patch("solvent.service.fulfill") as fulfill,
        pytest.raises(ValueError, match=REASON),
    ):
        agent._runner.retry_job(JOB["id"])
    assert agent.t.get_job(JOB["id"])["retry_count"] == row["retry_count"]
    assert accounting_snapshot(agent.t) == before
    assert stripe.mock_calls == []
    fulfill.assert_not_called()


@pytest.mark.parametrize("marker", MARKERS)
def test_legacy_checkouts_are_review_only_never_reminded_or_expired(agent, marker):
    historical_job(agent, marker)
    policy = CheckoutPolicy(reminder_after_hours=1, max_reminders=2, expire_after_hours=2)
    before = accounting_snapshot(agent.t)
    for hours in (1.5, 72):
        now = time.time() + hours * 3600
        rows = open_checkouts(agent.t, policy=policy, now=now)
        assert len(rows) == 1
        assert rows[0]["next_action"] == "review"
        assert rows[0]["checkout_url"] == ""
        with (
            mock.patch("solvent.delivery.send_payment_reminder") as reminder,
            mock.patch.object(agent._runner, "stripe") as stripe,
        ):
            assert sweep(agent.t, stripe=stripe, policy=policy, now=now) == {
                "reminded": [],
                "expired": [],
            }
        reminder.assert_not_called()
        assert stripe.mock_calls == []
    assert accounting_snapshot(agent.t) == before


def test_stale_nano_environment_cannot_select_rail_for_new_jobs(agent, monkeypatch):
    monkeypatch.setenv("SOLVENT_NANO", "live")
    monkeypatch.setenv("SOLVENT_NANO_RPC", "https://example.invalid/rpc")
    fresh = Solvent(seed_cents=50_000, fresh=False, sync_payment=False)
    assert not hasattr(fresh, "nano")
    with mock.patch.object(
        fresh.stripe,
        "create_checkout_session",
        return_value={
            "id": "cs_new",
            "url": "https://pay.example/new",
            "amount_cents": 4900,
            "simulated": True,
        },
    ) as checkout:
        result = fresh.handle_job({**JOB, "id": "new-stripe"})
    checkout.assert_called_once()
    assert result["stage"] == "payment_pending"
    assert fresh.t.get_nano_invoice("new-stripe") is None
    assert fresh.t.get_checkout("new-stripe")["rail"] == "stripe"


@pytest.mark.parametrize("path", ["/payments/nano/verify", "/webhooks/nano"])
def test_removed_http_routes_return_404(agent, monkeypatch, path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from solvent import server
    from solvent.webhook_log import WebhookLog

    monkeypatch.setattr(server, "WebhookLog", lambda: WebhookLog(":memory:"))
    monkeypatch.setenv("SOLVENT_DASHBOARD_TOKEN", "d" * 32)
    with TestClient(server.create_app()) as client:
        for headers in ({}, {"X-Solvent-Dashboard-Token": "d" * 32}):
            response = client.post(
                path, json={"job_id": JOB["id"], "block_hash": "B" * 64}, headers=headers
            )
            assert response.status_code == 404


@pytest.mark.parametrize(
    "setting", ["SOLVENT_NANO", "SOLVENT_NANO_RPC", "SOLVENT_NANO_CUSTODY", "SOLVENT_NANO_SEED"]
)
def test_doctor_flags_retired_config_without_loading_nano(agent, monkeypatch, setting):
    from solvent.doctor import run_checks

    monkeypatch.setenv(setting, "obsolete-secret-value")
    with mock.patch.dict(
        "sys.modules", {"solvent.nano_client": None, "solvent.nano_custody": None}
    ):
        checks = run_checks()
    check = next(c for c in checks if c["name"] == "retired_payment_config")
    assert not check["ok"]
    assert setting in check["detail"]
    assert "obsolete-secret-value" not in check["detail"]
    assert not {"nano_rail", "nano_custody"} & {c["name"] for c in checks}


@pytest.mark.parametrize("marker", MARKERS)
@pytest.mark.parametrize(
    "status", ["pending_quote", "awaiting_payment", "paid_pending_fulfill", "in_progress"]
)
def test_worker_queue_skips_history_and_resume_preserves_status(agent, marker, status):
    from solvent.queue import list_claimable, resume_incomplete_jobs

    historical_job(agent, marker, status)
    before = accounting_snapshot(agent.t)
    row = agent.t.get_job(JOB["id"])
    assert list_claimable(agent.t) == []
    assert resume_incomplete_jobs(agent.t) == []
    assert agent.t.get_job(JOB["id"]) == row
    assert accounting_snapshot(agent.t) == before


@pytest.mark.parametrize("marker", MARKERS)
def test_customer_status_never_advertises_historical_payment_link(agent, marker):
    from solvent.chat import _make_executor

    historical_job(agent, marker)
    agent.t.upsert_job(
        JOB["id"],
        "awaiting_payment",
        job_owner_session_id="customer",
        checkout_url="nano:nano_legacy?amount=1000",
    )
    execute = _make_executor(agent, "customer", live_search=False)
    result = json.loads(execute("job_status", {"job_id": JOB["id"]}))
    assert result["job"]["id"] == JOB["id"]
    assert not result["job"].get("checkout_url")
    assert "nano:nano_legacy" not in json.dumps(result)


@pytest.mark.parametrize("stage", ["paid", "fulfill"])
def test_direct_stage_rejects_retired_rail_before_booking_or_refunding(agent, stage):
    historical_job(agent, "invoice", "paid_pending_fulfill")
    before = accounting_snapshot(agent.t)
    q = quote(JOB)
    payment = {"rail": "nano", "paid": True, "stripe_ref": "B" * 64, "amount_cents": 4900}
    with (
        mock.patch.object(agent._runner, "stripe") as stripe,
        mock.patch("solvent.service.fulfill") as fulfill,
    ):
        if stage == "paid":
            result = agent._runner._stage_paid(JOB, payment, q)
        else:
            result = agent._runner._stage_fulfill_and_book(JOB, q, payment)
    assert result["stage"] == "payment_pending"
    assert result["reason"] == REASON
    assert stripe.mock_calls == []
    fulfill.assert_not_called()
    assert accounting_snapshot(agent.t) == before


def test_reopening_legacy_database_preserves_history_and_json_files(agent, tmp_path):
    from solvent.paths import config_path
    from solvent.treasury import Treasury

    historical_job(agent, "invoice", "paid_pending_fulfill")
    paths = [
        config_path(name)
        for name in ("nano_payments.json", "nano_custody_sim.json", "nano_custody_live.json")
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"historical": "preserve exactly"}')
    before = accounting_snapshot(agent.t)
    reopened = Treasury(agent.t.path)
    assert accounting_snapshot(reopened) == before
    assert reopened.list_nano_invoices() == agent.t.list_nano_invoices()
    assert all(path.read_text() == '{"historical": "preserve exactly"}' for path in paths)


@pytest.mark.parametrize("payment_ref", ["A" * 64, "block_sim_historical"])
def test_ledger_only_legacy_payment_cannot_resume(agent, payment_ref):
    t = agent.t
    t.upsert_job(JOB["id"], "paid_pending_fulfill", job_payload_json=JOB)
    t.earn(4900, "Historical Nano payment", job_id=JOB["id"], payment_ref=payment_ref)
    before = accounting_snapshot(t)
    with mock.patch("solvent.service.fulfill") as fulfill:
        assert agent.advance_job(JOB["id"])["reason"] == REASON
    fulfill.assert_not_called()
    assert accounting_snapshot(t) == before
