# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.2.x   | ✅ security fixes |
| < 0.2   | ❌ |

Fixes ship as 0.2.x patch releases on [PyPI](https://pypi.org/project/solvent-agent/). Please upgrade to the latest 0.2.x before reporting.

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
