# SOLVENT — Architecture

**SOLVENT is an AI agent that operates as a profitable, self-funding business.**
It sells research briefs, collects payment through Stripe, and spends its own
revenue to provision the compute, data, and SaaS it needs to do the work — and
it refuses any job that doesn't clear a margin. The result is a fully automated
company with its own balance sheet that grows on its own.

## The one-sentence pitch
An agent you can hand a Stripe key and walk away from: it earns, it pays its own
bills, it never works at a loss, and it can't spend a dollar outside policy.

## Why it wins the brief
The hackathon asks for "agents that can earn, spend, and run real operations."
SOLVENT closes the entire loop and adds the missing piece almost no agent has:
**economic self-awareness.** It maintains a treasury, prices against its own unit
costs, and gates every action on projected profit. That's the difference between
a demo bot and a business.

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
 │   STRIPE    │ ── creates Payment Link, collects payment ──▶ + revenue
 └─────┬───────┘
       ▼
 ┌─────────────┐   FULFIL
 │  NEMOTRON   │ ── produces the research brief ──▶ itemized resource usage
 └─────┬───────┘
       ▼
 ┌─────────────┐   SPEND (each payment screened first)
 │ GUARDRAILS  │ ── NemoClaw policy: allowlist, caps, reserve, ROI
 │   → STRIPE  │ ── pays nvidia-nemotron, market-data, saas ──▶ − expense
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
| **Earn + Spend** | **Stripe Skills** — Payment Links / invoices to charge customers; scoped agent payments to pay vendors and provision SaaS | `solvent/stripe_client.py` |
| **Agent orchestration** | **Hermes / Nous** tool-calling agent loop | `solvent/agent.py` |
| **Economic memory** | the treasury / ledger that makes it a business | `solvent/treasury.py`, `solvent/pricing.py` |

## Design choices that matter to the judges

- **Viability:** the margin gate (`pricing.py`) means SOLVENT is *structurally*
  incapable of unprofitable work. In the demo it earns $223, spends $13.35, and
  books a 94% margin — and declines the one job that doesn't pencil out.
- **Safety:** `guardrails.py` enforces a vendor allowlist, a per-transaction cap,
  a rolling 24h budget, a minimum cash reserve, and a no-negative-ROI rule. This
  is the answer to "would you actually give an agent a payment credential?"
- **Runs anywhere:** with no API keys it runs on deterministic offline stubs, so
  the demo always works. Add `NVIDIA_API_KEY` for live Nemotron and a Stripe
  **test** key (`sk_test_...`) for real Payment Links. The client refuses live
  Stripe keys outright.

## Files
```
solvent/
  agent.py         the orchestrator (earn → fulfil → spend → book)
  treasury.py      the ledger / balance sheet
  pricing.py       the margin gate
  guardrails.py    NemoClaw-style spend policy
  stripe_client.py two-sided Stripe layer (earn + spend)
  nemotron.py      NVIDIA Nemotron client (+ offline stub)
  service.py       the product: an on-demand research brief
  jobs.py          sample inbound work
  dashboard.py     renders the treasury to HTML
run_demo.py        the full business loop
demo_guardrails.py the safety story
```
