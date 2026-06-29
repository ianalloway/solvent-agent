# SOLVENT: A Self-Funding AI Agent

**A case study on building an agent that earns revenue, pays its own bills, and refuses unprofitable work.**

*by Ian Alloway · [github.com/ianalloway/solvent-agent](https://github.com/ianalloway/solvent-agent)*

---

## 1. The Thesis

Here's the question that started it: **can an AI agent be self-funding?**

Not "can it do tasks." Not "can it use tools." Those are solved problems. I mean: can an agent operate as a *business* — quote a job, collect payment, deliver the work, pay its own vendors, and end the day with more money than it started? Can its revenue exceed its compute costs, structurally and provably, by design rather than by luck?

Most agents can spend money. Almost none can run as a business. The difference matters. An agent that buys API calls is a cost center. An agent with a treasury, a pricing engine, and a margin gate is something else — it's an autonomous economic unit. It has a balance sheet. It can be profitable or unprofitable. And if you get the architecture right, it can be *incapable* of losing money.

I wanted to prove this wasn't theoretical. So I built SOLVENT: an agent that sells on-demand research briefs, collects payment through Stripe, fulfils the work using NVIDIA Nemotron, pays for its own inference and data costs, and tracks a real profit-and-loss statement in a SQLite ledger. Every job is profit-gated before it starts. Every payment is screened by a deterministic policy layer before it executes. The agent literally cannot spend more than it earns.

This was built for the NVIDIA × Stripe × Nous Research Agent Accelerated Business Hackathon, but the idea outgrew the competition. I think it's one of the more honest demonstrations of what "agentic commerce" actually requires — and where it still breaks.

---

## 2. Architecture

SOLVENT is a stage machine. Each inbound job runs through the same pipeline, and every stage is idempotent — recorded in SQLite with a unique key, so a crash or restart resumes from exactly where it left off. No double-charges, no lost work.

Here's the loop:

```
inbound job → MARGIN GATE → STRIPE (earn) → NEMOTRON (fulfil)
           → GUARDRAILS → STRIPE (spend) → BOOK P&L
```

### Job intake

Jobs arrive as structured requests — a research topic, a client budget, and estimated resource needs (token count, market-data calls, web searches). In the demo these come from a pre-loaded batch. In production they come from a web API, a Telegram bot with DM-based pairing, or the browser dashboard's chat panel. Every input passes through a security layer that sanitizes for prompt injection before it touches the model.

### The margin gate (`pricing.py`)

This is the core economic discipline. Before accepting any job, the agent estimates what the work will *cost* to fulfil — Nemotron inference at $0.30/1k tokens, market-data pulls at $1.20 each, web searches at $0.08, PDF render at $0.40, email delivery at $0.05. It compares the client's budget (which is the price — you can't charge more than someone will pay) against that estimated cost. If the projected margin doesn't clear a **35% floor**, or if the order is under a **$15 minimum**, the job is declined. No Stripe call is ever made.

The agent is *structurally incapable of unprofitable work*. That's not a policy it follows — it's a gate it cannot pass.

### Stripe — earn (`stripe_client.py`)

Accepted jobs get a real Stripe Checkout Session. The agent creates a Payment Link, records the `cs_...` session ID and `pi_...` PaymentIntent on the ledger, and either polls the session until `payment_status == paid` (demo/sync mode) or waits for a `checkout.session.completed` webhook (production/async mode). Revenue is **always collected before cost is incurred**. The client refuses live Stripe keys outright — test mode only. Product and Price objects are cached locally so repeated runs reuse a single "SOLVENT Research Brief" product instead of cluttering your Stripe dashboard.

### Fulfillment (`nemotron.py`, `service.py`)

Payment confirmed, the work begins. The agent calls NVIDIA Nemotron (Llama-3.1-Nemotron-Ultra-253B) via the OpenAI-compatible endpoint at `integrate.api.nvidia.com`. The fulfillment loop is bounded: the model can request tools — `web_search`, `market_data`, `summarize` — but only from an allowlist, and only up to a capped number of rounds and total tool calls. After gathering evidence, it writes a decision-ready research brief in markdown, which gets rendered to HTML and hosted at a signed URL.

The critical design choice here: **with no API key, the entire system still runs end-to-end on a deterministic offline stub.** The stub produces a plausible brief and returns estimated token counts. This means the demo always works — judges, recruiters, anyone can `git clone && python3 run_demo.py` and see the full money loop in 30 seconds with zero credentials. Add `NVIDIA_API_KEY` and the stub transparently swaps for live Nemotron inference.

### Guardrails (`guardrails.py`)

This is the answer to the question everyone asks: *"Would you actually give an agent a payment credential?"*

Every outbound spend passes through five deterministic checks before any Stripe call:

1. **Vendor allowlist** — money can only go to pre-approved vendors (Nemotron compute, market-data API, web-search API, PDF SaaS, email SaaS).
2. **Per-transaction cap** — no single payment over $50.
3. **Rolling 24-hour budget** — total spend bounded at $250/day.
4. **Solvency rule** — never spend below a $20 minimum cash reserve.
5. **ROI rule** — never spend on a job projected to be unprofitable.

These aren't suggestions the model can override. They're plain Python that runs before the Stripe call, raises `GuardrailError` on violation, and are fully tested. If a spend is blocked after payment has already been collected, the agent automatically refunds the client via the original PaymentIntent and books the refund on the ledger.

### The treasury (`treasury.py`)

This is SOLVENT's economic memory. A SQLite database holds every ledger entry — capital seeds, revenue, expenses — each stamped with job ID, vendor, Stripe references, and timestamp. It tracks balance, revenue, expenses, net profit, and margin percentage. A separate `job_metrics` table records estimated vs. actual COGS for every job, enabling margin-drift detection and, eventually, automated pricing tuning. File-level locking with `fcntl` guarantees atomicity across processes.

---

## 3. What Happened

I ran a batch of four sample jobs to exercise the full loop. Here's what the agent did:

| Job | Topic | Budget | Outcome |
|-----|-------|--------|---------|
| J1 | Competitive landscape for AI inference chips, 2026 | $49 | Accepted, fulfilled, booked |
| J2 | Stablecoin payment volumes and take-rate outlook | $75 | Accepted, fulfilled, booked |
| J3 | One-line definition of EBITDA | $6 | **Declined** — below $15 minimum |
| J4 | Edge-AI adoption in industrial robotics: 18-month outlook | $99 | Accepted, fulfilled, booked |

**Final P&L: $223.00 revenue, $13.35 operating spend, $209.65 net profit, 94% margin.**

The agent quoted J3 — a student wanting a cheap one-line answer for six bucks — and declined it without touching Stripe. That's the margin gate working as intended. The other three cleared the 35% floor, collected payment, produced briefs, paid their vendor bills (inference, data, rendering, delivery), and booked profit to the ledger.

Now, let me be honest about what this is and isn't. Those revenue and cost figures are real in the sense that they flow through actual Stripe Checkout Sessions and a real ledger with real accounting. But in demo mode, Stripe runs in test mode (simulated payment confirmation), and without an `NVIDIA_API_KEY`, the Nemotron calls hit the offline stub. When you add both keys, the briefs are genuinely written by Llama-3.1-Nemotron-Ultra and the payment links are real Stripe test-mode links you can pay with `4242 4242 4242 4242`.

What I proved is that the *architecture* works end-to-end: the margin gate correctly declines unprofitable work, the stage machine is crash-safe and idempotent, the guardrails block out-of-policy spends, the refund-on-failure path triggers correctly when a spend is blocked post-payment, and the treasury produces an honest P&L. The loop closes. The agent earns, it pays its bills, it books profit, and it declines work that doesn't pencil out.

What I have *not* yet proven is whether this runs unattended against real paying customers at scale, over weeks, with live money. That's a different test, and it's next.

---

## 4. Lessons

**Structural profitability beats policy compliance.** I could have written "don't lose money" in the system prompt and hoped the model obeyed. Instead, the margin gate is deterministic code that runs before Stripe is ever called. The model has no say in whether an unprofitable job is accepted. This is the single most important design decision in the project. Agents will surprise you; arithmetic won't.

**Offline-first is non-negotiable for demos.** The deterministic Nemotron stub saved me more times than I can count. Every demo, every judge interaction, every "let me just show you this" moment worked because the agent never depended on a network call being up. The stub isn't a hack — it's a first-class citizen that returns the same interface as the live path.

**COGS reconciliation is where reality bites.** The `reconcile_cogs` function compares estimated cost against actual cost after fulfillment. Margin drift happens. The model uses more tokens than estimated; a web search returns more results than expected. The system flags drift above 15% and records it in `job_metrics`. This is the seed of the auto-improver — after enough jobs, `solvent tune` can propose pricing adjustments to keep margins honest.

**Idempotency is the whole game.** Every stage writes a completion record with a unique key (`quote:J1`, `paid:J1:cs_...`, `spend:J1:nvidia-nemotron`). A crash at any point means a clean resume, not a double-charge or a half-delivered brief. This took more engineering than the "interesting" parts, and it's what separates a demo from something you could actually deploy.

**The security layer matters more than you think.** Job inputs pass through prompt-injection sanitization before reaching the model. A malicious topic string can't trick the agent into requesting treasury actions or payment tools — those are explicitly outside the model's tool allowlist. When you give an agent a Stripe key, input validation stops being optional.

---

## 5. What's Next

The architecture is proven. The next phase is about making it real:

- **Live operation.** Run SOLVENT against real test-mode Stripe with live Nemotron for an extended period — not a single batch, but continuous operation. Track whether margin drift accumulates, whether the auto-improver's pricing adjustments converge, whether the guardrails hold under load.

- **More services.** Research briefs are the first product. The service layer is pluggable — any job type that can estimate its COGS upfront and produce a deliverable fits the loop. Code review, data analysis, document generation — same margin gate, same guardrails, same treasury.

- **Stripe Issuing in production.** The spend side currently uses simulated vendor payments or test-mode Issuing virtual cards. Moving to capped, single-use virtual debit cards for each vendor payment would make outbound spend as real and auditable as inbound revenue.

- **Multi-model fulfillment.** Nemotron is the reasoning engine today, but the `complete()` interface is model-agnostic. Different job types could route to different models at different price points — and the margin gate would decide which are economical.

- **Telegram as the primary surface.** The bot already supports OpenClaw-style DM pairing, job commissioning in natural language, and push notifications when jobs are paid and delivered. Making chat the main interface — not the CLI — is where this starts to feel like a product instead of a repo.

The bigger question this project raised for me: if an agent can be self-funding at the micro level — one job, one payment, one P&L entry — what happens at scale? What does a fleet of margin-gated agents look like? An agent economy isn't agents that can spend. It's agents that can *run a business*. SOLVENT is a proof that the loop closes. What comes next is finding out how far it scales.

---

*Code: [github.com/ianalloway/solvent-agent](https://github.com/ianalloway/solvent-agent) · Ian Alloway · [ianalloway.xyz](https://ianalloway.xyz)*
