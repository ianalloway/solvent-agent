"""
quote_cmd.py — `solvent quote`: price a job without running it.

A dry run of the margin gate. Nothing is written to the treasury and no
Stripe call is made: the agent simply says what it would charge, what the job
would cost it, and — when it would decline — the counter-offer it would make
instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from .pricing import PricingPolicy, Quote, quote
from .products import BY_SLUG, UnknownProduct, get_product
from .treasury import fmt

_LABELS = {
    "nemotron_inference": "Nemotron inference",
    "market_data": "Market data",
    "web_search": "Web search",
    "pdf_render": "PDF render",
    "email_send": "Email delivery",
}


def build_job(
    topic: str,
    budget_cents: int,
    *,
    est_tokens: int = 8_000,
    market_data_calls: int = 2,
    web_search_calls: int = 6,
) -> dict[str, Any]:
    """The job dict the margin gate scores, from CLI arguments."""
    return {
        "id": "dry-run",
        "topic": topic,
        "budget_cents": budget_cents,
        "est_tokens": est_tokens,
        "market_data_calls": market_data_calls,
        "web_search_calls": web_search_calls,
    }


def format_quote(job: dict[str, Any], q: Quote, policy: PricingPolicy) -> str:
    """Render a quote as the operator-facing block `solvent quote` prints."""
    lines = [
        "",
        f"  {job['topic'][:66]}",
        f"  {'─' * 66}",
        f"  Customer budget      {fmt(job['budget_cents'])}",
        "",
        "  Estimated cost of fulfilment",
    ]
    for key, cents in q.cost_breakdown.items():
        lines.append(f"    {_LABELS.get(key, key):<22}{fmt(cents):>10}")
    lines += [
        f"    {'total':<22}{fmt(q.est_cost_cents):>10}",
        "",
        f"  Projected margin     {fmt(q.margin_cents)} ({q.margin_pct}%)"
        f"   floor {policy.margin_floor_pct}%",
        f"  Verdict              {'ACCEPT' if q.accept else 'DECLINE'} — {q.reason}",
    ]
    if q.counter_offer:
        offer = q.counter_offer
        lines += [
            "",
            f"  Counter-offer        {fmt(offer['price_cents'])} at {offer['margin_pct']}% margin",
            f"    {offer['message']}",
        ]
        if offer.get("scope_changes"):
            for dimension, value in offer["scope_changes"].items():
                lines.append(f"    {dimension:<22}{job.get(dimension):>8,} → {value:,}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="solvent quote",
        description="Price a job through the margin gate without running it.",
    )
    parser.add_argument("topic", help="what the customer is asking for")
    parser.add_argument(
        "--budget",
        type=float,
        help="customer budget in USD (e.g. --budget 49); omit when using --product",
    )
    parser.add_argument(
        "--product",
        choices=sorted(BY_SLUG),
        help="price a catalogue product instead of a bespoke budget",
    )
    parser.add_argument("--tokens", type=int, default=8_000, help="estimated Nemotron tokens")
    parser.add_argument("--market-calls", type=int, default=2, help="market-data pulls")
    parser.add_argument("--search-calls", type=int, default=6, help="web searches")
    parser.add_argument(
        "--margin-floor",
        type=float,
        default=PricingPolicy().margin_floor_pct,
        help="override the margin floor percentage for this quote",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="output as JSON")
    args = parser.parse_args()

    if args.product:
        try:
            product = get_product(args.product)
        except UnknownProduct as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(2)
        fields = product.as_job_fields()
        # An explicit --budget still wins: that is the customer haggling.
        budget_cents = (
            int(round(args.budget * 100)) if args.budget is not None else fields["budget_cents"]
        )
        tokens = fields["est_tokens"]
        market_calls = fields["market_data_calls"]
        search_calls = fields["web_search_calls"]
    elif args.budget is None:
        print("Pass --budget, or --product to price a catalogue product.", file=sys.stderr)
        sys.exit(2)
    else:
        budget_cents = int(round(args.budget * 100))
        tokens, market_calls, search_calls = args.tokens, args.market_calls, args.search_calls

    if budget_cents <= 0:
        print("Budget must be greater than 0.", file=sys.stderr)
        sys.exit(1)

    policy = PricingPolicy(margin_floor_pct=args.margin_floor)
    job = build_job(
        args.topic,
        budget_cents,
        est_tokens=tokens,
        market_data_calls=market_calls,
        web_search_calls=search_calls,
    )
    q = quote(job, policy)

    if args.as_json:
        print(json.dumps(asdict(q), indent=2))
    else:
        print(format_quote(job, q, policy))

    # Exit 1 on a decline so scripts can gate on the verdict.
    sys.exit(0 if q.accept else 1)


if __name__ == "__main__":
    main()
