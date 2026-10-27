# Nano (XNO) settle leg — design note (issue #83)

Status: **simulate implementation landed** (verify-only, Option A custody).
Enable with `SOLVENT_NANO=sim`. Live RPC / hot wallet remain non-goals for v1.
This document maps
[issue #83](https://github.com/ianalloway/solvent-agent/issues/83) onto the
shapes SOLVENT already uses; see `solvent/nano_client.py` for the sim client.

## Why it fits

SOLVENT's loop is: **quote (profit-gated) → get paid → fulfil → spend under
guardrails → book**. Today the earn side is Stripe test-mode Checkout /
Payment Links (`solvent/stripe_client.py`) with webhook verification before
fulfilment (`POST /webhooks/stripe` in `solvent/server.py`).

Issue #83 proposes the same trust shape with a public Nano block as the
receipt: create an invoice from the existing quote, verify a send block
against the ledger, then fulfil. Unprofitable or declined work never touches
either rail.

## Non-goals (v1)

- Replacing Stripe, Issuing spend, or reconcile-against-Stripe.
- Live main-net custody in the default zero-key demo.
- x402 HTTP challenge/response (may come later; see paypercall reference).
- Auto-converting treasury balances between USD cents and XNO.

## Proposed surface (additive)

Mirror the Stripe client so stages / server / checkout chase stay rail-agnostic
at the call sites.

| Stripe today | Nano (proposed) | Notes |
|---|---|---|
| `create_checkout_session(job_id, amount_cents, …)` | `create_nano_invoice(job_id, amount_cents, …)` | Returns destination `nano_…` + exact **raw** amount derived from the profit-gated quote. |
| `confirm_payment` / webhook `checkout.session.completed` | `verify_nano_block(invoice, block_hash)` | RPC `block_info`: `subtype=send`, `amount >= invoice.raw`, `destination == SOLVENT address`. |
| `POST /webhooks/stripe` | `POST /webhooks/nano` **or** worker poll | Prefer poll/verify-on-submit first (no shared webhook secret from a public ledger). Optional later: push notify from a trusted watcher. |
| `expire_checkout_session` | mark invoice expired in treasury | Checkout chase (`checkout.py`) already expires unpaid jobs; extend to nano invoice rows. |
| `reconcile.py` vs PaymentIntents | optional: list account history vs booked refs | Keep Stripe reconcile unchanged; add a separate nano drift report. |

### Invoice record (treasury)

Suggested fields on a checkout-like row (names indicative):

- `rail`: `"stripe"` \| `"nano"`
- `job_id`, `amount_cents` (quote still USD-denominated for pricing/P&L)
- `nano_destination`, `amount_raw` (string integer; Nano raw is large)
- `xno_rate_usd` + `rate_ts` (how cents mapped to raw at quote time)
- `block_hash` (once verified), `status`: open \| paid \| expired

Revenue ledger entries should grow a generic `payment_ref` (Stripe `pi_…` or
Nano block hash) rather than overloading `stripe_ref` forever — a thin alias
keeps old rows readable.

### Quote → raw amount

Pricing stays in integer USD cents. Conversion happens only inside the Nano
invoice helper:

1. Read `amount_cents` from the already-accepted quote (margin gate has passed).
2. Apply an explicit rate source (config or oracle); refuse invoice creation if
   rate missing/stale.
3. Compute `amount_raw` with integer/Decimal math (no binary float); document
   the rounding mode next to `solvent.money.dollars_to_cents`.
4. Persist rate + raw on the invoice so verify can re-check without re-quoting.

### Verify before fulfil (hard rule)

Same invariant as Stripe:

1. Margin gate already accepted the job.
2. Payment proof verified (`verify_nano_block` or Stripe webhook/`confirm_payment`).
3. Only then may the worker leave `awaiting_payment` and fulfil.

Reject: wrong destination, amount below invoice raw, non-send subtype,
replayed block hash already booked, expired invoice.

### Simulate mode (recommended first code PR)

Match Stripe's offline demo:

- No RPC, no seed, no network: `create_nano_invoice` returns `nano_sim_…` and a
  deterministic raw amount; `verify_nano_block` accepts a `block_sim_…` token
  instantly when `SOLVENT_NANO=sim` / no live endpoint configured.
- Live RPC behind an opt-in env (e.g. `SOLVENT_NANO_RPC`) and a dedicated
  receive address env — never a live Stripe-style key confusion.
- Unit tests: amount math, reject underpay / wrong destination / replay;
  stages still decline unprofitable work without opening an invoice.

## Key custody (open decision)

Issue #83 points at ~100 lines of key derivation and a signed-out onramp that
mints a fresh keypair. For SOLVENT as a *business* agent:

| Option | Pros | Cons |
|---|---|---|
| **A. Operator-provided receive address only** (watch / verify, no hot seed in-process) | Smallest blast radius; matches “verify public block” | Agent cannot auto-sweep or self-custody spend without a later wallet module |
| **B. Hot seed in env / locked file** | True self-custody of earnings | Key management, backup, malware surface — needs SECURITY.md treatment |
| **C. External signer (OS keychain / HSM / separate process)** | Better ops hygiene | More moving parts for v1 |

**Recommendation for v1:** Option A (verify-only) + simulate mode. Custody and
outbound Nano spend are a separate design.

## Config / doctor

- Document env vars in `docs/PRODUCTION.md` only after acceptance.
- `solvent doctor` should report: rail enabled?, RPC reachable?, receive
  address configured?, rate source fresh?, simulate vs live.
- Dashboard checkout list should show rail + destination / block hash without
  exposing seeds.

## Acceptance criteria (for a first implementation PR)

- [x] Opt-in only; default demo path unchanged with zero Nano config.
- [x] Profit gate runs before any invoice is created.
- [x] `verify_nano_block` (or sim) required before fulfilment.
- [x] Underpay / wrong destination / replay / expired invoice never fulfil.
- [x] Treasury records rail + payment ref; Stripe paths untouched.
- [x] Tests cover sim verify + rejection cases; no network in unit tests.
- [x] SECURITY.md notes: verify-only v1, no hot wallet, public block ≠ privacy.

## References

- Issue: https://github.com/ianalloway/solvent-agent/issues/83
- Nano signing / key derivation: https://docs.nano.org/protocol-design/signing-hashing-and-key-derivation/
- Example onramp address API cited in #83: `https://getunstuck.space/unstuck/api/v1/onramp/address`
- x402 reference cited in #83: `https://extract.paypercall.dev/.well-known/x402`

## Implementation status

Simulate (v1) ships when `SOLVENT_NANO=sim`:

| Piece | Location |
|---|---|
| Client | `solvent/nano_client.py` — `create_nano_invoice`, `verify_nano_block` |
| Stages | `solvent/stages.py` — nano branch in checkout / paid |
| Treasury | `nano_invoices` table + ledger `payment_ref` (alias of `stripe_ref`) |
| HTTP | `POST /payments/nano/verify` (verify-on-submit); `POST /webhooks/nano` → 501 stub |
| Doctor | `nano_rail` check reports sim vs unset |
| Tests | `tests/test_nano_client.py` |

Still deferred: live RPC, hot seed, FX oracle beyond the fixed sim rate, x402.
