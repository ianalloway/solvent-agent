# Nano retirement and migration

The Nano (XNO) payment and custody rail is retired in the unreleased code.
Stripe test-mode checkout and offline Stripe simulation are the supported paths.
This guide replaces the former design and setup instructions for issue #83;
it does not imply that a release, deployment or migration has already happened.

## What was removed

- Nano invoice creation and payment verification, including the verification and
  webhook HTTP routes.
- Nano client and custody modules, custody commands and tool registrations.
- Nano selection in the Gradio and static demos.
- Active use of `SOLVENT_NANO` and its related RPC, address, rate, custody and
  signing settings. They must not be used to enable payment processing.

New jobs use Stripe even if retired Nano settings remain in the environment.
Doctor reports stale Nano settings without connecting to RPC or reading keys.
The demos clear legacy rail settings when using older published package versions.

## Reconcile existing installations before upgrading

1. Stop accepting new Nano payments and pause the old worker. Identify outstanding
   invoices, jobs, customer obligations and any payments sent to old destinations.
2. Back up the complete SOLVENT data directory, including the SQLite database,
   ledger, checkout and invoice records, and any historical JSON files. Preserve
   `nano_payments.json`, `nano_custody_sim.json`, `nano_custody_live.json` and other
   custody records if present.
   Retain original amounts, rates, destinations, timestamps and block references.
   Upgraded `solvent serve` and `solvent worker` commands preserve existing data
   by default. Older versions require `--keep-balance` with both commands; the
   flag remains compatible after upgrading. Never use `--reset-state` or run a
   fresh demo against the production data directory. See the
   [startup and reset guidance](PRODUCTION.md#safe-restarts-and-explicit-resets).
3. Reconcile every outstanding old job and payment with the operator's records and,
   where applicable, the public ledger. Simulated proofs are not real payments.
   Resolve delivery, cancellation or refund obligations through a separately
   authorized operator process before deleting anything.
4. After reconciliation, remove obsolete Nano settings from deployment configuration
   and review any external signing or credential integrations separately. Do not
   delete records or credentials merely because this code no longer uses them.
5. Validate Stripe test-mode configuration and run `solvent doctor` before resuming
   new work. Do not convert old Nano invoices into Stripe charges or replay them
   as new orders without resolving the original obligation first.

There are **no automated Nano payouts or refunds** in this version. Historical
ledger-only refund entries do not prove that an on-chain refund occurred. The old
live custody integration shipped without a live signer and was not validated with
real funds; no balance transfer is implied by retirement.

## Historical records and held jobs

Existing SQLite ledger, invoice and checkout rows and historical JSON files are
preserved, not dropped or rewritten as part of this removal. Generic payment
references, including old block hashes, remain audit evidence. Stripe reconciliation
must not treat those hashes as Stripe PaymentIntent identifiers.

Historical Nano jobs are held for manual reconciliation before retry or fulfilment.
Advancement returns a payment-pending result with a retired-rail reason without
changing the saved job status or silently sending the job down Stripe. Historical
Nano checkouts require review: active checkout listings omit their old payment
URLs, and automated reminders and expiry
are skipped. Retaining a record does not mean that its payment rail is supported.

Archived changelog and release notes describe earlier versions only. Public Nano
block hashes, amounts and addresses remain public after integration removal.
