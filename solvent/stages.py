"""Idempotent job stage handlers for the SOLVENT money loop."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict

from .treasury import Treasury
from .guardrails import Guardrails, GuardrailError
from .pricing import quote, PricingPolicy
from .stripe_client import StripeClient
from .security import sanitise_job, SOLVENTSecurityError
from . import service, delivery, tools
from .observability import log_event


def _base_url() -> str:
    return os.environ.get("SOLVENT_BASE_URL", "http://127.0.0.1:8787").rstrip("/")


def validate_and_coerce_job(job: dict, treasury: Treasury) -> tuple[dict | None, str | None]:
    """Return (coerced_job, error_reason)."""
    if not isinstance(job, dict):
        return None, "job must be a dictionary"
    job_id = job.get("id")
    if not job_id or not isinstance(job_id, str):
        return None, "missing or invalid job ID"
    try:
        job = dict(job)
        sanitise_job(job)
    except SOLVENTSecurityError as exc:
        treasury.upsert_job(job_id, "failed", error_reason=str(exc))
        return None, f"security violation: {exc}"

    topic = job.get("topic")
    if not topic or not isinstance(topic, str) or not topic.strip():
        treasury.upsert_job(job_id, "failed", error_reason="missing or blank topic")
        return None, "missing or blank topic"

    budget_cents = job.get("budget_cents")
    if budget_cents is None:
        treasury.upsert_job(job_id, "failed", error_reason="missing budget_cents")
        return None, "missing budget_cents"
    try:
        budget_cents = int(float(budget_cents))
        if budget_cents < 0 or budget_cents > 1_000_000:
            treasury.upsert_job(job_id, "failed", error_reason="invalid budget")
            return None, "invalid budget"
        job["budget_cents"] = budget_cents
    except (ValueError, TypeError):
        treasury.upsert_job(job_id, "failed", error_reason="budget_cents must be numeric")
        return None, "budget_cents must be a valid numeric value"

    for param, default_val in [("est_tokens", 8_000), ("market_data_calls", 2), ("web_search_calls", 6)]:
        val = job.get(param)
        if val is None:
            val = default_val
        try:
            val = int(float(val))
            if val < 0:
                return None, f"{param} cannot be negative"
            if param == "est_tokens" and val > 100_000:
                return None, "est_tokens exceeds maximum limit"
            if param in ("market_data_calls", "web_search_calls") and val > 50:
                return None, f"{param} exceeds maximum limit"
            job[param] = val
        except (ValueError, TypeError):
            return None, f"{param} must be a valid numeric value"

    job.setdefault("customer_email", "client@example.com")
    treasury.upsert_job(
        job_id,
        "pending_quote",
        topic=job["topic"],
        budget_cents=job["budget_cents"],
        customer_email=job["customer_email"],
        est_tokens=job["est_tokens"],
        market_data_calls=job["market_data_calls"],
        web_search_calls=job["web_search_calls"],
        job_payload_json=job,
    )
    return job, None


class StageRunner:
    """Runs idempotent stages for a single job."""

    def __init__(
        self,
        treasury: Treasury,
        guard: Guardrails,
        stripe: StripeClient,
        pricing: PricingPolicy | None = None,
        on_event: callable | None = None,
        *,
        sync_payment: bool = True,
    ):
        self.t = treasury
        self.guard = guard
        self.stripe = stripe
        self.pricing = pricing or PricingPolicy()
        self.on_event = on_event
        self.sync_payment = sync_payment

    def _emit(self, **event):
        if "ts" not in event:
            event["ts"] = time.time()
        if self.on_event:
            self.on_event(event)
        try:
            from .gateway import handle_job_event
            handle_job_event(event)
        except Exception:
            pass
        log_event(
            self.t,
            job_id=event.get("job_id", ""),
            stage=event.get("stage", ""),
            stripe_ref=event.get("payment_intent") or event.get("stripe_ref"),
            margin_est=event.get("margin_pct"),
            margin_actual=event.get("actual_margin_pct"),
            simulated=event.get("simulated"),
            **{k: v for k, v in event.items() if k not in ("job_id", "stage", "simulated", "payment_intent", "stripe_ref", "margin_pct", "actual_margin_pct")},
        )
        try:
            from . import dashboard
            dashboard.render(self.t.snapshot(), [])
        except Exception:
            pass
        return event

    def run_job(self, job: dict) -> dict:
        job, err = validate_and_coerce_job(job, self.t)
        if err:
            return self._emit(stage="declined", job_id=job.get("id", "unknown") if job else "unknown", reason=err)

        q = self._stage_quote(job)
        if q.get("stage") == "declined" or not q.get("accept"):
            return q

        quote_obj = q.get("_quote") or quote(job, self.pricing)
        checkout = self._stage_checkout(job, q)
        if checkout.get("stage") == "payment_pending":
            return checkout

        paid = self._stage_paid(job, checkout, quote_obj)
        if paid.get("stage") == "payment_pending":
            return paid

        return self._stage_fulfill_and_book(job, quote_obj, paid)

    def advance_job(self, job_id: str) -> dict:
        """Resume a job from its current DB state."""
        row = self.t.get_job(job_id)
        if not row:
            return self._emit(stage="declined", job_id=job_id, reason="job not found")
        payload = row.get("job_payload_json")
        if isinstance(payload, str):
            job = json.loads(payload)
        else:
            job = {
                "id": job_id,
                "topic": row.get("topic"),
                "budget_cents": row.get("budget_cents"),
                "customer_email": row.get("customer_email"),
                "est_tokens": row.get("est_tokens"),
                "market_data_calls": row.get("market_data_calls"),
                "web_search_calls": row.get("web_search_calls"),
            }
        status = row.get("status", "")
        if status in ("completed", "failed"):
            return self._emit(stage="booked", job_id=job_id, status=status, job_pnl=self.t.job_pnl_cents(job_id))
        if status == "pending_quote" or not row.get("quote_json"):
            return self.run_job(job)
        q_data = json.loads(row["quote_json"])
        if status == "awaiting_payment":
            checkout = {
                "session_id": row.get("checkout_session_id"),
                "url": row.get("checkout_url"),
                "amount_cents": q_data.get("price_cents"),
            }
            from .pricing import Quote
            quote_obj = Quote(**{k: q_data[k] for k in q_data if k in Quote.__dataclass_fields__})
            paid = self._stage_paid(job, checkout, quote_obj)
            if paid.get("stage") == "payment_pending":
                return paid
            return self._stage_fulfill_and_book(job, quote_obj, paid)
        if status in ("in_progress", "paid_pending_fulfill"):
            from .pricing import Quote
            quote_obj = Quote(**{k: q_data[k] for k in q_data if k in Quote.__dataclass_fields__})
            paid = {"stripe_ref": None, "amount_cents": q_data.get("price_cents")}
            rev = self.t.entries
            for e in rev:
                if e.job_id == job_id and e.kind == "revenue":
                    paid["stripe_ref"] = e.stripe_ref
                    paid["amount_cents"] = e.amount_cents
                    break
            return self._stage_fulfill_and_book(job, quote_obj, paid)
        return self.run_job(job)

    def _stage_quote(self, job: dict) -> dict:
        job_id = job["id"]
        key = f"quote:{job_id}"
        cached = self.t.get_stage(key)
        if cached and cached["status"] == "completed":
            data = json.loads(cached["result_json"] or "{}")
            return self._emit(stage="quote", job_id=job_id, **data)

        q = quote(job, self.pricing)
        result = {
            "price": q.price_cents,
            "est_cost": q.est_cost_cents,
            "margin_pct": q.margin_pct,
            "accept": q.accept,
            "reason": q.reason,
        }
        self.t.complete_stage(job_id, "quote", key, result, payload={"job_id": job_id})
        self.t.upsert_job(job_id, "pending_quote" if q.accept else "failed", quote_json=json.dumps(asdict(q)))
        self._emit(stage="quote", job_id=job_id, title=job["topic"], **result)
        if not q.accept:
            self.t.upsert_job(job_id, "failed", error_reason=q.reason)
            self.t.upsert_metrics(job_id, decline_reason=q.reason, est_cost_cents=q.est_cost_cents)
            return self._emit(stage="declined", job_id=job_id, reason=q.reason)

        with self.t.lock():
            if self.guard._spent_last_24h() + q.est_cost_cents > self.guard.policy.daily_budget_cents:
                reason = "fulfilment cost would exceed 24h spend budget limit"
                self.t.upsert_job(job_id, "failed", error_reason=reason)
                return self._emit(stage="declined", job_id=job_id, reason=reason)
            projected = self.t.balance_cents() + q.price_cents - q.est_cost_cents
            if projected < self.guard.policy.min_reserve_cents:
                reason = "fulfilment would breach minimum cash reserve limit"
                self.t.upsert_job(job_id, "failed", error_reason=reason)
                return self._emit(stage="declined", job_id=job_id, reason=reason)
        return {**result, "accept": True, "_quote": q}

    def _stage_checkout(self, job: dict, q_result: dict) -> dict:
        job_id = job["id"]
        q = q_result.get("_quote") or quote(job, self.pricing)
        key = f"checkout:{job_id}"
        cached = self.t.get_stage(key)
        if cached and cached["status"] == "completed":
            return json.loads(cached["result_json"] or "{}")

        self.t.upsert_job(job_id, "awaiting_payment", current_stage="checkout")
        base = _base_url()
        success = delivery.hosted_brief_url(base, job_id)
        cancel = f"{base}/jobs/{job_id}?cancelled=1"

        session = self.stripe.create_checkout_session(
            job_id=job_id,
            amount_cents=q.price_cents,
            customer_email=job.get("customer_email", "client@example.com"),
            name=f"Research brief: {job['topic'][:60]}",
            success_url=success + "&paid=1",
            cancel_url=cancel,
        )
        result = {
            "session_id": session["id"],
            "url": session["url"],
            "amount_cents": q.price_cents,
            "simulated": session.get("simulated", False),
        }
        self.t.complete_stage(job_id, "checkout", key, result)
        self.t.upsert_checkout(job_id, session["id"], session["url"], "open")
        self.t.upsert_job(
            job_id,
            "awaiting_payment",
            checkout_session_id=session["id"],
            checkout_url=session["url"],
            current_stage="awaiting_payment",
        )
        self._emit(stage="invoice", job_id=job_id, url=session["url"], amount=q.price_cents, simulated=result["simulated"])
        return result

    def _stage_paid(self, job: dict, checkout: dict, q) -> dict:
        job_id = job["id"]
        session_id = checkout.get("session_id") or checkout.get("id", "")
        key = f"paid:{job_id}:{session_id}"
        cached = self.t.get_stage(key)
        if cached and cached["status"] == "completed":
            return json.loads(cached["result_json"] or "{}")

        if self.t.job_has_revenue(job_id):
            for e in self.t.entries:
                if e.job_id == job_id and e.kind == "revenue":
                    result = {
                        "paid": True,
                        "stripe_ref": e.stripe_ref,
                        "checkout_session_id": e.stripe_session_id,
                        "amount_cents": e.amount_cents,
                    }
                    return result

        link = {
            "id": session_id,
            "url": checkout.get("url"),
            "amount_cents": checkout.get("amount_cents", getattr(q, "price_cents", 0)),
            "simulated": checkout.get("simulated", False),
            "job_id": job_id,
        }
        if self.sync_payment:
            payment = self.stripe.confirm_payment(link, job_id=job_id)
        else:
            payment = self.stripe.get_cached_payment(job_id) or {"paid": False, "reason": "awaiting webhook"}

        if not payment.get("paid"):
            reason = payment.get("reason", "payment not received")
            self.t.upsert_job(job_id, "awaiting_payment", error_reason=reason)
            return self._emit(
                stage="payment_pending",
                job_id=job_id,
                reason=reason,
                url=checkout.get("url"),
            )

        with self.t.lock():
            self.t.earn(
                payment["amount_cents"],
                f"Payment for {job_id}",
                job_id=job_id,
                stripe_ref=payment["stripe_ref"],
                stripe_session_id=payment.get("checkout_session_id"),
                stripe_link_id=payment.get("payment_link_id"),
            )
        result = {
            "paid": True,
            "stripe_ref": payment["stripe_ref"],
            "checkout_session_id": payment.get("checkout_session_id"),
            "amount_cents": payment["amount_cents"],
        }
        self.t.complete_stage(job_id, "paid", key, result)
        self.t.upsert_job(job_id, "paid_pending_fulfill", current_stage="paid")
        self.t.upsert_checkout(job_id, session_id, checkout.get("url", ""), "paid")
        self._emit(
            stage="paid",
            job_id=job_id,
            amount=payment["amount_cents"],
            payment_intent=payment["stripe_ref"],
            checkout_session=payment.get("checkout_session_id"),
        )
        return result

    def _stage_fulfill_and_book(self, job: dict, q, paid: dict) -> dict:
        job_id = job["id"]
        payment_intent_id = paid.get("stripe_ref")
        paid_amount = paid.get("amount_cents", getattr(q, "price_cents", 0))
        self.t.upsert_job(job_id, "in_progress", current_stage="fulfill")

        try:
            fulfill_key = f"fulfill:{job_id}"
            if self.t.get_stage(fulfill_key) and self.t.get_stage(fulfill_key)["status"] == "completed":
                result = json.loads(self.t.get_stage(fulfill_key)["result_json"] or "{}")
            else:
                result = service.fulfill(job)
                self.t.complete_stage(job_id, "fulfill", fulfill_key, {
                    "deliverable_path": result["deliverable_path"],
                    "tokens": result["tokens"],
                })
                _tctx = result.get("tool_ctx")
                if getattr(_tctx, "budget_exhausted", False):
                    self._emit(
                        stage="tool_budget_exhausted", job_id=job_id,
                        tool_calls=_tctx.total_calls, limit=tools.MAX_TOOL_CALLS,
                    )
            self._emit(
                stage="fulfilled",
                job_id=job_id,
                deliverable=result["deliverable_path"],
                tokens=result.get("tokens", 0),
            )

            cogs = service.reconcile_cogs(q, result)
            self.t.upsert_metrics(
                job_id,
                est_cost_cents=cogs["est_cost_cents"],
                actual_cost_cents=cogs["actual_cost_cents"],
                est_margin_pct=cogs["est_margin_pct"],
                actual_margin_pct=cogs["actual_margin_pct"],
                margin_drift_cents=cogs["margin_drift_cents"],
                fulfillment_seconds=cogs["fulfillment_seconds"],
                tool_calls=cogs["tool_calls"],
                refunded=0,
            )
            self._emit(stage="cogs_reconciled", job_id=job_id, **cogs)

            deliver_key = f"deliver:{job_id}"
            if not (self.t.get_stage(deliver_key) and self.t.get_stage(deliver_key)["status"] == "completed"):
                hosted = delivery.hosted_brief_url(_base_url(), job_id)
                from pathlib import Path
                delivery.send_brief_email(
                    job.get("customer_email", "client@example.com"),
                    job_id,
                    Path(result["deliverable_path"]),
                    hosted,
                )
                self.t.complete_stage(job_id, "deliver", deliver_key, {"hosted_url": hosted})
                self.t.upsert_job(job_id, "in_progress", deliverable_url=hosted, current_stage="deliver")
                self._emit(stage="delivered", job_id=job_id, url=hosted)

            job_margin = getattr(q, "margin_cents", q.price_cents - q.est_cost_cents)
            for vendor, amount, memo in result.get("resources_used", []):
                if amount <= 0:
                    continue
                spend_key = f"spend:{job_id}:{vendor}"
                existing = self.t.get_stage(spend_key)
                if existing and existing["status"] == "completed":
                    continue
                with self.t.lock():
                    # Re-check under the lock: a concurrent advancer may have paid
                    # this vendor between the fast-path check above and here.
                    locked = self.t.get_stage(spend_key)
                    if locked and locked["status"] == "completed":
                        continue
                    decision = self.guard.evaluate(amount, vendor, projected_job_margin_cents=job_margin)
                    if not decision.allowed:
                        self.t.upsert_metrics(
                            job_id, block_rule=decision.rule, block_reason=decision.reason
                        )
                        self._emit(
                            stage="spend_blocked", job_id=job_id, vendor=vendor,
                            amount=amount, memo=memo,
                            rule=decision.rule, reason=decision.reason,
                        )
                        raise GuardrailError(
                            f"spend to {vendor} of {amount}c rejected by guardrails: {decision.reason}"
                        )
                    pay = self.stripe.pay_vendor(vendor, amount, memo, job_id=job_id)
                    self.t.spend(amount, memo, job_id=job_id, vendor=vendor, stripe_ref=pay["id"])
                    self.t.complete_stage(job_id, "spend", spend_key, {"vendor": vendor, "amount": amount})
                    self._emit(stage="spend", job_id=job_id, vendor=vendor, amount=amount, memo=memo)

            self.t.upsert_job(job_id, "completed", current_stage="booked")
            pnl = self.t.job_pnl_cents(job_id)
            booked_event = self._emit(
                stage="booked",
                job_id=job_id,
                job_pnl=pnl,
                balance=self.t.balance_cents(),
                status="completed",
                actual_margin_pct=cogs["actual_margin_pct"],
            )
            try:
                from .receipt import build_receipt
                _job_row = self.t.get_job(job_id) or {}
                _full_job = {**_job_row, **job}
                receipt_text = build_receipt(_full_job, pnl, self.t.balance_cents())
                self._emit(stage="receipt_ready", job_id=job_id, receipt=receipt_text)
            except Exception:
                pass
            return booked_event
        except Exception as e:
            reason = str(e)
            refund_key = f"refund:{job_id}:{payment_intent_id}"
            if payment_intent_id and not (
                self.t.get_stage(refund_key) and self.t.get_stage(refund_key)["status"] == "completed"
            ):
                refund = self.stripe.refund_payment(payment_intent_id, paid_amount, job_id=job_id)
                self.t.spend(
                    amount_cents=paid_amount,
                    memo=f"Refund for job {job_id} due to block/failure: {reason}",
                    job_id=job_id,
                    stripe_ref=refund["id"],
                )
                self.t.complete_stage(job_id, "refund", refund_key, {"refund_id": refund["id"]})
            self.t.upsert_metrics(job_id, refunded=1)
            self._emit(stage="refunded", job_id=job_id, amount=paid_amount, reason=reason)
            self.t.upsert_job(job_id, "failed", error_reason=reason, current_stage="failed")
            pnl = self.t.job_pnl_cents(job_id)
            booked_event = self._emit(
                stage="booked",
                job_id=job_id,
                job_pnl=pnl,
                balance=self.t.balance_cents(),
                status="failed",
            )
            try:
                from .receipt import build_refund_receipt
                _job_row = self.t.get_job(job_id) or {}
                _full_job = {**_job_row, **job, "error_reason": reason}
                refund_receipt_text = build_refund_receipt(_full_job, paid_amount)
                self._emit(stage="refund_receipt_ready", job_id=job_id, receipt=refund_receipt_text)
            except Exception:
                pass
            return booked_event

    def handle_webhook_payment(self, payment: dict) -> dict | None:
        """Apply a verified webhook payment and advance the job."""
        job_id = payment.get("job_id")
        if not job_id:
            return None
        session_id = payment.get("checkout_session_id", "")
        key = f"paid:{job_id}:{session_id}"
        if self.t.get_stage(key) and self.t.get_stage(key)["status"] == "completed":
            return self.advance_job(job_id)
        row = self.t.get_job(job_id)
        if not row:
            return None
        return self.advance_job(job_id)

    def retry_job(self, job_id: str) -> dict:
        """Retry a failed or payment_pending job, restarting from the appropriate stage.

        Rules:
        - Only ``failed`` or ``payment_pending`` (awaiting_payment) jobs may be retried.
        - Increments ``retry_count``; raises ValueError if the cap of 3 is exceeded.
        - ``payment_pending`` jobs resume from ``_stage_checkout`` (reuses the existing link).
        - ``failed`` pre-payment jobs restart from the quote stage.
        - ``failed`` post-payment jobs re-run ``_stage_fulfill_and_book`` only (no re-charge).
        """
        MAX_RETRIES = 3

        row = self.t.get_job(job_id)
        if not row:
            raise ValueError(f"Job {job_id!r} not found")

        status = row.get("status", "")
        if status not in ("failed", "awaiting_payment"):
            raise ValueError(
                f"Job {job_id!r} cannot be retried: status is {status!r} "
                "(only 'failed' or 'awaiting_payment'/'payment_pending' jobs are retryable)"
            )

        new_count = self.t.increment_retry_count(job_id)
        if new_count > MAX_RETRIES:
            raise ValueError(
                f"Job {job_id!r} has exceeded the maximum retry limit ({MAX_RETRIES})"
            )

        self._emit(stage="retry_started", job_id=job_id, retry_count=new_count, previous_status=status)

        # Reconstruct the job payload
        payload = row.get("job_payload_json")
        if isinstance(payload, str):
            job = json.loads(payload)
        else:
            job = {
                "id": job_id,
                "topic": row.get("topic"),
                "budget_cents": row.get("budget_cents"),
                "customer_email": row.get("customer_email"),
                "est_tokens": row.get("est_tokens"),
                "market_data_calls": row.get("market_data_calls"),
                "web_search_calls": row.get("web_search_calls"),
            }

        # --- payment_pending: resume from checkout (reuse existing payment link) ---
        if status == "awaiting_payment":
            checkout = {
                "session_id": row.get("checkout_session_id"),
                "url": row.get("checkout_url"),
                "amount_cents": row.get("budget_cents"),
            }
            q_data = json.loads(row["quote_json"]) if row.get("quote_json") else {}
            if q_data:
                checkout["amount_cents"] = q_data.get("price_cents", checkout["amount_cents"])
            from .pricing import Quote
            if q_data and all(k in q_data for k in Quote.__dataclass_fields__):
                quote_obj = Quote(**{k: q_data[k] for k in q_data if k in Quote.__dataclass_fields__})
            else:
                quote_obj = self._stage_quote(job).get("_quote")
                if quote_obj is None:
                    return self._emit(stage="declined", job_id=job_id, reason="quote failed during retry")
            paid = self._stage_paid(job, checkout, quote_obj)
            if paid.get("stage") == "payment_pending":
                return paid
            return self._stage_fulfill_and_book(job, quote_obj, paid)

        # --- failed job: determine which stage failed ---
        paid_already = self.t.job_has_revenue(job_id)

        if paid_already:
            # Payment already collected — only re-run fulfillment (no re-charge)
            q_data = json.loads(row["quote_json"]) if row.get("quote_json") else {}
            from .pricing import Quote
            if q_data and all(k in q_data for k in Quote.__dataclass_fields__):
                quote_obj = Quote(**{k: q_data[k] for k in q_data if k in Quote.__dataclass_fields__})
            else:
                from .pricing import quote as _quote
                quote_obj = _quote(job, self.pricing)
            paid = {"stripe_ref": None, "amount_cents": q_data.get("price_cents", 0)}
            for e in self.t.entries:
                if e.job_id == job_id and e.kind == "revenue":
                    paid["stripe_ref"] = e.stripe_ref
                    paid["amount_cents"] = e.amount_cents
                    break
            # Clear previous failed stage records so fulfill runs fresh
            return self._stage_fulfill_and_book(job, quote_obj, paid)
        else:
            # Failed before payment — restart from quote
            return self.run_job(job)
