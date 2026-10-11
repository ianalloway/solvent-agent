"""SOLVENT demo Space: watch a tiny AI-agent business run its margin gate.

Everything here is OFFLINE and zero-key. It imports the real `solvent-agent`
package from PyPI and runs its built-in demo (simulated Stripe checkout and a
deterministic stub analyst), so no API keys and no network access are needed.

Source: https://github.com/ianalloway/solvent-agent
"""

from __future__ import annotations

import html
import os
import secrets
import tempfile
import threading
from contextlib import contextmanager
from typing import Any

import gradio as gr

# --------------------------------------------------------------------------
# Offline sandbox
# --------------------------------------------------------------------------
# solvent reads its runtime location and a few switches from os.environ. We run
# every demo inside a throwaway SOLVENT_HOME with live-service switches forced
# off, under a lock (the environment is process-global).
_LOCK = threading.Lock()
_LIVE_KEYS = (
    "STRIPE_API_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "NVIDIA_API_KEY",
    "SOLVENT_LIVE_SEARCH",
    "SOLVENT_ASYNC",
    "SOLVENT_ALLOW_POLL",
)


@contextmanager
def offline_sandbox():
    """Temporary SOLVENT_HOME + simulation-only environment (restored on exit)."""
    with _LOCK, tempfile.TemporaryDirectory(prefix="solvent-space-") as home:
        saved = {k: os.environ.get(k) for k in (*_LIVE_KEYS, "SOLVENT_HOME",
                 "SOLVENT_DELIVERY_SECRET", "SOLVENT_FORCE_STRIPE_SIMULATE")}
        try:
            for k in _LIVE_KEYS:
                os.environ.pop(k, None)
            os.environ["SOLVENT_HOME"] = home
            os.environ["SOLVENT_FORCE_STRIPE_SIMULATE"] = "1"
            os.environ["SOLVENT_DELIVERY_SECRET"] = secrets.token_urlsafe(48)
            yield home
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def usd(cents: int | float) -> str:
    return f"${cents / 100:,.2f}"


# --------------------------------------------------------------------------
# 1) The 5-job demo
# --------------------------------------------------------------------------
DEMO_HEADERS = ["Job", "Topic", "Verdict", "Price", "Est. cost", "Proj. margin", "Realized P&L", "Cash after"]


def run_demo(seed_dollars: float = 100.0) -> dict[str, Any]:
    """Run solvent's five SAMPLE_JOBS end to end (simulated) and collect results."""
    with offline_sandbox():
        from solvent.agent import Solvent
        from solvent.jobs import SAMPLE_JOBS
        from solvent.money import dollars_to_cents

        events: list[dict] = []
        agent = Solvent(seed_cents=dollars_to_cents(seed_dollars), fresh=True, on_event=events.append)
        for job in SAMPLE_JOBS:
            agent.handle_job(dict(job))
        snap = agent.t.snapshot()

    jobs: dict[str, dict] = {}
    order: list[str] = []
    for e in events:
        jid = e.get("job_id")
        if not jid:
            continue
        if jid not in jobs:
            jobs[jid] = {"id": jid}
            order.append(jid)
        row = jobs[jid]
        st = e.get("stage")
        if st == "quote":
            row.update(topic=e.get("title", ""), price=e["price"], est_cost=e["est_cost"],
                       margin_pct=e["margin_pct"], accept=e["accept"], reason=e.get("reason", ""))
        elif st == "declined":
            row["declined_reason"] = e.get("reason", "")
        elif st == "counter_offer":
            row["counter"] = e
        elif st == "booked":
            row["pnl"] = e["job_pnl"]
            row["balance"] = e["balance"]
        elif st == "cogs_reconciled":
            row["actual_cost"] = e.get("actual_cost_cents")
    return {"jobs": [jobs[j] for j in order], "snapshot": snap, "events": events, "seed": seed_dollars}


def jobs_table(result: dict[str, Any]) -> list[list[str]]:
    rows = []
    for j in result["jobs"]:
        accepted = j.get("accept", False)
        rows.append([
            j["id"],
            j.get("topic", ""),
            "✅ ACCEPTED" if accepted else "⛔ DECLINED",
            usd(j.get("price", 0)),
            usd(j.get("est_cost", 0)),
            f"{j.get('margin_pct', 0)}%",
            usd(j["pnl"]) if "pnl" in j else "—",
            usd(j["balance"]) if "balance" in j else "—",
        ])
    return rows


_CARD = ("border:2px solid {border};background:{bg};border-radius:12px;padding:14px 18px;"
         "margin:6px 0;color:#111;")


def declined_card(result: dict[str, Any]) -> str:
    cards = []
    for j in result["jobs"]:
        if j.get("accept", True):
            continue
        c = j.get("counter") or {}
        body = [
            f"<div style='font-size:1.1em'><b>⛔ {html.escape(j['id'])} declined</b> — "
            f"{html.escape(j.get('topic', ''))}</div>",
            f"<div>Customer offered <b>{usd(j.get('price', 0))}</b>; estimated cost "
            f"<b>{usd(j.get('est_cost', 0))}</b> → projected margin <b>{j.get('margin_pct')}%</b>.</div>",
            f"<div>Reason: <i>{html.escape(j.get('declined_reason') or j.get('reason', ''))}</i></div>",
        ]
        if c:
            body.append(
                f"<div style='margin-top:8px'>💡 <b>Counter-offer:</b> "
                f"{html.escape(str(c.get('message', '')))} — {usd(c.get('price_cents', 0))} at "
                f"<b>{c.get('margin_pct')}%</b> margin.</div>"
            )
            for dim, val in (c.get("scope_changes") or {}).items():
                body.append(f"<div style='margin-left:24px'>{html.escape(dim)} → {html.escape(str(val))}</div>")
        else:
            body.append("<div style='margin-top:8px'>No viable counter-offer.</div>")
        cards.append(f"<div style='{_CARD.format(border='#d9534f', bg='#fdecea')}'>{''.join(body)}</div>")
    return "".join(cards) or "<i>No jobs were declined.</i>"


def pnl_markdown(result: dict[str, Any]) -> str:
    s = result["snapshot"]
    grew = s["balance_cents"] - s["capital_cents"]
    verdict = (f"🟢 The agent grew its treasury by **{usd(grew)}**." if grew > 0
               else f"🔴 The agent lost **{usd(-grew)}**." if grew < 0 else "The agent broke even.")
    return (
        "### 📊 Final P&L / treasury snapshot\n\n"
        "| | |\n|---|---:|\n"
        f"| Seed capital | {usd(s['capital_cents'])} |\n"
        f"| Revenue (simulated Stripe) | {usd(s['revenue_cents'])} |\n"
        f"| Operating spend | {usd(s['expense_cents'])} |\n"
        f"| **Net profit** | **{usd(s['net_profit_cents'])}** ({s['margin_pct']}% margin) |\n"
        f"| **Cash balance** | **{usd(s['balance_cents'])}** |\n\n{verdict}"
    )


def timeline_text(result: dict[str, Any]) -> str:
    lines = []
    for e in result["events"]:
        st, jid = e.get("stage"), e.get("job_id", "")
        if st == "quote":
            lines.append(f"[{jid}] margin gate: price {usd(e['price'])} | est cost {usd(e['est_cost'])} | "
                         f"{e['margin_pct']}% → {'ACCEPT' if e['accept'] else 'DECLINE'}")
        elif st == "declined":
            lines.append(f"[{jid}] declined: {e.get('reason')}")
        elif st == "counter_offer":
            lines.append(f"[{jid}] counter-offer: {e.get('message')} ({e.get('margin_pct')}% margin)")
        elif st == "invoice":
            lines.append(f"[{jid}] checkout link issued (simulated): {usd(e['amount'])}")
        elif st == "paid":
            lines.append(f"[{jid}] payment confirmed (simulated): +{usd(e['amount'])}")
        elif st == "fulfilled":
            lines.append(f"[{jid}] brief written by deterministic stub ({e.get('tokens')} tokens)")
        elif st == "spend":
            lines.append(f"[{jid}] spend {usd(e['amount'])} → {e['vendor']} ({e['memo']})")
        elif st == "booked":
            lines.append(f"[{jid}] booked P&L {usd(e['job_pnl'])} | treasury {usd(e['balance'])}")
    return "\n".join(lines)


def on_run_demo(seed: float):
    try:
        seed = float(seed)
        if not 15 <= seed <= 100_000:
            raise ValueError
    except (TypeError, ValueError):
        raise gr.Error("Seed capital must be a number between 15 and 100000 (USD).")
    r = run_demo(seed)
    return jobs_table(r), declined_card(r), pnl_markdown(r), timeline_text(r)


# --------------------------------------------------------------------------
# 2) Try your own job: the margin gate dry-run (same as `solvent quote`)
# --------------------------------------------------------------------------
def quote_job(topic: str, budget_usd: float, tokens: int = 8000, market_calls: int = 2,
              search_calls: int = 6, margin_floor: float | None = None) -> dict[str, Any]:
    with offline_sandbox():
        from solvent.money import dollars_to_cents
        from solvent.pricing import PricingPolicy, quote
        from solvent.quote_cmd import build_job, format_quote

        policy = PricingPolicy() if margin_floor is None else PricingPolicy(margin_floor_pct=float(margin_floor))
        budget_cents = dollars_to_cents(budget_usd)
        job = build_job(topic, budget_cents, est_tokens=int(tokens),
                        market_data_calls=int(market_calls), web_search_calls=int(search_calls))
        q = quote(job, policy)
        return {"job": job, "quote": q, "policy": policy, "text": format_quote(job, q, policy)}


def on_quote(topic, budget, tokens, market_calls, search_calls, floor):
    topic = (topic or "").strip()
    if not topic:
        raise gr.Error("Please enter a topic.")
    try:
        budget = float(budget)
    except (TypeError, ValueError):
        raise gr.Error("Budget must be a number in USD.")
    if not 0 < budget <= 100_000:
        raise gr.Error("Budget must be greater than $0 and at most $100,000.")
    for name, v, hi in (("tokens", tokens, 200_000), ("market-data calls", market_calls, 100),
                        ("web searches", search_calls, 500)):
        if v is None or not 0 <= float(v) <= hi:
            raise gr.Error(f"{name} must be between 0 and {hi}.")
    if floor is None or not 0 <= float(floor) < 100:
        raise gr.Error("Margin floor must be between 0 and 99.9 (%).")
    r = quote_job(topic[:200], budget, tokens, market_calls, search_calls, floor)
    q = r["quote"]
    if q.accept:
        head = (f"<div style='{_CARD.format(border='#2e8b57', bg='#e9f7ef')}'><b>✅ ACCEPT</b> — "
                f"{usd(q.price_cents)} for an estimated {usd(q.est_cost_cents)} of cost "
                f"→ <b>{q.margin_pct}%</b> margin (floor {r['policy'].margin_floor_pct}%).</div>")
    else:
        co = q.counter_offer
        extra = (f"<div style='margin-top:8px'>💡 <b>Counter-offer:</b> {html.escape(str(co['message']))} — "
                 f"{usd(co['price_cents'])} at <b>{co['margin_pct']}%</b> margin.</div>") if co else ""
        head = (f"<div style='{_CARD.format(border='#d9534f', bg='#fdecea')}'><b>⛔ DECLINE</b> — "
                f"{html.escape(q.reason)}. Budget {usd(q.price_cents)} vs. est. cost "
                f"{usd(q.est_cost_cents)} → {q.margin_pct}% margin (floor {r['policy'].margin_floor_pct}%).{extra}</div>")
    breakdown = [[k.replace("_", " "), usd(v)] for k, v in q.cost_breakdown.items()]
    breakdown.append(["total", usd(q.est_cost_cents)])
    return head, breakdown, r["text"]


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
INTRO = """
# 🪙 SOLVENT: an AI agent that runs its own research business

SOLVENT quotes research jobs against its **own costs**, **declines the unprofitable ones**
(offering a counter-offer instead), and books every job into a treasury P&L.
This Space runs the **real [`solvent-agent`](https://pypi.org/project/solvent-agent/) package** in an
**offline demo mode**: simulated Stripe checkout, deterministic stub analyst, **no API keys, no network**.
[GitHub](https://github.com/ianalloway/solvent-agent) · `pip install solvent-agent`
"""

with gr.Blocks(title="SOLVENT: AI agent that declines unprofitable work") as demo:
    gr.Markdown(INTRO)
    with gr.Tab("▶ Run the 5-job demo"):
        gr.Markdown("Runs the five built-in sample jobs end to end. **J3** is deliberately "
                    "underpriced, so watch the margin gate decline it and counter-offer.")
        with gr.Row():
            seed = gr.Number(value=100, label="Seed capital (USD)", minimum=15, maximum=100000, scale=1)
            run_btn = gr.Button("Run the demo", variant="primary", scale=2)
        table = gr.Dataframe(headers=DEMO_HEADERS, interactive=False, wrap=True, label="Per-job results")
        declined = gr.HTML(label="Declined job + counter-offer")
        pnl = gr.Markdown()
        with gr.Accordion("Event timeline", open=False):
            log = gr.Textbox(lines=18, interactive=False, show_label=False)
        run_btn.click(on_run_demo, [seed], [table, declined, pnl, log], api_name="run_demo")
    with gr.Tab("🧮 Try your own job"):
        gr.Markdown("A **dry run of the margin gate**, same logic as `solvent quote` on the CLI. "
                    "Nothing is stored and no payment is made. Try a $6 budget to see a decline.")
        with gr.Row():
            topic = gr.Textbox(label="Topic", value="Competitive landscape for edge-AI chips, 2026", scale=3)
            budget = gr.Number(label="Customer budget (USD)", value=6, minimum=0.01, scale=1)
        with gr.Accordion("Job scope (optional)", open=False):
            with gr.Row():
                tokens = gr.Slider(1000, 40000, value=8000, step=500, label="Estimated analyst tokens")
                mcalls = gr.Slider(0, 10, value=2, step=1, label="Market-data pulls")
                scalls = gr.Slider(0, 30, value=6, step=1, label="Web searches")
                floor = gr.Slider(0, 90, value=35, step=1, label="Margin floor (%)")
        q_btn = gr.Button("Quote this job", variant="primary")
        verdict = gr.HTML()
        cost_tbl = gr.Dataframe(headers=["Cost line", "Estimate"], interactive=False, label="Estimated cost of fulfilment")
        with gr.Accordion("CLI-style output", open=False):
            cli_out = gr.Textbox(lines=14, interactive=False, show_label=False)
        q_btn.click(on_quote, [topic, budget, tokens, mcalls, scalls, floor],
                    [verdict, cost_tbl, cli_out], api_name="quote")
    with gr.Tab("ℹ About"):
        gr.Markdown(
            "- **What is simulated?** Stripe checkout/payments and the analyst's brief. "
            "They are deterministic stubs, so numbers here are illustrative, not real revenue.\n"
            "- **Run it yourself:** `pip install solvent-agent && solvent` (demo) or "
            "`solvent quote \"topic\" --budget 49`.\n"
            "- **Live mode** (real Stripe test mode, real Nemotron) needs your own keys and is not part of this Space.\n"
            "- Not financial or investment advice; the sample jobs are fictional.\n\n"
            "Code: [github.com/ianalloway/solvent-agent](https://github.com/ianalloway/solvent-agent) · "
            "PyPI: [solvent-agent](https://pypi.org/project/solvent-agent/) · MIT licensed."
        )

if __name__ == "__main__":
    demo.launch()
