"""Cross-process chat notifications via append-only JSONL outbox."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

_OUTBOX = Path(__file__).resolve().parent.parent / "data" / "chat_outbox.jsonl"
_LOCK = Path(__file__).resolve().parent.parent / "data" / "chat_outbox.lock"


class _LockCtx:
    def __enter__(self):
        import fcntl

        _LOCK.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(_LOCK, os.O_CREAT | os.O_RDWR)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
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
    _OUTBOX.parent.mkdir(parents=True, exist_ok=True)
    with _LockCtx():
        with _OUTBOX.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def drain_chat_outbox() -> list[dict]:
    """Return and remove all pending outbox rows (best-effort, file-locked)."""
    if not _OUTBOX.is_file():
        return []
    with _LockCtx():
        raw = _OUTBOX.read_text(encoding="utf-8")
        _OUTBOX.write_text("", encoding="utf-8")
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
