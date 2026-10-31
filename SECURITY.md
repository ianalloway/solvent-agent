# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.3.x   | ✅ security fixes |
| 0.2.x   | ✅ security fixes (until 0.4) |
| < 0.2   | ❌ |

Fixes ship as patch releases on [PyPI](https://pypi.org/project/solvent-agent/). Please upgrade to the latest supported release before reporting.

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Report privately through GitHub: **[Security → Report a vulnerability](https://github.com/ianalloway/solvent-agent/security/advisories/new)** (a private security advisory). Include the version, how you ran it (CLI, server, behind a proxy), repro steps, and the impact you see.

You should hear back within a few days. Once a fix is released, the advisory is published with credit unless you'd rather stay anonymous.

## Scope notes

- SOLVENT refuses live Stripe keys (`sk_live_…`); only test mode is supported. A way around that is in scope.
- Things of particular interest: webhook verification, dashboard/admin token checks, delivery-token (HMAC) links, the spend-policy guardrails, and anything that lets untrusted job text reach HTML, CSV or the model unescaped.

## Running behind a reverse proxy

By default (`SOLVENT_LOCAL_ACCESS=auto`) some routes trust requests that look local. Behind a reverse proxy every request can look local, so set:

```bash
export SOLVENT_LOCAL_ACCESS=never
```

That way the delivery or dashboard token is always required. See [docs/PRODUCTION.md](docs/PRODUCTION.md) for the rest of the deployment checklist.

## Nano settle rail

The optional Nano (XNO) earn rail is **verify-only** (Option A custody):

- No hot wallet / seed is loaded or generated in-process.
- **Simulate** (`SOLVENT_NANO=sim`) accepts `block_sim_…` proofs with no RPC and no network.
- **Live** (`SOLVENT_NANO=live`) requires `SOLVENT_NANO_RPC`, `SOLVENT_NANO_ADDRESS`, and
  `SOLVENT_NANO_RATE_USD`. Partial config fails closed (`misconfigured`); doctor refuses
  to report OK. Live never accepts `block_sim_…` proofs; sim never calls RPC.
- Mixing sim and live env vars is rejected.
- A public block hash is a payment receipt, **not** privacy: amounts and
  addresses are visible on the ledger. Do not treat Nano settlement as
  confidential customer data handling.

### Nano custody (M3, off by default)

`SOLVENT_NANO_CUSTODY` is **off** unless set. When enabled:

- **sim** never reads a key, never touches the network, and only sweeps to
  `nano_sim_cold_…` addresses. Real addresses are refused.
- **live** names its key by **reference only** (`env:VAR` or
  `keyring:service/user`); anything that looks like an inline seed or a file
  path is refused. Resolved secrets are wrapped in a redacting handle and are
  never logged, persisted, or returned by `status` / `doctor`.
- Sweeps go **only** to the configured cold address, under a min amount, a
  rolling-24h cap, and an explicit approval (`solvent custody sweep --yes`).
- SOLVENT ships **no live signer**. Live sweeps refuse unless an external
  signer is injected in code. Live custody has **not** been validated with real
  funds; treat it as experimental.
