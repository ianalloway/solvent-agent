"""
agent.py — the SOLVENT orchestrator (the Hermes/Nous agent loop).

For each inbound job the agent:
  1. QUOTES it through the margin gate (refuses unprofitable work).
  2. EARNS — issues a Stripe payment link and collects payment up front.
  3. FULFILS — runs the analyst (Nemotron) to produce the deliverable.
  4. SPENDS — pays each vendor it consumed, every payment screened by guardrails.
  5. BOOKS the job's P&L into the treasury.

The whole thing is designed so that revenue is collected before cost is incurred
and no individual payment can violate policy — an autonomous business that is
safe by construction and profitable by rule.
"""

from __future__ import annotations

import time
from .treasury import Treasury, fmt
from .guardrails import Guardrails, GuardrailError
from .pricing import quote, PricingPolicy
from .stripe_client import StripeClient
from .security import sanitise_job, SOLVENTSecurityError
from . import service


class Solvent:
    def __init__(self, seed_cents: int = 10_000, fresh: bool = True, on_event: callable | None = None):
        self.t = Treasury()
        if fresh:
            self.t.reset()
            self.t.seed(seed_cents)
        self.guard = Guardrails(self.t)
        self.stripe = StripeClient()
        self.pricing = PricingPolicy()
        self.log: list[dict] = []
        self.on_event = on_event

    def _emit(self, **event):
        if "ts" not in event:
            event["ts"] = time.time()
        self.log.append(event)
        if self.on_event:
            self.on_event(event)
        
        # Real-time dashboard rendering
        try:
            from . import dashboard
            dashboard.render(self.t.snapshot(), self.log)
        except Exception:
            pass
            
        return event

    def handle_job(self, job: dict) -> dict:
        # 0. INPUT VALIDATION & COERCION -------------------------------
        if not isinstance(job, dict):
            return self._emit(stage="declined", job_id="unknown", reason="job must be a dictionary")

        job_id = job.get("id")
        if not job_id or not isinstance(job_id, str):
            return self._emit(stage="declined", job_id="unknown", reason="missing or invalid job ID")

        # SECURITY — sanitise all LLM-facing and external fields before
        # any downstream system sees them (prompt injection, email injection,
        # path traversal via job ID handled inside sanitise_job / service.fulfill)
        try:
            job = dict(job)  # work on a shallow copy; never mutate caller's dict
            sanitise_job(job)
        except SOLVENTSecurityError as exc:
            reason = f"security violation: {exc}"
            self.t.upsert_job(job_id, "failed", error_reason=reason)
            return self._emit(stage="declined", job_id=job_id, reason=reason)

        # Initial insert in jobs queue
        topic = job.get("topic")
        budget_cents = job.get("budget_cents")
        customer_email = job.get("customer_email")
        est_tokens = job.get("est_tokens")
        market_data_calls = job.get("market_data_calls")
        web_search_calls = job.get("web_search_calls")

        self.t.upsert_job(
            job_id,
            "pending_quote",
            topic=topic,
            budget_cents=budget_cents,
            customer_email=customer_email,
            est_tokens=est_tokens,
            market_data_calls=market_data_calls,
            web_search_calls=web_search_calls
        )

        if not topic or not isinstance(topic, str) or not topic.strip():
            reason = "missing or blank topic"
            self.t.upsert_job(job_id, "failed", error_reason=reason)
            return self._emit(stage="declined", job_id=job_id, reason=reason)

        # Coerce and validate budget
        if budget_cents is None:
            reason = "missing budget_cents"
            self.t.upsert_job(job_id, "failed", error_reason=reason)
            return self._emit(stage="declined", job_id=job_id, reason=reason)
        try:
            budget_cents = int(float(budget_cents))
            if budget_cents < 0:
                reason = "budget cannot be negative"
                self.t.upsert_job(job_id, "failed", error_reason=reason)
                return self._emit(stage="declined", job_id=job_id, reason=reason)
            # Cap maximum budget at $10,000 to prevent overflow/abuse
            if budget_cents > 1_000_000:
                reason = "budget exceeds maximum limit"
                self.t.upsert_job(job_id, "failed", error_reason=reason)
                return self._emit(stage="declined", job_id=job_id, reason=reason)
            job["budget_cents"] = budget_cents
        except (ValueError, TypeError):
            reason = "budget_cents must be a valid numeric value"
            self.t.upsert_job(job_id, "failed", error_reason=reason)
            return self._emit(stage="declined", job_id=job_id, reason=reason)

        # Coerce and validate parameters
        for param, default_val in [("est_tokens", 8_000), ("market_data_calls", 2), ("web_search_calls", 6)]:
            val = job.get(param)
            if val is None:
                val = default_val
            try:
                val = int(float(val))
                if val < 0:
                    reason = f"{param} cannot be negative"
                    self.t.upsert_job(job_id, "failed", error_reason=reason)
                    return self._emit(stage="declined", job_id=job_id, reason=reason)
                # Cap inputs to avoid resource exhaustion/unbounded loops
                if param == "est_tokens" and val > 100_000:
                    reason = "est_tokens exceeds maximum limit"
                    self.t.upsert_job(job_id, "failed", error_reason=reason)
                    return self._emit(stage="declined", job_id=job_id, reason=reason)
                if param in ("market_data_calls", "web_search_calls") and val > 50:
                    reason = f"{param} exceeds maximum limit"
                    self.t.upsert_job(job_id, "failed", error_reason=reason)
                    return self._emit(stage="declined", job_id=job_id, reason=reason)
                job[param] = val
            except (ValueError, TypeError):
                reason = f"{param} must be a valid numeric value"
                self.t.upsert_job(job_id, "failed", error_reason=reason)
                return self._emit(stage="declined", job_id=job_id, reason=reason)

        # Update job queue entry with fully coerced and validated values
        self.t.upsert_job(
            job_id,
            "pending_quote",
            topic=job["topic"],
            budget_cents=job["budget_cents"],
            customer_email=job.get("customer_email", "client@example.com"),
            est_tokens=job["est_tokens"],
            market_data_calls=job["market_data_calls"],
            web_search_calls=job["web_search_calls"]
        )

        # 1. QUOTE -----------------------------------------------------
        q = quote(job, self.pricing)
        self._emit(stage="quote", job_id=job["id"], title=job["topic"],
                   price=q.price_cents, est_cost=q.est_cost_cents,
                   margin_pct=q.margin_pct, accept=q.accept, reason=q.reason)
        if not q.accept:
            self.t.upsert_job(job_id, "failed", error_reason=q.reason)
            return self._emit(stage="declined", job_id=job["id"], reason=q.reason)

        # 1.5. PRE-SCREEN RESOURCE EXHAUSTION --------------------------
        with self.t.lock():
            # Check if estimated job fulfillment cost would exceed remaining daily budget
            if self.guard._spent_last_24h() + q.est_cost_cents > self.guard.policy.daily_budget_cents:
                reason = "fulfilment cost would exceed 24h spend budget limit"
                self.t.upsert_job(job_id, "failed", error_reason=reason)
                return self._emit(stage="declined", job_id=job["id"], reason=reason)

            # Check if expected balance after fulfillment would drop below the cash reserve
            projected_balance = self.t.balance_cents() + q.price_cents - q.est_cost_cents
            if projected_balance < self.guard.policy.min_reserve_cents:
                reason = "fulfilment would breach minimum cash reserve limit"
                self.t.upsert_job(job_id, "failed", error_reason=reason)
                return self._emit(stage="declined", job_id=job["id"], reason=reason)

        # 2. EARN ------------------------------------------------------
        self.t.upsert_job(job_id, "awaiting_payment")
        link = self.stripe.create_payment_link(
            name=f"Research brief: {job['topic'][:60]}",
            amount_cents=q.price_cents,
            customer_email=job.get("customer_email", "client@example.com"),
        )
        self._emit(stage="invoice", job_id=job["id"], url=link["url"],
                   amount=q.price_cents, simulated=link["simulated"])
        payment = self.stripe.confirm_payment(link)
        if not payment.get("paid"):
            reason = payment.get("reason", "payment not received")
            self.t.upsert_job(job_id, "awaiting_payment", error_reason=reason)
            return self._emit(
                stage="payment_pending",
                job_id=job["id"],
                reason=reason,
                url=link["url"],
                payment_link_id=link["id"],
            )
        self.t.earn(
            payment["amount_cents"],
            f"Payment for {job['id']}",
            job_id=job["id"],
            stripe_ref=payment["stripe_ref"],
            stripe_session_id=payment.get("checkout_session_id"),
            stripe_link_id=payment.get("payment_link_id"),
        )
        self._emit(
            stage="paid",
            job_id=job["id"],
            amount=payment["amount_cents"],
            payment_intent=payment["stripe_ref"],
            checkout_session=payment.get("checkout_session_id"),
            payment_link=payment.get("payment_link_id"),
        )

        # 3. FULFIL & 4. SPEND (inside try-except to trigger refund if blocked/failed)
        self.t.upsert_job(job_id, "in_progress")
        payment_intent_id = payment["stripe_ref"]
        paid_amount = payment["amount_cents"]
        
        try:
            # 3. FULFIL ------------------------------------------------
            result = service.fulfill(job)
            self._emit(stage="fulfilled", job_id=job["id"],
                       deliverable=result["deliverable_path"], tokens=result["tokens"])

            # 4. SPEND (each payment screened by guardrails under lock) ----
            job_margin = q.margin_cents
            for vendor, amount, memo in result["resources_used"]:
                if amount <= 0:
                    continue
                with self.t.lock():
                    if not self.guard.approve(amount, vendor, projected_job_margin_cents=job_margin):
                        self._emit(stage="spend_blocked", job_id=job["id"], vendor=vendor,
                                   amount=amount, memo=memo)
                        raise GuardrailError(f"spend to {vendor} of {amount}c rejected by guardrails")
                    pay = self.stripe.pay_vendor(vendor, amount, memo)
                    self.t.spend(amount, memo, job_id=job["id"], vendor=vendor, stripe_ref=pay["id"])
                    self._emit(stage="spend", job_id=job["id"], vendor=vendor, amount=amount, memo=memo)

            # 5. BOOK P&L ----------------------------------------------
            self.t.upsert_job(job_id, "completed")
            pnl = self.t.job_pnl_cents(job["id"])
            return self._emit(stage="booked", job_id=job["id"], job_pnl=pnl,
                              balance=self.t.balance_cents(), status="completed")

        except Exception as e:
            reason = str(e)
            refund = self.stripe.refund_payment(payment_intent_id, paid_amount)
            self.t.spend(
                amount_cents=paid_amount,
                memo=f"Refund for job {job_id} due to block/failure: {reason}",
                job_id=job_id,
                stripe_ref=refund["id"]
            )
            self._emit(stage="refunded", job_id=job_id, amount=paid_amount, reason=reason)
            self.t.upsert_job(job_id, "failed", error_reason=reason)
            
            pnl = self.t.job_pnl_cents(job_id)
            return self._emit(stage="booked", job_id=job_id, job_pnl=pnl,
                              balance=self.t.balance_cents(), status="failed")

    def run(self, jobs: list[dict]) -> dict:
        for job in jobs:
            self.handle_job(job)
        return self.t.snapshot()
