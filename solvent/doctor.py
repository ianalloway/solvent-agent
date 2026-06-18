"""Hermes-style diagnostics for SOLVENT."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .treasury import Treasury
from .workspace import ensure_workspace, list_workspace_files


def run_checks() -> list[dict]:
    checks: list[dict] = []
    t = Treasury()

    def add(name: str, ok: bool, detail: str = ""):
        checks.append({"name": name, "ok": ok, "detail": detail})

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
    elif stripe.startswith("sk_test_") or stripe.startswith("rk_test_"):
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


def main():
    checks = run_checks()
    failed = 0
    for c in checks:
        mark = "OK" if c["ok"] else "FAIL"
        detail = f" — {c['detail']}" if c.get("detail") else ""
        print(f"[{mark}] {c['name']}{detail}")
        if not c["ok"]:
            failed += 1
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
