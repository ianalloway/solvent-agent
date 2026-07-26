"""Tests for solvent/onboarding.py — untested until this file.

Covers three public surface areas:
- should_skip_onboarding / wants_reconfigure: CLI-flag and env-var contract
- _doctor_notes: safety/readiness guidance that locks in the "refuse live
  Stripe keys" invariant
"""

from __future__ import annotations

import pytest

from solvent.config import SolventConfig
from solvent.onboarding import _doctor_notes, should_skip_onboarding, wants_reconfigure

# ---------------------------------------------------------------------------
# should_skip_onboarding
# ---------------------------------------------------------------------------


class TestShouldSkipOnboarding:
    def test_skips_on_no_onboard_flag(self) -> None:
        assert should_skip_onboarding(["--no-onboard"])

    def test_skips_on_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOLVENT_SKIP_ONBOARD", "1")
        monkeypatch.delenv("--no-onboard", raising=False)
        assert should_skip_onboarding([])

    def test_no_skip_when_neither_set(self) -> None:
        assert not should_skip_onboarding([])

    def test_yes_variant_also_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOLVENT_SKIP_ONBOARD", "yes")
        assert should_skip_onboarding([])


# ---------------------------------------------------------------------------
# wants_reconfigure
# ---------------------------------------------------------------------------


class TestWantsReconfigure:
    def test_true_on_onboard_flag(self) -> None:
        assert wants_reconfigure(["--onboard"])

    def test_false_default(self) -> None:
        assert not wants_reconfigure([])

    def test_true_with_mixed_args(self) -> None:
        assert wants_reconfigure(["some", "other", "--onboard"])


# ---------------------------------------------------------------------------
# _doctor_notes — safety / readiness assertions
# ---------------------------------------------------------------------------


class TestDoctorNotes:
    def test_all_clear_with_defaults(self) -> None:
        notes = _doctor_notes(SolventConfig())
        assert notes == []

    def test_missing_nvidia_yields_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        cfg = SolventConfig(model="nemotron")
        notes = _doctor_notes(cfg)
        assert any("NVIDIA_API_KEY" in n for n in notes)

    def test_no_nvidia_warning_when_key_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-fake")
        cfg = SolventConfig(model="nemotron")
        notes = _doctor_notes(cfg)
        assert not any("NVIDIA_API_KEY" in n for n in notes)

    def test_missing_stripe_key_yields_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("STRIPE_API_KEY", raising=False)
        cfg = SolventConfig(stripe_test_mode=True)
        notes = _doctor_notes(cfg)
        assert any("STRIPE_API_KEY" in n for n in notes)

    def test_live_stripe_key_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STRIPE_API_KEY", "sk_live_abc123fake")
        cfg = SolventConfig(stripe_test_mode=True)
        notes = _doctor_notes(cfg)
        assert any("sk_live_" in n for n in notes)

    def test_test_mode_key_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STRIPE_API_KEY", "sk_test_abc123fake")
        cfg = SolventConfig(stripe_test_mode=True)
        notes = _doctor_notes(cfg)
        assert not any("sk_live_" in n for n in notes)

    def test_telegram_no_token_yields_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        cfg = SolventConfig(telegram_enabled=True)
        notes = _doctor_notes(cfg)
        assert any("TELEGRAM_BOT_TOKEN" in n for n in notes)
