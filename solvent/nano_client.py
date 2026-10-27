"""
nano_client.py — SOLVENT's Nano (XNO) earn-side settle rail (issue #83).

v1 is **verify-only + simulate**: no RPC, no seed, no hot wallet. Opt in with
``SOLVENT_NANO=sim``. Live RPC (``SOLVENT_NANO_RPC``) is reserved and refused
until a later PR. Stripe remains the default earn rail when Nano is unset.

Mirrors :class:`solvent.stripe_client.StripeClient` at the call sites:
create an invoice from a profit-gated quote, then verify a send proof before
fulfilment.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from .paths import config_path

#: 1 XNO = 10^30 raw (Nano protocol).
RAW_PER_XNO = Decimal(10) ** 30

#: Fixed USD-per-XNO rate used only in simulate mode (no oracle / network).
DEFAULT_SIM_RATE_USD = Decimal("0.85")

#: Default invoice lifetime (seconds); checkout chase can expire earlier.
DEFAULT_INVOICE_TTL_SECONDS = 48 * 3600


class NanoClientError(RuntimeError):
    """Nano settle rail misconfiguration or unsupported mode."""


def cents_to_raw(amount_cents: int, rate_usd: Decimal | str | float) -> str:
    """Map integer USD cents to a Nano raw amount string.

    ``raw = round_half_up((cents / 100) / rate_usd * 10**30)``. Uses Decimal
    end-to-end (same spirit as :func:`solvent.money.dollars_to_cents`); binary
    float is never used for the conversion.
    """
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int):
        raise ValueError(f"amount_cents must be int, got {amount_cents!r}")
    if amount_cents < 0:
        raise ValueError(f"amount_cents must be >= 0, got {amount_cents}")
    try:
        rate = rate_usd if isinstance(rate_usd, Decimal) else Decimal(str(rate_usd).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"not a finite USD rate: {rate_usd!r}") from exc
    if not rate.is_finite() or rate <= 0:
        raise ValueError(f"rate_usd must be a positive finite number, got {rate_usd!r}")
    # Default Decimal precision (28) is too small for 10^30 raw units.
    with localcontext() as ctx:
        ctx.prec = 80
        usd = Decimal(amount_cents) / Decimal(100)
        xno = usd / rate
        raw = (xno * RAW_PER_XNO).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return str(int(raw))


def _sim_destination(job_id: str) -> str:
    digest = hashlib.sha256(f"solvent-nano-sim:{job_id}".encode()).hexdigest()[:20]
    return f"nano_sim_{digest}"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "sim", "simulate")


class NanoClient:
    """Earn-side Nano invoice + verify client (simulate / verify-only v1)."""

    def __init__(self) -> None:
        mode = os.environ.get("SOLVENT_NANO", "").strip().lower()
        rpc = os.environ.get("SOLVENT_NANO_RPC", "").strip()
        self.rpc_url = rpc
        self.receive_address = os.environ.get("SOLVENT_NANO_ADDRESS", "").strip()
        self._booked_blocks: dict[str, dict] = {}
        self._load_booked()

        force_sim = _truthy(os.environ.get("SOLVENT_FORCE_NANO_SIMULATE"))
        if mode in ("sim", "simulate") or force_sim:
            # Opt-in simulate: SOLVENT_NANO=sim (or force flag for tests).
            self.mode = "sim"
            self.enabled = True
            self.simulate = True
        elif rpc:
            # Live path is intentionally not implemented in v1.
            self.mode = "live_unsupported"
            self.enabled = False
            self.simulate = False
        else:
            self.mode = "unset"
            self.enabled = False
            self.simulate = False

        rate_env = os.environ.get("SOLVENT_NANO_RATE_USD", "").strip()
        if rate_env:
            try:
                self.rate_usd = Decimal(rate_env)
                if not self.rate_usd.is_finite() or self.rate_usd <= 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError) as exc:
                raise NanoClientError(
                    f"SOLVENT_NANO_RATE_USD must be a positive number, got {rate_env!r}"
                ) from exc
        else:
            self.rate_usd = DEFAULT_SIM_RATE_USD

        ttl_env = os.environ.get("SOLVENT_NANO_INVOICE_TTL", "").strip()
        if ttl_env:
            self.invoice_ttl_seconds = max(int(ttl_env), 1)
        else:
            self.invoice_ttl_seconds = DEFAULT_INVOICE_TTL_SECONDS

    # ---- persistence (replay protection) ---------------------------------
    def _booked_path(self):
        return config_path("nano_payments.json")

    def _load_booked(self) -> None:
        path = self._booked_path()
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._booked_blocks = data
        except (json.JSONDecodeError, OSError):
            self._booked_blocks = {}

    def _save_booked(self) -> None:
        path = self._booked_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._booked_blocks, indent=2) + "\n", encoding="utf-8")

    def _remember_block(self, block_hash: str, payment: dict) -> None:
        self._booked_blocks[block_hash] = {
            "job_id": payment.get("job_id"),
            "invoice_id": payment.get("invoice_id"),
            "amount_raw": payment.get("amount_raw"),
            "ts": payment.get("ts") or time.time(),
        }
        self._save_booked()

    def is_block_booked(self, block_hash: str) -> bool:
        return bool(block_hash) and block_hash in self._booked_blocks

    # ---- status ----------------------------------------------------------
    def status(self) -> dict:
        """Operator-facing rail status (doctor / dashboard)."""
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "simulate": self.simulate,
            "rpc_configured": bool(self.rpc_url),
            "receive_address_configured": bool(self.receive_address),
            "rate_usd": str(self.rate_usd),
            "hot_wallet": False,
        }

    # ---- EARN: invoice ---------------------------------------------------
    def create_nano_invoice(
        self,
        job_id: str,
        amount_cents: int,
        *,
        customer_email: str = "",
        name: str = "",
        ttl_seconds: int | None = None,
        now: float | None = None,
    ) -> dict:
        """Create a Nano invoice from an already profit-gated quote.

        Simulate mode returns a ``nano_sim_…`` destination and a deterministic
        raw amount. Live RPC is not implemented in v1.
        """
        if not self.enabled:
            raise NanoClientError(
                "Nano settle rail is not enabled (set SOLVENT_NANO=sim for simulate mode)"
            )
        if self.mode == "live_unsupported":
            raise NanoClientError(
                "Live Nano RPC is not implemented in v1; use SOLVENT_NANO=sim "
                "or unset SOLVENT_NANO_RPC"
            )
        if not self.simulate:
            raise NanoClientError("Nano client is not in simulate mode")
        if amount_cents < 0:
            raise ValueError("amount_cents must be >= 0")

        stamp = time.time() if now is None else now
        rate_ts = stamp
        amount_raw = cents_to_raw(amount_cents, self.rate_usd)
        destination = _sim_destination(job_id)
        invoice_id = "inv_sim_" + uuid.uuid4().hex[:12]
        ttl = self.invoice_ttl_seconds if ttl_seconds is None else max(int(ttl_seconds), 1)
        expires_at = stamp + ttl
        pay_uri = f"nano:{destination}?amount={amount_raw}"

        return {
            "id": invoice_id,
            "job_id": job_id,
            "rail": "nano",
            "destination": destination,
            "amount_cents": amount_cents,
            "amount_raw": amount_raw,
            "xno_rate_usd": str(self.rate_usd),
            "rate_ts": rate_ts,
            "url": pay_uri,
            "customer_email": customer_email or "",
            "name": (name or "")[:500],
            "status": "open",
            "expires_at": expires_at,
            "simulated": True,
            "ts": stamp,
        }

    def expire_invoice(self, invoice: dict) -> dict:
        """Mark an invoice expired (no network)."""
        out = dict(invoice)
        out["status"] = "expired"
        return out

    # ---- EARN: verify ----------------------------------------------------
    def verify_nano_block(
        self,
        invoice: dict,
        block_hash: str,
        *,
        block: dict | None = None,
        now: float | None = None,
    ) -> dict:
        """Verify a send proof against an invoice before fulfilment.

        In simulate mode a ``block_sim_…`` token is accepted when it satisfies
        the same checks a live ``block_info`` would: send subtype, destination
        match, amount >= invoice raw, not expired, not replayed. Pass ``block``
        to inject amount/destination/subtype for underpay / wrong-dest tests
        without touching the network.
        """
        stamp = time.time() if now is None else now
        invoice_id = invoice.get("id") or ""
        expected_dest = invoice.get("destination") or ""
        expected_raw = str(invoice.get("amount_raw") or "0")
        job_id = invoice.get("job_id")

        def reject(reason: str) -> dict:
            return {
                "paid": False,
                "reason": reason,
                "block_hash": block_hash,
                "invoice_id": invoice_id,
                "job_id": job_id,
                "rail": "nano",
                "amount_cents": invoice.get("amount_cents"),
                "amount_raw": expected_raw,
                "ts": stamp,
            }

        if not self.enabled and not self.simulate:
            return reject("nano rail not enabled")

        if invoice.get("status") == "expired":
            return reject("invoice expired")
        expires_at = invoice.get("expires_at")
        if isinstance(expires_at, (int, float)) and stamp > float(expires_at):
            return reject("invoice expired")

        if not isinstance(block_hash, str) or not block_hash:
            return reject("missing block hash")

        if self.is_block_booked(block_hash):
            return reject("block hash already booked (replay)")

        # Simulate path only in v1.
        if not (self.simulate or self.mode == "sim"):
            return reject("live nano verify is not implemented in v1")

        if not block_hash.startswith("block_sim_"):
            return reject("simulate mode only accepts block_sim_… proofs")

        info = {
            "subtype": "send",
            "destination": expected_dest,
            "amount": expected_raw,
        }
        if block:
            if "subtype" in block:
                info["subtype"] = block["subtype"]
            if "destination" in block:
                info["destination"] = block["destination"]
            if "amount" in block:
                info["amount"] = str(block["amount"])
            elif "amount_raw" in block:
                info["amount"] = str(block["amount_raw"])

        if info.get("subtype") != "send":
            return reject("block subtype is not send")
        if info.get("destination") != expected_dest:
            return reject("wrong destination")

        try:
            paid_raw = int(str(info.get("amount") or "0"))
            need_raw = int(expected_raw)
        except (TypeError, ValueError):
            return reject("invalid amount_raw")
        if paid_raw < need_raw:
            return reject("underpay: amount below invoice raw")

        payment = {
            "paid": True,
            "rail": "nano",
            "payment_ref": block_hash,
            # Thin alias so existing earn()/stage paths that read stripe_ref work.
            "stripe_ref": block_hash,
            "block_hash": block_hash,
            "invoice_id": invoice_id,
            "checkout_session_id": invoice_id,
            "destination": expected_dest,
            "amount_cents": invoice.get("amount_cents"),
            "amount_raw": expected_raw,
            "paid_raw": str(paid_raw),
            "xno_rate_usd": invoice.get("xno_rate_usd"),
            "simulated": True,
            "job_id": job_id,
            "ts": stamp,
        }
        self._remember_block(block_hash, payment)
        return payment

    def mint_sim_block(self, invoice: dict | None = None) -> str:
        """Return a fresh ``block_sim_…`` token for demo / sync confirm."""
        suffix = uuid.uuid4().hex[:16]
        if invoice and invoice.get("id"):
            digest = hashlib.sha256(str(invoice["id"]).encode()).hexdigest()[:8]
            return f"block_sim_{digest}{suffix}"
        return f"block_sim_{suffix}"
