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
import re
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parent

#: POSIX-form drive prefix exported by MSYS shells, e.g. ``/c/Users/name``.
_MSYS_HOME = re.compile(r"^/([A-Za-z])/(.*)$")


def _is_source_checkout() -> bool:
    return (_REPO_ROOT / "pyproject.toml").is_file() or (_REPO_ROOT / ".git").exists()


def _normalise_home(value: str) -> Path:
    """Turn a home path from the environment into a usable ``Path``.

    MSYS shells (Git-Bash, Cygwin) export ``HOME`` in POSIX form
    (``/c/Users/name``). On Windows that would resolve to the junk path
    ``C:\\c\\Users\\name``, so translate the drive prefix first.
    """
    if os.name == "nt":
        match = _MSYS_HOME.match(value)
        if match:
            drive, rest = match.groups()
            return Path(f"{drive.upper()}:/{rest}")
    return Path(value)


def _home_dir() -> Path:
    """The user's home directory, honouring the environment.

    ``Path.home()`` ignores ``$HOME`` on Windows, so relocating the home
    directory by exporting it silently wrote to the real one. ``HOME`` wins
    everywhere (it is the documented contract); ``USERPROFILE`` is the
    Windows-native fallback.
    """
    for var in ("HOME", "USERPROFILE"):
        value = os.environ.get(var)
        if value:
            return _normalise_home(value)
    return Path.home()


def base_dir() -> Path:
    """The application home directory (created if necessary)."""
    env = os.environ.get("SOLVENT_HOME")
    if env:
        # Same MSYS drive-prefix translation we apply to $HOME — otherwise
        # SOLVENT_HOME=/c/Users/... on Git-Bash writes to a junk path.
        base = _normalise_home(env).expanduser().resolve()
    elif _is_source_checkout():
        base = _REPO_ROOT
    else:
        base = _home_dir() / ".solvent"
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
