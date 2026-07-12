# Contributing to SOLVENT

## Setup

```bash
git clone https://github.com/ianalloway/solvent-agent.git
cd solvent-agent
pip install -e ".[dev]"
python3 run_demo.py --no-onboard
python3 -m pytest -q
```

Install only the optional features you are changing:

```bash
pip install -e ".[stripe]"
pip install -e ".[serve]"
pip install -e ".[telegram]"
pip install -e ".[rich]"
pip install -e ".[qr]"
```

Dependency declarations live in `pyproject.toml`; do not add parallel requirements files.

## Pull requests

1. Create a focused branch.
2. Keep the standard-library core importable without optional extras.
3. Add or update tests for behavior changes.
4. Run `python3 -m pytest -q`.
5. Open a pull request that explains the user-visible behavior and tradeoffs.

## Project boundaries

- `solvent/agent.py` is a thin public orchestrator; durable workflow logic belongs in `solvent/stages.py`.
- Treasury writes and Stripe actions stay behind stages and guardrails.
- API keys belong in environment variables only.
- Live Stripe keys are never accepted.
- Offline mode must keep working without credentials.
