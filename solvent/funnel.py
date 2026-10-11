"""
funnel.py — how much work the shop wins, and where it loses the rest.

The treasury has always known the margin on the jobs it took. It has never
known the win rate. Those are different businesses: a 90% margin on one job in
ten is a worse shop than a 60% margin on eight, and nothing here could tell
them apart.

Every stage an order passes already leaves a trace — the intake screen, the
margin gate, the checkout link, the payment, the delivery — so the funnel is a
read over data the agent is keeping anyway:

    submitted → screened → quoted → accepted → checkout → paid → delivered

What falls out at each step is the interesting part. An order screened at
intake costs nothing; one declined by the margin gate is a customer who wanted
to pay and was turned away (and may have been given a counter-offer); one
abandoned at checkout is a customer who said yes and then did not pay. Those
three failures want three different fixes, which is why they are counted
separately rather than lumped into "not completed".

Lost pipeline is reported at the price the customer named, because that is the
money that was on the table.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from typing import Any

from .intake import REASON_PREFIX
from .treasury import Treasury, fmt

#: Statuses that mean the job finished and was billed.
COMPLETED = ("completed",)

#: Statuses that mean the job is still live.
OPEN_STATUSES = ("pending_quote", "awaiting_payment", "paid_pending_fulfill", "in_progress")

_PERIOD_RE = re.compile(r"^(\d+)([dwmh])$")
_UNIT_SECONDS = {"h": 3_600, "d": 86_400, "w": 604_800, "m": 2_592_000}

#: Decline reasons collapse to these buckets; the raw text carries live numbers.
_GATE_BUCKETS = (
    ("minimum order size", "below minimum order size"),
    ("below fulfilment cost", "budget below cost"),
    ("below floor", "below margin floor"),
    ("24h spend budget", "would breach daily spend budget"),
    ("cash reserve", "would breach cash reserve"),
)


def parse_since(value: str | None, *, now: float | None = None) -> float | None:
    """Turn `7d` / `24h` / `2w` / `3m` into a timestamp. ``None`` means all time."""
    if not value:
        return None
    stamp = now if now is not None else time.time()
    match = _PERIOD_RE.match(value.strip().lower())
    if not match:
        raise ValueError(f"unrecognised period {value!r}: use 7d, 24h, 2w or 3m")
    count, unit = match.groups()
    return stamp - int(count) * _UNIT_SECONDS[unit]


def _bucket(reason: str) -> str:
    for needle, label in _GATE_BUCKETS:
        if needle in reason:
            return label
    return reason.strip() or "unexplained"


def _screen_bucket(reason: str) -> str:
    """The intake rule that held an order, without the per-job detail."""
    if ":" not in reason:
        return reason.strip() or "unexplained"
    return reason.split(":", 1)[1].split("—")[0].strip() or "unexplained"


def _price_band(cents: int) -> str:
    for ceiling, label in ((2_500, "under $25"), (10_000, "$25–$100"), (50_000, "$100–$500")):
        if cents < ceiling:
            return label
    return "$500+"


def classify(job: dict[str, Any], *, paid: bool, had_checkout: bool) -> str:
    """Where this job left the funnel.

    The ledger is the authority on payment: a job can be marked failed after it
    was paid and refunded, and that is a very different loss from one that never
    paid at all.
    """
    status = job.get("status") or ""
    reason = str(job.get("error_reason") or "")

    if status in COMPLETED:
        return "delivered"
    if paid:
        return "refunded" if status == "failed" else "paid"
    if status == "expired":
        return "abandoned"
    if status == "cancelled":
        return "cancelled"
    if status == "failed":
        if reason.startswith(f"{REASON_PREFIX}:"):
            return "screened"
        if reason.startswith("security violation") or reason.startswith("unknown product"):
            return "rejected"
        return "declined"
    if had_checkout or status == "awaiting_payment":
        return "awaiting_payment"
    return "open"


def analyse(
    treasury: Treasury | None = None,
    *,
    since: float | None = None,
) -> dict[str, Any]:
    """Count every submitted order by where it left the funnel."""
    t = treasury or Treasury()

    paid_jobs = set()
    for entry in t.entries:
        if entry.kind == "revenue" and entry.job_id:
            paid_jobs.add(entry.job_id)
    checkouts = {row["job_id"] for row in t.list_checkouts()}
    offers = {
        event.get("job_id")
        for event in t.list_events(limit=2_000)
        if event.get("stage") == "counter_offer"
    }

    outcomes: Counter[str] = Counter()
    gate_reasons: Counter[str] = Counter()
    screen_reasons: Counter[str] = Counter()
    by_product: dict[str, Counter[str]] = {}
    by_band: dict[str, Counter[str]] = {}
    won_cents = 0
    lost_cents = 0
    counter_offered = 0
    counter_offered_won = 0
    jobs = 0

    for job in t.list_jobs():
        created = job.get("created_at") or job.get("updated_at") or 0
        if since is not None and float(created) < since:
            continue
        jobs += 1
        job_id = job.get("id")
        budget = int(job.get("budget_cents") or 0)
        outcome = classify(job, paid=job_id in paid_jobs, had_checkout=job_id in checkouts)
        outcomes[outcome] += 1

        if outcome in ("delivered", "paid"):
            won_cents += budget
        elif outcome in ("declined", "abandoned", "screened", "refunded", "cancelled"):
            lost_cents += budget

        if outcome == "declined":
            gate_reasons[_bucket(str(job.get("error_reason") or ""))] += 1
        elif outcome == "screened":
            screen_reasons[_screen_bucket(str(job.get("error_reason") or ""))] += 1

        if job_id in offers:
            counter_offered += 1
            if outcome in ("delivered", "paid"):
                counter_offered_won += 1

        product = _product_of(job)
        by_product.setdefault(product, Counter())[outcome] += 1
        by_band.setdefault(_price_band(budget), Counter())[outcome] += 1

    return {
        "jobs": jobs,
        "outcomes": dict(outcomes),
        "stages": _stages(outcomes),
        "rates": _rates(outcomes),
        "won_cents": won_cents,
        "lost_cents": lost_cents,
        "gate_reasons": dict(gate_reasons.most_common()),
        "screen_reasons": dict(screen_reasons.most_common()),
        "counter_offers": {
            "made": counter_offered,
            "won": counter_offered_won,
            "win_rate_pct": round(100 * counter_offered_won / counter_offered, 1)
            if counter_offered
            else None,
        },
        "by_product": {name: _segment(counts) for name, counts in sorted(by_product.items())},
        "by_price_band": {name: _segment(counts) for name, counts in sorted(by_band.items())},
    }


def _product_of(job: dict[str, Any]) -> str:
    payload = job.get("job_payload_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None
    if isinstance(payload, dict) and payload.get("product"):
        return str(payload["product"])
    return "bespoke"


def _stages(outcomes: Counter[str]) -> dict[str, int]:
    """Counts at each funnel stage, each a superset of the next."""
    submitted = sum(outcomes.values())
    screened_out = outcomes["screened"] + outcomes["rejected"]
    quoted = submitted - screened_out
    accepted = quoted - outcomes["declined"]
    checkout = accepted - outcomes["open"]
    paid = outcomes["paid"] + outcomes["delivered"] + outcomes["refunded"]
    return {
        "submitted": submitted,
        "quoted": quoted,
        "accepted": accepted,
        "checkout": checkout,
        "paid": paid,
        "delivered": outcomes["delivered"],
    }


def _rates(outcomes: Counter[str]) -> dict[str, float | None]:
    stages = _stages(outcomes)

    def pct(numerator: int, denominator: int) -> float | None:
        return round(100 * numerator / denominator, 1) if denominator else None

    return {
        "gate_acceptance_pct": pct(stages["accepted"], stages["quoted"]),
        "payment_conversion_pct": pct(stages["paid"], stages["checkout"]),
        "abandonment_pct": pct(outcomes["abandoned"], stages["checkout"]),
        "delivery_pct": pct(stages["delivered"], stages["paid"]),
        "end_to_end_pct": pct(stages["delivered"], stages["submitted"]),
    }


def _segment(counts: Counter[str]) -> dict[str, Any]:
    submitted = sum(counts.values())
    won = counts["delivered"] + counts["paid"]
    return {
        "submitted": submitted,
        "won": won,
        "win_rate_pct": round(100 * won / submitted, 1) if submitted else None,
        "outcomes": dict(counts.most_common()),
    }


def _bar(pct: float | None, width: int = 24) -> str:
    if pct is None:
        return "·" * width
    filled = min(int(round(pct / 100 * width)), width)
    return "█" * filled + "·" * (width - filled)


def format_funnel(data: dict[str, Any], *, since_label: str = "all time") -> str:
    """Render the funnel for a terminal."""
    stages = data["stages"]
    rates = data["rates"]
    lines = [
        "",
        f"  ORDER FUNNEL — {since_label}",
        f"  {'─' * 70}",
    ]
    if not data["jobs"]:
        lines += ["  No orders in this period.", ""]
        return "\n".join(lines)

    submitted = max(stages["submitted"], 1)
    for label, key in (
        ("Submitted", "submitted"),
        ("Quoted", "quoted"),
        ("Accepted by the gate", "accepted"),
        ("Checkout issued", "checkout"),
        ("Paid", "paid"),
        ("Delivered", "delivered"),
    ):
        count = stages[key]
        share = round(100 * count / submitted, 1)
        lines.append(f"  {label:<22}{count:>5}  {_bar(share)} {share}%")

    lines += [
        "",
        f"  Gate acceptance      {_fmt_pct(rates['gate_acceptance_pct'])}"
        f"   ·  payment conversion {_fmt_pct(rates['payment_conversion_pct'])}",
        f"  Abandoned at pay     {_fmt_pct(rates['abandonment_pct'])}"
        f"   ·  end to end {_fmt_pct(rates['end_to_end_pct'])}",
        f"  Won {fmt(data['won_cents'])}  ·  left on the table {fmt(data['lost_cents'])}",
    ]

    offers = data["counter_offers"]
    if offers["made"]:
        lines.append(
            f"  Counter-offers       {offers['made']} made, {offers['won']} won "
            f"({_fmt_pct(offers['win_rate_pct'])})"
        )

    if data["gate_reasons"]:
        lines += ["", "  Turned away by the margin gate"]
        for reason, count in data["gate_reasons"].items():
            lines.append(f"    {count:>4}  {reason}")
    if data["screen_reasons"]:
        lines += ["", "  Screened before pricing"]
        for reason, count in data["screen_reasons"].items():
            lines.append(f"    {count:>4}  {reason}")

    segments = [
        ("By product", data["by_product"]),
        ("By price band", data["by_price_band"]),
    ]
    for title, rows in segments:
        if len(rows) <= 1:
            continue
        lines += ["", f"  {title}", f"    {'SEGMENT':<16}{'ORDERS':>8}{'WON':>6}{'RATE':>8}"]
        for name, seg in rows.items():
            lines.append(
                f"    {name[:15]:<16}{seg['submitted']:>8}{seg['won']:>6}"
                f"{_fmt_pct(seg['win_rate_pct']):>8}"
            )
    lines.append("")
    return "\n".join(lines)


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value}%"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="solvent funnel",
        description="Win rate: what was submitted, what was won, and where the rest went.",
    )
    parser.add_argument("--since", help="lookback window: 7d, 24h, 2w, 3m (default: all time)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="output as JSON")
    args = parser.parse_args()

    try:
        since = parse_since(args.since)
    except ValueError as exc:
        import sys

        print(str(exc), file=sys.stderr)
        sys.exit(2)

    data = analyse(since=since)
    if args.as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(format_funnel(data, since_label=args.since or "all time"))


if __name__ == "__main__":
    main()
