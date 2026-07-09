"""Tests for solvent/events.py."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

from solvent.events_cmd import _fmt_ts, _human_line, show_events
from solvent.treasury import Treasury


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_treasury(tmp_path: Path) -> Treasury:
    return Treasury(path=tmp_path / "solvent.db")


def _record(t: Treasury, job_id: str, stage: str, **payload) -> None:
    t.record_event(job_id, stage, {"job_id": job_id, "stage": stage, **payload})


def _capture(treasury, **kwargs) -> str:
    buf = StringIO()
    with mock.patch("sys.stdout", buf):
        show_events(treasury, **kwargs)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# _fmt_ts
# ---------------------------------------------------------------------------

def test_fmt_ts_has_date_and_time():
    import time
    result = _fmt_ts(time.time())
    assert "-" in result and ":" in result


# ---------------------------------------------------------------------------
# _human_line
# ---------------------------------------------------------------------------

def test_human_line_basic():
    ev = {
        "id": "abc",
        "job_id": "job123456789",
        "stage": "fulfill",
        "payload_json": "{}",
        "ts": 1_700_000_000.0,
    }
    line = _human_line(ev)
    assert "job1234" in line
    assert "fulfill" in line


def test_human_line_with_stripe_ref():
    ev = {
        "id": "x",
        "job_id": "jjjjjjjjjjjj",
        "stage": "charge",
        "payload_json": json.dumps({"stripe_ref": "pi_abc"}),
        "ts": 1_700_000_000.0,
    }
    line = _human_line(ev)
    assert "stripe=pi_abc" in line


def test_human_line_with_duration():
    ev = {
        "id": "y",
        "job_id": "job000000000",
        "stage": "fulfill",
        "payload_json": json.dumps({"duration_ms": 1234.5}),
        "ts": 1_700_000_000.0,
    }
    line = _human_line(ev)
    assert "1235ms" in line or "1234ms" in line


def test_human_line_simulated():
    ev = {
        "id": "z",
        "job_id": "job000000000",
        "stage": "quote",
        "payload_json": json.dumps({"simulated": True}),
        "ts": 1_700_000_000.0,
    }
    assert "sim" in _human_line(ev)


def test_human_line_no_job_id():
    ev = {"id": "n", "job_id": None, "stage": "boot", "payload_json": "{}", "ts": 1_700_000_000.0}
    line = _human_line(ev)
    assert "boot" in line


# ---------------------------------------------------------------------------
# show_events
# ---------------------------------------------------------------------------

def test_show_events_empty(tmp_path):
    t = _make_treasury(tmp_path)
    out = _capture(t)
    assert "no events" in out.lower()


def test_show_events_shows_entries(tmp_path):
    t = _make_treasury(tmp_path)
    _record(t, "job-aaa", "quote")
    _record(t, "job-aaa", "fulfill")
    out = _capture(t, n=10)
    assert "quote" in out or "fulfill" in out


def test_show_events_newest_first(tmp_path):
    t = _make_treasury(tmp_path)
    _record(t, "jobA", "stage-first")
    _record(t, "jobA", "stage-second")
    out = _capture(t, n=10)
    assert out.index("stage-second") < out.index("stage-first")


def test_show_events_filter_stage(tmp_path):
    t = _make_treasury(tmp_path)
    _record(t, "job1", "quote")
    _record(t, "job2", "fulfill")
    out = _capture(t, n=10, stage="quote")
    assert "quote" in out
    assert "fulfill" not in out


def test_show_events_filter_job_prefix(tmp_path):
    t = _make_treasury(tmp_path)
    _record(t, "aaa-111", "quote")
    _record(t, "bbb-222", "fulfill")
    out = _capture(t, n=10, job="aaa")
    assert "aaa" in out
    assert "bbb" not in out


def test_show_events_limit_n(tmp_path):
    t = _make_treasury(tmp_path)
    for i in range(10):
        _record(t, f"job{i:04d}00", f"stage-{i}")
    out = _capture(t, n=3)
    count = sum(1 for line in out.splitlines() if "stage-" in line)
    assert count == 3


def test_show_events_json_mode(tmp_path):
    t = _make_treasury(tmp_path)
    _record(t, "job-json-1", "quote", ts=1.0)
    out = _capture(t, n=5, as_json=True)
    data = json.loads(out)
    assert isinstance(data, list)
    assert data[0]["job_id"] == "job-json-1"
    assert "payload" in data[0]
    assert "payload_json" not in data[0]


def test_show_events_json_payload_is_dict(tmp_path):
    t = _make_treasury(tmp_path)
    _record(t, "job-json-2", "fulfill", duration_ms=500.0)
    out = _capture(t, n=5, as_json=True)
    data = json.loads(out)
    assert isinstance(data[0]["payload"], dict)


# ---------------------------------------------------------------------------
# CLI main dispatch
# ---------------------------------------------------------------------------

def test_main_runs(tmp_path):
    t = _make_treasury(tmp_path)
    _record(t, "job-cli", "quote")
    with mock.patch("sys.argv", ["solvent"]):
        buf = StringIO()
        with mock.patch("sys.stdout", buf):
            with mock.patch("solvent.treasury.Treasury", return_value=t):
                from solvent.events_cmd import main
                main()
    assert "event" in buf.getvalue().lower() or "quote" in buf.getvalue()


def test_main_json_flag(tmp_path):
    t = _make_treasury(tmp_path)
    _record(t, "job-cli-json", "fulfill")
    with mock.patch("sys.argv", ["solvent", "--json"]):
        buf = StringIO()
        with mock.patch("sys.stdout", buf):
            with mock.patch("solvent.treasury.Treasury", return_value=t):
                from solvent.events_cmd import main
                main()
    data = json.loads(buf.getvalue())
    assert data[0]["job_id"] == "job-cli-json"
