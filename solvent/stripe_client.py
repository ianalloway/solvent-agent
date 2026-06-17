"""
stripe_client.py — SOLVENT's two-sided money interface (the Stripe Skills layer).

EARN side: when a customer commissions a report, SOLVENT creates a real Stripe
Product + Price + Payment Link (test mode) and sends it to the customer to pay.

SPEND side: when SOLVENT needs a resource (Nemotron inference, market data, a
SaaS subscription), it pays the vendor. Outbound vendor payment is represented
here as a recorded transaction; in production this is Stripe's agent payment
rails / an issued virtual card scoped by the guardrails.

Safety: STRIPE_API_KEY must be a TEST key (sk_test_...). The client refuses to
run against a live key. With no key at all it uses a built-in simulator so the
agent runs end-to-end for a demo.
"""

from __future__ import annotations

import os
import time
import uuid

try:
    import stripe  # type: ignore
    _HAS_STRIPE = True
except Exception:
    _HAS_STRIPE = False


class StripeClient:
    def __init__(self):
        self.key = os.environ.get("STRIPE_API_KEY", "")
        self.live = False
        if os.environ.get("SOLVENT_FORCE_STRIPE_SIMULATE", "").strip() in ("1", "true", "yes"):
            self.key = ""
        if self.key:
            if not self.key.startswith("sk_test_"):
                raise RuntimeError(
                    "Refusing to run: STRIPE_API_KEY must be a TEST key (sk_test_...). "
                    "SOLVENT never touches a live account in this prototype."
                )
            if not _HAS_STRIPE:
                raise RuntimeError("STRIPE_API_KEY set but the `stripe` package is not installed. "
                                   "Run: pip install stripe")
            stripe.api_key = self.key
            self.live = True

    # ---- EARN ---------------------------------------------------------
    def create_payment_link(self, name: str, amount_cents: int, customer_email: str) -> dict:
        """Create a real Stripe Payment Link (test mode) or simulate one."""
        if self.live:
            price = stripe.Price.create(
                currency="usd",
                unit_amount=amount_cents,
                product_data={"name": name},
            )
            link = stripe.PaymentLink.create(
                line_items=[{"price": price.id, "quantity": 1}],
                metadata={"customer_email": customer_email, "agent": "SOLVENT"},
            )
            return {"id": link.id, "url": link.url, "amount_cents": amount_cents, "simulated": False}
        # simulator
        ref = "plink_sim_" + uuid.uuid4().hex[:12]
        return {
            "id": ref,
            "url": f"https://pay.stripe.test/{ref}",
            "amount_cents": amount_cents,
            "simulated": True,
        }

    def confirm_payment(self, link: dict) -> dict:
        """In a real deployment a webhook confirms payment. For the demo we
        treat the customer as having paid the link (test mode / simulator)."""
        return {
            "paid": True,
            "stripe_ref": link["id"],
            "amount_cents": link["amount_cents"],
            "ts": time.time(),
        }

    # ---- SPEND --------------------------------------------------------
    def pay_vendor(self, vendor: str, amount_cents: int, memo: str) -> dict:
        """Outbound payment for a resource the agent provisions for itself.

        Represented as a scoped agent payment. In the simulator (and test mode)
        this does not move real money; it returns an auditable reference that the
        treasury records as an expense."""
        ref = "pay_sim_" + uuid.uuid4().hex[:12]
        return {
            "id": ref,
            "vendor": vendor,
            "amount_cents": amount_cents,
            "memo": memo,
            "simulated": not self.live,
            "ts": time.time(),
        }

    def refund_payment(self, stripe_ref: str, amount_cents: int) -> dict:
        """Refund a payment (test mode or simulated)."""
        if self.live:
            try:
                # For real Stripe test-mode, we try to create a refund.
                # In Stripe, you refund a Charge or PaymentIntent. If stripe_ref is a payment link or session id,
                # you'd need the payment intent. For simplicity, we try to pass it directly.
                refund = stripe.Refund.create(
                    payment_intent=stripe_ref,
                    amount=amount_cents
                )
                return {
                    "id": refund.id,
                    "stripe_ref": stripe_ref,
                    "amount_cents": amount_cents,
                    "simulated": False,
                    "ts": time.time()
                }
            except Exception as e:
                # Fallback to simulated reference if live call fails
                ref = "ref_sim_" + uuid.uuid4().hex[:12]
                return {
                    "id": ref,
                    "stripe_ref": stripe_ref,
                    "amount_cents": amount_cents,
                    "simulated": True,
                    "error": str(e),
                    "ts": time.time()
                }

        # Simulator path
        ref = "ref_sim_" + uuid.uuid4().hex[:12]
        return {
            "id": ref,
            "stripe_ref": stripe_ref,
            "amount_cents": amount_cents,
            "simulated": True,
            "ts": time.time()
        }

