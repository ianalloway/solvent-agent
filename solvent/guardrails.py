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
  4. Solvency rule            — never spend below a minimum cash reserve.
  5. ROI rule                 — never spend on a job whose projected margin is negative.

In production these checks would be enforced inside NVIDIA NemoClaw's sandbox so
the model literally cannot emit an out-of-policy payment. Here we implement the
same policy in plain Python so the behaviour is visible and testable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
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
        min_reserve_cents: Minimum cash reserve in cents that must be kept in the treasury.
    """
    vendor_allowlist: tuple[str, ...] = (
        "nvidia-nemotron",      # inference / compute
        "market-data-api",      # data the analyst needs
        "web-search-api",
        "pdf-render-saas",
        "email-delivery-saas",
    )
    max_txn_cents: int = 5_000          # $50 per single payment
    daily_budget_cents: int = 25_000    # $250 / 24h
    min_reserve_cents: int = 2_000      # keep at least $20 cash


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
    rule: str | None = None          # machine-readable rule id that denied
    reason: str = "approved"         # human-readable explanation
    amount_cents: int = 0
    vendor: str = ""
    spent_24h_cents: int = 0
    balance_cents: int = 0

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "rule": self.rule,
            "reason": self.reason,
            "amount_cents": self.amount_cents,
            "vendor": self.vendor,
            "spent_24h_cents": self.spent_24h_cents,
            "balance_cents": self.balance_cents,
        }


class Guardrails:
    """Enforces deterministic spend safety policies on all transaction requests."""

    def __init__(self, treasury: Treasury | Any, policy: SpendPolicy | None = None) -> None:
        """Initialize the guardrails with a treasury instance and a spend policy.

        Args:
            treasury: The Treasury instance or mock tracking the agent's ledger.
            policy: The SpendPolicy rules to enforce. Defaults to default SpendPolicy.
        """
        self.t = treasury
        self.policy: SpendPolicy = policy or SpendPolicy()

    def _spent_last_24h(self) -> int:
        """Calculate the sum of all expenses recorded in the last 24 hours.

        Returns:
            The total spend in cents.
        """
        cutoff = time.time() - 86_400
        return sum(
            e.amount_cents for e in self.t.entries
            if e.kind == "expense" and e.ts >= cutoff
        )

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
        balance = self.t.balance_cents()
        ctx = {
            "amount_cents": amount_cents,
            "vendor": vendor,
            "spent_24h_cents": spent_24h,
            "balance_cents": balance,
        }

        if vendor not in self.policy.vendor_allowlist:
            return Decision(False, "vendor_allowlist", f"vendor '{vendor}' not on allowlist", **ctx)

        if amount_cents > self.policy.max_txn_cents:
            return Decision(
                False, "max_txn_cap",
                f"txn {amount_cents}c exceeds per-transaction cap {self.policy.max_txn_cents}c",
                **ctx,
            )

        if spent_24h + amount_cents > self.policy.daily_budget_cents:
            return Decision(False, "daily_budget", "would exceed 24h spend budget", **ctx)

        if balance - amount_cents < self.policy.min_reserve_cents:
            return Decision(False, "min_reserve", "would breach minimum cash reserve", **ctx)

        if projected_job_margin_cents is not None and projected_job_margin_cents <= 0:
            return Decision(
                False, "roi", "job projected to be unprofitable; refusing to spend", **ctx
            )

        return Decision(True, None, "approved", **ctx)

    def check_spend(self, amount_cents: int, vendor: str, projected_job_margin_cents: int | None = None) -> None:
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

    def approve(self, amount_cents: int, vendor: str, projected_job_margin_cents: int | None = None) -> bool:
        """Screen an outbound payment and return True if approved, False if blocked.

        Args:
            amount_cents: The transaction amount in cents.
            vendor: The vendor name.
            projected_job_margin_cents: The projected profit margin of the job in cents.

        Returns:
            True if the payment is approved, False otherwise.
        """
        return self.evaluate(amount_cents, vendor, projected_job_margin_cents).allowed

