"""SOLVENT command-line entry point."""

from __future__ import annotations

from importlib import import_module
import sys
from typing import Final


HELP = """\
SOLVENT — a self-funding analyst agent.

Usage: solvent <command> [options]
       solvent                 run the demo (interactive onboarding on first run)

Commands:
  (none)        run the batch demo / interactive session
  init          first-run setup: create dirs, DB, and workspace files
  status        live summary: balance, jobs, API key presence; --watch to auto-refresh
  upgrade       check for a newer version on PyPI; --check exits 1 if outdated
  jobs          list/show/retry/cancel jobs (jobs --help for subcommands)
  retry <id>    compatibility alias for jobs retry <id>
  logs          tail the structured event log; -f to follow, --job/--stage to filter
  config        show/get/set/reset local configuration values
  serve         webhooks + job API + hosted briefs
  worker        resume incomplete jobs, process the queue
  telegram      long-poll the Telegram bot
  finance       income statement, unit economics, runway, forecast (alias: report)
  tune          propose pricing improvements (--apply to commit)
  reconcile     Stripe <-> ledger drift check
  doctor        stack diagnostics
  pairing       manage Telegram DM pairing codes
  workspace     seed the agent workspace (SOUL/BRAIN/AGENTS)
  webhooks      inspect the webhook event log (stats|list|failed)
  tui           live terminal dashboard (requires the rich extra)
  help          show this message
  version       print the installed version

Run `solvent <command> --help` where supported for command-specific options.
"""

# Keep optional features lazily imported: the zero-dependency core must remain
# importable without FastAPI, Stripe, Rich, or Telegram installed.
_COMMANDS: Final[dict[str, tuple[str, str]]] = {
    "init": ("init", "main"),
    "status": ("status", "main"),
    "jobs": ("job_cmd", "main"),
    "upgrade": ("upgrade", "main"),
    "logs": ("logs", "main"),
    "config": ("config_cmd", "main"),
    "serve": ("server", "main"),
    "worker": ("worker", "main"),
    "tune": ("improver", "main"),
    "reconcile": ("reconcile", "main"),
    "doctor": ("doctor", "main"),
    "telegram": ("channels.telegram", "main"),
    "pairing": ("pairing", "main"),
    "workspace": ("workspace", "main"),
    "tui": ("tui", "run"),
    "finance": ("finance", "main"),
    "report": ("finance", "main"),
    "webhooks": ("webhook_log", "main"),
}


def _print_version() -> None:
    from . import __version__

    print(f"solvent {__version__}")


def _dispatch(command: str):
    module_name, function_name = _COMMANDS[command]
    sys.argv.pop(1)
    handler = getattr(import_module(f"solvent.{module_name}"), function_name)
    return handler()


def _run_demo():
    from .cli import main as demo_main

    return demo_main()


def main():
    if len(sys.argv) == 1:
        return _run_demo()

    command = sys.argv[1]
    if command in ("version", "--version", "-V"):
        return _print_version()
    if command in ("help", "--help", "-h"):
        print(HELP)
        return None

    # Preserve the old top-level spelling without maintaining a second retry
    # implementation. job_cmd owns all job operations.
    if command == "retry":
        sys.argv[1:2] = ["jobs", "retry"]
        command = "jobs"

    if command in _COMMANDS:
        return _dispatch(command)

    # Demo options such as --interactive and --seed belong to solvent.cli.
    return _run_demo()


if __name__ == "__main__":
    main()
