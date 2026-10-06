"""
intake.py — screening inbound work before it reaches the margin gate.

`security.py` already refuses hostile *content* (prompt injection, unsafe
paths). This is the commercial screen that runs after it: the checks a shop
does before quoting at all.

  1. Contactable customer — an unreachable customer cannot be delivered to.
  2. Duplicate submission — the same person asking for the same brief twice in
     an hour is a double-click, not two commissions. Quoting both means
     charging twice for one piece of work.
  3. Burst — one customer flooding the queue crowds out everyone else and, on
     a pay-per-job agent, is what abuse looks like.
  4. Oversized order — a job far above the usual ceiling is either a mistake or
     the one deal worth a human glance before the agent commits to it.

A screened-out job never reaches pricing, Stripe, or Nemotron, so it costs
nothing. Thresholds live in `.solvent/intake_policy.json`, beside the pricing,
spend, and checkout policies.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .treasury import Treasury, fmt

POLICY_OVERRIDE_PATH = Path(".solvent/intake_policy.json")

#: Prefix on the decline reason, so intake blocks are greppable in the ledger,
#: the event log, and `solvent jobs`.
REASON_PREFIX = "intake"

#: Statuses that mean an earlier job is not really occupying the pipeline.
DEAD_STATUSES = ("failed", "cancelled", "expired")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
_WHITESPACE = re.compile(r"\s+")


@dataclass
class IntakePolicy:
    """Commercial limits on what the agent will even quote."""

    require_email: bool = True
    duplicate_window_minutes: float = 60.0
    # Generous enough that a real customer submitting a stream of distinct
    # briefs is never touched; tight enough to stop a runaway submitter.
    max_jobs_per_customer_per_hour: int = 20
    max_budget_cents: int = 100_000  # $1,000 — above this, ask a human
    blocked_email_domains: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class Screen:
    """The outcome of screening one job, in the shape guardrails use."""

    allowed: bool
    rule: str | None = None
    reason: str = "accepted"
    duplicate_of: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "rule": self.rule,
            "reason": self.reason,
            "duplicate_of": self.duplicate_of,
        }


def load_policy(path: Path | None = None) -> IntakePolicy:
    """Load the intake policy; anything unreadable falls back to defaults."""
    policy = IntakePolicy()
    override = path or POLICY_OVERRIDE_PATH
    if not override.is_file():
        return policy
    try:
        data = json.loads(override.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return policy
    if not isinstance(data, dict):
        return policy

    for name in ("duplicate_window_minutes", "max_jobs_per_customer_per_hour", "max_budget_cents"):
        value = data.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            setattr(policy, name, type(getattr(policy, name))(value))
    if isinstance(data.get("require_email"), bool):
        policy.require_email = data["require_email"]
    domains = data.get("blocked_email_domains")
    if isinstance(domains, list) and all(isinstance(d, str) for d in domains):
        policy.blocked_email_domains = tuple(d.strip().lower().lstrip("@") for d in domains if d)
    return policy


def normalise_topic(topic: str) -> str:
    """Fold a topic to the form duplicate detection compares."""
    return _WHITESPACE.sub(" ", (topic or "").strip().lower())


def _email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""


def screen_job(
    job: dict[str, Any],
    treasury: Treasury,
    policy: IntakePolicy | None = None,
    *,
    now: float | None = None,
) -> Screen:
    """Screen one inbound job. Never raises; a screen failure lets work through."""
    rules = policy or load_policy()
    stamp = now if now is not None else time.time()
    job_id = str(job.get("id") or "")
    email = str(job.get("customer_email") or "").strip().lower()

    # An operator has already looked at this one and said yes (see review.py).
    # The exemption rides on the job, so it covers this job and no other.
    if job.get("intake_approved"):
        return Screen(True, None, "approved by an operator")

    if rules.require_email and not _EMAIL_RE.match(email):
        return Screen(
            False, "unreachable_customer", f"no usable customer email ({email or 'blank'})"
        )
    if _email_domain(email) in rules.blocked_email_domains:
        return Screen(
            False, "blocked_domain", f"customer domain '{_email_domain(email)}' is blocked"
        )

    budget = int(job.get("budget_cents") or 0)
    if budget > rules.max_budget_cents:
        return Screen(
            False,
            "oversized_order",
            f"order {fmt(budget)} is above the automatic ceiling "
            f"{fmt(rules.max_budget_cents)}; needs an operator",
        )

    try:
        history = [
            row
            for row in treasury.list_jobs()
            if str(row.get("id")) != job_id
            and (row.get("customer_email") or "").strip().lower() == email
        ]
    except Exception:
        return Screen(True, None, "accepted (no history available)")

    topic = normalise_topic(str(job.get("topic") or ""))
    duplicate_cutoff = stamp - rules.duplicate_window_minutes * 60
    for row in history:
        if row.get("status") in DEAD_STATUSES:
            continue
        seen = row.get("created_at") or row.get("updated_at") or 0
        if float(seen) >= duplicate_cutoff and normalise_topic(row.get("topic") or "") == topic:
            return Screen(
                False,
                "duplicate",
                f"same brief already submitted as {row.get('id')} "
                f"within {round(rules.duplicate_window_minutes)} minutes",
                duplicate_of=str(row.get("id")),
            )

    hour_cutoff = stamp - 3_600
    recent = sum(1 for row in history if float(row.get("created_at") or 0) >= hour_cutoff)
    if recent >= rules.max_jobs_per_customer_per_hour:
        return Screen(
            False,
            "customer_burst",
            f"{recent} jobs from this customer in the last hour "
            f"(limit {rules.max_jobs_per_customer_per_hour})",
        )

    return Screen(True, None, "accepted")


def decline_reason(screen: Screen) -> str:
    """The reason string recorded on a screened-out job."""
    return f"{REASON_PREFIX}: {screen.rule} — {screen.reason}"


def recent_blocks(treasury: Treasury | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Jobs the intake screen turned away, newest first."""
    t = treasury or Treasury()
    rows = [
        {
            "job_id": job.get("id"),
            "customer_email": job.get("customer_email"),
            "topic": job.get("topic"),
            "budget_cents": job.get("budget_cents") or 0,
            "reason": job.get("error_reason") or "",
            "rule": (job.get("error_reason") or "").split(":")[1].split("—")[0].strip()
            if ":" in (job.get("error_reason") or "")
            else "",
            "ts": job.get("updated_at") or job.get("created_at"),
        }
        for job in t.list_jobs()
        if str(job.get("error_reason") or "").startswith(f"{REASON_PREFIX}:")
    ]
    rows.sort(key=lambda r: r["ts"] or 0, reverse=True)
    return rows[:limit]


def format_intake(policy: IntakePolicy, blocks: list[dict[str, Any]]) -> str:
    """Render the intake policy and what it has turned away."""
    lines = [
        "",
        "  INTAKE SCREEN",
        f"  {'─' * 70}",
        f"  Customer email       {'required' if policy.require_email else 'optional'}",
        f"  Duplicate window     {round(policy.duplicate_window_minutes)} minutes",
        f"  Burst limit          {policy.max_jobs_per_customer_per_hour} jobs per customer/hour",
        f"  Automatic ceiling    {fmt(policy.max_budget_cents)} per order",
        f"  Blocked domains      "
        f"{', '.join(policy.blocked_email_domains) if policy.blocked_email_domains else 'none'}",
        "",
    ]
    if not blocks:
        lines += ["  Nothing has been turned away at intake.", ""]
        return "\n".join(lines)

    by_rule: dict[str, int] = {}
    for row in blocks:
        by_rule[row["rule"]] = by_rule.get(row["rule"], 0) + 1
    lines.append("  Turned away")
    for rule, count in sorted(by_rule.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {count:>4}  {rule}")
    lines += ["", f"  {'JOB':<12}{'BUDGET':>10}  REASON"]
    for row in blocks:
        lines.append(
            f"  {str(row['job_id'])[:11]:<12}{fmt(row['budget_cents']):>10}  {row['reason'][:52]}"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="solvent intake",
        description="The commercial screen inbound jobs pass before they are quoted.",
    )
    parser.add_argument("--limit", type=int, default=20, help="how many blocked jobs to show")
    parser.add_argument("--json", action="store_true", dest="as_json", help="output as JSON")
    args = parser.parse_args()

    policy = load_policy()
    blocks = recent_blocks(limit=args.limit)
    if args.as_json:
        print(
            json.dumps(
                {"policy": policy.__dict__, "blocked": blocks},
                indent=2,
                default=str,
            )
        )
    else:
        print(format_intake(policy, blocks))


if __name__ == "__main__":
    main()
