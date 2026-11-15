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
# Prefer the header for API clients. Query tokens work for browser GETs but
# can leak via history / Referer; the server sends Referrer-Policy: no-referrer.
open "http://127.0.0.1:8787/?token=$SOLVENT_DASHBOARD_TOKEN"
# curl -H "X-Solvent-Dashboard-Token: $SOLVENT_DASHBOARD_TOKEN" http://127.0.0.1:8787/api/status

# Terminal 2 — async job processor
python3 -m solvent worker
```

### Safe restarts and explicit resets

`solvent serve` and `solvent worker` preserve the existing treasury by default.
Starting either command, including repeated server/worker restarts, does not
clear jobs, ledger entries, payment history, reservations or chat/pairing records.
Both processes must use the same `SOLVENT_HOME` to share a queue. Existing service
commands with `--keep-balance` still work; the flag is now a compatibility no-op.
No schema or data migration is needed for this startup change.

A new, empty home starts with zero recorded capital. `--seed` only applies with
`--reset-state`; ordinary startup never books extra capital. Record operating
capital explicitly through `Treasury.seed(cents)`, once per actual contribution.
For example, in the same `SOLVENT_HOME`, the following books $200 without clearing
existing history. This is an accounting entry, not a money transfer; repeating
it would book another $200.

```bash
python3 -c 'from solvent.treasury import Treasury; Treasury().seed(20_000)'
```

**Destructive reset, for disposable test data only:** stop every server and
worker sharing the home, verify the `SOLVENT_HOME` path, and back up the complete
home before resetting. The reset clears the treasury ledger, jobs, stages,
metrics, checkout/invoice history, reservations, events and chat/pairing records,
then seeds the requested amount (default $100). It does not reverse provider
payments, refunds or outstanding liabilities, and it does not remove generated
reports, provider cache files or the separate webhook log. Do not reset an
installation with unresolved payments or customer obligations.

```bash
# Deliberately erase this disposable test treasury, seed $200 and exit:
SOLVENT_HOME=/path/to/disposable-test-home python3 -m solvent worker --once --reset-state --seed 200
```

The server also accepts `--reset-state`, but it must never be included in a
service manager's normal restart command. `--reset-state` and `--keep-balance`
are mutually exclusive. Demo/programmatic reset behavior is unchanged: use a
separate disposable home for `run_demo.py`, and pass `fresh=False` when constructing
`Solvent` against an existing treasury.

### Interactive dashboard + voice

`python3 -m solvent serve` serves a live dashboard at `/` protected by `SOLVENT_DASHBOARD_TOKEN`. Prefer the `X-Solvent-Dashboard-Token` header for API clients; browsers may use `?token=...` (EventSource cannot set custom headers). Responses send `Referrer-Policy: no-referrer` so query tokens are less likely to leak to third parties. The protected dashboard includes:

- **SSE** (`GET /api/events`) — treasury metrics, job cards, and console log update in real time
- **Chat** (`POST /api/chat`) — talk to the agent; supports `/status`, `/jobs`, `/quote topic | 50`
- **Voice** — mic button uses browser Web Speech API (Chrome/Edge); toggle 🔊 for spoken replies
- **Jobs** (`POST /api/job`) — same payload as `POST /jobs`

## Payments

Stripe test-mode Checkout / Payment Links is the supported payment path. Offline
demos use simulated Stripe payments. Live Stripe keys remain refused.

The former Nano payment and custody integration has been removed. Before upgrading
an installation with historical Nano jobs, follow [the retirement and migration
guide](NANO_SETTLE.md). Retain its records and reconcile outstanding obligations;
there are no automated Nano payouts or refunds.

## Stripe setup (test mode)

1. Set `STRIPE_API_KEY=sk_test_...` or restricted `rk_test_...`
2. Create webhook endpoint: `POST {SOLVENT_BASE_URL}/webhooks/stripe`
3. Subscribe to `checkout.session.completed`, `checkout.session.async_payment_succeeded`, and `checkout.session.async_payment_failed`
4. Set `STRIPE_WEBHOOK_SECRET=whsec_...` (required: with no secret `/webhooks/stripe` rejects every request, and requests with a bad signature are rejected without being stored)
5. Submit jobs via `POST /jobs` — response includes `checkout_url` (also as `url`)

Webhook bodies are limited to 1 MiB (1,048,576 bytes), enforced while streaming
before signature verification. Larger requests receive `413`; invalid or
conflicting `Content-Length` headers receive `400` before the body is read.
Rejected requests are not stored in the webhook log.

Unpaid completed sessions remain `processing` until a delayed-payment success
or failure arrives. Processing sessions are not chased or expired. A failed
delayed payment stops fulfillment. Local expiry of a real Stripe session
requires an explicit provider confirmation; an API error, missing credentials,
or an already-completed session keeps the job open for retry and late webhooks.

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
| `GET /.well-known/solvent.json` | Public: server name, version, capabilities, endpoint map, and whether a gateway password is required. |
| `POST /jobs`, `GET /jobs/{id}` | Public intake and minimal status, as above. |
| `POST /webhooks/stripe` | Public, but the Stripe signature is verified first. |
| `POST /api/pair/verify` | Gateway password (if set), then a valid single-use pairing token. After 5 failed attempts from one peer (or 60 overall) it answers `429` for a few minutes. |
| `GET /briefs/{id}`, `GET /api/briefs/{id}`, `GET /api/receipt/{id}` | Delivery token (`?token=`), or a genuinely local request. |
| `GET /api/briefs` | Genuinely local requests only. |
| Everything else, including `GET /api/pair/qr`, `GET /api/settings` | Dashboard token. |

### Gateway password (OpenClaw "Gateway Password" field)

Set `SOLVENT_GATEWAY_PASSWORD` to protect the OpenClaw-facing endpoints from
unauthenticated access. The app sends it as `Authorization: Bearer <password>`
or in the `X-Solvent-Gateway-Password` header. Without it set, those endpoints
behave as before. The discovery document (`/.well-known/solvent.json`) advertises
whether a password is required so the app can prompt before connecting.

```bash
export SOLVENT_GATEWAY_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
```

### Settings overview

`GET /api/settings` (dashboard token required) returns the live server
configuration as JSON: host, port, TLS, model, rate limits, Telegram enabled,
Stripe test mode, and whether a gateway password is set. Useful for the
OpenClaw app settings screen and for operator audit.

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

### Refund uncertainty and recovery

A refund timeout is **not** proof that Stripe rejected the refund. SOLVENT
persists the intent and reserves the full amount before contacting Stripe.
An exception, interrupted worker, or non-final/mismatched response leaves the
job `refund_pending`: retry, worker resume, and delivery are blocked. The
reservation reduces spendable cash without claiming a successful refund.
Finance and dashboard reports flag the unresolved amount alongside the booked
ledger figures. Older `refund_failed` events without a booked refund are
quarantined the same way when the treasury opens.

Verified payments arriving after a job is cancelled, expired, or failed are
booked together with a full refund reservation in one transaction. The terminal
job status is preserved and fulfillment remains blocked. These late-payment
holds do **not** submit a refund automatically: inspect the payment in the
Stripe test dashboard, arrange the refund if none exists, then reconcile its
exact refund ID using the command below. Redelivery cannot book or reserve the
same payment twice.

Before resuming workers on an older treasury, audit previously interrupted
paid jobs against Stripe. A pre-upgrade crash after provider acceptance but
before logging `refund_failed` leaves no local evidence to migrate. Use a
lookback covering that history; the new durable intent only protects requests
started after this upgrade.

`solvent reconcile` is read-only by default. It reports pending local intents
even without Stripe access, and in full mode checks refund IDs, amounts and
statuses as well as PaymentIntents. A succeeded PaymentIntent alone does not
prove that its proceeds are still held. Use an appropriate `--since` window
when auditing historical refunds.

After locating the existing refund in the Stripe **test** dashboard, resolve
its local record with:

```bash
python3 -m solvent reconcile --resolve-refund JOB_ID re_EXISTING_REFUND_ID
```

This retrieves the refund and requires a succeeded, full USD refund for the
same PaymentIntent before atomically booking it and releasing the reservation.
It never sends or retries a refund. Failed, partial, pending, missing or
mismatched evidence leaves the job held for operator review. Do not clear a
reservation or start a replacement refund just because a response timed out:
provider idempotency keys may eventually expire. No automatic recovery is
attempted when the provider's outcome cannot be established.

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
