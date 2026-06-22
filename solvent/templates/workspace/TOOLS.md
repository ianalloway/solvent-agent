# TOOLS — local conventions

Guidance only — availability is enforced by the runtime registry.

## Business tools (chat / Telegram)

| Tool | Use when |
|------|----------|
| `treasury_status` | Balance, revenue, margin questions |
| `list_jobs` | Recent work overview |
| `job_status` | One job + metrics |
| `quote_brief` | Margin preview before payment |
| `submit_brief` | After confirmation — creates checkout |

## Research tools (fulfillment)

| Tool | Use when |
|------|----------|
| `web_search` | External evidence (offline stub without live search) |
| `market_data` | Symbol snapshot |
| `summarize` | Condense notes |

## Hermes bridge (large catalogs)

When you see `tool_search`, `tool_describe`, `tool_call` — use them to discover and invoke other tools.

## CLI operators run alongside chat

```bash
python -m solvent serve      # Stripe webhooks
python -m solvent worker     # fulfill jobs
python -m solvent telegram   # this bot
python -m solvent doctor     # diagnostics
python -m solvent pairing approve TG-XXXXXX
```

## Environment (never paste secrets into chat)

- `NVIDIA_API_KEY` — Nemotron live inference
- `STRIPE_API_KEY` — test checkout (`sk_test_` / `rk_test_` only)
- `TELEGRAM_BOT_TOKEN` — bot transport
- `SOLVENT_BASE_URL` — hosted brief URLs
