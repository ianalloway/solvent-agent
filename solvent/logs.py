"""
logs.py — tail and filter the SOLVENT structured event log.

Usage:
    solvent logs                  show last 20 log lines
    solvent logs -n 50            show last 50 lines
    solvent logs --follow         live-follow like tail -f
    solvent logs --job <id>       filter to a specific job ID (prefix match)
    solvent logs --stage <stage>  filter by stage name
    solvent logs --json           emit raw JSON lines (default: human-readable)
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from .paths import data_dir

_LOG_FIELDS_ORDER = ("ts", "job_id", "stage", "stripe_ref", "margin_actual", "duration_ms")


def log_path() -> Path:
    return data_dir() / "solvent.log"


def _fmt_ts(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return dt.strftime("%H:%M:%S")


def _fmt_duration(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


def _fmt_margin(m: float) -> str:
    return f"{m * 100:.1f}%"


def _human_line(record: dict) -> str:
    ts = _fmt_ts(record.get("ts", time.time()))
    job = str(record.get("job_id", ""))[:8]
    stage = record.get("stage", "?")
    parts = [f"{ts} [{job}] {stage}"]
    dur = record.get("duration_ms")
    if dur is not None:
        parts.append(_fmt_duration(dur))
    margin = record.get("margin_actual")
    if margin is not None:
        parts.append(f"margin={_fmt_margin(margin)}")
    stripe = record.get("stripe_ref")
    if stripe:
        parts.append(f"stripe={stripe[:12]}")
    extra_skip = {"ts", "job_id", "stage", "duration_ms", "margin_actual", "stripe_ref",
                  "margin_est", "simulated"}
    for k, v in record.items():
        if k not in extra_skip:
            parts.append(f"{k}={v}")
    return "  ".join(parts)


def _iter_lines(path: Path) -> Iterator[str]:
    """Yield non-empty lines from the log file."""
    if not path.is_file():
        return
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                yield line


def _tail_lines(path: Path, n: int) -> list[str]:
    """Return the last n lines of the log file efficiently."""
    if not path.is_file():
        return []
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        if size == 0:
            return []
        chunk = min(size, n * 200)
        f.seek(max(0, size - chunk))
        raw = f.read()
    lines = raw.decode(errors="replace").splitlines()
    lines = [line for line in lines if line.strip()]
    return lines[-n:]


def _parse(line: str) -> dict | None:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _matches(record: dict, *, job: str | None, stage: str | None) -> bool:
    if job:
        if not str(record.get("job_id", "")).startswith(job):
            return False
    if stage:
        if record.get("stage", "") != stage:
            return False
    return True


def show_logs(
    *,
    n: int = 20,
    job: str | None = None,
    stage: str | None = None,
    as_json: bool = False,
    follow: bool = False,
) -> None:
    path = log_path()

    if not follow:
        lines = _tail_lines(path, max(n * 4, 200))  # over-fetch then filter
        records = []
        for line in lines:
            r = _parse(line)
            if r and _matches(r, job=job, stage=stage):
                records.append(r)
        records = records[-n:]
        if not records:
            print("(no log entries)" if not path.is_file() else "(no matching entries)")
            return
        for r in records:
            print(json.dumps(r) if as_json else _human_line(r))
        return

    # --follow: print matching tail lines then watch for new ones
    if path.is_file():
        lines = _tail_lines(path, n)
        for line in lines:
            r = _parse(line)
            if r and _matches(r, job=job, stage=stage):
                print(json.dumps(r) if as_json else _human_line(r))

    pos = path.stat().st_size if path.is_file() else 0
    try:
        while True:
            time.sleep(0.5)
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size < pos:
                pos = 0  # log rotated
            if size == pos:
                continue
            with path.open(encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                new = f.read()
                pos = f.tell()
            for line in new.splitlines():
                line = line.strip()
                if not line:
                    continue
                r = _parse(line)
                if r and _matches(r, job=job, stage=stage):
                    print(json.dumps(r) if as_json else _human_line(r), flush=True)
    except KeyboardInterrupt:
        pass


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        description="Tail the SOLVENT structured event log.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-n", "--lines", type=int, default=20, metavar="N",
                   help="number of recent lines to show (default 20)")
    p.add_argument("-f", "--follow", action="store_true",
                   help="follow the log live (like tail -f)")
    p.add_argument("--job", metavar="ID",
                   help="filter to job ID (prefix match)")
    p.add_argument("--stage", metavar="STAGE",
                   help="filter to a specific pipeline stage")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="emit raw JSON lines")
    p.add_argument("--path", action="store_true",
                   help="print the log file path and exit")
    args = p.parse_args()

    if args.path:
        print(log_path())
        sys.exit(0)

    show_logs(
        n=args.lines,
        job=args.job,
        stage=args.stage,
        as_json=args.as_json,
        follow=args.follow,
    )


if __name__ == "__main__":
    main()
