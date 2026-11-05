# Commercial judgement

<sub>Moved from the main [README](../README.md).</sub>

## 🧭 Commercial Judgement

Three things turn the money loop into something that behaves like a shop
rather than a script.

### Counter-offers — a decline is a negotiating position

The margin gate still refuses work it cannot do profitably, but it no longer
just says no. Every decline carries the deal the agent *would* accept:

```bash
python3 -m solvent quote "Edge-AI in industrial robotics" --budget 8 --tokens 30000
```

```
  Projected margin     $-4.33 (-54.1%)   floor 35.0%
  Verdict              DECLINE — order $8 below minimum order size $15

  Counter-offer        $19.00 at 35.1% margin
    can deliver this brief as specified for $19.00
```

Two shapes, in order of preference: a **narrower scope** the customer's
existing budget can buy (fewer market-data pulls first — they are the priciest
resource), or, when no sellable scope fits, the **lowest price** that clears
the margin floor. The offer is emitted as a `counter_offer` event next to the
decline, so any channel — terminal, Telegram, the job API — can quote it back.
`solvent quote` runs the whole gate as a dry run: nothing is written to the
treasury, no Stripe call is made, and the exit code is 1 on a decline so
scripts can gate on the verdict.

Accepted quotes persist a resource contract. `est_tokens`, `web_search_calls`
and `market_data_calls` are total job allowances, including the quality retry,
not estimates that permit additional consumption. The server applies a minimum
of 4,000 tokens and three searches; zero market-data calls is supported. Both
research attempts share these allowances and a 20-tool-call cap.

Before each live model request (including nested summaries and transport
retries), SOLVENT reserves UTF-8 prompt bytes plus 256 chat-template tokens and
the 1,200-token output cap. This conservative bound assumes a byte-level
tokenizer and a two-message template within that overhead. Reservations are
not returned after uncertain failures. Tool and attempt reservations are also
saved before execution, so restarting a worker cannot reset a paid job's
budget. Reserved model usage is reported as estimated COGS at the quoted rates.
A quality retry that runs out of budget keeps the first draft; an initial
attempt that cannot finish follows the normal failure/refund path. Existing
quotes without a resource contract must be requoted before new fulfilment.

### Spend policy — bounding *how* money moves, not just how much

The guardrails gained two rules that shape the distribution of spend:

| Rule | What it stops |
|---|---|
| **Per-vendor 24h cap** | one vendor — compromised, mispriced, or just buggy — absorbing the whole day's budget |
| **Spend velocity** | a fulfilment loop that starts paying in a tight cycle, long before it drains the treasury |

Limits are operator-tunable without touching code, via
`.solvent/spend_policy.json` (a malformed file is ignored rather than allowed
to widen the policy):

```json
{
  "daily_budget_cents": 50000,
  "per_vendor_daily_cents": 8000,
  "max_txns_per_hour": 40,
  "vendor_daily_overrides": { "market-data-api": 15000 }
}
```

The `.solvent` configuration directory is inside the resolved runtime home:
the source checkout by default, or `$SOLVENT_HOME` when set. Run
`solvent guardrails` or `solvent doctor` to see the active spend-policy path.
If an older installation kept its `.solvent` files in a different launch
directory, move them into this resolved directory before upgrading.

`python3 -m solvent guardrails` prints the policy in force, how much of each
rolling window is used, per-vendor exposure against its cap, and every spend
the policy blocked.

### Spending controls — per-job envelopes and human approval

Two more rules sit on top of the daily, per-vendor and velocity limits. Both
run inside the same deterministic guardrail check, before any Stripe call, and
both fail closed.

| Rule | Decision rule id | What happens |
|---|---|---|
| **Per-job budget envelope** | `job_budget` | Every job has a hard spend cap in integer cents (`job_budget_cents`, default 15000 = $150; `0` turns it off). Spend up to *exactly* the cap is allowed; anything that would go past it is refused with "job '…' budget exhausted". Refunds don't count against it. |
| **Human-approval hook** | `approval_pending` / `approval_denied` | A spend *strictly above* `approval_threshold_cents` is not paid. It goes to a pluggable approver, which answers `"approved"`, `"denied"` or `"held"`. The default approver always holds. An approver that raises an error or returns anything else also holds. Approval cannot override a hard rule such as the job envelope. |

Configure them in `.solvent/spend_policy.json`:

```json
{ "job_budget_cents": 8000, "approval_threshold_cents": 2500 }
```

or with the environment variables `SOLVENT_JOB_BUDGET_CENTS` and
`SOLVENT_APPROVAL_THRESHOLD_CENTS`. Env vars can only **tighten** these
settings: a lower budget or a lower threshold. An env value that would raise
or disable a limit (for example `SOLVENT_JOB_BUDGET_CENTS=0`) is ignored.
Loosening or disabling takes an explicit policy-file setting, and any override
that loosens or disables a limit, or is ignored, is logged as a warning and
shown in `solvent guardrails`. The approval hook is off unless a threshold is
set.

A spend evaluated without a job id is not exempt from the budget. All unscoped
spends share one `unscoped` envelope under the same cap.

Wire in your own approver (a Slack prompt, a ticket queue, a CLI confirm):

```python
from solvent.guardrails import ApprovalRequest, Guardrails

def approver(req: ApprovalRequest) -> str:
    return "approved" if req.vendor == "market-data-api" else "held"

guard = Guardrails(treasury, approver=approver)
```

A held spend stops the job and emits a `spend_held` event, so nothing moves
until a human decides.

**How this relates to the card networks.** The pattern is close to what the
networks describe publicly. Mastercard's
[Agent Pay for Machines](https://www.mastercard.com/global/en/news-and-trends/press/2026/october/Mastercard-Agent-Pay-for-Machines-in-action.html)
gives an agent "a job and a spending limit", and Mastercard's
[agentic token framework](https://www.mastercard.com/us/en/news-and-trends/stories/2025/agentic-commerce-framework.html)
carries transaction limits and validity windows. In the
[Visa, BILL and Billtrust pilot](https://corporate.visa.com/en/sites/visa-perspectives/newsroom/visa-bill-billtrust-launch-pilot.html),
the agent only pays invoices the buyer has already approved. Stripe's
[Issuing for agents](https://docs.stripe.com/issuing/agents) offers per-card and
per-cardholder
[spending controls](https://docs.stripe.com/issuing/controls/spending-controls)
and real-time authorization webhooks. Those controls run at the card or network
layer. Solvent's run inside the agent, before it calls any payment API, so they
work in simulation and with any payment backend. They complement network-level
controls and don't replace them.

### Backlog — which job to work on next

A queue is not a plan. When several jobs are open and both cash and the 24h
spend budget are finite, the order the agent works in decides what it earns.
`python3 -m solvent backlog` ranks the open work the way a business would —
and the async worker consumes the same ranking:

1. **Finish what is already paid for.** Revenue is collected before cost is
   incurred, so a paid job left unfinished is a refund waiting to happen.
2. **Then best return on capital** — margin per cent of fulfilment cost, so a
   $20 job costing $5 outranks a $90 job costing $60.
3. **Never start work the treasury cannot fund.** A job whose fulfilment would
   breach the spend budget or the cash reserve is *deferred* until the
   treasury can pay for it — where the quote stage would otherwise decline it
   permanently — and a cheaper job behind it can still take the remaining
   capacity.

```
  #  JOB         STATUS                    PRICE     COST    ROI  TOPIC
  1  J4          awaiting_payment         $99.00    $8.07  11.27   Edge-AI adoption in industri
  2  J5          awaiting_payment        $125.00   $10.41  11.01   Unit economics of autonomous
    ⏸ J2: fulfilment needs 845c; only 200c of spend capacity left (24h budget / cash reserve)
```

### Knowing the business

Three more views the agent keeps on itself.

**`solvent customers` — who actually pays.** Every job carries an email and
every ledger entry carries a job id, so the two join into lifetime value per
customer: revenue, COGS, net, repeat rate, and the share of revenue riding on
the single best customer (the concentration risk). `--email <addr>` drills into
one customer's job history.

```
  CUSTOMER                           JOBS    REVENUE        NET  MARGIN  LAST
   analyst@logistics.example            1    $125.00    $124.45   99.6%  just now
  ↻analyst@fund.example                 2     $98.00     $96.90   98.9%  just now

  Customers 5  ·  repeat 1 (20.0%)  ·  revenue per customer $79.40
  Top customer is 31.5% of revenue
```

**`solvent costs` — a margin gate that learns.** The stage machine already
recorded what each job really cost; now that feeds back into the next quote.
When realized COGS run *hotter* than the static model, quotes are marked up by
the observed ratio (clamped, and only after five fulfilled jobs) so the margin
floor keeps meaning what it says. When they run *cooler*, nothing happens
automatically — an optimistic sample is not a reason to quote closer to the
bone, and cutting prices stays an operator decision.

**`solvent simulate` — try the policy before it touches money.** Margin floor,
transaction cap, daily budget, cash reserve: every one is a number somebody
picks, and picking them on a live treasury means finding out the expensive way.
This runs the same `pricing` and `guardrails` kernel over synthetic demand,
many times, and reports the distribution — acceptance rate, net per day, ending
balance percentiles, how often the business ends below its reserve, and which
rule did the blocking. One trial is one day of trading, so the rolling 24h
budget and the velocity rule bind the way they would in a real day.

```bash
python3 -m solvent simulate                            # the policy as it stands
python3 -m solvent simulate --cost-multiplier 6        # what if vendors got 6× pricier
python3 -m solvent simulate --margin-floor 55 --json   # tune the floor, machine-readable
```

Nothing in a simulation touches the treasury, Stripe, or Nemotron, and the same
`--seed` always reproduces the same run.

### Running the shop

**`solvent checkouts` — abandoned carts are normal; leaving them open is not.**
A job that reached `awaiting_payment` used to sit there forever: polled on
every worker pass, counted as pipeline, and never chased. Unpaid links now have
a lifecycle — the customer is reminded after `reminder_after_hours` (at most
`max_reminders` times, spaced), and the link expires after
`expire_after_hours`, closing the Stripe session and dropping the job out of
the queue. Expiry never moves money: an unpaid job has no revenue to refund.
The worker sweeps on every pass; `--sweep` runs it by hand. Tune it in
`.solvent/checkout_policy.json`.

**`solvent intake` — the screen before the margin gate.** `security.py` refuses
hostile content; this refuses bad *commerce*, and it runs before pricing so a
screened-out job costs nothing:

| Rule | What it catches |
|---|---|
| `duplicate` | the same customer asking for the same brief inside an hour — a double-click, not two commissions |
| `customer_burst` | one customer flooding the queue |
| `oversized_order` | an order above the automatic ceiling, where a human should look first |
| `unreachable_customer` | a missing or malformed email, or a blocked domain |

Blocks are recorded on the job with an `intake:` reason, so they are greppable
in `solvent jobs` and the event log. Thresholds live in
`.solvent/intake_policy.json`.

**`solvent optimize` — which policy should I actually run?** `simulate` answers
"what would this do"; this searches the space. It sweeps margin floor ×
minimum order over identical synthetic demand (common random numbers, so cells
differ by policy and nothing else) and picks the best-paying cell *inside a
stated risk budget* — by default, ending a day below the cash reserve at most
5% of the time. The constraint is the whole point: without it, "best" always
picks the reckless cell.

```
    FLOOR  MIN ORDER   ACCEPT     NET/DAY    p10 BAL  BELOW RES
    35.0%     $10.00    17.7%      $-3.04    $-34.29      28.0%
    45.0%     $10.00    24.0%      $35.84     $32.26       0.0% ←

  → Run a 45.0% margin floor with a $10.00 minimum order: $35.84 net per day.
    vs the policy in force (35.0% / $15.00): $12.35 more per day.
```

(that run is `--cost-multiplier 8`: the same search under a vendor price shock)

### Operating it day to day

**`solvent capacity` — how much work can this policy get through?** The
simulator found the ceiling the expensive way; this derives it in closed form.
Every spend rule implies a maximum number of jobs per day — daily budget ÷ cost
per job, spendable cash ÷ cost per job, payments per hour ÷ payments per job,
each vendor cap ÷ that vendor's share — and the lowest one is the real ceiling.
Unit costs come from what jobs *actually* cost when there is history and from
the pricing model when there is not, and the report names the one rule worth
changing:

```
  Ceiling              250.0 jobs/day
  Bound by             vendor_daily_budget:pdf-render-saas
                       $100.00 cap ÷ $0.40 of pdf-render-saas per job
  → Raise the cap for pdf-render-saas via vendor_daily_overrides in .solvent/spend_policy.json.
```

**`solvent alerts` — the checks you'd run every morning.** Runway, cash against
the reserve, paid-but-undelivered work, spend headroom, vendor exposure,
guardrail blocks, unpaid pipeline, and cost-model drift, in one sweep with
severities. It exits non-zero on anything critical, so it works as a cron job
or a CI step (`solvent alerts || page-someone`), `--notify` pushes problems to a
chat channel, and every alert says what to do about it — an alert nobody can
act on is noise.

**`solvent export` — the books, in a form a spreadsheet can read.** Writes the
ledger, jobs, per-job metrics and customer book as CSV (one file per table) or
a single JSON document, over a period given as `7d`, `24h`, `2w`, `3m` or
`YYYY-MM-DD`. The period is closed on the ledger rather than the job table —
revenue and cost counted when the money moved — so the printed summary ties out
against the rows it ships, with refunds separated from cost of sales.

### How long it takes

"When will I get it?" is the second question every customer asks, and the agent
had no answer. Fulfilment seconds were recorded, but that is only the model's
part — what the customer experiences is the wait from *paying* to *having the
brief*, across the queue, the worker's poll, retries and delivery.

That wait is already on the record (the ledger says when the money arrived, the
event log says when the brief went out), so `solvent turnaround` measures it
rather than estimating, and turns the distribution into something quotable: a
promise at a chosen percentile, rounded up, never faster than a floor. The
promise then appears on accepted quotes:

```
  Verdict              ACCEPT — accepted
  Turnaround           within 5 minutes
```

It also retires an arbitrary threshold. `alerts.py` called a paid job "stuck"
after two hours because two hours sounded reasonable; a shop that normally
delivers in forty seconds should not wait two hours to notice, and one that
takes a day should not cry wolf. With enough history, late is defined by the
shop's own p95 — and only until then by the fixed hours.

### Running it from Telegram

Fifteen commands' worth of reporting lived in the CLI, which is where the
operator is not. The gateway's slash commands now cover the business too —
`/funnel`, `/customers`, `/capacity`, `/alerts`, `/products`, `/quality`,
`/review`, `/costs`, `/status` — each a short read over local state, no model call, no
spend.

The catch is who is asking. Any *paired* Telegram user reaches that handler,
and a customer is not entitled to the agent's win rate or cash position, so
these are operator-only:

- the **dashboard** channel is the operator by construction — it sits behind
  `SOLVENT_DASHBOARD_TOKEN`;
- a Telegram user id in `SOLVENT_OPERATOR_IDS` is the operator in a private chat;
- anyone else gets "That command is operator-only", and `/help` does not list
  commands they cannot run.

The `treasury_status` model tool uses the same operator boundary. Pairing QR
tokens also require an operator; pairing a customer does not grant permission
to mint new tokens, and Telegram groups cannot mint them.

### The deliverable

Every job has been billed `pdf-render-saas` since the first commit, and no PDF
was ever produced — the agent was paying itself for a deliverable it did not
deliver. It renders one now: markdown, HTML **and** PDF, written beside each
other in `data/reports/`, linked from the delivery email and served from the
same token-gated route as the brief itself (`&format=pdf`), so nothing new is
exposed. With SMTP configured the PDF is attached to the email.

The renderer is `solvent/pdf.py`, about 250 lines of stdlib: a PDF is a text
container with a byte-offset table, so a single-font document needs no library,
only exact xref offsets. Base-14 Helvetica means nothing is embedded and a
brief weighs a couple of kilobytes. A render failure is caught and logged
rather than raised — losing a brief the customer has paid for would be far
worse than shipping it without the PDF.

### Knowing the win rate

**`solvent funnel`.** The treasury has always known the margin on the jobs it
took; it never knew the win rate. Those are different businesses — a 90% margin
on one job in ten is a worse shop than a 60% margin on eight, and nothing here
could tell them apart.

Every stage already leaves a trace, so the funnel is a read over data the agent
keeps anyway: submitted → quoted → accepted → checkout → paid → delivered. What
falls out at each step is the point, and the three failures want three
different fixes:

| Exit | What it means |
|---|---|
| `screened` | held before pricing — cost nothing |
| `declined` | a customer who wanted to pay and was turned away by the margin gate |
| `abandoned` | a customer who said yes at checkout and then did not pay |
| `refunded` | paid, then refunded when fulfilment failed — the expensive loss |

Lost pipeline is reported at the price the customer named, because that is the
money that was on the table. Win rate is broken down by product and price band,
and counter-offers are tracked separately, so "does the counter-offer actually
convert?" finally has an answer.

```
  Submitted                 9  ████████████████████████ 100.0%
  Accepted by the gate      6  ████████████████········ 66.7%
  Paid                      5  █████████████··········· 55.6%

  Gate acceptance      85.7%   ·  payment conversion 83.3%
  Won $373.00  ·  left on the table $9,130.00
  Counter-offers       1 made, 0 won (0.0%)
```

The same picture now has a **Commercial** panel in the HTML dashboard — win
rate, customer book, brief quality and the throughput ceiling beside the P&L —
so the business reads in one place instead of across six commands.

### Selling, not just quoting

**`solvent products` — a price list.** A shop that cannot say what it charges
cannot be ordered from. A product is a named scope at a list price (how much
reasoning, how many data pulls, how many searches), and a job can name one
instead of pricing itself:

```bash
python3 -m solvent quote "Edge-AI in robotics" --product standard
curl -X POST localhost:8787/jobs -d '{"topic": "...", "product": "deep-dive", ...}'
```

The catalogue is priced against *current* costs — calibration included — so a
list price that has quietly stopped clearing the margin floor shows up as a
stale price rather than as a run of unprofitable work.

Changing what the shop sells is a business decision, not a code change, so the
built-in list is overridable in `.solvent/products.json` beside the spend and
intake policies. Entries merge by slug — naming only a price keeps the built-in
scope — `retire` drops a product, and `replace: true` makes the file the whole
list:

```json
{
  "products": [
    { "slug": "express", "list_price_cents": 3500 },
    { "slug": "retainer", "name": "Monthly retainer", "list_price_cents": 49900,
      "est_tokens": 60000, "market_data_calls": 12, "web_search_calls": 40 }
  ],
  "retire": ["deep-dive"]
}
```

A malformed entry is dropped rather than sold — a half-read product is a price
nobody agreed to — and `solvent products --validate` reports it. The command
exits non-zero when any product has gone stale *or* the file is broken, so cron
catches either without reading the output.

**`solvent quality` — a gate on the product, not just the margin.** Every other
gate here asks whether the money is sound; this one asks whether the brief is.
Deterministic checks — required sections, length, figures cited, no prompt
scaffolding, topic coverage — score each brief out of 100 with a named reason
for every point lost. A model grading its own homework is the one judge you
cannot audit, which is why none of this asks an LLM.

Two failures are gates rather than tariffs, because points cannot buy them
back: scaffolding in the text is what a customer notices first, and a brief
matching none of the topic's keywords is not about what they asked for. Either
can score 85 on structure alone; neither is worth a pass. A failing brief gets
one regeneration attempt and the better draft ships — it always ships, because
the customer has paid and withholding the work is worse than delivering it with
the shortfall recorded.

**`solvent review` — the queue only a human can clear.** The intake screen
refuses an order above the automatic ceiling with "needs an operator", which
was honest and incomplete: there was no way to approve one. Now held jobs sit
in a queue with the rule that caught them, and the operator decides:

```bash
python3 -m solvent review                      # what is held, and why
python3 -m solvent review approve BIG          # back into the pipeline
python3 -m solvent review reject BIG --reason "could not verify funds"
```

Approval writes a one-off exemption onto that job — it does not loosen the
policy, which stays a deliberate edit — and both decisions are recorded as
events, because "who let this $5,000 order through" is a question that gets
asked later.
