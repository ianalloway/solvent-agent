"""Local diagnostics for SOLVENT's runtime, storage, and optional features."""

from __future__ import annotations

import importlib.util
import os
import sys

from .paths import base_dir, data_dir
from .treasury import Treasury
from .workspace import ensure_workspace, list_workspace_files


_EXTRAS = [
    ("rich", "rich", "terminal dashboard"),
    ("fastapi", "fastapi", "webhooks and hosted briefs"),
    ("uvicorn", "uvicorn", "HTTP server runtime"),
    ("stripe", "stripe", "Stripe test-mode integration"),
    ("telegram", "telegram", "Telegram channel"),
    ("qrcode", "qrcode", "pairing QR images"),
]


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def run_checks() -> list[dict]:
    checks: list[dict] = []
    treasury = Treasury()

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    version = sys.version_info
    add(
        "python_version",
        version >= (3, 10),
        f"{version.major}.{version.minor}.{version.micro} (need >= 3.10)",
    )

    try:
        from importlib.metadata import PackageNotFoundError, version as package_version

        try:
            detail = f"pip-installed (v{package_version('solvent-agent')})"
        except PackageNotFoundError:
            detail = "source checkout"
    except Exception as exc:  # pragma: no cover
        detail = f"unknown ({exc})"
    add("install_mode", True, detail)

    home = base_dir()
    runtime_dir = data_dir()
    add("data_home", os.access(runtime_dir, os.W_OK), str(home))

    available = [
        f"{label} {'✓' if _module_available(module) else '✗'}"
        for label, module, _ in _EXTRAS
    ]
    add("optional_extras", True, ", ".join(available))

    add(
        "sqlite_writable",
        os.access(treasury.path.parent, os.W_OK),
        str(treasury.path),
    )
    try:
        with treasury._conn() as connection:
            connection.execute("SELECT 1")
        add("sqlite_connect", True)
    except Exception as exc:
        add("sqlite_connect", False, str(exc))

    add(
        "nvidia_mode",
        True,
        "live key set" if os.environ.get("NVIDIA_API_KEY") else "offline stub",
    )

    stripe_key = os.environ.get("STRIPE_API_KEY", "")
    if stripe_key.startswith("sk_live_"):
        add("stripe_mode", False, "live keys are refused")
    elif stripe_key.startswith(("sk_test_", "rk_test_")):
        add("stripe_mode", True, "test key present")
    else:
        add("stripe_mode", True, "simulate mode")

    add(
        "telegram_mode",
        True,
        "token set" if os.environ.get("TELEGRAM_BOT_TOKEN") else "disabled",
    )

    stuck = [
        job
        for job in treasury.list_jobs()
        if job.get("status")
        in ("awaiting_payment", "in_progress", "paid_pending_fulfill")
    ]
    add("stuck_jobs", not stuck, f"{len(stuck)} stuck" if stuck else "none")

    balance = treasury.balance_cents()
    add("treasury_balance", balance >= 0, f"${balance / 100:.2f}")

    ensure_workspace()
    core_files = {"SOUL.md", "AGENTS.md", "BRAIN.md"}
    present = {row["name"] for row in list_workspace_files() if row["exists"]}
    missing = core_files - present
    add(
        "workspace_files",
        not missing,
        "ok" if not missing else f"missing {', '.join(sorted(missing))}",
    )

    return checks


QUICKSTART = """
Quickstart:
  solvent                 run the demo (offline, no keys needed)
  solvent finance         financial report (income · runway · forecast)
  solvent --help          list all commands

Enable optional features:
  pip install -e ".[all]"            server · Stripe · Telegram · TUI · QR
  export NVIDIA_API_KEY=nvapi-...    live Nemotron inference
  export STRIPE_API_KEY=sk_test_...  real test-mode payment links
"""


def main() -> None:
    failed = 0
    for check in run_checks():
        mark = "OK" if check["ok"] else "FAIL"
        detail = f" — {check['detail']}" if check.get("detail") else ""
        print(f"[{mark}] {check['name']}{detail}")
        failed += not check["ok"]
    print(QUICKSTART)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
