"""
paths.py — where SOLVENT keeps its runtime data.

Resolution order for the application home directory:

1. ``$SOLVENT_HOME`` if set (explicit override).
2. The repository root, when running from a source checkout (detected by a
   sibling ``pyproject.toml`` or ``.git``) — preserves the historical
   ``<repo>/data`` and ``<repo>/treasury_dashboard.html`` locations.
3. ``~/.solvent`` otherwise — so a ``pip install``ed ``solvent`` writes to the
   user's home instead of into ``site-packages``.

All runtime artifacts (the treasury DB, generated reports, the dashboard,
logs, outboxes) live under this home. The agent *workspace* (SOUL/BRAIN/...)
is a separate concern and not managed here.
"""

from __future__ import annotations

import os
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parent


def _is_source_checkout() -> bool:
    return (_REPO_ROOT / "pyproject.toml").is_file() or (_REPO_ROOT / ".git").exists()


def base_dir() -> Path:
    """The application home directory (created if necessary)."""
    env = os.environ.get("SOLVENT_HOME")
    if env:
        base = Path(env).expanduser().resolve()
    elif _is_source_checkout():
        base = _REPO_ROOT
    else:
        base = Path.home() / ".solvent"
    base.mkdir(parents=True, exist_ok=True)
    return base


def data_dir() -> Path:
    """Directory for the treasury DB, logs, status JSON, outboxes, etc."""
    d = base_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def reports_dir() -> Path:
    """Directory for generated research-brief deliverables."""
    d = data_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "solvent.db"


def dashboard_html() -> Path:
    return base_dir() / "treasury_dashboard.html"
