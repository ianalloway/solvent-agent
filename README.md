# 🪙 SOLVENT

**A self-funding analyst agent.** It sells research briefs, gets paid through
Stripe, spends its own revenue to provision the compute/data/SaaS it needs, and
refuses any job that doesn't clear a margin. A fully automated company with its
own balance sheet.

Built for the **Hermes Agent Accelerated Business Hackathon** (NVIDIA × Stripe ×
Nous Research).

---

## Run it in 30 seconds (no keys needed)

```bash
git clone https://github.com/ianalloway/solvent-agent.git
cd solvent-agent
python3 run_demo.py
```

You'll watch the agent quote four jobs, decline the unprofitable one, collect
payment, write each brief, pay its own vendors (every payment screened by
guardrails), and book its P&L. It finishes by writing **`treasury_dashboard.html`**
— open that in a browser to see the agent's balance sheet.

Then run the safety demo:

```bash
python3 demo_guardrails.py
```

This shows the agent **refusing** four classes of unsafe payment before any money
moves.

## Make it real (optional)

```bash
pip install -r requirements.txt           # installs the stripe SDK
export NVIDIA_API_KEY=nvapi-...           # live NVIDIA Nemotron inference
export STRIPE_API_KEY=sk_test_...         # real Stripe TEST-MODE payment links
python3 run_demo.py
```

- With `NVIDIA_API_KEY` set, the briefs are written by **Nemotron**.
- With a Stripe **test** key set, each job creates a **real Payment Link** you can
  open and pay with a test card (`4242 4242 4242 4242`) — and see in your Stripe
  test dashboard.
- The Stripe client **refuses** a live key (`sk_live_...`). This prototype never
  touches a real account.

## What's in here

| File | What it is |
|---|---|
| `run_demo.py` | the full earn → spend → profit loop |
| `demo_guardrails.py` | the spend-safety demo |
| `treasury_dashboard.html` | the agent's balance sheet (generated on each run) |
| `ARCHITECTURE.md` | how it works + sponsor-tech mapping |
| `solvent/` | the agent's source code |
| `tests/` | unit tests for pricing and guardrails |

## How it maps to the sponsors
- **NVIDIA Nemotron** — the analyst's reasoning engine (`solvent/nemotron.py`)
- **NVIDIA NemoClaw** — spend guardrails / policy sandbox (`solvent/guardrails.py`)
- **Stripe Skills** — earn (Payment Links) + spend (vendor payments) (`solvent/stripe_client.py`)
- **Nous / Hermes** — the agent orchestration loop (`solvent/agent.py`)
