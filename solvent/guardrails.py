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
from typing import TYPE_CHECKING, Any, Tuple

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
    vendor_allowlist: Tuple[str, ...] = (
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
    pass


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

    def check_spend(self, amount_cents: int, vendor: str, projected_job_margin_cents: int | None = None) -> None:
        """Raise GuardrailError if the spend violates policy. Returns None if OK.

        Args:
            amount_cents: The proposed transaction amount in cents.
            vendor: The vendor name requested for payment.
            projected_job_margin_cents: The projected profit margin of the job in cents.

        Raises:
            GuardrailError: If any of the spend policies are violated.
        """
        if vendor not in self.policy.vendor_allowlist:
            raise GuardrailError(f"vendor '{vendor}' not on allowlist")

        if amount_cents > self.policy.max_txn_cents:
            raise GuardrailError(
                f"txn {amount_cents}c exceeds per-transaction cap {self.policy.max_txn_cents}c"
            )

        if self._spent_last_24h() + amount_cents > self.policy.daily_budget_cents:
            raise GuardrailError("would exceed 24h spend budget")

        if self.t.balance_cents() - amount_cents < self.policy.min_reserve_cents:
            raise GuardrailError("would breach minimum cash reserve")

        if projected_job_margin_cents is not None and projected_job_margin_cents <= 0:
            raise GuardrailError("job projected to be unprofitable; refusing to spend")

    def approve(self, amount_cents: int, vendor: str, projected_job_margin_cents: int | None = None) -> bool:
        """Screen an outbound payment and return True if approved, False if blocked.

        Args:
            amount_cents: The transaction amount in cents.
            vendor: The vendor name.
            projected_job_margin_cents: The projected profit margin of the job in cents.

        Returns:
            True if the payment is approved, False otherwise.
        """
        try:
            self.check_spend(amount_cents, vendor, projected_job_margin_cents)
            return True
        except GuardrailError:
            return False

