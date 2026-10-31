---
title: SOLVENT Agent Demo
emoji: 🪙
colorFrom: yellow
colorTo: green
sdk: static
app_file: index.html
pinned: false
license: mit
short_description: AI agent that declines unprofitable jobs, in your browser
tags:
  - agents
  - ai-agents
  - stripe
  - margin-gate
  - pricing
  - offline-demo
  - pyodide
  - python
---

# 🪙 SOLVENT Agent Demo (static, runs in your browser)

An offline, zero-key demo of [**solvent-agent**](https://github.com/ianalloway/solvent-agent), a Python AI agent that
runs a small research business: it **quotes jobs against its own costs**, **declines unprofitable work**
(with a counter-offer), and books every job into a treasury P&L. The real package runs **inside your browser tab**
via Pyodide, so this is a free static Space with no server.

- **Run the 5-job demo**: the repo's built-in sample jobs, a per-job table (accepted/declined, price, cost, margin),
  the declined job (`J3`) with its counter-offer, the final P&L / treasury snapshot and the event timeline.
- **Try your own job**: a dry run of the margin gate, same logic as `solvent quote` on the CLI.

Stripe checkout and the analyst brief are deterministic stubs, so the numbers are illustrative. Not financial advice.
Tick **Use Nano sim settle rail** to run the same jobs through the simulated Nano rail (`nano_sim_…` invoices, `block_sim_…` proofs; still offline).

## How it works

- `index.html` + `app.js` + `style.css`: plain static page, no build step.
- On load it fetches [Pyodide](https://pyodide.org) (CPython compiled to WebAssembly) from the official jsDelivr CDN,
  then installs the pure-Python `solvent-agent` wheel from PyPI with `micropip` (pinned: `solvent-agent==0.3.0`).
- `space.py` holds the same logic as the Gradio version in [`../hf-space/app.py`](../hf-space/app.py), with the gradio UI
  removed; `app.js` calls it through a tiny JSON bridge and renders the results.
- Network: only Pyodide assets (`cdn.jsdelivr.net`) and the wheel (`pypi.org`, `files.pythonhosted.org`). No keys, no server,
  and nothing you type is uploaded. First visit downloads Pyodide (several MB, cached by the browser afterwards).

**Offline wheel (optional fallback):** if PyPI is unreachable, `app.js` tries `wheels/solvent_agent-0.3.0-py3-none-any.whl` next to `index.html`.
Drop that file from `pip download solvent-agent==0.3.0 --no-deps -d wheels` into a `wheels/` folder to use it.

## Run locally

```bash
cd hf-space-static
python -m http.server 8000   # then open http://localhost:8000
```

(Opening `index.html` straight from disk will not work: browsers block `fetch("space.py")` on `file://`.)

## Deploy this Space (maintainers)

Create a Space with SDK **Static** (free, no hardware) and upload `README.md`, `index.html`, `app.js`, `style.css` and
`space.py` to its root. No secrets are needed; do not add API keys. Bump the version pin in `app.js` when a new release ships.

## Links

GitHub: <https://github.com/ianalloway/solvent-agent> · PyPI: <https://pypi.org/project/solvent-agent/> ·
`pip install solvent-agent` · License: MIT
