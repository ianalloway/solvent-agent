"""Background worker for async SOLVENT job processing."""

from __future__ import annotations

import time

from .agent import Solvent
from .backlog import prioritise
from .money import dollars_to_cents
from .checkout import sweep as sweep_checkouts
from .queue import list_claimable, resume_incomplete_jobs


def run_worker(
    *,
    once: bool = False,
    poll_interval: float = 2.0,
    seed_cents: int = 10_000,
    fresh: bool = False,
) -> None:
    agent = Solvent(seed_cents=seed_cents, fresh=fresh, sync_payment=False)
    resumed = resume_incomplete_jobs(agent.t)
    for job_id in resumed:
        agent.advance_job(job_id)

    while True:
        # Chase or close unpaid links before picking up work: an expired job
        # drops out of the queue instead of being polled forever.
        try:
            sweep_checkouts(agent.t, stripe=agent.stripe)
        except Exception:
            pass

        # Work the backlog in business order — paid jobs first, then best
        # return on capital — and hold back work the treasury cannot fund yet.
        jobs = prioritise(list_claimable(agent.t), agent.t, agent.guard)
        for job in jobs:
            job_id = job["id"]
            if not agent.t.claim_job(job_id):
                continue
            try:
                agent.advance_job(job_id)
            finally:
                agent.t.release_job(job_id)
        if once:
            break
        time.sleep(poll_interval)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="SOLVENT async job worker")
    parser.add_argument("--once", action="store_true", help="process one pass then exit")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--seed", type=float, default=100.0)
    parser.add_argument("--keep-balance", action="store_true")
    args = parser.parse_args()
    run_worker(
        once=args.once,
        poll_interval=args.poll_interval,
        seed_cents=dollars_to_cents(args.seed),
        fresh=not args.keep_balance,
    )


if __name__ == "__main__":
    main()
