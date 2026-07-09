"""Tests for solvent/sessions_cmd.py."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

from solvent.sessions_cmd import show_sessions
from solvent.treasury import Treasury


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_treasury(tmp_path: Path) -> Treasury:
    return Treasury(path=tmp_path / "solvent.db")


def _capture(treasury, **kwargs) -> str:
    buf = StringIO()
    with mock.patch("sys.stdout", buf):
        show_sessions(treasury, **kwargs)
    return buf.getvalue()


def _add_session(t: Treasury, channel: str, ext_id: str, label: str | None = None) -> dict:
    return t.get_or_create_chat_session(channel, ext_id, user_label=label)


def _add_message(t: Treasury, session_id: str, role: str, content: str) -> None:
    t.add_chat_message(session_id, role, content)


# ---------------------------------------------------------------------------
# show_sessions — list view
# ---------------------------------------------------------------------------

def test_empty_shows_no_sessions(tmp_path):
    t = _make_treasury(tmp_path)
    out = _capture(t)
    assert "no chat sessions" in out.lower()


def test_lists_created_session(tmp_path):
    t = _make_treasury(tmp_path)
    _add_session(t, "telegram", "user123", "Alice")
    out = _capture(t)
    assert "telegram" in out
    assert "Alice" in out


def test_multiple_sessions_shown(tmp_path):
    t = _make_treasury(tmp_path)
    _add_session(t, "telegram", "user1", "Alice")
    _add_session(t, "web", "user2", "Bob")
    out = _capture(t, n=10)
    assert "Alice" in out
    assert "Bob" in out


def test_filter_by_channel(tmp_path):
    t = _make_treasury(tmp_path)
    _add_session(t, "telegram", "u1", "Alice")
    _add_session(t, "web", "u2", "Bob")
    out = _capture(t, n=10, channel="telegram")
    assert "Alice" in out
    assert "Bob" not in out


def test_message_count_shown(tmp_path):
    t = _make_treasury(tmp_path)
    s = _add_session(t, "telegram", "user1")
    _add_message(t, s["id"], "user", "hello")
    _add_message(t, s["id"], "assistant", "hi")
    out = _capture(t, n=10)
    assert "2" in out


def test_shows_external_id_when_no_label(tmp_path):
    t = _make_treasury(tmp_path)
    _add_session(t, "telegram", "ext999")
    out = _capture(t)
    assert "ext999" in out


def test_limit_n(tmp_path):
    t = _make_treasury(tmp_path)
    for i in range(5):
        _add_session(t, "telegram", f"user{i}", f"User{i}")
    out = _capture(t, n=3)
    count = sum(1 for line in out.splitlines() if "telegram" in line)
    assert count == 3


def test_list_json(tmp_path):
    t = _make_treasury(tmp_path)
    _add_session(t, "telegram", "u1", "Alice")
    out = _capture(t, n=10, as_json=True)
    data = json.loads(out)
    assert isinstance(data, list)
    assert data[0]["channel"] == "telegram"


# ---------------------------------------------------------------------------
# show_sessions — single session detail (--show)
# ---------------------------------------------------------------------------

def test_show_session_messages(tmp_path):
    t = _make_treasury(tmp_path)
    s = _add_session(t, "telegram", "user42", "Alice")
    _add_message(t, s["id"], "user", "Hello agent!")
    _add_message(t, s["id"], "assistant", "Hello Alice!")
    out = _capture(t, show_id=s["id"])
    assert "Hello agent!" in out
    assert "Hello Alice!" in out
    assert "USER" in out
    assert "ASSISTANT" in out


def test_show_session_not_found(tmp_path):
    t = _make_treasury(tmp_path)
    out = _capture(t, show_id="nonexistent:id")
    assert "not found" in out.lower()


def test_show_session_no_messages(tmp_path):
    t = _make_treasury(tmp_path)
    s = _add_session(t, "web", "anon1")
    out = _capture(t, show_id=s["id"])
    assert "no messages" in out.lower()


def test_show_session_json(tmp_path):
    t = _make_treasury(tmp_path)
    s = _add_session(t, "telegram", "u99", "Bob")
    _add_message(t, s["id"], "user", "test message")
    out = _capture(t, show_id=s["id"], as_json=True)
    data = json.loads(out)
    assert "session" in data
    assert "messages" in data
    assert data["session"]["channel"] == "telegram"
    assert data["messages"][0]["content"] == "test message"


def test_show_session_truncates_long_content(tmp_path):
    t = _make_treasury(tmp_path)
    s = _add_session(t, "web", "user1")
    _add_message(t, s["id"], "user", "x" * 200)
    out = _capture(t, show_id=s["id"])
    # Should be truncated, not full 200 chars shown as one chunk
    assert "x" * 121 not in out
    assert "…" in out


# ---------------------------------------------------------------------------
# CLI main dispatch
# ---------------------------------------------------------------------------

def test_main_runs(tmp_path):
    t = _make_treasury(tmp_path)
    _add_session(t, "telegram", "cli_user", "CLI User")
    with mock.patch("sys.argv", ["solvent"]):
        buf = StringIO()
        with mock.patch("sys.stdout", buf):
            with mock.patch("solvent.treasury.Treasury", return_value=t):
                from solvent.sessions_cmd import main
                main()
    assert "telegram" in buf.getvalue()


def test_main_json(tmp_path):
    t = _make_treasury(tmp_path)
    _add_session(t, "web", "web_user")
    with mock.patch("sys.argv", ["solvent", "--json"]):
        buf = StringIO()
        with mock.patch("sys.stdout", buf):
            with mock.patch("solvent.treasury.Treasury", return_value=t):
                from solvent.sessions_cmd import main
                main()
    data = json.loads(buf.getvalue())
    assert data[0]["channel"] == "web"
