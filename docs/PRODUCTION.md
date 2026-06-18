# SOLVENT Production Guide

Run SOLVENT as a webhook-first, async agent with hosted delivery.

## Quick start (offline demo)

```bash
python3 run_demo.py --no-onboard
```

## HTTP server + worker (production shape)

```bash
pip install -r requirements.txt
pip install -r requirements-serve.txt

export SOLVENT_BASE_URL=http://127.0.0.1:8787
export SOLVENT_DELIVERY_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')

# Terminal 1 — API + webhooks + hosted briefs
python3 -m solvent serve --port 8787

# Terminal 2 — async job processor
python3 -m solvent worker

# Or combined dev mode:
python3 run_demo.py --serve --no-onboard
```

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

# Auto-improvement (dry-run by default)
python3 -m solvent tune
python3 -m solvent tune --apply
```

## Environment variables

| Variable | Purpose |
|----------|---------|
| `STRIPE_API_KEY` | Test/restricted Stripe key |
| `STRIPE_WEBHOOK_SECRET` | Webhook signature verification |
| `SOLVENT_BASE_URL` | Checkout success URLs + hosted briefs |
| `SOLVENT_DELIVERY_SECRET` | HMAC token for `/briefs/{id}`; required, at least 32 characters, high entropy, and not a placeholder |
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

Idempotent stages are recorded in SQLite (`job_stages`). Metrics in `job_metrics` feed `solvent tune`.
