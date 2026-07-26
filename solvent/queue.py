"""SQLite-backed job queue helpers."""

from __future__ import annotations

from .treasury import Treasury

WORKER_STATUSES = (
    "awaiting_payment",
    "in_progress",
    "paid_pending_fulfill",
    "pending_quote",
)


def list_claimable(treasury: Treasury) -> list[dict]:
    return treasury.list_jobs_by_status(list(WORKER_STATUSES))


def resume_incomplete_jobs(treasury: Treasury) -> list[str]:
    """Find jobs with revenue but no completed fulfill stage."""
    resumed: list[str] = []
    for job in treasury.list_jobs():
        jid = job["id"]
        status = job.get("status", "")
        if status in ("completed", "failed"):
            continue
        if treasury.job_has_revenue(jid) and not treasury.stage_completed(
            jid, "fulfill"
        ):
            treasury.upsert_job(jid, "paid_pending_fulfill")
            resumed.append(jid)
        elif (
            status == "in_progress"
            and not treasury.stage_completed(jid, "fulfill")
            or status == "awaiting_payment"
            and treasury.get_checkout(jid)
        ):
            resumed.append(jid)
    return resumed
