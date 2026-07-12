# Telegram channel

The Telegram adapter uses the same gateway, memory, pricing, checkout, and guardrails as the dashboard and CLI.

## Install and configure

```bash
pip install -e ".[telegram,serve,stripe]"
export TELEGRAM_BOT_TOKEN="123456:ABC..."
```

Create the token with [@BotFather](https://t.me/BotFather). Keep it in the environment and never commit it.

The default direct-message policy is pairing:

```bash
export SOLVENT_TELEGRAM_DM_POLICY=pairing
```

Supported values are `pairing`, `allowlist`, and `open`. For allowlist mode, set `SOLVENT_TELEGRAM_ALLOW_FROM` to comma-separated Telegram user IDs.

## Run

Start each long-running process separately:

```bash
solvent serve      # checkout, webhooks, browser dashboard
solvent worker     # paid-job fulfillment
solvent telegram   # Telegram long polling
```

Verify local configuration with:

```bash
solvent doctor
```

## Pair users

With the default policy, an unknown user sends `/start` and receives a code. Approve it from the operator terminal:

```bash
solvent pairing list
solvent pairing approve TG-XXXXXX
```

Approved IDs are stored in the local SOLVENT configuration directory and are not committed.

## Commands

| Command | Action |
|---|---|
| `/help` | Usage summary |
| `/status` | Treasury snapshot |
| `/jobs` | Recent jobs |
| `/quote topic \| 50.00` | Margin preview |
| `/pair qr` | Create a short-lived OpenClaw pairing token |

Free-form conversation can quote and submit research briefs. `submit_brief` still passes through the same margin gate and checkout stages as every other channel.

## Security properties

- Pairing is required by default.
- Messages are rate-limited per user.
- The bot token is read only from the environment.
- Stripe live-mode keys are refused.
- Telegram cannot bypass treasury, pricing, payment, or spend-policy code.
- Outbound text is capped to Telegram's message limit.

## Agent workspace

Chat identity and operating context come from the local workspace:

```bash
solvent workspace setup
solvent workspace list
```

See [WORKSPACE.md](WORKSPACE.md) for the workspace file map.
