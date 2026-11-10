"""
checkout.py — chasing, and eventually closing, unpaid checkout links.

A job that reaches `awaiting_payment` used to stay there forever: the worker
re-polled it on every pass, the dashboard counted it as pipeline, and nobody
ever asked the customer whether they still wanted the work. Abandoned carts are
normal — the fix is a lifecycle, not hope.

Two moves, both time-based and both idempotent:

* **Remind.** After `reminder_after_hours` the customer gets the link again, at
  most `max_reminders` times, spaced by the same interval.
* **Expire.** After `expire_after_hours` the Stripe link is closed and the job
  is marked `expired`. Local expiry requires provider confirmation; an
  uncertain response remains retryable for the next sweep or a paid webhook.

Both thresholds are operator-tunable in `.solvent/checkout_policy.json`,
alongside the pricing and spend policy files. Retired Nano checkouts are shown
for manual review only; they are never chased, expired or rerouted to Stripe.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import delivery
from .paths import config_path
from .treasury import Treasury, fmt

logger = logging.getLogger(__name__)

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
    """Unpaid Stripe links and retired checkouts awaiting manual review."""
    t = treasury or Treasury()
    rules = policy or load_policy()
    stamp = now if now is not None else time.time()

    rows = []
    checkouts = t.list_checkouts(status="open")
    listed = {row["job_id"] for row in checkouts}
    # Interrupted historical writes may have left only a job or cached stage.
    # Keep those visible for review without inventing a replacement checkout.
    for job in t.list_jobs():
        if (
            job["id"] not in listed
            and job.get("status") == "awaiting_payment"
            and t.is_legacy_nano_job(job["id"])
        ):
            checkouts.append({"job_id": job["id"], "rail": "nano", "ts": job.get("ts")})
    for checkout in checkouts:
        job = t.get_job(checkout["job_id"]) or {}
        if job.get("status") != "awaiting_payment":
            continue  # paid, cancelled, or already expired elsewhere
        age = _age_hours(checkout, stamp)
        sent = checkout.get("reminders_sent") or 0
        due_at = rules.reminder_after_hours * (sent + 1)
        rail = checkout.get("rail") or "stripe"
        retired = t.is_legacy_nano_job(checkout["job_id"])
        if retired:
            rail = "nano"
        should_expire = age >= rules.expire_after_hours
        expires_in_hours = round(max(rules.expire_after_hours - age, 0), 2)

        rows.append(
            {
                "job_id": checkout["job_id"],
                "topic": job.get("topic") or "",
                "customer_email": job.get("customer_email") or "",
                "amount_cents": job.get("budget_cents") or 0,
                "checkout_url": "" if retired else checkout.get("checkout_url") or "",
                "rail": rail,
                "age_hours": round(age, 2),
                "reminders_sent": sent,
                "expires_in_hours": expires_in_hours,
                "next_action": (
                    "review"
                    if retired
                    else "expire"
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
            logger.warning("could not record %s event", event.get("stage"), exc_info=True)
        if on_event:
            on_event(event)

    for row in open_checkouts(t, policy=rules, now=stamp):
        job_id = row["job_id"]
        rail = row.get("rail") or "stripe"
        if row["next_action"] == "expire":
            if rail != "nano":
                # Do not abandon a session that may have completed while its
                # paid webhook is in flight, or may still accept payment.
                try:
                    stored = t.get_checkout(job_id) or {}
                    session_id = stored.get("session_id")
                    if not session_id:
                        raise RuntimeError("checkout session missing")
                    if stripe is None:
                        if not session_id.startswith("cs_sim_"):
                            raise RuntimeError("Stripe expiry unavailable")
                        result = {"status": "expired"}
                    else:
                        result = stripe.expire_checkout_session(session_id) or {}
                    if result.get("status") != "expired":
                        raise RuntimeError(result.get("error") or "expiry not confirmed")
                except Exception as exc:
                    # Keep the job open for a delayed webhook or the next sweep.
                    # An error is not proof that the customer did not pay.
                    logger.warning("could not expire Stripe checkout for %s: %s", job_id, exc)
                    emit({"stage": "checkout_expire_failed", "job_id": job_id, "reason": str(exc)})
                    continue
            # Re-check under the lock: a webhook/worker may have paid between
            # open_checkouts() and here. Never downgrade a paid/terminal job.
            with t.lock():
                current = t.get_job(job_id) or {}
                stored = t.get_checkout(job_id) or {}
                if (
                    current.get("status") != "awaiting_payment"
                    or t.job_has_revenue(job_id)
                    or stored.get("status") != "open"
                ):
                    continue
                t.upsert_checkout(
                    job_id,
                    stored.get("session_id", ""),
                    row["checkout_url"],
                    EXPIRED_STATUS,
                    rail=rail,
                )
                reason = f"checkout unpaid after {rules.expire_after_hours}h"
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
        from .stripe_client import StripeClient

        result = sweep(treasury, stripe=StripeClient(), policy=policy)
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
