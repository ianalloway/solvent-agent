# Contributing to SOLVENT

Thanks for your interest in SOLVENT! This is a hackathon project that I'm actively improving — contributions of all kinds are welcome.

## Good First Issues

| Area | What to do |
|---|---|
| **New research topics** | Add more sample jobs in `solvent/jobs.py` |
| **Nemotron prompts** | Improve the brief template in `solvent/service.py` |
| **New guardrail policies** | Add a rule to `solvent/guardrails.py` |
| **Dashboard improvements** | New charts or metrics in `solvent/dashboard.py` |
| **Tests** | Extend the pytest suite in `tests/` |

## Running Locally

```bash
git clone https://github.com/ianalloway/solvent-agent.git
cd solvent-agent
python3 run_demo.py          # no install required for the demo
pip install pytest
python3 -m pytest tests/ -v  # run the test suite
```

## Pull Request Process

1. Fork the repo and create a branch: `git checkout -b feat/your-feature`
2. Make your changes.
3. Ensure `python3 -m pytest tests/ -q` passes.
4. Open a pull request with a clear description of what changed and why.

## Code Style

- Python 3.10+ with type hints where it helps readability.
- No external dependencies in core `solvent/` (keep it importable with stdlib only).
- Dependencies in `requirements.txt` are for optional live integrations only.

## Questions?

Open an [issue](https://github.com/ianalloway/solvent-agent/issues) — happy to discuss ideas!
