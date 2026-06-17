# 🪙 SOLVENT

**A self-funding analyst agent.** It sells research briefs, gets paid through
Stripe, spends its own revenue to provision the compute/data/SaaS it needs, and
refuses any job that doesn't clear a margin. A fully automated company with its
own balance sheet.

Built for the **Hermes Agent Accelerated Business Hackathon** (NVIDIA × Stripe ×
Nous Research).

---

## Quick start

**Requirements:** Python 3.10+ (stdlib only — no install needed for the demo)

### 1. Clone the repo

```bash
git clone https://github.com/ianalloway/solvent-agent.git
cd solvent-agent
```

### 2. Run the demo

```bash
python3 run_demo.py
```

That's it. No API keys, no `pip install`. The demo runs on built-in offline stubs.

You'll see the agent process four jobs in the terminal:
- **Jobs 1, 2, 4** — quoted, paid, fulfilled, vendor costs paid
- **Job 3** — declined (costs more than the customer pays)

At the end you'll get a summary like:

```
Earned:   $223.00
Spent:    $ 13.35
Margin:      94%
Balance:  $310.00  (started with $100 seed)
```

### 3. Open the dashboard

After the demo finishes, open the generated balance sheet in your browser:

```bash
open treasury_dashboard.html        # macOS
# xdg-open treasury_dashboard.html  # Linux
# start treasury_dashboard.html     # Windows
```

This shows revenue, expenses, profit, and every transaction the agent logged.

### 4. Run the safety demo (optional, ~10 seconds)

```bash
python3 demo_guardrails.py
```

Shows four payments being **blocked** by the spend guardrails (unknown vendor,
over cap, reserve breach, negative ROI) before any money moves.

---

## Other commands

| Command | What it does |
|---|---|
| `python3 run_demo.py` | Run the 4-job batch demo (default) |
| `python3 run_demo.py --interactive` | Enter your own research topics and budgets |
| `python3 demo_guardrails.py` | Show the spend-safety policy layer |
| `python3 -m pytest tests/ -q` | Run unit tests (needs `pip install pytest`) |

---

## Make it real (optional)

To use live Nemotron inference and real Stripe test-mode payment links:

```bash
pip install -r requirements.txt
export NVIDIA_API_KEY=nvapi-...       # from build.nvidia.com
export STRIPE_API_KEY=sk_test_...     # Stripe test mode only
python3 run_demo.py
```

- With `NVIDIA_API_KEY` set, briefs are written by **Nemotron**.
- With a Stripe **test** key, each job creates a real Payment Link you can pay
  with test card `4242 4242 4242 4242`.
- The Stripe client **refuses** live keys (`sk_live_...`).

---

## What's in here

| File | What it is |
|---|---|
| `run_demo.py` | the full earn → spend → profit loop |
| `demo_guardrails.py` | the spend-safety demo |
| `treasury_dashboard.html` | balance sheet (generated after each run) |
| `ARCHITECTURE.md` | how it works + sponsor-tech mapping |
| `solvent/` | the agent's source code |
| `tests/` | unit tests for pricing and guardrails |

## How it maps to the sponsors

- **NVIDIA Nemotron** — the analyst's reasoning engine (`solvent/nemotron.py`)
- **NVIDIA NemoClaw** — spend guardrails / policy sandbox (`solvent/guardrails.py`)
- **Stripe Skills** — earn (Payment Links) + spend (vendor payments) (`solvent/stripe_client.py`)
- **Nous / Hermes** — the agent orchestration loop (`solvent/agent.py`)
