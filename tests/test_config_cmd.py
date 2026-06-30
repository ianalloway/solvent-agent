"""Tests for solvent/config_cmd.py."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

from solvent.config import CONFIG_PATH, SolventConfig, save_config
from solvent.config_cmd import cmd_get, cmd_reset, cmd_set, cmd_show, _coerce


# ---------------------------------------------------------------------------
# _coerce
# ---------------------------------------------------------------------------

def test_coerce_bool_true():
    assert _coerce("bool", "true") is True
    assert _coerce("bool", "1") is True
    assert _coerce("bool", "yes") is True


def test_coerce_bool_false():
    assert _coerce("bool", "false") is False
    assert _coerce("bool", "0") is False
    assert _coerce("bool", "no") is False


def test_coerce_bool_invalid():
    with pytest.raises(ValueError):
        _coerce("bool", "maybe")


def test_coerce_int():
    assert _coerce("int", "42") == 42


def test_coerce_float():
    assert _coerce("float", "3.14") == pytest.approx(3.14)


def test_coerce_str():
    assert _coerce("str", "hello") == "hello"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    """Redirect config reads/writes to tmp_path."""
    cfg_dir = tmp_path / ".solvent"
    cfg_dir.mkdir()
    cfg_path = cfg_dir / "config.json"
    monkeypatch.setattr("solvent.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("solvent.config.CONFIG_PATH", cfg_path)
    monkeypatch.setattr("solvent.config_cmd.CONFIG_PATH", cfg_path, raising=False)
    yield cfg_path


# ---------------------------------------------------------------------------
# cmd_show
# ---------------------------------------------------------------------------

def test_cmd_show_prints_keys(capsys):
    cmd_show()
    captured = capsys.readouterr()
    assert "model" in captured.out
    assert "rate_burst_limit" in captured.out


def test_cmd_show_json(capsys):
    cmd_show(as_json=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "model" in data
    assert "rate_burst_limit" in data


def test_cmd_show_reflects_saved_config(isolate_config, capsys):
    cfg = SolventConfig(model="nemotron")
    save_config(cfg)
    cmd_show()
    captured = capsys.readouterr()
    assert "nemotron" in captured.out


# ---------------------------------------------------------------------------
# cmd_get
# ---------------------------------------------------------------------------

def test_cmd_get_known_key(capsys):
    rc = cmd_get("model")
    assert rc == 0
    captured = capsys.readouterr()
    assert "offline" in captured.out


def test_cmd_get_unknown_key(capsys):
    rc = cmd_get("nonexistent_key")
    assert rc == 1
    captured = capsys.readouterr()
    assert "unknown key" in captured.err


def test_cmd_get_json_mode(capsys):
    rc = cmd_get("rate_burst_limit", as_json=True)
    assert rc == 0
    captured = capsys.readouterr()
    val = json.loads(captured.out)
    assert isinstance(val, int)


# ---------------------------------------------------------------------------
# cmd_set
# ---------------------------------------------------------------------------

def test_cmd_set_string_field(isolate_config, capsys):
    rc = cmd_set("model", "nemotron")
    assert rc == 0
    from solvent.config import load_config
    cfg = load_config()
    assert cfg.model == "nemotron"


def test_cmd_set_bool_field(isolate_config):
    rc = cmd_set("stripe_test_mode", "true")
    assert rc == 0
    from solvent.config import load_config
    cfg = load_config()
    assert cfg.stripe_test_mode is True


def test_cmd_set_int_field(isolate_config):
    rc = cmd_set("rate_burst_limit", "10")
    assert rc == 0
    from solvent.config import load_config
    cfg = load_config()
    assert cfg.rate_burst_limit == 10


def test_cmd_set_unknown_key_returns_1(capsys):
    rc = cmd_set("bogus_key", "value")
    assert rc == 1
    captured = capsys.readouterr()
    assert "unknown key" in captured.err


def test_cmd_set_invalid_value_returns_1(capsys):
    rc = cmd_set("rate_burst_limit", "not_a_number")
    assert rc == 1


def test_cmd_set_validation_failure_returns_1(capsys):
    rc = cmd_set("model", "turbo-gpt-99")
    assert rc == 1
    captured = capsys.readouterr()
    assert "validation" in captured.err.lower() or "invalid" in captured.err.lower()


# ---------------------------------------------------------------------------
# cmd_reset
# ---------------------------------------------------------------------------

def test_cmd_reset_restores_defaults(isolate_config, capsys):
    cmd_set("model", "nemotron")
    rc = cmd_reset()
    assert rc == 0
    from solvent.config import load_config
    cfg = load_config()
    assert cfg.model == "offline"
    captured = capsys.readouterr()
    assert "reset" in captured.out.lower()


# ---------------------------------------------------------------------------
# CLI main dispatch
# ---------------------------------------------------------------------------

def test_main_show(isolate_config, capsys):
    with mock.patch("sys.argv", ["solvent", "show"]):
        with pytest.raises(SystemExit) as exc:
            from solvent.config_cmd import main
            main()
        assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "model" in captured.out


def test_main_no_subcommand_shows_all(isolate_config, capsys):
    with mock.patch("sys.argv", ["solvent"]):
        with pytest.raises(SystemExit) as exc:
            from solvent.config_cmd import main
            main()
        assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "model" in captured.out


def test_main_set_and_get(isolate_config, capsys):
    with mock.patch("sys.argv", ["solvent", "set", "model", "nemotron"]):
        with pytest.raises(SystemExit) as exc:
            from solvent.config_cmd import main
            main()
        assert exc.value.code == 0

    with mock.patch("sys.argv", ["solvent", "get", "model"]):
        with pytest.raises(SystemExit) as exc:
            from solvent.config_cmd import main
            main()
        assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "nemotron" in captured.out
