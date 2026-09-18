"""
guardrails.py — the NemoClaw-style spend safety layer.

Every time SOLVENT wants to move money, the request passes through here first.
This is the "run agents safely" piece: an autonomous agent with a Stripe key is
dangerous unless its spending is bounded. These guardrails are deterministic,
auditable, and run BEFORE any Stripe call.

Rules enforced:
  1. Vendor allowlist        — money can only go to pre-approved vendors.
  2. Per-transaction cap      — no single spend over a ceiling.
  3. Daily spend budget       — total spend in a rolling 24h window is bounded.
  4. Per-vendor daily cap     — no single vendor can absorb the whole budget.
  5. Spend velocity           — bounded number of payments per rolling hour.
  6. Solvency rule            — never spend below a minimum cash reserve.
  7. ROI rule                 — never spend on a job whose projected margin is negative.

Rules 1-3 and 6-7 bound *how much* the agent can spend; rules 4 and 5 bound
*how it is distributed*. A single compromised or misbehaving vendor cannot
drain the day's budget on its own, and a fulfilment loop that starts paying in
a tight cycle trips the velocity rule long before it empties the treasury.

In production these checks would be enforced inside NVIDIA NemoClaw's sandbox so
the model literally cannot emit an out-of-policy payment. Here we implement the
same policy in plain Python so the behaviour is visible and testable.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .treasury import Treasury


@dataclass
class SpendPolicy:
    """Spend policy boundaries enforced by the guardrails.

    Attributes:
        vendor_allowlist: Approved vendor names that are permitted for spend.
        max_txn_cents: Maximum amount in cents permitted for a single transaction.
        daily_budget_cents: Maximum cumulative amount in cents permitted in a rolling 24h period.
        per_vendor_daily_cents: Default per-vendor ceiling for the same 24h window.
        vendor_daily_overrides: Per-vendor ceilings that differ from the default.
        max_txns_per_hour: Maximum number of payments in a rolling hour.
        min_reserve_cents: Minimum cash reserve in cents that must be kept in the treasury.
    """

    vendor_allowlist: tuple[str, ...] = (
        "nvidia-nemotron",  # inference / compute
        "market-data-api",  # data the analyst needs
        "web-search-api",
        "pdf-render-saas",
        "email-delivery-saas",
    )
    max_txn_cents: int = 5_000  # $50 per single payment
    daily_budget_cents: int = 25_000  # $250 / 24h
    per_vendor_daily_cents: int = 10_000  # $100 / vendor / 24h
    vendor_daily_overrides: dict[str, int] = field(default_factory=dict)
    max_txns_per_hour: int = 60  # a fulfilment loop gone wrong trips here
    min_reserve_cents: int = 2_000  # keep at least $20 cash

    def vendor_daily_cap_cents(self, vendor: str) -> int:
        """The rolling-24h ceiling for one vendor."""
        return self.vendor_daily_overrides.get(vendor, self.per_vendor_daily_cents)


#: Operators tune the spend policy here rather than in code, mirroring
#: ``.solvent/pricing_overrides.json`` on the pricing side.
POLICY_OVERRIDE_PATH = Path(".solvent/spend_policy.json")

_SCALAR_LIMITS = (
    "max_txn_cents",
    "daily_budget_cents",
    "per_vendor_daily_cents",
    "max_txns_per_hour",
    "min_reserve_cents",
)


def load_spend_policy(path: Path | None = None) -> SpendPolicy:
    """Build the spend policy, applying `.solvent/spend_policy.json` if present.

    Unknown keys and malformed values are ignored: a broken override file must
    never silently *widen* the policy, so anything that cannot be read falls
    back to the built-in defaults.
    """
    policy = SpendPolicy()
    override_path = path or POLICY_OVERRIDE_PATH
    if not override_path.is_file():
        return policy
    try:
        data = json.loads(override_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return policy
    if not isinstance(data, dict):
        return policy

    for key in _SCALAR_LIMITS:
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            setattr(policy, key, int(value))

    allowlist = data.get("vendor_allowlist")
    if isinstance(allowlist, list) and all(isinstance(v, str) for v in allowlist):
        policy.vendor_allowlist = tuple(allowlist)

    overrides = data.get("vendor_daily_overrides")
    if isinstance(overrides, dict):
        policy.vendor_daily_overrides = {
            k: int(v)
            for k, v in overrides.items()
            if (
                isinstance(k, str)
                and isinstance(v, (int, float))
                and not isinstance(v, bool)
                and v >= 0
            )
        }
    return policy


class GuardrailError(Exception):
    """Raised when an outbound spend request violates the spend policy."""


@dataclass
class Decision:
    """A structured, auditable spend-policy decision.

    Modelled on policy-engine decision objects (OPA/Cedar): rather than
    collapsing to a bare bool, every evaluation records *which* rule fired and
    *why*, plus the context it was evaluated against, so the audit trail and
    dashboard can explain a block.
    """

    allowed: bool
    rule: str | None = None  # machine-readable rule id that denied
    reason: str = "approved"  # human-readable explanation
    amount_cents: int = 0
    vendor: str = ""
    spent_24h_cents: int = 0
    balance_cents: int = 0
    vendor_spent_24h_cents: int = 0
    txns_last_hour: int = 0

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "rule": self.rule,
            "reason": self.reason,
            "amount_cents": self.amount_cents,
            "vendor": self.vendor,
            "spent_24h_cents": self.spent_24h_cents,
            "balance_cents": self.balance_cents,
            "vendor_spent_24h_cents": self.vendor_spent_24h_cents,
            "txns_last_hour": self.txns_last_hour,
        }


class Guardrails:
    """Enforces deterministic spend safety policies on all transaction requests."""

    def __init__(self, treasury: Treasury | Any, policy: SpendPolicy | None = None) -> None:
        """Initialize the guardrails with a treasury instance and a spend policy.

        Args:
            treasury: The Treasury instance or mock tracking the agent's ledger.
            policy: The SpendPolicy rules to enforce. Defaults to the built-in
                policy with any `.solvent/spend_policy.json` overrides applied.
        """
        self.t = treasury
        self.policy: SpendPolicy = policy or load_spend_policy()

    def _spent_last_24h(self, vendor: str | None = None) -> int:
        """Sum of expenses in the last 24 hours, optionally for one vendor only.

        Args:
            vendor: Restrict the total to this vendor. ``None`` totals every
                vendor (and un-attributed expenses such as refunds).

        Returns:
            The total spend in cents.
        """
        cutoff = time.time() - 86_400
        return sum(
            e.amount_cents
            for e in self.t.entries
            if e.kind == "expense" and e.ts >= cutoff and (vendor is None or e.vendor == vendor)
        )

    def _txns_last_hour(self) -> int:
        """How many payments the agent has made in the last rolling hour."""
        cutoff = time.time() - 3_600
        return sum(1 for e in self.t.entries if e.kind == "expense" and e.ts >= cutoff)

    def vendor_exposure(self) -> list[dict[str, Any]]:
        """Per-vendor spend against its 24h cap, worst headroom first.

        The operator-facing view of rule 4: who the agent has been paying, and
        how much room each vendor has left before the cap stops it.
        """
        spent_by_vendor = {v: self._spent_last_24h(v) for v in self.policy.vendor_allowlist}
        rows = []
        for vendor, spent in spent_by_vendor.items():
            cap = self.policy.vendor_daily_cap_cents(vendor)
            rows.append(
                {
                    "vendor": vendor,
                    "spent_24h_cents": spent,
                    "cap_cents": cap,
                    "headroom_cents": max(cap - spent, 0),
                    "used_pct": round(100 * spent / cap, 1) if cap else 0.0,
                }
            )
        rows.sort(key=lambda r: (-r["used_pct"], r["vendor"]))
        return rows

    def evaluate(
        self,
        amount_cents: int,
        vendor: str,
        projected_job_margin_cents: int | None = None,
    ) -> Decision:
        """Evaluate a proposed spend against every policy rule.

        Rules are checked in priority order and the *first* violation is
        returned. The treasury is read once so the decision reflects a single
        consistent snapshot. Returns a :class:`Decision` either way (never
        raises); use :meth:`check_spend` for the raising variant.
        """
        spent_24h = self._spent_last_24h()
        vendor_spent_24h = self._spent_last_24h(vendor)
        txns_last_hour = self._txns_last_hour()
        balance = self.t.balance_cents()
        ctx: dict[str, Any] = {
            "amount_cents": amount_cents,
            "vendor": vendor,
            "spent_24h_cents": spent_24h,
            "balance_cents": balance,
            "vendor_spent_24h_cents": vendor_spent_24h,
            "txns_last_hour": txns_last_hour,
        }

        # Amount is always positive; kind carries the sign on the ledger. A
        # zero/negative "spend" would silently *credit* the treasury if booked,
        # so reject it before any other rule can approve it.
        if amount_cents <= 0:
            return Decision(
                False,
                "positive_amount",
                f"amount must be positive, got {amount_cents}c",
                **ctx,
            )

        if vendor not in self.policy.vendor_allowlist:
            return Decision(False, "vendor_allowlist", f"vendor '{vendor}' not on allowlist", **ctx)

        if amount_cents > self.policy.max_txn_cents:
            return Decision(
                False,
                "max_txn_cap",
                f"txn {amount_cents}c exceeds per-transaction cap {self.policy.max_txn_cents}c",
                **ctx,
            )

        if spent_24h + amount_cents > self.policy.daily_budget_cents:
            return Decision(False, "daily_budget", "would exceed 24h spend budget", **ctx)

        vendor_cap = self.policy.vendor_daily_cap_cents(vendor)
        if vendor_spent_24h + amount_cents > vendor_cap:
            return Decision(
                False,
                "vendor_daily_budget",
                f"would exceed 24h budget of {vendor_cap}c for vendor '{vendor}'",
                **ctx,
            )

        if txns_last_hour >= self.policy.max_txns_per_hour:
            return Decision(
                False,
                "spend_velocity",
                f"{txns_last_hour} payments in the last hour reaches the cap "
                f"of {self.policy.max_txns_per_hour}/h",
                **ctx,
            )

        if balance - amount_cents < self.policy.min_reserve_cents:
            return Decision(False, "min_reserve", "would breach minimum cash reserve", **ctx)

        if projected_job_margin_cents is not None and projected_job_margin_cents <= 0:
            return Decision(
                False,
                "roi",
                "job projected to be unprofitable; refusing to spend",
                **ctx,
            )

        return Decision(True, None, "approved", **ctx)

    def check_spend(
        self,
        amount_cents: int,
        vendor: str,
        projected_job_margin_cents: int | None = None,
    ) -> None:
        """Raise GuardrailError if the spend violates policy. Returns None if OK.

        Args:
            amount_cents: The proposed transaction amount in cents.
            vendor: The vendor name requested for payment.
            projected_job_margin_cents: The projected profit margin of the job in cents.

        Raises:
            GuardrailError: If any of the spend policies are violated.
        """
        decision = self.evaluate(amount_cents, vendor, projected_job_margin_cents)
        if not decision.allowed:
            raise GuardrailError(decision.reason)

    def approve(
        self,
        amount_cents: int,
        vendor: str,
        projected_job_margin_cents: int | None = None,
    ) -> bool:
        """Screen an outbound payment and return True if approved, False if blocked.

        Args:
            amount_cents: The transaction amount in cents.
            vendor: The vendor name.
            projected_job_margin_cents: The projected profit margin of the job in cents.

        Returns:
            True if the payment is approved, False otherwise.
        """
        return self.evaluate(amount_cents, vendor, projected_job_margin_cents).allowed
