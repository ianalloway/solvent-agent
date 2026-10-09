# Changelog

All notable changes to SOLVENT are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-10-09

This is everything on `main` since the 0.1.0 PyPI upload on 2026-09-04.
The headline: the agent's commercial judgement grew from a single margin gate
into a full set of operating tools, and every one of them ships as a CLI command.

### Added
- **Counter-offers and `solvent quote`.** Every decline now carries the deal the
  agent *would* accept: a narrower scope at the customer's budget or the lowest
  price that clears the margin floor. `solvent quote` runs the gate as a dry
  run and exits 1 on a decline. (#75)
- **Spend-distribution guardrails.** Per-vendor 24-hour caps, a spend-velocity
  limit, and a tunable policy, inspectable with `solvent guardrails`. (#75)
- **Capital-aware backlog.** `solvent backlog` ranks open jobs by return on
  capital and fundability, and the worker uses that order. (#75)
- **Customer book.** `solvent customers` reports lifetime value, repeat rate, and
  margin by customer. (#82)
- **Cost calibration.** `solvent costs` compares estimated and realized COGS, and
  the agent quotes from what jobs actually cost. (#82)
- **Policy simulation.** `solvent simulate` tries pricing and spend policy over
  synthetic demand before it touches real money. (#82)
- **Intake and checkout lifecycle.** `solvent intake` screens inbound work before
  quoting, and `solvent checkouts` chases unpaid links and then closes them. (#82)
- **Policy optimizer.** `solvent optimize` searches margin floor × minimum order
  for the best policy. (#82)
- **Throughput capacity.** `solvent capacity` reports the jobs/day ceiling and
  which spend rule binds it. (#84)
- **Health alerts.** `solvent alerts` runs one health sweep and exits non-zero on
  anything critical. (#84)
- **Books export.** `solvent export` writes the ledger, jobs, metrics, and
  customers to CSV/JSON, with a period close. (#84)
- **Price list.** `solvent products` checks the price list against current
  fulfilment cost. (#85)
- **Quality gate.** `solvent quality` shows the scores the deliverable gate gave
  shipped briefs. (#85)
- **Operator review queue.** `solvent review` approves or rejects jobs that
  intake held for a human. (#85)

### Fixed
- `solvent serve` job-submission routes no longer raise `RecursionError` (#77, #86).
- The agent runs on Windows (`fcntl` import, path guard, home-directory
  fallback). (#73)
- A refund is no longer booked as operating spend.

### Documentation
- The README separates the offline demo from production revenue and documents
  every new command. (#74)

### Fixed
- Config and spend policy are pinned to the Solvent home instead of the working
  directory, so limits can no longer silently fail open (#78, #87).
- Every inference call is now billed, and COGS drift is flagged in both
  directions (#79, #90).
- Operator dashboard escapes customer-supplied job data (#92).
- README demo figures regenerated from a real run (#80, #89).

### Known issues
- Webhook handling: events are logged before the Stripe signature is checked, and
  the webhook list, stats and replay routes are not authenticated. Run `serve`
  only on a trusted network until this is fixed.
- Some dollars-to-cents conversions truncate instead of rounding ($19.99 can
  become 1998 cents).

## [0.1.0] - 2026-09-04

First PyPI release (`pip install solvent-agent`), built by the publish workflow
from `main` at `33cb136`. The older `v0.1.0` git tag (2026-07-25) points at an
earlier commit. It includes the offline demo, Stripe
test-mode Payment Links, NVIDIA Nemotron fulfilment, the guardrail sandbox,
`serve` / `worker` / `telegram`, and the treasury dashboard.

[0.2.0]: https://github.com/ianalloway/solvent-agent/compare/33cb136cfd04...v0.2.0
[0.1.0]: https://github.com/ianalloway/solvent-agent/tree/33cb136cfd04
