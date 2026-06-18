# SOLVENT — Architecture

**SOLVENT is an AI agent that operates as a profitable, self-funding business.**
It sells research briefs, collects payment through Stripe, and spends its own
revenue to provision the compute, data, and SaaS it needs to do the work — and
it refuses any job that doesn't clear a margin. The result is a fully automated
company with its own balance sheet that grows on its own.

## The one-sentence pitch
An agent you can hand a Stripe key and walk away from: it earns, it pays its own
bills, it never works at a loss, and it can't spend a dollar outside policy.

## Why it matters
Most agents can spend money; few can run as a business. SOLVENT closes the full
loop with **economic self-awareness** — it maintains a treasury, prices against its
own unit costs, and gates every action on projected profit. That's the difference
between a demo bot and a business.

## The money loop (per job)

```
 inbound job
     │
     ▼
 ┌─────────────┐   margin < floor?  ┌───────────┐
 │  MARGIN GATE│ ─────────────────▶ │  DECLINE  │
 │  (pricing)  │                    └───────────┘
 └─────┬───────┘ accept
       ▼
 ┌─────────────┐   EARN
 │   STRIPE    │ ── Checkout Session → webhook (primary) ──▶ + revenue
 └─────┬───────┘    idempotent stages in SQLite
       ▼
 ┌─────────────┐   FULFIL
 │  NEMOTRON   │ ── bounded tool-calling research loop ──▶ actual COGS
 └─────┬───────┘
       ▼
 ┌─────────────┐   DELIVER
 │  HOSTED+SMTP│ ── signed brief URL + optional email
 └─────┬───────┘
       ▼
 ┌─────────────┐   SPEND (each payment screened first)
 │ GUARDRAILS  │ ── NemoClaw policy: allowlist, caps, reserve, ROI
 │   → STRIPE  │ ── Issuing virtual card (test) or simulated spend ──▶ − expense
 └─────┬───────┘
       ▼
   BOOK P&L  ──▶ treasury updated, dashboard refreshed
```

Revenue is always collected **before** cost is incurred, and no single payment
can violate policy — the business is safe by construction and profitable by rule.

## How the sponsor stack maps in

| Layer | Sponsor tech | Where it lives in the code |
|---|---|---|
| **Reasoning / the analyst** | **NVIDIA Nemotron** (Llama-3.1-Nemotron-Ultra) via the OpenAI-compatible endpoint | `solvent/nemotron.py` |
| **Spend safety** | **NVIDIA NemoClaw**-style policy sandbox — every payment screened before it executes | `solvent/guardrails.py` |
| **Earn + Spend** | **Stripe** — Checkout Sessions, webhooks, idempotency keys; Issuing for outbound spend (test mode) | `solvent/stripe_client.py` |
| **Agent orchestration** | Idempotent **stage machine** + bounded Nemotron tool loop | `solvent/stages.py`, `solvent/tools.py`, `solvent/agent.py` |
| **Async processing** | SQLite queue + worker resume | `solvent/worker.py`, `solvent/queue.py` |
| **Delivery** | Hosted briefs + SMTP/outbox | `solvent/delivery.py`, `solvent/server.py` |
| **Auto-improvement** | Metrics-driven tuning | `solvent/improver.py` |
| **Observability** | JSON logs + Stripe reconciliation | `solvent/observability.py`, `solvent/reconcile.py` |
| **Economic memory** | the treasury / ledger that makes it a business | `solvent/treasury.py`, `solvent/pricing.py` |

## Key design choices

- **Viability:** the margin gate (`pricing.py`) means SOLVENT is *structurally*
  incapable of unprofitable work. In the demo it earns $223, spends $13.35, and
  books a 94% margin — and declines the one job that doesn't pencil out.
- **Safety:** `guardrails.py` enforces a vendor allowlist, a per-transaction cap,
  a rolling 24h budget, a minimum cash reserve, and a no-negative-ROI rule. This
  is the answer to "would you actually give an agent a payment credential?"
- **Runs anywhere:** with no API keys it runs on deterministic offline stubs, so
  the demo always works. Add `NVIDIA_API_KEY` for live Nemotron and a Stripe
  **test** key (`sk_test_...`) for real Payment Links. The client refuses live
  Stripe keys outright. In test mode, payment is verified via Checkout Session
  polling (or optional webhooks) before fulfilment; refunds use the PaymentIntent
  id (`pi_...`). Product/Price catalog is cached locally; Issuing provides
  capped virtual cards when enabled on the test account.

## Files
```
solvent/
  agent.py         thin orchestrator delegating to stages
  stages.py        idempotent stage machine (quote→paid→fulfill→deliver→spend)
  worker.py        async job processor + resume incomplete jobs
  server.py        FastAPI: webhooks, /jobs, /briefs (optional deps)
  tools.py         allowlisted research tools
  delivery.py      hosted briefs + SMTP/outbox email
  observability.py structured JSON logging
  improver.py      auto-tune pricing from job metrics
  reconcile.py     Stripe ↔ ledger reconciliation
  treasury.py      ledger, jobs, stages, metrics (SQLite)
  pricing.py       margin gate + COGS overrides
  guardrails.py    NemoClaw-style spend policy
  stripe_client.py Checkout Sessions + webhooks
  nemotron.py      Nemotron client + bounded tool loop
  service.py       research brief fulfillment
  jobs.py          sample inbound work
  dashboard.py     treasury HTML dashboard
run_demo.py        CLI demo (--async, --serve)
docs/PRODUCTION.md production deployment guide
```
