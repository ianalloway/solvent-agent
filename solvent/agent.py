"""
agent.py — the SOLVENT orchestrator.

For each inbound job the agent runs an idempotent stage machine:
  1. QUOTES through the margin gate
  2. EARNS via Stripe Checkout (webhook-first; sync confirm in demo mode)
  3. FULFILS via bounded Nemotron tool-calling
  4. DELIVERS hosted brief + optional email
  5. SPENDS on vendors (guardrail-screened)
  6. BOOKS P&L into the treasury
"""

from __future__ import annotations

import os
import time
from typing import Callable

from .guardrails import Guardrails
from .pricing import PricingPolicy
from .stages import StageRunner, validate_and_coerce_job
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
        self.pricing = PricingPolicy()
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
        """Validate and persist a job for async worker processing."""
        job, err = validate_and_coerce_job(job, self.t)
        if err:
            job_id = job.get("id", "unknown") if job else "unknown"
            return self._emit(stage="declined", job_id=job_id, reason=err)
        q = self._runner._stage_quote(job)
        if q.get("stage") == "declined" or not q.get("accept"):
            return q
        return self._runner._stage_checkout(job, q)

    def run(self, jobs: list[dict]) -> dict:
        for job in jobs:
            self.handle_job(job)
        return self.t.snapshot()
