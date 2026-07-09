"""Tests for solvent/metrics_cmd.py."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

from solvent.metrics_cmd import _summary, show_metrics
from solvent.treasury import Treasury


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_treasury(tmp_path: Path) -> Treasury:
    return Treasury(path=tmp_path / "solvent.db")


def _upsert(t: Treasury, job_id: str, **kwargs) -> None:
    t.upsert_metrics(job_id, **kwargs)


def _capture(treasury, **kwargs) -> str:
    buf = StringIO()
    with mock.patch("sys.stdout", buf):
        show_metrics(treasury, **kwargs)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# _summary
# ---------------------------------------------------------------------------

def test_summary_empty():
    s = _summary([])
    assert s["total"] == 0
    assert s["completed"] == 0


def test_summary_with_data():
    rows = [
        {"actual_margin_pct": 40.0, "fulfillment_seconds": 10.0,
         "margin_drift_cents": 0, "est_cost_cents": 100,
         "decline_reason": None, "block_rule": None, "refunded": 0, "ts": 1.0},
        {"actual_margin_pct": 60.0, "fulfillment_seconds": 20.0,
         "margin_drift_cents": 5, "est_cost_cents": 100,
         "decline_reason": None, "block_rule": None, "refunded": 0, "ts": 2.0},
    ]
    s = _summary(rows)
    assert s["completed"] == 2
    assert s["avg_actual_margin_pct"] == 50.0
    assert s["avg_fulfillment_seconds"] == 15.0
    assert s["cost_overruns"] == 0


def test_summary_counts_overruns():
    rows = [
        {"actual_margin_pct": 30.0, "fulfillment_seconds": 5.0,
         "margin_drift_cents": 20, "est_cost_cents": 100,  # 20% overrun
         "decline_reason": None, "block_rule": None, "refunded": 0, "ts": 1.0},
    ]
    s = _summary(rows)
    assert s["cost_overruns"] == 1


def test_summary_counts_declined_blocked_refunded():
    rows = [
        {"actual_margin_pct": None, "decline_reason": "price_too_low",
         "block_rule": None, "refunded": 0, "est_cost_cents": None,
         "margin_drift_cents": None, "fulfillment_seconds": None, "ts": 1.0},
        {"actual_margin_pct": None, "decline_reason": None,
         "block_rule": "ip_ban", "refunded": 1, "est_cost_cents": None,
         "margin_drift_cents": None, "fulfillment_seconds": None, "ts": 2.0},
    ]
    s = _summary(rows)
    assert s["declined"] == 1
    assert s["blocked"] == 1
    assert s["refunded"] == 1


# ---------------------------------------------------------------------------
# show_metrics
# ---------------------------------------------------------------------------

def test_empty_shows_no_metrics(tmp_path):
    t = _make_treasury(tmp_path)
    out = _capture(t)
    assert "no job metrics" in out.lower()


def test_shows_job_row(tmp_path):
    t = _make_treasury(tmp_path)
    _upsert(t, "job-abc123", est_margin_pct=45.0, actual_margin_pct=42.0,
            fulfillment_seconds=12.5, tool_calls=3)
    out = _capture(t, n=10)
    assert "job-abc12" in out
    assert "42.0%" in out
    assert "12.5s" in out


def test_newest_first(tmp_path):
    t = _make_treasury(tmp_path)
    _upsert(t, "job-first1", actual_margin_pct=10.0)
    _upsert(t, "job-second", actual_margin_pct=20.0)
    out = _capture(t, n=10)
    assert out.index("job-second") < out.index("job-first")


def test_filter_by_job(tmp_path):
    t = _make_treasury(tmp_path)
    _upsert(t, "job-aaa", actual_margin_pct=30.0)
    _upsert(t, "job-bbb", actual_margin_pct=50.0)
    out = _capture(t, job="job-aaa")
    assert "job-aaa" in out
    assert "job-bbb" not in out


def test_job_not_found(tmp_path):
    t = _make_treasury(tmp_path)
    out = _capture(t, job="nonexistent")
    assert "no job metrics" in out.lower()


def test_filter_slow(tmp_path):
    t = _make_treasury(tmp_path)
    _upsert(t, "job-fast", fulfillment_seconds=5.0, actual_margin_pct=30.0)
    _upsert(t, "job-slow", fulfillment_seconds=60.0, actual_margin_pct=30.0)
    out = _capture(t, n=10, slow_threshold=30.0)
    assert "job-slow" in out
    assert "job-fast" not in out


def test_filter_drifted(tmp_path):
    t = _make_treasury(tmp_path)
    _upsert(t, "job-ok11", est_cost_cents=100, margin_drift_cents=5,
            actual_margin_pct=40.0)   # 5% drift — OK
    _upsert(t, "job-bad1", est_cost_cents=100, margin_drift_cents=20,
            actual_margin_pct=30.0)   # 20% drift — drifted
    out = _capture(t, n=10, drifted_only=True)
    assert "job-bad1" in out
    assert "job-ok11" not in out


def test_limit_n(tmp_path):
    t = _make_treasury(tmp_path)
    for i in range(10):
        _upsert(t, f"jobtest{i:04d}", actual_margin_pct=float(i * 5))
    out = _capture(t, n=3)
    count = sum(1 for line in out.splitlines() if "jobtest" in line)
    assert count == 3


def test_flags_shown(tmp_path):
    t = _make_treasury(tmp_path)
    _upsert(t, "job-dec1", decline_reason="margin_too_low")
    _upsert(t, "job-blk1", block_rule="ip_ban")
    _upsert(t, "job-ref1", refunded=1, actual_margin_pct=30.0)
    out = _capture(t, n=10)
    assert "DECLINED" in out
    assert "BLOCKED" in out
    assert "REFUNDED" in out


def test_json_mode(tmp_path):
    t = _make_treasury(tmp_path)
    _upsert(t, "job-json", actual_margin_pct=55.0, tool_calls=2)
    out = _capture(t, n=5, as_json=True)
    data = json.loads(out)
    assert isinstance(data, list)
    assert data[0]["job_id"] == "job-json"


def test_summary_mode(tmp_path):
    t = _make_treasury(tmp_path)
    _upsert(t, "job-s1", actual_margin_pct=40.0, fulfillment_seconds=10.0)
    _upsert(t, "job-s2", actual_margin_pct=60.0, fulfillment_seconds=20.0)
    out = _capture(t, summary=True)
    assert "Avg actual margin" in out
    assert "50.0%" in out


def test_summary_json(tmp_path):
    t = _make_treasury(tmp_path)
    _upsert(t, "job-sj", actual_margin_pct=45.0)
    out = _capture(t, summary=True, as_json=True)
    data = json.loads(out)
    assert "avg_actual_margin_pct" in data


# ---------------------------------------------------------------------------
# CLI main dispatch
# ---------------------------------------------------------------------------

def test_main_runs(tmp_path):
    t = _make_treasury(tmp_path)
    _upsert(t, "job-cli1", actual_margin_pct=35.0)
    with mock.patch("sys.argv", ["solvent"]):
        buf = StringIO()
        with mock.patch("sys.stdout", buf):
            with mock.patch("solvent.treasury.Treasury", return_value=t):
                from solvent.metrics_cmd import main
                main()
    assert "job-cli1" in buf.getvalue()


def test_main_summary_flag(tmp_path):
    t = _make_treasury(tmp_path)
    _upsert(t, "job-cli2", actual_margin_pct=50.0)
    with mock.patch("sys.argv", ["solvent", "--summary"]):
        buf = StringIO()
        with mock.patch("sys.stdout", buf):
            with mock.patch("solvent.treasury.Treasury", return_value=t):
                from solvent.metrics_cmd import main
                main()
    assert "summary" in buf.getvalue().lower()
