# SOLVENT — repository context

_Hermes-style project instructions (architecture + conventions). Operator rules live in `.solvent/workspace/AGENTS.md`._

## Architecture

- **Orchestrator:** `solvent/agent.py` → `solvent/stages.py` stage machine
- **Treasury:** `solvent/treasury.py` SQLite ledger
- **Chat surface:** `solvent/gateway.py` → `solvent/chat.py` (Nemotron + tools)
- **Identity:** `.solvent/workspace/SOUL.md` (slot #1), `BRAIN.md`, workspace `AGENTS.md`
- **Channels:** `python -m solvent telegram` (OpenClaw pairing pattern)

## Economic kernel

Margin gate (`pricing.py`) → Stripe checkout → Nemotron fulfill → deliver → guardrailed spend → book.

A decline carries a counter-offer (narrower scope at the same budget, else the
lowest price clearing the floor). `backlog.py` ranks open work — paid jobs
first, then margin per cent of cost — and defers work the treasury cannot fund
instead of letting the quote stage decline it. Spend policy is tunable in
`.solvent/spend_policy.json`.

Realized COGS feed back into the gate (`calibration.py`): quotes are marked up
when costs run hot, never marked down. Refunds are booked with the
`customer-refund` vendor tag and are excluded from spend budgets — they are an
outflow, not operating spend. `simulate.py` runs pricing + guardrails over
synthetic demand without touching the treasury, and `optimize.py` searches the
policy grid under a risk budget.

Inbound work passes `intake.py` (duplicates, bursts, oversized orders,
unreachable customers) before it is quoted. Unpaid checkouts are chased and
then expired by `checkout.py`, swept on every worker pass. Policy files:
`.solvent/{pricing_overrides,spend_policy,checkout_policy,intake_policy}.json`.

`capacity.py` derives the jobs/day ceiling each spend rule implies and names the
binding one; `alerts.py` runs the health sweep (cash, runway, stuck jobs, spend
headroom, vendor exposure, blocks, unpaid pipeline, cost drift) and exits
non-zero on critical; `export.py` closes a period on the ledger and writes the
books as CSV or JSON.

Nemotron may chat and plan; treasury writes and Stripe stay in stages/guardrails.

## Commands

```bash
python -m solvent            # batch demo / onboarding (alias: run_demo.py)
python -m solvent init|status|jobs|logs|config|upgrade
python -m solvent serve|worker|telegram|doctor|pairing|workspace|finance|reconcile
python -m solvent quote "<topic>" --budget 49   # dry-run the margin gate
python -m solvent backlog|guardrails            # ranked queue / spend policy in force
python -m solvent customers|costs|simulate     # customer book / cost model / policy sim
python -m solvent checkouts|intake|optimize    # unpaid links / intake screen / policy search
python -m solvent capacity|alerts|export       # throughput ceiling / health sweep / books
```

## Conventions

- Config: `.solvent/config.json` (gitignored); API keys in env only
- Offline Nemotron stub when `NVIDIA_API_KEY` unset
- Test Stripe only (`sk_test_` / `rk_test_`; live keys refused)
