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

The list ships with three products and is an operator's to change: editing
prices, renaming, retiring or adding products is a business decision, not a
code change, so `.solvent/products.json` overrides the built-ins the same way
the spend and intake policies do. A malformed entry is dropped rather than
sold, because a half-read product is a price nobody agreed to.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .paths import config_path
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

#: The built-in list, before any operator override.
BY_SLUG = {product.slug: product for product in CATALOG}

#: Fields an override may set. `slug` identifies the product, so it is not one.
_INT_FIELDS = ("list_price_cents", "est_tokens", "market_data_calls", "web_search_calls")
_TEXT_FIELDS = ("name", "blurb")


class UnknownProduct(ValueError):
    """Raised when a job names a product that is not in the catalogue."""


def _product_from(entry: dict[str, Any], base: Product | None) -> Product | None:
    """Build one product from an override entry, or ``None`` if it is unusable.

    Unspecified fields fall back to the built-in product of the same slug, so an
    operator can change a price without restating the whole scope.
    """
    slug = str(entry.get("slug") or "").strip().lower()
    if not slug:
        return None
    fields: dict[str, Any] = {}
    for field in _INT_FIELDS:
        if field in entry:
            value = entry[field]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                return None  # a price or scope that cannot be read is not sellable
            fields[field] = int(value)
        elif base is None:
            return None  # a brand-new product must state its own price and scope
    for field in _TEXT_FIELDS:
        if field in entry and isinstance(entry[field], str):
            fields[field] = entry[field].strip()
    if base is not None:
        return replace(base, slug=slug, **fields)
    return Product(
        slug=slug,
        name=fields.get("name") or slug.replace("-", " ").title(),
        list_price_cents=fields["list_price_cents"],
        est_tokens=fields["est_tokens"],
        market_data_calls=fields["market_data_calls"],
        web_search_calls=fields["web_search_calls"],
        blurb=fields.get("blurb", ""),
    )


def load_catalog(path: Path | None = None) -> tuple[Product, ...]:
    """The catalogue in force: the built-ins with `.solvent/products.json` applied.

    The file may be a list of products or ``{"products": [...]}``. Entries merge
    by slug, so an override naming only a price keeps the built-in scope;
    ``{"retire": ["express"]}`` removes a product; ``"replace": true`` makes the
    file the whole list. Anything unreadable leaves the built-ins alone — an
    operator's typo must never silently sell a product nobody priced.
    """
    override = path if path is not None else config_path("products.json")
    if not override.is_file():
        return CATALOG
    try:
        data = json.loads(override.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return CATALOG

    if isinstance(data, list):
        entries: Any = data
        retire: tuple[str, ...] = ()
        replace_all = False
    elif isinstance(data, dict):
        entries = data.get("products") or []
        retire = tuple(
            str(slug).strip().lower()
            for slug in (data.get("retire") or [])
            if isinstance(slug, str)
        )
        replace_all = data.get("replace") is True
    else:
        return CATALOG
    if not isinstance(entries, list):
        return CATALOG

    merged: dict[str, Product] = {} if replace_all else dict(BY_SLUG)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        slug = str(entry.get("slug") or "").strip().lower()
        product = _product_from(entry, BY_SLUG.get(slug))
        if product is not None:
            merged[product.slug] = product
    for slug in retire:
        merged.pop(slug, None)

    # An override that empties the catalogue leaves nothing to sell, which is
    # never what was meant.
    return tuple(merged.values()) or CATALOG


def by_slug(path: Path | None = None) -> dict[str, Product]:
    """The catalogue in force, indexed by slug."""
    return {product.slug: product for product in load_catalog(path)}


def get_product(slug: str) -> Product:
    """Look up a product by slug in the catalogue in force."""
    index = by_slug()
    try:
        return index[str(slug).strip().lower()]
    except KeyError as exc:
        known = ", ".join(index)
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


def price_list(
    policy: PricingPolicy | None = None,
    products: tuple[Product, ...] | None = None,
) -> list[dict[str, Any]]:
    """Every product priced against current costs, with its margin and verdict."""
    applied = policy or PricingPolicy()
    rows = []
    for product in products if products is not None else load_catalog():
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


def catalog_problems(path: Path | None = None) -> list[str]:
    """Anything wrong with the override file itself, in plain words.

    Separate from staleness: a stale price is a business problem, while these
    are an operator's typo, and a dropped entry is otherwise silent.
    """
    override = path if path is not None else config_path("products.json")
    if not override.is_file():
        return []
    try:
        data = json.loads(override.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{override.name} is not valid JSON ({exc.msg} at line {exc.lineno})"]
    except OSError as exc:
        return [f"{override.name} cannot be read ({exc})"]

    if isinstance(data, list):
        entries: Any = data
    elif isinstance(data, dict):
        entries = data.get("products") or []
    else:
        return [f"{override.name} must be a list of products or an object with 'products'"]
    if not isinstance(entries, list):
        return [f"{override.name}: 'products' must be a list"]

    problems: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"{override.name} entry {index + 1}"
        if not isinstance(entry, dict):
            problems.append(f"{where} is not an object and was dropped")
            continue
        slug = str(entry.get("slug") or "").strip().lower()
        if not slug:
            problems.append(f"{where} has no slug and was dropped")
            continue
        if slug in seen:
            problems.append(f"{where}: duplicate slug {slug!r} — the last one wins")
        seen.add(slug)
        if _product_from(entry, BY_SLUG.get(slug)) is None:
            if slug in BY_SLUG:
                problems.append(f"{where} ({slug}): a price or scope value is unreadable")
            else:
                problems.append(
                    f"{where} ({slug}): a new product must state list_price_cents, "
                    "est_tokens, market_data_calls and web_search_calls"
                )
    return problems


def format_price_list(
    rows: list[dict[str, Any]],
    policy: PricingPolicy,
    problems: list[str] | None = None,
) -> str:
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
    for problem in problems or []:
        lines.append(f"  ⚠ {problem}")
    if problems:
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
    parser.add_argument(
        "--validate",
        action="store_true",
        help="only check the catalogue file and the margin floor; print nothing else",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="output as JSON")
    args = parser.parse_args()

    # Price against the live cost model, calibration included.
    from .calibration import calibration_factor

    policy = PricingPolicy(cost_calibration=calibration_factor())
    rows = price_list(policy)
    problems = catalog_problems()
    stale = stale_products(rows)

    if args.as_json:
        print(
            json.dumps(
                {
                    "products": rows,
                    "margin_floor_pct": policy.margin_floor_pct,
                    "problems": problems,
                    "stale": [row["slug"] for row in stale],
                },
                indent=2,
            )
        )
    elif args.validate:
        for problem in problems:
            print(f"  ⚠ {problem}")
        for row in stale:
            print(f"  ⚠ {row['slug']} no longer clears the margin floor: {row['reason']}")
        if not problems and not stale:
            print(f"  {len(rows)} product(s) priced, catalogue clean.")
    else:
        print(format_price_list(rows, policy, problems))

    # Non-zero when the list has gone stale or the file is broken, so cron can
    # catch either without reading the output.
    sys.exit(1 if (stale or problems) else 0)


if __name__ == "__main__":
    main()
