# Agent Workspace (Brain + Soul)

SOLVENT adopts the **OpenClaw workspace** and **Hermes SOUL** patterns: markdown files in `.solvent/workspace/` are injected into the system prompt every session. Together they are the agent's **brain**; `SOUL.md` is the **soul** (identity slot #1).

## Layout

```
.solvent/workspace/
├── SOUL.md          # Identity, tone, values (Hermes slot #1)
├── IDENTITY.md      # Name, emoji, intro line
├── BRAIN.md         # Active pipeline / NOW state (read every session)
├── AGENTS.md        # Operating rules and workflows
├── USER.md          # Who you are
├── TOOLS.md         # Tool and CLI conventions
├── HEARTBEAT.md     # Optional proactive checklist
├── MEMORY.md        # Curated long-term memory (main DM only)
├── BOOTSTRAP.md     # One-time first-run ritual
├── memory/
│   └── YYYY-MM-DD.md
└── skills/
    └── {name}/SKILL.md   # OpenClaw/Hermes skill layout

.solvent/skills/     # Learned skills (improver promotions)
```

Repo-root **`AGENTS.md`** (this repository) is loaded as Hermes-style project context alongside the workspace files.

## Prompt assembly

| Order | File | Loaded when |
|-------|------|-------------|
| 1 | `SOUL.md` | Every session (identity) |
| 2 | `IDENTITY.md` | Every session |
| 3 | Core economic rules | Every session (code) |
| 4 | `BRAIN.md`, `AGENTS.md`, `TOOLS.md`, `USER.md` | Project context |
| 5 | `MEMORY.md` | Main/private sessions only (Telegram DM, CLI) |
| 6 | `memory/today+yesterday` | When present |
| 7 | `skills/*.md` | When present |

Shared/group channels skip `MEMORY.md` (OpenClaw privacy pattern).

## Setup

```bash
python -m solvent workspace setup   # seed templates (never overwrites)
python -m solvent workspace list    # show files + sizes
python -m solvent doctor            # checks SOUL/AGENTS/BRAIN exist
```

Onboarding (`python3 run_demo.py --onboard`) seeds the workspace automatically.

## Customize

Edit files in `.solvent/workspace/` — changes apply on the next message.

**Rule of thumb** (Hermes/OpenClaw):

| If it describes… | Put it in… |
|------------------|------------|
| Who the agent is, tone, values | `SOUL.md` |
| Who you are | `USER.md` |
| How to work, workflows | `AGENTS.md` |
| What's happening *now* | `BRAIN.md` |
| Tool/CLI notes | `TOOLS.md` |
| Durable learned facts | `MEMORY.md` |
| Day-to-day log | `memory/YYYY-MM-DD.md` |

## BRAIN.md

Not in upstream OpenClaw, but widely used: a living dashboard SOLVENT reads each session (referenced from `SOUL.md`). Update pipeline, blockers, and next actions — or ask the bot to update it after job events.

Job lifecycle events (`paid`, `fulfilled`, `delivered`) append to today's daily memory log automatically.

## Override path

```bash
export SOLVENT_WORKSPACE=/path/to/custom/workspace
```

## Related

- [TELEGRAM.md](TELEGRAM.md) — chat channel uses this workspace for every turn
- [ONBOARDING.md](../ONBOARDING.md) — wizard patterns from NemoClaw/Hermes
