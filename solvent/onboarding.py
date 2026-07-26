"""
onboarding.py — first-run terminal wizard for SOLVENT.

Inspired by:
  - NemoClaw: `nemoclaw onboard` (provider-first, credentials in env, non-interactive flag)
  - Hermes: `hermes setup` (quick wizard, ~/.hermes config, interaction mode choice)
"""

from __future__ import annotations

import os
import sys

from .config import SolventConfig, save_config
from .workspace import seed_workspace

# Match run_demo.py terminal palette
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_CYAN = "\033[36m"
C_GREY = "\033[90m"

BAR = f"{C_GREY}{'─' * 66}{C_RESET}"


def _prompt_choice(label: str, options: list[tuple[str, str]], default: int = 1) -> int:
    """Numbered menu; returns 1-based index."""
    print(f"\n{C_BOLD}{label}{C_RESET}")
    for i, (title, desc) in enumerate(options, start=1):
        mark = f"{C_CYAN}[{i}]{C_RESET}"
        print(f"  {mark} {C_BOLD}{title}{C_RESET} — {C_GREY}{desc}{C_RESET}")
    while True:
        raw = input(f"\n{C_CYAN}Choice [{default}]:{C_RESET} ").strip()
        if not raw:
            return default
        try:
            choice = int(raw)
        except ValueError:
            print(f"  {C_RED}Enter a number 1–{len(options)}.{C_RESET}")
            continue
        if 1 <= choice <= len(options):
            return choice
        print(f"  {C_RED}Enter a number 1–{len(options)}.{C_RESET}")


def _yes_no(prompt: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input(f"{C_CYAN}{prompt} ({hint}):{C_RESET} ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _print_banner():
    print(f"\n🪙  {C_BOLD}SOLVENT — First-Run Setup{C_RESET}")
    print(BAR)
    print(f"  {C_GREY}Self-funding analyst agent · Hermes hackathon demo{C_RESET}")
    print(f"  {C_GREY}Patterns: NemoClaw onboard + Hermes setup wizard{C_RESET}")
    print(BAR)


def _print_programmatic_guidance():
    print(f"\n{C_BOLD}Programmatic mode — use SOLVENT as a library{C_RESET}")
    print(BAR)
    print("""
  from solvent.agent import Solvent
  from solvent.jobs import SAMPLE_JOBS

  agent = Solvent(seed_cents=10_000)
  agent.handle_job(SAMPLE_JOBS[0])
  snap = agent.run(SAMPLE_JOBS[1:])
""")
    print(f"  {C_GREY}See README.md → Mode 3 — Programmatic{C_RESET}")
    print(f"  {C_GREY}Re-run setup anytime: python3 run_demo.py --onboard{C_RESET}")
    print(BAR + "\n")


def _doctor_notes(cfg: SolventConfig) -> list[str]:
    notes: list[str] = []
    if cfg.model == "nemotron" and not os.environ.get("NVIDIA_API_KEY"):
        notes.append(
            f"{C_YELLOW}NVIDIA_API_KEY not set — Nemotron will fall back to offline stub.{C_RESET}"
        )
    if cfg.stripe_test_mode and not os.environ.get("STRIPE_API_KEY"):
        notes.append(
            f"{C_YELLOW}STRIPE_API_KEY not set — Stripe will run in simulator mode.{C_RESET}"
        )
    if cfg.stripe_test_mode and os.environ.get("STRIPE_API_KEY", "").startswith(
        "sk_live_"
    ):
        notes.append(
            f"{C_RED}STRIPE_API_KEY is a live key — SOLVENT refuses sk_live_* keys.{C_RESET}"
        )
    if cfg.telegram_enabled and not os.environ.get("TELEGRAM_BOT_TOKEN"):
        notes.append(
            f"{C_YELLOW}TELEGRAM_BOT_TOKEN not set — run `python -m solvent telegram` after exporting token.{C_RESET}"
        )
    return notes


def _print_summary(cfg: SolventConfig, path):
    model_label = (
        "Offline stub" if cfg.model == "offline" else f"Nemotron ({cfg.nemotron_model})"
    )
    mode_labels = {
        "batch": "Batch demo (4 sample jobs)",
        "interactive": "Interactive REPL",
        "programmatic": "Programmatic (library only)",
    }
    stripe_label = (
        "Test mode (real Payment Links)" if cfg.stripe_test_mode else "Simulated"
    )

    print(f"\n{C_GREEN}✓ Setup complete{C_RESET} — saved to {C_BLUE}{path}{C_RESET}")
    print(BAR)
    print(f"  Model      {model_label}")
    print(f"  Mode       {mode_labels.get(cfg.interaction_mode, cfg.interaction_mode)}")
    print(f"  Stripe     {stripe_label}")
    if cfg.telegram_enabled:
        print(f"  Telegram   enabled · dm_policy={cfg.telegram_dm_policy}")
    for note in _doctor_notes(cfg):
        print(f"  ⚠️  {note}")
    print(BAR)

    if cfg.interaction_mode == "programmatic":
        print(f"\n  {C_GREY}Programmatic mode — see library usage below.{C_RESET}")
    elif cfg.interaction_mode == "interactive":
        print(f"\n  {C_GREY}Starting interactive session…{C_RESET}\n")
    else:
        print(f"\n  {C_GREY}Starting batch demo…{C_RESET}\n")


def run_wizard() -> SolventConfig:
    """Interactive onboarding; returns saved config."""
    _print_banner()

    # --- Model (NemoClaw: provider-first) --------------------------------
    model_choice = _prompt_choice(
        "Step 1 — Choose reasoning model",
        [
            ("Offline stub", "Zero credentials · deterministic briefs (demo default)"),
            (
                "NVIDIA Nemotron",
                "Live inference · set NVIDIA_API_KEY in your environment",
            ),
            (
                "Custom endpoint",
                "Future: OpenAI-compatible URL · uses offline stub for now",
            ),
        ],
        default=1,
    )
    if model_choice == 2:
        model = "nemotron"
        nemotron_model = os.environ.get(
            "NEMOTRON_MODEL", "nvidia/llama-3.1-nemotron-ultra-253b-v1"
        )
        if not os.environ.get("NVIDIA_API_KEY"):
            print(
                f"\n  {C_YELLOW}Tip:{C_RESET} export NVIDIA_API_KEY=nvapi-... "
                f"(from build.nvidia.com) before running jobs."
            )
    elif model_choice == 3:
        model = "offline"
        nemotron_model = "nvidia/llama-3.1-nemotron-ultra-253b-v1"
        print(
            f"\n  {C_GREY}Custom endpoints are not wired yet; using offline stub. "
            f"Set NEMOTRON_MODEL + NVIDIA_API_KEY to experiment.{C_RESET}"
        )
    else:
        model = "offline"
        nemotron_model = "nvidia/llama-3.1-nemotron-ultra-253b-v1"

    # --- Interaction mode (Hermes: CLI vs gateway) -----------------------
    mode_choice = _prompt_choice(
        "Step 2 — How do you want to interact?",
        [
            ("Batch demo", "Run 4 canned jobs · best for judges (~30s)"),
            ("Interactive REPL", "Type your own research topics and budgets"),
            ("Programmatic only", "Skip the CLI · import Solvent in Python"),
        ],
        default=1,
    )
    mode_map = {1: "batch", 2: "interactive", 3: "programmatic"}
    interaction_mode = mode_map[mode_choice]

    # --- Stripe (optional earn/spend layer) ------------------------------
    print(f"\n{C_BOLD}Step 3 — Stripe test mode{C_RESET}")
    print(f"  {C_GREY}Real test Payment Links need STRIPE_API_KEY=sk_test_...{C_RESET}")
    stripe_test_mode = _yes_no("Enable Stripe test mode?", default=False)

    # --- Telegram (optional channel) ---------------------------------------
    print(f"\n{C_BOLD}Step 4 — Telegram bot (optional){C_RESET}")
    print(
        f"  {C_GREY}Requires TELEGRAM_BOT_TOKEN in env · see docs/TELEGRAM.md{C_RESET}"
    )
    telegram_enabled = _yes_no("Enable Telegram in config?", default=False)
    telegram_dm_policy = "pairing"
    telegram_allow_from = None
    if telegram_enabled:
        pol = _prompt_choice(
            "DM policy for unknown users",
            [
                ("Pairing", "6-digit code + operator approve (recommended)"),
                ("Allowlist", "Only IDs in telegram_allow_from / allowlist file"),
                ("Open", "Accept all DMs (not recommended)"),
            ],
            default=1,
        )
        telegram_dm_policy = {1: "pairing", 2: "allowlist", 3: "open"}[pol]

    cfg = SolventConfig(
        onboarded=True,
        model=model,
        nemotron_model=nemotron_model,
        interaction_mode=interaction_mode,
        stripe_test_mode=stripe_test_mode,
        telegram_enabled=telegram_enabled,
        telegram_dm_policy=telegram_dm_policy,
        telegram_allow_from=telegram_allow_from,
    )
    path = save_config(cfg)
    ws = seed_workspace()
    print(f"  {C_GREY}Agent workspace seeded at {ws}{C_RESET}")
    _print_summary(cfg, path)
    return cfg


def should_skip_onboarding(argv: list[str] | None = None) -> bool:
    args = argv if argv is not None else sys.argv[1:]
    if "--no-onboard" in args:
        return True
    if os.environ.get("SOLVENT_SKIP_ONBOARD", "").strip() in ("1", "true", "yes"):
        return True
    return False


def wants_reconfigure(argv: list[str] | None = None) -> bool:
    args = argv if argv is not None else sys.argv[1:]
    return "--onboard" in args
