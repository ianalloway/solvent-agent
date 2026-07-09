"""Tests for solvent/observability.py."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from solvent.observability import log_event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log_event(tmp_path: Path, treasury=None, **kwargs) -> dict:
    log_path = tmp_path / "solvent.log"
    with mock.patch("solvent.observability.LOG_PATH", log_path):
        return log_event(treasury, **kwargs)


# ---------------------------------------------------------------------------
# Return value
# ---------------------------------------------------------------------------

def test_log_event_returns_dict(tmp_path):
    rec = _log_event(tmp_path, job_id="j1", stage="start")
    assert isinstance(rec, dict)
    assert rec["job_id"] == "j1"
    assert rec["stage"] == "start"
    assert "ts" in rec


def test_log_event_includes_extra_fields(tmp_path):
    rec = _log_event(tmp_path, job_id="j2", stage="fulfill", custom="hello")
    assert rec["custom"] == "hello"


def test_log_event_filters_none_values(tmp_path):
    rec = _log_event(tmp_path, job_id="j3", stage="s", stripe_ref=None, margin_est=None)
    assert "stripe_ref" not in rec
    assert "margin_est" not in rec


def test_log_event_includes_optional_fields_when_set(tmp_path):
    rec = _log_event(
        tmp_path,
        job_id="j4",
        stage="quote",
        stripe_ref="pi_123",
        margin_est=0.42,
        duration_ms=123.4,
        simulated=True,
    )
    assert rec["stripe_ref"] == "pi_123"
    assert rec["margin_est"] == pytest.approx(0.42)
    assert rec["duration_ms"] == pytest.approx(123.4)
    assert rec["simulated"] is True


# ---------------------------------------------------------------------------
# File output
# ---------------------------------------------------------------------------

def test_log_event_writes_to_log_file(tmp_path):
    _log_event(tmp_path, job_id="j5", stage="write_test")
    log_path = tmp_path / "solvent.log"
    assert log_path.exists()
    line = log_path.read_text(encoding="utf-8").strip()
    data = json.loads(line)
    assert data["job_id"] == "j5"


def test_log_event_appends_multiple_lines(tmp_path):
    _log_event(tmp_path, job_id="j6", stage="a")
    _log_event(tmp_path, job_id="j6", stage="b")
    log_path = tmp_path / "solvent.log"
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["stage"] == "a"
    assert json.loads(lines[1])["stage"] == "b"


def test_log_event_creates_parent_dirs(tmp_path):
    nested = tmp_path / "deep" / "dir" / "solvent.log"
    with mock.patch("solvent.observability.LOG_PATH", nested):
        log_event(None, job_id="j7", stage="mkdir_test")
    assert nested.exists()


def test_log_event_survives_oserror(tmp_path, monkeypatch):
    log_path = tmp_path / "solvent.log"

    def _bad_open(*a, **kw):
        raise OSError("disk full")

    with mock.patch("solvent.observability.LOG_PATH", log_path):
        with mock.patch("builtins.open", _bad_open):
            rec = log_event(None, job_id="j8", stage="error_test")
    assert rec["job_id"] == "j8"


# ---------------------------------------------------------------------------
# Treasury interaction
# ---------------------------------------------------------------------------

def test_log_event_calls_treasury_record_event(tmp_path):
    t = mock.MagicMock()
    _log_event(tmp_path, treasury=t, job_id="j9", stage="record_test")
    t.record_event.assert_called_once()
    args = t.record_event.call_args[0]
    assert args[0] == "j9"
    assert args[1] == "record_test"


def test_log_event_skips_treasury_on_none(tmp_path):
    rec = _log_event(tmp_path, treasury=None, job_id="j10", stage="no_treasury")
    assert rec["job_id"] == "j10"


def test_log_event_survives_treasury_exception(tmp_path):
    t = mock.MagicMock()
    t.record_event.side_effect = RuntimeError("db gone")
    rec = _log_event(tmp_path, treasury=t, job_id="j11", stage="exception_test")
    assert rec["job_id"] == "j11"


# ---------------------------------------------------------------------------
# stderr JSON logging (SOLVENT_LOG_JSON)
# ---------------------------------------------------------------------------

def test_log_event_prints_to_stderr_when_env_set(tmp_path, capsys):
    with mock.patch.dict(os.environ, {"SOLVENT_LOG_JSON": "1"}):
        _log_event(tmp_path, job_id="j12", stage="stderr_test")
    err = capsys.readouterr().err
    data = json.loads(err.strip())
    assert data["job_id"] == "j12"


def test_log_event_no_stderr_by_default(tmp_path, capsys):
    env = {k: v for k, v in os.environ.items() if k != "SOLVENT_LOG_JSON"}
    with mock.patch.dict(os.environ, env, clear=True):
        _log_event(tmp_path, job_id="j13", stage="quiet_test")
    assert capsys.readouterr().err == ""
