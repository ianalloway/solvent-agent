"""Tests for measured paid-to-delivered turnaround (solvent.turnaround)."""

from __future__ import annotations

import time

import pytest

from solvent.turnaround import (
    FLOOR_SECONDS,
    MIN_SAMPLES,
    breaches,
    format_turnaround,
    humanise,
    late_threshold_seconds,
    measure,
    promise_seconds,
    report,
    stats,
)
from solvent.treasury import Treasury


@pytest.fixture
def treasury(tmp_path):
    t = Treasury(path=tmp_path / "ledger.db")
    t.seed(100_000)
    return t


def _delivered(treasury, job_id, *, waited: float, paid_at: float | None = None):
    """A job paid at `paid_at` and delivered `waited` seconds later."""
    start = paid_at if paid_at is not None else time.time() - waited
    treasury.earn(4_900, f"paid {job_id}", job_id=job_id, stripe_ref=f"pi_{job_id}", ts=start)
    treasury.record_event(
        job_id, "delivered", {"stage": "delivered", "job_id": job_id, "ts": start + waited}
    )
    with treasury._conn() as conn, conn:
        conn.execute(
            "UPDATE events SET ts = ? WHERE job_id = ? AND stage = 'delivered'",
            (start + waited, job_id),
        )


def test_nothing_to_measure_yet(treasury):
    assert measure(treasury) == []
    assert stats([])["samples"] == 0
    assert promise_seconds([]) is None
    assert "nothing to measure" in format_turnaround(report(treasury))


def test_the_wait_is_measured_from_payment_to_delivery(treasury):
    _delivered(treasury, "J1", waited=120.0)
    rows = measure(treasury)
    assert len(rows) == 1
    assert rows[0]["job_id"] == "J1"
    assert rows[0]["seconds"] == pytest.approx(120.0, abs=0.5)


def test_a_paid_but_undelivered_job_is_not_a_measurement(treasury):
    treasury.earn(4_900, "paid J1", job_id="J1", stripe_ref="pi_1")
    assert measure(treasury) == []


def test_an_unpaid_delivery_is_not_a_measurement(treasury):
    treasury.record_event("J1", "delivered", {"stage": "delivered", "job_id": "J1"})
    assert measure(treasury) == []


def test_percentiles_describe_the_distribution(treasury):
    for index, waited in enumerate([10.0, 20.0, 30.0, 40.0, 500.0]):
        _delivered(treasury, f"J{index}", waited=waited)
    figures = stats(measure(treasury))

    assert figures["samples"] == 5
    assert figures["p50"] == pytest.approx(30.0, abs=0.5)
    assert figures["max"] == pytest.approx(500.0, abs=0.5)
    assert figures["mean"] == pytest.approx(120.0, abs=1.0)


def test_a_promise_needs_enough_history(treasury):
    for index in range(MIN_SAMPLES - 1):
        _delivered(treasury, f"J{index}", waited=30.0)
    assert promise_seconds(measure(treasury)) is None
    assert "not enough history" in humanise(None)


def test_a_promise_is_never_faster_than_the_floor(treasury):
    for index in range(MIN_SAMPLES + 2):
        _delivered(treasury, f"J{index}", waited=2.0)  # suspiciously quick
    assert promise_seconds(measure(treasury)) == FLOOR_SECONDS


def test_the_promise_sits_at_the_chosen_percentile(treasury):
    for index, waited in enumerate([100.0, 200.0, 300.0, 400.0, 5_000.0]):
        _delivered(treasury, f"J{index}", waited=waited)
    rows = measure(treasury)

    p50 = promise_seconds(rows, percentile=0.5)
    p90 = promise_seconds(rows, percentile=0.9)
    assert p50 is not None and p90 is not None
    assert p50 < p90


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (12.0, "within 5 minutes"),
        (300.0, "within 5 minutes"),
        (301.0, "within 10 minutes"),
        (1_500.0, "within 30 minutes"),
        (7_000.0, "within 2 hour(s)"),
        (200_000.0, "within 3 day(s)"),
    ],
)
def test_a_promise_rounds_up_to_something_quotable(seconds, expected):
    assert humanise(seconds) == expected


def test_breaches_are_the_jobs_that_took_longer(treasury):
    for index, waited in enumerate([60.0, 60.0, 60.0, 9_000.0]):
        _delivered(treasury, f"J{index}", waited=waited)
    rows = measure(treasury)
    promise = promise_seconds(rows, percentile=0.5)

    missed = breaches(rows, promise)
    assert [row["job_id"] for row in missed] == ["J3"]
    assert breaches(rows, None) == []


def test_late_is_defined_by_the_shop_s_own_history(treasury):
    for index in range(MIN_SAMPLES + 1):
        _delivered(treasury, f"J{index}", waited=120.0)
    threshold = late_threshold_seconds(treasury)
    assert threshold is not None
    assert threshold > 120.0  # slack above the normal wait


def test_without_history_there_is_no_measured_threshold(treasury):
    assert late_threshold_seconds(treasury) is None


def test_the_report_renders(treasury):
    for index, waited in enumerate([30.0, 45.0, 60.0, 4_000.0]):
        _delivered(treasury, f"J{index}", waited=waited)
    data = report(treasury)
    rendered = format_turnaround(data)

    assert "TURNAROUND" in rendered
    assert "Quotable promise" in rendered
    assert "J3" in rendered  # the slowest job is named


# --- the alert and the quote use it ----------------------------------------


def test_alerts_fall_back_to_fixed_hours_without_history(treasury):
    from solvent.alerts import WARN, check_stuck_jobs

    treasury.upsert_job("J1", "paid_pending_fulfill", topic="t", budget_cents=4_900)
    assert check_stuck_jobs(treasury, now=time.time() + 3 * 3_600).severity == WARN


def test_alerts_use_the_measured_threshold_when_there_is_history(treasury):
    """A shop that delivers in two minutes should not wait two hours to notice."""
    from solvent.alerts import OK, WARN, check_stuck_jobs

    for index in range(MIN_SAMPLES + 1):
        _delivered(treasury, f"D{index}", waited=120.0)
    treasury.upsert_job("J1", "paid_pending_fulfill", topic="t", budget_cents=4_900)

    # Well inside the measured threshold: nothing to say yet.
    assert check_stuck_jobs(treasury, now=time.time() + 60).severity == OK
    # An hour in, far beyond a two-minute shop's p95, but under the old 2h rule.
    assert check_stuck_jobs(treasury, now=time.time() + 3_600).severity == WARN


def test_the_quote_quotes_the_measured_turnaround(treasury, monkeypatch, capsys):
    import sys

    from solvent import quote_cmd

    rows = [
        {"job_id": f"J{i}", "paid_at": 0.0, "delivered_at": 90.0, "seconds": 90.0} for i in range(4)
    ]
    monkeypatch.setattr("solvent.turnaround.measure", lambda *a, **k: rows)
    monkeypatch.setattr(sys, "argv", ["solvent quote", "A topic", "--budget", "49"])

    with pytest.raises(SystemExit) as exit_info:
        quote_cmd.main()
    assert exit_info.value.code == 0
    assert "Turnaround           within 5 minutes" in capsys.readouterr().out
