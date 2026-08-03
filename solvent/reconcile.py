"""Reconcile Stripe payments against the SOLVENT treasury ledger."""

from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from .treasury import Treasury

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
    for e in treasury.entries:
        if e.kind == "revenue":
            if e.stripe_ref:
                ref_counts[e.stripe_ref] += 1
            if e.stripe_session_id:
                session_counts[e.stripe_session_id] += 1

    ledger_refs = set(ref_counts)
    duplicates = sorted(
        {ident for ident, n in ref_counts.items() if n > 1}
        | {ident for ident, n in session_counts.items() if n > 1}
    )

    report = {
        "ledger_revenue_count": len(ledger_refs),
        "unmatched_stripe": [],
        "unmatched_ledger": sorted(ledger_refs),
        "duplicates": duplicates,
        # Duplicates are detectable from the ledger alone, so they count as
        # drift even when Stripe is unreachable.
        "drift": bool(duplicates),
    }

    key = os.environ.get("STRIPE_API_KEY", "")
    # Live keys (standard sk_live_ and restricted rk_live_) are refused unconditionally,
    # matching SOLVENT's invariant that no code path may operate against a live Stripe account.
    if (
        not key
        or not _HAS_STRIPE
        or key.startswith("sk_live_")
        or key.startswith("rk_live_")
    ):
        report["mode"] = "ledger_only"
        return report

    stripe_sdk.api_key = key
    since = int((datetime.now(timezone.utc) - timedelta(days=since_days)).timestamp())
    stripe_pis: set[str] = set()
    try:
        for pi in stripe_sdk.PaymentIntent.list(
            created={"gte": since}, limit=100
        ).auto_paging_iter():
            if pi.status == "succeeded":
                stripe_pis.add(pi.id)
    except Exception as exc:
        report["stripe_error"] = str(exc)
        # A failed Stripe fetch is never a completed full reconcile: keep the
        # report honest and always carry a mode so callers never KeyError.
        report["mode"] = "ledger_only"
        return report

    unmatched_stripe = stripe_pis - ledger_refs
    unmatched_ledger = ledger_refs - stripe_pis
    report["unmatched_stripe"] = sorted(unmatched_stripe)
    report["unmatched_ledger"] = sorted(unmatched_ledger)
    report["drift"] = bool(unmatched_stripe or unmatched_ledger or duplicates)
    report["mode"] = "full"
    return report


def main():
    import argparse

    parser = argparse.ArgumentParser(description="SOLVENT Stripe reconciliation")
    parser.add_argument("--since", default="7d", help="lookback e.g. 7d")
    args = parser.parse_args()
    days = int(args.since.replace("d", "")) if args.since.endswith("d") else 7
    report = reconcile(since_days=days)
    print(report)
    if report.get("drift"):
        sys.exit(1)


if __name__ == "__main__":
    main()
