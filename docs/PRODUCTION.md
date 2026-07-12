# SOLVENT production guide

SOLVENT's production shape is webhook-first: the HTTP process creates checkout sessions and receives Stripe events; the worker resumes paid jobs and fulfills them.

## Install

Use package extras as the single dependency source:

```bash
pip install -e ".[serve,stripe]"
```

## Configure

```bash
export SOLVENT_BASE_URL=https://solvent.example.com
export SOLVENT_DELIVERY_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export STRIPE_API_KEY=sk_test_...
export STRIPE_WEBHOOK_SECRET=whsec_...
```

`SOLVENT_DELIVERY_SECRET` must be a non-placeholder value of at least 32 characters. SOLVENT refuses Stripe live-mode keys.

## Run

Use separate processes so either side can restart independently:

```bash
# API, Stripe webhooks, hosted briefs, dashboard
solvent serve --host 0.0.0.0 --port 8787

# Queue and incomplete-job processor
solvent worker
```

The dashboard is served at `/`. Health and status endpoints are available at `/health` and `/api/status`.

## Stripe webhook

Create a Stripe test-mode webhook endpoint:

```text
POST https://solvent.example.com/webhooks/stripe
```

Subscribe to `checkout.session.completed`. A verified event records revenue idempotently and lets the worker continue fulfillment.

For a CLI-only polling flow, explicitly enable the legacy poller:

```bash
export SOLVENT_ALLOW_POLL=1
```

## Submit a job

```bash
curl -X POST https://solvent.example.com/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "J99",
    "topic": "EV battery supply chain",
    "budget_cents": 7500,
    "customer_email": "you@example.com"
  }'
```

The response includes the job status and checkout URL. Jobs remain `awaiting_payment` until Stripe confirms payment.

## Delivery

Signed brief URLs are generated from `SOLVENT_BASE_URL` and `SOLVENT_DELIVERY_SECRET`.

Without SMTP, messages are written to the local outbox. To send email:

```bash
export SMTP_HOST=smtp.example.com
export SMTP_PORT=587
export SMTP_USER=...
export SMTP_PASS=...
export SMTP_FROM=agent@example.com
```

## Operations

```bash
solvent status
solvent jobs
solvent jobs retry <id>
solvent finance
solvent reconcile --since 7d
solvent tune
solvent tune --apply
solvent webhooks stats
solvent webhooks failed
solvent doctor
```

## Runtime data

All runtime artifacts honor `SOLVENT_HOME`:

```bash
export SOLVENT_HOME=/var/lib/solvent
```

That directory contains the treasury database, webhook log, reports, dashboard data, logs, and outboxes. Back it up and mount it on persistent storage.

## Relevant environment variables

| Variable | Purpose |
|---|---|
| `SOLVENT_HOME` | Runtime data root |
| `SOLVENT_BASE_URL` | Public URL used in checkout and delivery links |
| `SOLVENT_DELIVERY_SECRET` | HMAC secret for signed brief URLs |
| `STRIPE_API_KEY` | Stripe test or restricted test key |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signature secret |
| `SOLVENT_ALLOW_POLL` | Enable legacy CLI payment polling |
| `SOLVENT_LOG_JSON` | Emit structured JSON logs |
| `NVIDIA_API_KEY` | Enable live Nemotron inference |
| `SMTP_*` | Optional email delivery |
