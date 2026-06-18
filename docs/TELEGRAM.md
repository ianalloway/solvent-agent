# Telegram + SOLVENT

Talk to your self-funding research agent on Telegram. Pairing, conversational commissioning, and job lifecycle notifications are implemented with OpenClaw-style channel security and Hermes-style tool/memory patterns — all in Python, no external gateway runtime.

## Prerequisites

1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the token.
2. Install Telegram dependencies:

```bash
pip install -r requirements-telegram.txt
```

3. Export the token (never commit it):

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."
```

4. Optional: set DM policy (default `pairing`):

```bash
export SOLVENT_TELEGRAM_DM_POLICY=pairing   # pairing | allowlist | open
```

## Run the stack

In separate terminals:

```bash
python -m solvent serve      # Stripe webhooks + checkout
python -m solvent worker     # fulfill paid jobs
python -m solvent telegram     # long-poll bot
```

Verify configuration:

```bash
python -m solvent doctor
```

## Pairing (OpenClaw pattern)

Unknown users messaging the bot receive a code:

1. User sends `/start` → bot replies with `TG-XXXXXX`
2. Operator approves:

```bash
python -m solvent pairing list
python -m solvent pairing approve TG-XXXXXX
```

Approved user IDs are stored in `.solvent/telegram_allowlist.json` (gitignored).

## Example conversation

```
You: What's my treasury balance?
Bot: [uses treasury_status tool] Balance $100.00, margin 94%...

You: I want a research brief on EV battery supply chains, budget $50, email me@co.com
Bot: Margin looks good. Confirm to proceed?
You: Yes, submit it
Bot: Checkout: https://checkout.stripe.com/...

[payment completes via worker]
Bot: Payment received for job Tabc12345. Fulfillment starting.
Bot: Your brief for Tabc12345 is ready: http://127.0.0.1:8787/brief/...
```

## Slash commands

| Command | Action |
|---------|--------|
| `/help` | Usage summary |
| `/status` | Treasury snapshot |
| `/jobs` | Recent jobs |
| `/quote topic \| 50.00` | Margin preview |

Free-form chat also works — Nemotron uses business tools (`quote_brief`, `submit_brief`) behind guardrails.

## Agent workspace (brain + soul)

Chat loads `.solvent/workspace/` each turn:

- **SOUL.md** — identity and tone (Hermes slot #1)
- **BRAIN.md** — active pipeline / what needs attention now
- **AGENTS.md** — operating rules

```bash
python -m solvent workspace setup
python -m solvent workspace list
```

See **[WORKSPACE.md](WORKSPACE.md)** for the full file map.

## Security

- Bot token only in environment variables
- Default DM policy requires pairing before chat
- Rate limit: 30 messages/user/hour
- `submit_brief` runs the same margin gate + checkout stages as the CLI agent
- Live Stripe keys (`sk_live_*`) are refused

## Config file

`.solvent/config.json` supports:

```json
{
  "telegram_enabled": true,
  "telegram_dm_policy": "pairing",
  "telegram_allow_from": ["123456789"]
}
```

Re-run onboarding to set Telegram options:

```bash
python3 run_demo.py --onboard
```
