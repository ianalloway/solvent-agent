"""
agent.py — the SOLVENT orchestrator.

For each inbound job the agent runs an idempotent stage machine:
  1. QUOTES through the margin gate
  2. EARNS via Stripe Checkout (webhook-first; sync confirm in demo mode)
  3. FULFILS via bounded Nemotron tool-calling
  4. DELIVERS hosted brief + optional email
  5. SPENDS on vendors (guardrail-screened)
  6. BOOKS P&L into the treasury

Each job's realized cost is booked back into the margin gate: the next quote
is calibrated on what the last ones actually cost (see `calibration.py`).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from .calibration import calibration_factor
from .delivery import is_safe_job_id
from .guardrails import Guardrails
from .pricing import PricingPolicy
from .stages import StageRunner, _job_id_of, validate_and_coerce_job
from .stripe_client import StripeClient
from .treasury import Treasury


class Solvent:
    def __init__(
        self,
        seed_cents: int = 10_000,
        fresh: bool = True,
        on_event: Callable | None = None,
        *,
        sync_payment: bool | None = None,
    ):
        self.t = Treasury()
        if fresh:
            self.t.reset()
            self.t.seed(seed_cents)
        self.guard = Guardrails(self.t)
        self.stripe = StripeClient()
        # Quotes are marked up when realized COGS have been running above the
        # static cost model; a fresh treasury has no history, so this is 1.0.
        self.pricing = PricingPolicy(cost_calibration=calibration_factor(self.t))
        self.log: list[dict] = []
        self.on_event = on_event
        if sync_payment is None:
            async_flag = os.environ.get("SOLVENT_ASYNC", "").strip()
            sync_payment = async_flag not in ("1", "true", "yes")
        self._runner = StageRunner(
            self.t,
            self.guard,
            self.stripe,
            self.pricing,
            on_event=self._capture_event,
            sync_payment=sync_payment,
        )

    def _capture_event(self, event: dict):
        self.log.append(event)
        if self.on_event:
            self.on_event(event)

    def _emit(self, **event):
        if "ts" not in event:
            event["ts"] = time.time()
        self.log.append(event)
        if self.on_event:
            self.on_event(event)
        return event

    def handle_job(self, job: dict) -> dict:
        return self._runner.run_job(job)

    def advance_job(self, job_id: str) -> dict:
        return self._runner.advance_job(job_id)

    def enqueue_job(self, job: dict) -> dict:
        """Validate and persist a new job for async worker processing.

        The job id must be unused: the row is claimed with an atomic insert
        first, so a submission can never overwrite or restart an existing job.
        """
        job_id = job.get("id") if isinstance(job, dict) else None
        if not (isinstance(job_id, str) and is_safe_job_id(job_id)):
            job_id = None
        if job_id:
            if not self.t.insert_job(job_id, "received"):
                return {"stage": "declined", "job_id": job_id, "reason": "job id already exists"}
        validated, err = validate_and_coerce_job(job, self.t)
        if err:
            if job_id:
                row = self.t.get_job(job_id)
                if row and row.get("status") == "received":
                    self.t.upsert_job(job_id, "failed", error_reason=err)
            return self._emit(stage="declined", job_id=_job_id_of(job, validated), reason=err)
        assert validated is not None
        q = self._runner._stage_quote(validated)
        if q.get("stage") == "declined" or not q.get("accept"):
            return q
        return self._runner._stage_checkout(validated, q)

    def run(self, jobs: list[dict]) -> dict:
        for job in jobs:
            self.handle_job(job)
        return self.t.snapshot()
