# SOLVENT — 3-Minute Demo Video Script

**Goal:** show judges (Nous, NVIDIA, Stripe) a real agent that earns, spends, and
runs as a profitable business — judged on usefulness, viability, presentation.

**Format:** screen recording + voiceover. Total ~2:50. Record the terminal +
the `treasury_dashboard.html` page. Keep energy high; let the numbers do the work.

**Tools to film with:** QuickTime screen recording (Mac) or OBS. Talk over it, or
record voice separately and lay it on top. Don't over-produce — judges value
substance.

---

### SHOT 1 — The hook (0:00–0:20)
*On camera or over a black title card reading "SOLVENT — an agent that runs itself
as a business."*

> "Most AI agents spend money. This one *makes* it. SOLVENT is an agent you can
> hand a Stripe key and walk away from. It sells research, gets paid, pays its own
> bills, and refuses any job that won't turn a profit. Let me show you it run."

### SHOT 2 — The business loop (0:20–1:25)
*Screen: terminal. Run `python run_demo.py`. Let it scroll.*

> "Four jobs come in. Watch what it does with each one."

Point at the screen as these land:
- *(J1/J2)* "It quotes the job against its own costs, sees an 87% margin, issues a
  **Stripe payment link**, and collects the money up front."
- *(fulfilled)* "Then **Nemotron** writes the brief…"
- *(spend lines)* "…and here's the part nobody else does — it **pays its own
  vendors** out of that revenue. Inference, market data, the SaaS it used. Every
  one of those is a real outbound payment."
- *(J3 declined)* "This one's a six-dollar job. It costs more than that to do, so
  the agent **declines it.** It will not work at a loss."

> "By the end: $223 earned, $13 spent, **94% margin**, and the agent grew its own
> treasury from a $100 seed to $310 — by itself."

### SHOT 3 — The balance sheet (1:25–2:00)
*Screen: open `treasury_dashboard.html`. Slowly scroll the cards + ledger.*

> "This is the agent's balance sheet. Revenue in, vendor costs out, net profit,
> and every transaction logged. This isn't a chatbot — it's a company with books."

### SHOT 4 — Safety (2:00–2:35)
*Screen: terminal. Run `python demo_guardrails.py`.*

> "Now — would you actually give an agent your payment credential? You would if it
> can't misuse it. Every spend passes a **NemoClaw-style** policy layer first."

Point at each BLOCKED line:
> "Unknown vendor — blocked. Payment over the cap — blocked. Would drain its cash
> reserve — blocked. A job that'd lose money — blocked. Only the in-policy payment
> goes through. The agent is safe by construction."

### SHOT 5 — The close (2:35–2:50)
*Back to title card or the dashboard.*

> "Earns, spends, runs operations, stays solvent — built on Nemotron, NemoClaw,
> and Stripe. That's SOLVENT. Imagine a thousand of these, each a profitable
> micro-business, running themselves. Thanks for watching."

*End card: "SOLVENT · NVIDIA × Stripe × Nous · @YourHandle"*

---

## Filming checklist
- [ ] Increase terminal font size (so text is readable on phones).
- [ ] Run `python run_demo.py` once before recording (warms paths, looks clean).
- [ ] Have `treasury_dashboard.html` already open in a tab to cut to.
- [ ] (Optional, stronger) set a Stripe **test** key first so the payment-link
      URLs are real `https://buy.stripe.com/...` links you can click on camera.
- [ ] Keep it under 3:00. Tweet the video tagging **@NousResearch**.

## The 30-second version (if you want a teaser)
Run `run_demo.py`, narrate the result line ("earned $238, 93% margin, grew its own
treasury"), cut to the dashboard, end. Post as a reply to the main thread.
