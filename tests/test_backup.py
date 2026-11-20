"""`solvent backup`: consistent snapshots, verification, restore and retention."""

import io
import json
import tarfile
import threading
import time
from pathlib import Path

import pytest

from solvent import backup
from solvent.treasury import Treasury


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A SOLVENT home with a ledger, a webhook log, config, workspace and a brief."""
    from solvent import treasury as treasury_mod
    from solvent.webhook_log import WebhookLog

    root = tmp_path / "home"
    monkeypatch.setenv("SOLVENT_HOME", str(root))
    monkeypatch.setattr(treasury_mod, "DB_PATH", root / "data" / "solvent.db")
    t = Treasury()
    t.seed(20_000)
    t.upsert_job("J1", "completed", topic="Lithium outlook")
    WebhookLog()
    for rel, text in {
        ".solvent/spend_policy.json": '{"job_budget_cents": 8000}',
        ".solvent/workspace/SOUL.md": "# soul",
        "data/reports/J1.md": "# brief",
        "data/outbox/J1.eml": "Subject: brief",
        "data/solvent.log": "noise",
        "treasury_dashboard.html": "<html>",
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _names(archive: str | Path) -> set[str]:
    with tarfile.open(archive, "r:gz") as tar:
        return {m.name for m in tar.getmembers()}


def _repack(archive: Path, out: Path, *, manifest=None, extra: dict | None = None) -> Path:
    """Copy an archive, optionally rewriting its manifest or adding members."""
    with tarfile.open(archive, "r:gz") as src, tarfile.open(out, "w:gz") as dst:
        for member in src.getmembers():
            data = src.extractfile(member).read()
            if member.name == backup.MANIFEST and manifest is not None:
                data = json.dumps(manifest(json.loads(data))).encode()
                member.size = len(data)
            dst.addfile(member, io.BytesIO(data))
        for name, data in (extra or {}).items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            dst.addfile(info, io.BytesIO(data))
    return out


def test_create_writes_a_private_archive_of_state_not_noise(home):
    made = backup.create()
    path = Path(made["path"])
    assert path.parent == home / "backups" and backup.ARCHIVE_RE.fullmatch(path.name)
    assert path.stat().st_mode & 0o777 == 0o600
    assert _names(path) == {
        backup.MANIFEST,
        "data/solvent.db",
        ".solvent/webhooks.db",
        ".solvent/spend_policy.json",
        ".solvent/workspace/SOUL.md",
        "data/reports/J1.md",
        "data/outbox/J1.eml",
    }
    ledger = made["databases"]["data/solvent.db"]
    assert ledger["integrity"] == "ok" and ledger["rows"]["jobs"] == 1
    assert ledger["rows"]["ledger"] == len(Treasury().entries)


def test_a_snapshot_taken_during_writes_is_consistent(home):
    t = Treasury()
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            t.earn(100, f"payment {i}", job_id=f"J-w{i}")
            i += 1

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        time.sleep(0.2)
        made = backup.create()
    finally:
        stop.set()
        thread.join()
    report = backup.verify(Path(made["path"]))
    assert report["ok"], report["problems"]
    assert report["databases"]["data/solvent.db"]["rows"]["ledger"] > 1


def test_verify_and_restore_round_trip(home, tmp_path):
    made = backup.create()
    report = backup.verify(Path(made["path"]))
    assert report["ok"] and report["files"] == 6
    target = tmp_path / "restored"
    done = backup.restore(Path(made["path"]), target)
    assert done["files"] == 6
    restored = Treasury(target / "data" / "solvent.db")
    assert restored.balance_cents() == Treasury().balance_cents() == 20_000
    assert (target / "data/reports/J1.md").read_text() == "# brief"
    assert (target / "data/reports/J1.md").stat().st_mode & 0o777 == 0o600
    for directory in (target, target / ".solvent", target / ".solvent/workspace", target / "data"):
        assert directory.stat().st_mode & 0o777 == 0o700, directory


def test_restore_refuses_a_non_empty_directory(home, tmp_path):
    made = backup.create()
    with pytest.raises(backup.BackupError, match="not an empty directory"):
        backup.restore(Path(made["path"]), home)


def test_verify_catches_tampering_and_unsafe_members(home, tmp_path):
    good = Path(backup.create()["path"])

    def bump(m):
        m["databases"]["data/solvent.db"]["rows"]["jobs"] += 1
        return m

    edited = _repack(good, tmp_path / "edited.tar.gz", manifest=bump)
    assert "row counts differ" in " ".join(backup.verify(edited)["problems"])

    evil = _repack(good, tmp_path / "evil.tar.gz", extra={"../../escape.txt": b"x"})
    assert "unsafe member '../../escape.txt'" in backup.verify(evil)["problems"]
    with pytest.raises(backup.BackupError, match="failed verification"):
        backup.restore(evil, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()

    def drop_file(m):
        m["files"].append("data/reports/J9.md")
        return m

    short = _repack(good, tmp_path / "short.tar.gz", manifest=drop_file)
    assert "missing data/reports/J9.md" in backup.verify(short)["problems"]


def test_verify_rejects_archives_that_are_not_backups(tmp_path):
    plain = tmp_path / "plain.tar.gz"
    with tarfile.open(plain, "w:gz") as tar:
        info = tarfile.TarInfo("hello.txt")
        tar.addfile(info, io.BytesIO(b""))
    assert backup.verify(plain)["problems"] == ["no manifest: not a SOLVENT backup"]
    junk = tmp_path / "junk.tar.gz"
    junk.write_bytes(b"not a tarball")
    assert backup.verify(junk)["problems"][0].startswith("unreadable archive")


def test_retention_keeps_the_newest(home):
    base = time.time()
    for days in range(3):
        backup.create(keep=0, now=base + days * 86400)
    assert len(backup.list_archives()) == 3
    made = backup.create(keep=2, now=base + 3 * 86400)
    names = [a["name"] for a in backup.list_archives()]
    assert len(names) == 2 and Path(made["path"]).name == names[-1]
    assert len(made["pruned"]) == 2


def test_archives_never_include_earlier_archives(home):
    first = backup.create(now=time.time())
    second = backup.create(now=time.time() + 5)
    assert not any("backups" in name for name in _names(second["path"]))
    assert Path(first["path"]).exists()


def test_create_needs_a_treasury(tmp_path, monkeypatch):
    from solvent import treasury as treasury_mod

    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path / "empty"))
    monkeypatch.setattr(treasury_mod, "DB_PATH", tmp_path / "empty" / "data" / "solvent.db")
    with pytest.raises(backup.BackupError, match="no treasury database"):
        backup.create()


def test_cli_create_list_verify_restore(home, tmp_path, capsys):
    backup.main([])
    out = capsys.readouterr().out
    assert out.startswith("Backed up 6 files (1 jobs, 1 ledger entries) to ")
    backup.main(["--list"])
    name = capsys.readouterr().out.split()[0]
    archive = home / "backups" / name
    backup.main(["--verify", str(archive)])
    assert capsys.readouterr().out.startswith("OK: backup from ")
    backup.main(["--restore", str(archive), "--into", str(tmp_path / "r"), "--json"])
    assert json.loads(capsys.readouterr().out)["files"] == 6


def test_cli_failures_exit_nonzero(home, tmp_path, capsys):
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(b"x")
    with pytest.raises(SystemExit) as exc:
        backup.main(["--verify", str(bad)])
    assert exc.value.code == 1 and "FAILED" in capsys.readouterr().out
    for argv in (["--restore", str(bad)], ["--keep", "-1"], ["--into", str(tmp_path)]):
        with pytest.raises(SystemExit) as exc:
            backup.main(argv)
        assert exc.value.code == 2


def test_backup_is_dispatched_by_the_cli(home, monkeypatch, capsys):
    from solvent.__main__ import main

    monkeypatch.setattr("sys.argv", ["solvent", "backup", "--list"])
    main()
    assert capsys.readouterr().out.strip() == "No backups yet."
