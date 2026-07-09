"""Tests for solvent/rate_limit_cmd.py."""

from __future__ import annotations

import json
import time
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

from solvent.rate_limit_cmd import (
    cmd_ban,
    cmd_check,
    cmd_cleanup,
    cmd_list_banned,
    cmd_stats,
    cmd_unban,
)


# ---------------------------------------------------------------------------
# Helper — in-memory DB path
# ---------------------------------------------------------------------------

_MEM = ":memory:"


# ---------------------------------------------------------------------------
# cmd_stats
# ---------------------------------------------------------------------------

def test_stats_empty_user(capsys):
    rc = cmd_stats("user:1", db_path=_MEM)
    assert rc == 0
    out = capsys.readouterr().out
    assert "user:1" in out
    assert "burst" in out
    assert "banned  no" in out


def test_stats_json(capsys):
    rc = cmd_stats("user:json", db_path=_MEM, as_json=True)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["user_key"] == "user:json"
    assert "burst_count" in data
    assert "is_banned" in data


# ---------------------------------------------------------------------------
# cmd_check
# ---------------------------------------------------------------------------

def test_check_allowed(capsys):
    rc = cmd_check("new_user", db_path=_MEM)
    assert rc == 0
    assert "ALLOWED" in capsys.readouterr().out


def test_check_blocked_when_burst_exceeded(capsys):
    from solvent.rate_limit import RateLimiter
    rl = RateLimiter(db_path=_MEM, burst_limit=2)
    rl.check("user:burst")
    rl.check("user:burst")
    # Now at limit — cmd_check should report blocked
    with mock.patch("solvent.rate_limit_cmd._limiter", return_value=rl):
        rc = cmd_check("user:burst", db_path=_MEM)
    assert rc == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_check_blocked_when_banned(capsys):
    from solvent.rate_limit import RateLimiter
    rl = RateLimiter(db_path=_MEM)
    rl.ban("user:evil", duration_seconds=3600, reason="spam")
    with mock.patch("solvent.rate_limit_cmd._limiter", return_value=rl):
        rc = cmd_check("user:evil", db_path=_MEM)
    assert rc == 1
    out = capsys.readouterr().out
    assert "BLOCKED" in out
    assert "Banned" in out


def test_check_json(capsys):
    rc = cmd_check("u1", db_path=_MEM, as_json=True)
    data = json.loads(capsys.readouterr().out)
    assert data["allowed"] is True
    assert data["user_key"] == "u1"


# ---------------------------------------------------------------------------
# cmd_ban / cmd_unban
# ---------------------------------------------------------------------------

def test_ban_and_unban(capsys):
    from solvent.rate_limit import RateLimiter
    rl = RateLimiter(db_path=_MEM)

    with mock.patch("solvent.rate_limit_cmd._limiter", return_value=rl):
        rc = cmd_ban("user:x", duration=600, reason="test ban", db_path=_MEM)
    assert rc == 0
    assert rl.is_banned("user:x")

    with mock.patch("solvent.rate_limit_cmd._limiter", return_value=rl):
        rc = cmd_unban("user:x", db_path=_MEM)
    assert rc == 0
    assert not rl.is_banned("user:x")
    out = capsys.readouterr().out
    assert "Unbanned" in out


def test_unban_not_banned(capsys):
    rc = cmd_unban("ghost", db_path=_MEM)
    assert rc == 0
    assert "not banned" in capsys.readouterr().out


def test_ban_json(capsys):
    from solvent.rate_limit import RateLimiter
    rl = RateLimiter(db_path=_MEM)
    with mock.patch("solvent.rate_limit_cmd._limiter", return_value=rl):
        rc = cmd_ban("u2", duration=120, reason="test", db_path=_MEM, as_json=True)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["banned"] is True
    assert data["reason"] == "test"


# ---------------------------------------------------------------------------
# cmd_list_banned
# ---------------------------------------------------------------------------

def test_list_banned_empty(capsys):
    rc = cmd_list_banned(db_path=_MEM)
    assert rc == 0
    assert "no active bans" in capsys.readouterr().out


def test_list_banned_shows_active(capsys):
    from solvent.rate_limit import RateLimiter
    rl = RateLimiter(db_path=_MEM)
    rl.ban("alice", duration_seconds=3600, reason="spammer")
    rl.ban("bob", duration_seconds=7200, reason="")
    with mock.patch("solvent.rate_limit_cmd._limiter", return_value=rl):
        rc = cmd_list_banned(db_path=_MEM)
    assert rc == 0
    out = capsys.readouterr().out
    assert "alice" in out
    assert "spammer" in out
    assert "bob" in out


def test_list_banned_does_not_show_expired(capsys):
    from solvent.rate_limit import RateLimiter
    rl = RateLimiter(db_path=_MEM)
    # Insert an already-expired ban directly
    rl._conn.execute(
        "INSERT INTO rate_bans (user_key, expires_at, reason) VALUES (?, ?, ?)",
        ("old_user", time.time() - 10, "expired"),
    )
    rl._conn.commit()
    with mock.patch("solvent.rate_limit_cmd._limiter", return_value=rl):
        rc = cmd_list_banned(db_path=_MEM)
    assert rc == 0
    assert "old_user" not in capsys.readouterr().out


def test_list_banned_json(capsys):
    from solvent.rate_limit import RateLimiter
    rl = RateLimiter(db_path=_MEM)
    rl.ban("charlie", duration_seconds=60)
    with mock.patch("solvent.rate_limit_cmd._limiter", return_value=rl):
        rc = cmd_list_banned(db_path=_MEM, as_json=True)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list)
    assert data[0]["user_key"] == "charlie"


# ---------------------------------------------------------------------------
# cmd_cleanup
# ---------------------------------------------------------------------------

def test_cleanup_removes_old_records(capsys):
    from solvent.rate_limit import RateLimiter
    rl = RateLimiter(db_path=_MEM)
    now = time.time()
    rl._conn.executemany(
        "INSERT INTO rate_events (user_key, ts) VALUES (?, ?)",
        [("old", now - 90000)] * 5 + [("new", now)] * 2,
    )
    rl._conn.commit()
    with mock.patch("solvent.rate_limit_cmd._limiter", return_value=rl):
        rc = cmd_cleanup(db_path=_MEM)
    assert rc == 0
    out = capsys.readouterr().out
    assert "5" in out  # removed 5 old events
    remaining = rl._conn.execute("SELECT COUNT(*) FROM rate_events").fetchone()[0]
    assert remaining == 2


def test_cleanup_json(capsys):
    from solvent.rate_limit import RateLimiter
    rl = RateLimiter(db_path=_MEM)
    with mock.patch("solvent.rate_limit_cmd._limiter", return_value=rl):
        rc = cmd_cleanup(db_path=_MEM, as_json=True)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert "removed_events" in data
    assert "removed_bans" in data


# ---------------------------------------------------------------------------
# CLI main dispatch
# ---------------------------------------------------------------------------

def test_main_stats(capsys):
    with mock.patch("sys.argv", ["solvent", "stats", "user:cli"]):
        with mock.patch("solvent.rate_limit_cmd._limiter") as m:
            from solvent.rate_limit import RateLimiter
            rl = RateLimiter(db_path=_MEM)
            m.return_value = rl
            with pytest.raises(SystemExit) as exc:
                from solvent.rate_limit_cmd import main
                main()
            assert exc.value.code == 0


def test_main_list_banned(capsys):
    with mock.patch("sys.argv", ["solvent", "list-banned"]):
        with mock.patch("solvent.rate_limit_cmd._limiter") as m:
            from solvent.rate_limit import RateLimiter
            m.return_value = RateLimiter(db_path=_MEM)
            with pytest.raises(SystemExit) as exc:
                from solvent.rate_limit_cmd import main
                main()
            assert exc.value.code == 0
    assert "no active bans" in capsys.readouterr().out


def test_main_no_subcommand_prints_help(capsys):
    with mock.patch("sys.argv", ["solvent"]):
        with pytest.raises(SystemExit) as exc:
            from solvent.rate_limit_cmd import main
            main()
        assert exc.value.code == 0
