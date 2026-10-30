"""
guardrails.py — the NemoClaw-style spend safety layer.

Every time SOLVENT wants to move money, the request passes through here first.
This is the "run agents safely" piece: an autonomous agent with a Stripe key is
dangerous unless its spending is bounded. These guardrails are deterministic,
auditable, and run BEFORE any Stripe call.

Rules enforced:
  0. Positive amount         — never book a zero or negative spend (kind carries the sign).
  1. Vendor allowlist        — money can only go to pre-approved vendors.
  2. Per-transaction cap      — no single spend over a ceiling.
  3. Daily spend budget       — total spend in a rolling 24h window is bounded.
  4. Per-vendor daily cap     — no single vendor can absorb the whole budget.
  5. Spend velocity           — bounded number of payments per rolling hour.
  6. Solvency rule            — never spend below a minimum cash reserve.
  7. ROI rule                 — never spend on a job whose projected margin is negative.
  8. Per-job budget envelope  — each job has a hard spend cap; once used up, it fails closed.
  9. Human approval hook      — a spend above a threshold is held for a human, never auto-paid.

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
import logging
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .paths import config_path
from .treasury import REFUND_VENDOR

if TYPE_CHECKING:
    from .treasury import Treasury

log = logging.getLogger(__name__)

#: Spends evaluated without a job id share this one budget envelope.
UNSCOPED_JOB = "unscoped"


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
    # Hard cap on the total spend attributed to one job. 0 disables the envelope.
    job_budget_cents: int = 15_000  # $150 / job
    # Spends strictly above this amount need a human decision. None disables it.
    approval_threshold_cents: int | None = None
    # Human-readable notes about overrides that loosened or disabled a limit.
    warnings: list[str] = field(default_factory=list, compare=False)

    def vendor_daily_cap_cents(self, vendor: str) -> int:
        """The rolling-24h ceiling for one vendor."""
        return self.vendor_daily_overrides.get(vendor, self.per_vendor_daily_cents)


_SCALAR_LIMITS = (
    "max_txn_cents",
    "daily_budget_cents",
    "per_vendor_daily_cents",
    "max_txns_per_hour",
    "min_reserve_cents",
    "job_budget_cents",
)

ENV_JOB_BUDGET = "SOLVENT_JOB_BUDGET_CENTS"
ENV_APPROVAL_THRESHOLD = "SOLVENT_APPROVAL_THRESHOLD_CENTS"


def _env_cents(name: str) -> int | None:
    """A non-negative integer-cents value from the environment, else None."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _is_finite_number(value: object) -> bool:
    """True for a real int/float that is not a bool, NaN or +/-Infinity.

    JSON accepts ``Infinity``, ``NaN`` and overflowing literals such as
    ``1e400``; ``int()`` on those raises, which used to crash every command
    that loads the policy and print a traceback instead of falling back.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return isinstance(value, int) or math.isfinite(value)


def load_spend_policy(path: Path | None = None) -> SpendPolicy:
    """Build the spend policy, applying `.solvent/spend_policy.json` if present.

    Unknown keys and malformed values are ignored: a broken override file must
    never silently *widen* the policy, so anything that cannot be read falls
    back to the built-in defaults.

    Environment variables may only *tighten* the job budget and the approval
    threshold. An env value that would loosen or disable either one is ignored
    and recorded as a warning. Loosening or disabling needs an explicit
    setting in the policy file, and that is warned about too.
    """
    policy = SpendPolicy()
    default = SpendPolicy()
    override_path = path if path is not None else config_path("spend_policy.json")
    if override_path.is_file():
        _apply_policy_file(policy, override_path)

    # Policy-file settings that loosen the built-in envelope are allowed, but visible.
    if policy.job_budget_cents == 0:
        policy.warnings.append(
            f"job budget DISABLED by {override_path.name} (job_budget_cents=0); "
            "per-job spend is uncapped"
        )
    elif policy.job_budget_cents > default.job_budget_cents:
        policy.warnings.append(
            f"job budget loosened by {override_path.name} to {policy.job_budget_cents}c "
            f"(default {default.job_budget_cents}c)"
        )

    job_budget = _env_cents(ENV_JOB_BUDGET)
    if job_budget is not None:
        current = policy.job_budget_cents
        tightens = job_budget > 0 and (current == 0 or job_budget <= current)
        if tightens:
            policy.job_budget_cents = job_budget
        else:
            policy.warnings.append(
                f"ignored {ENV_JOB_BUDGET}={job_budget}: env vars may only tighten the job "
                f"budget ({current}c); set job_budget_cents in the policy file to loosen it"
            )

    threshold = _env_cents(ENV_APPROVAL_THRESHOLD)
    if threshold is not None:
        current_t = policy.approval_threshold_cents
        if current_t is None or threshold <= current_t:
            policy.approval_threshold_cents = threshold
        else:
            policy.warnings.append(
                f"ignored {ENV_APPROVAL_THRESHOLD}={threshold}: env vars may only lower the "
                f"approval threshold ({current_t}c); set approval_threshold_cents in the "
                "policy file to raise it"
            )

    for warning in policy.warnings:
        log.warning("spend policy: %s", warning)
    return policy


def _apply_policy_file(policy: SpendPolicy, override_path: Path) -> SpendPolicy:
    try:
        data = json.loads(override_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        return policy
    if not isinstance(data, dict):
        return policy

    for key in _SCALAR_LIMITS:
        value = data.get(key)
        if _is_finite_number(value) and value >= 0:
            setattr(policy, key, int(value))

    allowlist = data.get("vendor_allowlist")
    if isinstance(allowlist, list) and all(isinstance(v, str) for v in allowlist):
        policy.vendor_allowlist = tuple(allowlist)

    overrides = data.get("vendor_daily_overrides")
    if isinstance(overrides, dict):
        policy.vendor_daily_overrides = {
            k: int(v)
            for k, v in overrides.items()
            if isinstance(k, str) and _is_finite_number(v) and v >= 0
        }

    threshold = data.get("approval_threshold_cents")
    if _is_finite_number(threshold) and threshold >= 0:
        policy.approval_threshold_cents = int(threshold)
    return policy


ApprovalVerdict = Literal["approved", "denied", "held"]


@dataclass(frozen=True)
class ApprovalRequest:
    """What a human approver is asked to decide on."""

    amount_cents: int
    vendor: str
    job_id: str | None
    threshold_cents: int


#: An approver gets the request and returns "approved", "denied" or "held".
Approver = Callable[[ApprovalRequest], ApprovalVerdict]


def hold_for_approval(request: ApprovalRequest) -> ApprovalVerdict:
    """The default approver: never auto-approve, keep the spend pending."""
    return "held"


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
    job_id: str | None = None
    job_spent_cents: int = 0
    pending_approval: bool = False

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
            "job_id": self.job_id,
            "job_spent_cents": self.job_spent_cents,
            "pending_approval": self.pending_approval,
        }


class Guardrails:
    """Enforces deterministic spend safety policies on all transaction requests."""

    def __init__(
        self,
        treasury: Treasury | Any,
        policy: SpendPolicy | None = None,
        approver: Approver | None = None,
    ) -> None:
        """Initialize the guardrails with a treasury instance and a spend policy.

        Args:
            treasury: The Treasury instance or mock tracking the agent's ledger.
            policy: The SpendPolicy rules to enforce. Defaults to the built-in
                policy with any `.solvent/spend_policy.json` overrides applied.
            approver: Called for spends above ``approval_threshold_cents``.
                Defaults to :func:`hold_for_approval`, which never approves.
        """
        self.t = treasury
        self.policy: SpendPolicy = policy or load_spend_policy()
        self.approver: Approver = approver or hold_for_approval

    def _job_spent(self, job_id: str | None) -> int:
        """All operating spend ever attributed to one job (refunds excluded).

        A spend with no job id is not exempt: every unscoped spend shares one
        bucket, so ``job_id=None`` totals all expenses that carry no job id.
        """
        return sum(
            e.amount_cents
            for e in self.t.entries
            if e.kind == "expense" and e.job_id == job_id and e.vendor != REFUND_VENDOR
        )

    def _spent_last_24h(self, vendor: str | None = None) -> int:
        """Sum of expenses in the last 24 hours, optionally for one vendor only.

        Money refunded to a customer is an outflow but not operating spend, so
        it is left out of the total: a run of refunds must not exhaust the
        budget the agent needs to fulfil the work it *has* been paid for. The
        cash reserve rule still accounts for it, because it reads the balance.

        Args:
            vendor: Restrict the total to this vendor. ``None`` totals every
                vendor payment.

        Returns:
            The total spend in cents.
        """
        cutoff = time.time() - 86_400
        return sum(
            e.amount_cents
            for e in self.t.entries
            if e.kind == "expense"
            and e.ts >= cutoff
            and (e.vendor == vendor if vendor is not None else e.vendor != REFUND_VENDOR)
        )

    def _txns_last_hour(self) -> int:
        """How many payments the agent has made in the last rolling hour."""
        cutoff = time.time() - 3_600
        return sum(
            1
            for e in self.t.entries
            if e.kind == "expense" and e.ts >= cutoff and e.vendor != REFUND_VENDOR
        )

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
        job_id: str | None = None,
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
        job_spent = self._job_spent(job_id)
        ctx: dict[str, Any] = {
            "job_id": job_id,
            "job_spent_cents": job_spent,
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

        budget = self.policy.job_budget_cents
        if budget > 0 and job_spent + amount_cents > budget:
            return Decision(
                False,
                "job_budget",
                f"job '{job_id if job_id is not None else UNSCOPED_JOB}' budget exhausted: {job_spent}c spent + {amount_cents}c "
                f"would exceed its {budget}c envelope",
                **ctx,
            )

        threshold = self.policy.approval_threshold_cents
        if threshold is not None and amount_cents > threshold:
            request = ApprovalRequest(amount_cents, vendor, job_id, threshold)
            try:
                verdict = self.approver(request)
            except Exception:  # a broken approver must fail closed
                verdict = "held"
            if verdict == "denied":
                return Decision(
                    False,
                    "approval_denied",
                    f"spend of {amount_cents}c denied by human approver",
                    **ctx,
                )
            if verdict != "approved":
                return Decision(
                    False,
                    "approval_pending",
                    f"spend of {amount_cents}c is above the {threshold}c approval "
                    "threshold; held pending human approval",
                    pending_approval=True,
                    **ctx,
                )

        return Decision(True, None, "approved", **ctx)

    def check_spend(
        self,
        amount_cents: int,
        vendor: str,
        projected_job_margin_cents: int | None = None,
        job_id: str | None = None,
    ) -> None:
        """Raise GuardrailError if the spend violates policy. Returns None if OK.

        Args:
            amount_cents: The proposed transaction amount in cents.
            vendor: The vendor name requested for payment.
            projected_job_margin_cents: The projected profit margin of the job in cents.

        Raises:
            GuardrailError: If any of the spend policies are violated.
        """
        decision = self.evaluate(amount_cents, vendor, projected_job_margin_cents, job_id=job_id)
        if not decision.allowed:
            raise GuardrailError(decision.reason)

    def approve(
        self,
        amount_cents: int,
        vendor: str,
        projected_job_margin_cents: int | None = None,
        job_id: str | None = None,
    ) -> bool:
        """Screen an outbound payment and return True if approved, False if blocked.

        Args:
            amount_cents: The transaction amount in cents.
            vendor: The vendor name.
            projected_job_margin_cents: The projected profit margin of the job in cents.

        Returns:
            True if the payment is approved, False otherwise.
        """
        return self.evaluate(
            amount_cents, vendor, projected_job_margin_cents, job_id=job_id
        ).allowed
