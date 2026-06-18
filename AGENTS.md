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

Nemotron may chat and plan; treasury writes and Stripe stay in stages/guardrails.

## Commands

```bash
python -m solvent serve|worker|telegram|doctor|pairing|workspace
python3 run_demo.py          # batch demo / onboarding
```

## Conventions

- Config: `.solvent/config.json` (gitignored); API keys in env only
- Offline Nemotron stub when `NVIDIA_API_KEY` unset
- Test Stripe only (`sk_test_` / `rk_test_`; live keys refused)
