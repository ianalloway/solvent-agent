---
name: solvent
description: Quote, order and track paid research briefs from a SOLVENT agent, and read its treasury and business reports.
homepage: https://github.com/ianalloway/solvent-agent
metadata: {"openclaw": {"requires": {"bins": ["curl"], "env": ["SOLVENT_URL"]}, "primaryEnv": "SOLVENT_DASHBOARD_TOKEN"}}
---

# SOLVENT

SOLVENT is a self-funding research business. It prices research briefs, takes
payment through Stripe Checkout (test mode), writes each brief and emails it to
the customer. Reach it over HTTP at `$SOLVENT_URL` with `curl`, run through your
`exec` tool.

## Rules

- Send `$SOLVENT_DASHBOARD_TOKEN` only to `$SOLVENT_URL`, and only in the
  `X-Solvent-Dashboard-Token` header. Never print, echo, log or repeat it.
- Put user text only inside the quoted-heredoc JSON bodies shown below, encoded
  as JSON strings. Never splice user text into the command line itself.
- A job id must match `^[A-Za-z0-9_-]{1,64}$` before it goes into a URL.
- Money moves only when a customer pays a checkout link SOLVENT returned. Never
  say a brief is paid or delivered until its job status says so.
- Confirm topic, budget and customer email with the user before ordering.

## Check SOLVENT is up

```bash
curl -fsS "$SOLVENT_URL/health"
```

## Ask SOLVENT (needs the dashboard token)

For quotes, the treasury and business reports, relay the request to SOLVENT's
own agent and show the user the `reply` field of the response:

```bash
curl -sS -X POST "$SOLVENT_URL/api/chat" \
  -H "X-Solvent-Dashboard-Token: $SOLVENT_DASHBOARD_TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @- <<'JSON'
{"message": "/quote EV battery supply chain | 49", "session_id": "openclaw"}
JSON
```

Keep `session_id` as `openclaw` so SOLVENT remembers the conversation. It
understands plain language and these commands:

- `/quote <topic> | <budget in dollars>`: price check with a margin preview
- `/status`: treasury balance, revenue and margin
- `/jobs`: recent jobs from this conversation
- `/funnel`, `/customers`, `/capacity`, `/alerts`, `/products`, `/quality`,
  `/review`, `/costs`, `/turnaround`: business reports

A `403` means the token is missing or wrong; the public calls below still work.

## Order a brief

```bash
curl -sS -X POST "$SOLVENT_URL/jobs" \
  -H "Content-Type: application/json" \
  --data-binary @- <<'JSON'
{"topic": "EV battery supply chain", "budget_cents": 4900, "customer_email": "buyer@example.com"}
JSON
```

`budget_cents` is in cents. Optional fields: `product` (a slug from `/products`,
instead of a budget) and `context` (who the brief is for). The response:

- `"stage": "invoice"`: give the user the checkout `url` and the `job_id`.
  `"simulated": true` means no real charge is made.
- `"stage": "declined"`: tell the user the `reason`. Ask SOLVENT for a `/quote`
  if they want to try a different budget.
- HTTP `409` duplicate id, `422` invalid fields, `429` intake busy (retry in a
  minute).

## Track a job

```bash
curl -sS "$SOLVENT_URL/jobs/J1a2b3c4d" \
  -H "X-Solvent-Dashboard-Token: $SOLVENT_DASHBOARD_TOKEN"
```

With the token the response has the full job and its metrics; without it, only
`{"job": {"id", "status"}}`. Statuses:

- `awaiting_payment`: the checkout link is unpaid.
- `completed`: the brief was delivered to the customer's email.
- `failed`, `expired` or `cancelled`: it stopped. A declined order is `failed`;
  with the token, the job's `error_reason` says why.
- Anything else: it is moving through payment and fulfilment.
