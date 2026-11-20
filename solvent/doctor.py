"""Hermes-style diagnostics for SOLVENT."""

from __future__ import annotations

import importlib.util
import os
import sys

from .paths import base_dir, config_path, data_dir
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
    add(
        "python_version",
        pv >= (3, 10),
        f"{pv.major}.{pv.minor}.{pv.micro} (need >= 3.10)",
    )

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
    policy_path = config_path("spend_policy.json")
    add(
        "spend_policy",
        True,
        f"{policy_path} ({'override' if policy_path.is_file() else 'built-in defaults'})",
    )

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
    from . import llm_backend

    if llm_backend.enabled():
        add("llm_backend", True, llm_backend.describe())

    stripe = os.environ.get("STRIPE_API_KEY", "")
    if stripe.startswith("sk_live_"):
        add("stripe_key", False, "live keys refused")
    elif stripe.startswith(("sk_test_", "rk_test_")):
        add("stripe_key", True, "test key present")
    else:
        add("stripe_key", True, "simulate mode (no key)")

    # Diagnose stale configuration without reading keys or contacting a Nano node.
    retired = sorted(
        name
        for name, value in os.environ.items()
        if value and (name.startswith("SOLVENT_NANO") or name == "SOLVENT_FORCE_NANO_SIMULATE")
    )
    add(
        "retired_payment_config",
        not retired,
        "Nano rail removed; unset: " + ", ".join(retired) if retired else "none",
    )
    historical = [
        job
        for job in t.list_jobs()
        if job.get("status") not in ("completed", "expired") and t.is_legacy_nano_job(job["id"])
    ]
    add(
        "retired_payment_jobs",
        not historical,
        f"{len(historical)} historical Nano jobs require manual reconciliation"
        if historical
        else "none",
    )

    from .access import public_exposure
    from .config import effective_base_url

    base_url, source = effective_base_url()
    env_url = os.environ.get("SOLVENT_BASE_URL", "").strip()
    overridden = source == "config" and env_url and env_url.rstrip("/") != base_url.rstrip("/")
    add(
        "base_url",
        not overridden,
        f"saved base_url {base_url} overrides SOLVENT_BASE_URL={env_url} for serve/worker; "
        f"run: solvent config set base_url {env_url}"
        if overridden
        else f"{base_url} (from {source})",
    )
    exposure = public_exposure(base_url)
    add("public_exposure", not exposure, "; ".join(exposure) if exposure else "ok")

    from .openclaw import default_skills_dir, skill_status

    skills_dir = default_skills_dir()
    if not skills_dir.parent.is_dir():
        add("openclaw_skill", True, f"no OpenClaw workspace at {skills_dir.parent} (optional)")
    else:
        add(
            "openclaw_skill",
            True,
            {
                "missing": "not installed; run: solvent connect --install-skill",
                "current": f"installed in {skills_dir}",
                "different": "differs from this release; run: solvent connect --install-skill --force",
            }[skill_status(skills_dir)],
        )

    from . import push

    push_error = push.config_error()
    push_sink = push.sink()
    add(
        "openclaw_push",
        push_error is None,
        push_error or (f"on, to {push_sink.wake_url}" if push_sink else "off (optional)"),
    )

    tg = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    add("telegram_token", bool(tg), "set" if tg else "telegram disabled")

    stuck = [
        j
        for j in t.list_jobs()
        if j.get("status") in ("awaiting_payment", "in_progress", "paid_pending_fulfill")
    ]
    add("stuck_jobs", len(stuck) == 0, f"{len(stuck)} stuck" if stuck else "none")

    bal = t.balance_cents()
    add("treasury_balance", bal >= 0, f"${bal / 100:.2f}")

    ensure_workspace()
    files = list_workspace_files()
    core = {"SOUL.md", "AGENTS.md", "BRAIN.md"}
    present = {f["name"] for f in files if f["exists"]}
    missing = core - present
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
