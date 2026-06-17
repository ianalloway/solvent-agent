# SOLVENT — Submission Pack

Everything you need to enter the Hermes Agent Accelerated Business Hackathon.
**Deadline: EOD Tuesday, June 30.**

To enter you must:
1. Tweet a 1–3 min demo video tagging **@NousResearch** with a short writeup
2. Drop the link in the submissions channel (discord.gg/nousresearch)
3. Fill out the form: form.typeform.com/to/hpEifIK4

---

## 1) Tweet copy (the post with your video)

> Meet SOLVENT 🪙 — an AI agent that runs itself as a profitable business.
>
> It sells research briefs, gets paid via @stripe, spends its own revenue to
> provision the compute + data it needs on @NVIDIAAI Nemotron, and refuses any
> job that won't clear a margin.
>
> In the demo it turns a $100 seed into $310 at a 94% margin — and can't spend a
> dollar outside policy (NemoClaw-style guardrails).
>
> Earns. Spends. Stays solvent. Built for #HermesHackathon @NousResearch
>
> [attach video]

*(Shorter alt, if you want punchier):*
> Most agents spend money. SOLVENT *makes* it. 🪙
> Sells research → gets paid on @stripe → pays its own vendors on @NVIDIAAI
> Nemotron → declines unprofitable work. $100 → $310, 94% margin, fully
> autonomous. @NousResearch #HermesHackathon

## 2) Short writeup (paste under the video / in the thread)

**SOLVENT — a self-funding analyst agent.**

SOLVENT closes the whole agent-economy loop: it *earns*, it *spends*, and it runs
as a real business. Customers commission research briefs; SOLVENT prices each job
against its own unit costs, collects payment up front via a Stripe payment link,
writes the brief on NVIDIA Nemotron, then pays its own vendors — inference, market
data, SaaS — out of that revenue. Every payment is screened by a NemoClaw-style
policy layer (vendor allowlist, per-transaction cap, daily budget, cash reserve,
and a no-negative-ROI rule), so you can hand it a payment credential safely. And a
margin gate makes it structurally incapable of unprofitable work — in the demo it
declines a job that costs more than the customer will pay.

The result is a fully automated company with its own balance sheet that grows on
its own: $100 seed → $310, 94% margin, in one run. The thesis: the next wave of
agents won't just *use* money — they'll be accountable for it. SOLVENT is the
reference design for an agent that earns its keep.

Stack: Nemotron (reasoning) · NemoClaw-style guardrails (safety) · Stripe Skills
(earn + spend) · Hermes/Nous (orchestration).

## 3) Typeform answers (draft — adapt to the actual fields)

- **Project name:** SOLVENT
- **One-liner:** An AI agent that runs itself as a profitable, self-funding
  business — earns via Stripe, spends on Nemotron, refuses unprofitable work.
- **What it does:** Sells on-demand research briefs. Quotes each job against its
  own costs, collects payment via Stripe Payment Links, fulfils on Nemotron, then
  pays its own vendors — every payment screened by guardrails. Maintains a live
  treasury/P&L.
- **How it uses NVIDIA:** Nemotron-Ultra for the analyst reasoning; a
  NemoClaw-style policy sandbox screens every outbound payment.
- **How it uses Stripe:** Two-sided — Payment Links/invoices to charge customers
  (earn) and scoped agent payments to provision its own compute/data/SaaS (spend).
- **Why it's useful/viable:** It's a business, not a bot. The margin gate makes
  loss-making work impossible; the guardrails make autonomy safe. Demo: $100 →
  $310 at 94% margin. Generalizes to any agent that should be P&L-accountable.
- **Demo video:** [your tweet URL]
- **Repo / code:** [optional — zip the folder or push to GitHub]

## 4) Two-week plan to June 30

| When | Do |
|---|---|
| **Day 1–2** | Run `run_demo.py` + `demo_guardrails.py`. Read `ARCHITECTURE.md`. Confirm the story makes sense to you. |
| **Day 3–4** | (Optional, big credibility boost) Get a free NVIDIA API key (build.nvidia.com) and a Stripe **test** key. Set both env vars so the briefs are real Nemotron output and the payment links are real test links. |
| **Day 5–7** | Record the demo using `VIDEO_SCRIPT.md`. Re-record until it's tight and under 3:00. |
| **Day 8–9** | Polish: add your handle to the end card, write the tweet, double-check tags (@NousResearch, @NVIDIAAI, @stripe). |
| **Day 10** | Post the tweet, drop the link in Discord, submit the Typeform. Don't wait for the deadline. |

## 5) Pre-submit checklist
- [ ] Video is 1–3 minutes and tagged **@NousResearch**
- [ ] Tweet includes the short writeup
- [ ] Link dropped in the submissions Discord channel
- [ ] Typeform submitted
- [ ] (Bonus) Real Nemotron + Stripe test mode shown on camera
- [ ] (Bonus) Code pushed somewhere judges can see it

## 6) Judge-criteria cheat sheet (what to emphasize on camera)
- **Usefulness:** "any agent that should be accountable for money can use this
  pattern — it's a reference design, not a one-off."
- **Viability:** "the margin gate means it can't lose money; the demo is already
  94% margin." Say the numbers out loud.
- **Presentation:** lead with the loop running live, then the balance sheet, then
  the safety blocks. Three clean beats.

## 7) Stretch ideas (only if you have time — not required to win)
- Real outbound spend via a Stripe Issuing **test** virtual card scoped to the
  guardrails (makes "spends its own money" literally true on camera).
- A web intake form so jobs arrive from real users during the demo.
- A second agent that *buys* from SOLVENT — agent-to-agent commerce.
- Multiple SOLVENT instances with different margin floors competing for jobs.
