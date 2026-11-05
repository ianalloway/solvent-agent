# Tests and repository layout

<sub>Moved from the main [README](../README.md).</sub>

## 🧪 Tests

```bash
pip install "solvent-agent[dev]"
python3 -m pytest tests/ -v
ruff check solvent tests run_demo.py
```

Unit tests cover: pricing & margin gate · guardrail policy · treasury ledger · Stripe client (simulate + test mode) · config/onboarding.

---

## 📁 Repository Layout

```
solvent/
  __main__.py      `python -m solvent` / `solvent` command dispatcher
  cli.py           demo / interactive CLI (`solvent` with no subcommand)
  agent.py         the orchestrator (earn → fulfil → spend → book)
  stages.py        idempotent stage machine (quote→paid→fulfill→spend→publish→deliver)
  spending.py      durable authorization at live provider boundaries
  treasury.py      SQLite ledger / balance sheet
  pricing.py       the margin gate (+ counter-offers on a decline)
  quote_cmd.py     `solvent quote` — dry-run the margin gate
  guardrails.py    NemoClaw-style spend policy (caps · vendor budgets · velocity)
  guardrail_cmd.py `solvent guardrails` — policy in force + vendor exposure
  backlog.py       capital-aware job prioritisation (`solvent backlog`)
  calibration.py   realized COGS → cost-model calibration (`solvent costs`)
  customers.py     lifetime value and repeat rate (`solvent customers`)
  simulate.py      policy simulator over synthetic demand (`solvent simulate`)
  optimize.py      policy search under a risk budget (`solvent optimize`)
  checkout.py      payment reminders and link expiry (`solvent checkouts`)
  intake.py        commercial screen on inbound jobs (`solvent intake`)
  capacity.py      throughput ceiling and binding rule (`solvent capacity`)
  alerts.py        health sweep with exit codes (`solvent alerts`)
  export.py        books export + period close (`solvent export`)
  products.py      the price list (`solvent products`)
  quality.py       deliverable quality gate (`solvent quality`)
  review.py        operator approval queue (`solvent review`)
  funnel.py        order funnel and win rate (`solvent funnel`)
  turnaround.py    measured delivery time (`solvent turnaround`)
  ops.py           operator-only business commands on the chat surface
  stripe_client.py two-sided Stripe layer (earn + spend)
  nemotron.py      NVIDIA Nemotron client (+ offline stub)
  service.py       the product: an on-demand research brief
  pdf.py           dependency-free PDF rendering of the brief
  jobs.py          sample inbound work
  dashboard.py     renders the treasury to HTML + JSON
  finance.py       income statement · unit economics · runway · forecast
  config.py        onboarding wizard and config persistence
  server.py        FastAPI webhooks + job API + hosted briefs (serve)
  worker.py        async job processor + resume incomplete jobs
  gateway.py       channel router (Telegram → chat sessions)
  chat.py          conversational loop + business tools
  memory.py        Hermes-style session memory
  doctor.py        stack diagnostics
  workspace.py     SOUL/BRAIN/AGENTS prompt assembly
  channels/        Telegram long-poll adapter
run_demo.py        the full business loop (CLI entry point)
tests/             pytest suite
docs/              screenshots and supporting docs
```
