"""
ops.py — running the business from the chat surface, operator only.

Fifteen commands' worth of business reporting lives in the CLI, which means it
lives wherever the operator is not. The Telegram channel is where they actually
are, and the gateway already routes slash commands there.

The catch is who is on the other end. Any *paired* Telegram user reaches the
gateway's command handler, and a customer is not entitled to the agent's win
rate, customer book, or cash position. So these commands are gated:

* the **dashboard** channel is the operator by construction — it is behind
  `SOLVENT_DASHBOARD_TOKEN`;
* a Telegram id listed in `SOLVENT_OPERATOR_IDS` is the operator;
* anything else is a customer, and is told the command exists but is not
  theirs, rather than being shown the numbers.

Each report answers from local state, so nothing here calls the model or
spends. The summaries are deliberately short — a chat window is not a terminal,
and the full panels are a `solvent <command>` away.
"""

from __future__ import annotations

import os

from .treasury import Treasury, fmt

#: Comma-separated channel-specific ids (Telegram user ids) that may run ops
#: commands. Empty means "only the dashboard".
OPERATOR_IDS_ENV = "SOLVENT_OPERATOR_IDS"

#: Channels that are operator-authenticated by construction.
OPERATOR_CHANNELS = frozenset({"dashboard", "cli"})


def operator_ids() -> frozenset[str]:
    raw = os.environ.get(OPERATOR_IDS_ENV, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def is_operator(channel: str, external_id: str) -> bool:
    """Whether this sender may see the business's own numbers."""
    if channel in OPERATOR_CHANNELS:
        return True
    return str(external_id) in operator_ids()


def _funnel(t: Treasury) -> str:
    from .funnel import analyse

    data = analyse(t)
    if not data["jobs"]:
        return "No orders yet."
    stages, rates = data["stages"], data["rates"]
    lines = [
        f"Submitted {stages['submitted']} → delivered {stages['delivered']}",
        f"Gate acceptance: {_pct(rates['gate_acceptance_pct'])}",
        f"Payment conversion: {_pct(rates['payment_conversion_pct'])}",
        f"Won {fmt(data['won_cents'])}, left on the table {fmt(data['lost_cents'])}",
    ]
    worst = next(iter(data["gate_reasons"].items()), None)
    if worst:
        lines.append(f"Biggest decline reason: {worst[0]} ({worst[1]})")
    return "\n".join(lines)


def _customers(t: Treasury) -> str:
    from .customers import book_summary, customer_stats

    rows = customer_stats(t)
    if not rows:
        return "No customers yet."
    book = book_summary(rows)
    lines = [
        f"{book['customers']} customers, {book['repeat_customers']} repeat "
        f"({book['repeat_rate_pct']}%)",
        f"Top customer is {book['top_customer_share_pct']}% of revenue",
    ]
    for row in rows[:3]:
        lines.append(f"  {row['email'][:32]} — {fmt(row['net_cents'])} net, {row['jobs']} job(s)")
    return "\n".join(lines)


def _capacity(t: Treasury) -> str:
    from .capacity import report

    data = report(t)
    return "\n".join(
        [
            f"Ceiling: {data['ceiling_jobs_per_day']} jobs/day",
            f"Bound by: {data['binding_rule']}",
            f"Room today: {data['jobs_left_today']} job(s) "
            f"({data['used_pct']}% of the 24h budget used)",
        ]
    )


def _alerts(t: Treasury) -> str:
    from .alerts import CRITICAL, OK, run_checks

    results = run_checks(t)
    problems = [a for a in results if a.severity != OK]
    if not problems:
        return f"All {len(results)} checks pass."
    lines = []
    for alert in problems:
        mark = "CRITICAL" if alert.severity == CRITICAL else "warn"
        lines.append(f"[{mark}] {alert.check}: {alert.message}")
    return "\n".join(lines)


def _products(t: Treasury) -> str:
    from .calibration import calibration_factor
    from .pricing import PricingPolicy
    from .products import catalog_problems, price_list, stale_products

    rows = price_list(PricingPolicy(cost_calibration=calibration_factor(t)))
    lines = [
        f"{row['slug']}: {fmt(row['list_price_cents'])} at {row['margin_pct']}% margin"
        + ("" if row["sellable"] else "  ⚠ below floor")
        for row in rows
    ]
    stale = stale_products(rows)
    if stale:
        lines.append(f"{len(stale)} product(s) no longer clear the floor.")
    problems = catalog_problems()
    if problems:
        lines.append(f"Catalogue file: {problems[0]}")
    return "\n".join(lines)


def _quality(t: Treasury) -> str:
    from .quality import quality_report

    data = quality_report(t)
    if not data["briefs"]:
        return "No briefs scored yet."
    lines = [
        f"{data['briefs']} brief(s) scored, mean {data['mean_score']}/100 "
        f"(worst {data['min_score']})",
        "Grades: " + ", ".join(f"{g} {n}" for g, n in sorted(data["grades"].items())),
    ]
    if data["failing_checks"]:
        lines.append(
            "Failing checks: "
            + ", ".join(f"{name} ({count})" for name, count in data["failing_checks"].items())
        )
    return "\n".join(lines)


def _review(t: Treasury) -> str:
    from .review import pending

    rows = pending(t)
    if not rows:
        return "Nothing waiting on an operator."
    lines = [f"{len(rows)} job(s) held:"]
    for row in rows[:5]:
        lines.append(
            f"  {row['job_id']} {fmt(row['budget_cents'])} — {row['rule']} "
            f"({row['customer_email'][:24]})"
        )
    lines.append("Approve with: solvent review approve <job>")
    return "\n".join(lines)


def _costs(t: Treasury) -> str:
    from .calibration import cost_drift, calibration_factor, recommendation

    drift = cost_drift(t)
    factor = calibration_factor(drift=drift)
    return "\n".join(
        [
            f"{drift['samples']} fulfilled job(s): est {fmt(drift['est_total_cents'])} "
            f"vs actual {fmt(drift['actual_total_cents'])} (×{drift['ratio']})",
            f"Calibration in force: ×{factor}",
            recommendation(drift, factor),
        ]
    )


def _turnaround(t: Treasury) -> str:
    from .turnaround import format_turnaround, report

    data = report(t)
    if not data["stats"]["samples"]:
        return "No delivered jobs yet, so nothing to measure."
    del format_turnaround
    figures = data["stats"]
    return "\n".join(
        [
            f"{figures['samples']} delivered: median {figures['p50']}s, p95 {figures['p95']}s",
            f"Can promise: {data['promise']}",
            f"Missed the promise: {data['breaches']} ({data['breach_pct']}%)",
        ]
    )


#: Operator-only commands, each a short read over local state.
COMMANDS = {
    "/funnel": ("win rate and where orders are lost", _funnel),
    "/customers": ("lifetime value and repeat rate", _customers),
    "/capacity": ("jobs/day ceiling and the rule that binds it", _capacity),
    "/alerts": ("health sweep: cash, delivery, spend, cost model", _alerts),
    "/products": ("the price list and its margins", _products),
    "/quality": ("scores the quality gate gave shipped briefs", _quality),
    "/review": ("jobs the intake screen is holding", _review),
    "/costs": ("estimated vs realized COGS", _costs),
    "/turnaround": ("measured paid-to-delivered time", _turnaround),
}


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value}%"


def help_lines() -> list[str]:
    """The operator section of /help."""
    return [f"{cmd} — {blurb}" for cmd, (blurb, _) in COMMANDS.items()]


def handle(
    command: str,
    *,
    channel: str,
    external_id: str,
    treasury: Treasury | None = None,
) -> str | None:
    """Answer an ops command, or ``None`` if it is not one.

    A non-operator is told the command exists and is not theirs: pretending it
    does not exist would be a worse answer to give the operator's own colleague,
    and the numbers stay unsaid either way.
    """
    entry = COMMANDS.get(command.strip().lower())
    if entry is None:
        return None
    if not is_operator(channel, external_id):
        return "That command is operator-only."
    _blurb, report = entry
    try:
        return report(treasury or Treasury())
    except Exception as exc:
        # A broken report must not look like a broken bot.
        return f"Could not read that right now ({type(exc).__name__})."
