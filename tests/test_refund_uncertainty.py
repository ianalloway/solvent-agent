"""No network: a provider can accept a refund even when its response is lost."""

import json
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from solvent import service
from solvent.guardrails import Guardrails, SpendPolicy
from solvent.pricing import quote
from solvent.reconcile import reconcile
from solvent.stages import StageRunner
from solvent.treasury import REFUND_VENDOR, Treasury


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    monkeypatch.setattr("solvent.dashboard.render", Mock())
    t = Treasury(tmp_path / "treasury.db")
    t.seed(2000)
    job = {
        "id": "job",
        "topic": "AI chips",
        "budget_cents": 5000,
        "customer_email": "test@example.com",
        "est_tokens": 1000,
        "market_data_calls": 0,
        "web_search_calls": 0,
    }
    q = quote(job)
    t.upsert_job(
        "job", "paid_pending_fulfill", job_payload_json=job, quote_json=json.dumps(asdict(q))
    )
    t.earn(5000, "Payment", job_id="job", stripe_ref="pi_fake")
    provider = Mock()
    events = []
    r = StageRunner(t, Guardrails(t, SpendPolicy()), provider, on_event=events.append)
    monkeypatch.setattr(service, "fulfill", Mock(side_effect=RuntimeError("fulfill failed")))
    return t, r, provider, job, q, {"stripe_ref": "pi_fake", "amount_cents": 5000}, events


def confirmed():
    return {"id": "re_fake", "stripe_ref": "pi_fake", "amount_cents": 5000, "status": "succeeded"}


def test_accepted_then_timeout_blocks_every_resume_after_restart(setup, monkeypatch):
    t, r, provider, job, q, paid, events = setup
    accepted = []

    def accept_then_timeout(*args, **kwargs):
        # The intent and spend reservation MUST already be durable at the I/O boundary.
        reopened = Treasury(t.path)
        assert reopened.pending_refunds("job")[0]["provider_idempotency_key"] == "refund-job"
        assert sum(e.amount_cents for e in reopened.reserved_spends()) == 5000
        accepted.append(confirmed())
        raise TimeoutError("accepted; response lost")

    provider.refund_payment.side_effect = accept_then_timeout
    result = r._stage_fulfill_and_book(job, q, paid)
    assert accepted
    assert result["status"] == "refund_pending"
    assert t.job_refund_cents("job") == 0  # Do not invent a confirmed refund.
    assert t.job_net_paid_cents("job") == 0
    assert t.snapshot()["available_balance_cents"] == 2000
    assert not r.guard.evaluate(1, "nvidia-nemotron", 100).allowed
    assert not any(e["stage"] in ("refunded", "refund_receipt_ready", "booked") for e in events)
    assert reconcile(t)["drift"] is True

    reopened = Treasury(t.path)
    r = StageRunner(reopened, Guardrails(reopened), provider)
    fulfill = Mock(side_effect=AssertionError("must not fulfill after unresolved refund"))
    delivery = Mock()
    monkeypatch.setattr(service, "fulfill", fulfill)
    monkeypatch.setattr("solvent.delivery.send_brief_email", delivery)
    with pytest.raises(ValueError, match="unresolved refund"):
        r.retry_job("job")
    assert r.advance_job("job")["status"] == "refund_pending"
    assert r.run_job(job)["status"] == "refund_pending"
    assert r._stage_fulfill_and_book(job, q, paid)["status"] == "refund_pending"
    assert (reopened.get_job("job")["retry_count"] or 0) == 0
    assert reopened.get_stage("publish:job") is None
    fulfill.assert_not_called()
    delivery.assert_not_called()
    provider.refund_payment.assert_called_once()


def test_crash_after_provider_acceptance_preserves_intent(setup):
    t, r, provider, job, q, paid, _ = setup
    provider.refund_payment.side_effect = SystemExit("process died after provider accepted")
    with pytest.raises(SystemExit):
        r._stage_fulfill_and_book(job, q, paid)
    assert t.get_job("job")["status"] == "in_progress"
    assert Treasury(t.path).pending_refunds("job")
    assert r.advance_job("job")["status"] == "refund_pending"
    provider.refund_payment.assert_called_once()


@pytest.mark.parametrize(
    "change",
    [
        {"status": "pending"},
        {"status": "failed"},
        {"status": None},
        {"amount_cents": 4999},
        {"stripe_ref": "pi_other"},
        {"id": ""},
    ],
)
def test_unconfirmed_response_never_books_success(setup, change):
    t, r, provider, job, q, paid, _ = setup
    provider.refund_payment.return_value = {**confirmed(), **change}
    assert r._stage_fulfill_and_book(job, q, paid)["status"] == "refund_pending"
    assert t.job_refund_cents("job") == 0
    assert t.pending_refunds("job")


def test_confirmed_refund_is_atomic_and_idempotent(setup):
    t, r, provider, job, q, paid, _ = setup
    provider.refund_payment.return_value = confirmed()
    assert r._stage_fulfill_and_book(job, q, paid)["status"] == "failed"
    assert not t.pending_refunds()
    assert not t.reserved_spends()
    assert t.job_refund_cents("job") == 5000
    t.complete_refund("job", "pi_fake", "re_fake")
    assert t.job_refund_cents("job") == 5000
    assert t.get_stage("refund:job:pi_fake")["status"] == "completed"
    with pytest.raises(ValueError, match="refunded"):
        r.retry_job("job")


def test_booking_failure_keeps_reservation_and_intent(setup, monkeypatch):
    t, r, provider, job, q, paid, _ = setup
    provider.refund_payment.return_value = confirmed()
    monkeypatch.setattr(t, "complete_refund", Mock(side_effect=RuntimeError("disk error")))
    assert r._stage_fulfill_and_book(job, q, paid)["status"] == "refund_pending"
    assert t.job_refund_cents("job") == 0
    assert t.pending_refunds("job")
    assert t.reserved_spends()


def full_report(t, monkeypatch, refunds):
    sdk = Mock()
    sdk.PaymentIntent.list.return_value.auto_paging_iter.return_value = [
        SimpleNamespace(id="pi_fake", status="succeeded")
    ]
    sdk.Refund.list.return_value.auto_paging_iter.return_value = refunds
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_fake")
    monkeypatch.setattr("solvent.reconcile.stripe_sdk", sdk)
    monkeypatch.setattr("solvent.reconcile._HAS_STRIPE", True)
    return reconcile(t), sdk


def test_full_reconcile_finds_provider_refund_even_without_new_intent(setup, monkeypatch):
    t, *_ = setup
    report, _ = full_report(
        t, monkeypatch, [SimpleNamespace(id="re_fake", status="succeeded", amount=5000)]
    )
    assert report["mode"] == "full"
    assert not report["unmatched_stripe"]
    assert report["unmatched_stripe_refunds"] == ["re_fake"]
    assert report["drift"] is True


@pytest.mark.parametrize(
    "amount,status,drift",
    [
        (5000, "succeeded", False),
        (4999, "succeeded", True),
        (5000, "pending", True),
        (5000, "failed", True),
    ],
)
def test_full_reconcile_checks_refund_status_and_amount(setup, monkeypatch, amount, status, drift):
    t, *_ = setup
    t.spend(5000, "refund", job_id="job", vendor=REFUND_VENDOR, stripe_ref="re_fake")
    report, _ = full_report(
        t, monkeypatch, [SimpleNamespace(id="re_fake", status=status, amount=amount)]
    )
    assert report["drift"] is drift
    if amount != 5000:
        assert report["refund_amount_mismatches"] == ["re_fake"]


def test_refund_fetch_error_never_claims_full_reconciliation(setup, monkeypatch):
    t, *_ = setup
    _, sdk = full_report(t, monkeypatch, [])
    sdk.Refund.list.side_effect = TimeoutError("refund lookup unavailable")
    report = reconcile(t)
    assert report["mode"] == "ledger_only"
    assert "refund lookup unavailable" in report["stripe_error"]


def test_upgrade_quarantines_old_refund_failed_events_idempotently(setup):
    t, _, provider, *_ = setup
    t.record_event("job", "refund_failed", {"reason": "response timed out"})
    t.upsert_job("job", "failed")
    t = Treasury(t.path)
    assert len(t.pending_refunds()) == 1
    t = Treasury(t.path)
    assert len(t.reserved_spends()) == 1
    assert t.job_net_paid_cents("job") == 0
    r = StageRunner(t, Guardrails(t), provider)
    with pytest.raises(ValueError, match="unresolved refund"):
        r.retry_job("job")
    provider.refund_payment.assert_not_called()


def test_upgrade_does_not_hold_an_already_booked_refund(setup):
    t, *_ = setup
    t.record_event("job", "refund_failed", {"reason": "response timed out"})
    t.spend(
        5000, "refund confirmed later", job_id="job", vendor=REFUND_VENDOR, stripe_ref="re_fake"
    )
    assert not Treasury(t.path).pending_refunds()


def test_concurrent_refund_intent_reserves_once(setup):
    from concurrent.futures import ThreadPoolExecutor

    t, *_ = setup
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(lambda _: Treasury(t.path).begin_refund("job", "pi_fake", 5000), range(4))
        )
    assert sum(results) == 1
    assert len(t.pending_refunds()) == 1
    assert len(t.reserved_spends()) == 1


def test_finance_warns_about_retained_funds_uncertainty(setup, monkeypatch, capsys):
    from solvent import finance

    t, *_ = setup
    t.begin_refund("job", "pi_fake", 5000)
    monkeypatch.setattr(finance, "Treasury", lambda: t)
    monkeypatch.setattr("sys.argv", ["finance", "--json"])
    finance.main()
    report = json.loads(capsys.readouterr().out)
    assert report["pending_refund_cents"] == 5000
    assert report["available_balance_cents"] == 2000
    assert "not confirmed retained funds" in finance.format_report(report)


def test_crash_after_settlement_commit_is_terminal_on_restart(setup, monkeypatch):
    t, r, provider, job, q, paid, _ = setup
    provider.refund_payment.return_value = confirmed()
    complete = t.complete_refund

    def crash_after_commit(*args):
        complete(*args)
        raise SystemExit("process died after local commit")

    monkeypatch.setattr(t, "complete_refund", crash_after_commit)
    with pytest.raises(SystemExit):
        r._stage_fulfill_and_book(job, q, paid)
    reopened = Treasury(t.path)
    assert reopened.get_job("job")["status"] == "failed"
    assert reopened.get_metrics("job")["refunded"] == 1
    assert not reopened.pending_refunds()
    assert not reopened.reserved_spends()
    assert r.advance_job("job")["status"] == "failed"


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"status": "pending"},
        {"amount": 4999},
        {"currency": "eur"},
        {"payment_intent": "pi_wrong"},
        {"id": "re_wrong"},
    ],
)
def test_operator_resolution_requires_exact_provider_evidence(setup, monkeypatch, change):
    from solvent.reconcile import resolve_refund

    t, *_ = setup
    t.begin_refund("job", "pi_fake", 5000)
    _, sdk = full_report(t, monkeypatch, [])
    sdk.Refund.retrieve.return_value = SimpleNamespace(
        **{
            "id": "re_fake",
            "status": "succeeded",
            "amount": 5000,
            "currency": "usd",
            "payment_intent": "pi_fake",
            **change,
        }
    )
    if change:
        with pytest.raises(ValueError, match="not confirmed"):
            resolve_refund(t, "job", "re_fake")
        assert t.pending_refunds()
        assert t.job_refund_cents("job") == 0
    else:
        assert resolve_refund(t, "job", "re_fake")["status"] == "refunded"
        assert not t.pending_refunds()
        assert not t.reserved_spends()
        assert t.job_refund_cents("job") == 5000
        assert t.get_job("job")["status"] == "failed"
    sdk.Refund.create.assert_not_called()


def test_dashboard_keeps_pending_refund_warning_in_static_and_live_views(
    setup, monkeypatch, tmp_path
):
    from solvent import dashboard

    t, *_ = setup
    t.begin_refund("job", "pi_fake", 5000)
    status = dashboard.build_status_data(t.snapshot(), [])
    assert status["pending_refund_cents"] == 5000
    assert status["available_balance_cents"] == 2000
    # The fixture suppresses incidental renders; restore the real implementation.
    import importlib

    dashboard = importlib.reload(dashboard)
    monkeypatch.setattr(dashboard, "OUT", tmp_path / "dashboard.html")
    monkeypatch.setattr(dashboard, "_commercial_html", lambda: "")
    for live in (False, True):
        html = dashboard.render(t.snapshot(), [], live=live).read_text()
        assert 'id="refund-warning"' in html
        assert "Unresolved refunds: $50.00" in html
        assert "Available cash: $20.00" in html
        assert "updateRefundWarning(data);" in html


def test_offline_duplicate_refunds_are_drift(setup):
    t, *_ = setup
    for _ in range(2):
        t.spend(2500, "duplicate", job_id="job", vendor=REFUND_VENDOR, stripe_ref="re_fake")
    report = reconcile(t)
    assert report["mode"] == "ledger_only"
    assert report["duplicate_refunds"] == ["re_fake"]
    assert report["drift"] is True


def test_late_pending_notification_cannot_overwrite_confirmed_refund(setup):
    t, r, *_ = setup
    t.begin_refund("job", "pi_fake", 5000)
    assert t.pending_refunds("job")  # Caller observed pending, then a resolver won.
    t.complete_refund("job", "pi_fake", "re_fake")
    assert r._refund_pending("job")["status"] == "failed"
    assert t.get_job("job")["status"] == "failed"


def test_startup_resume_preserves_status_only_refund_hold(setup):
    from solvent.queue import resume_incomplete_jobs

    t, *_ = setup
    t.upsert_job("job", "refund_pending")
    assert resume_incomplete_jobs(t) == []
    assert t.get_job("job")["status"] == "refund_pending"


def test_old_provider_refund_booked_now_is_verified_by_id(setup, monkeypatch):
    t, *_ = setup
    t.spend(5000, "late resolution", job_id="job", vendor=REFUND_VENDOR, stripe_ref="re_fake")
    sdk = Mock()
    sdk.PaymentIntent.list.return_value.auto_paging_iter.return_value = [
        SimpleNamespace(id="pi_fake", status="succeeded")
    ]
    sdk.Refund.list.return_value.auto_paging_iter.return_value = []
    sdk.Refund.retrieve.return_value = SimpleNamespace(
        id="re_fake", status="succeeded", amount=5000
    )
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_fake")
    monkeypatch.setattr("solvent.reconcile.stripe_sdk", sdk)
    monkeypatch.setattr("solvent.reconcile._HAS_STRIPE", True)
    report = reconcile(t)
    sdk.Refund.retrieve.assert_called_once_with("re_fake")
    assert report["mode"] == "full"
    assert report["drift"] is False
