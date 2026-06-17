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

```bash
git clone https://github.com/ianalloway/solvent-agent.git
cd solvent-agent
python3 run_demo.py
```

No API keys, no `pip install`. The agent runs on built-in offline stubs.

On **first run**, a terminal setup wizard asks you to choose a model, interaction
mode, and optional Stripe test mode. Preferences are saved to `.solvent/config.json`
(gitignored). See [ONBOARDING.md](ONBOARDING.md) for design notes (NemoClaw + Hermes patterns).

---

## First run / onboarding

The wizard runs automatically when `.solvent/config.json` does not exist:

```bash
python3 run_demo.py          # guided setup, then your chosen mode
python3 run_demo.py --onboard   # re-run setup anytime
python3 run_demo.py --no-onboard   # skip wizard; offline batch defaults
SOLVENT_SKIP_ONBOARD=1 python3 run_demo.py   # same as --no-onboard
```

**Step 1 — Model**

| Choice | Needs |
|--------|-------|
| Offline stub (default) | Nothing |
| NVIDIA Nemotron | `NVIDIA_API_KEY` in environment |
| Custom endpoint | Documented for future; uses stub today |

**Step 2 — Interaction**

| Choice | What happens |
|--------|----------------|
| Batch demo | 4 canned jobs (~30s, best for judges) |
| Interactive REPL | Type topics and budgets at a prompt |
| Programmatic | Prints import examples; use `Solvent` in Python |

**Step 3 — Stripe test mode** (optional)

Enable for real Stripe **test** Payment Links when `STRIPE_API_KEY=sk_test_...` is set.
Otherwise payments stay simulated.

Equivalent entry points:

```bash
python3 run_demo.py
python3 -m solvent
```

---

## Starting the agent

**There is no separate server or daemon.** The agent is the `Solvent` class in
`solvent/agent.py` — a job-processing orchestrator. You start it by creating a
`Solvent` instance and feeding it work. `run_demo.py` is the CLI wrapper around
that class.

SOLVENT is **batch-oriented**, not a long-running web service: each run processes
one or more jobs, updates the treasury, writes a dashboard, and exits.

### What happens on startup

When you start the agent (via `run_demo.py` or Python), `Solvent.__init__`:

1. **Resets the treasury** — clears the SQLite ledger in `data/solvent.db`
2. **Seeds capital** — books $100.00 of starting cash (`seed_cents=10_000`)
3. **Wires dependencies** — spend guardrails, Stripe client (simulated unless
   `STRIPE_API_KEY` is set), and pricing policy
4. **Opens the event log** — every quote, payment, fulfillment, and spend is
   recorded; `treasury_dashboard.html` updates live as jobs run

For each inbound job, the agent runs: **quote → earn → fulfil → spend → book P&L**.
Unprofitable jobs are declined before any money moves.

### Mode 1 — Batch demo (default)

Runs four pre-loaded sample jobs and exits. Best for judges: shows margin gating,
Stripe earn/spend, Nemotron fulfillment, and guardrails in ~30 seconds.

```bash
python3 run_demo.py
```

- **Jobs J1, J2, J4** — quoted, paid, fulfilled, vendor costs paid
- **Job J3** — declined (customer budget below cost)

Session summary example:

```
Revenue        $223.00
Operating spend $ 13.35
Net profit     $209.65  (94% margin)
Cash balance   $310.00  (seed was $100.00)
```

### Mode 2 — Interactive agent (your own jobs)

Same agent, but you type research topics and client budgets at a prompt. The
session keeps running until you quit — this is how you **start the agent for
custom work**, not just watch the canned demo.

```bash
python3 run_demo.py --interactive
# or
python3 run_demo.py -i
```

You'll be prompted for a topic and budget per job. Type `y` to submit another
request, or anything else to finish and print the balance sheet.

### Mode 3 — Programmatic (import in Python)

Use the agent as a library — no CLI required:

```python
from solvent.agent import Solvent
from solvent.jobs import SAMPLE_JOBS

agent = Solvent(seed_cents=10_000)          # reset treasury, seed $100
agent.handle_job(SAMPLE_JOBS[0])            # process one job
snap = agent.run(SAMPLE_JOBS[1:])           # process a list; returns snapshot

print(snap["balance_cents"], snap["margin_pct"])
```

Pass `fresh=False` to keep an existing treasury across runs, and `on_event=fn`
to hook into the same event stream `run_demo.py` prints to the terminal.

### Open the dashboard

After any run, open the generated balance sheet:

```bash
open treasury_dashboard.html        # macOS
# xdg-open treasury_dashboard.html  # Linux
# start treasury_dashboard.html     # Windows
```

### Guardrails demo (not the agent)

`demo_guardrails.py` is a **standalone policy demo** — it does not start the
agent or process jobs. It shows five spend attempts and which ones guardrails
block:

```bash
python3 demo_guardrails.py
```

---

## Command reference

| Command | What it does |
|---|---|
| `python3 run_demo.py` | **Start the agent** — onboarding on first run, then saved mode |
| `python3 run_demo.py --onboard` | Re-run the setup wizard |
| `python3 run_demo.py --no-onboard` | Skip wizard; use defaults if no config |
| `python3 run_demo.py --interactive` | **Start the agent** — interactive mode (overrides config) |
| `python3 -m solvent` | Same as `run_demo.py` |
| `python3 demo_guardrails.py` | Spend-policy demo only (no agent loop) |
| `python3 -m pytest tests/ -q` | Unit tests (needs `pip install pytest`) |

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
| `run_demo.py` | CLI entry + onboarding wizard |
| `ONBOARDING.md` | first-run design notes (NemoClaw / Hermes patterns) |
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
