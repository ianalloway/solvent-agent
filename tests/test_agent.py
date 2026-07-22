"""Unit tests for the SOLVENT Solvent orchestrator (solvent.agent).

Covers initialization with a fresh ledger, event logging, and the no-op
run() dispatcher when given an empty job list.
"""

from __future__ import annotations

import pytest

from solvent.agent import Solvent


def test_solvent_initialises_with_seed_capital(tmp_path, monkeypatch):
    monkeypatch.setattr("solvent.treasury.DB_PATH", tmp_path / "ledger.db")
    agent = Solvent(seed_cents=10_000, fresh=True)
    snap = agent.t.snapshot()
    assert snap["balance_cents"] == 10_000
    assert snap["capital_cents"] == 10_000
    assert snap["revenue_cents"] == 0
    assert snap["expense_cents"] == 0


def test_solvent_run_empty_list_returns_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr("solvent.treasury.DB_PATH", tmp_path / "ledger.db")
    agent = Solvent(seed_cents=5_000, fresh=True)
    snap = agent.run([])
    assert snap["balance_cents"] == 5_000
    # seed capital is recorded as one entry
    assert len(snap["entries"]) == 1
    assert snap["entries"][0]["kind"] == "capital"


def test_solvent_emit_appends_event_to_log(tmp_path, monkeypatch):
    monkeypatch.setattr("solvent.treasury.DB_PATH", tmp_path / "ledger.db")
    agent = Solvent(seed_cents=1_000, fresh=True)
    event = agent._emit(stage="test", message="ping")
    assert len(agent.log) == 1
    assert agent.log[0]["stage"] == "test"
    assert agent.log[0]["message"] == "ping"
    assert "ts" in agent.log[0]
    # _emit must return the event it logged
    assert event["stage"] == "test"


def test_solvent_callback_receives_events(tmp_path, monkeypatch):
    received = []
    monkeypatch.setattr("solvent.treasury.DB_PATH", tmp_path / "ledger.db")
    agent = Solvent(seed_cents=1_000, fresh=True, on_event=received.append)
    agent._emit(stage="callback_test", detail=42)
    assert len(received) == 1
    assert received[0]["stage"] == "callback_test"
    assert received[0]["detail"] == 42
