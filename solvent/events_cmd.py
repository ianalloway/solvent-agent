"""
events.py — structured per-job stage events from the SOLVENT treasury DB.

Complements `solvent logs` (which reads the append-only log file) with a
queryable view of events persisted to SQLite via observability.log_event.

Usage:
    solvent events                        show last 20 events
    solvent events -n 50                  show last 50 events
    solvent events --job <id>             filter to a specific job (prefix match)
    solvent events --stage <stage>        filter by stage name
    solvent events --json                 output raw JSON
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Optional


def _fmt_ts(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _human_line(ev: dict) -> str:
    ts = _fmt_ts(ev["ts"])
    job = f"[{ev['job_id'][:8]}]" if ev.get("job_id") else "[        ]"
    stage = (ev.get("stage") or "")[:20]
    payload = ev.get("payload_json") or "{}"
    try:
        data = json.loads(payload)
    except Exception:
        data = {}
    extras = []
    if data.get("stripe_ref"):
        extras.append(f"stripe={data['stripe_ref']}")
    if data.get("margin_actual") is not None:
        extras.append(f"margin={data['margin_actual']:.0%}")
    if data.get("duration_ms") is not None:
        extras.append(f"{data['duration_ms']:.0f}ms")
    if data.get("simulated"):
        extras.append("sim")
    suffix = "  " + "  ".join(extras) if extras else ""
    return f"{ts}  {job}  {stage:<20}{suffix}"


def show_events(
    treasury=None,
    *,
    n: int = 20,
    job: Optional[str] = None,
    stage: Optional[str] = None,
    as_json: bool = False,
) -> None:
    if treasury is None:
        from .treasury import Treasury
        treasury = Treasury()

    raw = treasury.list_events(job_id=job if (job and len(job) >= 8) else None, limit=max(n * 5, 500))

    # Apply prefix filter (treasury only does exact job_id match)
    if job:
        raw = [e for e in raw if e.get("job_id") and e["job_id"].startswith(job)]

    if stage:
        raw = [e for e in raw if e.get("stage") == stage]

    events = raw[:n]

    if not events:
        print("(no events)")
        return

    if as_json:
        out = []
        for ev in events:
            row = dict(ev)
            raw_payload = row.pop("payload_json", None)
            try:
                row["payload"] = json.loads(raw_payload) if raw_payload else {}
            except Exception:
                row["payload"] = {}
            out.append(row)
        print(json.dumps(out, indent=2, default=str))
        return

    print(f"  {len(events)} event(s) shown (newest first)\n")
    for ev in events:
        print(_human_line(ev))


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        description="Show structured per-job stage events (newest first).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-n", "--lines", type=int, default=20, metavar="N",
                   help="number of events to show (default 20)")
    p.add_argument("--job", metavar="ID",
                   help="filter to a specific job ID (prefix match)")
    p.add_argument("--stage", metavar="STAGE",
                   help="filter by stage name (e.g. quote, fulfill, charge)")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="output raw JSON")
    args = p.parse_args()

    show_events(n=args.lines, job=args.job, stage=args.stage, as_json=args.as_json)


if __name__ == "__main__":
    main()
