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
4. Set `STRIPE_WEBHOOK_SECRET=whsec_...` (required: with no secret `/webhooks/stripe` rejects every request, and requests with a bad signature are rejected without being stored)
5. Submit jobs via `POST /jobs` — response includes `checkout_url` (also as `url`)

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

Anonymous callers may only submit a job. The reply carries the Stripe `url` /
`checkout_url`, the `amount_cents` and the `job_id`; a decline carries a generic
reason only (never costs, margins, treasury state or other customers' jobs).
Only the documented fields are read (`id`, `topic`, `budget_cents`,
`customer_email`, `est_tokens`, `market_data_calls`, `web_search_calls`,
`context`, `product`); bodies over 32 KB are refused with `413`, and an `id`
that already exists is refused with `409` (omit `id` to get a generated one).

Check a job's status with `GET /jobs/{id}`. Without the dashboard token you get
`{"job": {"id", "status"}}` (this is what a customer sees after cancelling out
of Stripe Checkout); with `X-Solvent-Dashboard-Token` you get the full row and
metrics, including the customer email and costs.

## Which routes are public

| Route | Access |
|-------|--------|
| `GET /health` | Public: `{status, version}`. With the dashboard token it also reports `balance_cents`. |
| `POST /jobs`, `GET /jobs/{id}` | Public intake and minimal status, as above. |
| `POST /webhooks/stripe` | Public, but the Stripe signature is verified first. |
| `POST /api/pair/verify` | Public, but only a valid, unexpired, single-use pairing token succeeds. After 5 failed attempts from one peer (or 60 overall) it answers `429` for a few minutes. |
| `GET /briefs/{id}`, `GET /api/briefs/{id}`, `GET /api/receipt/{id}` | Delivery token (`?token=`), or a genuinely local request. |
| `GET /api/briefs` | Genuinely local requests only. |
| Everything else, including `GET /api/pair/qr` | Dashboard token. |

### OpenClaw pairing

Minting a pairing token is an operator action. Open
`http://127.0.0.1:8787/api/pair/qr?token=$SOLVENT_DASHBOARD_TOKEN` (or send
`/pair qr` in the dashboard chat, or in Telegram once you are paired), scan the
QR code with the app, and the app redeems it through `POST /api/pair/verify`.
Tokens last 10 minutes and work once. `/pair qr` is refused over Telegram when
`SOLVENT_TELEGRAM_DM_POLICY=open`, because anyone could then mint one.

### Running behind a reverse proxy

"Local" means the TCP peer is loopback **and** the request carries no proxy
header (`Forwarded`, `X-Forwarded-*`, `X-Real-IP`, `CF-Connecting-IP`,
`True-Client-IP`, `Via`, ...). A proxy on the same host would otherwise make every
internet request look local, so the local-only routes would be open.

A proxy that does not add any of those headers cannot be detected. If you put
SOLVENT behind one, set

```bash
export SOLVENT_LOCAL_ACCESS=never   # nothing is "local": delivery token or dashboard token required
```

`SOLVENT_LOCAL_ACCESS` accepts `auto` (default, as above), `never`, and `peer`
(the old behaviour: trust the peer address only; do not use it behind a proxy).

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
| `SOLVENT_DASHBOARD_TOKEN` | Bearer-style shared secret for `/`, `/api/status`, `/api/events`, `/api/chat`, `/api/job`, `/api/pair/qr`, the webhook admin routes (`/api/webhooks`, `/api/webhooks/stats`, `/api/webhooks/{event_id}/replay`), and the full detail of `/health` and `/jobs/{id}`; set to a high-entropy value before serving the dashboard |
| `SOLVENT_LOCAL_ACCESS` | How "local" is decided for the local-only routes: `auto` (default), `never` (use behind a reverse proxy) or `peer` (legacy). See *Running behind a reverse proxy* |
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
