"""
finance.py — financial intelligence for a self-funding agent.

SOLVENT books every dollar to the treasury ledger; this module turns that
ledger into the numbers a business actually steers by:

  * income statement   — revenue, operating cost, net profit, margin
  * unit economics      — what one job earns, costs, and nets on average
  * runway              — cash on hand vs. burn rate → days of runway, or
                          "cash-flow positive" when the agent funds itself
  * a balance sparkline — the treasury's trajectory at a glance

Everything is computed from ``LedgerEntry`` records, so the functions are
pure and trivially testable without a database. Exposed on the CLI as
``python -m solvent finance`` (alias ``report``).
"""

from __future__ import annotations

import json
import sys
import time
from typing import Iterable, Optional

from .treasury import LedgerEntry, Treasury, fmt

# Below this much elapsed history a daily burn/gain rate is statistical noise,
# so we decline to project a runway rather than quote a meaningless number.
MIN_SPAN_DAYS = 1.0 / 24.0  # one hour

_SPARK_TICKS = "▁▂▃▄▅▆▇█"


def _signed(e: LedgerEntry) -> int:
    return e.amount_cents if e.kind in ("revenue", "capital") else -e.amount_cents


def income_statement(entries: Iterable[LedgerEntry]) -> dict:
    """Revenue, operating cost, net profit and margin, plus a vendor breakdown."""
    entries = list(entries)
    revenue = sum(e.amount_cents for e in entries if e.kind == "revenue")
    expense = sum(e.amount_cents for e in entries if e.kind == "expense")
    capital = sum(e.amount_cents for e in entries if e.kind == "capital")
    net = revenue - expense

    by_vendor: dict[str, int] = {}
    for e in entries:
        if e.kind == "expense":
            by_vendor[e.vendor or "unattributed"] = (
                by_vendor.get(e.vendor or "unattributed", 0) + e.amount_cents
            )

    return {
        "revenue_cents": revenue,
        "operating_cost_cents": expense,
        "net_profit_cents": net,
        "net_margin_pct": round(100 * net / revenue, 1) if revenue else 0.0,
        "seed_capital_cents": capital,
        "balance_cents": sum(_signed(e) for e in entries),
        "expense_by_vendor": dict(
            sorted(by_vendor.items(), key=lambda kv: kv[1], reverse=True)
        ),
    }


def unit_economics(entries: Iterable[LedgerEntry]) -> dict:
    """Per-job averages: revenue, cost, profit and contribution margin.

    Robust regardless of wall-clock span — meaningful even after a 30-second
    demo run, unlike a time-based burn rate.
    """
    jobs: dict[str, dict[str, int]] = {}
    for e in entries:
        if e.kind in ("revenue", "expense") and e.job_id:
            agg = jobs.setdefault(e.job_id, {"rev": 0, "exp": 0})
            agg["rev" if e.kind == "revenue" else "exp"] += e.amount_cents

    completed = [v for v in jobs.values() if v["rev"] > 0]
    n = len(completed)
    if not n:
        return {
            "completed_jobs": 0,
            "avg_revenue_cents": 0,
            "avg_cost_cents": 0,
            "avg_profit_cents": 0,
            "contribution_margin_pct": 0.0,
        }

    avg_rev = sum(v["rev"] for v in completed) // n
    avg_exp = sum(v["exp"] for v in completed) // n
    avg_profit = avg_rev - avg_exp
    return {
        "completed_jobs": n,
        "avg_revenue_cents": avg_rev,
        "avg_cost_cents": avg_exp,
        "avg_profit_cents": avg_profit,
        "contribution_margin_pct": round(100 * avg_profit / avg_rev, 1) if avg_rev else 0.0,
    }


def runway(
    entries: Iterable[LedgerEntry],
    *,
    reserve_cents: int = 0,
    now: Optional[float] = None,
) -> dict:
    """Cash runway from the operating burn/gain rate.

    Returns the current balance, the average daily net operating cash flow,
    and — when the agent is net-burning — how many days until cash hits the
    reserve floor. A net-positive trend reports ``cashflow_positive``.
    """
    entries = list(entries)
    balance = sum(_signed(e) for e in entries)
    op = [e for e in entries if e.kind in ("revenue", "expense")]

    base = {
        "balance_cents": balance,
        "reserve_cents": reserve_cents,
        "spendable_cents": balance - reserve_cents,
        "daily_net_cents": None,
        "runway_days": None,
        "cashflow_positive": None,
        "status": "idle",
    }
    if not op:
        return base

    times = [e.ts for e in op]
    span_days = (max(times) - min(times)) / 86_400.0
    net_op = sum(_signed(e) for e in op)
    base["cashflow_positive"] = net_op >= 0

    if span_days < MIN_SPAN_DAYS:
        base["status"] = "insufficient_history"
        return base

    daily_net = net_op / span_days
    base["daily_net_cents"] = round(daily_net)

    if daily_net >= 0:
        base["status"] = "cashflow_positive"
        base["runway_days"] = None  # self-funding: not burning down
    else:
        base["status"] = "burning"
        base["runway_days"] = round(max(0.0, (balance - reserve_cents) / -daily_net), 1)
    return base


def balance_series(entries: Iterable[LedgerEntry], buckets: int = 40) -> list[int]:
    """Running balance over time, down-sampled to at most ``buckets`` points."""
    ordered = sorted(entries, key=lambda e: e.ts)
    running = 0
    series: list[int] = []
    for e in ordered:
        running += _signed(e)
        series.append(running)
    if len(series) <= buckets:
        return series
    step = len(series) / buckets
    return [series[min(len(series) - 1, int(i * step))] for i in range(buckets)]


def sparkline(values: list[int]) -> str:
    """Render integers as a compact unicode sparkline."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return _SPARK_TICKS[0] * len(values)
    span = hi - lo
    return "".join(
        _SPARK_TICKS[min(len(_SPARK_TICKS) - 1, (v - lo) * (len(_SPARK_TICKS) - 1) // span)]
        for v in values
    )


def build_report(entries: Iterable[LedgerEntry], *, reserve_cents: int = 0) -> dict:
    """Assemble the full financial picture as a JSON-serialisable dict."""
    entries = list(entries)
    return {
        "income_statement": income_statement(entries),
        "unit_economics": unit_economics(entries),
        "runway": runway(entries, reserve_cents=reserve_cents),
        "balance_sparkline": sparkline(balance_series(entries)),
    }


def format_report(report: dict) -> str:
    """Human-readable text rendering of :func:`build_report`."""
    inc = report["income_statement"]
    ue = report["unit_economics"]
    rw = report["runway"]
    lines = [
        "SOLVENT — Financial Report",
        "=" * 48,
        "",
        "Income statement",
        f"  Revenue            {fmt(inc['revenue_cents'])}",
        f"  Operating cost     {fmt(inc['operating_cost_cents'])}",
        f"  Net profit         {fmt(inc['net_profit_cents'])}  ({inc['net_margin_pct']}% margin)",
        f"  Cash balance       {fmt(inc['balance_cents'])}  (seed {fmt(inc['seed_capital_cents'])})",
    ]
    if inc["expense_by_vendor"]:
        lines.append("  Operating cost by vendor:")
        for vendor, amt in inc["expense_by_vendor"].items():
            lines.append(f"    - {vendor:<22} {fmt(amt)}")

    lines += [
        "",
        "Unit economics (per completed job)",
        f"  Completed jobs     {ue['completed_jobs']}",
        f"  Avg revenue        {fmt(ue['avg_revenue_cents'])}",
        f"  Avg cost           {fmt(ue['avg_cost_cents'])}",
        f"  Avg profit         {fmt(ue['avg_profit_cents'])}  ({ue['contribution_margin_pct']}% contribution)",
        "",
        "Runway",
    ]
    if rw["status"] == "idle":
        lines.append("  No operating activity yet.")
    elif rw["status"] == "insufficient_history":
        lines.append(
            f"  Balance {fmt(rw['balance_cents'])} — too little time history to project a daily rate."
        )
    elif rw["status"] == "cashflow_positive":
        lines.append(
            f"  Cash-flow positive: +{fmt(rw['daily_net_cents'])}/day. "
            "The agent funds itself — no runway burndown."
        )
    else:  # burning
        lines.append(
            f"  Burning {fmt(-rw['daily_net_cents'])}/day → "
            f"{rw['runway_days']} days of runway "
            f"(to {fmt(rw['reserve_cents'])} reserve)."
        )

    spark = report.get("balance_sparkline")
    if spark:
        lines += ["", f"Balance trajectory  {spark}"]
    return "\n".join(lines)


def main() -> None:
    """CLI: ``python -m solvent finance [--json] [--reserve USD]``."""
    args = sys.argv[1:]
    as_json = "--json" in args
    reserve_cents = 0
    if "--reserve" in args:
        try:
            reserve_cents = int(float(args[args.index("--reserve") + 1]) * 100)
        except (ValueError, IndexError):
            print("Usage: python -m solvent finance [--json] [--reserve <usd>]")
            sys.exit(1)

    entries = Treasury().entries
    report = build_report(entries, reserve_cents=reserve_cents)
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))


if __name__ == "__main__":
    main()
