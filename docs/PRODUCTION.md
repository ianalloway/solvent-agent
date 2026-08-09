# SOLVENT Production Guide

Run SOLVENT as a webhook-first, async agent with hosted delivery.

## Quick start (offline demo)

```bash
python3 run_demo.py --no-onboard
```

## HTTP server + worker (production shape)

```bash
pip install -e ".[serve]"

export SOLVENT_BASE_URL=http://127.0.0.1:8787
export SOLVENT_DELIVERY_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
export SOLVENT_DASHBOARD_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')

# Terminal 1 — API + webhooks + interactive dashboard
python3 -m solvent serve --port 8787
open "http://127.0.0.1:8787/?token=$SOLVENT_DASHBOARD_TOKEN"

# Terminal 2 — async job processor
python3 -m solvent worker
```

### Interactive dashboard + voice

`python3 -m solvent serve` serves a live dashboard at `/` protected by `SOLVENT_DASHBOARD_TOKEN`. Pass it as `?token=...` in a browser URL or as `X-Solvent-Dashboard-Token` for API clients. The protected dashboard includes:

- **SSE** (`GET /api/events`) — treasury metrics, job cards, and console log update in real time
- **Chat** (`POST /api/chat`) — talk to the agent; supports `/status`, `/jobs`, `/quote topic | 50`
- **Voice** — mic button uses browser Web Speech API (Chrome/Edge); toggle 🔊 for spoken replies
- **Jobs** (`POST /api/job`) — same payload as `POST /jobs`

## Stripe setup (test mode)

1. Set `STRIPE_API_KEY=sk_test_...` or restricted `rk_test_...`
2. Create webhook endpoint: `POST {SOLVENT_BASE_URL}/webhooks/stripe`
3. Subscribe to `checkout.session.completed`
4. Set `STRIPE_WEBHOOK_SECRET=whsec_...`
5. Submit jobs via `POST /jobs` — response includes `checkout_url`

Polling is disabled by default. For CLI-only test flows:

```bash
export SOLVENT_ALLOW_POLL=1
```

## Submit a job

```bash
curl -X POST http://127.0.0.1:8787/jobs \
  -H 'Content-Type: application/json' \
  -d '{"id":"J99","topic":"EV battery supply chain","budget_cents":7500,"customer_email":"you@example.com"}'
```

## Email delivery (optional)

Without SMTP, briefs are written to `data/outbox/{job_id}.eml`.

```bash
export SMTP_HOST=smtp.example.com
export SMTP_PORT=587
export SMTP_USER=...
export SMTP_PASS=...
export SMTP_FROM=agent@yourdomain.com
```

## Operations

```bash
# Structured JSON logs
export SOLVENT_LOG_JSON=1

# Reconcile Stripe vs ledger
python3 -m solvent reconcile --since 7d
```

## Environment variables

| Variable | Purpose |
|----------|---------|
| `STRIPE_API_KEY` | Test/restricted Stripe key |
| `STRIPE_WEBHOOK_SECRET` | Webhook signature verification |
| `SOLVENT_BASE_URL` | Checkout success URLs + hosted briefs |
| `SOLVENT_DELIVERY_SECRET` | HMAC token for `/briefs/{id}`; required, at least 32 characters, high entropy, and not a placeholder |
| `SOLVENT_DASHBOARD_TOKEN` | Bearer-style shared secret for `/`, `/api/status`, `/api/events`, `/api/chat`, and `/api/job`; set to a high-entropy value before serving the dashboard |
| `SOLVENT_ASYNC` | Non-blocking payment (worker resumes jobs) |
| `SOLVENT_ALLOW_POLL` | Legacy payment polling |
| `SOLVENT_LOG_JSON` | JSON lines to stderr + `data/solvent.log` |
| `NVIDIA_API_KEY` | Live Nemotron (optional) |
| `SMTP_*` | Email delivery |

## Architecture

```
POST /jobs → quote → Checkout Session → awaiting_payment
     ↓ webhook checkout.session.completed
worker → paid → fulfill (tool agent) → deliver → spend → book
```

Idempotent stages are recorded in SQLite (`job_stages`). Estimated vs. actual COGS are recorded in `job_metrics` for margin-drift analysis.
