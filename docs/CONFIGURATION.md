# Configuration: making it real

<sub>Moved from the main [README](../README.md).</sub>

## 🔑 Make It Real

To use live Nemotron inference and real Stripe test-mode payment links:

```bash
pip install "solvent-agent[stripe]"

export NVIDIA_API_KEY=nvapi-...        # from build.nvidia.com
export STRIPE_API_KEY=sk_test_...      # Stripe test mode only (live keys refused)

python3 run_demo.py
```

With both keys set:

- Briefs are written by **NVIDIA Nemotron** (Llama-3.1-Nemotron-Ultra).
- Each job creates a real **Stripe Payment Link**. Pay with test card `4242 4242 4242 4242`.
- SOLVENT **polls** the Checkout Session (`cs_...`) until `payment_status == paid` before fulfilling — no instant confirm.
- Optional: set `STRIPE_WEBHOOK_SECRET` and forward `checkout.session.completed`, `checkout.session.async_payment_succeeded`, and `checkout.session.async_payment_failed` events via `StripeClient.process_webhook()`.
- Optional: enable **Stripe Issuing** on your test account to provision capped single-use virtual debit cards for each vendor payment.

### Local or OpenAI-compatible model (Ollama, LM Studio, vLLM)

Briefs can be written by any OpenAI-compatible `/chat/completions` server instead of Nemotron. This is opt-in, uses only the standard library, and is ignored unless you set one of the variables below:

```bash
ollama pull llama3.2
export SOLVENT_LLM_MODEL=llama3.2                       # base URL defaults to Ollama
# export SOLVENT_LLM_BASE_URL=http://localhost:11434/v1  # or any OpenAI-compatible URL
# export SOLVENT_LLM_API_KEY=...                         # optional; sent only as a Bearer header
solvent --no-onboard
```

If the server is down, slow (`SOLVENT_LLM_TIMEOUT`, default 30s), or returns an error or malformed reply, SOLVENT logs a warning and falls back to the deterministic offline brief, so the job still completes. The API key is never logged or written to a brief.

**API keys.** `SOLVENT_LLM_API_KEY` stays on your machine: it is read from the environment and sent only as a Bearer header to the configured `SOLVENT_LLM_BASE_URL`, never anywhere else. Redirects are refused rather than followed, so a 3xx can never forward the key to another host or downgrade it to `http://`; point the base URL at the final endpoint. Give each tool its own key with a spend cap, so one leaked or misbehaving key can only spend so much.

### Environment variables

| Variable | Purpose |
|---|---|
| `SOLVENT_HOME` | Where runtime data and `.solvent` configuration (treasury DB, policies, reports, dashboard, logs) are stored. Defaults to the repo when run from a checkout, else `~/.solvent` |
| `NVIDIA_API_KEY` | Live Nemotron inference (`nvapi-...`) |
| `STRIPE_API_KEY` | Stripe test key (`sk_test_...`) |
| `STRIPE_WEBHOOK_SECRET` | Required to accept `/webhooks/stripe` events (signature is verified before anything is stored) |
| `STRIPE_PAYMENT_POLL_TIMEOUT` | Seconds to wait for payment (default `120`) |
| `STRIPE_PAYMENT_POLL_INTERVAL` | Poll interval in seconds (default `2`) |
| `SOLVENT_FORCE_STRIPE_SIMULATE` | Force offline simulate mode even with a key |
| `SOLVENT_DASHBOARD_TOKEN` | Shared secret required for hosted dashboard/control routes |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from BotFather |
| `SOLVENT_TELEGRAM_DM_POLICY` | `pairing` · `allowlist` · `open` (default `pairing`) |
| `SOLVENT_TELEGRAM_ALLOW_FROM` | Comma-separated Telegram user IDs for allowlist mode |
| `SOLVENT_PORT` | Port for the `serve` API server (default `8787`) |
| `SOLVENT_BASE_URL` | Base URL for hosted brief links and Stripe webhook callbacks |
| `SOLVENT_LLM_BASE_URL` | Opt-in OpenAI-compatible endpoint for briefs (default when enabled: `http://localhost:11434/v1`, Ollama) |
| `SOLVENT_LLM_MODEL` | Model name for that endpoint; setting it alone enables the backend (default `llama3.2`) |
| `SOLVENT_LLM_API_KEY` | Optional Bearer key for the endpoint; never logged |
| `SOLVENT_LLM_TIMEOUT` | Request timeout in seconds for that endpoint (default `30`) |
| `NEMOTRON_MODEL` | Nemotron model override (default: `nvidia/llama-3.1-nemotron-ultra-253b-v1`) |
| `SOLVENT_DELIVERY_SECRET` | HMAC token secret for `/briefs/{job_id}`; at least 32 characters, high entropy |
| `SOLVENT_SKIP_ONBOARD` | Set to `1` to skip the first-run wizard |
| `SOLVENT_ALLOW_POLL` | When set to `1`/`true`/`yes`, actively poll Stripe Checkout Sessions for payment status instead of awaiting webhook confirmation (default: off) |
| `SOLVENT_ASYNC` | Run job fulfillment asynchronously instead of blocking on payment polling (default: off / synchronous) |
| `SOLVENT_LIVE_SEARCH` | Enable live web search integration in the agent chat loop (default: off) |
| `SOLVENT_LOG_JSON` | Emit structured JSON log lines to stderr in addition to the log file (default: off) |
| `SOLVENT_UPDATE_CHECK` | Opt-in: run a background version-update hint on CLI startup when set to `1`/`true`/`yes` |
| `SOLVENT_NO_UPDATE_CHECK` | Set to any value to suppress the background version-update hint |
| `SOLVENT_WORKSPACE` | Override path for the agent workspace directory (SOUL/BRAIN/AGENTS files) |
| `SOLVENT_WORKSPACE_MAX_CHARS` | Max characters loaded per workspace context file (default `8000`) |
| `SOLVENT_WORKSPACE_TOTAL_MAX_CHARS` | Max total characters across all workspace context files (default `40000`) |
| `SMTP_HOST` | SMTP server hostname. When empty (default), brief delivery is **simulated** — research briefs are written to the outbox directory instead of emailed. When set, briefs are emailed to the customer |
| `SMTP_PORT` | SMTP server port (default `587`) |
| `SMTP_USER` | SMTP authentication username |
| `SMTP_PASS` | SMTP authentication password |
| `SMTP_FROM` | "From" address for outgoing brief emails (default: `SMTP_USER`, else `agent@solvent.local`) |

Product/Price objects are cached in `.solvent/stripe_catalog.json` so repeated runs reuse a single **SOLVENT Research Brief** product instead of cluttering your Stripe dashboard.

---

## 💬 Telegram (conversational channel)

Full chat on Telegram with OpenClaw-style pairing and Hermes-style tool/memory patterns. See **[docs/TELEGRAM.md](TELEGRAM.md)**.

```bash
pip install "solvent-agent[telegram]"
export TELEGRAM_BOT_TOKEN=...

python -m solvent serve &    # Stripe webhooks + checkout
python -m solvent worker &   # fulfill jobs
python -m solvent telegram     # long-poll bot

python -m solvent doctor       # diagnostics
python -m solvent pairing list # pending DM codes
```

Users pair via `/start`, commission briefs in natural language, receive checkout links, and get push updates when jobs are paid and delivered.

Personality and operating rules come from the **agent workspace** (`SOUL.md`, `BRAIN.md`, `AGENTS.md`) — see **[docs/WORKSPACE.md](WORKSPACE.md)**.
