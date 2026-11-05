"""Regression coverage for underquoted research and retry budget resets."""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from solvent import llm_backend, nemotron, pricing, service, tools
from solvent import spending
from solvent.guardrails import Guardrails
from solvent.resource_budget import (
    BudgetExceeded,
    ResourceBudget,
    ResourceContract,
    current_budget,
    using_budget,
)
from solvent.treasury import Treasury


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("NVIDIA_API_KEY", "test-only")
    monkeypatch.delenv("SOLVENT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("SOLVENT_LLM_MODEL", raising=False)
    monkeypatch.setattr(nemotron, "_force_offline", False)
    monkeypatch.setattr(service, "OUTPUT_DIR", tmp_path / "reports")
    treasury = Treasury(tmp_path / "authorization.db")
    treasury.seed(100_000)
    with spending.scope(Guardrails(treasury), "budget-test", 10_000):
        yield


def contract(**scope):
    return ResourceContract.for_job(scope, pricing.get_resource_costs())


def response(text="# Brief\nToo short."):
    reply = Mock()
    reply.__enter__ = Mock(return_value=reply)
    reply.__exit__ = Mock(return_value=False)
    reply.read.return_value = json.dumps(
        {
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }
    ).encode()
    return reply


def test_zero_estimates_cannot_remove_server_minimum():
    zero = {"est_tokens": 0, "web_search_calls": 0, "market_data_calls": 0}
    floor = {"est_tokens": 4000, "web_search_calls": 3, "market_data_calls": 0}
    assert pricing.estimate_cost(zero) == pricing.estimate_cost(floor)
    q = pricing.quote({**zero, "budget_cents": 1500})
    saved = ResourceContract(**q.resource_contract)
    assert saved.tokens == 4000
    assert saved.attempts == 2
    assert q.est_cost_cents == sum(saved.breakdown().values())


@pytest.mark.parametrize("value", [-1, True, float("nan"), "0"])
def test_invalid_scope_is_not_a_discount(value):
    with pytest.raises(ValueError):
        pricing.quote({"budget_cents": 1500, "est_tokens": value})


@pytest.mark.parametrize("backend", ["nvidia", "compatible"])
def test_token_reservation_blocks_network_and_keeps_output_capped(backend, monkeypatch):
    request = Mock(return_value=response())
    if backend == "nvidia":
        monkeypatch.setattr(nemotron.urllib.request, "urlopen", request)
        complete = nemotron._live_complete
    else:
        monkeypatch.setattr(llm_backend._OPENER, "open", request)
        complete = llm_backend.complete
    budget = ResourceBudget(contract(est_tokens=4000))
    with using_budget(budget):
        complete("s", "u")
        complete("s", "u")
        with pytest.raises(BudgetExceeded):
            complete("s", "u")
    assert request.call_count == 2
    assert budget.used["tokens"] == 2 * (2 + 256 + 1200)
    for call in request.call_args_list:
        assert json.loads(call.args[0].data)["max_tokens"] == 1200


def test_large_unicode_prompt_is_rejected_before_network(monkeypatch):
    request = Mock()
    monkeypatch.setattr(nemotron.urllib.request, "urlopen", request)
    with using_budget(ResourceBudget(contract(est_tokens=4000))):
        with pytest.raises(BudgetExceeded):
            nemotron.complete("system", "🦔" * 1000)
    request.assert_not_called()


def test_transient_failures_keep_reservations(monkeypatch):
    request = Mock(side_effect=TimeoutError)
    monkeypatch.setattr(nemotron.urllib.request, "urlopen", request)
    monkeypatch.setattr(nemotron.time, "sleep", lambda _: None)
    monkeypatch.setattr(nemotron, "MAX_RETRIES", 20)
    budget = ResourceBudget(contract(est_tokens=4000))
    with using_budget(budget):
        with pytest.raises(BudgetExceeded):
            nemotron.complete("s", "u")
    assert request.call_count == 2
    assert budget.used["tokens"] == 2916


def test_tool_limits_apply_across_contexts_before_side_effects(monkeypatch):
    search = Mock(return_value="result")
    data = Mock(return_value="result")
    monkeypatch.setattr(tools, "web_search", search)
    monkeypatch.setattr(tools, "market_data", data)
    budget = ResourceBudget(contract(web_search_calls=3, market_data_calls=0))
    with using_budget(budget):
        for _ in range(3):
            tools.dispatch("web_search", {"query": "q"}, tools.ToolContext(), None, True)
        with pytest.raises(BudgetExceeded):
            tools.dispatch("web_search", {"query": "q"}, tools.ToolContext(), None, True)
        with pytest.raises(BudgetExceeded):
            tools.dispatch("market_data", {"symbol": "NVDA"}, tools.ToolContext(), None, True)
    assert search.call_count == 3
    data.assert_not_called()
    assert budget.used["tool_calls"] == 3


def test_nested_summary_uses_same_token_allowance(monkeypatch):
    request = Mock(return_value=response())
    monkeypatch.setattr(nemotron.urllib.request, "urlopen", request)
    budget = ResourceBudget(contract(est_tokens=4000))
    with using_budget(budget):
        nemotron.complete("s", "u")
        tools.dispatch("summarize", {"text": "notes"}, tools.ToolContext(), nemotron.complete)
        with pytest.raises(BudgetExceeded):
            nemotron.complete("s", "u")
    assert request.call_count == 2


def test_quality_retry_shares_budget_and_retains_first_draft(monkeypatch):
    request = Mock(return_value=response())
    monkeypatch.setattr(nemotron.urllib.request, "urlopen", request)
    job = {
        "id": "J-budget",
        "topic": "chips",
        "est_tokens": 4000,
        "web_search_calls": 0,
        "market_data_calls": 0,
        "budget_cents": 1500,
    }
    result = service.fulfill(job)
    # First draft fits; the retry cannot fit its prompt plus output reservation.
    assert request.call_count == 1
    assert result["text"].startswith("# Brief")
    assert result["tool_ctx"].budget_exhausted
    assert result["actual_cost_cents"] <= pricing.quote(job).est_cost_cents
    assert result["usage"]["estimated"]
    assert current_budget() is None


def test_both_attempts_tools_are_billed_even_if_first_draft_wins(monkeypatch):
    drafts = [tools.ToolContext(web_search_calls=2), tools.ToolContext(web_search_calls=1)]
    monkeypatch.setattr(
        nemotron,
        "research_brief",
        Mock(
            side_effect=[
                ("# Brief\nShort.", {"total_tokens": 100}, drafts[0]),
                ("# Brief\nShort.", {"total_tokens": 100}, drafts[1]),
            ]
        ),
    )
    result = service.fulfill({"id": "J-tools", "topic": "chips"})
    assert result["tool_ctx"].web_search_calls == 3
    assert (
        next(cost for vendor, cost, _ in result["resources_used"] if vendor == "web-search-api")
        == 24
    )


def test_reservations_survive_restart_and_are_atomic(tmp_path):
    treasury = Treasury(tmp_path / "treasury.db")
    c = contract(web_search_calls=3)

    def run(_):
        # Each worker starts with an empty in-memory budget.
        store = Treasury(tmp_path / "treasury.db")
        budget = ResourceBudget(
            c, lambda limits, inc: store.reserve_job_resources("J1", limits, inc)
        )
        try:
            budget.reserve(web_search=1)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=5) as pool:
        assert sum(pool.map(run, range(10))) == 3
    saved = treasury.get_stage("resource_budget:J1")
    assert json.loads(saved["result_json"])["web_search"] == 3


def test_budget_context_does_not_leak_after_failure():
    first, second = ResourceBudget(contract()), ResourceBudget(contract())
    with using_budget(first):
        with pytest.raises(BudgetExceeded), using_budget(second):
            second.reserve(tokens=100_000)
        assert current_budget() is first
    assert current_budget() is None


def test_stages_use_saved_quote_and_preserve_attempt_cap(tmp_path, monkeypatch):
    from solvent import treasury
    from solvent.agent import Solvent

    monkeypatch.setattr(treasury, "DB_PATH", tmp_path / "stages.db")
    monkeypatch.setenv("SOLVENT_FORCE_STRIPE_SIMULATE", "1")
    agent = Solvent(seed_cents=50_000, fresh=True)
    job = {
        "id": "J-saved",
        "topic": "chips",
        "budget_cents": 5000,
        "est_tokens": 4000,
        "market_data_calls": 0,
        "web_search_calls": 3,
    }
    first = agent._runner._stage_quote(job)["_quote"]
    # A resumed/cached quote must not use an edited payload or current prices.
    job["est_tokens"] = 100_000
    monkeypatch.setattr(
        pricing,
        "get_resource_costs",
        lambda: {
            **pricing.RESOURCE_COSTS_CENTS,
            "nemotron_tokens_per_1k": 999,
        },
    )
    saved = agent._runner._stage_quote(job)["_quote"]
    assert saved.resource_contract == first.resource_contract
    assert saved.est_cost_cents == first.est_cost_cents

    observed = []

    def fail_after_reservation(_job):
        budget = current_budget()
        observed.append(budget.contract.tokens)
        budget.reserve(attempts=1)
        raise RuntimeError("simulated crash after reservation")

    monkeypatch.setattr(service, "fulfill", fail_after_reservation)
    paid = {"amount_cents": 5000}
    for _ in range(3):
        agent._runner._stage_fulfill_and_book(job, saved, paid)
    assert observed == [4000, 4000, 4000]
    state = agent.t.get_stage("resource_budget:J-saved")
    assert json.loads(state["result_json"])["attempts"] == 2


def test_legacy_quote_is_refused_before_fulfillment(tmp_path, monkeypatch):
    from solvent import treasury
    from solvent.agent import Solvent

    monkeypatch.setattr(treasury, "DB_PATH", tmp_path / "legacy.db")
    monkeypatch.setenv("SOLVENT_FORCE_STRIPE_SIMULATE", "1")
    agent = Solvent(seed_cents=50_000, fresh=True)
    job = {"id": "J-legacy", "topic": "chips", "budget_cents": 5000}
    old = pricing.quote(job)
    old.resource_contract = None
    fulfill = Mock()
    monkeypatch.setattr(service, "fulfill", fulfill)
    event = agent._runner._stage_fulfill_and_book(job, old, {"amount_cents": 5000})
    assert event["status"] == "failed"
    fulfill.assert_not_called()
