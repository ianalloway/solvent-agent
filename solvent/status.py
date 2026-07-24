"""
status.py — quick at-a-glance summary of the SOLVENT agent's live state.

Usage:
    solvent status          human-readable summary
    solvent status --json   machine-readable JSON
    solvent status --watch  refresh every N seconds (default 5)
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

_STATUS_LABELS = {
    "completed": "done",
    "failed": "failed",
    "in_progress": "running",
    "awaiting_payment": "awaiting payment",
    "paid_pending_fulfill": "paid/fulfilling",
}

_MARK = {
    "done": "✓",
    "failed": "✗",
    "running": "▶",
    "awaiting payment": "…",
    "paid/fulfilling": "▶",
}


def _fmt_cents(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _time_ago(ts: float) -> str:
    """Return a human-readable 'N ago' string for a Unix timestamp."""
    delta = time.time() - ts
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    return f"{int(delta / 86400)}d ago"


def gather(treasury=None) -> dict[str, Any]:
    """Collect all status data into a single dict."""
    if treasury is None:
        from .treasury import Treasury
        treasury = Treasury()

    snap = treasury.snapshot()
    jobs = treasury.list_jobs()
    events = treasury.list_events(limit=5)

    # Count by status
    status_counts: dict[str, int] = {}
    for j in jobs:
        s = j.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    # Most recent job
    last_job = None
    if jobs:
        last_job = max(jobs, key=lambda j: j.get("updated_at") or j.get("created_at") or 0)

    # Last event time
    last_event_ts = events[0].get("ts") if events else None

    # API key presence
    keys = {
        "nvidia": bool(os.environ.get("NVIDIA_API_KEY")),
        "stripe": bool(os.environ.get("STRIPE_API_KEY")),
        "telegram": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
    }

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "balance_cents": snap.get("balance_cents", 0),
        "revenue_cents": snap.get("revenue_cents", 0),
        "net_profit_cents": snap.get("net_profit_cents", 0),
        "margin_pct": snap.get("margin_pct", 0.0),
        "total_jobs": len(jobs),
        "status_counts": status_counts,
        "last_job": {
            "id": last_job["id"],
            "topic": (last_job.get("topic") or "")[:60],
            "status": last_job.get("status", ""),
            "updated_at": last_job.get("updated_at") or last_job.get("created_at"),
        } if last_job else None,
        "last_event_ts": last_event_ts,
        "api_keys": keys,
    }


def format_status(data: dict) -> str:
    lines = []
    ts = data.get("timestamp", "")[:19].replace("T", " ")
    lines.append(f"SOLVENT status  {ts} UTC")
    lines.append("─" * 50)

    # Treasury
    bal = data["balance_cents"]
    rev = data["revenue_cents"]
    margin = data["margin_pct"]
    lines.append(f"  Balance   {_fmt_cents(bal):>10}   Revenue {_fmt_cents(rev):>10}   Margin {margin:.1f}%")
    lines.append("")

    # Jobs
    total = data["total_jobs"]
    counts = data["status_counts"]
    active = counts.get("in_progress", 0) + counts.get("paid_pending_fulfill", 0)
    pending = counts.get("awaiting_payment", 0)
    done = counts.get("completed", 0)
    failed = counts.get("failed", 0)

    lines.append(f"  Jobs      {total} total  •  {active} running  •  {pending} awaiting payment")
    lines.append(f"            {done} completed  •  {failed} failed")

    last = data.get("last_job")
    if last:
        ago = _time_ago(last["updated_at"]) if last.get("updated_at") else "?"
        label = _STATUS_LABELS.get(last["status"], last["status"])
        mark = _MARK.get(label, "·")
        topic = last["topic"] or last["id"]
        lines.append(f"  Last job  [{mark}] {topic[:45]}  ({ago})")

    lines.append("")

    # API keys
    keys = data.get("api_keys", {})
    key_parts = []
    key_parts.append(f"nvidia {'✓' if keys.get('nvidia') else '✗'}")
    key_parts.append(f"stripe {'✓' if keys.get('stripe') else '✗'}")
    key_parts.append(f"telegram {'✓' if keys.get('telegram') else '✗'}")
    lines.append(f"  API keys  {' · '.join(key_parts)}")

    last_ev = data.get("last_event_ts")
    if last_ev:
        lines.append(f"  Activity  last event {_time_ago(float(last_ev))}")

    return "\n".join(lines)


def main():
    import argparse

    p = argparse.ArgumentParser(
        description="Print a live summary of the SOLVENT agent state.",
    )
    p.add_argument("--json", action="store_true", dest="as_json", help="output as JSON")
    p.add_argument(
        "--watch",
        nargs="?",
        const=5,
        type=float,
        metavar="SECONDS",
        help="refresh every N seconds (default 5)",
    )
    args = p.parse_args()

    def _once():
        data = gather()
        if args.as_json:
            print(json.dumps(data, indent=2, default=str))
        else:
            print(format_status(data))

    if args.watch:
        try:
            while True:
                if not args.as_json:
                    # Clear screen for watch mode
                    print("\033[2J\033[H", end="")
                _once()
                time.sleep(args.watch)
        except KeyboardInterrupt:
            pass
    else:
        _once()


if __name__ == "__main__":
    main()
