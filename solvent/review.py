"""
review.py — the queue of work only a human can wave through.

The intake screen refuses an order above the automatic ceiling with "needs an
operator", which was honest and incomplete: there was no way for the operator
to actually approve one. A $5,000 commission arrived, was declined, and that
was the end of it.

This is the other half. Jobs the screen turned away sit in a review queue with
the rule that caught them; the operator approves (the job goes back into the
pipeline carrying a one-off exemption) or rejects it (the job is cancelled with
their reason on it). Both decisions are recorded as events, because "who let
this $5,000 order through" is a question that gets asked later.

Approval exempts one job, once. It does not change the policy — that stays a
deliberate edit to `.solvent/intake_policy.json`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Callable

from .intake import REASON_PREFIX
from .treasury import Treasury, fmt

#: Marker carried on an approved job's payload; `intake.screen_job` honours it.
APPROVAL_FLAG = "intake_approved"


def _rule_of(reason: str) -> str:
    """The intake rule named in a decline reason."""
    if ":" not in reason:
        return ""
    tail = reason.split(":", 1)[1]
    return tail.split("—")[0].strip()


def job_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a submittable job from its treasury row."""
    payload = row.get("job_payload_json")
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            pass
    elif isinstance(payload, dict):
        return dict(payload)
    return {
        "id": row.get("id"),
        "topic": row.get("topic"),
        "budget_cents": row.get("budget_cents"),
        "customer_email": row.get("customer_email"),
        "est_tokens": row.get("est_tokens"),
        "market_data_calls": row.get("market_data_calls"),
        "web_search_calls": row.get("web_search_calls"),
    }


def pending(treasury: Treasury | None = None) -> list[dict[str, Any]]:
    """Jobs waiting on an operator, newest first."""
    t = treasury or Treasury()
    rows = [
        {
            "job_id": job.get("id"),
            "topic": job.get("topic") or "",
            "customer_email": job.get("customer_email") or "",
            "budget_cents": job.get("budget_cents") or 0,
            "rule": _rule_of(job.get("error_reason") or ""),
            "reason": job.get("error_reason") or "",
            "ts": job.get("updated_at") or job.get("created_at"),
        }
        for job in t.list_jobs()
        if job.get("status") == "failed"
        and str(job.get("error_reason") or "").startswith(f"{REASON_PREFIX}:")
    ]
    rows.sort(key=lambda row: row["ts"] or 0, reverse=True)
    return rows


def approve(
    treasury: Treasury | None = None,
    job_id: str = "",
    *,
    runner: Callable[[dict], Any] | None = None,
    operator: str = "operator",
) -> dict[str, Any]:
    """Wave one job through the intake screen and put it back in the pipeline.

    The exemption is written onto that job's payload, so it applies to this job
    and nothing else. Without a `runner` the job is left queued for the worker.
    """
    t = treasury or Treasury()
    row = t.get_job(job_id)
    if not row:
        raise ValueError(f"job {job_id!r} not found")
    previous = row.get("error_reason") or ""
    if not previous.startswith(f"{REASON_PREFIX}:"):
        raise ValueError(f"job {job_id!r} is not waiting on review (status {row.get('status')!r})")

    job = job_payload(row)
    job[APPROVAL_FLAG] = True
    t.upsert_job(
        job_id,
        "pending_quote",
        job_payload_json=job,
        error_reason="",
        current_stage="approved",
    )
    t.record_event(
        job_id,
        "intake_approved",
        {
            "stage": "intake_approved",
            "job_id": job_id,
            "operator": operator,
            "overrode": _rule_of(previous),
            "reason": previous,
            "ts": time.time(),
        },
    )

    result = runner(job) if runner else None
    return {
        "job_id": job_id,
        "overrode": _rule_of(previous),
        "queued": runner is None,
        "result": result,
    }


def reject(
    treasury: Treasury | None = None,
    job_id: str = "",
    *,
    reason: str = "",
    operator: str = "operator",
) -> dict[str, Any]:
    """Decline a job for good, with the operator's reason recorded on it."""
    t = treasury or Treasury()
    row = t.get_job(job_id)
    if not row:
        raise ValueError(f"job {job_id!r} not found")
    note = reason.strip() or "no reason given"
    t.upsert_job(
        job_id,
        "cancelled",
        error_reason=f"rejected by {operator}: {note}",
        current_stage="rejected",
    )
    t.record_event(
        job_id,
        "intake_rejected",
        {
            "stage": "intake_rejected",
            "job_id": job_id,
            "operator": operator,
            "reason": note,
            "ts": time.time(),
        },
    )
    return {"job_id": job_id, "reason": note}


def format_queue(rows: list[dict[str, Any]]) -> str:
    """Render the review queue for a terminal."""
    lines = ["", "  REVIEW QUEUE", f"  {'─' * 72}"]
    if not rows:
        lines += ["  Nothing waiting on an operator.", ""]
        return "\n".join(lines)
    lines.append(f"  {'JOB':<14}{'BUDGET':>11}  {'RULE':<20}CUSTOMER")
    for row in rows:
        lines.append(
            f"  {str(row['job_id'])[:13]:<14}{fmt(row['budget_cents']):>11}  "
            f"{row['rule'][:19]:<20}{row['customer_email'][:26]}"
        )
    waiting = sum(row["budget_cents"] for row in rows)
    lines += [
        "",
        f"  {len(rows)} job(s) held, {fmt(waiting)} of orders waiting on a decision",
        "  Approve with `solvent review approve <job>`, decline with "
        "`solvent review reject <job> --reason ...`",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="solvent review",
        description="Approve or reject the jobs the intake screen held for a human.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="output as JSON")
    sub = parser.add_subparsers(dest="cmd")

    approve_p = sub.add_parser("approve", help="wave a held job through intake")
    approve_p.add_argument("job_id")
    approve_p.add_argument(
        "--run",
        action="store_true",
        help="run the job now instead of leaving it for the worker",
    )

    reject_p = sub.add_parser("reject", help="decline a held job for good")
    reject_p.add_argument("job_id")
    reject_p.add_argument("--reason", default="", help="why it was declined")

    sub.add_parser("list", help="show the queue (default)")

    args = parser.parse_args()
    treasury = Treasury()
    command = args.cmd or "list"

    try:
        if command == "approve":
            runner = None
            if args.run:
                from .agent import Solvent

                agent = Solvent(fresh=False)
                runner = agent.handle_job
            result = approve(treasury, args.job_id, runner=runner)
            if args.as_json:
                print(json.dumps(result, indent=2, default=str))
            else:
                where = "queued for the worker" if result["queued"] else "run now"
                print(
                    f"\n  Approved {result['job_id']} (overrode {result['overrode']}) — {where}.\n"
                )
            return

        if command == "reject":
            result = reject(treasury, args.job_id, reason=args.reason)
            if args.as_json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"\n  Rejected {result['job_id']}: {result['reason']}\n")
            return
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    rows = pending(treasury)
    if args.as_json:
        print(json.dumps(rows, indent=2, default=str))
    else:
        print(format_queue(rows))


if __name__ == "__main__":
    main()
