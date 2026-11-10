"""Idempotent job stage handlers for the SOLVENT money loop."""

from __future__ import annotations

import hashlib
import logging
import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from . import _filelock, delivery, service, spending, tools
from .guardrails import GuardrailError, Guardrails
from .intake import decline_reason, screen_job
from .products import UnknownProduct, apply_product
from .observability import log_event
from .pricing import PricingPolicy, quote
from .resource_budget import ResourceBudget, ResourceContract, using_budget
from .security import SOLVENTSecurityError, sanitise_job
from .nano_client import NanoClient
from .stripe_client import StripeClient
from .treasury import REFUND_VENDOR, Treasury


def _base_url() -> str:
    return os.environ.get("SOLVENT_BASE_URL", "http://127.0.0.1:8787").rstrip("/")


def _job_id_of(submitted: Any, validated: dict | None) -> str:
    """The best job id available for an event, even when validation failed."""
    for candidate in (validated, submitted):
        if isinstance(candidate, dict):
            job_id = candidate.get("id")
            if isinstance(job_id, str) and delivery.is_safe_job_id(job_id):
                return job_id
    return "unknown"


def _coerce_job(job: Any) -> tuple[dict | None, str | None, str | None]:
    """Field validation with no side effects.

    Returns ``(coerced_job, error, recorded_reason)``; ``recorded_reason`` is
    what the worker path stores on the job row for that error, or ``None`` when
    the error has never been recorded.
    """
    if not isinstance(job, dict):
        return None, "job must be a dictionary", None
    job_id = job.get("id")
    if not job_id or not isinstance(job_id, str) or not delivery.is_safe_job_id(job_id):
        return None, "missing or invalid job ID", None
    try:
        job = dict(job)
        sanitise_job(job)
    except SOLVENTSecurityError as exc:
        logging.getLogger(__name__).warning("job failed security checks: %s", exc)
        # Client-facing reason stays generic; details stay in the server log.
        return None, "security violation", "security violation"

    # A job may name a product instead of pricing itself; the product fills in
    # the scope and list price, and everything below validates it as normal.
    try:
        job = apply_product(job)
    except UnknownProduct as exc:
        logging.getLogger(__name__).info("job named unknown product: %s", exc)
        return None, "unknown product", "unknown product"

    topic = job.get("topic")
    if not topic or not isinstance(topic, str) or not topic.strip():
        return None, "missing or blank topic", "missing or blank topic"

    budget_cents = job.get("budget_cents")
    if budget_cents is None:
        return None, "missing budget_cents", "missing budget_cents"
    try:
        budget_cents = int(float(budget_cents))
    except (ValueError, TypeError):
        return None, "budget_cents must be a valid numeric value", "budget_cents must be numeric"
    if budget_cents < 0 or budget_cents > 1_000_000:
        return None, "invalid budget", "invalid budget"
    job["budget_cents"] = budget_cents

    for param, default_val in [
        ("est_tokens", 8_000),
        ("market_data_calls", 2),
        ("web_search_calls", 6),
    ]:
        val = job.get(param)
        if val is None:
            val = default_val
        try:
            val = int(float(val))
        except (ValueError, TypeError):
            return None, f"{param} must be a valid numeric value", None
        if val < 0:
            return None, f"{param} cannot be negative", None
        if param == "est_tokens" and val > 100_000:
            return None, "est_tokens exceeds maximum limit", None
        if param in ("market_data_calls", "web_search_calls") and val > 50:
            return None, f"{param} exceeds maximum limit", None
        job[param] = val

    job.setdefault("customer_email", "client@example.com")
    return job, None, None


def coerce_job(job: Any) -> tuple[dict | None, str | None]:
    """Validate and normalise a submitted job without writing anything."""
    coerced, err, _recorded = _coerce_job(job)
    return coerced, err


def screen_and_record(job: dict, treasury: Treasury) -> tuple[dict | None, str | None]:
    """Run the intake screen on a coerced job and record the outcome on its row."""
    job_id = job["id"]
    # Commercial screen: duplicates, bursts, oversized orders, unreachable
    # customers. Runs before pricing, so a screened-out job costs nothing.
    screen = screen_job(job, treasury)
    if not screen.allowed:
        reason = decline_reason(screen)
        treasury.upsert_job(
            job_id,
            "failed",
            topic=job["topic"],
            budget_cents=job["budget_cents"],
            customer_email=job["customer_email"],
            error_reason=reason,
        )
        treasury.upsert_metrics(job_id, decline_reason=reason)
        return None, reason

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
        job_owner_session_id=job.get("job_owner_session_id"),
    )
    return job, None


def validate_and_coerce_job(job: dict, treasury: Treasury) -> tuple[dict | None, str | None]:
    """Return (coerced_job, error_reason), recording failures on the job row."""
    coerced, err, recorded = _coerce_job(job)
    if err:
        if recorded is not None:
            treasury.upsert_job(job["id"], "failed", error_reason=recorded)
        return None, err
    assert coerced is not None
    return screen_and_record(coerced, treasury)


class StageRunner:
    """Runs idempotent stages for a single job."""

    def __init__(
        self,
        treasury: Treasury,
        guard: Guardrails,
        stripe: StripeClient,
        pricing: PricingPolicy | None = None,
        on_event: Callable | None = None,
        *,
        sync_payment: bool = True,
        nano: NanoClient | None = None,
    ):
        self.t = treasury
        self.guard = guard
        self.stripe = stripe
        self.nano = nano or NanoClient()
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
            **{
                k: v
                for k, v in event.items()
                if k
                not in (
                    "job_id",
                    "stage",
                    "simulated",
                    "payment_intent",
                    "stripe_ref",
                    "margin_pct",
                    "actual_margin_pct",
                )
            },
        )
        try:
            from . import dashboard

            dashboard.render(self.t.snapshot(), [])
        except Exception:
            pass
        return event

    def run_job(self, job: dict) -> dict:
        if isinstance(job, dict) and isinstance(job.get("id"), str):
            if (
                self.t.pending_refunds(job["id"])
                or (self.t.get_job(job["id"]) or {}).get("status") == "refund_pending"
            ):
                return self._refund_pending(job["id"])
        validated, err = validate_and_coerce_job(job, self.t)
        if err:
            # Validation and intake failures still belong to the job that was
            # submitted, so the decline is traceable in its own event log.
            return self._emit(stage="declined", job_id=_job_id_of(job, validated), reason=err)
        assert validated is not None

        q = self._stage_quote(validated)
        if q.get("stage") == "declined" or not q.get("accept"):
            return q

        quote_obj = q.get("_quote") or quote(validated, self.pricing)
        checkout = self._stage_checkout(validated, q)
        if checkout.get("stage") == "payment_pending":
            return checkout

        paid = self._stage_paid(validated, checkout, quote_obj)
        if paid.get("stage") == "payment_pending":
            return paid

        return self._stage_fulfill_and_book(validated, quote_obj, paid)

    def advance_job(self, job_id: str, *, verified_nano_payment: dict | None = None) -> dict:
        """Resume a job from its current DB state."""
        if self.t.pending_refunds(job_id):
            return self._refund_pending(job_id)
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
        # `expired` is terminal: the checkout sweep closed the unpaid link.
        # Falling through to run_job() would resurrect the job as awaiting_payment.
        if status == "refund_pending":
            return self._refund_pending(job_id)
        if status in ("completed", "failed", "expired"):
            return self._emit(
                stage="booked",
                job_id=job_id,
                status=status,
                job_pnl=self.t.job_pnl_cents(job_id),
                balance=self.t.balance_cents(),
            )
        if status == "pending_quote" or not row.get("quote_json"):
            return self.run_job(job)
        q_data = json.loads(row["quote_json"])
        if status == "awaiting_payment":
            checkout = {
                "session_id": row.get("checkout_session_id"),
                "url": row.get("checkout_url"),
                "amount_cents": q_data.get("price_cents"),
            }
            stored = self.t.get_checkout(job_id) or {}
            nano_inv = self.t.get_nano_invoice(job_id)
            if nano_inv:
                checkout["rail"] = "nano"
                checkout["destination"] = nano_inv.get("destination")
                checkout["amount_raw"] = nano_inv.get("amount_raw")
                checkout["xno_rate_usd"] = nano_inv.get("xno_rate_usd")
                checkout["expires_at"] = nano_inv.get("expires_at")
                checkout["invoice"] = {
                    "id": nano_inv.get("invoice_id") or checkout["session_id"],
                    "invoice_id": nano_inv.get("invoice_id"),
                    "job_id": job_id,
                    "destination": nano_inv.get("destination"),
                    "amount_cents": nano_inv.get("amount_cents") or checkout["amount_cents"],
                    "amount_raw": nano_inv.get("amount_raw"),
                    "xno_rate_usd": nano_inv.get("xno_rate_usd"),
                    "expires_at": nano_inv.get("expires_at"),
                    "status": nano_inv.get("status") or "open",
                    "block_hash": nano_inv.get("block_hash"),
                    "url": nano_inv.get("pay_uri") or checkout.get("url"),
                }
            else:
                checkout["rail"] = stored.get("rail") or "stripe"
            from .pricing import Quote

            quote_obj = Quote(**{k: q_data[k] for k in q_data if k in Quote.__dataclass_fields__})
            paid = self._stage_paid(
                job, checkout, quote_obj, verified_nano_payment=verified_nano_payment
            )
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
            emitted = self._emit(stage="quote", job_id=job_id, **data)
            row = self.t.get_job(job_id)
            if data.get("accept") and row and row.get("quote_json"):
                from .pricing import Quote

                return {**emitted, "_quote": Quote(**json.loads(row["quote_json"]))}
            return emitted

        q = quote(job, self.pricing)
        result = {
            "price": q.price_cents,
            "est_cost": q.est_cost_cents,
            "margin_pct": q.margin_pct,
            "accept": q.accept,
            "reason": q.reason,
        }
        if q.counter_offer:
            result["counter_offer"] = q.counter_offer
        self.t.complete_stage(job_id, "quote", key, result, payload={"job_id": job_id})
        self.t.upsert_job(
            job_id,
            "pending_quote" if q.accept else "failed",
            quote_json=json.dumps(asdict(q)),
        )
        self._emit(stage="quote", job_id=job_id, title=job["topic"], **result)
        if not q.accept:
            self.t.upsert_job(job_id, "failed", error_reason=q.reason)
            self.t.upsert_metrics(job_id, decline_reason=q.reason, est_cost_cents=q.est_cost_cents)
            declined = self._emit(stage="declined", job_id=job_id, reason=q.reason)
            # A decline is a negotiating position, not a dead end: tell the
            # customer the price (or the scope) the agent *would* say yes to.
            if q.counter_offer:
                self._emit(stage="counter_offer", job_id=job_id, **q.counter_offer)
            return declined

        with self.t.lock():
            if (
                self.guard._spent_last_24h() + q.est_cost_cents
                > self.guard.policy.daily_budget_cents
            ):
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

        # Opt-in Nano earn rail (SOLVENT_NANO=sim|live). Default remains Stripe.
        if self.nano.enabled:
            invoice = self.nano.create_nano_invoice(
                job_id=job_id,
                amount_cents=q.price_cents,
                customer_email=job.get("customer_email", "client@example.com"),
                name=f"Research brief: {job['topic'][:60]}",
            )
            result = {
                "session_id": invoice["id"],
                "url": invoice["url"],
                "amount_cents": q.price_cents,
                "amount_raw": invoice["amount_raw"],
                "destination": invoice["destination"],
                "xno_rate_usd": invoice["xno_rate_usd"],
                "expires_at": invoice["expires_at"],
                "rail": "nano",
                "simulated": bool(invoice.get("simulated")),
                "invoice": invoice,
            }
            self.t.complete_stage(job_id, "checkout", key, result)
            self.t.upsert_nano_invoice(job_id, invoice)
            self.t.upsert_checkout(job_id, invoice["id"], invoice["url"], "open", rail="nano")
            self.t.upsert_job(
                job_id,
                "awaiting_payment",
                checkout_session_id=invoice["id"],
                checkout_url=invoice["url"],
                current_stage="awaiting_payment",
            )
            self._emit(
                stage="invoice",
                job_id=job_id,
                url=invoice["url"],
                amount=q.price_cents,
                rail="nano",
                destination=invoice["destination"],
                amount_raw=invoice["amount_raw"],
                simulated=bool(invoice.get("simulated")),
            )
            return result

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
            "rail": "stripe",
            "simulated": session.get("simulated", False),
        }
        self.t.complete_stage(job_id, "checkout", key, result)
        self.t.upsert_checkout(job_id, session["id"], session["url"], "open", rail="stripe")
        self.t.upsert_job(
            job_id,
            "awaiting_payment",
            checkout_session_id=session["id"],
            checkout_url=session["url"],
            current_stage="awaiting_payment",
        )
        self._emit(
            stage="invoice",
            job_id=job_id,
            url=session["url"],
            amount=q.price_cents,
            rail="stripe",
            simulated=result["simulated"],
        )
        return result

    def _persist_paid_state(self, job_id: str, checkout: dict, key: str, result: dict) -> None:
        """Repair payment projections from committed revenue, under the treasury lock."""
        rail = result["rail"]
        session_id = (
            result.get("checkout_session_id")
            or checkout.get("session_id")
            or checkout.get("id", "")
        )
        if rail == "nano":
            self.t.mark_nano_invoice(job_id, status="paid", block_hash=result.get("payment_ref"))
        self.t.complete_stage(job_id, "paid", key, result)
        self.t.upsert_checkout(job_id, session_id, checkout.get("url", ""), "paid", rail=rail)
        # Publish the new job state last. An interruption before this point can
        # be repaired by the revenue fast path without charging again.
        current = self.t.get_job(job_id) or {}
        if current.get("status") not in ("expired", "failed", "completed", "in_progress"):
            self.t.upsert_job(job_id, "paid_pending_fulfill", current_stage="paid")

    def _stage_paid(
        self, job: dict, checkout: dict, q, *, verified_nano_payment: dict | None = None
    ) -> dict:
        job_id = job["id"]
        session_id = checkout.get("session_id") or checkout.get("id", "")
        key = f"paid:{job_id}:{session_id}"
        rail = checkout.get("rail")
        if not rail:
            stored = self.t.get_checkout(job_id) or {}
            rail = stored.get("rail") or ("nano" if self.t.get_nano_invoice(job_id) else "stripe")

        with self.t.lock():
            if self.t.job_has_revenue(job_id):
                for entry in self.t.entries:
                    if entry.job_id == job_id and entry.kind == "revenue":
                        result = {
                            "paid": True,
                            "rail": rail,
                            "stripe_ref": entry.stripe_ref,
                            "payment_ref": entry.resolved_payment_ref(),
                            "checkout_session_id": entry.stripe_session_id or session_id,
                            "amount_cents": entry.amount_cents,
                        }
                        self._persist_paid_state(job_id, checkout, key, result)
                        return result

        if rail == "nano":
            invoice = checkout.get("invoice") or self.t.get_nano_invoice(job_id) or {}
            if not invoice.get("destination") and checkout.get("destination"):
                invoice = {
                    "id": session_id,
                    "job_id": job_id,
                    "destination": checkout.get("destination"),
                    "amount_cents": checkout.get("amount_cents", getattr(q, "price_cents", 0)),
                    "amount_raw": checkout.get("amount_raw"),
                    "xno_rate_usd": checkout.get("xno_rate_usd"),
                    "expires_at": checkout.get("expires_at"),
                    "status": invoice.get("status") or "open",
                    "url": checkout.get("url"),
                }
            if verified_nano_payment is not None:
                payment = verified_nano_payment
            elif self.sync_payment and self.nano.simulate:
                # Sync demo only auto-confirms in sim; live always needs verify-on-submit.
                block_hash = self.nano.mint_sim_block(invoice)
                payment = self.nano.verify_nano_block(invoice, block_hash)
            else:
                # Async / live: operator/customer submits block via POST /payments/nano/verify.
                payment = {
                    "paid": False,
                    "reason": "awaiting nano block verification",
                }
                # A previously verified block may already be on the invoice row.
                if invoice.get("status") == "paid" and invoice.get("block_hash"):
                    payment = {
                        "paid": True,
                        "payment_ref": invoice["block_hash"],
                        "stripe_ref": invoice["block_hash"],
                        "block_hash": invoice["block_hash"],
                        "checkout_session_id": invoice.get("invoice_id") or session_id,
                        "amount_cents": invoice.get("amount_cents"),
                        "amount_raw": invoice.get("amount_raw"),
                        "rail": "nano",
                        "simulated": bool(invoice.get("simulated", self.nano.simulate)),
                        "job_id": job_id,
                    }
        else:
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
                payment = self.stripe.get_cached_payment(job_id) or {
                    "paid": False,
                    "reason": "awaiting webhook",
                }
                # Production serve uses the webhook cache; apply the same
                # amount/currency/PI/session checks as sync confirm/poll.
                if payment.get("paid"):
                    payment = self.stripe.accept_cached_payment(payment, link)

        if not payment.get("paid"):
            reason = payment.get("reason", "payment not received")
            with self.t.lock():
                row = self.t.get_job(job_id) or {}
                if row.get("status") == "awaiting_payment" and not self.t.job_has_revenue(job_id):
                    self.t.upsert_job(job_id, "awaiting_payment", error_reason=reason)
            return self._emit(
                stage="payment_pending",
                job_id=job_id,
                reason=reason,
                url=checkout.get("url"),
                rail=rail,
            )

        payment_ref = payment.get("payment_ref") or payment.get("stripe_ref")
        with self.t.lock():
            # Refuse earn on terminal jobs (expire race / late webhook).
            job_row = self.t.get_job(job_id) or {}
            if job_row.get("status") in ("expired", "failed", "completed"):
                return self._emit(
                    stage="payment_pending",
                    job_id=job_id,
                    reason=f"job is {job_row.get('status')}; payment not booked",
                    url=checkout.get("url"),
                    rail=rail,
                    status=job_row.get("status"),
                )
            # Re-check under the lock: a concurrent webhook/worker may have
            # booked this payment between the fast-path check above and here.
            if self.t.job_has_revenue(job_id):
                for e in self.t.entries:
                    if e.job_id == job_id and e.kind == "revenue":
                        result = {
                            "paid": True,
                            "rail": rail,
                            "stripe_ref": e.stripe_ref,
                            "payment_ref": e.payment_ref or e.stripe_ref,
                            "checkout_session_id": e.stripe_session_id or session_id,
                            "amount_cents": e.amount_cents,
                        }
                        self._persist_paid_state(job_id, checkout, key, result)
                        return result
            if rail == "nano" and verified_nano_payment is not None:
                current_invoice = self.t.get_nano_invoice(job_id) or {}
                matches = (
                    job_row.get("status") == "awaiting_payment"
                    and current_invoice.get("status") != "expired"
                    and isinstance(current_invoice.get("expires_at"), (int, float))
                    and time.time() <= current_invoice["expires_at"]
                    and payment.get("job_id") == job_id
                    and payment.get("invoice_id") == current_invoice.get("invoice_id")
                    and payment.get("destination") == current_invoice.get("destination")
                    and payment.get("amount_raw") == current_invoice.get("amount_raw")
                    and payment.get("amount_cents") == current_invoice.get("amount_cents")
                    and payment.get("amount_cents") == getattr(q, "price_cents", 0)
                )
                if not matches or not self.nano._try_book_block(
                    payment["block_hash"], payment, resume_invoice=True
                ):
                    return self._emit(
                        stage="payment_pending",
                        job_id=job_id,
                        reason="Nano payment does not match an open invoice or is already booked",
                        rail=rail,
                    )
            self.t.earn(
                payment["amount_cents"],
                f"Payment for {job_id}",
                job_id=job_id,
                stripe_ref=payment.get("stripe_ref") or payment_ref,
                payment_ref=payment_ref,
                stripe_session_id=payment.get("checkout_session_id") or session_id,
                stripe_link_id=payment.get("payment_link_id"),
            )
            result = {
                "paid": True,
                "rail": rail,
                "stripe_ref": payment.get("stripe_ref") or payment_ref,
                "payment_ref": payment_ref,
                "checkout_session_id": payment.get("checkout_session_id") or session_id,
                "amount_cents": payment["amount_cents"],
            }
            self._persist_paid_state(job_id, checkout, key, result)
        self._emit(
            stage="paid",
            job_id=job_id,
            amount=payment["amount_cents"],
            payment_intent=payment_ref,
            payment_ref=payment_ref,
            checkout_session=payment.get("checkout_session_id") or session_id,
            rail=rail,
        )
        return result

    def _stage_fulfill_and_book(self, job: dict, q, paid: dict) -> dict:
        # Serialize duplicate advancers without holding the treasury lock over
        # provider I/O. Different jobs still reserve shared budgets atomically.
        digest = hashlib.sha256(job["id"].encode()).hexdigest()
        lock_path = self.t.path.with_name(self.t.path.name + "." + digest + ".fulfill.lock")
        with lock_path.open("a") as handle:
            _filelock.acquire(handle, timeout=60.0)
            try:
                if self.t.pending_refunds(job["id"]):
                    return self._refund_pending(job["id"])
                row = self.t.get_job(job["id"]) or {}
                if row.get("status") == "refund_pending":
                    return self._refund_pending(job["id"])
                if row.get("status") == "completed" or self.t.job_refund_cents(job["id"]):
                    return self._emit(stage="booked", job_id=job["id"], status=row.get("status"))
                return self._fulfill_and_book_locked(job, q, paid)
            finally:
                _filelock.release(handle)

    def _refund_pending(self, job_id: str, reason: str = "refund outcome requires review") -> dict:
        with self.t.lock():
            # A resolver may have committed after the caller observed pending.
            # Never overwrite that terminal state with a stale hold.
            if not self.t.pending_refunds(job_id) and self.t.job_refund_cents(job_id):
                return self._emit(stage="booked", job_id=job_id, status="failed")
            self.t.upsert_job(job_id, "refund_pending", current_stage="refund", error_reason=reason)
        return self._emit(
            stage="refund_pending", job_id=job_id, status="refund_pending", reason=reason
        )

    def _fulfill_and_book_locked(self, job: dict, q, paid: dict) -> dict:
        job_id = job["id"]
        payment_intent_id = paid.get("stripe_ref")
        paid_amount = paid.get("amount_cents", getattr(q, "price_cents", 0))
        self.t.upsert_job(job_id, "in_progress", current_stage="fulfill")

        try:
            if any(e.job_id == job_id for e in self.t.reserved_spends()):
                raise GuardrailError("unresolved provider charges; review required")
            fulfill_key = f"fulfill:{job_id}"
            fulfill_stage = self.t.get_stage(fulfill_key)
            if fulfill_stage and fulfill_stage["status"] == "completed":
                result = json.loads(fulfill_stage["result_json"] or "{}")
                if "resources_used" not in result:
                    raise GuardrailError(
                        "cached fulfillment lacks resource accounting; review required"
                    )
            else:
                if not q.resource_contract:
                    raise ValueError(
                        "legacy quote lacks a resource contract; requote before fulfilment"
                    )
                contract = ResourceContract(**q.resource_contract)
                budget = ResourceBudget(
                    contract,
                    persist=lambda limits, increments: self.t.reserve_job_resources(
                        job_id, limits, increments
                    ),
                )
                margin = getattr(q, "margin_cents", q.price_cents - q.est_cost_cents)
                with using_budget(budget), spending.scope(self.guard, job_id, margin):
                    result = service.fulfill(job)
                self.t.complete_stage(
                    job_id,
                    "fulfill",
                    fulfill_key,
                    {
                        "deliverable_path": result["deliverable_path"],
                        "tokens": result["tokens"],
                        "pdf_path": result.get("pdf_path"),
                        "resources_used": result.get("resources_used", []),
                        "actual_cost_cents": result.get("actual_cost_cents", 0),
                        "fulfillment_seconds": result.get("fulfillment_seconds", 0),
                        "tool_calls": getattr(result.get("tool_ctx"), "total_calls", 0),
                    },
                )
                _tctx = result.get("tool_ctx")
                if _tctx is not None and getattr(_tctx, "budget_exhausted", False):
                    self._emit(
                        stage="tool_budget_exhausted",
                        job_id=job_id,
                        tool_calls=_tctx.total_calls,
                        limit=tools.MAX_TOOL_CALLS,
                    )
            self._emit(
                stage="fulfilled",
                job_id=job_id,
                deliverable=result["deliverable_path"],
                tokens=result.get("tokens", 0),
            )

            score = result.get("quality")
            if score is not None:
                self.t.upsert_metrics(
                    job_id,
                    quality_score=score.score,
                    quality_grade=score.grade,
                    quality_flags=",".join(score.failures),
                )
                self._emit(
                    stage="quality_checked",
                    job_id=job_id,
                    score=score.score,
                    grade=score.grade,
                    failures=score.failures,
                    attempts=result.get("quality_attempts", 1),
                )

            job_margin = getattr(q, "margin_cents", q.price_cents - q.est_cost_cents)
            resources = {}
            for vendor, amount, memo in result.get("resources_used", []):
                if type(amount) is not int or amount < 0:
                    raise GuardrailError("invalid resource charge")
                resources[vendor] = resources.get(vendor, 0) + amount
            for vendor, expected in resources.items():
                if expected <= 0:
                    continue
                memo = f"Resources for {job_id}: {vendor}"
                spend_key = f"spend:{job_id}:{vendor}"
                with self.t.lock():
                    # Live adapters already booked their charges. Account only
                    # for the remainder (offline/simulated resources), never
                    # use the existence of one charge to skip an entire vendor.
                    charges = [
                        e
                        for e in self.t.entries
                        if e.kind == "expense" and e.job_id == job_id and e.vendor == vendor
                    ]
                    booked = sum(e.amount_cents for e in charges)
                    # Quote resource usage is a conservative allowance. Live
                    # adapters settle actual/ambiguous charges themselves; do
                    # not pay the unused quote allowance a second time.
                    if any(e.id.startswith("rs_") for e in charges):
                        expected = booked
                    amount = max(0, expected - booked)
                    if amount:
                        decision = self.guard.evaluate(
                            amount, vendor, projected_job_margin_cents=job_margin, job_id=job_id
                        )
                        if not decision.allowed:
                            self.t.upsert_metrics(
                                job_id, block_rule=decision.rule, block_reason=decision.reason
                            )
                            self._emit(
                                stage="spend_held"
                                if decision.pending_approval
                                else "spend_blocked",
                                job_id=job_id,
                                vendor=vendor,
                                amount=amount,
                                memo=memo,
                                rule=decision.rule,
                                reason=decision.reason,
                            )
                            raise GuardrailError(
                                f"spend to {vendor} of {amount}c rejected by guardrails: "
                                f"{decision.reason}"
                            )
                        pay = self.stripe.pay_vendor(vendor, amount, memo, job_id=job_id)
                        self.t.spend(
                            amount, memo, job_id=job_id, vendor=vendor, stripe_ref=pay["id"]
                        )
                        self._emit(
                            stage="spend", job_id=job_id, vendor=vendor, amount=amount, memo=memo
                        )
                    self.t.complete_stage(
                        job_id,
                        "spend",
                        spend_key,
                        {"vendor": vendor, "amount": max(booked, expected)},
                    )

            # Include conservative charges for failed/unknown live attempts,
            # which may not appear in the successful completion's usage report.
            result["actual_cost_cents"] = sum(
                e.amount_cents
                for e in self.t.entries
                if e.kind == "expense" and e.job_id == job_id and e.vendor != REFUND_VENDOR
            )
            cogs = service.reconcile_cogs(q, result)
            self.t.upsert_metrics(
                job_id,
                **{
                    k: v
                    for k, v in cogs.items()
                    if k
                    in (
                        "est_cost_cents",
                        "actual_cost_cents",
                        "est_margin_pct",
                        "actual_margin_pct",
                        "margin_drift_cents",
                        "fulfillment_seconds",
                        "tool_calls",
                    )
                },
            )
            self.t.upsert_metrics(job_id, refunded=0)
            self._emit(stage="cogs_reconciled", job_id=job_id, **cogs)
            if any(e.job_id == job_id for e in self.t.reserved_spends()):
                raise GuardrailError("unresolved provider charges; review required")
            # Once published, delivery errors require review, never an automatic
            # refund: the customer may already have retrieved the hosted brief.
            self.t.complete_stage(job_id, "publish", f"publish:{job_id}", {})
            deliver_key = f"deliver:{job_id}"
            deliver_stage = self.t.get_stage(deliver_key)
            if not (deliver_stage and deliver_stage["status"] == "completed"):
                hosted = delivery.hosted_brief_url(_base_url(), job_id)
                from pathlib import Path

                delivery.send_brief_email(
                    job.get("customer_email", "client@example.com"),
                    job_id,
                    Path(result["deliverable_path"]),
                    hosted,
                    pdf_path=Path(result["pdf_path"]) if result.get("pdf_path") else None,
                )
                self.t.complete_stage(job_id, "deliver", deliver_key, {"hosted_url": hosted})
                self.t.upsert_job(
                    job_id,
                    "in_progress",
                    deliverable_url=hosted,
                    current_stage="deliver",
                )
                self._emit(stage="delivered", job_id=job_id, url=hosted)

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
            refund_booked = False
            published = self.t.get_stage(f"publish:{job_id}")
            delivered = self.t.get_stage(f"deliver:{job_id}")
            if published or delivered:
                self._emit(stage="delivery_review_required", job_id=job_id, reason=reason)
            if payment_intent_id and not (published or delivered):
                try:
                    # Fast path: already refunded (stage or ledger).
                    with self.t.lock():
                        refund_stage = self.t.get_stage(refund_key)
                        already = bool(refund_stage and refund_stage["status"] == "completed")
                        if already or self.t.job_refund_cents(job_id) > 0:
                            if not already:
                                self.t.complete_stage(
                                    job_id,
                                    "refund",
                                    refund_key,
                                    {"refund_id": "existing"},
                                )
                            refund_booked = True

                    if not refund_booked:
                        if not self.t.begin_refund(job_id, payment_intent_id, paid_amount):
                            return self._refund_pending(job_id)
                        # Stripe I/O outside the treasury lock; re-check before booking.
                        # Nano (and other non-Stripe) refs are not PaymentIntents —
                        # Stripe.refund with a block hash raises in live mode.
                        if str(payment_intent_id).startswith("pi_"):
                            refund = self.stripe.refund_payment(
                                payment_intent_id, paid_amount, job_id=job_id
                            )
                            if (
                                refund.get("status") != "succeeded"
                                or refund.get("amount_cents") != paid_amount
                                or refund.get("stripe_ref") != payment_intent_id
                                or not refund.get("id")
                            ):
                                raise ValueError("refund response does not confirm the full refund")
                            refund_id = refund["id"]
                        else:
                            refund_id = "ref_ledger_" + str(payment_intent_id)[:24]
                        self.t.complete_refund(job_id, payment_intent_id, refund_id)
                        refund_booked = True

                    self._emit(
                        stage="refunded",
                        job_id=job_id,
                        amount=paid_amount,
                        reason=reason,
                    )
                except Exception as refund_err:
                    reason = f"{reason}; refund outcome unresolved: {refund_err}"
                    return self._refund_pending(job_id, reason)
            if refund_booked:
                self.t.upsert_metrics(job_id, refunded=1)
            self.t.upsert_job(job_id, "failed", error_reason=reason, current_stage="failed")
            pnl = self.t.job_pnl_cents(job_id)
            booked_event = self._emit(
                stage="booked",
                job_id=job_id,
                job_pnl=pnl,
                balance=self.t.balance_cents(),
                status="failed",
            )
            if refund_booked:
                try:
                    from .receipt import build_refund_receipt

                    _job_row = self.t.get_job(job_id) or {}
                    _full_job = {**_job_row, **job, "error_reason": reason}
                    refund_receipt_text = build_refund_receipt(_full_job, paid_amount)
                    self._emit(
                        stage="refund_receipt_ready",
                        job_id=job_id,
                        receipt=refund_receipt_text,
                    )
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
        paid_stage = self.t.get_stage(key)
        if paid_stage and paid_stage["status"] == "completed":
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
        - ``failed`` jobs whose payment was refunded refuse retry (no free re-fulfill).
        """
        MAX_RETRIES = 3

        if self.t.pending_refunds(job_id):
            raise ValueError(f"Job {job_id!r} has an unresolved refund; reconcile before retrying")

        row = self.t.get_job(job_id)
        if not row:
            raise ValueError(f"Job {job_id!r} not found")

        status = row.get("status", "")
        if status not in ("failed", "awaiting_payment"):
            raise ValueError(
                f"Job {job_id!r} cannot be retried: status is {status!r} "
                "(only 'failed' or 'awaiting_payment'/'payment_pending' jobs are retryable)"
            )

        # Refuse before burning a retry slot: refunded payments must not unlock
        # free re-fulfillment (revenue row alone is not enough).
        if status == "failed":
            net_paid = self.t.job_net_paid_cents(job_id)
            if self.t.job_has_revenue(job_id) and net_paid <= 0:
                raise ValueError(
                    f"Job {job_id!r} was refunded; collect a new payment "
                    "(or open a new job) before retrying fulfillment"
                )

        new_count = self.t.increment_retry_count(job_id)
        if new_count > MAX_RETRIES:
            raise ValueError(f"Job {job_id!r} has exceeded the maximum retry limit ({MAX_RETRIES})")

        self._emit(
            stage="retry_started",
            job_id=job_id,
            retry_count=new_count,
            previous_status=status,
        )

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
            stored = self.t.get_checkout(job_id) or {}
            nano_inv = self.t.get_nano_invoice(job_id)
            if nano_inv:
                checkout["rail"] = "nano"
                checkout["destination"] = nano_inv.get("destination")
                checkout["amount_raw"] = nano_inv.get("amount_raw")
                checkout["xno_rate_usd"] = nano_inv.get("xno_rate_usd")
                checkout["expires_at"] = nano_inv.get("expires_at")
                checkout["invoice"] = {
                    "id": nano_inv.get("invoice_id") or checkout["session_id"],
                    "invoice_id": nano_inv.get("invoice_id"),
                    "job_id": job_id,
                    "destination": nano_inv.get("destination"),
                    "amount_cents": nano_inv.get("amount_cents") or checkout["amount_cents"],
                    "amount_raw": nano_inv.get("amount_raw"),
                    "xno_rate_usd": nano_inv.get("xno_rate_usd"),
                    "expires_at": nano_inv.get("expires_at"),
                    "status": nano_inv.get("status") or "open",
                    "block_hash": nano_inv.get("block_hash"),
                    "url": nano_inv.get("pay_uri") or checkout.get("url"),
                }
            else:
                checkout["rail"] = stored.get("rail") or "stripe"
            from .pricing import Quote

            quote_obj: Quote | None = None
            if q_data and all(k in q_data for k in Quote.__dataclass_fields__):
                quote_obj = Quote(
                    **{k: q_data[k] for k in q_data if k in Quote.__dataclass_fields__}
                )
            else:
                quote_obj = self._stage_quote(job).get("_quote")
                if quote_obj is None:
                    return self._emit(
                        stage="declined",
                        job_id=job_id,
                        reason="quote failed during retry",
                    )
            paid = self._stage_paid(job, checkout, quote_obj)
            if paid.get("stage") == "payment_pending":
                return paid
            return self._stage_fulfill_and_book(job, quote_obj, paid)

        # --- failed job: determine which stage failed ---
        # Net of customer refunds (refunded jobs are refused above).
        if self.t.job_net_paid_cents(job_id) > 0:
            # Payment still held — only re-run fulfillment (no re-charge)
            q_data = json.loads(row["quote_json"]) if row.get("quote_json") else {}
            from .pricing import Quote

            if q_data and all(k in q_data for k in Quote.__dataclass_fields__):
                quote_obj = Quote(
                    **{k: q_data[k] for k in q_data if k in Quote.__dataclass_fields__}
                )
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

        # Failed before payment — restart from quote
        return self.run_job(job)
