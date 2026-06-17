# SOLVENT — Demo Narration (read-aloud teleprompter)

Read the **SAY** lines straight into the mic. `[SCREEN]` tells you what to be
showing. Pauses marked `(beat)`. Whole thing is ~2:45 at a calm pace. The numbers
below match the real verified run — if you set live keys later, just re-read the
final tally from your screen.

---

### 0:00 — HOOK  ·  `[SCREEN: title card or your face]`

**SAY:** "Most AI agents spend money. This one makes it.
(beat)
This is SOLVENT — an agent you can hand a Stripe key and walk away from. It sells
research, gets paid, pays its own bills, and refuses any job that won't turn a
profit. Let me just run it."

---

### 0:18 — LAUNCH  ·  `[SCREEN: terminal — type the command, hit enter]`

**SAY:** "It starts with a hundred dollars of seed capital. Four research jobs are
waiting in its queue. Watch what it does with each one."

`[SCREEN: output begins scrolling — J1]`

---

### 0:30 — JOB 1, the full loop  ·  `[SCREEN: J1 block]`

**SAY:** "Job one. First it quotes the work against its own costs — forty-nine
dollars to the customer, about six dollars to fulfil. Eighty-seven percent margin,
so it accepts.
(beat)
It issues a Stripe payment link and collects the forty-nine dollars up front.
Then Nemotron writes the brief.
(beat)
And here's the part nobody else does — it pays its own vendors out of that
revenue. Inference. Market data. The SaaS it used. Every one of those is a real
outbound payment. Job one books forty-five dollars of profit."

---

### 1:05 — JOB 2, go faster  ·  `[SCREEN: J2 block]`

**SAY:** "Job two, same loop — seventy-five dollars in, it does the work, pays its
costs, banks seventy dollars. The balance is climbing on its own."

---

### 1:20 — JOB 3, the decline (KEY MOMENT)  ·  `[SCREEN: J3 — point at the DECLINE line]`

**SAY:** "Now this one matters. Job three is a six-dollar request — but it costs
more than six dollars to actually produce. So the agent declines it.
(beat)
It will not work at a loss. That's the whole idea — this is a business, not a bot."

---

### 1:35 — JOB 4 + the tally  ·  `[SCREEN: J4, then the RESULT block]`

**SAY:** "Job four, a ninety-nine dollar brief — accepted, fulfilled, costs paid.
(beat)
And here's where it lands: two hundred twenty-three dollars earned, about thirteen
spent — a ninety-four percent margin. It grew its own treasury from a hundred
dollars to three hundred and ten. By itself."

---

### 1:55 — THE BALANCE SHEET  ·  `[SCREEN: open treasury_dashboard.html, scroll slowly]`

**SAY:** "This is the agent's balance sheet. Revenue in, vendor costs out, net
profit, every transaction logged — and the job it turned down. This isn't a
chatbot. It's a company with books."

---

### 2:15 — SAFETY  ·  `[SCREEN: terminal — run: python demo_guardrails.py]`

**SAY:** "Last thing. Would you actually give an agent your payment credential?
You would — if it can't misuse it. Every spend hits a NemoClaw-style policy layer
first."

`[SCREEN: point at each BLOCKED line]`

**SAY:** "Unknown vendor — blocked. Payment over the cap — blocked. Would drain its
cash reserve — blocked. A job that'd lose money — blocked. Only the in-policy
payment goes through. It's safe by construction."

---

### 2:40 — CLOSE  ·  `[SCREEN: back to the dashboard or title card]`

**SAY:** "Earns, spends, runs operations, stays solvent — on Nemotron, NemoClaw,
and Stripe. That's SOLVENT.
(beat)
Now imagine a thousand of these — each a profitable little business, running
itself. Thanks for watching."

`[END CARD: "SOLVENT · NVIDIA × Stripe × Nous · @YourHandle"]`

---

## Quick recording tips
- One clean take of `run_demo.py` first (off-camera) so paths are warm.
- Bump terminal font size so it's legible on a phone.
- If you fluff a line, pause 2 seconds and re-say it — easy to trim later.
- Total target: **under 3:00.** If you're tight on time, the J1 + J3 + result +
  one guardrail block is enough to win the point.
