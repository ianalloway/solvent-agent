"""Consistent, restorable snapshots of a SOLVENT home (`solvent backup`).

The treasury and webhook databases are copied with SQLite's online backup API,
so a snapshot taken while `serve` and `worker` run is consistent. Saved config,
policies, the agent workspace and delivered briefs are copied as files. Each
archive carries a manifest of what it holds and each database's row counts:
``--verify`` checks an archive against it without touching the live home, and
``--restore`` unpacks one into a new, empty directory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

from . import __version__
from .paths import base_dir

MANIFEST = "manifest.json"
FORMAT = 1
DEFAULT_KEEP = 14
ARCHIVE_RE = re.compile(r"solvent-backup-\d{8}T\d{6}Z\.tar\.gz")
WEBHOOK_DB = ".solvent/webhooks.db"
TREES = (".solvent/workspace", "data/reports", "data/outbox")


class BackupError(Exception):
    pass


def default_dir() -> Path:
    return base_dir() / "backups"


def _database_sources(home: Path) -> list[tuple[Path, str]]:
    from . import treasury

    return [(Path(treasury.DB_PATH), "data/solvent.db"), (home / WEBHOOK_DB, WEBHOOK_DB)]


def _file_sources(home: Path, exclude: Path) -> list[tuple[Path, str]]:
    found = sorted((home / ".solvent").glob("*.json"))
    for tree in TREES:
        root = home / tree
        if root.is_dir():
            found += sorted(p for p in root.rglob("*") if p.is_file())
    exclude = exclude.resolve()
    return [
        (path, path.relative_to(home).as_posix())
        for path in found
        if not path.is_symlink() and exclude not in path.resolve().parents
    ]


def _snapshot(src: Path, out: Path) -> None:
    source = sqlite3.connect(src, timeout=30.0)
    try:
        target = sqlite3.connect(out)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _db_facts(path: Path) -> dict:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        ]
        rows = {
            name: conn.execute(
                'SELECT COUNT(*) FROM "{}"'.format(name.replace('"', '""'))
            ).fetchone()[0]
            for name in tables
        }
    finally:
        conn.close()
    return {"integrity": integrity, "rows": rows}


def create(dest: Path | None = None, *, keep: int = DEFAULT_KEEP, now: float | None = None) -> dict:
    """Write a new archive into ``dest`` and prune it to the newest ``keep`` (0 keeps all)."""
    home = base_dir()
    dest = (dest or default_dir()).expanduser()
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    path = dest / f"solvent-backup-{stamp}.tar.gz"
    if path.exists():
        raise BackupError(f"{path} already exists; wait a second and run it again")
    with tempfile.TemporaryDirectory(dir=dest, prefix=".partial-") as tmp:
        staging = Path(tmp)
        members: list[tuple[Path, str]] = []
        databases = {}
        for src, arcname in _database_sources(home):
            if not src.is_file():
                continue
            out = staging / arcname
            out.parent.mkdir(parents=True, exist_ok=True)
            _snapshot(src, out)
            databases[arcname] = _db_facts(out)
            members.append((out, arcname))
        if "data/solvent.db" not in databases:
            raise BackupError(f"no treasury database at {_database_sources(home)[0][0]}")
        members += _file_sources(home, exclude=dest)
        manifest = {
            "format": FORMAT,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "solvent_version": __version__,
            "databases": databases,
            "files": [arcname for _, arcname in members],
        }
        (staging / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        partial = staging / "archive.tar.gz"
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(staging / MANIFEST, arcname=MANIFEST)
            for src, arcname in members:
                tar.add(src, arcname=arcname, recursive=False)
        os.chmod(partial, 0o600)
        os.replace(partial, path)
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "files": len(members),
        "databases": databases,
        "pruned": prune(dest, keep),
    }


def list_archives(dest: Path | None = None) -> list[dict]:
    dest = (dest or default_dir()).expanduser()
    if not dest.is_dir():
        return []
    return [
        {"name": p.name, "path": str(p), "bytes": p.stat().st_size}
        for p in sorted(dest.iterdir())
        if ARCHIVE_RE.fullmatch(p.name) and p.is_file()
    ]


def prune(dest: Path, keep: int) -> list[str]:
    """Delete all but the newest ``keep`` archives (0 keeps all); returns what went."""
    if keep <= 0:
        return []
    archives = list_archives(dest)
    doomed = archives[:-keep] if len(archives) > keep else []
    for archive in doomed:
        Path(archive["path"]).unlink()
    return [archive["name"] for archive in doomed]


def _unsafe(member: tarfile.TarInfo) -> bool:
    name = PurePosixPath(member.name)
    return (
        not (member.isfile() or member.isdir())
        or name.is_absolute()
        or ".." in name.parts
        or "\\" in member.name
    )


def _read_manifest(tar: tarfile.TarFile) -> dict:
    try:
        handle = tar.extractfile(MANIFEST)
    except KeyError:
        raise BackupError("no manifest: not a SOLVENT backup") from None
    if handle is None:
        raise BackupError("no manifest: not a SOLVENT backup")
    with handle:
        manifest = json.load(handle)
    if manifest.get("format") != FORMAT:
        raise BackupError(f"unsupported backup format {manifest.get('format')!r}")
    return manifest


def verify(archive: Path) -> dict:
    """Check an archive's members, database integrity and row counts against its manifest."""
    problems: list[str] = []
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            problems += [f"unsafe member {m.name!r}" for m in members if _unsafe(m)]
            manifest = _read_manifest(tar)
            present = {m.name for m in members if m.isfile()}
            problems += [f"missing {name}" for name in manifest["files"] if name not in present]
            with tempfile.TemporaryDirectory() as tmp:
                for arcname, expected in manifest["databases"].items():
                    out = Path(tmp) / PurePosixPath(arcname).name
                    handle = tar.extractfile(arcname) if arcname in present else None
                    if handle is None:
                        continue
                    with handle, open(out, "wb") as dst:
                        shutil.copyfileobj(handle, dst)
                    facts = _db_facts(out)
                    if facts["integrity"] != "ok":
                        problems.append(f"{arcname}: integrity check says {facts['integrity']}")
                    if facts["rows"] != expected["rows"]:
                        problems.append(f"{arcname}: row counts differ from the manifest")
    except (OSError, tarfile.TarError, sqlite3.Error, ValueError) as exc:
        return {"ok": False, "problems": [f"unreadable archive: {exc}"]}
    except BackupError as exc:
        return {"ok": False, "problems": [str(exc)]}
    return {
        "ok": not problems,
        "problems": problems,
        "created_at": manifest["created_at"],
        "solvent_version": manifest.get("solvent_version"),
        "files": len(manifest["files"]),
        "databases": manifest["databases"],
    }


def restore(archive: Path, into: Path) -> dict:
    """Unpack a verified archive into a new or empty directory; never over a live home."""
    report = verify(archive)
    if not report["ok"]:
        raise BackupError("archive failed verification: " + "; ".join(report["problems"]))
    into = into.expanduser()
    if into.exists() and (not into.is_dir() or any(into.iterdir())):
        raise BackupError(f"{into} is not an empty directory; restore into a new one")
    into.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = into.resolve()
    restored = 0
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name == MANIFEST or not member.isfile():
                continue
            target = (root / member.name).resolve()
            if root not in target.parents:
                raise BackupError(f"unsafe member {member.name!r}")
            # mkdir(parents=True) applies its mode to the last directory only.
            for parent in reversed(target.relative_to(root).parents[:-1]):
                (root / parent).mkdir(exist_ok=True, mode=0o700)
            handle = tar.extractfile(member)
            with handle, open(target, "wb") as dst:
                shutil.copyfileobj(handle, dst)
            os.chmod(target, 0o600)
            restored += 1
    return {"into": str(root), "files": restored, "created_at": report["created_at"]}


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _ledger_line(databases: dict) -> str:
    rows = databases.get("data/solvent.db", {}).get("rows", {})
    return f"{rows.get('jobs', 0)} jobs, {rows.get('ledger', 0)} ledger entries"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="solvent backup",
        description="Snapshot the treasury, config and delivered briefs into a restorable archive.",
    )
    parser.add_argument(
        "--dir", type=Path, help="where archives live (default: $SOLVENT_HOME/backups)"
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP,
        help=f"archives to keep after writing a new one; 0 keeps all (default: {DEFAULT_KEEP})",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", help="list archives instead of writing one")
    action.add_argument("--verify", type=Path, metavar="ARCHIVE", help="check an archive")
    action.add_argument(
        "--restore", type=Path, metavar="ARCHIVE", help="unpack an archive (--into)"
    )
    parser.add_argument("--into", type=Path, help="new, empty directory for --restore")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    if args.keep < 0:
        parser.error("--keep must be 0 or more")
    if bool(args.restore) != bool(args.into):
        parser.error("--restore and --into go together")

    def emit(data: dict | list, text: str) -> None:
        print(json.dumps(data, indent=2) if args.json else text)

    try:
        if args.list:
            archives = list_archives(args.dir)
            emit(
                archives,
                "\n".join(f"{a['name']}  {_size(a['bytes'])}" for a in archives)
                or "No backups yet.",
            )
        elif args.verify:
            report = verify(args.verify)
            if report["ok"]:
                text = (
                    f"OK: backup from {report['created_at']}, {report['files']} files, "
                    f"{_ledger_line(report['databases'])}."
                )
            else:
                text = "FAILED:\n" + "\n".join(f"  {p}" for p in report["problems"])
            emit(report, text)
            if not report["ok"]:
                sys.exit(1)
        elif args.restore:
            done = restore(args.restore, args.into)
            emit(
                done,
                f"Restored {done['files']} files into {done['into']}. With serve and worker "
                "stopped, point SOLVENT_HOME at it (or move it into place) and restart them.",
            )
        else:
            made = create(args.dir, keep=args.keep)
            pruned = f" Pruned {len(made['pruned'])} old archive(s)." if made["pruned"] else ""
            emit(
                made,
                f"Backed up {made['files']} files ({_ledger_line(made['databases'])}) to "
                f"{made['path']} ({_size(made['bytes'])}).{pruned}",
            )
    except BackupError as exc:
        print(f"solvent backup: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
