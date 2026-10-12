"""The onboarding wizard must not crash when stdin is not a TTY."""

from __future__ import annotations

import io

import pytest

from solvent import onboarding
from solvent.config import SolventConfig, default_config


class _FakeStdin(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__("")
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_run_wizard_without_tty_returns_defaults(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _FakeStdin(tty=False))
    saved = []
    monkeypatch.setattr(onboarding, "save_config", lambda cfg: saved.append(cfg))
    cfg = onboarding.run_wizard()
    assert isinstance(cfg, SolventConfig)
    assert cfg == default_config()
    assert saved == []  # defaults are not persisted
    assert "no interactive terminal" in capsys.readouterr().err


def test_run_wizard_with_closed_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", None)
    assert onboarding.run_wizard() == default_config()


def test_run_wizard_with_tty_still_prompts(monkeypatch):
    monkeypatch.setattr("sys.stdin", _FakeStdin(tty=True))

    def _eof(*_a, **_k):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    with pytest.raises(EOFError):
        onboarding.run_wizard()
