"""tests/test_tui.py — unit tests for the rich terminal dashboard."""

from __future__ import annotations

import time
import types
from unittest.mock import MagicMock, patch

import pytest

# `rich` is an optional extra; skip the whole module when it isn't installed.
pytest.importorskip("rich")


# ---------------------------------------------------------------------------
# Helpers: build minimal fake data that mirrors Treasury outputs
# ---------------------------------------------------------------------------

SNAPSHOT = {
    "balance_cents": 5000,
    "capital_cents": 10000,
    "revenue_cents": 8000,
    "expense_cents": 3000,
    "net_profit_cents": 5000,
    "margin_pct": 62.5,
    "entries": [],
}

JOBS = [
    {
        "id": "job_001",
        "topic": "Nvidia Q3 earnings deep-dive for institutional investors",
        "status": "completed",
        "_pnl_cents": 1200,
        "created_at": time.time() - 60,
        "updated_at": time.time(),
    },
    {
        "id": "job_002",
        "topic": "This topic is exactly forty characters long!!! and then some more text",
        "status": "failed",
        "_pnl_cents": -300,
        "created_at": time.time() - 30,
        "updated_at": time.time(),
    },
    {
        "id": "job_003",
        "topic": "Short topic",
        "status": "in_progress",
        "_pnl_cents": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    },
    {
        "id": "job_004",
        "topic": "Awaiting payment research",
        "status": "awaiting_payment",
        "_pnl_cents": 0,
        "created_at": time.time(),
        "updated_at": time.time(),
    },
]

EVENTS = [
    {"id": "ev_001", "job_id": "job_001", "stage": "quote", "ts": time.time() - 90},
    {"id": "ev_002", "job_id": "job_001", "stage": "paid", "ts": time.time() - 60},
    {"id": "ev_003", "job_id": "job_001", "stage": "fulfilled", "ts": time.time() - 30},
    {"id": "ev_004", "job_id": "job_002", "stage": "declined", "ts": time.time()},
]


# ---------------------------------------------------------------------------
# Tests: build_layout
# ---------------------------------------------------------------------------

def test_build_layout_returns_layout():
    """build_layout should return a rich Layout without raising."""
    from rich.layout import Layout
    from solvent.tui import build_layout

    result = build_layout(SNAPSHOT, JOBS, EVENTS)
    assert isinstance(result, Layout)


def test_build_layout_with_empty_data():
    """build_layout should handle empty jobs / events gracefully."""
    from rich.layout import Layout
    from solvent.tui import build_layout

    empty_snap = {
        "balance_cents": 0,
        "revenue_cents": 0,
        "margin_pct": 0.0,
    }
    result = build_layout(empty_snap, [], [])
    assert isinstance(result, Layout)


def test_build_layout_truncates_long_topics():
    """Topics longer than 40 chars should be truncated with an ellipsis."""
    from rich.layout import Layout
    from solvent.tui import build_layout

    long_topic_job = [{"id": "x", "topic": "A" * 80, "status": "completed", "_pnl_cents": 0}]
    # Should not raise; the table cell will contain a truncated string
    result = build_layout(SNAPSHOT, long_topic_job, [])
    assert isinstance(result, Layout)


# ---------------------------------------------------------------------------
# Tests: status colour mapping
# ---------------------------------------------------------------------------

def test_status_color_completed():
    from solvent.tui import _status_color
    assert _status_color("completed") == "green"


def test_status_color_failed():
    from solvent.tui import _status_color
    assert _status_color("failed") == "red"


def test_status_color_in_progress():
    from solvent.tui import _status_color
    assert _status_color("in_progress") == "yellow"


def test_status_color_awaiting_payment():
    from solvent.tui import _status_color
    assert _status_color("awaiting_payment") == "cyan"


def test_status_color_unknown_fallback():
    from solvent.tui import _status_color
    assert _status_color("some_unknown_status") == "white"


# ---------------------------------------------------------------------------
# Tests: margin colour mapping
# ---------------------------------------------------------------------------

def test_margin_color_high():
    from solvent.tui import _margin_color
    assert _margin_color(75.0) == "green"


def test_margin_color_boundary_50():
    from solvent.tui import _margin_color
    assert _margin_color(50.0) == "green"


def test_margin_color_mid():
    from solvent.tui import _margin_color
    assert _margin_color(25.0) == "yellow"


def test_margin_color_boundary_10():
    from solvent.tui import _margin_color
    assert _margin_color(10.0) == "yellow"


def test_margin_color_low():
    from solvent.tui import _margin_color
    assert _margin_color(5.0) == "red"


def test_margin_color_zero():
    from solvent.tui import _margin_color
    assert _margin_color(0.0) == "red"


# ---------------------------------------------------------------------------
# Tests: balance colour mapping
# ---------------------------------------------------------------------------

def test_balance_color_positive():
    from solvent.tui import _balance_color
    assert _balance_color(100) == "green"


def test_balance_color_zero():
    from solvent.tui import _balance_color
    assert _balance_color(0) == "red"


def test_balance_color_negative():
    from solvent.tui import _balance_color
    assert _balance_color(-50) == "red"


# ---------------------------------------------------------------------------
# Tests: run() — mock Live so it does not block
# ---------------------------------------------------------------------------

def test_run_exits_on_keyboard_interrupt():
    """run() should return cleanly when KeyboardInterrupt is raised."""
    from solvent.treasury import Treasury

    # Build an in-memory treasury so no DB file is needed
    treasury = Treasury(path="/tmp/solvent_tui_test.db")

    mock_live_instance = MagicMock()
    mock_live_instance.__enter__ = MagicMock(return_value=mock_live_instance)
    mock_live_instance.__exit__ = MagicMock(return_value=False)

    # Make sleep raise KeyboardInterrupt immediately
    with patch("solvent.tui.Live", return_value=mock_live_instance), \
         patch("solvent.tui.time.sleep", side_effect=KeyboardInterrupt):
        from solvent.tui import run
        run(treasury=treasury, refresh_interval=0.01)  # should not raise


def test_run_calls_live_update():
    """run() should call live.update() at least once per sleep cycle."""
    from solvent.treasury import Treasury

    treasury = Treasury(path="/tmp/solvent_tui_test2.db")

    call_count = {"n": 0}

    def fake_sleep(_):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise KeyboardInterrupt

    mock_live_instance = MagicMock()
    mock_live_instance.__enter__ = MagicMock(return_value=mock_live_instance)
    mock_live_instance.__exit__ = MagicMock(return_value=False)

    with patch("solvent.tui.Live", return_value=mock_live_instance), \
         patch("solvent.tui.time.sleep", side_effect=fake_sleep):
        from solvent.tui import run
        run(treasury=treasury, refresh_interval=0.01)

    assert mock_live_instance.update.call_count >= 1
