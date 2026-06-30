"""
init.py — first-run scaffolding for SOLVENT.

Creates the application home directory, initialises the treasury database,
seeds workspace files, and prints a clear onboarding summary so users know
exactly where everything lives and what to do next.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .paths import base_dir, data_dir, db_path, reports_dir
from .workspace import seed_workspace, BOOTSTRAP_FILES


_ENV_EXAMPLE = """\
# SOLVENT environment variables (copy to .env and fill in)

# Override the application home directory (default: ~/.solvent when pip-installed)
# SOLVENT_HOME=~/.solvent

# NVIDIA Nemotron — live inference (optional; offline stub works without this)
# NVIDIA_API_KEY=nvapi-...

# Stripe — live test-mode payments (optional; simulate mode works without this)
# STRIPE_API_KEY=sk_test_...

# Telegram — bot notifications (optional)
# TELEGRAM_BOT_TOKEN=...
# TELEGRAM_CHAT_ID=...
"""


def run(*, force: bool = False) -> int:
    """
    Scaffold the SOLVENT runtime environment.

    Returns 0 on success, 1 on error.
    """
    created: list[str] = []
    already: list[str] = []
    errors: list[str] = []

    def _track(path: Path, *, was_new: bool) -> None:
        (created if was_new else already).append(str(path))

    # --- application home -------------------------------------------------
    home = base_dir()
    _track(home, was_new=not home.exists())

    # --- data directory + treasury DB ------------------------------------
    ddir = data_dir()
    _track(ddir, was_new=not ddir.exists())

    rdir = reports_dir()
    _track(rdir, was_new=not rdir.exists())

    db = db_path()
    db_new = not db.exists()
    try:
        from .treasury import Treasury
        t = Treasury(path=str(db))
        # Touch the DB by running the lightest possible query
        with t._conn() as conn:
            conn.execute("SELECT 1")
        _track(db, was_new=db_new)
    except Exception as exc:
        errors.append(f"treasury DB: {exc}")

    # --- .env.example ----------------------------------------------------
    env_example = home / ".env.example"
    if not env_example.exists():
        env_example.write_text(_ENV_EXAMPLE, encoding="utf-8")
        created.append(str(env_example))
    else:
        already.append(str(env_example))

    # --- workspace files --------------------------------------------------
    try:
        workspace_root = seed_workspace(force=force)
        for name in BOOTSTRAP_FILES:
            p = workspace_root / name
            _track(p, was_new=not p.exists() or force)
    except Exception as exc:
        errors.append(f"workspace: {exc}")

    # --- report -----------------------------------------------------------
    print("SOLVENT init")
    print("=" * 60)

    print(f"\n  Home:     {home}")
    print(f"  Data:     {ddir}")
    print(f"  DB:       {db}")
    print(f"  Reports:  {rdir}")

    if created:
        print(f"\n  Created ({len(created)}):")
        for p in created:
            print(f"    + {p}")

    if already:
        print(f"\n  Already present ({len(already)}) — skipped.")

    if errors:
        print(f"\n  Errors ({len(errors)}):")
        for e in errors:
            print(f"    ! {e}")
        return 1

    print("""
Next steps:
  solvent doctor          verify the full stack
  solvent                 run the demo (no keys needed)
  solvent finance         financial report

Optional: edit .env.example → .env, fill in API keys, then:
  source .env
  solvent doctor          re-run to confirm live features

Documentation: https://github.com/ianalloway/solvent-agent
""")
    return 0


def main():
    import argparse
    p = argparse.ArgumentParser(
        description="Initialise SOLVENT — create data dir, treasury DB, and workspace files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing workspace template files",
    )
    args = p.parse_args()
    sys.exit(run(force=args.force))


if __name__ == "__main__":
    main()
