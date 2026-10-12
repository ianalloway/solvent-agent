"""Tests for operator-only business commands over chat (solvent.ops)."""

from __future__ import annotations

import pytest

from solvent import ops
from solvent.treasury import Treasury


@pytest.fixture
def treasury(tmp_path):
    t = Treasury(path=tmp_path / "ledger.db")
    t.seed(100_000)
    t.upsert_job(
        "J1", "completed", topic="a brief", budget_cents=4_900, customer_email="a@x.example"
    )
    t.upsert_checkout("J1", "cs_1", "https://pay/1", "paid")
    t.earn(4_900, "paid J1", job_id="J1", stripe_ref="pi_1")
    t.spend(500, "inference", job_id="J1", vendor="nvidia-nemotron")
    t.upsert_metrics(
        "J1",
        est_cost_cents=600,
        actual_cost_cents=500,
        quality_score=100.0,
        quality_grade="pass",
        quality_flags="",
    )
    return t


# --- who counts as the operator --------------------------------------------


def test_the_dashboard_is_the_operator_by_construction():
    assert ops.is_operator("dashboard", "anyone")
    assert ops.is_operator("cli", "")


def test_a_paired_telegram_user_is_not_an_operator(monkeypatch):
    monkeypatch.delenv(ops.OPERATOR_IDS_ENV, raising=False)
    assert not ops.is_operator("telegram", "123456")


def test_a_listed_telegram_id_is_an_operator(monkeypatch):
    monkeypatch.setenv(ops.OPERATOR_IDS_ENV, "123456, 999")
    assert ops.is_operator("telegram", "123456")
    assert ops.is_operator("telegram", "999")
    assert not ops.is_operator("telegram", "7")


def test_an_empty_operator_list_does_not_let_everyone_in(monkeypatch):
    monkeypatch.setenv(ops.OPERATOR_IDS_ENV, "  ,  ")
    assert ops.operator_ids() == frozenset()
    assert not ops.is_operator("telegram", "")


# --- the gate ---------------------------------------------------------------


def test_a_customer_is_told_the_command_is_not_theirs(treasury, monkeypatch):
    monkeypatch.delenv(ops.OPERATOR_IDS_ENV, raising=False)
    answer = ops.handle("/funnel", channel="telegram", external_id="123", treasury=treasury)
    assert answer == "That command is operator-only."


def test_no_figures_leak_to_a_customer(treasury, monkeypatch):
    monkeypatch.delenv(ops.OPERATOR_IDS_ENV, raising=False)
    for command in ops.COMMANDS:
        answer = ops.handle(command, channel="telegram", external_id="123", treasury=treasury)
        assert answer == "That command is operator-only."
        assert "$" not in answer


def test_an_unknown_command_is_not_claimed(treasury):
    assert ops.handle("/nonsense", channel="dashboard", external_id="x", treasury=treasury) is None


def test_a_broken_report_does_not_look_like_a_broken_bot(treasury, monkeypatch):
    def _boom(_t):
        raise RuntimeError("db gone")

    monkeypatch.setitem(ops.COMMANDS, "/funnel", ("x", _boom))
    answer = ops.handle("/funnel", channel="dashboard", external_id="x", treasury=treasury)
    assert "Could not read that right now" in answer
    assert "RuntimeError" in answer


# --- the reports ------------------------------------------------------------


@pytest.mark.parametrize("command", sorted(ops.COMMANDS))
def test_every_report_answers_for_an_operator(treasury, command):
    answer = ops.handle(command, channel="dashboard", external_id="x", treasury=treasury)
    assert isinstance(answer, str) and answer.strip()


def test_the_funnel_report_names_the_win_rate(treasury):
    answer = ops.handle("/funnel", channel="dashboard", external_id="x", treasury=treasury)
    assert "Submitted 1" in answer
    assert "Gate acceptance" in answer


def test_the_customers_report_lists_the_book(treasury):
    answer = ops.handle("/customers", channel="dashboard", external_id="x", treasury=treasury)
    assert "a@x.example" in answer
    assert "1 customers" in answer


def test_the_capacity_report_names_the_binding_rule(treasury):
    answer = ops.handle("/capacity", channel="dashboard", external_id="x", treasury=treasury)
    assert "Ceiling:" in answer
    assert "Bound by:" in answer


def test_the_alerts_report_says_all_clear_when_it_is(treasury):
    answer = ops.handle("/alerts", channel="dashboard", external_id="x", treasury=treasury)
    assert "checks pass" in answer


def test_the_alerts_report_names_problems(treasury, tmp_path):
    thin = Treasury(path=tmp_path / "thin.db")
    thin.seed(100)  # under the reserve
    answer = ops.handle("/alerts", channel="dashboard", external_id="x", treasury=thin)
    assert "CRITICAL" in answer
    assert "cash" in answer


def test_the_products_report_prices_the_list(treasury):
    answer = ops.handle("/products", channel="dashboard", external_id="x", treasury=treasury)
    assert "express" in answer
    assert "margin" in answer


def test_the_quality_report_summarises_scores(treasury):
    answer = ops.handle("/quality", channel="dashboard", external_id="x", treasury=treasury)
    assert "mean 100.0/100" in answer


def test_the_review_report_is_empty_when_nothing_is_held(treasury):
    assert "Nothing waiting" in ops.handle(
        "/review", channel="dashboard", external_id="x", treasury=treasury
    )


def test_the_review_report_lists_held_jobs(treasury):
    treasury.upsert_job(
        "BIG",
        "failed",
        topic="teardown",
        budget_cents=500_000,
        customer_email="w@f.example",
        error_reason="intake: oversized_order — above the ceiling",
    )
    answer = ops.handle("/review", channel="dashboard", external_id="x", treasury=treasury)
    assert "BIG" in answer
    assert "oversized_order" in answer


def test_the_costs_report_names_the_calibration(treasury):
    answer = ops.handle("/costs", channel="dashboard", external_id="x", treasury=treasury)
    assert "Calibration in force" in answer


def test_help_lines_cover_every_command():
    lines = ops.help_lines()
    assert len(lines) == len(ops.COMMANDS)
    for command in ops.COMMANDS:
        assert any(line.startswith(command) for line in lines)


# --- gateway integration ----------------------------------------------------


@pytest.fixture
def gateway_with(tmp_path, monkeypatch):
    """A gateway on its own treasury, with the module-level limiter isolated."""
    from solvent import gateway as gateway_module
    from solvent.agent import Solvent
    from solvent.rate_limit import RateLimiter

    def _build() -> "gateway_module.Gateway":
        agent = Solvent(seed_cents=10_000, fresh=True)
        db = tmp_path / "t.db"
        agent.t.path = db
        agent.t.lock_path = db.with_suffix(".lock")
        agent.t._init_db()
        agent.t.seed(10_000)
        monkeypatch.setattr(gateway_module, "_rate_limiter", RateLimiter(db_path=":memory:"))
        return gateway_module.Gateway(agent=agent)

    return _build


def test_the_gateway_answers_an_operator_on_the_dashboard(gateway_with):
    answer = gateway_with().handle_inbound("dashboard", "op-1", "/capacity")
    assert "Ceiling:" in answer


def test_the_gateway_withholds_from_a_telegram_customer(gateway_with, monkeypatch):
    monkeypatch.delenv(ops.OPERATOR_IDS_ENV, raising=False)
    monkeypatch.setenv("SOLVENT_TELEGRAM_DM_POLICY", "open")

    answer = gateway_with().handle_inbound("telegram", "555", "/funnel")
    assert answer == "That command is operator-only."


def test_help_lists_ops_commands_only_for_the_operator(gateway_with, monkeypatch):
    monkeypatch.delenv(ops.OPERATOR_IDS_ENV, raising=False)
    monkeypatch.setenv("SOLVENT_TELEGRAM_DM_POLICY", "open")
    gateway = gateway_with()

    assert "/funnel" in gateway.handle_inbound("dashboard", "op-1", "/help")
    assert "/funnel" not in gateway.handle_inbound("telegram", "555", "/help")
