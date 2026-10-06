"""
products.py — the price list.

Until now a customer named their own budget and the margin gate decided whether
it was enough. That works for bespoke commissions and nothing else: a shop that
cannot say what it charges cannot be ordered from, and every quote is a
negotiation.

A product is a named scope at a list price: how much reasoning, how many data
pulls, how many searches, and what the brief costs. Jobs may name one instead
of a budget, and the catalogue is checked against *current* costs — including
the calibration learned from realized COGS — so a list price that has quietly
stopped clearing the margin floor shows up as a stale price rather than as a
run of unprofitable work.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from typing import Any

from .pricing import PricingPolicy, quote
from .treasury import fmt


@dataclass(frozen=True)
class Product:
    """One sellable scope at one price."""

    slug: str
    name: str
    list_price_cents: int
    est_tokens: int
    market_data_calls: int
    web_search_calls: int
    blurb: str

    def as_job_fields(self) -> dict[str, Any]:
        """The job fields this product dictates."""
        return {
            "budget_cents": self.list_price_cents,
            "est_tokens": self.est_tokens,
            "market_data_calls": self.market_data_calls,
            "web_search_calls": self.web_search_calls,
        }

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


#: The shop's price list. Scopes rise with price, so the margin stays roughly
#: flat across the range rather than collapsing at the top.
CATALOG: tuple[Product, ...] = (
    Product(
        slug="express",
        name="Express brief",
        list_price_cents=2_500,
        est_tokens=6_000,
        market_data_calls=1,
        web_search_calls=5,
        blurb="One question, answered with sourced numbers. Same-day.",
    ),
    Product(
        slug="standard",
        name="Standard research brief",
        list_price_cents=7_500,
        est_tokens=12_000,
        market_data_calls=3,
        web_search_calls=10,
        blurb="Market sizing, unit economics, risks and a recommendation.",
    ),
    Product(
        slug="deep-dive",
        name="Deep dive",
        list_price_cents=19_900,
        est_tokens=25_000,
        market_data_calls=6,
        web_search_calls=20,
        blurb="Diligence-grade: competitive set, sensitivities, break-evens.",
    ),
)

BY_SLUG = {product.slug: product for product in CATALOG}


class UnknownProduct(ValueError):
    """Raised when a job names a product that is not in the catalogue."""


def get_product(slug: str) -> Product:
    """Look up a product by slug."""
    try:
        return BY_SLUG[str(slug).strip().lower()]
    except KeyError as exc:
        known = ", ".join(BY_SLUG)
        raise UnknownProduct(f"unknown product {slug!r} (known: {known})") from exc


def apply_product(job: dict[str, Any]) -> dict[str, Any]:
    """Fill a job's scope and price from the product it names.

    Anything the job states explicitly wins: a customer who names a product and
    then asks for twice the searches gets what they asked for, priced through
    the same margin gate as any other job.
    """
    slug = job.get("product")
    if not slug:
        return job
    product = get_product(slug)
    filled = dict(job)
    for field, value in product.as_job_fields().items():
        filled.setdefault(field, value)
    filled["product"] = product.slug
    return filled


def price_list(policy: PricingPolicy | None = None) -> list[dict[str, Any]]:
    """Every product priced against current costs, with its margin and verdict."""
    applied = policy or PricingPolicy()
    rows = []
    for product in CATALOG:
        q = quote(
            {"id": f"price-{product.slug}", **product.as_job_fields()},
            applied,
            with_counter_offer=False,
        )
        rows.append(
            {
                **product.as_dict(),
                "est_cost_cents": q.est_cost_cents,
                "margin_cents": q.margin_cents,
                "margin_pct": q.margin_pct,
                "sellable": q.accept,
                "reason": q.reason,
            }
        )
    return rows


def stale_products(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Products whose list price no longer clears the floor."""
    return [row for row in rows if not row["sellable"]]


def format_price_list(rows: list[dict[str, Any]], policy: PricingPolicy) -> str:
    """Render the price list for a terminal."""
    lines = [
        "",
        "  PRICE LIST",
        f"  {'─' * 74}",
        f"  Margin floor {policy.margin_floor_pct}%  ·  cost calibration "
        f"×{policy.cost_calibration}",
        "",
        f"  {'PRODUCT':<14}{'PRICE':>10}{'COST':>9}{'MARGIN':>9}  SCOPE",
    ]
    for row in rows:
        flag = "" if row["sellable"] else "  ⚠ below floor"
        lines.append(
            f"  {row['slug']:<14}{fmt(row['list_price_cents']):>10}"
            f"{fmt(row['est_cost_cents']):>9}{row['margin_pct']:>8}%"
            f"  {row['est_tokens']:,} tok · {row['market_data_calls']} data · "
            f"{row['web_search_calls']} search{flag}"
        )
    lines.append("")
    for row in rows:
        lines.append(f"  {row['slug']:<14}{row['blurb']}")

    stale = stale_products(rows)
    lines.append("")
    if stale:
        lines.append(
            f"  ⚠ {len(stale)} product(s) no longer clear the margin floor: "
            f"{', '.join(r['slug'] for r in stale)}"
        )
        lines.append("    Raise the list price, narrow the scope, or cut vendor cost.")
    else:
        lines.append("  Every product clears the margin floor at current costs.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="solvent products",
        description="The price list, checked against what fulfilment currently costs.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="output as JSON")
    args = parser.parse_args()

    # Price against the live cost model, calibration included.
    from .calibration import calibration_factor

    policy = PricingPolicy(cost_calibration=calibration_factor())
    rows = price_list(policy)

    if args.as_json:
        print(json.dumps({"products": rows, "margin_floor_pct": policy.margin_floor_pct}, indent=2))
    else:
        print(format_price_list(rows, policy))

    # Non-zero when the price list has gone stale, so a cron job can catch it.
    sys.exit(1 if stale_products(rows) else 0)


if __name__ == "__main__":
    main()
