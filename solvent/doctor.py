"""Hermes-style diagnostics for SOLVENT."""

from __future__ import annotations

import importlib.util
import os
import sys

from .paths import base_dir, data_dir
from .treasury import Treasury
from .workspace import ensure_workspace, list_workspace_files

# Optional extras: (human label, import name, what it unlocks).
_EXTRAS = [
    ("fastapi", "fastapi", "webhooks + hosted briefs"),
    ("uvicorn", "uvicorn", "server runtime"),
    ("stripe", "stripe", "live Stripe test-mode"),
    ("telegram", "telegram", "Telegram channel"),
]


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def run_checks() -> list[dict]:
    checks: list[dict] = []
    t = Treasury()

    def add(name: str, ok: bool, detail: str = ""):
        checks.append({"name": name, "ok": ok, "detail": detail})

    # --- install / runtime environment ---------------------------------
    pv = sys.version_info
    add("python_version", pv >= (3, 10), f"{pv.major}.{pv.minor}.{pv.micro} (need >= 3.10)")

    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            add("install_mode", True, f"pip-installed (v{version('solvent-agent')})")
        except PackageNotFoundError:
            add("install_mode", True, "source checkout (not pip-installed)")
    except Exception as exc:  # pragma: no cover
        add("install_mode", True, f"unknown ({exc})")

    home = base_dir()
    ddir = data_dir()
    add("data_home", os.access(ddir, os.W_OK), f"{home}  (set SOLVENT_HOME to relocate)")

    available = [f"{label} {'✓' if _module_available(mod) else '✗'}" for label, mod, _ in _EXTRAS]
    add("optional_extras", True, ", ".join(available))

    add("sqlite_writable", t.path.parent.exists() or True, str(t.path))
    try:
        with t._conn() as conn:
            conn.execute("SELECT 1")
        add("sqlite_connect", True)
    except Exception as exc:
        add("sqlite_connect", False, str(exc))

    nvidia = bool(os.environ.get("NVIDIA_API_KEY"))
    add("nvidia_api_key", nvidia, "set" if nvidia else "offline stub mode")

    stripe = os.environ.get("STRIPE_API_KEY", "")
    if stripe.startswith("sk_live_"):
        add("stripe_key", False, "live keys refused")
    elif stripe.startswith(("sk_test_", "rk_test_")):
        add("stripe_key", True, "test key present")
    else:
        add("stripe_key", True, "simulate mode (no key)")

    tg = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    add("telegram_token", bool(tg), "set" if tg else "telegram disabled")

    stuck = [
        j for j in t.list_jobs()
        if j.get("status") in ("awaiting_payment", "in_progress", "paid_pending_fulfill")
    ]
    add("stuck_jobs", len(stuck) == 0, f"{len(stuck)} stuck" if stuck else "none")

    bal = t.balance_cents()
    add("treasury_balance", bal >= 0, f"${bal/100:.2f}")

    ensure_workspace()
    files = list_workspace_files()
    core = {"SOUL.md", "AGENTS.md", "BRAIN.md"}
    present = {f["name"] for f in files if f["exists"]}
    missing = core - present
    add("workspace_files", not missing, "ok" if not missing else f"missing {', '.join(sorted(missing))}")

    return checks


QUICKSTART = """
Quickstart:
  solvent                 run the demo (offline, no keys needed)
  solvent finance         financial report (income · runway · forecast)
  solvent --help          list all commands

Enable live features:
  pip install -e ".[all]"            Stripe · server · Telegram · QR pairing
  export NVIDIA_API_KEY=nvapi-...    live Nemotron inference
  export STRIPE_API_KEY=sk_test_...  real test-mode payment links
"""


def main():
    checks = run_checks()
    failed = 0
    for c in checks:
        mark = "OK" if c["ok"] else "FAIL"
        detail = f" — {c['detail']}" if c.get("detail") else ""
        print(f"[{mark}] {c['name']}{detail}")
        if not c["ok"]:
            failed += 1
    print(QUICKSTART)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
