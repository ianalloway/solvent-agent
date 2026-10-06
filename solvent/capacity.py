"""
capacity.py — how much work can this policy actually get through in a day?

The simulator found it the expensive way: at 25 jobs a day the agent kept
blocking its own spend, not because it was broke but because the rolling 24h
budget ran out. That is a throughput ceiling, and it is knowable in closed
form — no Monte Carlo needed.

Every spend rule implies a maximum number of jobs per day:

  daily budget   → budget ÷ cost per job
  cash reserve   → spendable cash ÷ cost per job
  velocity       → payments per hour × 24 ÷ payments per job
  per-vendor cap → vendor cap ÷ that vendor's share of a job
  per-txn cap    → zero, if any single vendor payment exceeds it

The lowest of those is the real ceiling, and the rule that produced it is the
one worth changing. Unit costs come from what jobs *actually* cost when there
is history, and from the pricing model when there is not.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .guardrails import Guardrails, SpendPolicy, load_spend_policy
from .pricing import estimate_cost
from .treasury import REFUND_VENDOR, Treasury, fmt

#: The job shape used for unit costs when the treasury has no history.
REFERENCE_JOB = {
    "est_tokens": 9_000,
    "market_data_calls": 2,
    "web_search_calls": 8,
}

#: Cost lines of the reference job, mapped to the vendor each one is paid to.
REFERENCE_VENDORS = {
    "nemotron_inference": "nvidia-nemotron",
    "market_data": "market-data-api",
    "web_search": "web-search-api",
    "pdf_render": "pdf-render-saas",
    "email_send": "email-delivery-saas",
}


@dataclass
class Limit:
    """One rule's ceiling on jobs per day."""

    rule: str
    jobs_per_day: float
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "jobs_per_day": round(self.jobs_per_day, 1),
            "detail": self.detail,
        }


def unit_costs(treasury: Treasury | None = None) -> dict[str, Any]:
    """Cost of fulfilling one job, per vendor, from history where possible."""
    t = treasury or Treasury()

    by_job: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    try:
        for entry in t.entries:
            if entry.kind != "expense" or not entry.job_id or entry.vendor == REFUND_VENDOR:
                continue
            if entry.vendor:
                by_job[entry.job_id][entry.vendor] += entry.amount_cents
    except Exception:
        by_job = {}

    if by_job:
        vendors: dict[str, float] = defaultdict(float)
        for spends in by_job.values():
            for vendor, cents in spends.items():
                vendors[vendor] += cents
        jobs = len(by_job)
        per_vendor = {vendor: total / jobs for vendor, total in vendors.items()}
        payments = sum(len(s) for s in by_job.values()) / jobs
        return {
            "source": "realized",
            "sample_jobs": jobs,
            "cost_per_job_cents": round(sum(per_vendor.values())),
            "per_vendor_cents": {v: round(c) for v, c in per_vendor.items()},
            "payments_per_job": round(payments, 2),
        }

    total, breakdown = estimate_cost(REFERENCE_JOB)
    per_vendor = {
        REFERENCE_VENDORS[line]: cents
        for line, cents in breakdown.items()
        if line in REFERENCE_VENDORS
    }
    return {
        "source": "estimated",
        "sample_jobs": 0,
        "cost_per_job_cents": total,
        "per_vendor_cents": per_vendor,
        "payments_per_job": float(len([c for c in per_vendor.values() if c > 0])),
    }


def limits(
    costs: dict[str, Any],
    policy: SpendPolicy,
    *,
    balance_cents: int,
) -> list[Limit]:
    """Every rule's ceiling on jobs per day, tightest first."""
    per_job = max(costs["cost_per_job_cents"], 1)
    payments = max(costs["payments_per_job"], 1)
    found: list[Limit] = []

    found.append(
        Limit(
            "daily_budget",
            policy.daily_budget_cents / per_job,
            f"{fmt(policy.daily_budget_cents)} per 24h ÷ {fmt(per_job)} per job",
        )
    )
    spendable = max(balance_cents - policy.min_reserve_cents, 0)
    found.append(
        Limit(
            "min_reserve",
            spendable / per_job,
            f"{fmt(spendable)} spendable above the reserve ÷ {fmt(per_job)} per job",
        )
    )
    found.append(
        Limit(
            "spend_velocity",
            policy.max_txns_per_hour * 24 / payments,
            f"{policy.max_txns_per_hour}/h × 24 ÷ {payments} payments per job",
        )
    )

    for vendor, cents in sorted(costs["per_vendor_cents"].items()):
        if cents <= 0:
            continue
        cap = policy.vendor_daily_cap_cents(vendor)
        found.append(
            Limit(
                f"vendor_daily_budget:{vendor}",
                cap / cents,
                f"{fmt(cap)} cap ÷ {fmt(round(cents))} of {vendor} per job",
            )
        )
        if cents > policy.max_txn_cents:
            found.append(
                Limit(
                    f"max_txn_cap:{vendor}",
                    0.0,
                    f"{fmt(round(cents))} to {vendor} exceeds the "
                    f"{fmt(policy.max_txn_cents)} per-transaction cap — every job blocks",
                )
            )

    found.sort(key=lambda limit: limit.jobs_per_day)
    return found


def binding_hint(rule: str) -> str:
    """What an operator would actually change to lift this ceiling."""
    if rule == "min_reserve":
        return "Add operating capital, or lower min_reserve_cents — this is a cash limit, not a policy one."
    if rule == "daily_budget":
        return "Raise daily_budget_cents in .solvent/spend_policy.json."
    if rule == "spend_velocity":
        return "Raise max_txns_per_hour in .solvent/spend_policy.json."
    if rule.startswith("vendor_daily_budget:"):
        vendor = rule.split(":", 1)[1]
        return (
            f"Raise the cap for {vendor} via vendor_daily_overrides in .solvent/spend_policy.json."
        )
    if rule.startswith("max_txn_cap:"):
        vendor = rule.split(":", 1)[1]
        return (
            f"Every job blocks: raise max_txn_cents above what one job pays {vendor}, "
            "or split that spend."
        )
    return "Raise this limit in .solvent/spend_policy.json."


def report(
    treasury: Treasury | None = None,
    *,
    policy: SpendPolicy | None = None,
) -> dict[str, Any]:
    """Throughput ceiling, what binds it, and how much of today is left."""
    t = treasury or Treasury()
    rules = policy or load_spend_policy()
    guard = Guardrails(t, rules)
    costs = unit_costs(t)
    balance = t.balance_cents()

    found = limits(costs, rules, balance_cents=balance)
    binding = found[0]
    spent_24h = guard._spent_last_24h()
    headroom = max(rules.daily_budget_cents - spent_24h, 0)
    per_job = max(costs["cost_per_job_cents"], 1)

    return {
        "unit_costs": costs,
        "balance_cents": balance,
        "ceiling_jobs_per_day": round(binding.jobs_per_day, 1),
        "binding_rule": binding.rule,
        "binding_detail": binding.detail,
        "binding_hint": binding_hint(binding.rule),
        "limits": [limit.as_dict() for limit in found],
        "spent_24h_cents": spent_24h,
        "budget_headroom_cents": headroom,
        "jobs_left_today": round(headroom / per_job, 1),
        "used_pct": round(100 * spent_24h / rules.daily_budget_cents, 1)
        if rules.daily_budget_cents
        else 0.0,
    }


def format_capacity(data: dict[str, Any]) -> str:
    """Render the capacity report for a terminal."""
    costs = data["unit_costs"]
    source = (
        f"realized over {costs['sample_jobs']} job(s)"
        if costs["source"] == "realized"
        else "estimated from the pricing model (no fulfilment history yet)"
    )
    lines = [
        "",
        "  THROUGHPUT CAPACITY",
        f"  {'─' * 72}",
        f"  Cost per job         {fmt(costs['cost_per_job_cents'])}  ({source})",
        f"  Payments per job     {costs['payments_per_job']}",
        "",
        f"  Ceiling              {data['ceiling_jobs_per_day']} jobs/day",
        f"  Bound by             {data['binding_rule']}",
        f"                       {data['binding_detail']}",
        "",
        f"  Used in last 24h     {fmt(data['spent_24h_cents'])} ({data['used_pct']}%)  ·  "
        f"room for {data['jobs_left_today']} more job(s) today",
        "",
        f"  {'RULE':<38}{'JOBS/DAY':>10}",
    ]
    for limit in data["limits"]:
        marker = " ←" if limit["rule"] == data["binding_rule"] else ""
        lines.append(f"  {limit['rule'][:37]:<38}{limit['jobs_per_day']:>10}{marker}")
    lines += [
        "",
        f"  → {data.get('binding_hint', '')}",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="solvent capacity",
        description="How many jobs a day the spend policy can fulfil, and which rule binds.",
    )
    parser.add_argument(
        "--cost-per-job",
        type=float,
        help="override the unit cost in USD (what-if, instead of history)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="output as JSON")
    args = parser.parse_args()

    treasury = Treasury()
    policy = load_spend_policy()

    if args.cost_per_job is not None:
        override = max(int(round(args.cost_per_job * 100)), 1)
        costs = unit_costs(treasury)
        scale = override / max(costs["cost_per_job_cents"], 1)
        costs = {
            **costs,
            "source": "override",
            "cost_per_job_cents": override,
            "per_vendor_cents": {v: round(c * scale) for v, c in costs["per_vendor_cents"].items()},
        }
        balance = treasury.balance_cents()
        found = limits(costs, policy, balance_cents=balance)
        binding = found[0]
        data = {
            "unit_costs": costs,
            "balance_cents": balance,
            "ceiling_jobs_per_day": round(binding.jobs_per_day, 1),
            "binding_rule": binding.rule,
            "binding_detail": binding.detail,
            "binding_hint": binding_hint(binding.rule),
            "limits": [limit.as_dict() for limit in found],
            "spent_24h_cents": 0,
            "budget_headroom_cents": policy.daily_budget_cents,
            "jobs_left_today": round(policy.daily_budget_cents / override, 1),
            "used_pct": 0.0,
        }
    else:
        data = report(treasury, policy=policy)

    if args.as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(format_capacity(data))


if __name__ == "__main__":
    main()
