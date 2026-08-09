# Agent Workspace (Brain + Soul)

SOLVENT adopts the **OpenClaw workspace** and **Hermes SOUL** patterns: markdown files in `.solvent/workspace/` are injected into the system prompt every session. Together they are the agent's **brain**; `SOUL.md` is the **soul** (identity slot #1).

## Layout

```
.solvent/workspace/
├── SOUL.md          # Identity, tone, values (Hermes slot #1)
├── BRAIN.md         # Active pipeline / NOW state (read every session)
├── AGENTS.md        # Operating rules and workflows
├── MEMORY.md        # Optional curated long-term memory (main sessions only; not seeded by default)
├── memory/
│   └── YYYY-MM-DD.md   # Daily event log, appended automatically
└── skills/
    └── {name}/SKILL.md   # OpenClaw/Hermes skill layout

.solvent/skills/     # Learned skills (promoted via solvent.workspace.promote_skill)
```

Repo-root **`AGENTS.md`** (this repository) is loaded as Hermes-style project context alongside the workspace files.

## Prompt assembly

| Order | File | Loaded when |
|-------|------|-------------|
| 1 | `SOUL.md` | Every session (identity) |
| 2 | Core economic rules | Every session (code) |
| 3 | `BRAIN.md`, `AGENTS.md` | Project context |
| 4 | `MEMORY.md` | Main/private sessions only, if present |
| 5 | `memory/today+yesterday` | When present |
| 6 | `skills/*.md` | When present |

Shared/group channels skip `MEMORY.md` (OpenClaw privacy pattern) — create the file yourself under `.solvent/workspace/MEMORY.md` if you want durable curated facts; it is not seeded by default.

## Setup

```bash
python -m solvent workspace setup   # seed SOUL/AGENTS/BRAIN templates (never overwrites)
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
| How to work, workflows | `AGENTS.md` |
| What's happening *now* | `BRAIN.md` |
| Durable learned facts (optional) | `MEMORY.md` |
| Day-to-day log | `memory/YYYY-MM-DD.md` |

## BRAIN.md

Not in upstream OpenClaw, but widely used: a living dashboard SOLVENT reads each session (referenced from `SOUL.md`). Update pipeline, blockers, and next actions.

Job lifecycle events (`paid`, `fulfilled`, `delivered`) append to today's daily memory log automatically.

## Override path

```bash
export SOLVENT_WORKSPACE=/path/to/custom/workspace
```

## Related

- [TELEGRAM.md](TELEGRAM.md) — chat channel uses this workspace for every turn
