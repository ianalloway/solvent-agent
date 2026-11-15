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

## How it works

- `index.html` + `app.js` + `style.css`: plain static page, no build step.
- On load it fetches the pinned Pyodide runtime from jsDelivr and the pinned pure-Python SOLVENT wheel from PyPI. The browser verifies the hashes of both scripts and every downloaded runtime and wheel asset before use.
- `space.py` runs the demo and quote calculations offline; `app.js` renders the results. No keys are needed, and the demo does not upload what you type.
- If you update the runtime or wheel, update the reviewed URLs and integrity hashes together. First visit downloads several MB.

## Run locally

```bash
cd hf-space-static
python -m http.server 8000   # then open http://localhost:8000
```

(Opening `index.html` straight from disk will not work: browsers block `fetch("space.py")` on `file://`.)

## Deploy this Space (maintainers)

Create a Space with SDK **Static** (free, no hardware) and upload `README.md`, `index.html`, `app.js`, `style.css` and
`space.py` to its root. No secrets are needed; do not add API keys. Update the version and integrity pins together when a new release ships.

## Links

GitHub: <https://github.com/ianalloway/solvent-agent> · PyPI: <https://pypi.org/project/solvent-agent/> ·
`pip install solvent-agent` · License: MIT
