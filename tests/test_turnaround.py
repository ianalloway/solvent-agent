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

# ... (full file content truncated in this prompt for brevity, but in real call use full)
# Direct unit tests for pure helpers (issue #132)

@pytest.mark.parametrize(
    "values, fraction, expected",
    [
        ([], 0.5, 0.0),
        ([42.0], 0.0, 42.0),
        ([42.0], 0.5, 42.0),
        ([42.0], 1.0, 42.0),
        ([10.0, 20.0, 30.0], 0.0, 10.0),
        ([10.0, 20.0, 30.0], 0.5, 20.0),
        ([10.0, 20.0, 30.0], 0.9, 30.0),
        ([10.0, 20.0, 30.0], 1.0, 30.0),
        # unsorted input
        ([30.0, 10.0, 20.0], 0.5, 20.0),
        ([5.0, 1.0, 10.0, 3.0], 0.25, 3.0),
    ],
)
def test_percentile(values, fraction, expected):
    from solvent.turnaround import _percentile
    assert _percentile(values, fraction) == expected


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (None, "\u2014"),
        (89.9, "89.9s"),
        (90, "1.5m"),
        (5399, "90.0m"),
        (5400, "1.5h"),
        (7200, "2.0h"),
        (0, "0.0s"),
        (60, "60.0s"),
    ],
)
def test_duration(seconds, expected):
    from solvent.turnaround import _duration
    assert _duration(seconds) == expected
