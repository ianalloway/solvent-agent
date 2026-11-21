"""Push operator notifications to an OpenClaw Gateway (`solvent notify`).

Job events and health-alert changes are queued in the treasury database, and
the worker delivers them to the Gateway's ``POST <hooks>/wake``, which drops
them into the agent's main session: the chat the OpenClaw app shows. OpenClaw
treats wake text as a trusted system event, so every message is built from
fields SOLVENT controls (job ids, amounts, alert summaries), never from customer
input such as a brief's topic. See docs/OPENCLAW.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from .access import is_loopback_url
from .provider_http import OPENER, read_limited
from .treasury import Treasury, fmt

HOOKS_URL_ENV = "SOLVENT_OPENCLAW_HOOKS_URL"
HOOKS_TOKEN_ENV = "SOLVENT_OPENCLAW_HOOKS_TOKEN"
AGENT_ID_ENV = "SOLVENT_OPENCLAW_AGENT_ID"
EVENTS_ENV = "SOLVENT_OPENCLAW_NOTIFY"
ALERTS_INTERVAL_ENV = "SOLVENT_OPENCLAW_ALERTS_INTERVAL"

#: Pushed unless SOLVENT_OPENCLAW_NOTIFY says otherwise: money moving, and
#: anything waiting on the operator. Declines are routine and stay out.
DEFAULT_EVENTS = (
    "paid",
    "delivered",
    "failed",
    "refunded",
    "refund_pending",
    "spend_held",
    "delivery_review_required",
    "alerts",
)

MAX_ATTEMPTS = 8
#: A notification older than this is no longer news; give up on it.
TTL_SECONDS = 24 * 3600
RETENTION_SECONDS = 7 * 86400
TIMEOUT_SECONDS = 5.0
BATCH = 20
MAX_TEXT_CHARS = 600
DEFAULT_ALERTS_INTERVAL = 900.0
_JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass(frozen=True)
class Sink:
    wake_url: str
    token: str
    agent_id: str = ""


def config_error(env: Mapping[str, str] | None = None) -> str | None:
    """Why the push settings cannot work; None when they are valid or unset."""
    env = os.environ if env is None else env
    base = env.get(HOOKS_URL_ENV, "").strip()
    token = env.get(HOOKS_TOKEN_ENV, "").strip()
    if not base and not token:
        return None
    if not base or not token:
        missing = HOOKS_URL_ENV if not base else HOOKS_TOKEN_ENV
        return f"{missing} is not set"
    try:
        parts = urlsplit(base)
        parts.port
    except ValueError:
        return f"{HOOKS_URL_ENV} is not a valid URL"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return f"{HOOKS_URL_ENV} must be an http(s) URL"
    if parts.username or parts.password or parts.query or parts.fragment:
        return f"{HOOKS_URL_ENV} must not carry credentials, a query or a fragment"
    if parts.scheme == "http" and not is_loopback_url(base):
        return (
            f"{HOOKS_URL_ENV} is plain http to another host; the hook token would travel in clear"
        )
    return None


def sink(env: Mapping[str, str] | None = None) -> Sink | None:
    """The configured Gateway, or None when pushes are off or misconfigured."""
    env = os.environ if env is None else env
    base = env.get(HOOKS_URL_ENV, "").strip().rstrip("/")
    token = env.get(HOOKS_TOKEN_ENV, "").strip()
    if not base or not token or config_error(env):
        return None
    return Sink(f"{base}/wake", token, env.get(AGENT_ID_ENV, "").strip())


def enabled_events(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """Event kinds to push; an explicitly empty SOLVENT_OPENCLAW_NOTIFY pushes nothing."""
    env = os.environ if env is None else env
    raw = env.get(EVENTS_ENV)
    if raw is None:
        return frozenset(DEFAULT_EVENTS)
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def alerts_interval(env: Mapping[str, str] | None = None) -> float:
    env = os.environ if env is None else env
    try:
        return max(60.0, float(env.get(ALERTS_INTERVAL_ENV, DEFAULT_ALERTS_INTERVAL)))
    except ValueError:
        return DEFAULT_ALERTS_INTERVAL


def _text(body: str) -> str:
    text = f"SOLVENT: {body}"
    return text if len(text) <= MAX_TEXT_CHARS else text[: MAX_TEXT_CHARS - 1] + "…"


def job_message(event: Mapping) -> tuple[str, str] | None:
    """``(kind, text)`` for a job event the operator should hear about, else None."""
    job_id = str(event.get("job_id") or "")
    if not _JOB_ID.fullmatch(job_id):
        return None
    stage = event.get("stage")
    amount = event.get("amount")
    money = (
        f" ({fmt(amount)})"
        if isinstance(amount, int) and not isinstance(amount, bool) and amount > 0
        else ""
    )
    show = f"see `solvent jobs show {job_id}`"
    if stage == "booked" and event.get("status") == "failed":
        return "failed", f"job {job_id} failed after payment; {show}."
    messages = {
        "paid": f"job {job_id} was paid{money}; fulfilment is starting.",
        "delivered": f"the brief for job {job_id} was delivered to the customer.",
        "refunded": f"job {job_id} was refunded{money}.",
        "refund_pending": f"job {job_id} has a refund waiting for review; see `solvent reconcile`.",
        "spend_held": f"a vendor spend{money} for job {job_id} is held for approval; "
        "see `solvent guardrails`.",
        "delivery_review_required": f"the delivery of job {job_id} needs review; {show}.",
    }
    if stage not in messages:
        return None
    return str(stage), messages[str(stage)]


def publish_job_event(
    treasury: Treasury, event: Mapping, env: Mapping[str, str] | None = None
) -> bool:
    """Queue a push for ``event`` if pushes are on and its kind is enabled."""
    env = os.environ if env is None else env
    if sink(env) is None:
        return False
    message = job_message(event)
    if message is None or message[0] not in enabled_events(env):
        return False
    kind, text = message
    return treasury.enqueue_push(_text(text), dedupe_key=f"{kind}:{event['job_id']}")


def _alert_text(alert) -> str:
    text = f"{alert.severity} alert, {alert.check}: {alert.message}."
    return f"{text} {alert.action}" if alert.action else text


def publish_alert_changes(
    treasury: Treasury, alerts: Iterable, env: Mapping[str, str] | None = None
) -> int:
    """Queue one push per check whose severity changed since the last push.

    A check seen passing for the first time is recorded silently; a problem that
    persists is not repeated; a problem that clears is announced once.
    """
    from .alerts import OK

    env = os.environ if env is None else env
    if sink(env) is None or "alerts" not in enabled_events(env):
        return 0
    queued = 0
    for alert in alerts:
        key = f"alert:{alert.check}"
        previous = treasury.get_push_state(key)
        if previous == alert.severity:
            continue
        if alert.severity != OK:
            queued += treasury.enqueue_push(_text(_alert_text(alert)))
        elif previous is not None:
            queued += treasury.enqueue_push(
                _text(f"resolved: {alert.check} is back to normal ({alert.message}).")
            )
        treasury.set_push_state(key, alert.severity)
    return queued


def push_alerts_now(treasury: Treasury, alerts: Iterable[Mapping]) -> int:
    """Queue every problem in an alert sweep and deliver at once (`alerts --notify`)."""
    queued = 0
    for alert in alerts:
        if alert["severity"] == "ok":
            continue
        text = f"{alert['severity']} alert, {alert['check']}: {alert['message']}."
        if alert.get("action"):
            text += f" {alert['action']}"
        queued += treasury.enqueue_push(_text(text))
    deliver_due(treasury)
    return queued


def _retry_after(value: str | None) -> float:
    try:
        return min(max(float(value or 0), 0.0), 3600.0)
    except ValueError:
        return 0.0


def _post(target: Sink, text: str, opener) -> tuple[bool, str, float]:
    """POST one wake; returns ``(ok, error, retry_after_seconds)``. Never raises."""
    body = {"text": text, "mode": "now"}
    if target.agent_id:
        body["agentId"] = target.agent_id
    request = urllib.request.Request(
        target.wake_url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {target.token}", "Content-Type": "application/json"},
    )
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            read_limited(response, 64 * 1024)
        return True, "", 0.0
    except urllib.error.HTTPError as exc:
        retry_after = _retry_after(exc.headers.get("Retry-After") if exc.headers else None)
        exc.close()
        return False, f"HTTP {exc.code}", retry_after
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return False, f"unreachable: {str(reason)[:200]}", 0.0


def deliver_due(
    treasury: Treasury,
    env: Mapping[str, str] | None = None,
    *,
    now: float | None = None,
    opener=OPENER,
) -> dict:
    """Send the pushes that are due, oldest first; stop at the first failure."""
    target = sink(env)
    if target is None:
        return {"sent": 0, "failed": 0}
    now = time.time() if now is None else now
    treasury.prune_pushes(now - RETENTION_SECONDS)
    sent = failed = 0
    for row in treasury.due_pushes(now, BATCH):
        ok, error, retry_after = _post(target, row["text"], opener)
        if ok:
            treasury.mark_push_sent(row["id"], now)
            sent += 1
            continue
        failed += 1
        attempts = row["attempts"] + 1
        retry_at = now + max(min(30.0 * 2 ** (attempts - 1), 3600.0), retry_after)
        give_up = attempts >= MAX_ATTEMPTS or now - row["created_at"] >= TTL_SECONDS
        treasury.mark_push_failed(
            row["id"], error, attempts=attempts, retry_at=None if give_up else retry_at, now=now
        )
        # A failure means the Gateway is down or misconfigured: back the rest off too.
        treasury.defer_pushes(retry_at)
        break
    return {"sent": sent, "failed": failed}


class Pump:
    """The worker's push duty each pass: sweep alerts on an interval, deliver what is due."""

    def __init__(self, env: Mapping[str, str] | None = None, clock=time.monotonic):
        self.env = env
        self.clock = clock
        self._last_alerts: float | None = None

    def tick(self, treasury: Treasury) -> dict:
        if sink(self.env) is None:
            return {"sent": 0, "failed": 0}
        now = self.clock()
        if self._last_alerts is None or now - self._last_alerts >= alerts_interval(self.env):
            self._last_alerts = now
            from .alerts import run_checks

            publish_alert_changes(treasury, run_checks(treasury), self.env)
        return deliver_due(treasury, self.env)


def status(treasury: Treasury, env: Mapping[str, str] | None = None) -> dict:
    env = os.environ if env is None else env
    target = sink(env)
    return {
        "on": target is not None,
        "error": config_error(env),
        "wake_url": target.wake_url if target else None,
        "agent_id": (target.agent_id or None) if target else None,
        "events": sorted(enabled_events(env)),
        "outbox": treasury.push_summary(),
    }


def _print_status(data: dict) -> None:
    if data["on"]:
        agent = f" (agent {data['agent_id']})" if data["agent_id"] else ""
        print(f"OpenClaw push: on, to {data['wake_url']}{agent}")
    elif data["error"]:
        print(f"OpenClaw push: off, {data['error']}")
    else:
        print(f"OpenClaw push: off (set {HOOKS_URL_ENV} and {HOOKS_TOKEN_ENV})")
    print(f"Events: {', '.join(data['events']) or 'none'}")
    box = data["outbox"]
    line = f"Outbox: {box['pending']} pending, {box['sent']} sent, {box['failed']} given up"
    if box["last_error"]:
        line += f" (last error: {box['last_error']})"
    print(line)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="solvent notify",
        description="Push SOLVENT job events and alerts to an OpenClaw Gateway.",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--test", action="store_true", help="queue a test message and send now")
    action.add_argument("--flush", action="store_true", help="send everything that is due now")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    treasury = Treasury()
    delivery = None
    if args.test or args.flush:
        if sink() is None:
            reason = config_error() or f"set {HOOKS_URL_ENV} and {HOOKS_TOKEN_ENV} first"
            print(f"OpenClaw push is off: {reason}", file=sys.stderr)
            sys.exit(2)
        if args.test:
            treasury.enqueue_push(_text("test notification from `solvent notify --test`."))
        delivery = deliver_due(treasury)
    data = {**status(treasury), "delivery": delivery}
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        _print_status(data)
        if delivery:
            print(f"Sent {delivery['sent']}, failed {delivery['failed']}.")
    if delivery and delivery["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
