"""
logs.py — tail and filter the SOLVENT structured event log.

Usage:
    solvent logs                  show last 20 log lines
    solvent logs -n 50            show last 50 lines
    solvent logs --follow         live-follow like tail -f
    solvent logs --job <id>       filter to a specific job ID (prefix match)
    solvent logs --stage <stage>  filter by stage name
    solvent logs --min-duration-ms 500  show stages taking at least half a second
    solvent logs --json           emit raw JSON lines (default: human-readable)
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .paths import data_dir

_LOG_FIELDS_ORDER = (
    "ts",
    "job_id",
    "stage",
    "stripe_ref",
    "margin_actual",
    "duration_ms",
)


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
    extra_skip = {
        "ts",
        "job_id",
        "stage",
        "duration_ms",
        "margin_actual",
        "stripe_ref",
        "margin_est",
        "simulated",
    }
    for k, v in record.items():
        if k not in extra_skip:
            parts.append(f"{k}={' '.join(str(v).split())}")
    return "  ".join(parts)


_TAIL_BLOCK = 8192


def _tail_lines(path: Path, n: int) -> list[str]:
    """Return the last n complete lines of the log file, however long they are."""
    if n <= 0 or not path.is_file():
        return []
    with path.open("rb") as f:
        f.seek(0, 2)
        pos = f.tell()
        raw = b""
        while pos > 0:
            step = min(_TAIL_BLOCK, pos)
            pos -= step
            f.seek(pos)
            raw = f.read(step) + raw
            lines = _complete_lines(raw, at_start=pos == 0)
            if len(lines) >= n:
                break
    return _complete_lines(raw, at_start=pos == 0)[-n:]


def _complete_lines(raw: bytes, *, at_start: bool) -> list[str]:
    lines = raw.decode(errors="replace").splitlines()
    if not at_start:
        lines = lines[1:]
    return [line for line in lines if line.strip()]


def _parse(line: str) -> dict | None:
    try:
        record = json.loads(line)
        return record if isinstance(record, dict) else None
    except json.JSONDecodeError:
        return None


def _matches(
    record: dict, *, job: str | None, stage: str | None, min_duration_ms: float | None = None
) -> bool:
    if job:
        if not str(record.get("job_id", "")).startswith(job):
            return False
    if stage:
        if record.get("stage", "") != stage:
            return False
    if min_duration_ms is not None:
        duration = record.get("duration_ms")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            return False
        if not math.isfinite(duration) or duration < min_duration_ms:
            return False
    return True


def show_logs(
    *,
    n: int = 20,
    job: str | None = None,
    stage: str | None = None,
    min_duration_ms: float | None = None,
    as_json: bool = False,
    follow: bool = False,
) -> None:
    path = log_path()

    def matching_records(lines):
        for line in lines:
            record = _parse(line)
            if record and _matches(record, job=job, stage=stage, min_duration_ms=min_duration_ms):
                yield record

    pos = 0
    records = []
    if path.is_file():
        if job or stage or min_duration_ms is not None:
            # Sparse matches may be far back in a busy log. Bound memory by the display limit.
            with path.open(encoding="utf-8", errors="replace") as stream:
                records = deque(matching_records(stream), maxlen=max(0, n))
                pos = stream.tell()
        else:
            pos = path.stat().st_size
            lines = _tail_lines(path, n if follow else max(n * 4, 200))
            records = deque(matching_records(lines), maxlen=max(0, n))
    for record in records:
        print(json.dumps(record) if as_json else _human_line(record))

    if not follow:
        if not records:
            print("(no log entries)" if not path.is_file() else "(no matching entries)")
        return

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
            for record in matching_records(new.splitlines()):
                print(json.dumps(record) if as_json else _human_line(record), flush=True)
    except KeyboardInterrupt:
        pass


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        description="Tail the SOLVENT structured event log.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "-n",
        "--lines",
        type=int,
        default=20,
        metavar="N",
        help="number of recent lines to show (default 20)",
    )
    p.add_argument("-f", "--follow", action="store_true", help="follow the log live (like tail -f)")
    p.add_argument("--job", metavar="ID", help="filter to job ID (prefix match)")
    p.add_argument("--stage", metavar="STAGE", help="filter to a specific pipeline stage")
    p.add_argument(
        "--min-duration-ms", type=float, help="minimum stage duration in milliseconds (inclusive)"
    )
    p.add_argument("--json", dest="as_json", action="store_true", help="emit raw JSON lines")
    p.add_argument("--path", action="store_true", help="print the log file path and exit")
    args = p.parse_args()

    if args.min_duration_ms is not None and (
        not math.isfinite(args.min_duration_ms) or args.min_duration_ms < 0
    ):
        p.error("--min-duration-ms must be a finite, non-negative number")

    if args.path:
        print(log_path())
        sys.exit(0)

    show_logs(
        n=args.lines,
        job=args.job,
        stage=args.stage,
        min_duration_ms=args.min_duration_ms,
        as_json=args.as_json,
        follow=args.follow,
    )


if __name__ == "__main__":
    main()
