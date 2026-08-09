"""
OpenClaw + Hermes agent workspace loader.

The workspace (``.solvent/workspace/``) is the agent's brain: markdown files
injected into the system prompt each session, following:

- Hermes: SOUL.md is slot #1 (identity), AGENTS.md is project context
- Community: BRAIN.md for active business state (read every session)
- OpenClaw: daily memory logs + an optional MEMORY.md for durable facts
"""

from __future__ import annotations

import os
import re
import shutil
import time
from datetime import date, timedelta
from pathlib import Path

from .config import CONFIG_DIR

WORKSPACE_DIR = CONFIG_DIR / "workspace"
SKILLS_DIR = CONFIG_DIR / "skills"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates" / "workspace"
REPO_ROOT = Path(__file__).resolve().parent.parent

MAX_FILE_CHARS = int(os.environ.get("SOLVENT_WORKSPACE_MAX_CHARS", "8000"))
MAX_TOTAL_CHARS = int(os.environ.get("SOLVENT_WORKSPACE_TOTAL_MAX_CHARS", "40000"))

BOOTSTRAP_FILES = (
    "SOUL.md",
    "AGENTS.md",
    "BRAIN.md",
)

# Hermes/OpenClaw injection order for project context
CONTEXT_FILES = (
    "BRAIN.md",
    "AGENTS.md",
)

SOLVENT_CORE_RULES = (
    "You operate inside SOLVENT's economic kernel. "
    "Use tools for treasury facts, job status, and quotes. "
    "To commission a paid research brief gather topic, budget_cents, and customer_email; "
    "call submit_brief only after user confirmation. "
    "Never claim payment succeeded until job status is paid. "
    "Treasury writes, Stripe, and vendor spend are enforced by stages and guardrails — "
    "you cannot bypass them."
)


def workspace_path() -> Path:
    override = os.environ.get("SOLVENT_WORKSPACE", "").strip()
    return Path(override) if override else WORKSPACE_DIR


def _truncate(text: str, limit: int = MAX_FILE_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 40] + "\n\n[... truncated; read file for full content ...]"


def _read_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not raw.strip():
        return None
    return _truncate(raw)


def _missing_marker(name: str) -> str:
    return f"[missing workspace file: {name}]"


def seed_workspace(*, force: bool = False) -> Path:
    """Create workspace dirs and copy default templates (never overwrite existing)."""
    root = workspace_path()
    root.mkdir(parents=True, exist_ok=True)
    (root / "memory").mkdir(exist_ok=True)
    (root / "skills").mkdir(exist_ok=True)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    skills_src = TEMPLATES_DIR / "skills"
    if skills_src.is_dir():
        for item in skills_src.iterdir():
            dest = root / "skills" / item.name
            if item.is_dir() and not dest.exists():
                shutil.copytree(item, dest)

    for name in BOOTSTRAP_FILES:
        dest = root / name
        if dest.exists() and not force:
            continue
        src = TEMPLATES_DIR / name
        if src.is_file():
            shutil.copy2(src, dest)
        elif not dest.exists():
            dest.write_text(
                f"# {name}\n\n(Edit this file to customize SOLVENT.)\n",
                encoding="utf-8",
            )

    return root


def ensure_workspace() -> Path:
    root = workspace_path()
    if not root.is_dir() or not any((root / f).exists() for f in ("SOUL.md", "AGENTS.md")):
        return seed_workspace()
    return root


def list_workspace_files() -> list[dict]:
    root = ensure_workspace()
    out: list[dict] = []
    for name in BOOTSTRAP_FILES:
        p = root / name
        out.append(
            {
                "name": name,
                "path": str(p),
                "exists": p.is_file(),
                "bytes": p.stat().st_size if p.is_file() else 0,
            }
        )
    mem = root / "memory"
    if mem.is_dir():
        for p in sorted(mem.glob("*.md"))[-7:]:
            out.append(
                {
                    "name": f"memory/{p.name}",
                    "path": str(p),
                    "exists": True,
                    "bytes": p.stat().st_size,
                }
            )
    for skills_root in (root / "skills", SKILLS_DIR):
        if not skills_root.is_dir():
            continue
        for name, path in _iter_skills(skills_root):
            out.append(
                {
                    "name": f"skills/{name}/SKILL.md"
                    if path.name == "SKILL.md"
                    else f"skills/{path.name}",
                    "path": str(path),
                    "exists": True,
                    "bytes": path.stat().st_size,
                }
            )
    return out


def _iter_skills(skills_root: Path):
    """OpenClaw/Hermes layout: skills/{name}/SKILL.md; flat *.md also supported."""
    seen: set[str] = set()
    for skill_dir in sorted(skills_root.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            seen.add(skill_dir.name)
            yield skill_dir.name, skill_md
    for p in sorted(skills_root.glob("*.md")):
        if p.stem not in seen:
            yield p.stem, p


def load_repo_project_context() -> str | None:
    """Hermes pattern: repo-root project instructions (AGENTS.md / HERMES.md)."""
    for name in (".hermes.md", "HERMES.md", "AGENTS.md"):
        body = _read_file(REPO_ROOT / name)
        if body:
            return f"## Repository {name}\n{body}"
    return None


def load_soul() -> str:
    """Hermes slot #1 — agent identity."""
    root = ensure_workspace()
    content = _read_file(root / "SOUL.md")
    return content or _missing_marker("SOUL.md")


def load_context_file(name: str) -> str | None:
    return _read_file(workspace_path() / name)


def load_daily_memory(days_back: int = 1) -> str | None:
    """OpenClaw pattern: today + yesterday daily logs."""
    mem_dir = workspace_path() / "memory"
    if not mem_dir.is_dir():
        return None
    parts: list[str] = []
    today = date.today()
    for offset in range(days_back + 1):
        d = today - timedelta(days=offset)
        p = mem_dir / f"{d.isoformat()}.md"
        chunk = _read_file(p)
        if chunk:
            parts.append(f"## memory/{p.name}\n{chunk}")
    if not parts:
        return None
    return "\n\n".join(parts)


def load_skills() -> str | None:
    """Workspace + global learned skills (OpenClaw skills/*/SKILL.md layout)."""
    sections: list[str] = []
    for label, skills_root in (
        ("workspace skills", workspace_path() / "skills"),
        ("learned skills", SKILLS_DIR),
    ):
        if not skills_root.is_dir():
            continue
        for name, path in _iter_skills(skills_root):
            body = _read_file(path)
            if body:
                sections.append(f"### {label}: {name}\n{body}")
    if not sections:
        return None
    return "# Skills\n\n" + "\n\n".join(sections)


def build_system_prompt(
    *,
    session_kind: str = "main",
    include_memory: bool = True,
) -> str:
    """
    Compose the chat system prompt (Hermes prompt assembly + OpenClaw workspace).

    session_kind: ``main`` (private DM) loads MEMORY.md; ``shared`` skips it.
    """
    ensure_workspace()
    parts: list[str] = []
    total = 0

    def add_block(text: str) -> None:
        nonlocal total
        if not text or not text.strip():
            return
        if total + len(text) > MAX_TOTAL_CHARS:
            text = _truncate(text, MAX_TOTAL_CHARS - total - 50)
        parts.append(text)
        total += len(text)

    # Slot 1: SOUL (identity — verbatim, Hermes)
    add_block(load_soul())

    add_block(SOLVENT_CORE_RULES)

    context_sections: list[str] = []
    for name in CONTEXT_FILES:
        body = load_context_file(name)
        if body:
            context_sections.append(f"## {name}\n{body}")
        else:
            context_sections.append(f"## {name}\n{_missing_marker(name)}")

    if context_sections:
        add_block("# Project Context\n\n" + "\n\n".join(context_sections))

    repo_ctx = load_repo_project_context()
    if repo_ctx:
        add_block(repo_ctx)

    if include_memory and session_kind == "main":
        mem = load_context_file("MEMORY.md")
        if mem:
            add_block(f"# Long-term Memory\n\n{mem}")

    daily = load_daily_memory()
    if daily:
        add_block(f"# Recent Daily Notes\n\n{daily}")

    skills = load_skills()
    if skills:
        add_block(skills)

    return "\n\n".join(parts)


def build_chat_system_prompt(channel: str = "cli") -> str:
    """Gateway/chat entry — authenticated private channels get MEMORY.md."""
    session_kind = "main" if channel in ("cli", "telegram", "interactive") else "shared"
    return build_system_prompt(session_kind=session_kind)


def append_daily_memory(note: str, *, day: date | None = None) -> Path:
    """Append a line to today's memory log (OpenClaw memory/YYYY-MM-DD.md)."""
    ensure_workspace()
    d = day or date.today()
    path = workspace_path() / "memory" / f"{d.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H:%M")
    line = f"- {stamp} {note.strip()}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
    return path


def promote_skill(name: str, content: str) -> Path:
    """Write a learned skill (improver promotions) as skills/{name}/SKILL.md."""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-]+", "-", name.strip().lower()).strip("-") or "skill"
    skill_dir = SKILLS_DIR / safe
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path


def main():
    import argparse

    parser = argparse.ArgumentParser(description="SOLVENT agent workspace")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="seed workspace templates")
    sub.add_parser("list", help="list workspace files")
    args = parser.parse_args()

    if args.cmd == "setup":
        path = seed_workspace()
        print(f"Workspace ready: {path}")
        for row in list_workspace_files():
            mark = "✓" if row["exists"] else "✗"
            print(f"  [{mark}] {row['name']}")
    elif args.cmd == "list":
        for row in list_workspace_files():
            print(f"{row['name']:24} {row['bytes']:6} bytes  {row['path']}")


if __name__ == "__main__":
    main()
