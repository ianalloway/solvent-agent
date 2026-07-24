"""Tests for solvent/logs.py."""

from __future__ import annotations

import json
import time
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

from solvent.logs import (
    _fmt_duration,
    _fmt_margin,
    _fmt_ts,
    _human_line,
    _matches,
    _parse,
    _tail_lines,
    show_logs,
)

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def test_fmt_ts_returns_string():
    result = _fmt_ts(time.time())
    assert ":" in result and len(result) == 8  # HH:MM:SS


def test_fmt_duration_ms():
    assert "ms" in _fmt_duration(500)


def test_fmt_duration_seconds():
    assert "s" in _fmt_duration(2500)
    assert "ms" not in _fmt_duration(2500)


def test_fmt_margin():
    assert "50.0%" == _fmt_margin(0.5)


# ---------------------------------------------------------------------------
# _parse
# ---------------------------------------------------------------------------

def test_parse_valid_json():
    r = _parse('{"ts": 1.0, "stage": "fulfill"}')
    assert r == {"ts": 1.0, "stage": "fulfill"}


def test_parse_invalid_returns_none():
    assert _parse("not json") is None


def test_parse_empty_string():
    assert _parse("") is None


# ---------------------------------------------------------------------------
# _matches
# ---------------------------------------------------------------------------

def test_matches_no_filter():
    assert _matches({"job_id": "abc", "stage": "x"}, job=None, stage=None)


def test_matches_job_prefix():
    assert _matches({"job_id": "abc123"}, job="abc", stage=None)
    assert not _matches({"job_id": "xyz"}, job="abc", stage=None)


def test_matches_stage():
    assert _matches({"stage": "fulfill"}, job=None, stage="fulfill")
    assert not _matches({"stage": "quote"}, job=None, stage="fulfill")


def test_matches_both_filters():
    r = {"job_id": "abc", "stage": "fulfill"}
    assert _matches(r, job="abc", stage="fulfill")
    assert not _matches(r, job="abc", stage="quote")


# ---------------------------------------------------------------------------
# _human_line
# ---------------------------------------------------------------------------

def test_human_line_basic():
    r = {"ts": time.time(), "job_id": "deadbeef", "stage": "quote"}
    line = _human_line(r)
    assert "deadbee" in line
    assert "quote" in line


def test_human_line_with_duration():
    r = {"ts": time.time(), "job_id": "abc", "stage": "fulfill", "duration_ms": 1500}
    line = _human_line(r)
    assert "1.5s" in line


def test_human_line_with_margin():
    r = {"ts": time.time(), "job_id": "abc", "stage": "fulfill", "margin_actual": 0.3}
    line = _human_line(r)
    assert "30.0%" in line


def test_human_line_with_stripe():
    r = {"ts": time.time(), "job_id": "abc", "stage": "charge", "stripe_ref": "pi_123456789012345"}
    line = _human_line(r)
    assert "pi_123456789" in line


def test_human_line_extra_fields():
    r = {"ts": time.time(), "job_id": "abc", "stage": "x", "customer": "test@x.com"}
    line = _human_line(r)
    assert "customer=test@x.com" in line


# ---------------------------------------------------------------------------
# _tail_lines
# ---------------------------------------------------------------------------

def test_tail_lines_nonexistent(tmp_path):
    assert _tail_lines(tmp_path / "missing.log", 10) == []


def test_tail_lines_empty(tmp_path):
    p = tmp_path / "log"
    p.write_text("")
    assert _tail_lines(p, 10) == []


def test_tail_lines_returns_last_n(tmp_path):
    p = tmp_path / "log"
    p.write_text("\n".join(f"line{i}" for i in range(50)) + "\n")
    result = _tail_lines(p, 5)
    assert result == [f"line{i}" for i in range(45, 50)]


def test_tail_lines_fewer_than_n(tmp_path):
    p = tmp_path / "log"
    p.write_text("line1\nline2\n")
    result = _tail_lines(p, 10)
    assert result == ["line1", "line2"]


# ---------------------------------------------------------------------------
# show_logs
# ---------------------------------------------------------------------------

def _make_log(tmp_path: Path, records: list[dict]) -> Path:
    p = tmp_path / "solvent.log"
    with p.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return p


def _capture_show(tmp_path, **kwargs) -> str:
    with mock.patch("solvent.logs.log_path", return_value=tmp_path / "solvent.log"):
        buf = StringIO()
        with mock.patch("sys.stdout", buf):
            show_logs(**kwargs)
    return buf.getvalue()


def test_show_logs_empty_file(tmp_path):
    _make_log(tmp_path, [])
    out = _capture_show(tmp_path)
    assert "no" in out.lower()


def test_show_logs_no_file(tmp_path):
    out = _capture_show(tmp_path)
    assert "no" in out.lower()


def test_show_logs_human_readable(tmp_path):
    _make_log(tmp_path, [{"ts": time.time(), "job_id": "abc123", "stage": "fulfill"}])
    out = _capture_show(tmp_path, n=5)
    assert "abc123" in out
    assert "fulfill" in out


def test_show_logs_json_mode(tmp_path):
    record = {"ts": time.time(), "job_id": "abc", "stage": "quote"}
    _make_log(tmp_path, [record])
    out = _capture_show(tmp_path, n=5, as_json=True)
    parsed = json.loads(out.strip())
    assert parsed["stage"] == "quote"


def test_show_logs_job_filter(tmp_path):
    records = [
        {"ts": time.time(), "job_id": "abc", "stage": "quote"},
        {"ts": time.time(), "job_id": "xyz", "stage": "fulfill"},
    ]
    _make_log(tmp_path, records)
    out = _capture_show(tmp_path, n=10, job="abc")
    assert "abc" in out
    assert "xyz" not in out


def test_show_logs_stage_filter(tmp_path):
    records = [
        {"ts": time.time(), "job_id": "abc", "stage": "quote"},
        {"ts": time.time(), "job_id": "abc", "stage": "fulfill"},
    ]
    _make_log(tmp_path, records)
    out = _capture_show(tmp_path, n=10, stage="quote")
    assert "quote" in out
    assert "fulfill" not in out


def test_show_logs_n_limits_output(tmp_path):
    records = [{"ts": time.time(), "job_id": f"j{i}", "stage": "x"} for i in range(10)]
    _make_log(tmp_path, records)
    out = _capture_show(tmp_path, n=3)
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 3


# ---------------------------------------------------------------------------
# CLI main
# ---------------------------------------------------------------------------

def test_main_path_flag(tmp_path, capsys):
    with mock.patch("solvent.logs.log_path", return_value=tmp_path / "solvent.log"):
        with mock.patch("sys.argv", ["solvent", "--path"]):
            with pytest.raises(SystemExit) as exc:
                from solvent.logs import main
                main()
            assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "solvent.log" in captured.out


def test_main_default_runs(tmp_path):
    _make_log(tmp_path, [{"ts": time.time(), "job_id": "a", "stage": "b"}])
    with mock.patch("solvent.logs.log_path", return_value=tmp_path / "solvent.log"):
        with mock.patch("sys.argv", ["solvent"]):
            buf = StringIO()
            with mock.patch("sys.stdout", buf):
                from solvent.logs import main
                main()
    assert "b" in buf.getvalue()
