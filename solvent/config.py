"""
config.py — local user preferences for SOLVENT.

Saved to `.solvent/config.json` (gitignored). API keys are never stored here;
they remain in environment variables (`NVIDIA_API_KEY`, `STRIPE_API_KEY`).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

CONFIG_DIR = Path(".solvent")
CONFIG_PATH = CONFIG_DIR / "config.json"

VALID_MODELS = ("offline", "nemotron")
VALID_MODES = ("batch", "interactive", "programmatic")


@dataclass
class SolventConfig:
    onboarded: bool = False
    model: str = "offline"
    nemotron_model: str = "nvidia/llama-3.1-nemotron-ultra-253b-v1"
    interaction_mode: str = "batch"
    stripe_test_mode: bool = False

    def validate(self) -> None:
        if self.model not in VALID_MODELS:
            raise ValueError(f"invalid model: {self.model}")
        if self.interaction_mode not in VALID_MODES:
            raise ValueError(f"invalid interaction_mode: {self.interaction_mode}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SolventConfig:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


def config_exists() -> bool:
    return CONFIG_PATH.is_file()


def load_config() -> SolventConfig | None:
    if not config_exists():
        return None
    with CONFIG_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    cfg = SolventConfig.from_dict(data)
    cfg.validate()
    return cfg


def save_config(config: SolventConfig) -> Path:
    config.validate()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, indent=2)
        f.write("\n")
    return CONFIG_PATH


def default_config() -> SolventConfig:
    """Sensible defaults when onboarding is skipped."""
    return SolventConfig(
        onboarded=True,
        model="offline",
        interaction_mode="batch",
        stripe_test_mode=False,
    )


def apply_config(config: SolventConfig) -> None:
    """Push saved preferences into runtime modules and env hints."""
    from . import nemotron

    nemotron.configure(model=config.model, nemotron_model=config.nemotron_model)

    if config.stripe_test_mode:
        os.environ.pop("SOLVENT_FORCE_STRIPE_SIMULATE", None)
    else:
        os.environ["SOLVENT_FORCE_STRIPE_SIMULATE"] = "1"
