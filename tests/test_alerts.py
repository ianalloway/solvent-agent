"""Tests for the health sweep (solvent.alerts)."""

from __future__ import annotations

import time
from unittest import mock

import pytest

from solvent.alerts import (
    CRITICAL,
    OK,
    WARN,
    Alert,
    check_blocked_spends,
    check_cash,
    check_cost_model,
    check_spend_headroom,
    check_stuck_jobs,
    check_unpaid_pipeline,
    check_vendor_exposure,
    format_alerts,
    notify,
    run_checks,
    summarise,
    worst_severity,
)
from solvent.guardrails import SpendPolicy
from solvent.treasury import Treasury

HOUR = 3_600


@pytest.fixture
def treasury(tmp_path):
    t = Treasury(path=tmp_path / "ledger.db")
    t.seed(100_000)
    return t


# --- cash -------------------------------------------------------------------


def test_cash_is_critical_below_the_reserve(tmp_path):
    thin = Treasury(path=tmp_path / "thin.db")
    thin.seed(500)  # under the $20 reserve
    alert = check_cash(thin)
    assert alert.severity == CRITICAL
    assert "below the" in alert.message
    assert alert.action


def test_cash_is_ok_with_a_healthy_balance(treasury):
    assert check_cash(treasury).severity == OK


def test_short_runway_is_critical(treasury):
    with mock.patch(
        "solvent.alerts.runway",
        return_value={"status": "burning", "runway_days": 3.0},
    ):
        alert = check_cash(treasury)
    assert alert.severity == CRITICAL
    assert "3.0 days" in alert.message


def test_a_month_of_runway_only_warns(treasury):
    with mock.patch(
        "solvent.alerts.runway",
        return_value={"status": "burning", "runway_days": 20.0},
    ):
        assert check_cash(treasury).severity == WARN


# --- delivery ---------------------------------------------------------------


def test_paid_but_undelivered_work_is_flagged(treasury):
    treasury.upsert_job("J1", "paid_pending_fulfill", topic="t", budget_cents=4_900)
    now = time.time() + 3 * HOUR
    alert = check_stuck_jobs(treasury, now=now)
    assert alert.severity == WARN
    assert "J1" in alert.message


def test_long_stuck_work_is_critical(treasury):
    treasury.upsert_job("J1", "in_progress", topic="t", budget_cents=4_900)
    assert check_stuck_jobs(treasury, now=time.time() + 24 * HOUR).severity == CRITICAL


def test_fresh_work_is_not_stuck(treasury):
    treasury.upsert_job("J1", "in_progress", topic="t", budget_cents=4_900)
    assert check_stuck_jobs(treasury).severity == OK


# --- spend ------------------------------------------------------------------


def test_spend_headroom_warns_as_the_budget_fills(treasury):
    policy = SpendPolicy()
    treasury.spend(
        int(policy.daily_budget_cents * 0.85), "burn", job_id="J1", vendor="nvidia-nemotron"
    )
    assert check_spend_headroom(treasury).severity == WARN


def test_spend_headroom_is_critical_when_nearly_spent(treasury):
    policy = SpendPolicy()
    treasury.spend(
        int(policy.daily_budget_cents * 0.97), "burn", job_id="J1", vendor="nvidia-nemotron"
    )
    alert = check_spend_headroom(treasury)
    assert alert.severity == CRITICAL
    assert "blocking and refunding" in alert.action


def test_vendor_exposure_warns_near_a_vendor_cap(treasury):
    policy = SpendPolicy()
    treasury.spend(
        int(policy.per_vendor_daily_cents * 0.96),
        "one hungry vendor",
        job_id="J1",
        vendor="market-data-api",
    )
    alert = check_vendor_exposure(treasury)
    assert alert.severity == WARN
    assert "market-data-api" in alert.message


def test_repeated_blocks_are_flagged(treasury):
    for i in range(3):
        treasury.record_event(f"J{i}", "spend_blocked", {"vendor": "x", "amount": 1})
    assert check_blocked_spends(treasury).severity == WARN


def test_old_blocks_do_not_count(treasury):
    for i in range(5):
        treasury.record_event(f"J{i}", "spend_blocked", {"vendor": "x", "amount": 1})
    assert check_blocked_spends(treasury, now=time.time() + 48 * HOUR).severity == OK


# --- pipeline and cost model ------------------------------------------------


def test_overdue_checkouts_are_flagged(treasury):
    treasury.upsert_job("J1", "awaiting_payment", topic="t", budget_cents=4_900)
    treasury.upsert_checkout("J1", "cs_1", "https://pay.example/1", "open")
    alert = check_unpaid_pipeline(treasury, now=time.time() + 99 * HOUR)
    assert alert.severity == WARN
    assert "sweep" in alert.action


def test_a_fresh_checkout_is_only_reported(treasury):
    treasury.upsert_job("J1", "awaiting_payment", topic="t", budget_cents=4_900)
    treasury.upsert_checkout("J1", "cs_1", "https://pay.example/1", "open")
    alert = check_unpaid_pipeline(treasury)
    assert alert.severity == OK
    assert "$49.00" in alert.message


def test_hot_costs_are_flagged(treasury):
    for i in range(6):
        treasury.upsert_metrics(f"J{i}", est_cost_cents=1_000, actual_cost_cents=1_600)
    alert = check_cost_model(treasury)
    assert alert.severity == CRITICAL
    assert "pricing_overrides" in alert.action


def test_mild_drift_only_warns(treasury):
    for i in range(6):
        treasury.upsert_metrics(f"J{i}", est_cost_cents=1_000, actual_cost_cents=1_300)
    assert check_cost_model(treasury).severity == WARN


def test_no_history_is_not_an_alert(treasury):
    assert check_cost_model(treasury).severity == OK


# --- the sweep --------------------------------------------------------------


def test_run_checks_covers_every_check(treasury):
    alerts = run_checks(treasury)
    assert {a.check for a in alerts} == {
        "cash",
        "stuck_jobs",
        "spend_headroom",
        "vendor_exposure",
        "blocked_spends",
        "unpaid_pipeline",
        "cost_model",
    }


def test_a_broken_check_becomes_an_alert_not_a_crash(treasury):
    # A check that blows up must not take the whole sweep down with it.
    with mock.patch("solvent.alerts.runway", side_effect=RuntimeError("boom")):
        alerts = run_checks(treasury)
    broken = next(a for a in alerts if a.check == "cash")
    assert broken.severity == WARN
    assert "check failed" in broken.message


def test_severity_rolls_up_to_the_worst():
    alerts = [Alert("a", OK, ""), Alert("b", WARN, ""), Alert("c", CRITICAL, "")]
    assert worst_severity(alerts) == CRITICAL
    assert worst_severity([Alert("a", OK, "")]) == OK
    assert worst_severity([]) == OK


def test_summary_counts_by_severity():
    data = summarise([Alert("a", OK, ""), Alert("b", WARN, ""), Alert("c", WARN, "")])
    assert data["severity"] == WARN
    assert (data["critical"], data["warn"], data["ok"]) == (0, 2, 1)


def test_quiet_output_hides_passing_checks():
    data = summarise([Alert("fine", OK, "all good"), Alert("bad", CRITICAL, "oh no", "fix it")])
    loud = format_alerts(data)
    quiet = format_alerts(data, quiet=True)
    assert "all good" in loud
    assert "all good" not in quiet
    assert "oh no" in quiet
    assert "fix it" in quiet


def test_an_all_clear_sweep_says_so():
    assert "All checks pass" in format_alerts(summarise([Alert("a", OK, "fine")]), quiet=True)


def test_notify_pushes_only_problems():
    data = summarise([Alert("a", OK, "fine"), Alert("b", CRITICAL, "broken", "fix it")])
    with mock.patch("solvent.notifications.enqueue_chat") as enqueue:
        sent = notify(data, "telegram")
    assert sent == 1
    channel, external_id, text = enqueue.call_args[0]
    assert channel == "telegram"
    assert "CRITICAL" in text
    assert "fix it" in text
