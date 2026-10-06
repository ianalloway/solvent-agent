"""
alerts.py — the checks an operator would run every morning.

The agent already knows its runway, its cost drift, its spend headroom and its
unpaid pipeline; each lives in a different command, and nobody reads six
reports before coffee. This collapses them into one pass/fail sweep with
severities and an exit code, so it works as a cron job or a CI step:

    solvent alerts || page-someone

Each check is a pure function of treasury state, so they are cheap and
testable, and every alert says what is wrong *and* what to do about it. An
alert nobody can act on is noise.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

from .calibration import calibration_factor, cost_drift
from .capacity import report as capacity_report
from .checkout import load_policy as load_checkout_policy
from .checkout import open_checkouts
from .finance import runway
from .guardrails import Guardrails, load_spend_policy
from .treasury import Treasury, fmt

OK = "ok"
WARN = "warn"
CRITICAL = "critical"

_RANK = {OK: 0, WARN: 1, CRITICAL: 2}

#: Hours a paid job may sit unfulfilled before the customer is being let down.
STUCK_JOB_WARN_HOURS = 2.0
STUCK_JOB_CRITICAL_HOURS = 12.0

#: Days of runway.
RUNWAY_WARN_DAYS = 30.0
RUNWAY_CRITICAL_DAYS = 7.0

#: Cost calibration: how far realized COGS may drift above the model.
DRIFT_WARN_FACTOR = 1.2
DRIFT_CRITICAL_FACTOR = 1.5

#: Share of the rolling 24h spend budget already used.
BUDGET_WARN_PCT = 80.0
BUDGET_CRITICAL_PCT = 95.0

#: Guardrail blocks in the last 24h.
BLOCKS_WARN = 3


@dataclass
class Alert:
    """One health check's verdict."""

    check: str
    severity: str
    message: str
    action: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
            "action": self.action,
        }


def check_cash(t: Treasury) -> Alert:
    """Cash against the reserve floor, and runway if the agent is burning."""
    policy = load_spend_policy()
    balance = t.balance_cents()
    if balance < policy.min_reserve_cents:
        return Alert(
            "cash",
            CRITICAL,
            f"balance {fmt(balance)} is below the {fmt(policy.min_reserve_cents)} reserve",
            "Add operating capital: no vendor spend will be approved until it clears.",
        )

    state = runway(t.entries, reserve_cents=policy.min_reserve_cents)
    days = state.get("runway_days")
    if state.get("status") == "burning" and days is not None:
        if days <= RUNWAY_CRITICAL_DAYS:
            return Alert(
                "cash",
                CRITICAL,
                f"{days} days of runway at the current burn",
                "Raise prices, cut vendor cost, or add capital now.",
            )
        if days <= RUNWAY_WARN_DAYS:
            return Alert(
                "cash",
                WARN,
                f"{days} days of runway at the current burn",
                "Check `solvent finance` — the trend is down, not yet urgent.",
            )
    return Alert("cash", OK, f"balance {fmt(balance)}, {state.get('status', 'idle')}")


def check_cost_model(t: Treasury) -> Alert:
    """Realized COGS against the cost model the gate quotes from."""
    drift = cost_drift(t)
    factor = calibration_factor(drift=drift)
    if drift["samples"] == 0:
        return Alert("cost_model", OK, "no fulfilled jobs yet")
    if factor >= DRIFT_CRITICAL_FACTOR:
        return Alert(
            "cost_model",
            CRITICAL,
            f"jobs cost x{drift['ratio']} what the model says over {drift['samples']} job(s)",
            "Quotes are marked up, but the rates are wrong: update "
            ".solvent/pricing_overrides.json.",
        )
    if factor >= DRIFT_WARN_FACTOR:
        return Alert(
            "cost_model",
            WARN,
            f"realized costs are running x{drift['ratio']} the model",
            "See `solvent costs` for the worst-drifting jobs.",
        )
    return Alert("cost_model", OK, f"cost model within tolerance (x{drift['ratio']})")


def check_stuck_jobs(t: Treasury, *, now: float | None = None) -> Alert:
    """Paid work that has not been delivered."""
    stamp = now if now is not None else time.time()
    stuck = []
    for job in t.list_jobs_by_status(["paid_pending_fulfill", "in_progress"]):
        age = (stamp - float(job.get("updated_at") or job.get("created_at") or stamp)) / 3_600
        if age >= STUCK_JOB_WARN_HOURS:
            stuck.append((job.get("id"), round(age, 1)))
    if not stuck:
        return Alert("stuck_jobs", OK, "no paid job is waiting on fulfilment")
    worst = max(age for _, age in stuck)
    severity = CRITICAL if worst >= STUCK_JOB_CRITICAL_HOURS else WARN
    names = ", ".join(f"{job_id} ({age}h)" for job_id, age in stuck[:5])
    return Alert(
        "stuck_jobs",
        severity,
        f"{len(stuck)} paid job(s) not delivered: {names}",
        "Run `solvent worker` — the customer has paid and is waiting.",
    )


def check_spend_headroom(t: Treasury) -> Alert:
    """How much of the rolling 24h spend budget is left."""
    data = capacity_report(t)
    used = data["used_pct"]
    if used >= BUDGET_CRITICAL_PCT:
        return Alert(
            "spend_headroom",
            CRITICAL,
            f"{used}% of the 24h spend budget used; room for {data['jobs_left_today']} more job(s)",
            "Fulfilment will start blocking and refunding. Raise daily_budget_cents "
            "or wait out the window.",
        )
    if used >= BUDGET_WARN_PCT:
        return Alert(
            "spend_headroom",
            WARN,
            f"{used}% of the 24h spend budget used",
            f"Ceiling is {data['ceiling_jobs_per_day']} jobs/day, bound by {data['binding_rule']}.",
        )
    return Alert("spend_headroom", OK, f"{used}% of the 24h spend budget used")


def check_blocked_spends(t: Treasury, *, now: float | None = None) -> Alert:
    """Guardrail blocks in the last 24h — each one refunded a paying customer."""
    stamp = now if now is not None else time.time()
    cutoff = stamp - 86_400
    blocks = [
        ev
        for ev in t.list_events(limit=500)
        if ev.get("stage") == "spend_blocked" and float(ev.get("ts") or 0) >= cutoff
    ]
    if len(blocks) >= BLOCKS_WARN:
        return Alert(
            "blocked_spends",
            WARN,
            f"{len(blocks)} spend(s) blocked in the last 24h, each refunding a paid job",
            "See `solvent guardrails` for which rule is firing.",
        )
    return Alert("blocked_spends", OK, f"{len(blocks)} spend(s) blocked in the last 24h")


def check_unpaid_pipeline(t: Treasury, *, now: float | None = None) -> Alert:
    """Checkouts past their expiry that the sweep has not closed."""
    policy = load_checkout_policy()
    rows = open_checkouts(t, policy=policy, now=now)
    if not rows:
        return Alert("unpaid_pipeline", OK, "nothing waiting on payment")
    overdue = [row for row in rows if row["next_action"] == "expire"]
    waiting = sum(row["amount_cents"] for row in rows)
    if overdue:
        return Alert(
            "unpaid_pipeline",
            WARN,
            f"{len(overdue)} checkout(s) past the {policy.expire_after_hours}h expiry",
            "Run `solvent checkouts --sweep`, or start the worker, which sweeps each pass.",
        )
    return Alert(
        "unpaid_pipeline",
        OK,
        f"{len(rows)} open checkout(s), {fmt(waiting)} waiting on payment",
    )


def check_vendor_exposure(t: Treasury) -> Alert:
    """Any single vendor close to its own 24h ceiling."""
    guard = Guardrails(t, load_spend_policy())
    rows = guard.vendor_exposure()
    hottest = rows[0] if rows else None
    if hottest and hottest["used_pct"] >= BUDGET_CRITICAL_PCT:
        return Alert(
            "vendor_exposure",
            WARN,
            f"{hottest['vendor']} is at {hottest['used_pct']}% of its 24h cap",
            "Raise that vendor's cap or spread the work; spends to it will start blocking.",
        )
    used = hottest["used_pct"] if hottest else 0.0
    return Alert("vendor_exposure", OK, f"highest vendor exposure {used}% of cap")


#: Every check, in report order.
CHECKS: tuple[tuple[str, Callable[[Treasury], Alert]], ...] = (
    ("cash", check_cash),
    ("stuck_jobs", check_stuck_jobs),
    ("spend_headroom", check_spend_headroom),
    ("vendor_exposure", check_vendor_exposure),
    ("blocked_spends", check_blocked_spends),
    ("unpaid_pipeline", check_unpaid_pipeline),
    ("cost_model", check_cost_model),
)


def run_checks(treasury: Treasury | None = None) -> list[Alert]:
    """Run every check. A check that raises becomes its own alert, not a crash."""
    t = treasury or Treasury()
    results: list[Alert] = []
    for name, check in CHECKS:
        try:
            results.append(check(t))
        except Exception as exc:
            results.append(
                Alert(name, WARN, f"check failed: {exc}", "This is a bug in the check itself.")
            )
    return results


def worst_severity(alerts: list[Alert]) -> str:
    return max((a.severity for a in alerts), key=lambda s: _RANK[s], default=OK)


def summarise(alerts: list[Alert]) -> dict[str, Any]:
    return {
        "severity": worst_severity(alerts),
        "critical": sum(1 for a in alerts if a.severity == CRITICAL),
        "warn": sum(1 for a in alerts if a.severity == WARN),
        "ok": sum(1 for a in alerts if a.severity == OK),
        "alerts": [a.as_dict() for a in alerts],
    }


_ICON = {OK: "✓", WARN: "▲", CRITICAL: "✗"}


def format_alerts(data: dict[str, Any], *, quiet: bool = False) -> str:
    """Render the sweep; `quiet` hides the checks that passed."""
    lines = ["", "  HEALTH", f"  {'─' * 66}"]
    shown = [a for a in data["alerts"] if not (quiet and a["severity"] == OK)]
    if not shown:
        lines += ["  All checks pass.", ""]
        return "\n".join(lines)
    for alert in shown:
        lines.append(f"  {_ICON[alert['severity']]} {alert['check']:<18}{alert['message']}")
        if alert["action"] and alert["severity"] != OK:
            lines.append(f"      → {alert['action']}")
    lines += [
        "",
        f"  {data['critical']} critical  ·  {data['warn']} warning  ·  {data['ok']} ok",
        "",
    ]
    return "\n".join(lines)


def notify(data: dict[str, Any], channel: str = "dashboard") -> int:
    """Push non-ok alerts to chat sessions on a channel. Returns the count sent."""
    from .notifications import enqueue_chat

    sent = 0
    for alert in data["alerts"]:
        if alert["severity"] == OK:
            continue
        text = f"[{alert['severity'].upper()}] {alert['check']}: {alert['message']}"
        if alert["action"]:
            text += f"\n→ {alert['action']}"
        enqueue_chat(channel, "alerts", text)
        sent += 1
    return sent


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="solvent alerts",
        description="One health sweep over cash, delivery, spend, and the cost model.",
    )
    parser.add_argument("--quiet", action="store_true", help="show only problems")
    parser.add_argument(
        "--notify",
        metavar="CHANNEL",
        nargs="?",
        const="dashboard",
        help="also push problems to a chat channel (default: dashboard)",
    )
    parser.add_argument(
        "--warn-exit",
        action="store_true",
        help="exit non-zero on warnings too, not just critical alerts",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="output as JSON")
    args = parser.parse_args()

    data = summarise(run_checks())

    if args.as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(format_alerts(data, quiet=args.quiet))

    if args.notify:
        sent = notify(data, args.notify)
        if not args.as_json:
            print(f"  Pushed {sent} alert(s) to {args.notify}.\n")

    failing = data["severity"] == CRITICAL or (args.warn_exit and data["severity"] == WARN)
    sys.exit(1 if failing else 0)


if __name__ == "__main__":
    main()
