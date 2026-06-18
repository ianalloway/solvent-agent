# SOLVENT Onboarding — Design Notes

Patterns borrowed from **NVIDIA NemoClaw/OpenClaw** and **Nous Hermes** for the
first-run terminal wizard.

## NemoClaw / OpenClaw patterns

| Pattern | Source | SOLVENT adoption |
|---------|--------|------------------|
| Dedicated `onboard` command as lifecycle entry point | `nemoclaw onboard` | `python3 run_demo.py` runs wizard when no `.solvent/config.json` exists; `--onboard` to reconfigure |
| Provider-first wizard (pick inference, then credential) | NemoClaw quickstart | Step 1: model provider (offline stub vs Nemotron); warn if `NVIDIA_API_KEY` missing |
| Numbered provider menu | NemoClaw inference options | `[1] Offline stub`, `[2] NVIDIA Nemotron`, `[3] Custom (documented)` |
| Non-interactive escape hatch | `nemoclaw onboard --non-interactive`, env overrides | `--no-onboard`, `SOLVENT_SKIP_ONBOARD=1` → defaults without prompts |
| Credentials in local store (not repo) | `~/.nemoclaw/credentials.json` | `.solvent/config.json` (gitignored); API keys stay in env |
| Runtime model switch without full reinstall | `openshell inference set` | Config `model` field; re-run `--onboard` to change |
| Status banner after setup | `nemoclaw <name> status` | Post-wizard summary: model, mode, Stripe sim/live |
| DM pairing for chat channels | OpenClaw `dmPolicy: pairing` | Telegram `/start` → `python -m solvent pairing approve` |
| Agent workspace files | OpenClaw `~/.openclaw/workspace` | `.solvent/workspace/` seeded on onboard · `python -m solvent workspace setup` |

## Hermes / Nous patterns

| Pattern | Source | SOLVENT adoption |
|---------|--------|------------------|
| `hermes setup` interactive wizard on first boot | Hermes quickstart | Auto-run wizard when config missing |
| Quick vs full setup tiers | Hostinger Hermes tutorial | Single quick wizard (3 steps); programmatic mode = “skip to library” |
| Provider + model selection | `hermes model` | Model step with offline default for zero-credential demos |
| Interaction surface choice | `hermes` CLI vs `hermes --tui` vs `hermes gateway` | Batch demo / interactive REPL / programmatic-only guidance |
| Config in `~/.hermes/` | Hermes docs | `.solvent/config.json` |
| `hermes doctor` for diagnostics | Hermes CLI | `python -m solvent doctor` |
| Channel gateway + pairing | OpenClaw `dmPolicy: pairing` | `solvent/gateway.py`, `solvent/channels/telegram.py`, `python -m solvent pairing` |
| Progressive tool disclosure | Hermes `tool_search` / `tool_call` | `solvent/hermes_tools.py` when catalog > 10 tools |
| Session memory | Hermes chat history | `solvent/memory.py` · `chat_messages` table |
| Agent workspace (brain) | OpenClaw workspace + Hermes SOUL | `.solvent/workspace/` · `SOUL.md`, `BRAIN.md`, `AGENTS.md` · [docs/WORKSPACE.md](docs/WORKSPACE.md) |
| Welcome banner with active model | Hermes TUI | Colored banner in `onboarding.py` matching `run_demo.py` |

## SOLVENT-specific choices

- **Terminal-first** — no web UI; hackathon judges run `python3 run_demo.py` in one terminal.
- **Offline default** — matches “zero install, zero keys” README promise.
- **Stripe optional** — toggle test mode; still requires `STRIPE_API_KEY` in env for live test links.
- **CLI overrides config** — `-i` / `--interactive` wins over saved `interaction_mode` for power users.
