<div align="center">

# 🪙 SOLVENT

**A self-funding analyst agent with a real treasury, margin gate, and spend policy.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Stars](https://img.shields.io/github/stars/ianalloway/solvent-agent?style=social)](https://github.com/ianalloway/solvent-agent/stargazers)

</div>

SOLVENT sells research briefs, collects payment through Stripe, fulfills the work with NVIDIA Nemotron or an offline stub, pays its own approved vendors, and books the result. It declines work that does not meet its margin floor.

```text
job → quote → collect payment → fulfill → deliver → guardrailed spend → book P&L
```

## Quick start

The core demo has no third-party dependencies and needs no API keys:

```bash
git clone https://github.com/ianalloway/solvent-agent.git
cd solvent-agent
python3 run_demo.py --no-onboard
```

The batch run processes four sample jobs, including an unprofitable job that is declined. It writes reports under `data/reports/` and renders `treasury_dashboard.html`.

![SOLVENT treasury dashboard](docs/dashboard.png)

Install the package for the `solvent` command:

```bash
pip install -e .
solvent
solvent finance
solvent --help
```

Runtime data is written to the repository when running from a checkout and to `~/.solvent` when installed elsewhere. Set `SOLVENT_HOME=/path/to/home` to override it.

## Optional features

Third-party integrations are explicit extras; there are no parallel requirements files to keep in sync.

```bash
pip install -e ".[stripe]"       # Stripe test-mode checkout and spend
pip install -e ".[serve]"        # FastAPI, webhooks, hosted briefs
pip install -e ".[telegram]"     # Telegram channel
pip install -e ".[rich]"         # terminal dashboard
pip install -e ".[qr]"           # pairing QR images
pip install -e ".[all]"          # every runtime feature
pip install -e ".[dev]"          # pytest + HTTP test client
```

Live Stripe keys are deliberately refused. Use `sk_test_...` or `rk_test_...`.

## Running modes

### Batch demo

```bash
python3 run_demo.py --no-onboard
# or, after installation:
solvent --no-onboard
```

### Interactive jobs

```bash
python3 run_demo.py --interactive --no-onboard
```

Enter a topic and budget. Use `/fund 200` to add operating capital during the session.

### Programmatic API

```python
from solvent.agent import Solvent
from solvent.jobs import SAMPLE_JOBS

agent = Solvent(seed_cents=10_000)
result = agent.handle_job(SAMPLE_JOBS[0])
snapshot = agent.t.snapshot()
```

### Guardrails demonstration

```bash
python3 demo_guardrails.py
```

This exercises vendor allowlisting, transaction caps, daily budget, cash reserve, and projected-ROI checks without touching Stripe.

## Production-shaped stack

Install the integrations and set a delivery secret:

```bash
pip install -e ".[serve,stripe]"
export SOLVENT_DELIVERY_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export SOLVENT_BASE_URL=http://127.0.0.1:8787

# Terminal 1: API, webhooks, hosted briefs, dashboard
solvent serve --port 8787

# Terminal 2: resume and process queued work
solvent worker
```

The HTTP surface includes:

- `GET /health`
- `GET /` — interactive dashboard
- `GET /api/status`
- `GET /api/events` — Server-Sent Events
- `POST /api/chat`
- `POST /jobs`
- `POST /webhooks/stripe`
- signed `GET /briefs/{job_id}` delivery URLs

See [docs/PRODUCTION.md](docs/PRODUCTION.md) for Stripe webhook and SMTP setup.

## Telegram

```bash
pip install -e ".[telegram,serve,stripe]"
export TELEGRAM_BOT_TOKEN=...

solvent serve
solvent worker
solvent telegram
```

Unknown users are paired before they can interact with the agent. See [docs/TELEGRAM.md](docs/TELEGRAM.md).

## Operations

```bash
solvent status
solvent jobs
solvent jobs show <id>
solvent jobs events <id>
solvent jobs retry <id>
solvent jobs cancel <id>
solvent finance --json
solvent reconcile --since 7d
solvent tune                 # dry run
solvent tune --apply
solvent webhooks stats
solvent webhooks list
solvent doctor
solvent tui
```

`solvent report` remains an alias for `solvent finance`; `solvent retry <id>` remains a compatibility alias for `solvent jobs retry <id>`.

## Safety model

- **Profitability:** pricing estimates COGS before checkout and rejects work below the configured margin floor.
- **Payment ordering:** revenue is collected before fulfillment cost is incurred.
- **Spend policy:** every vendor payment is checked against an allowlist, per-transaction cap, rolling budget, reserve floor, and job ROI.
- **Idempotency:** job stages and Stripe references are stored in SQLite so retries do not double-charge or double-book.
- **Credential boundary:** API keys stay in environment variables; Stripe live-mode keys are rejected.
- **Offline behavior:** deterministic stubs keep the complete business loop runnable without credentials.

## Main modules

```text
solvent/agent.py          thin public orchestrator
solvent/stages.py         idempotent money-loop state machine
solvent/treasury.py       SQLite ledger, jobs, stages, metrics
solvent/pricing.py        cost model and margin gate
solvent/guardrails.py     outbound spend policy
solvent/stripe_client.py  checkout, verification, refund, vendor spend
solvent/service.py        research-brief fulfillment
solvent/delivery.py       hosted links, HTML, SMTP/outbox
solvent/server.py         FastAPI application
solvent/worker.py         async resume/queue processor
solvent/gateway.py        dashboard and Telegram channel routing
solvent/finance.py        statements, runway, trend, forecast
```

Repository-specific architecture and safety conventions are also summarized in [AGENTS.md](AGENTS.md).

## Tests

```bash
pip install -e ".[dev]"
python3 -m pytest -q
```

CI runs Python 3.10–3.12, verifies the zero-dependency core, builds and installs the wheel, then installs every optional feature and exercises the batch demo, interactive flow, guardrails, worker, command surfaces, real HTTP server, and QR endpoint.

## License

MIT. See [LICENSE](LICENSE).
