"""Cross-process chat notifications via append-only JSONL outbox."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .paths import data_dir


def _outbox_path() -> Path:
    return data_dir() / "chat_outbox.jsonl"


def _lock_path() -> Path:
    return data_dir() / "chat_outbox.lock"


class _LockCtx:
    def __enter__(self):
        import fcntl

        _lock = _lock_path()
        _lock.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(_lock, os.O_CREAT | os.O_RDWR)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        self._lock_path = _lock
        return self

    def __exit__(self, *args):
        import fcntl

        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)


def enqueue_chat(channel: str, external_id: str, text: str) -> None:
    """Append a chat notification for another process (e.g. serve) to deliver."""
    row = {
        "channel": channel,
        "external_id": external_id,
        "text": text,
        "ts": time.time(),
    }
    _outbox_path().parent.mkdir(parents=True, exist_ok=True)
    with _LockCtx(), _outbox_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def drain_chat_outbox() -> list[dict]:
    """Return and remove all pending outbox rows (best-effort, file-locked)."""
    outbox = _outbox_path()
    if not outbox.is_file():
        return []
    with _LockCtx():
        raw = outbox.read_text(encoding="utf-8")
        outbox.write_text("", encoding="utf-8")
    rows: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
