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

import datetime
import json
import sys
from typing import Iterable, Optional

from .treasury import LedgerEntry, Treasury, fmt

_PERIODS = ("day", "week", "month")

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


def _period_key(ts: float, period: str) -> str:
    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
    if period == "day":
        return dt.strftime("%Y-%m-%d")
    if period == "week":
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if period == "month":
        return dt.strftime("%Y-%m")
    raise ValueError(f"unknown period {period!r}; expected one of {_PERIODS}")


def period_pnl(entries: Iterable[LedgerEntry], period: str = "day") -> list[dict]:
    """Net P&L bucketed by calendar period, in chronological order.

    Each bucket carries revenue, operating cost, net profit (capital excluded),
    and the running ending cash balance (capital included) at period close.
    """
    if period not in _PERIODS:
        raise ValueError(f"unknown period {period!r}; expected one of {_PERIODS}")

    buckets: dict[str, dict] = {}
    order: list[str] = []
    for e in sorted(entries, key=lambda e: e.ts):
        key = _period_key(e.ts, period)
        if key not in buckets:
            buckets[key] = {
                "period": key, "revenue_cents": 0, "operating_cost_cents": 0,
                "net_cents": 0, "capital_cents": 0,
            }
            order.append(key)
        b = buckets[key]
        if e.kind == "revenue":
            b["revenue_cents"] += e.amount_cents
            b["net_cents"] += e.amount_cents
        elif e.kind == "expense":
            b["operating_cost_cents"] += e.amount_cents
            b["net_cents"] -= e.amount_cents
        else:  # capital
            b["capital_cents"] += e.amount_cents

    running = 0
    out = []
    for key in order:
        b = buckets[key]
        running += b["net_cents"] + b["capital_cents"]
        b["ending_balance_cents"] = running
        out.append(b)
    return out


def forecast(entries: Iterable[LedgerEntry], *, horizon_days: int = 30) -> dict:
    """Project the cash balance ``horizon_days`` out, with a best/worst band.

    Models daily net P&L as a random walk: the central projection extends the
    mean daily net, and the band widens with the daily volatility scaled by
    ``sqrt(horizon)`` (so uncertainty grows over time, not linearly). Based on
    *active* days, so it assumes a similar working cadence continues.
    """
    entries = list(entries)
    balance = sum(_signed(e) for e in entries)
    base = {
        "horizon_days": horizon_days,
        "balance_cents": balance,
        "daily_mean_cents": None,
        "daily_volatility_cents": None,
        "projected_balance_cents": None,
        "best_cents": None,
        "worst_cents": None,
        "status": "insufficient_history",
    }

    daily = period_pnl(entries, "day")
    nets = [b["net_cents"] for b in daily]
    if len(nets) < 2 or horizon_days <= 0:
        return base

    mean = sum(nets) / len(nets)
    variance = sum((n - mean) ** 2 for n in nets) / (len(nets) - 1)
    std = variance ** 0.5
    band = std * (horizon_days ** 0.5)
    projected = balance + mean * horizon_days

    base.update(
        status="ok",
        daily_mean_cents=round(mean),
        daily_volatility_cents=round(std),
        projected_balance_cents=round(projected),
        best_cents=round(projected + band),
        worst_cents=round(projected - band),
    )
    return base


def build_report(
    entries: Iterable[LedgerEntry],
    *,
    reserve_cents: int = 0,
    period: str = "day",
    horizon_days: int = 30,
) -> dict:
    """Assemble the full financial picture as a JSON-serialisable dict."""
    entries = list(entries)
    return {
        "income_statement": income_statement(entries),
        "unit_economics": unit_economics(entries),
        "runway": runway(entries, reserve_cents=reserve_cents),
        "balance_sparkline": sparkline(balance_series(entries)),
        "period": period,
        "period_pnl": period_pnl(entries, period),
        "forecast": forecast(entries, horizon_days=horizon_days),
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

    fc = report.get("forecast")
    if fc:
        lines += ["", f"Forecast ({fc['horizon_days']}d)"]
        if fc["status"] != "ok":
            lines.append("  Insufficient history to project a balance.")
        else:
            lines += [
                f"  Daily net (mean)   {fmt(fc['daily_mean_cents'])}  (±{fmt(fc['daily_volatility_cents'])} vol)",
                f"  Projected balance  {fmt(fc['projected_balance_cents'])}",
                f"  Best / worst       {fmt(fc['best_cents'])} / {fmt(fc['worst_cents'])}",
            ]

    spark = report.get("balance_sparkline")
    if spark:
        lines += ["", f"Balance trajectory  {spark}"]

    trend = report.get("period_pnl") or []
    if trend:
        period = report.get("period", "day")
        recent = trend[-12:]
        lines += ["", f"Net P&L by {period} (last {len(recent)})"]
        for b in recent:
            sign = "+" if b["net_cents"] >= 0 else "-"
            lines.append(
                f"  {b['period']:<11} net {sign}{fmt(abs(b['net_cents']))}"
                f"   (rev {fmt(b['revenue_cents'])} / cost {fmt(b['operating_cost_cents'])})"
                f"   bal {fmt(b['ending_balance_cents'])}"
            )
        net_spark = sparkline([b["net_cents"] for b in recent])
        if net_spark:
            lines += ["", f"Net P&L trend       {net_spark}"]
    return "\n".join(lines)


def main() -> None:
    """CLI: ``python -m solvent finance [--json] [--reserve USD] [--period P]``."""
    args = sys.argv[1:]
    as_json = "--json" in args
    reserve_cents = 0
    period = "day"
    horizon_days = 30
    usage = (
        "Usage: python -m solvent finance [--json] [--reserve <usd>] "
        "[--period day|week|month] [--horizon <days>]"
    )
    if "--reserve" in args:
        try:
            reserve_cents = int(float(args[args.index("--reserve") + 1]) * 100)
        except (ValueError, IndexError):
            print(usage)
            sys.exit(1)
    if "--period" in args:
        try:
            period = args[args.index("--period") + 1]
        except IndexError:
            print(usage)
            sys.exit(1)
        if period not in _PERIODS:
            print(usage)
            sys.exit(1)
    if "--horizon" in args:
        try:
            horizon_days = int(args[args.index("--horizon") + 1])
        except (ValueError, IndexError):
            print(usage)
            sys.exit(1)
        if horizon_days <= 0:
            print(usage)
            sys.exit(1)

    entries = Treasury().entries
    report = build_report(
        entries, reserve_cents=reserve_cents, period=period, horizon_days=horizon_days
    )
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))


if __name__ == "__main__":
    main()
