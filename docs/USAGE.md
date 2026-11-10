# Running modes and operations

<sub>Moved from the main [README](../README.md).</sub>

## 🎮 Running Modes

### Batch demo (default — best for judges)

```bash
python3 run_demo.py
```

5 pre-loaded jobs (4 accepted, 1 declined). ~30 seconds. Shows margin gating, Stripe earn/spend, Nemotron fulfillment, and guardrails in action.

### Interactive — your own jobs

```bash
python3 run_demo.py --interactive
```

Type a research topic and client budget at the prompt. The agent quotes, pays, fulfils, and books P&L for each one in real time. Keep going until you quit.

### Add funds mid-session

```bash
python3 run_demo.py --seed 500        # start with $500 instead of $100
python3 run_demo.py --keep-balance    # resume existing treasury balance
```

In interactive mode, type `/fund 200` at the prompt to deposit $200 into the live treasury without restarting.

### Programmatic

```python
from solvent.agent import Solvent
from solvent.jobs import SAMPLE_JOBS

agent = Solvent(seed_cents=10_000)  # reset treasury, seed $100
agent.handle_job(SAMPLE_JOBS[0])  # process one job
snap = agent.run(SAMPLE_JOBS[1:])  # process a list; returns snapshot

print(snap["balance_cents"], snap["margin_pct"])
```

### Production mode (webhooks + async worker)

```bash
pip install "solvent-agent[serve]"
export SOLVENT_DASHBOARD_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')

python3 -m solvent serve --port 8787   # webhooks + job API + hosted briefs
python3 -m solvent worker              # resume incomplete jobs, process queue

# Interactive voice dashboard (chat + live SSE updates):
open "http://127.0.0.1:8787/?token=$SOLVENT_DASHBOARD_TOKEN"
```

The server and worker preserve all existing treasury state by default. Existing
`--keep-balance` flags remain supported. A new home starts with zero recorded
capital; `--seed` is used only with the explicit, destructive `--reset-state`
option. See [safe restarts and resets](PRODUCTION.md#safe-restarts-and-explicit-resets)
before resetting test data. Keep demos in a separate `SOLVENT_HOME` from service
instances because demo reset behavior is unchanged.

The hosted dashboard at `/` includes a **chat panel** (type or use the mic with Web Speech API) and **live treasury updates** via Server-Sent Events (`/api/events`). Dashboard/control routes require `SOLVENT_DASHBOARD_TOKEN` via `?token=...` or the `X-Solvent-Dashboard-Token` header before they expose status data or route chat through the Nemotron agent loop.

See [docs/PRODUCTION.md](PRODUCTION.md) for Stripe webhook setup, SMTP delivery, and reconciliation.

### Operations

```bash
python3 -m solvent quote "AI inference chips, 2026" --budget 49   # dry-run the margin gate
python3 -m solvent backlog                # rank open jobs by return on capital
python3 -m solvent guardrails             # spend policy in force + vendor exposure
python3 -m solvent customers              # lifetime value, repeat rate, margin by customer
python3 -m solvent costs                  # estimated vs realized COGS + calibration
python3 -m solvent simulate               # run the policy over synthetic demand
python3 -m solvent optimize               # search margin floor x min order for the best policy
python3 -m solvent checkouts              # unpaid links: age, reminders, expiry
python3 -m solvent intake                 # the screen inbound jobs pass before pricing
python3 -m solvent capacity               # jobs/day ceiling and which rule binds it
python3 -m solvent alerts                 # one health sweep; non-zero exit on critical
python3 -m solvent export --since 30d     # ledger/jobs/metrics/customers + period close
python3 -m solvent products               # the price list, checked against current cost
python3 -m solvent quality                # scores the quality gate gave shipped briefs
python3 -m solvent review                 # approve/reject the jobs intake held for a human
python3 -m solvent funnel --since 7d      # win rate: submitted → quoted → paid → delivered
python3 -m solvent turnaround             # measured paid-to-delivered time, and the promise
python3 -m solvent reconcile --since 7d   # Stripe ↔ ledger drift check
python3 -m solvent finance                # income statement, unit economics, runway
python3 -m solvent finance --json         # machine-readable report
python3 -m solvent finance --reserve 50   # runway to a $50 cash-reserve floor
python3 -m solvent finance --period week  # net P&L trend by day | week | month
python3 -m solvent finance --horizon 60   # forecast the balance 60 days out
```

`finance` (alias `report`) turns the treasury ledger into the numbers a
business steers by: revenue/cost/net-margin, average profit per job, a cash
**runway** — days of burn remaining, or `cash-flow positive` once the agent
funds itself — a **net-P&L trend** bucketed by day/week/month, and a
**balance forecast** (central projection with a best/worst band whose width
grows with daily volatility). The income statement, runway, trend, and
forecast also render as a **Financial Statement** panel in the HTML dashboard.
