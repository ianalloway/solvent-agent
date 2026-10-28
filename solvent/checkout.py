"""
checkout.py — chasing, and eventually closing, unpaid checkout links.

A job that reaches `awaiting_payment` used to stay there forever: the worker
re-polled it on every pass, the dashboard counted it as pipeline, and nobody
ever asked the customer whether they still wanted the work. Abandoned carts are
normal — the fix is a lifecycle, not hope.

Two moves, both time-based and both idempotent:

* **Remind.** After `reminder_after_hours` the customer gets the link again, at
  most `max_reminders` times, spaced by the same interval.
* **Expire.** After `expire_after_hours` (or sooner, when a Nano invoice's own
  ``expires_at`` has passed) the link is closed, the job is marked `expired`,
  and the worker stops polling it. Expiry never touches money: an unpaid job
  has no revenue to refund.

Both thresholds are operator-tunable in `.solvent/checkout_policy.json`,
alongside the pricing and spend policy files. Stripe and Nano rails share this
chase path.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import delivery
from .paths import config_path
from .treasury import Treasury, fmt

#: Job status for a checkout nobody paid.
EXPIRED_STATUS = "expired"


@dataclass
class CheckoutPolicy:
    """How long an unpaid link lives, and how often the customer is nudged."""

    reminder_after_hours: float = 4.0
    max_reminders: int = 2
    expire_after_hours: float = 48.0

    def validate(self) -> None:
        if self.expire_after_hours <= 0:
            raise ValueError("expire_after_hours must be positive")


_NUMERIC_FIELDS = ("reminder_after_hours", "max_reminders", "expire_after_hours")


def load_policy(path: Path | None = None) -> CheckoutPolicy:
    """Load the chase policy, falling back to defaults on anything unreadable."""
    policy = CheckoutPolicy()
    override = path if path is not None else config_path("checkout_policy.json")
    if not override.is_file():
        return policy
    try:
        data = json.loads(override.read_text(encoding="utf-8"))
    except (ValueError, OSError):  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        return policy
    if not isinstance(data, dict):
        return policy
    for field in _NUMERIC_FIELDS:
        value = data.get(field)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (isinstance(value, int) or math.isfinite(value))
            and value >= 0
        ):
            setattr(policy, field, type(getattr(policy, field))(value))
    try:
        policy.validate()
    except ValueError:
        return CheckoutPolicy()
    return policy


def _age_hours(row: dict[str, Any], now: float) -> float:
    started = row.get("created_at") or row.get("ts") or now
    return max(now - float(started), 0) / 3_600


def open_checkouts(
    treasury: Treasury | None = None,
    *,
    policy: CheckoutPolicy | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Unpaid checkouts with their age and what happens to them next.

    Rail-agnostic: Stripe sessions and Nano invoices both appear here. A Nano
    invoice whose own ``expires_at`` has passed is treated as due to expire even
    when the chase-policy window has not elapsed yet.
    """
    t = treasury or Treasury()
    rules = policy or load_policy()
    stamp = now if now is not None else time.time()

    rows = []
    for checkout in t.list_checkouts(status="open"):
        job = t.get_job(checkout["job_id"]) or {}
        if job.get("status") != "awaiting_payment":
            continue  # paid, cancelled, or already expired elsewhere
        age = _age_hours(checkout, stamp)
        sent = checkout.get("reminders_sent") or 0
        due_at = rules.reminder_after_hours * (sent + 1)
        rail = checkout.get("rail") or "stripe"
        nano_inv = t.get_nano_invoice(checkout["job_id"]) if rail == "nano" else None
        if rail != "nano" and nano_inv is None:
            nano_inv = t.get_nano_invoice(checkout["job_id"])
            if nano_inv:
                rail = "nano"

        invoice_expired = False
        expires_at = None
        if nano_inv:
            expires_at = nano_inv.get("expires_at")
            if isinstance(expires_at, (int, float)) and stamp > float(expires_at):
                invoice_expired = True
            if nano_inv.get("status") == EXPIRED_STATUS:
                invoice_expired = True

        policy_expired = age >= rules.expire_after_hours
        should_expire = policy_expired or invoice_expired
        if should_expire:
            expires_in_hours = 0.0
        elif isinstance(expires_at, (int, float)):
            invoice_left = max(float(expires_at) - stamp, 0) / 3_600
            policy_left = max(rules.expire_after_hours - age, 0)
            expires_in_hours = round(min(invoice_left, policy_left), 2)
        else:
            expires_in_hours = round(max(rules.expire_after_hours - age, 0), 2)

        rows.append(
            {
                "job_id": checkout["job_id"],
                "topic": job.get("topic") or "",
                "customer_email": job.get("customer_email") or "",
                "amount_cents": job.get("budget_cents") or 0,
                "checkout_url": checkout.get("checkout_url") or "",
                "rail": rail,
                "age_hours": round(age, 2),
                "reminders_sent": sent,
                "expires_in_hours": expires_in_hours,
                "invoice_expired": invoice_expired,
                "next_action": (
                    "expire"
                    if should_expire
                    else "remind"
                    if sent < rules.max_reminders and age >= due_at
                    else "wait"
                ),
            }
        )
    return rows


def sweep(
    treasury: Treasury | None = None,
    *,
    stripe: Any | None = None,
    nano: Any | None = None,
    policy: CheckoutPolicy | None = None,
    now: float | None = None,
    on_event: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    """Send due reminders and expire dead links. Safe to run on every worker pass."""
    t = treasury or Treasury()
    rules = policy or load_policy()
    stamp = now if now is not None else time.time()
    reminded: list[str] = []
    expired: list[str] = []

    def emit(event: dict[str, Any]) -> None:
        event.setdefault("ts", stamp)
        try:
            t.record_event(event.get("job_id", ""), event.get("stage", ""), event)
        except Exception:
            pass
        if on_event:
            on_event(event)

    for row in open_checkouts(t, policy=rules, now=stamp):
        job_id = row["job_id"]
        rail = row.get("rail") or "stripe"
        if row["next_action"] == "expire":
            if rail != "nano" and stripe is not None:
                # Best effort: a live Stripe session should not outlive the job.
                try:
                    stored = t.get_checkout(job_id) or {}
                    session_id = stored.get("session_id")
                    if session_id:
                        stripe.expire_checkout_session(session_id)
                except Exception:
                    pass
            t.upsert_checkout(job_id, "", row["checkout_url"], EXPIRED_STATUS, rail=rail)
            nano_inv = t.get_nano_invoice(job_id)
            if nano_inv:
                if nano is not None and hasattr(nano, "expire_invoice"):
                    try:
                        nano.expire_invoice(nano_inv)
                    except Exception:
                        pass
                t.mark_nano_invoice(job_id, status=EXPIRED_STATUS)
            reason = (
                "nano invoice expired"
                if row.get("invoice_expired")
                else f"checkout unpaid after {rules.expire_after_hours}h"
            )
            t.upsert_job(
                job_id,
                EXPIRED_STATUS,
                error_reason=reason,
                current_stage=EXPIRED_STATUS,
            )
            expired.append(job_id)
            emit(
                {
                    "stage": "checkout_expired",
                    "job_id": job_id,
                    "rail": rail,
                    "age_hours": row["age_hours"],
                    "reminders_sent": row["reminders_sent"],
                    "invoice_expired": bool(row.get("invoice_expired")),
                }
            )
        elif row["next_action"] == "remind":
            number = row["reminders_sent"] + 1
            try:
                delivery.send_payment_reminder(
                    row["customer_email"],
                    job_id,
                    row["topic"],
                    row["checkout_url"],
                    row["amount_cents"],
                    expires_in_hours=row["expires_in_hours"],
                    reminder_number=number,
                )
            except Exception as exc:  # a dead mailbox must not stall the sweep
                emit({"stage": "reminder_failed", "job_id": job_id, "reason": str(exc)})
                continue
            t.record_checkout_reminder(job_id, stamp)
            reminded.append(job_id)
            emit(
                {
                    "stage": "payment_reminder",
                    "job_id": job_id,
                    "rail": rail,
                    "reminder": number,
                    "age_hours": row["age_hours"],
                }
            )

    return {"reminded": reminded, "expired": expired}


def format_checkouts(rows: list[dict[str, Any]], policy: CheckoutPolicy) -> str:
    """Render the open-checkout pipeline for a terminal."""
    lines = [
        "",
        "  OPEN CHECKOUTS",
        f"  {'─' * 74}",
        f"  Remind after {policy.reminder_after_hours}h (max {policy.max_reminders})  ·  "
        f"expire after {policy.expire_after_hours}h",
        "",
    ]
    if not rows:
        lines += ["  Nothing waiting on payment.", ""]
        return "\n".join(lines)

    lines.append(
        f"  {'JOB':<12}{'RAIL':<8}{'AMOUNT':>10}{'AGE':>9}{'REMINDED':>10}{'NEXT':>9}  CUSTOMER"
    )
    for row in rows:
        lines.append(
            f"  {row['job_id'][:11]:<12}{(row.get('rail') or 'stripe')[:7]:<8}"
            f"{fmt(row['amount_cents']):>10}"
            f"{row['age_hours']:>8.1f}h{row['reminders_sent']:>10}"
            f"{row['next_action']:>9}  {row['customer_email'][:28]}"
        )
    waiting = sum(r["amount_cents"] for r in rows)
    lines += ["", f"  {len(rows)} open  ·  {fmt(waiting)} of revenue waiting on payment", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="solvent checkouts",
        description="Unpaid checkout links: what is waiting, what gets chased, what expires.",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="send due reminders and expire dead links now",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="output as JSON")
    args = parser.parse_args()

    treasury = Treasury()
    policy = load_policy()

    if args.sweep:
        from .nano_client import NanoClient
        from .stripe_client import StripeClient

        result = sweep(
            treasury, stripe=StripeClient(), nano=NanoClient(), policy=policy
        )
        if args.as_json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"\n  Reminded {len(result['reminded'])}  ·  expired {len(result['expired'])}\n")
        return

    rows = open_checkouts(treasury, policy=policy)
    if args.as_json:
        print(json.dumps(rows, indent=2, default=str))
    else:
        print(format_checkouts(rows, policy))


if __name__ == "__main__":
    main()
