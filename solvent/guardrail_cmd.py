"""
guardrail_cmd.py — `solvent guardrails`: what the spend policy allows right now.

The guardrails are the reason an autonomous agent can hold a payment
credential, so they should not be a black box. This command prints the policy
in force, how much of each limit the agent has already used in the rolling
window, and the spends that were blocked.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from .guardrails import Guardrails, SpendPolicy, load_spend_policy
from .paths import config_path
from .treasury import Treasury, fmt


def _bar(used_pct: float, width: int = 16) -> str:
    filled = min(int(round(used_pct / 100 * width)), width)
    return "█" * filled + "·" * (width - filled)


def gather(treasury: Treasury | None = None, policy: SpendPolicy | None = None) -> dict:
    """Snapshot the live guardrail state as plain data."""
    t = treasury or Treasury()
    guard = Guardrails(t, policy or load_spend_policy())
    spent_24h = guard._spent_last_24h()
    daily_cap = guard.policy.daily_budget_cents
    blocks = [
        {
            "ts": ev.get("ts"),
            "job_id": ev.get("job_id"),
            "payload": json.loads(ev.get("payload_json") or "{}"),
        }
        for ev in t.list_events(limit=500)
        if ev.get("stage") == "spend_blocked"
    ]
    policy_path = config_path("spend_policy.json")
    return {
        "policy_path": str(policy_path),
        "policy_override_present": policy_path.is_file(),
        "policy": {
            "vendor_allowlist": list(guard.policy.vendor_allowlist),
            "max_txn_cents": guard.policy.max_txn_cents,
            "daily_budget_cents": daily_cap,
            "per_vendor_daily_cents": guard.policy.per_vendor_daily_cents,
            "vendor_daily_overrides": dict(guard.policy.vendor_daily_overrides),
            "max_txns_per_hour": guard.policy.max_txns_per_hour,
            "min_reserve_cents": guard.policy.min_reserve_cents,
        },
        "spent_24h_cents": spent_24h,
        "daily_headroom_cents": max(daily_cap - spent_24h, 0),
        "daily_used_pct": round(100 * spent_24h / daily_cap, 1) if daily_cap else 0.0,
        "txns_last_hour": guard._txns_last_hour(),
        "balance_cents": t.balance_cents(),
        "reserve_headroom_cents": max(t.balance_cents() - guard.policy.min_reserve_cents, 0),
        "vendors": guard.vendor_exposure(),
        "recent_blocks": blocks[:10],
    }


def format_guardrails(data: dict) -> str:
    """Render the guardrail snapshot for a terminal."""
    policy = data["policy"]
    lines = [
        "",
        "  SPEND GUARDRAILS",
        f"  {'─' * 62}",
        f"  Policy file          {data['policy_path']}"
        f" ({'override' if data['policy_override_present'] else 'built-in defaults'})",
        f"  Per transaction      max {fmt(policy['max_txn_cents'])}",
        f"  Rolling 24h budget   {fmt(data['spent_24h_cents'])} of "
        f"{fmt(policy['daily_budget_cents'])} used "
        f"({data['daily_used_pct']}%)  {_bar(data['daily_used_pct'])}",
        f"  Velocity             {data['txns_last_hour']} of "
        f"{policy['max_txns_per_hour']} payments this hour",
        f"  Cash reserve         {fmt(data['balance_cents'])} on hand, floor "
        f"{fmt(policy['min_reserve_cents'])} "
        f"({fmt(data['reserve_headroom_cents'])} spendable)",
        "",
        "  Vendor exposure (rolling 24h)",
        f"  {'VENDOR':<22}{'SPENT':>10}{'CAP':>10}  USED",
    ]
    for row in data["vendors"]:
        lines.append(
            f"  {row['vendor']:<22}{fmt(row['spent_24h_cents']):>10}"
            f"{fmt(row['cap_cents']):>10}  {_bar(row['used_pct'], 12)} {row['used_pct']}%"
        )

    blocks = data["recent_blocks"]
    lines.append("")
    if not blocks:
        lines.append("  No spends blocked in the recorded history.")
    else:
        lines.append(f"  Blocked spends ({len(blocks)} most recent)")
        for block in blocks:
            payload = block["payload"]
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(block["ts"] or 0))
            lines.append(
                f"    {when}  {block['job_id'] or '-':<10} "
                f"{fmt(payload.get('amount', 0))} → {payload.get('vendor', '?')}"
                f"  [{payload.get('rule', 'blocked')}]"
            )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="solvent guardrails",
        description="Show the spend policy in force and how much of it is used.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="output as JSON")
    args = parser.parse_args()

    try:
        data = gather()
    except Exception as exc:  # pragma: no cover - defensive, mirrors status.py
        print(f"Could not read the treasury: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(format_guardrails(data))


if __name__ == "__main__":
    main()
