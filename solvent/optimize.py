"""
optimize.py — search the policy space instead of guessing at it.

`simulate.py` answers "what would this policy do?". This answers the question
an operator actually has: "which policy should I run?".

It sweeps a grid of margin floors and minimum order sizes, runs each through
the simulator on the *same* synthetic demand (common random numbers, so cells
differ by policy and nothing else), and ranks the results under a risk
constraint: maximise expected net per day, subject to finishing below the cash
reserve no more than `max_below_reserve_pct` of the time.

The constraint is the point. A floor of 5% books more revenue than a floor of
45% and ruins the business a fifth of the time; without a stated risk budget,
"best" would always pick the reckless cell.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from .guardrails import SpendPolicy, load_spend_policy
from .money import dollars_to_cents
from .pricing import PricingPolicy
from .simulate import simulate
from .treasury import fmt

DEFAULT_FLOORS = (15.0, 25.0, 35.0, 45.0, 55.0)
DEFAULT_MIN_PRICES_CENTS = (1_000, 1_500, 2_500)

#: How often a policy may end a day below the cash reserve and still count as
#: a candidate. Anything above this is not a business, it is a gamble.
DEFAULT_MAX_BELOW_RESERVE_PCT = 5.0


def sweep(
    floors: tuple[float, ...] = DEFAULT_FLOORS,
    min_prices: tuple[int, ...] = DEFAULT_MIN_PRICES_CENTS,
    *,
    trials: int = 40,
    jobs: int = 12,
    capital_cents: int = 10_000,
    seed: int = 7,
    cost_multiplier: float = 1.0,
    cost_spread: float = 0.35,
    cost_calibration: float = 1.0,
    spend_policy: SpendPolicy | None = None,
) -> list[dict[str, Any]]:
    """Run every (margin floor × minimum order) cell over identical demand."""
    policy = spend_policy or load_spend_policy()
    rows: list[dict[str, Any]] = []
    for floor in floors:
        for min_price in min_prices:
            pricing = PricingPolicy(
                margin_floor_pct=floor,
                min_price_cents=min_price,
                cost_calibration=cost_calibration,
            )
            data = simulate(
                trials=trials,
                jobs=jobs,
                capital_cents=capital_cents,
                seed=seed,
                pricing=pricing,
                spend_policy=policy,
                cost_spread=cost_spread,
                cost_multiplier=cost_multiplier,
            )
            rows.append(
                {
                    "margin_floor_pct": floor,
                    "min_price_cents": min_price,
                    "acceptance_rate_pct": data["acceptance_rate_pct"],
                    "mean_net_cents": data["mean_net_cents"],
                    "balance_p10_cents": data["balance_p10_cents"],
                    "balance_p50_cents": data["balance_p50_cents"],
                    "below_reserve_pct": data["below_reserve_pct"],
                    "insolvent_pct": data["insolvent_pct"],
                    "loss_making_trials_pct": data["loss_making_trials_pct"],
                }
            )
    return rows


def recommend(
    rows: list[dict[str, Any]],
    *,
    max_below_reserve_pct: float = DEFAULT_MAX_BELOW_RESERVE_PCT,
) -> dict[str, Any] | None:
    """The best-paying cell inside the risk budget.

    When no cell fits the budget, the safest one is returned instead and
    flagged, because "nothing qualifies" is not useful advice on its own.
    """
    if not rows:
        return None
    safe = [r for r in rows if r["below_reserve_pct"] <= max_below_reserve_pct]
    if safe:
        # Ties go to the more conservative policy: at equal expected net, a
        # higher margin floor is free protection, and a lower minimum order
        # keeps the door open to more customers.
        best = max(
            safe,
            key=lambda r: (r["mean_net_cents"], r["margin_floor_pct"], -r["min_price_cents"]),
        )
        return {**best, "within_risk_budget": True}
    safest = min(rows, key=lambda r: (r["below_reserve_pct"], -r["mean_net_cents"]))
    return {**safest, "within_risk_budget": False}


def format_sweep(
    rows: list[dict[str, Any]],
    best: dict[str, Any] | None,
    *,
    max_below_reserve_pct: float = DEFAULT_MAX_BELOW_RESERVE_PCT,
    baseline: dict[str, Any] | None = None,
) -> str:
    """Render the grid, marking the recommended cell."""
    lines = [
        "",
        "  POLICY SEARCH",
        f"  {'─' * 74}",
        f"  Maximise net per day, subject to ending below the cash reserve at most "
        f"{max_below_reserve_pct}% of days",
        "",
        f"  {'FLOOR':>7}{'MIN ORDER':>11}{'ACCEPT':>9}{'NET/DAY':>12}"
        f"{'p10 BAL':>11}{'BELOW RES':>11}",
    ]
    for row in rows:
        marker = (
            " ←"
            if best
            and row["margin_floor_pct"] == best["margin_floor_pct"]
            and row["min_price_cents"] == best["min_price_cents"]
            else ""
        )
        lines.append(
            f"  {row['margin_floor_pct']:>6}%{fmt(row['min_price_cents']):>11}"
            f"{row['acceptance_rate_pct']:>8}%{fmt(row['mean_net_cents']):>12}"
            f"{fmt(row['balance_p10_cents']):>11}{row['below_reserve_pct']:>10}%{marker}"
        )

    lines.append("")
    if not best:
        lines += ["  Nothing to recommend — the grid was empty.", ""]
        return "\n".join(lines)

    verdict = (
        f"  → Run a {best['margin_floor_pct']}% margin floor with a "
        f"{fmt(best['min_price_cents'])} minimum order: "
        f"{fmt(best['mean_net_cents'])} net per day at "
        f"{best['acceptance_rate_pct']}% acceptance."
    )
    lines.append(verdict)
    if not best["within_risk_budget"]:
        lines.append(
            f"    ⚠ No policy in this grid stays inside the risk budget; this is merely "
            f"the safest ({best['below_reserve_pct']}% of days below reserve)."
        )
    if baseline:
        delta = best["mean_net_cents"] - baseline["mean_net_cents"]
        direction = "more" if delta >= 0 else "less"
        lines.append(
            f"    vs the policy in force ({baseline['margin_floor_pct']}% / "
            f"{fmt(baseline['min_price_cents'])}): {fmt(abs(delta))} {direction} per day."
        )
    lines.append("")
    return "\n".join(lines)


def _parse_list(raw: str, *, cents: bool) -> tuple:
    values = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        number = float(chunk)
        values.append(dollars_to_cents(number) if cents else number)
    return tuple(values)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="solvent optimize",
        description="Search margin floor × minimum order for the best policy inside a risk budget.",
    )
    parser.add_argument(
        "--floors",
        default=",".join(str(f) for f in DEFAULT_FLOORS),
        help="margin floor percentages to try, comma-separated",
    )
    parser.add_argument(
        "--min-prices",
        default=",".join(f"{p / 100:g}" for p in DEFAULT_MIN_PRICES_CENTS),
        help="minimum order sizes in USD, comma-separated",
    )
    parser.add_argument("--trials", type=int, default=40, help="paper days per cell")
    parser.add_argument("--jobs", type=int, default=12, help="inbound jobs per day")
    parser.add_argument("--capital", type=float, default=100.0, help="starting cash in USD")
    parser.add_argument(
        "--max-ruin",
        type=float,
        default=DEFAULT_MAX_BELOW_RESERVE_PCT,
        help="risk budget: max %% of days allowed to end below the cash reserve",
    )
    parser.add_argument(
        "--cost-multiplier",
        type=float,
        default=1.0,
        help="stress the search: what if vendors cost N× the model",
    )
    parser.add_argument("--cost-spread", type=float, default=0.35, help="cost uncertainty")
    parser.add_argument("--seed", type=int, default=7, help="RNG seed; same seed, same grid")
    parser.add_argument("--json", action="store_true", dest="as_json", help="output as JSON")
    args = parser.parse_args()

    floors = _parse_list(args.floors, cents=False) or DEFAULT_FLOORS
    min_prices = _parse_list(args.min_prices, cents=True) or DEFAULT_MIN_PRICES_CENTS

    kwargs = dict(
        trials=max(args.trials, 1),
        jobs=max(args.jobs, 1),
        capital_cents=dollars_to_cents(args.capital),
        seed=args.seed,
        cost_multiplier=max(args.cost_multiplier, 0.0),
        cost_spread=max(args.cost_spread, 0.0),
    )
    rows = sweep(floors, min_prices, **kwargs)
    best = recommend(rows, max_below_reserve_pct=args.max_ruin)

    # What the policy in force would do on the same demand, for comparison.
    current = PricingPolicy()
    baseline_rows = sweep((current.margin_floor_pct,), (current.min_price_cents,), **kwargs)
    baseline = baseline_rows[0] if baseline_rows else None

    if args.as_json:
        print(
            json.dumps(
                {"grid": rows, "recommended": best, "baseline": baseline},
                indent=2,
                default=str,
            )
        )
    else:
        print(format_sweep(rows, best, max_below_reserve_pct=args.max_ruin, baseline=baseline))


if __name__ == "__main__":
    main()
