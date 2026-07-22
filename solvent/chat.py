"""Conversational harness + business tools for gateway channels."""

from __future__ import annotations

import json
import os
import re
import uuid

from .agent import Solvent
from .memory import SessionMemory
from . import nemotron
from . import tools
from .pricing import quote
from .security import sanitise_prompt_input, PromptInjectionError, InputValidationError
from .treasury import fmt
from .workspace import build_chat_system_prompt, ensure_workspace

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_BUDGET_RE = re.compile(r"(?:budget|pay|spend)\s*[:\$]?\s*\$?(\d+(?:\.\d{1,2})?)", re.I)


def _new_chat_job_id(agent: Solvent) -> str:
    """Generate a server-owned chat job ID that does not overwrite an existing job."""
    for _ in range(10):
        job_id = "T" + uuid.uuid4().hex[:8]
        if not agent.t.get_job(job_id):
            return job_id
    return "T" + uuid.uuid4().hex


def _make_executor(agent: Solvent, session_id: str, live_search: bool):
    ctx = tools.ToolContext()

    def run(name: str, args: dict) -> str:
        if name == "treasury_status":
            s = agent.t.snapshot()
            return json.dumps({
                "balance_cents": s["balance_cents"],
                "revenue_cents": s["revenue_cents"],
                "expense_cents": s["expense_cents"],
                "margin_pct": s["margin_pct"],
            })
        if name == "list_jobs":
            jobs = agent.t.list_jobs()[-10:]
            return json.dumps([
                {"id": j["id"], "status": j.get("status"), "topic": j.get("topic")}
                for j in jobs
            ])
        if name == "job_status":
            jid = args.get("job_id", "")
            row = agent.t.get_job(jid)
            metrics = agent.t.get_metrics(jid)
            return json.dumps({"job": row, "metrics": metrics})
        if name == "quote_brief":
            job = {
                "id": "Q-preview",
                "topic": args.get("topic", ""),
                "budget_cents": int(args.get("budget_cents", 0)),
                "est_tokens": 8000,
                "market_data_calls": 2,
                "web_search_calls": 6,
            }
            q = quote(job, agent.pricing)
            return json.dumps({
                "accept": q.accept,
                "price_cents": q.price_cents,
                "est_cost_cents": q.est_cost_cents,
                "margin_pct": q.margin_pct,
                "reason": q.reason,
            })
        if name == "submit_brief":
            topic = args.get("topic", "")
            budget = int(args.get("budget_cents", 0))
            email = args.get("customer_email", "client@example.com")
            job_id = _new_chat_job_id(agent)
            job = {
                "id": job_id,
                "topic": topic,
                "budget_cents": budget,
                "customer_email": email,
                "est_tokens": 8000,
                "market_data_calls": 2,
                "web_search_calls": 6,
                "context": f"Commissioned via chat session {session_id}",
            }
            result = agent.enqueue_job(job)
            if result.get("stage") != "declined" and not result.get("error"):
                agent.t.update_chat_session(session_id, notify_job_id=job_id, pending_job_json="")
            if result.get("url"):
                return json.dumps({
                    "job_id": job_id,
                    "checkout_url": result.get("url"),
                    "stage": result.get("stage", "invoice"),
                })
            return json.dumps(result)
        if name in tools.ALLOWED_TOOLS:
            return tools.dispatch(
                name, args, ctx, lambda s, u: nemotron.complete(s, u)[0], live_search=live_search
            )
        return json.dumps({"error": f"unknown tool {name!r}"})

    return run


def _load_pending(agent: Solvent, session_id: str) -> dict:
    sess = agent.t.get_chat_session(session_id)
    if not sess or not sess.get("pending_job_json"):
        return {}
    try:
        return json.loads(sess["pending_job_json"])
    except json.JSONDecodeError:
        return {}


def _save_pending(agent: Solvent, session_id: str, pending: dict) -> None:
    agent.t.update_chat_session(session_id, pending_job_json=json.dumps(pending))


def _merge_commission_slots(agent: Solvent, session_id: str, text: str) -> dict:
    """Accumulate topic/budget/email across turns for brief commissioning."""
    pending = _load_pending(agent, session_id)
    lower = text.lower()
    if any(k in lower for k in ("brief", "commission", "research on", "report on")):
        pending.setdefault("intent", "commission")
    if "topic" in lower and ":" in text:
        _, topic = text.split(":", 1)
        pending["topic"] = topic.strip()[:200]
    elif pending.get("intent") and len(text) > 10 and "topic" not in pending:
        pending.setdefault("topic", text.strip()[:200])
    m = _BUDGET_RE.search(text)
    if m:
        pending["budget_cents"] = int(float(m.group(1)) * 100)
    em = _EMAIL_RE.search(text)
    if em:
        pending["customer_email"] = em.group(0)
    if pending:
        _save_pending(agent, session_id, pending)
    return pending


def _pending_prompt(pending: dict) -> str:
    if not pending or pending.get("intent") != "commission":
        return ""
    have = []
    need = []
    for key, label in (
        ("topic", "topic"),
        ("budget_cents", "budget_cents"),
        ("customer_email", "customer_email"),
    ):
        if pending.get(key):
            have.append(f"{label}={pending[key]}")
        else:
            need.append(label)
    if not need:
        return (
            "Pending commission slots are complete: "
            + ", ".join(have)
            + ". Confirm with the user before submit_brief."
        )
    return (
        "Pending commission (slot-filling): have "
        + (", ".join(have) if have else "nothing yet")
        + f"; still need: {', '.join(need)}."
    )


def handle_message(
    session_id: str,
    text: str,
    *,
    agent: Solvent | None = None,
    memory: SessionMemory | None = None,
    channel: str = "cli",
) -> str:
    """Run one conversational turn; returns assistant reply text."""
    ensure_workspace()
    agent = agent or Solvent(seed_cents=10_000, fresh=False, sync_payment=False)
    memory = memory or SessionMemory(agent.t)
    system_prompt = build_chat_system_prompt(channel)

    try:
        text = sanitise_prompt_input(text, field_name="message", max_len=4000)
    except (PromptInjectionError, InputValidationError) as exc:
        return f"Message rejected: {exc}"

    memory.append(session_id, "user", text)
    pending = _merge_commission_slots(agent, session_id, text)
    history = memory.format_for_prompt(session_id, limit=20)
    live_search = os.environ.get("SOLVENT_LIVE_SEARCH", "").strip() in ("1", "true", "yes")

    catalog = {**tools.TOOL_REGISTRY, **tools.BUSINESS_TOOL_REGISTRY}
    executor = _make_executor(agent, session_id, live_search)
    tool_lines = "\n".join(f"- {name}: {meta.get('description', '')}" for name, meta in catalog.items())

    slot_hint = _pending_prompt(pending)
    user = (
        f"Conversation so far:\n{history}\n\n"
        f"Available tools: {', '.join(sorted(catalog))}\n"
        f"{tool_lines}\n"
    )
    if slot_hint:
        user += f"\n{slot_hint}\n"
    user += f"\nUser: {text}"

    tool_budget = tools.MAX_TOOL_CALLS
    calls_made = 0
    for _ in range(6):
        reply, _ = nemotron.complete(system_prompt, user)
        calls = nemotron.parse_tool_calls(reply)
        if not calls:
            memory.append(session_id, "assistant", reply)
            return reply
        notes = []
        for name, args in calls:
            if calls_made >= tool_budget:
                notes.append(
                    f"[budget] per-turn tool-call limit ({tool_budget}) reached; "
                    "answer the user now without more tool calls."
                )
                break
            result = executor(name, args)
            calls_made += 1
            notes.append(f"[{name}] {result}")
        user = user + f"\n\nAssistant: {reply}\n\nTool results:\n" + "\n".join(notes)

    fallback = "I'm having trouble completing that request. Try /status or ask for a quote with topic and budget."
    memory.append(session_id, "assistant", fallback)
    return fallback


def format_job_notification(event: dict) -> str | None:
    stage = event.get("stage")
    jid = event.get("job_id", "")
    try:
        from .workspace import append_daily_memory
        if stage in ("paid", "fulfilled", "delivered", "declined"):
            append_daily_memory(f"job {jid}: {stage}")
    except Exception:
        pass
    if stage == "paid":
        return f"Payment received for job {jid} ({fmt(event.get('amount', 0))}). Fulfillment starting."
    if stage == "fulfilled":
        return f"Job {jid} fulfilled. Report saved."
    if stage == "delivered":
        return f"Your brief for {jid} is ready: {event.get('url', '')}"
    if stage == "declined":
        return f"Job {jid} declined: {event.get('reason', '')}"
    if stage == "payment_pending":
        return f"Awaiting payment for {jid}: {event.get('url', '')}"
    return None
