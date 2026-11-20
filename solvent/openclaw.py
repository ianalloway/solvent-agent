"""Plug SOLVENT into an OpenClaw Gateway as a skill (`solvent connect`).

The OpenClaw apps pair with an OpenClaw Gateway (WebSocket, port 18789), never
with SOLVENT. SOLVENT joins through a SKILL.md that teaches the Gateway's agent
to call SOLVENT's HTTP API with curl; see docs/OPENCLAW.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from .access import is_loopback_url
from .config import effective_base_url

SKILL_NAME = "solvent"
GUIDE_URL = "https://github.com/ianalloway/solvent-agent/blob/main/docs/OPENCLAW.md"
SKILL_TEMPLATE = (
    Path(__file__).resolve().parent / "templates" / "openclaw" / SKILL_NAME / "SKILL.md"
)


def skill_text() -> str:
    """The SKILL.md this release ships."""
    return SKILL_TEMPLATE.read_text(encoding="utf-8")


def default_skills_dir(env: Mapping[str, str] | None = None) -> Path:
    """The OpenClaw workspace skills folder (also the host side of its Docker mount)."""
    env = os.environ if env is None else env
    workspace = env.get("OPENCLAW_WORKSPACE_DIR", "").strip()
    root = Path(workspace).expanduser() if workspace else Path.home() / ".openclaw" / "workspace"
    return root / "skills"


def skill_path(skills_dir: Path) -> Path:
    return skills_dir / SKILL_NAME / "SKILL.md"


def skill_status(skills_dir: Path) -> str:
    """``missing``, ``current`` (this release's skill) or ``different`` (edited or older)."""
    path = skill_path(skills_dir)
    if not path.is_file():
        return "missing"
    return "current" if path.read_text(encoding="utf-8") == skill_text() else "different"


def install_skill(skills_dir: Path, *, force: bool = False) -> str:
    """Write the skill; returns ``installed``, ``updated``, ``unchanged`` or ``kept``.

    A skill that differs from this release's (a local edit, or another release)
    is ``kept`` unless ``force`` is set.
    """
    status = skill_status(skills_dir)
    if status == "current":
        return "unchanged"
    if status == "different" and not force:
        return "kept"
    path = skill_path(skills_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(skill_text(), encoding="utf-8")
    return "installed" if status == "missing" else "updated"


def report(skills_dir: Path, env: Mapping[str, str] | None = None) -> dict:
    """What the Gateway needs to reach SOLVENT, and what is still missing."""
    env = os.environ if env is None else env
    url, source = effective_base_url(env)
    notes = []
    if is_loopback_url(url):
        notes.append(
            f"{url} only works for a Gateway on this host outside Docker; inside a "
            "container 127.0.0.1 is the container, so use SOLVENT's public https URL"
        )
    if not env.get("SOLVENT_DASHBOARD_TOKEN", "").strip():
        notes.append(
            "SOLVENT_DASHBOARD_TOKEN is not set in this shell; without it on the Gateway "
            "the skill can order and track briefs but not quote or read reports"
        )
    return {
        "skill": {"path": str(skill_path(skills_dir)), "status": skill_status(skills_dir)},
        "solvent_url": url,
        "solvent_url_source": source,
        "gateway_env": {
            "SOLVENT_URL": url,
            "SOLVENT_DASHBOARD_TOKEN": "<your SOLVENT_DASHBOARD_TOKEN>",
        },
        "notes": notes,
    }


def _tilde(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home) :] if path == home or path.startswith(home + os.sep) else path


_STATUS_TEXT = {
    "missing": "not installed (run: solvent connect --install-skill)",
    "current": "installed",
    "different": "differs from this release (edited, or older); --install-skill --force replaces it",
}

_INSTALL_TEXT = {
    "installed": "Installed the solvent skill.",
    "updated": "Updated the solvent skill.",
    "unchanged": "The solvent skill is already up to date.",
    "kept": "Kept the existing solvent skill: it differs from this release. Use --force to replace it.",
}


def _print_report(data: dict) -> None:
    print("SOLVENT for OpenClaw\n")
    print("The OpenClaw app pairs with your OpenClaw Gateway (default port 18789),")
    print("not with SOLVENT. SOLVENT joins that Gateway as a skill its agent can call.\n")
    print(f"  Skill    {_tilde(data['skill']['path'])}")
    print(f"           {_STATUS_TEXT[data['skill']['status']]}")
    print(f"  SOLVENT  {data['solvent_url']}  (from {data['solvent_url_source']})\n")
    print("Give the Gateway these, e.g. in ~/.openclaw/.env on the Gateway host:")
    for name, value in data["gateway_env"].items():
        print(f"  {name}={value}")
    print("\nThen run `openclaw skills check` and start a new chat with /new.")
    print(f"Guide: {GUIDE_URL}")
    for note in data["notes"]:
        print(f"\nNOTE: {note}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="solvent connect",
        description="Plug SOLVENT into an OpenClaw Gateway as a skill.",
    )
    parser.add_argument(
        "--install-skill", action="store_true", help="write the solvent skill into the skills dir"
    )
    parser.add_argument(
        "--skills-dir",
        type=Path,
        help="skills folder (default: $OPENCLAW_WORKSPACE_DIR/skills or ~/.openclaw/workspace/skills)",
    )
    parser.add_argument("--force", action="store_true", help="replace an edited or older skill")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    if args.force and not args.install_skill:
        parser.error("--force only applies with --install-skill")

    skills_dir = (args.skills_dir or default_skills_dir()).expanduser()
    outcome = install_skill(skills_dir, force=args.force) if args.install_skill else None
    data = report(skills_dir)
    if args.json:
        print(json.dumps({**data, "install": outcome}, indent=2))
    else:
        if outcome:
            print(_INSTALL_TEXT[outcome] + "\n")
        _print_report(data)
    if outcome == "kept":
        sys.exit(1)


if __name__ == "__main__":
    main()
