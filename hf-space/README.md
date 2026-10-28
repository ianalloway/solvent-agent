---
title: SOLVENT Agent Demo
emoji: 🪙
colorFrom: yellow
colorTo: green
sdk: gradio
sdk_version: 6.30.0
python_version: "3.12"
app_file: app.py
pinned: false
license: mit
short_description: Offline demo: an AI agent that declines unprofitable jobs
tags:
  - agents
  - ai-agents
  - stripe
  - nano
  - margin-gate
  - pricing
  - offline-demo
  - python
---

# 🪙 SOLVENT Agent Demo

An offline, zero-key demo of [**solvent-agent**](https://github.com/ianalloway/solvent-agent), a Python AI agent that
runs a small research business: it **quotes jobs against its own costs**, **declines unprofitable work**
(with a counter-offer), and books every job into a treasury P&L.

- **Run the 5-job demo**: the repo's built-in sample jobs, a per-job table (accepted/declined, price, cost, margin),
  the declined job (`J3`) with its counter-offer highlighted, and the final P&L / treasury snapshot.
- **Nano sim toggle**: optional `SOLVENT_NANO=sim` path — invoices use `nano_sim_…` destinations and
  `block_sim_…` proofs (still offline; no RPC).
- **Try your own job**: topic + budget → the margin-gate dry run (same logic as `solvent quote`).

## Zero keys, offline

This Space installs the real package from PyPI (`solvent-agent>=0.2.2`, bump to `>=0.3.0` after the
0.3.0 publish) and does **not** vendor its code.
Stripe checkout is simulated by default; flip **Use Nano sim settle rail** for `nano_sim_…` /
`block_sim_…` recording in the event timeline. The analyst is a deterministic stub, so it needs
**no API keys and no network access**.
`app.py` also clears any `STRIPE_*` / `NVIDIA_*` / live Nano RPC variables and runs each demo in a
throwaway data directory. Numbers shown are illustrative simulation output, not real revenue.

### Example Nano sim recording (timeline)

```
[J1] Nano invoice issued (sim): $49.00 → nano_sim_…
[J1] Nano payment confirmed (sim block_sim_…): +$49.00 ref=block_sim_…
```

CLI equivalent:

```bash
export SOLVENT_NANO=sim
pip install solvent-agent && solvent
```

## Run locally

```bash
pip install -r requirements.txt gradio
python app.py
```

Or skip the UI: `pip install solvent-agent && solvent` (full demo) or `solvent quote "topic" --budget 49`.

## Deploy this Space (maintainers)

1. Create a Space at <https://huggingface.co/new-space> (SDK: **Gradio**, hardware: CPU basic, public).
2. Upload the files in this folder (`README.md`, `app.py`, `requirements.txt`) to the Space repo root, either in the web UI
   (*Files → Add file → Upload files*) or with the CLI:
   ```bash
   hf auth login   # or: huggingface-cli login
   hf upload <your-username>/solvent-agent-demo . . --repo-type=space
   ```
   (`hf` is the current `huggingface_hub` CLI; the older `huggingface-cli upload` also works.)
3. Wait for the build; the Space should start with no secrets configured. **Do not add any API keys**: none are needed.
4. Keep `sdk_version` in `README.md` in step with a Gradio release you have tested, and bump `solvent-agent` when a new version ships.
5. After **0.3.0** is on PyPI, set `requirements.txt` to `solvent-agent>=0.3.0` so the Space picks up live Nano client APIs used by the toggle path.

## Links

- GitHub: <https://github.com/ianalloway/solvent-agent> · PyPI: <https://pypi.org/project/solvent-agent/> · License: MIT
- Not financial advice. The sample jobs and customers are fictional.
