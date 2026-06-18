"""
stripe_client.py — SOLVENT's two-sided money interface (the Stripe Skills layer).

EARN side: when a customer commissions a report, SOLVENT creates a real Stripe
Payment Link (test mode) and sends it to the customer to pay.

SPEND side: when SOLVENT needs a resource (Nemotron inference, market data, a
SaaS subscription), it pays the vendor via Stripe Issuing (test mode) when
available, or records a simulated transaction for demos.

Safety: STRIPE_API_KEY must be a TEST key (sk_test_...). The client refuses to
run against a live key. With no key at all it uses a built-in simulator so the
agent runs end-to-end for a demo.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from .security import (
    validate_stripe_key,
    validate_catalog_schema,
    verify_webhook_signature,
    check_event_replay,
    validate_email,
    WebhookAuthError,
    ReplayAttackError,
)

try:
    import stripe  # type: ignore
    _HAS_STRIPE = True
except Exception:
    stripe = None  # type: ignore
    _HAS_STRIPE = False

CATALOG_PATH = Path(".solvent/stripe_catalog.json")
WEBHOOK_CACHE_PATH = Path(".solvent/stripe_payments.json")
PRODUCT_NAME = "SOLVENT Research Brief"
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_POLL_TIMEOUT = 120.0


class StripeClient:
    def __init__(self):
        self.key = os.environ.get("STRIPE_API_KEY", "")
        self.live = False
        if os.environ.get("SOLVENT_FORCE_STRIPE_SIMULATE", "").strip() in ("1", "true", "yes"):
            self.key = ""
        self.webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
        self.poll_timeout = float(os.environ.get("STRIPE_PAYMENT_POLL_TIMEOUT", DEFAULT_POLL_TIMEOUT))
        self.poll_interval = float(os.environ.get("STRIPE_PAYMENT_POLL_INTERVAL", DEFAULT_POLL_INTERVAL))
        self._catalog: dict | None = None
        self._webhook_payments: dict[str, dict] = {}
        self._issuing_available: bool | None = None
        if self.key:
            # AUTH — validate key prefix; live keys refused unconditionally
            try:
                validate_stripe_key(self.key)
            except Exception as exc:
                raise RuntimeError(str(exc)) from exc
            if not _HAS_STRIPE:
                raise RuntimeError(
                    "STRIPE_API_KEY set but the `stripe` package is not installed. "
                    "Run: pip install stripe"
                )
            stripe.api_key = self.key
            self.live = True
        self._load_webhook_cache()

    # ---- catalog ----------------------------------------------------
    def _load_catalog(self) -> dict:
        if self._catalog is not None:
            return self._catalog
        if CATALOG_PATH.is_file():
            try:
                raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
                # DATA PROTECTION — strip unknown / malformed keys before trusting
                self._catalog = validate_catalog_schema(raw)
            except (json.JSONDecodeError, OSError):
                self._catalog = {}
        else:
            self._catalog = {}
        return self._catalog

    def _save_catalog(self) -> None:
        if self._catalog is None:
            return
        CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CATALOG_PATH.write_text(json.dumps(self._catalog, indent=2) + "\n", encoding="utf-8")

    def _get_or_create_product(self) -> str:
        catalog = self._load_catalog()
        if catalog.get("product_id"):
            return catalog["product_id"]
        product = stripe.Product.create(name=PRODUCT_NAME, metadata={"agent": "SOLVENT"})
        catalog["product_id"] = product.id
        catalog.setdefault("prices", {})
        self._save_catalog()
        return product.id

    def _get_or_create_price(self, amount_cents: int) -> str:
        catalog = self._load_catalog()
        key = str(amount_cents)
        prices = catalog.setdefault("prices", {})
        if prices.get(key):
            return prices[key]
        product_id = self._get_or_create_product()
        price = stripe.Price.create(
            currency="usd",
            unit_amount=amount_cents,
            product=product_id,
        )
        prices[key] = price.id
        self._save_catalog()
        return price.id

    # ---- webhook cache ----------------------------------------------
    def _load_webhook_cache(self) -> None:
        if not WEBHOOK_CACHE_PATH.is_file():
            return
        try:
            data = json.loads(WEBHOOK_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._webhook_payments = data
        except (json.JSONDecodeError, OSError):
            self._webhook_payments = {}

    def _save_webhook_cache(self) -> None:
        WEBHOOK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        WEBHOOK_CACHE_PATH.write_text(
            json.dumps(self._webhook_payments, indent=2) + "\n",
            encoding="utf-8",
        )

    def _cache_webhook_payment(self, plink_id: str, payment: dict) -> None:
        self._webhook_payments[plink_id] = payment
        self._save_webhook_cache()

    def process_webhook(self, payload: bytes, sig_header: str) -> dict | None:
        """Validate checkout.session.completed and cache verified payment.

        AUTH: signature is verified with HMAC-SHA256 before any data is read.
        ACCOUNT TAKEOVER: duplicate event IDs are rejected (replay protection).
        """
        if not self.live or not self.webhook_secret:
            return None

        # 1. Verify Stripe-Signature header (raises WebhookAuthError if invalid)
        verify_webhook_signature(payload, sig_header, self.webhook_secret)

        # 2. Parse event using the Stripe SDK for structural validation
        event = stripe.Webhook.construct_event(payload, sig_header, self.webhook_secret)

        # 3. Replay protection — reject duplicate event IDs
        event_id = event.get("id", "")
        check_event_replay(event_id)  # raises ReplayAttackError if duplicate

        if event["type"] != "checkout.session.completed":
            return None
        session = event["data"]["object"]
        if session.get("payment_status") != "paid":
            return None
        plink_id = session.get("payment_link")
        if not plink_id:
            return None
        payment = self._session_to_payment(session)
        self._cache_webhook_payment(plink_id, payment)
        return payment

    # ---- EARN -------------------------------------------------------
    def create_payment_link(self, name: str, amount_cents: int, customer_email: str) -> dict:
        """Create a real Stripe Payment Link (test mode) or simulate one."""
        # ACCOUNT TAKEOVER — validate email before it reaches Stripe or the ledger
        try:
            customer_email = validate_email(customer_email)
        except Exception:
            customer_email = "client@example.com"  # safe fallback for demo
        if self.live:
            price_id = self._get_or_create_price(amount_cents)
            link = stripe.PaymentLink.create(
                line_items=[{"price": price_id, "quantity": 1}],
                metadata={"customer_email": customer_email, "agent": "SOLVENT", "brief": name[:500]},
            )
            return {
                "id": link.id,
                "url": link.url,
                "amount_cents": amount_cents,
                "simulated": False,
            }
        ref = "plink_sim_" + uuid.uuid4().hex[:12]
        return {
            "id": ref,
            "url": f"https://pay.stripe.test/{ref}",
            "amount_cents": amount_cents,
            "simulated": True,
        }

    def _session_to_payment(self, session) -> dict:
        pi_id = session.get("payment_intent") if isinstance(session, dict) else session.payment_intent
        if hasattr(pi_id, "id"):
            pi_id = pi_id.id
        amount = session.get("amount_total") if isinstance(session, dict) else session.amount_total
        return {
            "paid": True,
            "stripe_ref": pi_id,
            "checkout_session_id": session.get("id") if isinstance(session, dict) else session.id,
            "payment_link_id": session.get("payment_link") if isinstance(session, dict) else session.payment_link,
            "amount_cents": amount,
            "ts": time.time(),
        }

    def _poll_checkout_session(self, plink_id: str, link: dict) -> dict:
        deadline = time.time() + self.poll_timeout
        while time.time() < deadline:
            if self.webhook_secret and plink_id in self._webhook_payments:
                cached = self._webhook_payments[plink_id]
                if cached.get("paid"):
                    return cached
            sessions = stripe.checkout.Session.list(payment_link=plink_id, limit=5)
            for session in sessions.data:
                if session.payment_status == "paid":
                    payment = self._session_to_payment(session)
                    payment["payment_link_id"] = plink_id
                    if not payment.get("amount_cents"):
                        payment["amount_cents"] = link["amount_cents"]
                    return payment
            time.sleep(self.poll_interval)
        return {
            "paid": False,
            "reason": f"payment not received within {int(self.poll_timeout)}s",
            "payment_link_id": plink_id,
            "amount_cents": link["amount_cents"],
            "ts": time.time(),
        }

    def confirm_payment(self, link: dict) -> dict:
        """Verify payment before fulfilment.

        Simulate mode: instant confirm (demo / offline).
        Live mode: poll Checkout Sessions until paid, or use webhook cache when
        STRIPE_WEBHOOK_SECRET is set.
        """
        if not self.live or link.get("simulated"):
            sim_suffix = uuid.uuid4().hex[:12]
            return {
                "paid": True,
                "stripe_ref": f"pi_sim_{sim_suffix}",
                "checkout_session_id": f"cs_sim_{sim_suffix}",
                "payment_link_id": link["id"],
                "amount_cents": link["amount_cents"],
                "simulated": True,
                "ts": time.time(),
            }
        if self.webhook_secret and link["id"] in self._webhook_payments:
            cached = self._webhook_payments[link["id"]]
            if cached.get("paid"):
                return cached
        return self._poll_checkout_session(link["id"], link)

    # ---- SPEND (Issuing) --------------------------------------------
    def _issuing_enabled(self) -> bool:
        if not self.live:
            return False
        if self._issuing_available is not None:
            return self._issuing_available
        try:
            stripe.issuing.Cardholder.list(limit=1)
            self._issuing_available = True
        except Exception:
            self._issuing_available = False
        return self._issuing_available

    def _get_or_create_cardholder(self) -> str:
        catalog = self._load_catalog()
        if catalog.get("cardholder_id"):
            return catalog["cardholder_id"]
        holder = stripe.issuing.Cardholder.create(
            name="SOLVENT Agent",
            email="agent@solvent.local",
            type="company",
            billing={
                "address": {
                    "line1": "123 Agent Way",
                    "city": "San Francisco",
                    "state": "CA",
                    "postal_code": "94103",
                    "country": "US",
                }
            },
            metadata={"agent": "SOLVENT"},
        )
        catalog["cardholder_id"] = holder.id
        self._save_catalog()
        return holder.id

    def pay_vendor(self, vendor: str, amount_cents: int, memo: str) -> dict:
        """Outbound payment for a resource the agent provisions for itself."""
        if self.live and self._issuing_enabled():
            try:
                cardholder_id = self._get_or_create_cardholder()
                card = stripe.issuing.Card.create(
                    cardholder=cardholder_id,
                    currency="usd",
                    type="virtual",
                    spending_controls={
                        "spending_limits": [
                            {"amount": amount_cents, "interval": "all_time"},
                            {"amount": amount_cents, "interval": "per_authorization"},
                        ],
                    },
                    metadata={"vendor": vendor, "memo": memo[:500], "agent": "SOLVENT"},
                )
                return {
                    "id": card.id,
                    "vendor": vendor,
                    "amount_cents": amount_cents,
                    "memo": memo,
                    "simulated": False,
                    "issuing": True,
                    "card_last4": getattr(card, "last4", None),
                    "ts": time.time(),
                }
            except Exception:
                pass
        ref = "pay_sim_" + uuid.uuid4().hex[:12]
        return {
            "id": ref,
            "vendor": vendor,
            "amount_cents": amount_cents,
            "memo": memo,
            "simulated": True,
            "issuing": False,
            "ts": time.time(),
        }

    # ---- REFUND -----------------------------------------------------
    def refund_payment(self, payment_intent_id: str, amount_cents: int) -> dict:
        """Refund a verified PaymentIntent (test mode or simulated)."""
        if self.live:
            if not payment_intent_id or not str(payment_intent_id).startswith("pi_"):
                raise ValueError(
                    f"refund requires a PaymentIntent id (pi_...), got: {payment_intent_id!r}"
                )
            refund = stripe.Refund.create(
                payment_intent=payment_intent_id,
                amount=amount_cents,
            )
            return {
                "id": refund.id,
                "stripe_ref": payment_intent_id,
                "amount_cents": amount_cents,
                "simulated": False,
                "ts": time.time(),
            }
        ref = "ref_sim_" + uuid.uuid4().hex[:12]
        return {
            "id": ref,
            "stripe_ref": payment_intent_id,
            "amount_cents": amount_cents,
            "simulated": True,
            "ts": time.time(),
        }
