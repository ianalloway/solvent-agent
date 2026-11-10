"""Regression coverage for authorization before consumption and publication."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock
from urllib.error import URLError

import pytest

from solvent import llm_backend, nemotron, service, spending, tools
from solvent.guardrails import GuardrailError, Guardrails, SpendPolicy
from solvent.pricing import PricingPolicy, quote
from solvent.stages import StageRunner
from solvent.stripe_client import StripeClient
from solvent.treasury import Treasury


@pytest.fixture
def treasury(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_DELIVERY_SECRET", "x" * 32)
    monkeypatch.delenv("SOLVENT_LLM_MODEL", raising=False)
    monkeypatch.delenv("SOLVENT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(service, "OUTPUT_DIR", tmp_path / "reports")
    t = Treasury(tmp_path / "ledger.db")
    t.seed(100_000)
    return t


def runner(t, **policy):
    return StageRunner(t, Guardrails(t, SpendPolicy(**policy)), StripeClient(), PricingPolicy())


def paid_job(t):
    job = {"id": "job", "topic": "AI chips", "budget_cents": 5000}
    t.upsert_job("job", "paid_pending_fulfill")
    t.earn(5000, "Paid", job_id="job", stripe_ref="pi_test")
    q = quote(job)
    paid = {"stripe_ref": "pi_test", "amount_cents": 5000}
    return job, q, paid


def response(usage=None, text="# Brief"):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    data = {"choices": [{"message": {"content": text}}]}
    if usage is not None:
        data["usage"] = usage
    result.read.return_value = json.dumps(data).encode()
    return result


@pytest.mark.parametrize(
    "sink",
    [
        lambda: nemotron._live_request("system", "user"),
        lambda: llm_backend.complete("system", "user"),
        lambda: tools._live_web_search("chips"),
        lambda: tools._live_market_data("NVDA"),
    ],
)
def test_denied_reservation_never_reaches_network(treasury, monkeypatch, sink):
    network = Mock(side_effect=AssertionError("network must not run"))
    monkeypatch.setattr(nemotron.urllib.request, "urlopen", network)
    monkeypatch.setattr(llm_backend._OPENER, "open", network)
    with spending.scope(Guardrails(treasury, SpendPolicy(job_budget_cents=1)), "job", 100):
        with pytest.raises(GuardrailError):
            sink()
    network.assert_not_called()
    assert not treasury.reserved_spends()
    assert treasury.expense_cents() == 0


def test_denial_prevents_live_fulfillment_and_delivery(treasury, monkeypatch):
    r = runner(treasury, job_budget_cents=1)
    args = paid_job(treasury)
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(nemotron, "_force_offline", False)
    network = Mock()
    email = Mock()
    monkeypatch.setattr(nemotron.urllib.request, "urlopen", network)
    monkeypatch.setattr("solvent.delivery.send_brief_email", email)
    assert r._stage_fulfill_and_book(*args)["status"] == "failed"
    network.assert_not_called()
    email.assert_not_called()
    assert treasury.job_refund_cents("job") == 5000
    assert treasury.get_stage("publish:job") is None


@pytest.mark.parametrize(
    "limit", ["job_budget_cents", "daily_budget_cents", "per_vendor_daily_cents"]
)
def test_concurrent_workers_cannot_overreserve(treasury, limit):
    barrier = Barrier(8)

    def attempt(_):
        # Independent handles model different workers sharing the SQLite file.
        t = Treasury(treasury.path)
        g = Guardrails(t, SpendPolicy(**{limit: 100}))
        barrier.wait()
        try:
            return g.reserve(20, "nvidia-nemotron", "request", job_id="job")
        except GuardrailError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(attempt, range(8)))
    assert len([i for i in ids if i]) == 5
    assert sum(e.amount_cents for e in treasury.reserved_spends()) == 100
    assert not Guardrails(Treasury(treasury.path), SpendPolicy(**{limit: 100})).approve(
        1, "nvidia-nemotron", job_id="job"
    )


def test_reservation_survives_restart_and_settlement_is_bounded_and_idempotent(treasury):
    g = Guardrails(treasury, SpendPolicy(job_budget_cents=100))
    rid = g.reserve(100, "nvidia-nemotron", "request", job_id="job")
    reopened = Treasury(treasury.path)
    with pytest.raises(ValueError):
        reopened.settle_spend(rid, 101)
    assert len(reopened.reserved_spends()) == 1
    reopened.settle_spend(rid, 40)
    reopened.settle_spend(rid, 40)
    assert reopened.expense_cents() == 40
    assert not reopened.reserved_spends()
    assert g.approve(60, "nvidia-nemotron", job_id="job")
    assert not g.approve(61, "nvidia-nemotron", job_id="job")


def test_timeout_cost_is_retained_and_retry_needs_new_authorization(treasury, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(nemotron, "MAX_RETRIES", 2)
    monkeypatch.setattr(nemotron.time, "sleep", lambda _: None)
    network = Mock(side_effect=URLError("timeout after acceptance"))
    monkeypatch.setattr(nemotron.urllib.request, "urlopen", network)
    # This prompt's request ceiling is 44 cents, leaving no room for a retry.
    with spending.scope(Guardrails(treasury, SpendPolicy(job_budget_cents=44)), "job", 100):
        with pytest.raises(GuardrailError):
            nemotron._live_complete("sys", "user")
    assert network.call_count == 1
    assert treasury.expense_cents() == 44
    assert not treasury.reserved_spends()


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {"total_tokens": -1},
        {"prompt_tokens": 1, "completion_tokens": 1201, "total_tokens": 1202},
    ],
)
def test_missing_or_invalid_usage_never_releases_unknown_cost(treasury, monkeypatch, usage):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(nemotron.urllib.request, "urlopen", Mock(return_value=response(usage)))
    with spending.scope(Guardrails(treasury), "job", 100):
        if usage is None:
            nemotron._live_request("sys", "user")
        else:
            with pytest.raises(GuardrailError):
                nemotron._live_request("sys", "user")
    assert treasury.expense_cents() == 44


def test_validated_usage_releases_only_unused_headroom(treasury, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    usage = {"prompt_tokens": 20, "completion_tokens": 80, "total_tokens": 100}
    network = Mock(return_value=response(usage))
    monkeypatch.setattr(nemotron.urllib.request, "urlopen", network)
    with spending.scope(Guardrails(treasury, SpendPolicy(job_budget_cents=47)), "job", 100):
        nemotron._live_request("sys", "user")
        nemotron._live_request("sys", "user")
        with pytest.raises(GuardrailError):
            nemotron._live_request("sys", "user")
    assert network.call_count == 2
    assert treasury.expense_cents() == 6


def result_file(tmp_path, resources):
    path = tmp_path / "brief.md"
    path.write_text("# Brief")
    return {
        "deliverable_path": str(path),
        "tokens": 100,
        "resources_used": resources,
        "actual_cost_cents": sum(x[1] for x in resources),
    }


@pytest.mark.parametrize("cached", [False, True])
def test_resource_denial_precedes_publication_even_on_resume(
    treasury, tmp_path, monkeypatch, cached
):
    r = runner(treasury, max_txn_cents=1)
    args = paid_job(treasury)
    result = result_file(tmp_path, [("pdf-render-saas", 40, "pdf")])
    fulfill = Mock(return_value=result)
    email = Mock()
    monkeypatch.setattr(service, "fulfill", fulfill)
    monkeypatch.setattr("solvent.delivery.send_brief_email", email)
    if cached:
        treasury.complete_stage("job", "fulfill", "fulfill:job", result)
    assert r._stage_fulfill_and_book(*args)["status"] == "failed"
    email.assert_not_called()
    assert not treasury.get_stage("publish:job")
    assert treasury.job_refund_cents("job") == 5000
    if cached:
        fulfill.assert_not_called()
    else:
        saved = json.loads(treasury.get_stage("fulfill:job")["result_json"])
        assert saved["resources_used"] == [["pdf-render-saas", 40, "pdf"]]


def test_partial_vendor_booking_does_not_skip_remaining_charge(treasury, tmp_path, monkeypatch):
    r = runner(treasury)
    args = paid_job(treasury)
    treasury.spend(10, "live", vendor="nvidia-nemotron", job_id="job")
    monkeypatch.setattr(
        service,
        "fulfill",
        Mock(
            return_value=result_file(
                tmp_path, [("nvidia-nemotron", 30, "inference"), ("nvidia-nemotron", 20, "retry")]
            )
        ),
    )

    def send(*a, **kw):
        assert treasury.expense_cents() == 50
        assert treasury.get_stage("publish:job")

    monkeypatch.setattr("solvent.delivery.send_brief_email", send)
    assert r._stage_fulfill_and_book(*args)["status"] == "completed"
    assert treasury.expense_cents() == 50


def test_delivery_ambiguity_does_not_refund_published_value(treasury, tmp_path, monkeypatch):
    r = runner(treasury)
    args = paid_job(treasury)
    monkeypatch.setattr(
        service,
        "fulfill",
        Mock(return_value=result_file(tmp_path, [("email-delivery-saas", 5, "email")])),
    )
    monkeypatch.setattr("solvent.delivery.send_brief_email", Mock(side_effect=OSError("lost ack")))
    assert r._stage_fulfill_and_book(*args)["status"] == "failed"
    assert treasury.get_stage("publish:job")
    assert treasury.job_refund_cents("job") == 0


def test_duplicate_advancers_fulfill_and_deliver_only_once(treasury, tmp_path, monkeypatch):
    r = runner(treasury)
    args = paid_job(treasury)
    fulfill = Mock(return_value=result_file(tmp_path, [("pdf-render-saas", 40, "pdf")]))
    email = Mock()
    monkeypatch.setattr(service, "fulfill", fulfill)
    monkeypatch.setattr("solvent.delivery.send_brief_email", email)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: r._stage_fulfill_and_book(*args), range(2)))
    assert all(result["status"] == "completed" for result in results)
    assert fulfill.call_count == email.call_count == 1
    assert treasury.expense_cents() == 40


def test_direct_live_adapter_uses_unscoped_policy(treasury, monkeypatch):
    monkeypatch.setattr("solvent.treasury.DB_PATH", treasury.path)
    monkeypatch.setenv("SOLVENT_JOB_BUDGET_CENTS", "1")
    network = Mock()
    monkeypatch.setattr(nemotron.urllib.request, "urlopen", network)
    with pytest.raises(GuardrailError):
        nemotron._live_request("system", "user")
    network.assert_not_called()


def test_nested_summary_and_quality_retry_share_job_budget(treasury, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(nemotron, "_force_offline", False)
    usage = {"prompt_tokens": 20, "completion_tokens": 80, "total_tokens": 100}
    summary = '<tool_call>{"name":"summarize","arguments":{"text":"notes"}}</tool_call>'
    network = Mock(
        side_effect=[
            response(usage, summary),
            response(usage, "Summary"),
            response(usage, "# Brief\nShort"),
            response(usage, "# Brief\nRetry"),
        ]
    )
    monkeypatch.setattr(nemotron.urllib.request, "urlopen", network)
    with spending.scope(Guardrails(treasury), "job", 1000):
        result = service.fulfill({"id": "job", "topic": "AI chips", "est_tokens": 20_000})
    assert network.call_count == 4
    assert result["quality_attempts"] == 2
    assert result["tokens"] >= 400  # quoted allowances retain conservative token bounds
    charges = [e for e in treasury.entries if e.kind == "expense"]
    assert len(charges) == 4
    assert {e.job_id for e in charges} == {"job"}
    assert sum(e.amount_cents for e in charges) == 12


def test_crashed_request_blocks_resume_before_more_provider_calls(treasury, monkeypatch):
    r = runner(treasury)
    args = paid_job(treasury)
    r.guard.reserve(100, "nvidia-nemotron", "lost worker", job_id="job")
    fulfill = Mock()
    monkeypatch.setattr(service, "fulfill", fulfill)
    assert r._stage_fulfill_and_book(*args)["status"] == "failed"
    fulfill.assert_not_called()
    assert len(treasury.reserved_spends()) == 1
    assert not treasury.get_stage("publish:job")


@pytest.mark.parametrize(
    "status, expected", [("completed", 200), ("failed", 404), ("in_progress", 404)]
)
def test_legacy_hosted_access_requires_successful_booking(
    treasury, tmp_path, monkeypatch, status, expected
):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from solvent.delivery import make_delivery_token
    from solvent.server import create_app

    monkeypatch.setattr("solvent.treasury.DB_PATH", treasury.path)
    monkeypatch.setenv("SOLVENT_DASHBOARD_TOKEN", "d" * 32)
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    (reports / "legacy.md").write_text("# Legacy brief")
    app = create_app()
    app.state.agent.t.upsert_job("legacy", status)
    client = TestClient(app, client=("203.0.113.10", 12345))
    token = make_delivery_token("legacy")
    for path in ("/briefs/legacy", "/api/briefs/legacy"):
        assert client.get(f"{path}?token={token}").status_code == expected


def test_quote_exhaustion_does_not_book_an_unmade_provider_request(treasury, monkeypatch):
    from solvent.pricing import get_resource_costs
    from solvent.resource_budget import (
        BudgetExceeded,
        ResourceBudget,
        ResourceContract,
        using_budget,
    )

    budget = ResourceBudget(ResourceContract.for_job({"est_tokens": 4000}, get_resource_costs()))
    budget.reserve(tokens=4000)
    network = Mock()
    monkeypatch.setattr(nemotron.urllib.request, "urlopen", network)
    with using_budget(budget), spending.scope(Guardrails(treasury), "job", 100):
        with pytest.raises(BudgetExceeded):
            nemotron._live_request("system", "user")
    network.assert_not_called()
    assert treasury.expense_cents() == 0
    assert not treasury.reserved_spends()


def test_live_settlement_does_not_charge_unused_quote_allowance(treasury, monkeypatch):
    r = runner(treasury)
    job, _, paid = paid_job(treasury)
    job["est_tokens"] = 4000
    q = quote(job)
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(nemotron, "_force_offline", False)
    usage = {"prompt_tokens": 20, "completion_tokens": 80, "total_tokens": 100}
    network = Mock(return_value=response(usage, "# Brief\nShort"))
    monkeypatch.setattr(nemotron.urllib.request, "urlopen", network)
    email = Mock()
    monkeypatch.setattr("solvent.delivery.send_brief_email", email)
    assert r._stage_fulfill_and_book(job, q, paid)["status"] == "completed"
    # The quality retry exceeds the quote and never runs. Its unused token
    # allowance must not be paid as an extra inference charge at booking.
    assert network.call_count == email.call_count == 1
    assert treasury.expense_cents() == 3 + 40 + 5
    assert treasury.get_metrics("job")["actual_cost_cents"] == 48
