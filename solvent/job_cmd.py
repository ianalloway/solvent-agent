"""
CLI for inspecting and managing SOLVENT jobs.

Usage:
    solvent jobs                  list recent jobs (last 20)
    solvent jobs list             same as above
    solvent jobs list --status=running
    solvent jobs list --status=failed --limit=50
    solvent jobs show <id>        full detail for one job
    solvent jobs events <id>      event log for one job
    solvent jobs retry <id>       re-run a stuck/failed job
    solvent jobs cancel <id>      mark a job as cancelled
    solvent jobs --json           machine-readable list output
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from typing import Optional


_STATUS_EMOJI = {
    "awaiting_payment": "⏳",
    "paid_pending_fulfill": "💳",
    "in_progress": "▶ ",
    "completed": "✓ ",
    "failed": "✗ ",
    "cancelled": "⊘ ",
}

_ALL_STATUSES = list(_STATUS_EMOJI.keys())


def _fmt_ts(ts) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, TypeError):
        return str(ts)


def _ago(ts) -> str:
    if not ts:
        return ""
    try:
        delta = time.time() - float(ts)
    except (ValueError, TypeError):
        return ""
    if delta < 60:
        return f"{int(delta)}s"
    if delta < 3600:
        return f"{int(delta / 60)}m"
    if delta < 86400:
        return f"{int(delta / 3600)}h"
    return f"{int(delta / 86400)}d"


def _fmt_cents(cents) -> str:
    if cents is None:
        return "—"
    return f"${int(cents) / 100:,.2f}"


def _status_label(status: str) -> str:
    emoji = _STATUS_EMOJI.get(status, "? ")
    return f"{emoji} {status}"


def _col(s: str, width: int) -> str:
    s = str(s or "")
    if len(s) > width:
        return s[: width - 1] + "…"
    return s.ljust(width)


def cmd_list(
    treasury,
    *,
    status_filter: Optional[str] = None,
    limit: int = 20,
    as_json: bool = False,
):
    jobs = treasury.list_jobs()
    if status_filter:
        jobs = [j for j in jobs if j.get("status") == status_filter]
    jobs = jobs[-limit:]

    if as_json:
        print(json.dumps(jobs, indent=2, default=str))
        return

    if not jobs:
        msg = "No jobs"
        if status_filter:
            msg += f" with status '{status_filter}'"
        print(msg)
        return

    header = f"{'ID':18} {'STATUS':22} {'TOPIC':42} {'BUDGET':8} {'AGO':5}"
    print(header)
    print("─" * len(header))

    for job in reversed(jobs):
        job_id = job.get("id", "")[:17]
        status = job.get("status", "")
        emoji = _STATUS_EMOJI.get(status, "? ")
        topic = (job.get("topic") or "")[:41]
        budget = _fmt_cents(job.get("budget_cents"))
        ago = _ago(job.get("updated_at") or job.get("created_at"))
        print(
            f"{_col(job_id, 18)} {emoji}{_col(status, 20)} "
            f"{_col(topic, 42)} {_col(budget, 8)} {ago}"
        )


def cmd_show(treasury, job_id: str, *, as_json: bool = False):
    job = treasury.get_job(job_id)
    if not job:
        print(f"Job not found: {job_id}", file=sys.stderr)
        raise SystemExit(1)

    try:
        pnl = treasury.job_pnl_cents(job_id)
    except Exception:
        pnl = None

    try:
        metrics = treasury.get_metrics(job_id) or {}
    except Exception:
        metrics = {}

    if as_json:
        out = dict(job)
        out["pnl_cents"] = pnl
        out["metrics"] = metrics
        print(json.dumps(out, indent=2, default=str))
        return

    status = job.get("status", "")
    print(f"Job:      {job_id}")
    print(f"Status:   {_status_label(status)}")
    print(f"Topic:    {job.get('topic') or '—'}")
    print(f"Customer: {job.get('customer_email') or '—'}")
    print(f"Budget:   {_fmt_cents(job.get('budget_cents'))}")
    if pnl is not None:
        print(f"P&L:      {_fmt_cents(pnl)}")
    print(f"Created:  {_fmt_ts(job.get('created_at'))}")
    print(f"Updated:  {_fmt_ts(job.get('updated_at'))}")

    if metrics:
        print("\nMetrics:")
        for key, value in metrics.items():
            if key == "job_id":
                continue
            print(f"  {key}: {value}")


def cmd_events(treasury, job_id: str, *, limit: int = 20, as_json: bool = False):
    job = treasury.get_job(job_id)
    if not job:
        print(f"Job not found: {job_id}", file=sys.stderr)
        raise SystemExit(1)

    events = treasury.list_events(job_id=job_id, limit=limit)

    if as_json:
        print(json.dumps(events, indent=2, default=str))
        return

    if not events:
        print(f"No events for job {job_id}")
        return

    print(f"Events for {job_id}:")
    print(f"  {'TIME':17} {'STAGE':20}")
    print(f"  {'─' * 17} {'─' * 20}")
    for event in events:
        ts = _fmt_ts(event.get("ts"))
        stage = event.get("stage", "")
        print(f"  {ts:17} {stage}")


def cmd_retry(treasury, job_id: str, *, runner=None):
    """Retry through the same StageRunner used by the worker and agent."""
    if runner is None:
        from .guardrails import Guardrails
        from .stages import StageRunner
        from .stripe_client import StripeClient

        runner = StageRunner(treasury, Guardrails(treasury), StripeClient())

    try:
        result = runner.retry_job(job_id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

    print(json.dumps(result, indent=2, default=str))
    return result


def cmd_cancel(treasury, job_id: str):
    job = treasury.get_job(job_id)
    if not job:
        print(f"Job not found: {job_id}", file=sys.stderr)
        raise SystemExit(1)

    current = job.get("status", "")
    if current in ("completed", "cancelled"):
        print(f"Job {job_id} is already {current} — nothing to do.")
        return

    treasury.upsert_job(job_id, "cancelled")
    treasury.record_event(job_id, "cancelled", {"reason": "manual-cancel"})
    print(f"Job {job_id} cancelled (was: {current})")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="solvent jobs",
        description="Inspect and manage SOLVENT jobs.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="output as JSON")

    sub = parser.add_subparsers(dest="cmd")

    list_parser = sub.add_parser("list", help="list recent jobs")
    list_parser.add_argument("--status", choices=_ALL_STATUSES, help="filter by status")
    list_parser.add_argument("--limit", type=int, default=20)

    show_parser = sub.add_parser("show", help="full detail for one job")
    show_parser.add_argument("job_id")

    events_parser = sub.add_parser("events", help="event log for one job")
    events_parser.add_argument("job_id")
    events_parser.add_argument("--limit", type=int, default=20)

    retry_parser = sub.add_parser("retry", help="retry a failed or awaiting-payment job")
    retry_parser.add_argument("job_id")

    cancel_parser = sub.add_parser("cancel", help="cancel a job")
    cancel_parser.add_argument("job_id")

    args = parser.parse_args()
    command = args.cmd or "list"

    from .treasury import Treasury

    treasury = Treasury()

    if command == "list":
        cmd_list(
            treasury,
            status_filter=getattr(args, "status", None),
            limit=getattr(args, "limit", 20),
            as_json=args.as_json,
        )
    elif command == "show":
        cmd_show(treasury, args.job_id, as_json=args.as_json)
    elif command == "events":
        cmd_events(treasury, args.job_id, limit=args.limit, as_json=args.as_json)
    elif command == "retry":
        cmd_retry(treasury, args.job_id)
    elif command == "cancel":
        cmd_cancel(treasury, args.job_id)


if __name__ == "__main__":
    main()
