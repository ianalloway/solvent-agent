"""
nano_custody.py — Nano (XNO) custody, milestone M3 of issue #83.

Off by default. Nothing here runs unless ``SOLVENT_NANO_CUSTODY`` is set.

What custody adds on top of the verify-only earn rail (``nano_client``):
the agent can *account for* the XNO it has earned and *sweep* it to an
operator-controlled cold address, under caps and an approval hook. It never
spends Nano on anything else.

Modes (hard separation, mirrors ``nano_client``):

* **off** (default) — unset / ``off``. Verify-only Option A, exactly as 0.3.0.
* **sim** — ``SOLVENT_NANO_CUSTODY=sim``; requires ``SOLVENT_NANO=sim``.
  Balances come from verified ``block_sim_…`` payments; sweeps produce
  ``block_sim_sweep_…`` records in a local JSON file. No key is ever read,
  no network, no real address is accepted as a sweep target.
* **live** — ``SOLVENT_NANO_CUSTODY=live``; requires ``SOLVENT_NANO=live``,
  a key *reference* (``SOLVENT_NANO_CUSTODY_KEY_REF``) and a real cold address.
  Live sweeps need an injected :class:`Signer` (Option C, external signer).
  SOLVENT ships **no** live signer: block signing + PoW against mainnet has not
  been validated with real funds, so live sweeps refuse by default.
* **misconfigured** — anything partial or mixed; custody disabled, doctor fails.

Key material rules:

* A key is only ever named by reference: ``env:VAR_NAME`` (read from the
  process environment at sign time) or ``keyring:service/username`` (OS secret
  store via the optional ``keyring`` package). No file refs, no inline seeds.
* A reference that *looks like* a raw seed / private key is rejected.
* Resolved secrets live in :class:`SecretHandle`, whose ``repr``/``str`` are
  redacted; they are never written to disk, logged, or returned by ``status``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .nano_client import is_nano_address
from .paths import config_path

#: Default per-sweep floor (raw). Below this, sweeping is noise. 0.001 XNO.
DEFAULT_SWEEP_MIN_RAW = 10**27

#: Default rolling-24h sweep cap (raw). 100 XNO.
DEFAULT_SWEEP_DAILY_CAP_RAW = 100 * 10**30

_DAY = 24 * 3600

_ENV_REF_RE = re.compile(r"^env:([A-Z_][A-Z0-9_]*)$")
_KEYRING_REF_RE = re.compile(r"^keyring:([^/\s]+)/([^/\s]+)$")
#: Things that look like key material rather than a reference to it.
_RAW_SECRET_RE = re.compile(r"^(?:[0-9A-Fa-f]{64}|[0-9A-Fa-f]{128})$")

_SIM_COLD_PREFIX = "nano_sim_cold_"


class NanoCustodyError(RuntimeError):
    """Custody misconfiguration or a refused custody action."""


class SecretHandle:
    """Opaque wrapper around resolved key material. Never prints its value."""

    __slots__ = ("_value", "ref")

    def __init__(self, value: str, ref: str) -> None:
        self._value = value
        self.ref = ref

    def reveal(self) -> str:
        """Return the secret. Only a :class:`Signer` should call this."""
        return self._value

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"SecretHandle(ref={self.ref!r}, value=<redacted>)"

    __str__ = __repr__


def parse_key_ref(ref: str) -> tuple[str, tuple[str, ...]]:
    """Validate a key reference. Returns ``(kind, args)``; never resolves it."""
    ref = (ref or "").strip()
    if not ref:
        raise NanoCustodyError("SOLVENT_NANO_CUSTODY_KEY_REF is unset")
    if _RAW_SECRET_RE.match(ref) or ref.lower().startswith(("seed:", "raw:", "hex:")):
        raise NanoCustodyError(
            "SOLVENT_NANO_CUSTODY_KEY_REF must be a reference (env:NAME or "
            "keyring:service/user), not key material"
        )
    m = _ENV_REF_RE.match(ref)
    if m:
        return "env", (m.group(1),)
    m = _KEYRING_REF_RE.match(ref)
    if m:
        return "keyring", (m.group(1), m.group(2))
    raise NanoCustodyError(
        f"unsupported key reference {ref.split(':', 1)[0]!r}: use env:NAME or "
        "keyring:service/user (file paths and inline secrets are refused)"
    )


def resolve_key_ref(ref: str) -> SecretHandle:
    """Resolve a key reference to a :class:`SecretHandle` (live only)."""
    kind, args = parse_key_ref(ref)
    if kind == "env":
        value = os.environ.get(args[0], "")
        if not value:
            raise NanoCustodyError(f"key reference env:{args[0]} is empty")
        return SecretHandle(value, ref)
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError as exc:
        raise NanoCustodyError("keyring: reference needs the optional 'keyring' package") from exc
    value = keyring.get_password(args[0], args[1])
    if not value:
        raise NanoCustodyError(f"no secret stored at keyring:{args[0]}/{args[1]}")
    return SecretHandle(value, ref)


class Signer(Protocol):
    """External signer (Option C). Builds, signs and publishes one send block."""

    def send(self, *, key: SecretHandle, source: str, destination: str, amount_raw: int) -> str:
        """Publish a send of ``amount_raw`` and return the 64-hex block hash."""


@dataclass
class SweepPlan:
    destination: str
    amount_raw: int
    balance_raw: int
    swept_24h_raw: int
    daily_cap_raw: int
    reason: str = ""
    ok: bool = True

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "destination": self.destination,
            "amount_raw": str(self.amount_raw),
            "balance_raw": str(self.balance_raw),
            "swept_24h_raw": str(self.swept_24h_raw),
            "daily_cap_raw": str(self.daily_cap_raw),
            "reason": self.reason,
        }


def _sim_cold_address() -> str:
    return _SIM_COLD_PREFIX + hashlib.sha256(b"solvent-nano-sim-cold").hexdigest()[:20]


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise NanoCustodyError(f"{name} must be an integer raw amount") from exc
    if value < 0:
        raise NanoCustodyError(f"{name} must be >= 0")
    return value


@dataclass
class NanoCustody:
    """Custody accounting + guarded sweeps. Construct via :meth:`from_env`."""

    mode: str = "off"
    misconfig_reason: str = ""
    key_ref: str = ""
    cold_address: str = ""
    hot_address: str = ""
    sweep_min_raw: int = DEFAULT_SWEEP_MIN_RAW
    daily_cap_raw: int = DEFAULT_SWEEP_DAILY_CAP_RAW
    signer: Signer | None = None
    clock: Callable[[], float] = time.time
    _state: dict = field(default_factory=dict)

    # ---- construction ---------------------------------------------------
    @classmethod
    def from_env(cls, *, signer: Signer | None = None, clock=time.time) -> "NanoCustody":
        flag = os.environ.get("SOLVENT_NANO_CUSTODY", "").strip().lower()
        rail = os.environ.get("SOLVENT_NANO", "").strip().lower()
        c = cls(signer=signer, clock=clock)
        c.key_ref = os.environ.get("SOLVENT_NANO_CUSTODY_KEY_REF", "").strip()
        cold = os.environ.get("SOLVENT_NANO_COLD_ADDRESS", "").strip()
        c.hot_address = os.environ.get("SOLVENT_NANO_ADDRESS", "").strip()
        try:
            c.sweep_min_raw = _int_env("SOLVENT_NANO_SWEEP_MIN_RAW", DEFAULT_SWEEP_MIN_RAW)
            c.daily_cap_raw = _int_env(
                "SOLVENT_NANO_SWEEP_DAILY_CAP_RAW", DEFAULT_SWEEP_DAILY_CAP_RAW
            )
        except NanoCustodyError as exc:
            c.mode, c.misconfig_reason = "misconfigured", str(exc)
            return c

        if flag in ("", "0", "off", "false", "no"):
            c.mode = "off"
            if c.key_ref or cold:
                c.mode = "misconfigured"
                c.misconfig_reason = (
                    "custody key ref / cold address set but SOLVENT_NANO_CUSTODY is off"
                )
            return c
        if flag in ("sim", "simulate"):
            if rail not in ("sim", "simulate"):
                c.mode, c.misconfig_reason = (
                    "misconfigured",
                    "SOLVENT_NANO_CUSTODY=sim requires SOLVENT_NANO=sim",
                )
            elif c.key_ref:
                c.mode, c.misconfig_reason = (
                    "misconfigured",
                    "sim custody never reads keys: unset SOLVENT_NANO_CUSTODY_KEY_REF",
                )
            elif cold and not cold.startswith(_SIM_COLD_PREFIX):
                c.mode, c.misconfig_reason = (
                    "misconfigured",
                    f"sim custody only sweeps to {_SIM_COLD_PREFIX}… addresses",
                )
            else:
                c.mode = "sim"
                c.cold_address = cold or _sim_cold_address()
            c._load()
            return c
        if flag in ("live", "main", "mainnet"):
            missing = []
            if rail not in ("live", "main", "mainnet"):
                missing.append("SOLVENT_NANO=live")
            try:
                parse_key_ref(c.key_ref)
            except NanoCustodyError as exc:
                missing.append(str(exc))
            if not is_nano_address(cold):
                missing.append("SOLVENT_NANO_COLD_ADDRESS (valid nano_ address)")
            elif cold == c.hot_address:
                missing.append("SOLVENT_NANO_COLD_ADDRESS must differ from SOLVENT_NANO_ADDRESS")
            if missing:
                c.mode = "misconfigured"
                c.misconfig_reason = "incomplete live custody config: " + "; ".join(missing)
            else:
                c.mode = "live"
                c.cold_address = cold
            c._load()
            return c
        c.mode, c.misconfig_reason = (
            "misconfigured",
            f"SOLVENT_NANO_CUSTODY={flag!r} not one of off|sim|live",
        )
        return c

    @property
    def enabled(self) -> bool:
        return self.mode in ("sim", "live")

    # ---- persistence ----------------------------------------------------
    def _path(self):
        return config_path(f"nano_custody_{self.mode}.json")

    def _load(self) -> None:
        self._state = {"sweeps": []}
        if self.mode not in ("sim", "live"):
            return
        path = self._path()
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("sweeps"), list):
                    self._state = data
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._state, indent=2) + "\n", encoding="utf-8")

    # ---- accounting -----------------------------------------------------
    def earned_raw(self) -> int:
        """Sum of verified, booked Nano payments for this mode."""
        path = config_path("nano_payments.json")
        if not path.is_file():
            return 0
        try:
            booked = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        total = 0
        for block, rec in (booked or {}).items():
            is_sim = str(block).startswith("block_sim_")
            if (self.mode == "sim") != is_sim:
                continue  # never mix sim proofs into live custody (or vice versa)
            try:
                total += int(rec.get("amount_raw") or 0)
            except (TypeError, ValueError):
                continue
        return total

    def swept_raw(self, *, since: float | None = None) -> int:
        return sum(
            int(s["amount_raw"])
            for s in self._state.get("sweeps", [])
            if since is None or float(s.get("ts", 0)) >= since
        )

    def balance_raw(self) -> int:
        return max(self.earned_raw() - self.swept_raw(), 0)

    # ---- sweeps ---------------------------------------------------------
    def plan_sweep(self) -> SweepPlan:
        if not self.enabled:
            return SweepPlan("", 0, 0, 0, self.daily_cap_raw, self._why_disabled(), False)
        balance = self.balance_raw()
        swept_24h = self.swept_raw(since=self.clock() - _DAY)
        headroom = max(self.daily_cap_raw - swept_24h, 0)
        amount = min(balance, headroom)
        plan = SweepPlan(self.cold_address, amount, balance, swept_24h, self.daily_cap_raw)
        if balance < self.sweep_min_raw:
            plan.ok, plan.amount_raw = False, 0
            plan.reason = "balance below SOLVENT_NANO_SWEEP_MIN_RAW"
        elif amount < self.sweep_min_raw:
            plan.ok, plan.amount_raw = False, 0
            plan.reason = "rolling 24h sweep cap reached"
        return plan

    def execute_sweep(self, *, approve: Callable[[SweepPlan], bool] | None = None) -> dict:
        """Run one sweep. ``approve`` must return True (human-approval hook)."""
        plan = self.plan_sweep()
        if not plan.ok:
            raise NanoCustodyError(f"sweep refused: {plan.reason}")
        if approve is None or not approve(plan):
            raise NanoCustodyError("sweep refused: not approved")
        if self.mode == "sim":
            n = len(self._state["sweeps"])
            block = (
                "block_sim_sweep_"
                + hashlib.sha256(f"{self.cold_address}:{plan.amount_raw}:{n}".encode()).hexdigest()[
                    :24
                ]
            )
        else:
            if self.signer is None:
                raise NanoCustodyError(
                    "live sweep refused: no external signer configured. SOLVENT ships "
                    "no live Nano signer; live custody needs validation with real funds"
                )
            key = resolve_key_ref(self.key_ref)
            block = self.signer.send(
                key=key,
                source=self.hot_address,
                destination=self.cold_address,
                amount_raw=plan.amount_raw,
            )
            if not re.fullmatch(r"[0-9A-Fa-f]{64}", block or ""):
                raise NanoCustodyError("signer returned an invalid block hash")
        rec = {
            "block": block,
            "destination": self.cold_address,
            "amount_raw": str(plan.amount_raw),
            "ts": self.clock(),
            "mode": self.mode,
        }
        self._state["sweeps"].append(rec)
        self._save()
        return rec

    # ---- status ---------------------------------------------------------
    def _why_disabled(self) -> str:
        if self.mode == "off":
            return "custody off (SOLVENT_NANO_CUSTODY unset)"
        return self.misconfig_reason or "custody disabled"

    def status(self) -> dict:
        """Operator-facing status. Never includes key material."""
        out: dict[str, Any] = {
            "mode": self.mode,
            "enabled": self.enabled,
            "misconfig_reason": self.misconfig_reason,
            "key_ref_kind": self.key_ref.split(":", 1)[0] if self.key_ref else "",
            "cold_address": self.cold_address,
            "signer": type(self.signer).__name__ if self.signer else None,
            "live_validated": False,
        }
        if self.enabled:
            out["balance_raw"] = str(self.balance_raw())
            out["swept_raw"] = str(self.swept_raw())
            out["sweeps"] = len(self._state.get("sweeps", []))
        return out


def main(argv: list[str] | None = None) -> int:
    """``solvent custody [status|plan|sweep --yes]``."""
    import argparse

    p = argparse.ArgumentParser(prog="solvent custody", description=__doc__.split("\n\n")[1])
    p.add_argument("action", nargs="?", default="status", choices=["status", "plan", "sweep"])
    p.add_argument("--yes", action="store_true", help="approve the planned sweep")
    args = p.parse_args(argv)
    c = NanoCustody.from_env()
    if args.action == "status":
        print(json.dumps(c.status(), indent=2))
        return 0 if c.mode != "misconfigured" else 1
    if args.action == "plan":
        print(json.dumps(c.plan_sweep().to_dict(), indent=2))
        return 0
    try:
        rec = c.execute_sweep(approve=lambda _plan: bool(args.yes))
    except NanoCustodyError as exc:
        print(f"refused: {exc}")
        return 1
    print(json.dumps(rec, indent=2))
    return 0
