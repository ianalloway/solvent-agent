"""
simulate.py — what would this policy do to the business?

Margin floor, per-transaction cap, daily budget, cash reserve: every one of
these is a number somebody picks, and picking them on a live treasury means
finding out the expensive way. This runs the same kernel — `pricing.quote`,
`guardrails.Guardrails` — over a synthetic stream of inbound work, many times,
and reports the distribution of outcomes.

Nothing here touches the real treasury, Stripe, or Nemotron: a trial is a
paper business. One trial models roughly one day of trading, so the rolling
24h spend budget and the velocity rule bind the way they would in a real day.

Costs are the part nobody knows in advance, so realized cost is drawn around
the estimate with a configurable spread — that is the thing the margin floor
exists to absorb, and the ruin rate is how you find out whether it does.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from dataclasses import dataclass, field
from typing import Any

from .guardrails import Guardrails, SpendPolicy, load_spend_policy
from .money import dollars_to_cents
from .pricing import PricingPolicy, quote
from .treasury import REFUND_VENDOR, LedgerEntry, fmt

#: Which vendor each estimated cost line is paid to, so simulated spend hits
#: the same allowlist and per-vendor caps the real loop does.
COST_LINE_VENDORS = {
    "nemotron_inference": "nvidia-nemotron",
    "market_data": "market-data-api",
    "web_search": "web-search-api",
    "pdf_render": "pdf-render-saas",
    "email_send": "email-delivery-saas",
}


@dataclass
class JobMix:
    """The shape of inbound demand a trial draws from."""

    min_budget_cents: int = 1_000
    max_budget_cents: int = 15_000
    min_tokens: int = 5_000
    max_tokens: int = 20_000
    max_market_calls: int = 5
    max_search_calls: int = 14

    def draw(self, rng: random.Random, index: int) -> dict[str, Any]:
        """One inbound job."""
        budget = rng.randint(self.min_budget_cents, self.max_budget_cents)
        # Bigger budgets buy bigger briefs — but only sub-linearly: a small
        # customer still wants a real brief, which is why cheap work is the
        # work that fails the margin floor.
        scale = 0.35 + 0.65 * (budget / max(self.max_budget_cents, 1))
        return {
            "id": f"SIM{index}",
            "topic": f"simulated commission {index}",
            "budget_cents": budget,
            "est_tokens": int(
                self.min_tokens
                + scale * (self.max_tokens - self.min_tokens) * rng.uniform(0.6, 1.3)
            ),
            "market_data_calls": max(
                0, round(self.max_market_calls * scale * rng.uniform(0.4, 1.2))
            ),
            "web_search_calls": max(
                1, round(self.max_search_calls * scale * rng.uniform(0.5, 1.2))
            ),
        }


class _PaperLedger:
    """The minimum ledger the guardrails need: entries and a balance."""

    def __init__(self, capital_cents: int):
        self.entries: list[LedgerEntry] = [
            LedgerEntry(kind="capital", amount_cents=capital_cents, memo="sim seed")
        ]

    def balance_cents(self) -> int:
        return sum(e.signed_cents() for e in self.entries)

    def add(
        self,
        kind: str,
        amount_cents: int,
        *,
        vendor: str | None = None,
        job_id: str | None = None,
    ) -> None:
        self.entries.append(
            LedgerEntry(
                kind=kind, amount_cents=amount_cents, memo="sim", vendor=vendor, job_id=job_id
            )
        )


@dataclass
class TrialResult:
    """What one simulated day of trading did."""

    revenue_cents: int = 0
    cogs_cents: int = 0
    refunded_cents: int = 0
    ending_balance_cents: int = 0
    accepted: int = 0
    declined: int = 0
    blocked: int = 0
    decline_reasons: dict[str, int] = field(default_factory=dict)
    block_rules: dict[str, int] = field(default_factory=dict)

    @property
    def net_cents(self) -> int:
        return self.revenue_cents - self.cogs_cents - self.refunded_cents


def _decline_bucket(reason: str) -> str:
    """Collapse a decline reason to a stable key (they carry live numbers)."""
    if "minimum order size" in reason:
        return "below minimum order size"
    if "below fulfilment cost" in reason:
        return "budget below cost"
    if "below floor" in reason:
        return "below margin floor"
    if "24h spend budget" in reason:
        return "daily spend budget"
    if "cash reserve" in reason:
        return "cash reserve"
    return reason


def run_trial(
    rng: random.Random,
    *,
    jobs: int,
    capital_cents: int,
    pricing: PricingPolicy,
    spend_policy: SpendPolicy,
    mix: JobMix,
    cost_spread: float,
    cost_multiplier: float = 1.0,
) -> TrialResult:
    """Run one paper day: quote, earn, spend under policy, book."""
    ledger = _PaperLedger(capital_cents)
    guard = Guardrails(ledger, spend_policy)
    result = TrialResult()

    for index in range(jobs):
        job = mix.draw(rng, index)
        q = quote(job, pricing, with_counter_offer=False)
        if not q.accept:
            result.declined += 1
            bucket = _decline_bucket(q.reason)
            result.decline_reasons[bucket] = result.decline_reasons.get(bucket, 0) + 1
            continue

        # The agent's own pre-flight: it will not start work it cannot fund.
        if (
            ledger.balance_cents() + q.price_cents - q.est_cost_cents
            < spend_policy.min_reserve_cents
        ):
            result.declined += 1
            result.decline_reasons["cash reserve"] = (
                result.decline_reasons.get("cash reserve", 0) + 1
            )
            continue

        # Revenue is collected before cost is incurred.
        ledger.add("revenue", q.price_cents)
        result.revenue_cents += q.price_cents
        result.accepted += 1

        # Realized cost lands around the estimate, split across the same vendors.
        realized_factor = cost_multiplier * rng.lognormvariate(0, cost_spread)
        spends = [
            (COST_LINE_VENDORS[line], max(round(cents * realized_factor), 0))
            for line, cents in q.cost_breakdown.items()
            if line in COST_LINE_VENDORS
        ]

        sim_job_id = f"sim-{index}"
        blocked_rule: str | None = None
        paid: list[tuple[str, int]] = []
        for vendor, amount in spends:
            if amount <= 0:
                continue
            decision = guard.evaluate(
                amount, vendor, projected_job_margin_cents=q.margin_cents, job_id=sim_job_id
            )
            if not decision.allowed:
                blocked_rule = decision.rule or "blocked"
                break
            ledger.add("expense", amount, vendor=vendor, job_id=sim_job_id)
            paid.append((vendor, amount))

        if blocked_rule:
            # Same as the real stage machine: a blocked spend refunds the job.
            ledger.add("expense", q.price_cents, vendor=REFUND_VENDOR)
            result.refunded_cents += q.price_cents
            result.accepted -= 1
            result.blocked += 1
            result.block_rules[blocked_rule] = result.block_rules.get(blocked_rule, 0) + 1
        result.cogs_cents += sum(amount for _, amount in paid)

    result.ending_balance_cents = ledger.balance_cents()
    return result


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(int(round((len(ordered) - 1) * pct)), len(ordered) - 1)
    return ordered[index]


def simulate(
    *,
    trials: int = 200,
    jobs: int = 12,
    capital_cents: int = 10_000,
    seed: int = 7,
    pricing: PricingPolicy | None = None,
    spend_policy: SpendPolicy | None = None,
    mix: JobMix | None = None,
    cost_spread: float = 0.35,
    cost_multiplier: float = 1.0,
) -> dict[str, Any]:
    """Run many paper days and summarise the distribution of outcomes."""
    price_policy = pricing or PricingPolicy()
    policy = spend_policy or load_spend_policy()
    job_mix = mix or JobMix()
    rng = random.Random(seed)

    results = [
        run_trial(
            rng,
            jobs=jobs,
            capital_cents=capital_cents,
            pricing=price_policy,
            spend_policy=policy,
            mix=job_mix,
            cost_spread=cost_spread,
            cost_multiplier=cost_multiplier,
        )
        for _ in range(trials)
    ]

    balances = [r.ending_balance_cents for r in results]
    nets = [r.net_cents for r in results]
    decline_reasons: dict[str, int] = {}
    block_rules: dict[str, int] = {}
    for r in results:
        for key, count in r.decline_reasons.items():
            decline_reasons[key] = decline_reasons.get(key, 0) + count
        for key, count in r.block_rules.items():
            block_rules[key] = block_rules.get(key, 0) + count

    offered = trials * jobs
    accepted = sum(r.accepted for r in results)
    below_reserve = sum(1 for b in balances if b < policy.min_reserve_cents)
    insolvent = sum(1 for b in balances if b <= 0)

    return {
        "config": {
            "trials": trials,
            "jobs_per_trial": jobs,
            "capital_cents": capital_cents,
            "seed": seed,
            "cost_spread": cost_spread,
            "cost_multiplier": cost_multiplier,
            "margin_floor_pct": price_policy.margin_floor_pct,
            "min_price_cents": price_policy.min_price_cents,
            "cost_calibration": price_policy.cost_calibration,
            "daily_budget_cents": policy.daily_budget_cents,
            "max_txn_cents": policy.max_txn_cents,
            "min_reserve_cents": policy.min_reserve_cents,
        },
        "jobs_offered": offered,
        "jobs_accepted": accepted,
        "jobs_declined": sum(r.declined for r in results),
        "jobs_blocked": sum(r.blocked for r in results),
        "acceptance_rate_pct": round(100 * accepted / offered, 1) if offered else 0.0,
        "mean_net_cents": round(statistics.fmean(nets)) if nets else 0,
        "median_net_cents": _percentile(nets, 0.5),
        "loss_making_trials_pct": round(100 * sum(1 for n in nets if n < 0) / trials, 1)
        if trials
        else 0.0,
        "balance_p10_cents": _percentile(balances, 0.10),
        "balance_p50_cents": _percentile(balances, 0.50),
        "balance_p90_cents": _percentile(balances, 0.90),
        "below_reserve_pct": round(100 * below_reserve / trials, 1) if trials else 0.0,
        "insolvent_pct": round(100 * insolvent / trials, 1) if trials else 0.0,
        "decline_reasons": dict(sorted(decline_reasons.items(), key=lambda kv: -kv[1])),
        "block_rules": dict(sorted(block_rules.items(), key=lambda kv: -kv[1])),
    }


def format_simulation(data: dict[str, Any]) -> str:
    """Render a simulation summary for a terminal."""
    cfg = data["config"]
    lines = [
        "",
        "  POLICY SIMULATION",
        f"  {'─' * 70}",
        f"  {cfg['trials']} trials × {cfg['jobs_per_trial']} inbound jobs  ·  seed "
        f"{cfg['seed']}  ·  cost spread {cfg['cost_spread']}  ·  vendor prices ×"
        f"{cfg['cost_multiplier']}",
        f"  Margin floor {cfg['margin_floor_pct']}%  ·  min order "
        f"{fmt(cfg['min_price_cents'])}  ·  calibration ×{cfg['cost_calibration']}",
        f"  Daily budget {fmt(cfg['daily_budget_cents'])}  ·  txn cap "
        f"{fmt(cfg['max_txn_cents'])}  ·  reserve {fmt(cfg['min_reserve_cents'])}",
        "",
        f"  Accepted             {data['jobs_accepted']} of {data['jobs_offered']} "
        f"({data['acceptance_rate_pct']}%)",
        f"  Declined             {data['jobs_declined']}",
        f"  Blocked + refunded   {data['jobs_blocked']}",
        "",
        f"  Net per trial        mean {fmt(data['mean_net_cents'])}  ·  median "
        f"{fmt(data['median_net_cents'])}",
        f"  Ending balance       p10 {fmt(data['balance_p10_cents'])}  ·  p50 "
        f"{fmt(data['balance_p50_cents'])}  ·  p90 {fmt(data['balance_p90_cents'])}",
        f"  Loss-making trials   {data['loss_making_trials_pct']}%",
        f"  Below cash reserve   {data['below_reserve_pct']}%"
        f"   ·  insolvent {data['insolvent_pct']}%",
    ]
    if data["decline_reasons"]:
        lines += ["", "  Why work was declined"]
        for reason, count in data["decline_reasons"].items():
            lines.append(f"    {count:>6}  {reason}")
    if data["block_rules"]:
        lines += ["", "  Guardrail blocks (each refunded a paid job)"]
        for rule, count in data["block_rules"].items():
            lines.append(f"    {count:>6}  {rule}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="solvent simulate",
        description="Run the pricing and spend policy over synthetic demand, many times.",
    )
    parser.add_argument("--trials", type=int, default=200, help="paper days to run")
    parser.add_argument("--jobs", type=int, default=12, help="inbound jobs per day")
    parser.add_argument("--capital", type=float, default=100.0, help="starting cash in USD")
    parser.add_argument("--margin-floor", type=float, help="override the margin floor percentage")
    parser.add_argument("--min-price", type=float, help="override the minimum order size in USD")
    parser.add_argument(
        "--cost-spread",
        type=float,
        default=0.35,
        help="how far realized costs wander from the estimate (0 = perfect estimates)",
    )
    parser.add_argument(
        "--cost-multiplier",
        type=float,
        default=1.0,
        help="stress test: what if every vendor's real price were N× the cost model",
    )
    parser.add_argument(
        "--calibration",
        type=float,
        default=1.0,
        help="mark quotes up by this factor, as `solvent costs` would (see calibration.py)",
    )
    parser.add_argument("--seed", type=int, default=7, help="RNG seed; same seed, same run")
    parser.add_argument("--json", action="store_true", dest="as_json", help="output as JSON")
    args = parser.parse_args()

    pricing = PricingPolicy()
    if args.margin_floor is not None:
        pricing.margin_floor_pct = args.margin_floor
    if args.min_price is not None:
        pricing.min_price_cents = dollars_to_cents(args.min_price)
    pricing.cost_calibration = max(args.calibration, 0.0)

    data = simulate(
        trials=max(args.trials, 1),
        jobs=max(args.jobs, 1),
        capital_cents=dollars_to_cents(args.capital),
        seed=args.seed,
        pricing=pricing,
        cost_spread=max(args.cost_spread, 0.0),
        cost_multiplier=max(args.cost_multiplier, 0.0),
    )
    if args.as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(format_simulation(data))


if __name__ == "__main__":
    main()
