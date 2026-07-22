"""Reconcile Stripe payments against the SOLVENT treasury ledger."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from .treasury import Treasury

try:
    import stripe as stripe_sdk
    _HAS_STRIPE = True
except Exception:
    stripe_sdk = None
    _HAS_STRIPE = False


def reconcile(treasury: Treasury | None = None, since_days: int = 7) -> dict:
    treasury = treasury or Treasury()
    ledger_refs = set()
    session_refs = set()
    for e in treasury.entries:
        if e.kind == "revenue":
            if e.stripe_ref:
                ledger_refs.add(e.stripe_ref)
            if e.stripe_session_id:
                session_refs.add(e.stripe_session_id)

    report = {
        "ledger_revenue_count": len(ledger_refs),
        "unmatched_stripe": [],
        "unmatched_ledger": list(ledger_refs),
        "duplicates": [],
        "drift": False,
    }

    key = os.environ.get("STRIPE_API_KEY", "")
    # Live keys (standard sk_live_ and restricted rk_live_) are refused unconditionally,
    # matching SOLVENT's invariant that no code path may operate against a live Stripe account.
    if not key or not _HAS_STRIPE or key.startswith("sk_live_") or key.startswith("rk_live_"):
        report["mode"] = "ledger_only"
        return report

    stripe_sdk.api_key = key
    since = int((datetime.now(timezone.utc) - timedelta(days=since_days)).timestamp())
    stripe_pis: set[str] = set()
    try:
        for pi in stripe_sdk.PaymentIntent.list(created={"gte": since}, limit=100).auto_paging_iter():
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
    report["drift"] = bool(unmatched_stripe or unmatched_ledger)
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
