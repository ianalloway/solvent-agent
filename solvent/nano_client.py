"""
nano_client.py — SOLVENT's Nano (XNO) earn-side settle rail (issue #83).

Verify-only custody (Option A): no seed / hot wallet in-process.

Modes (hard separation — never mixed):

* **sim** — ``SOLVENT_NANO=sim`` (or ``SOLVENT_FORCE_NANO_SIMULATE``). No RPC.
  Destinations are ``nano_sim_…``; proofs are ``block_sim_…``.
* **live** — ``SOLVENT_NANO=live`` with both ``SOLVENT_NANO_RPC`` and
  ``SOLVENT_NANO_ADDRESS``. Verifies real ``block_info`` against the ledger.
  Never accepts ``block_sim_…`` proofs.
* **misconfigured** — partial live intent (RPC xor address, or ``live`` without
  both, or sim+RPC together). Rail disabled; doctor fails closed.
* **unset** — Stripe remains the default earn rail.

Stripe remains the default when Nano is unset.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any

from .paths import config_path

#: 1 XNO = 10^30 raw (Nano protocol).
RAW_PER_XNO = Decimal(10) ** 30

#: Fixed USD-per-XNO rate used only in simulate mode (no oracle / network).
DEFAULT_SIM_RATE_USD = Decimal("0.85")

#: Default invoice lifetime (seconds); checkout chase can expire earlier.
DEFAULT_INVOICE_TTL_SECONDS = 48 * 3600

#: urllib timeout for live RPC calls (seconds).
DEFAULT_RPC_TIMEOUT_SECONDS = 10.0

#: Nano / legacy xrb account address (base32 alphabet excludes 0, l, v, 2).
_NANO_ADDRESS_RE = re.compile(
    r"^(?:nano|xrb)_[13456789abcdefghijkmnopqrstuwxyz]{60}$"
)

#: 64-char hex block hash (Nano).
_BLOCK_HASH_RE = re.compile(r"^[0-9A-Fa-f]{64}$")


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


def is_nano_address(value: str | None) -> bool:
    """Return True if ``value`` looks like a Nano account address."""
    return bool(value) and bool(_NANO_ADDRESS_RE.match(value.strip()))


def is_block_hash(value: str | None) -> bool:
    """Return True if ``value`` looks like a 64-hex Nano block hash."""
    return bool(value) and bool(_BLOCK_HASH_RE.match(value.strip()))


def _sim_destination(job_id: str) -> str:
    digest = hashlib.sha256(f"solvent-nano-sim:{job_id}".encode()).hexdigest()[:20]
    return f"nano_sim_{digest}"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "sim", "simulate")


def _parse_rate(rate_env: str, *, required: bool, default: Decimal | None) -> Decimal:
    if rate_env:
        try:
            rate = Decimal(rate_env)
            if not rate.is_finite() or rate <= 0:
                raise InvalidOperation
            return rate
        except (InvalidOperation, ValueError) as exc:
            raise NanoClientError(
                f"SOLVENT_NANO_RATE_USD must be a positive number, got {rate_env!r}"
            ) from exc
    if required:
        raise NanoClientError(
            "Live Nano requires SOLVENT_NANO_RATE_USD (positive USD per XNO); "
            "refusing a silent default so quotes stay explicit"
        )
    if default is None:
        raise NanoClientError("rate_usd default missing")
    return default


class NanoClient:
    """Earn-side Nano invoice + verify client (sim or live verify-only)."""

    def __init__(self, *, rpc_post: Any | None = None) -> None:
        mode_env = os.environ.get("SOLVENT_NANO", "").strip().lower()
        rpc = os.environ.get("SOLVENT_NANO_RPC", "").strip()
        address = os.environ.get("SOLVENT_NANO_ADDRESS", "").strip()
        force_sim = _truthy(os.environ.get("SOLVENT_FORCE_NANO_SIMULATE"))
        self.rpc_url = rpc
        self.receive_address = address
        self._rpc_post = rpc_post  # injectable for tests
        self._booked_blocks: dict[str, dict] = {}
        self._load_booked()
        self._rate_explicit = bool(os.environ.get("SOLVENT_NANO_RATE_USD", "").strip())
        self.misconfig_reason = ""

        wants_sim = mode_env in ("sim", "simulate") or force_sim
        wants_live = mode_env in ("live", "main", "mainnet")

        # Hard rule: never mix sim proofs with live RPC intent.
        if wants_sim and (rpc or address or wants_live):
            self.mode = "misconfigured"
            self.enabled = False
            self.simulate = False
            self.misconfig_reason = (
                "sim and live Nano config must not be mixed "
                "(unset SOLVENT_NANO_RPC / SOLVENT_NANO_ADDRESS for sim, "
                "or use SOLVENT_NANO=live without sim)"
            )
        elif wants_sim:
            self.mode = "sim"
            self.enabled = True
            self.simulate = True
        elif wants_live:
            missing = []
            if not rpc:
                missing.append("SOLVENT_NANO_RPC")
            if not address:
                missing.append("SOLVENT_NANO_ADDRESS")
            elif not is_nano_address(address):
                missing.append("SOLVENT_NANO_ADDRESS (invalid nano_/xrb_ form)")
            if not os.environ.get("SOLVENT_NANO_RATE_USD", "").strip():
                missing.append("SOLVENT_NANO_RATE_USD")
            if missing:
                self.mode = "misconfigured"
                self.enabled = False
                self.simulate = False
                self.misconfig_reason = (
                    "incomplete live Nano config: set " + ", ".join(missing)
                )
            else:
                self.mode = "live"
                self.enabled = True
                self.simulate = False
        elif rpc or address:
            # Fail closed: any live-looking env without SOLVENT_NANO=live.
            bits = []
            if not wants_live:
                bits.append("SOLVENT_NANO=live")
            if not rpc:
                bits.append("SOLVENT_NANO_RPC")
            if not address:
                bits.append("SOLVENT_NANO_ADDRESS")
            elif not is_nano_address(address):
                bits.append("SOLVENT_NANO_ADDRESS (invalid nano_/xrb_ form)")
            if not os.environ.get("SOLVENT_NANO_RATE_USD", "").strip():
                bits.append("SOLVENT_NANO_RATE_USD")
            self.mode = "misconfigured"
            self.enabled = False
            self.simulate = False
            self.misconfig_reason = "incomplete live Nano config: set " + ", ".join(bits)
        else:
            self.mode = "unset"
            self.enabled = False
            self.simulate = False

        rate_env = os.environ.get("SOLVENT_NANO_RATE_USD", "").strip()
        try:
            if self.mode == "live":
                self.rate_usd = _parse_rate(rate_env, required=True, default=None)
            elif self.mode == "sim":
                self.rate_usd = _parse_rate(
                    rate_env, required=False, default=DEFAULT_SIM_RATE_USD
                )
            else:
                self.rate_usd = _parse_rate(
                    rate_env, required=False, default=DEFAULT_SIM_RATE_USD
                )
        except NanoClientError as exc:
            self.rate_usd = DEFAULT_SIM_RATE_USD
            self.mode = "misconfigured"
            self.enabled = False
            self.simulate = False
            self.misconfig_reason = str(exc)

        ttl_env = os.environ.get("SOLVENT_NANO_INVOICE_TTL", "").strip()
        if ttl_env:
            self.invoice_ttl_seconds = max(int(ttl_env), 1)
        else:
            self.invoice_ttl_seconds = DEFAULT_INVOICE_TTL_SECONDS

        timeout_env = os.environ.get("SOLVENT_NANO_RPC_TIMEOUT", "").strip()
        if timeout_env:
            self.rpc_timeout_seconds = max(float(timeout_env), 0.5)
        else:
            self.rpc_timeout_seconds = DEFAULT_RPC_TIMEOUT_SECONDS

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
            "receive_address_valid": (
                is_nano_address(self.receive_address) if self.receive_address else False
            ),
            "rate_usd": str(self.rate_usd),
            "rate_explicit": self._rate_explicit,
            "hot_wallet": False,
            "misconfig_reason": self.misconfig_reason,
        }

    def probe_rpc(self) -> dict:
        """Best-effort live RPC reachability check (``version`` action)."""
        if not self.rpc_url:
            return {"ok": False, "detail": "SOLVENT_NANO_RPC unset"}
        try:
            data = self._rpc({"action": "version"})
        except NanoClientError as exc:
            return {"ok": False, "detail": str(exc)}
        except Exception as exc:  # pragma: no cover - defensive
            return {"ok": False, "detail": f"rpc error: {exc}"}
        node = data.get("node_vendor") or data.get("node_id") or data.get("rpc_version")
        detail = f"reachable ({node})" if node else "reachable"
        return {"ok": True, "detail": detail, "raw": data}

    # ---- RPC -------------------------------------------------------------
    def _rpc(self, payload: dict) -> dict:
        if self.simulate:
            raise NanoClientError("simulate mode must not call live Nano RPC")
        if not self.rpc_url:
            raise NanoClientError("SOLVENT_NANO_RPC is not configured")
        if self._rpc_post is not None:
            result = self._rpc_post(self.rpc_url, payload)
            if not isinstance(result, dict):
                raise NanoClientError("rpc_post returned non-object")
            return result

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.rpc_url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.rpc_timeout_seconds) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise NanoClientError(f"Nano RPC HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise NanoClientError(f"Nano RPC unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise NanoClientError("Nano RPC timed out") from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NanoClientError("Nano RPC returned non-JSON") from exc
        if not isinstance(data, dict):
            raise NanoClientError("Nano RPC returned non-object JSON")
        if data.get("error"):
            raise NanoClientError(f"Nano RPC error: {data['error']}")
        return data

    def fetch_block_info(self, block_hash: str) -> dict:
        """Fetch ``block_info`` for a confirmed send check."""
        data = self._rpc(
            {
                "action": "block_info",
                "json_block": "true",
                "hash": block_hash,
            }
        )
        contents = data.get("contents") or {}
        if isinstance(contents, str):
            try:
                contents = json.loads(contents)
            except json.JSONDecodeError:
                contents = {}
        destination = (
            data.get("link_as_account")
            or (contents.get("link_as_account") if isinstance(contents, dict) else None)
            or data.get("destination")
            or ""
        )
        amount = str(data.get("amount") or "")
        subtype = str(data.get("subtype") or "").lower()
        if "confirmed" in data:
            confirmed = str(data.get("confirmed")).lower() in ("true", "1", "yes")
        else:
            confirmed = True  # older nodes may omit the field
        return {
            "subtype": subtype,
            "destination": destination,
            "amount": amount,
            "confirmed": confirmed,
            "raw": data,
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
        """Create a Nano invoice from an already profit-gated quote."""
        if self.mode == "misconfigured":
            raise NanoClientError(
                self.misconfig_reason or "Nano settle rail is misconfigured"
            )
        if not self.enabled:
            raise NanoClientError(
                "Nano settle rail is not enabled "
                "(set SOLVENT_NANO=sim or SOLVENT_NANO=live with RPC + address + rate)"
            )
        if amount_cents < 0:
            raise ValueError("amount_cents must be >= 0")

        stamp = time.time() if now is None else now
        rate_ts = stamp
        amount_raw = cents_to_raw(amount_cents, self.rate_usd)
        ttl = self.invoice_ttl_seconds if ttl_seconds is None else max(int(ttl_seconds), 1)
        expires_at = stamp + ttl

        if self.simulate:
            destination = _sim_destination(job_id)
            invoice_id = "inv_sim_" + uuid.uuid4().hex[:12]
            simulated = True
        else:
            destination = self.receive_address
            if not is_nano_address(destination):
                raise NanoClientError("SOLVENT_NANO_ADDRESS is not a valid nano_ address")
            invoice_id = "inv_live_" + uuid.uuid4().hex[:12]
            simulated = False

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
            "simulated": simulated,
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

        Sim accepts ``block_sim_…`` only (optional ``block`` inject for tests).
        Live fetches RPC ``block_info`` and never accepts sim proofs.
        """
        stamp = time.time() if now is None else now
        invoice_id = invoice.get("id") or invoice.get("invoice_id") or ""
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

        if self.mode == "misconfigured":
            return reject(self.misconfig_reason or "nano rail misconfigured")
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

        if self.simulate or self.mode == "sim":
            return self._verify_sim(
                invoice,
                block_hash,
                block=block,
                stamp=stamp,
                invoice_id=invoice_id,
                expected_dest=expected_dest,
                expected_raw=expected_raw,
                job_id=job_id,
                reject=reject,
            )

        # Live path — hard reject sim tokens.
        if block_hash.startswith("block_sim_") or expected_dest.startswith("nano_sim_"):
            return reject("live mode rejects simulate proofs / destinations")
        if not is_block_hash(block_hash):
            return reject("invalid block hash")

        try:
            info = self.fetch_block_info(block_hash)
        except NanoClientError as exc:
            return reject(str(exc))

        # Optional test inject overlays RPC result (unit tests only).
        if block:
            if "subtype" in block:
                info["subtype"] = block["subtype"]
            if "destination" in block:
                info["destination"] = block["destination"]
            if "amount" in block:
                info["amount"] = str(block["amount"])
            elif "amount_raw" in block:
                info["amount"] = str(block["amount_raw"])
            if "confirmed" in block:
                info["confirmed"] = bool(block["confirmed"])

        if not info.get("confirmed", True):
            return reject("block not confirmed")

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
            "stripe_ref": block_hash,
            "block_hash": block_hash,
            "invoice_id": invoice_id,
            "checkout_session_id": invoice_id,
            "destination": expected_dest,
            "amount_cents": invoice.get("amount_cents"),
            "amount_raw": expected_raw,
            "paid_raw": str(paid_raw),
            "xno_rate_usd": invoice.get("xno_rate_usd"),
            "simulated": False,
            "job_id": job_id,
            "ts": stamp,
        }
        self._remember_block(block_hash, payment)
        return payment

    def _verify_sim(
        self,
        invoice: dict,
        block_hash: str,
        *,
        block: dict | None,
        stamp: float,
        invoice_id: str,
        expected_dest: str,
        expected_raw: str,
        job_id: str | None,
        reject,
    ) -> dict:
        if self.rpc_url:
            return reject("simulate mode must not use SOLVENT_NANO_RPC")
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
        if not self.simulate:
            raise NanoClientError("mint_sim_block is only available in simulate mode")
        suffix = uuid.uuid4().hex[:16]
        if invoice and invoice.get("id"):
            digest = hashlib.sha256(str(invoice["id"]).encode()).hexdigest()[:8]
            return f"block_sim_{digest}{suffix}"
        return f"block_sim_{suffix}"
