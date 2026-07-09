"""Tests for solvent/ledger.py."""

from __future__ import annotations

import json
import sys
import time
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

from solvent.ledger import _entry_to_dict, _fmt_cents, _fmt_ts, _human_line, show_ledger
from solvent.treasury import LedgerEntry, Treasury


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def test_fmt_cents():
    assert "$    1.50" == _fmt_cents(150)
    assert "$    0.00" == _fmt_cents(0)


def test_fmt_ts_returns_datetime_string():
    result = _fmt_ts(time.time())
    assert "-" in result and ":" in result


def test_human_line_revenue():
    e = LedgerEntry(kind="revenue", amount_cents=500, memo="Stripe charge", job_id="abc123")
    line = _human_line(e)
    assert "+" in line
    assert "5.00" in line
    assert "Stripe charge" in line
    assert "abc123" in line


def test_human_line_expense():
    e = LedgerEntry(kind="expense", amount_cents=100, memo="Nemotron inference", vendor="nvidia")
    line = _human_line(e)
    assert "-" in line
    assert "1.00" in line
    assert "nvidia" in line


def test_human_line_capital():
    e = LedgerEntry(kind="capital", amount_cents=10000, memo="Seed capital")
    line = _human_line(e)
    assert "~" in line
    assert "100.00" in line


def test_human_line_truncates_memo():
    e = LedgerEntry(kind="revenue", amount_cents=100, memo="x" * 100)
    line = _human_line(e)
    assert "x" * 51 not in line  # truncated at 50


def test_entry_to_dict():
    e = LedgerEntry(kind="revenue", amount_cents=500, memo="test", job_id="j1")
    d = _entry_to_dict(e)
    assert d["kind"] == "revenue"
    assert d["amount_cents"] == 500
    assert d["job_id"] == "j1"


# ---------------------------------------------------------------------------
# show_ledger
# ---------------------------------------------------------------------------

def _make_treasury(tmp_path: Path) -> Treasury:
    t = Treasury(path=tmp_path / "solvent.db")
    return t


def _capture_show(treasury, **kwargs) -> str:
    buf = StringIO()
    with mock.patch("sys.stdout", buf):
        show_ledger(treasury, **kwargs)
    return buf.getvalue()


def test_show_ledger_empty(tmp_path):
    t = _make_treasury(tmp_path)
    out = _capture_show(t)
    assert "no ledger" in out.lower()


def test_show_ledger_shows_entries(tmp_path):
    t = _make_treasury(tmp_path)
    t.record("revenue", 1500, "Test job", job_id="job1")
    out = _capture_show(t)
    assert "15.00" in out
    assert "Test job" in out


def test_show_ledger_newest_first(tmp_path):
    t = _make_treasury(tmp_path)
    t.record("revenue", 100, memo="first")
    t.record("revenue", 200, memo="second")
    out = _capture_show(t, n=10)
    assert out.index("second") < out.index("first")


def test_show_ledger_filter_kind(tmp_path):
    t = _make_treasury(tmp_path)
    t.record("revenue", 500, memo="earned")
    t.record("expense", 100, memo="spent")
    out = _capture_show(t, n=10, kind="revenue")
    assert "earned" in out
    assert "spent" not in out


def test_show_ledger_filter_job(tmp_path):
    t = _make_treasury(tmp_path)
    t.record("revenue", 500, "job-a", job_id="aaa111")
    t.record("revenue", 300, "job-b", job_id="bbb222")
    out = _capture_show(t, n=10, job="aaa")
    assert "job-a" in out
    assert "job-b" not in out


def test_show_ledger_limit_n(tmp_path):
    t = _make_treasury(tmp_path)
    for i in range(10):
        t.record("revenue", 100, f"entry{i}")
    out = _capture_show(t, n=3)
    # Only last 3 shown (newest first)
    count = sum(1 for line in out.splitlines() if "entry" in line)
    assert count == 3


def test_show_ledger_json_mode(tmp_path):
    t = _make_treasury(tmp_path)
    t.record("capital", 5000, memo="seed")
    out = _capture_show(t, n=5, as_json=True)
    data = json.loads(out)
    assert isinstance(data, list)
    assert data[0]["kind"] == "capital"
    assert data[0]["amount_cents"] == 5000


def test_show_ledger_balance_in_header(tmp_path):
    t = _make_treasury(tmp_path)
    t.record("capital", 10000, memo="seed")
    t.record("expense", 2000, memo="spend")
    out = _capture_show(t)
    assert "80.00" in out  # 100 - 20 = $80.00


# ---------------------------------------------------------------------------
# CLI main
# ---------------------------------------------------------------------------

def test_main_runs(tmp_path):
    t = _make_treasury(tmp_path)
    t.record("revenue", 999, "CLI test")
    with mock.patch("sys.argv", ["solvent"]):
        buf = StringIO()
        with mock.patch("sys.stdout", buf):
            with mock.patch("solvent.treasury.Treasury", return_value=t):
                from solvent.ledger import main
                main()
    assert "9.99" in buf.getvalue()


def test_main_json_flag(tmp_path):
    t = _make_treasury(tmp_path)
    t.record("revenue", 500, "json-test")
    with mock.patch("sys.argv", ["solvent", "--json"]):
        buf = StringIO()
        with mock.patch("sys.stdout", buf):
            with mock.patch("solvent.treasury.Treasury", return_value=t):
                from solvent.ledger import main
                main()
    data = json.loads(buf.getvalue())
    assert data[0]["memo"] == "json-test"
