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
  # Entry points and CLI
  __main__.py         `python -m solvent` / `solvent` command dispatcher
  cli.py              demo / interactive CLI (`solvent` with no subcommand)
  init.py             first-run scaffolding: app home, treasury DB (`solvent init`)
  onboarding.py       first-run terminal wizard
  config.py           onboarding wizard and config persistence
  config_cmd.py       view and edit configuration (`solvent config`)
  status.py           at-a-glance live state (`solvent status`)
  job_cmd.py          inspect and manage jobs (`solvent jobs`)
  logs.py             tail and filter the structured event log (`solvent logs`)
  upgrade.py          version check and upgrade helper (`solvent upgrade`)
  doctor.py           stack diagnostics
  paths.py            where runtime data lives (`SOLVENT_HOME` resolution)

  # Orchestration
  agent.py            the orchestrator (earn → fulfil → spend → book)
  stages.py           idempotent stage machine (quote→paid→fulfill→spend→publish→deliver)
  queue.py            SQLite-backed job queue helpers
  worker.py           async job processor + resume incomplete jobs
  jobs.py             sample inbound work

  # Money: pricing, payments, ledger
  pricing.py          the margin gate (+ counter-offers on a decline)
  quote_cmd.py        `solvent quote` — dry-run the margin gate
  money.py            one shared dollars → cents conversion
  treasury.py         SQLite ledger / balance sheet
  stripe_client.py    two-sided Stripe layer (earn + spend)
  webhook_log.py      durable log of received Stripe webhook events
  reconcile.py        reconcile Stripe payments against the ledger (`solvent reconcile`)
  checkout.py         payment reminders and link expiry (`solvent checkouts`)
  receipt.py          formatted job receipts (Telegram / email / HTTP)
  finance.py          income statement · unit economics · runway · forecast
  export.py           books export + period close (`solvent export`)

  # Spend safety
  spending.py         durable authorization at live provider boundaries
  resource_budget.py  quote-owned resource ceilings, reserved before metered calls
  guardrails.py       NemoClaw-style spend policy (caps · vendor budgets · velocity)
  guardrail_cmd.py    `solvent guardrails` — policy in force + vendor exposure
  security.py         defence layer (webhook signatures, key checks, replay, input sanitising)

  # Business operations
  backlog.py          capital-aware job prioritisation (`solvent backlog`)
  calibration.py      realized COGS → cost-model calibration (`solvent costs`)
  customers.py        lifetime value and repeat rate (`solvent customers`)
  simulate.py         policy simulator over synthetic demand (`solvent simulate`)
  optimize.py         policy search under a risk budget (`solvent optimize`)
  intake.py           commercial screen on inbound jobs (`solvent intake`)
  capacity.py         throughput ceiling and binding rule (`solvent capacity`)
  alerts.py           health sweep with exit codes (`solvent alerts`)
  products.py         the price list (`solvent products`)
  quality.py          deliverable quality gate (`solvent quality`)
  review.py           operator approval queue (`solvent review`)
  funnel.py           order funnel and win rate (`solvent funnel`)
  turnaround.py       measured delivery time (`solvent turnaround`)
  ops.py              operator-only business commands on the chat surface

  # Fulfilment and delivery
  nemotron.py         NVIDIA Nemotron client (+ offline stub)
  llm_backend.py      optional OpenAI-compatible backend (Ollama, LM Studio, vLLM)
  tools.py            allowlisted research tools for the bounded agent loop
  service.py          the product: an on-demand research brief
  pdf.py              dependency-free PDF rendering of the brief
  delivery.py         hosted brief delivery and optional SMTP email

  # Server, dashboard and chat
  server.py           FastAPI webhooks + job API + hosted briefs (serve)
  access.py           request-origin and abuse-limiting helpers for the server
  rate_limit.py       sliding-window rate limiter with SQLite persistence
  event_hub.py        thread-safe event hub for dashboard SSE
  dashboard.py        assembles and writes the treasury dashboard page
  dashboard_status.py status data shared by the page and SSE + escaping helpers
  dashboard_assets.py static dashboard CSS and JavaScript
  dashboard_chat.py   live dashboard chat panel + voice (Web Speech API)
  observability.py    structured JSON logging and event persistence
  gateway.py          channel router (Telegram → chat sessions)
  chat.py             conversational loop + business tools
  memory.py           Hermes-style session memory
  notifications.py    cross-process chat notifications (JSONL outbox)
  pairing.py          OpenClaw-style pairing for Telegram DMs
  qr.py               QR codes for pairing
  workspace.py        SOUL/BRAIN/AGENTS prompt assembly
  _filelock.py        portable advisory file locking

  channels/           Telegram long-poll adapter
run_demo.py           the full business loop (CLI entry point)
tests/                pytest suite
docs/                 screenshots and supporting docs
```
