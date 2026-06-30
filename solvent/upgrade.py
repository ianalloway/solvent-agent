"""
upgrade.py — version check and upgrade helper for SOLVENT.

Usage:
    solvent upgrade           print upgrade instructions if a newer version exists
    solvent upgrade --check   exit 1 if current version is behind PyPI (CI-friendly)
    solvent upgrade --install run pip install to upgrade in-place
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Optional

_PYPI_URL = "https://pypi.org/pypi/solvent-agent/json"
_TIMEOUT = 5  # seconds


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse 'X.Y.Z' into a comparable tuple; non-numeric parts become 0."""
    parts = []
    for seg in v.strip().lstrip("v").split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def current_version() -> str:
    from . import __version__
    return __version__


def latest_pypi_version() -> Optional[str]:
    """Fetch the latest release version from PyPI. Returns None on any error."""
    try:
        req = urllib.request.Request(
            _PYPI_URL,
            headers={"User-Agent": "solvent-agent/upgrade-check"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        return data["info"]["version"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, OSError):
        return None


def check_upgrade(*, quiet: bool = False) -> dict:
    """
    Compare current version to latest on PyPI.

    Returns a dict with keys: current, latest, up_to_date, error.
    """
    current = current_version()
    latest = latest_pypi_version()

    if latest is None:
        if not quiet:
            print("[solvent upgrade] Could not reach PyPI — skipping version check.")
        return {"current": current, "latest": None, "up_to_date": True, "error": True}

    up_to_date = _parse_version(current) >= _parse_version(latest)

    if not quiet:
        if up_to_date:
            print(f"solvent {current} is up to date.")
        else:
            print(f"solvent {current} → {latest} available.")
            print("  Upgrade with:  pip install --upgrade solvent-agent")
            print("  Or full extras: pip install --upgrade 'solvent-agent[all]'")

    return {"current": current, "latest": latest, "up_to_date": up_to_date, "error": False}


def main():
    import argparse
    import subprocess

    p = argparse.ArgumentParser(
        description="Check for solvent-agent upgrades on PyPI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if a newer version is available (CI-friendly)",
    )
    p.add_argument(
        "--install",
        action="store_true",
        help="run pip install --upgrade solvent-agent if a newer version is available",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="output result as JSON",
    )
    args = p.parse_args()

    result = check_upgrade(quiet=args.as_json)

    if args.as_json:
        print(json.dumps(result, indent=2))

    if not result["up_to_date"] and args.install:
        print("\nRunning: pip install --upgrade solvent-agent")
        rc = subprocess.call([sys.executable, "-m", "pip", "install", "--upgrade", "solvent-agent"])
        sys.exit(rc)

    if args.check and not result["up_to_date"]:
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
