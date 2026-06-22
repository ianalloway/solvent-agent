"""OpenClaw-style channel gateway: route inbound messages, dispatch outbound."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Callable

from .agent import Solvent
from .chat import handle_message, format_job_notification
from .memory import SessionMemory
from . import pairing
from .treasury import Treasury, fmt

_outbound_handlers: dict[str, Callable[[str, str], None]] = {}
_rate_limits: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_PER_HOUR = 30


def register_outbound(channel: str, handler: Callable[[str, str], None]) -> None:
    _outbound_handlers[channel] = handler


def notify(channel: str, external_id: str, text: str) -> None:
    handler = _outbound_handlers.get(channel)
    if handler:
        handler(external_id, text)
    try:
        from .notifications import enqueue_chat
        enqueue_chat(channel, external_id, text)
    except Exception:
        pass


def _rate_ok(user_key: str) -> bool:
    now = time.time()
    window = _rate_limits[user_key]
    _rate_limits[user_key] = [t for t in window if now - t < 3600]
    if len(_rate_limits[user_key]) >= RATE_LIMIT_PER_HOUR:
        return False
    _rate_limits[user_key].append(now)
    return True


class Gateway:
    def __init__(self, agent: Solvent | None = None):
        self.agent = agent or Solvent(seed_cents=10_000, fresh=False, sync_payment=False)
        self.memory = SessionMemory(self.agent.t)

    def handle_inbound(
        self,
        channel: str,
        external_id: str,
        text: str,
        *,
        user_label: str | None = None,
    ) -> str:
        if not _rate_ok(f"{channel}:{external_id}"):
            return "Rate limit exceeded. Please wait before sending more messages."

        session = self.memory.get_or_create(channel, external_id, user_label)

        if channel == "telegram" and not pairing.is_allowed(external_id, user_label):
            if text.strip().lower().startswith("/start"):
                code = pairing.request_pairing(external_id, user_label)
                return (
                    f"Pairing required. Your code: {code}\n"
                    "Ask the operator to run: python -m solvent pairing approve " + code
                )
            return "Pairing required. Send /start to get a pairing code."

        if text.strip().startswith("/"):
            return self._handle_command(channel, external_id, text.strip(), session)

        return handle_message(
            session["id"], text, agent=self.agent, memory=self.memory, channel=channel
        )

    def _handle_command(self, channel: str, external_id: str, text: str, session: dict) -> str:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/help":
            return (
                "SOLVENT research business bot.\n"
                "/status — treasury snapshot\n"
                "/jobs — recent jobs\n"
                "/quote <topic> | <budget_usd> — margin preview\n"
                "Or chat naturally to commission a research brief."
            )
        if cmd == "/status":
            s = self.agent.t.snapshot()
            return (
                f"Balance: {fmt(s['balance_cents'])}\n"
                f"Revenue: {fmt(s['revenue_cents'])}\n"
                f"Margin: {s['margin_pct']}%"
            )
        if cmd == "/jobs":
            jobs = self.agent.t.list_jobs()[-5:]
            if not jobs:
                return "No jobs yet."
            return "\n".join(
                f"{j['id']}: {j.get('status')} — {(j.get('topic') or '')[:40]}"
                for j in jobs
            )
        if cmd == "/quote" and "|" in arg:
            topic, budget_s = [x.strip() for x in arg.split("|", 1)]
            try:
                cents = int(float(budget_s) * 100)
            except ValueError:
                return "Usage: /quote topic | 50.00"
            from .chat import handle_message
            prompt = f"Quote a brief on '{topic}' with budget_cents={cents}. Use quote_brief tool."
            return handle_message(session["id"], prompt, agent=self.agent, memory=self.memory, channel=channel)

        return handle_message(session["id"], text, agent=self.agent, memory=self.memory, channel=channel)

    def on_job_event(self, event: dict) -> None:
        """Push job lifecycle updates to sessions watching a job."""
        jid = event.get("job_id")
        if not jid:
            return
        msg = format_job_notification(event)
        if not msg:
            return
        for sess in self.agent.t.list_chat_sessions_by_channel("telegram"):
            if sess.get("notify_job_id") == jid:
                notify("telegram", sess["external_id"], msg)
        for sess in self.agent.t.list_chat_sessions_by_channel("dashboard"):
            if sess.get("notify_job_id") == jid:
                notify("dashboard", sess["external_id"], msg)


_default_gateway: Gateway | None = None


def get_gateway() -> Gateway:
    global _default_gateway
    if _default_gateway is None:
        _default_gateway = Gateway()
    return _default_gateway


def handle_job_event(event: dict) -> None:
    get_gateway().on_job_event(event)
