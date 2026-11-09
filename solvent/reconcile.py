"""Reconcile Stripe payments against the SOLVENT treasury ledger."""

from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from .treasury import REFUND_VENDOR, Treasury

try:
    import stripe as stripe_sdk  # type: ignore[import-not-found]

    _HAS_STRIPE = True
except Exception:
    stripe_sdk = None
    _HAS_STRIPE = False


def reconcile(treasury: Treasury | None = None, since_days: int = 7) -> dict:
    treasury = treasury or Treasury()
    # Count, don't just collect: the same PaymentIntent or Checkout Session
    # booked as revenue twice is double-counted cash, which is exactly the
    # drift reconciliation exists to catch.
    ref_counts: Counter[str] = Counter()
    session_counts: Counter[str] = Counter()
    refund_counts: Counter[str] = Counter()
    ledger_refunds: Counter[str] = Counter()
    with treasury.lock():
        entries = treasury.entries
        pending = treasury.pending_refunds()
    for e in entries:
        if e.kind == "revenue":
            if e.stripe_ref:
                ref_counts[e.stripe_ref] += 1
            if e.stripe_session_id:
                session_counts[e.stripe_session_id] += 1
        if (e.kind == "expense" and e.vendor == REFUND_VENDOR and e.stripe_ref
                and e.stripe_ref.startswith("re_")):
            ledger_refunds[e.stripe_ref] += e.amount_cents
            refund_counts[e.stripe_ref] += 1

    ledger_refs = set(ref_counts)
    duplicates = sorted(
        {ident for ident, n in ref_counts.items() if n > 1}
        | {ident for ident, n in session_counts.items() if n > 1}
    )

    report = {
        "ledger_revenue_count": len(ledger_refs),
        "unmatched_stripe": [],
        # Ledger-only mode lists every booked ref so operators can see the
        # full revenue surface without a Stripe round-trip.
        "unmatched_ledger": sorted(ledger_refs),
        "duplicates": duplicates,
        "duplicate_refunds": sorted(k for k, n in refund_counts.items() if n > 1),
        # Duplicates are detectable from the ledger alone, so they count as
        # drift even when Stripe is unreachable.
        "pending_refunds": pending,
        "unmatched_stripe_refunds": [],
        "unmatched_ledger_refunds": [],
        "refund_amount_mismatches": [],
        "pending_stripe_refunds": [],
        "drift": bool(duplicates or pending or any(n > 1 for n in refund_counts.values())),
    }

    key = os.environ.get("STRIPE_API_KEY", "")
    # Live keys (standard sk_live_ and restricted rk_live_) are refused unconditionally,
    # matching SOLVENT's invariant that no code path may operate against a live Stripe account.
    if not key or not _HAS_STRIPE or key.startswith("sk_live_") or key.startswith("rk_live_"):
        report["mode"] = "ledger_only"
        return report

    stripe_sdk.api_key = key
    since = int((datetime.now(timezone.utc) - timedelta(days=since_days)).timestamp())
    # Stripe only returns PaymentIntents in the lookback window. Comparing that
    # set to *all-time* ledger refs (including Nano block hashes stored in
    # stripe_ref) produced permanent false drift. Scope the ledger side to the
    # same window and to Stripe-shaped PaymentIntent ids (pi_...).
    window_pi_refs = {
        e.stripe_ref
        for e in entries
        if e.kind == "revenue"
        and e.stripe_ref
        and str(e.stripe_ref).startswith("pi_")
        and e.ts >= since
    }
    stripe_pis: set[str] = set()
    window_refund_refs = {e.stripe_ref for e in entries if e.ts >= since
                          and e.kind == "expense" and e.vendor == REFUND_VENDOR
                          and e.stripe_ref and e.stripe_ref.startswith("re_")}
    stripe_refunds: dict[str, int] = {}
    seen_refunds: set[str] = set()

    def observe_refund(refund):
        seen_refunds.add(refund.id)
        if refund.status == "succeeded":
            stripe_refunds[refund.id] = refund.amount
        elif refund.status not in ("failed", "canceled"):
            report["pending_stripe_refunds"].append(refund.id)

    try:
        for pi in stripe_sdk.PaymentIntent.list(
            created={"gte": since}, limit=100
        ).auto_paging_iter():
            if pi.status == "succeeded":
                stripe_pis.add(pi.id)
        # PaymentIntents remain succeeded after a refund. Comparing only their
        # IDs cannot detect money that left Stripe without a ledger refund.
        for refund in stripe_sdk.Refund.list(
            created={"gte": since}, limit=100
        ).auto_paging_iter():
            observe_refund(refund)
        # Recovery can book today a refund created before the lookback. Fetch
        # those exact ledger IDs rather than misreporting historical success as
        # missing. Conversely, recent provider refunds match all ledger IDs.
        for refund_id in sorted(window_refund_refs - seen_refunds):
            refund = stripe_sdk.Refund.retrieve(refund_id)
            if refund.id != refund_id:
                raise ValueError("refund lookup returned an unexpected ID")
            observe_refund(refund)
    except Exception as exc:
        report["stripe_error"] = str(exc)
        # A failed Stripe fetch is never a completed full reconcile: keep the
        # report honest and always carry a mode so callers never KeyError.
        report["mode"] = "ledger_only"
        return report

    unmatched_stripe = stripe_pis - window_pi_refs
    unmatched_ledger = window_pi_refs - stripe_pis
    report["unmatched_stripe"] = sorted(unmatched_stripe)
    report["unmatched_ledger"] = sorted(unmatched_ledger)
    report["unmatched_stripe_refunds"] = sorted(stripe_refunds.keys() - ledger_refunds.keys())
    report["unmatched_ledger_refunds"] = sorted(window_refund_refs - stripe_refunds.keys())
    report["refund_amount_mismatches"] = sorted(
        key for key in stripe_refunds.keys() & ledger_refunds.keys()
        if stripe_refunds[key] != ledger_refunds[key]
    )
    report["pending_stripe_refunds"].sort()
    report["drift"] = bool(report["drift"] or unmatched_stripe or unmatched_ledger
                           or report["unmatched_stripe_refunds"]
                           or report["unmatched_ledger_refunds"]
                           or report["refund_amount_mismatches"]
                           or report["pending_stripe_refunds"])
    report["mode"] = "full"
    return report


def main():
    import argparse

    parser = argparse.ArgumentParser(description="SOLVENT Stripe reconciliation")
    parser.add_argument("--since", default="7d", help="lookback e.g. 7d")
    parser.add_argument("--resolve-refund", nargs=2, metavar=("JOB_ID", "REFUND_ID"),
                        help="verify an existing Stripe refund and settle its local pending record")
    args = parser.parse_args()
    if args.resolve_refund:
        try:
            print(resolve_refund(Treasury(), *args.resolve_refund))
        except Exception as exc:
            parser.exit(1, f"Refund remains unresolved: {exc}\n")
        return
    days = int(args.since.replace("d", "")) if args.since.endswith("d") else 7
    report = reconcile(since_days=days)
    print(report)
    if report.get("drift") or report.get("stripe_error"):
        sys.exit(1)


def resolve_refund(treasury: Treasury, job_id: str, refund_id: str) -> dict:
    """Read provider evidence, then settle local state. Never submit a refund.

    Requires an explicit refund ID, not an automatic retry of an old
    idempotency key (which a provider may have expired).
    """
    pending = treasury.pending_refunds(job_id)
    if len(pending) != 1:
        raise ValueError("job must have exactly one pending refund")
    key = os.environ.get("STRIPE_API_KEY", "")
    if not _HAS_STRIPE or not key.startswith(("sk_test_", "rk_test_")):
        raise ValueError("a Stripe test key and Stripe SDK are required")
    if not refund_id.startswith("re_"):
        raise ValueError("expected a Stripe refund ID (re_...)")
    stripe_sdk.api_key = key
    refund = stripe_sdk.Refund.retrieve(refund_id)
    intent = pending[0]
    if (refund.id != refund_id or refund.status != "succeeded"
            or refund.payment_intent != intent["payment_ref"]
            or refund.amount != intent["amount_cents"] or refund.currency != "usd"):
        raise ValueError("provider has not confirmed this job's exact full USD refund")
    treasury.complete_refund(job_id, intent["payment_ref"], refund_id)
    return {"job_id": job_id, "refund_id": refund_id, "status": "refunded",
            "amount_cents": intent["amount_cents"]}


if __name__ == "__main__":
    main()
