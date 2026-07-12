"""Explicit version check and upgrade helper for SOLVENT."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Optional


_PYPI_URL = "https://pypi.org/pypi/solvent-agent/json"
_TIMEOUT = 5


def _parse_version(version: str) -> tuple[int, ...]:
    """Parse X.Y.Z into a comparable tuple; non-numeric parts become zero."""
    parts = []
    for segment in version.strip().lstrip("v").split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def current_version() -> str:
    from . import __version__

    return __version__


def latest_pypi_version() -> Optional[str]:
    """Fetch the latest release version from PyPI, or None on any error."""
    try:
        request = urllib.request.Request(
            _PYPI_URL,
            headers={"User-Agent": "solvent-agent/upgrade-check"},
        )
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            data = json.loads(response.read().decode())
        return data["info"]["version"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, OSError):
        return None


def check_upgrade(*, quiet: bool = False) -> dict:
    """Compare the installed version with the latest version on PyPI."""
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

    return {
        "current": current,
        "latest": latest,
        "up_to_date": up_to_date,
        "error": False,
    }


def main():
    import argparse
    import subprocess

    parser = argparse.ArgumentParser(
        description="Check for solvent-agent upgrades on PyPI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if a newer version is available (CI-friendly)",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="run pip install --upgrade solvent-agent if a newer version is available",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="output result as JSON",
    )
    args = parser.parse_args()

    result = check_upgrade(quiet=args.as_json)
    if args.as_json:
        print(json.dumps(result, indent=2))

    if not result["up_to_date"] and args.install:
        print("\nRunning: pip install --upgrade solvent-agent")
        return_code = subprocess.call(
            [sys.executable, "-m", "pip", "install", "--upgrade", "solvent-agent"]
        )
        raise SystemExit(return_code)

    if args.check and not result["up_to_date"]:
        raise SystemExit(1)

    raise SystemExit(0)


if __name__ == "__main__":
    main()
