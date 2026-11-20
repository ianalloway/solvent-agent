# Using SOLVENT from OpenClaw

[OpenClaw](https://docs.openclaw.ai) is a self-hosted agent whose phone apps
connect to an **OpenClaw Gateway** over WebSocket. SOLVENT is not a Gateway,
so the app cannot connect to SOLVENT directly. Instead, SOLVENT plugs into your
Gateway as a **skill**. You can then ask your OpenClaw agent, from the phone or
any channel it listens on, to quote, order and track briefs and to read
SOLVENT's business reports.

```
OpenClaw app ──WebSocket──▶ OpenClaw Gateway :18789 ──agent runs curl──▶ SOLVENT
  (phone)                   skills/solvent/SKILL.md                   :8787 or its https URL
```

For running both on one server, see [HETZNER.md](HETZNER.md).

## 1. Install the skill

On the Gateway host:

```bash
python3 -m solvent connect --install-skill
```

This writes `~/.openclaw/workspace/skills/solvent/SKILL.md`, or the same path
under `$OPENCLAW_WORKSPACE_DIR`; `--skills-dir` picks another folder. OpenClaw's
Docker setup mounts the host's `~/.openclaw/workspace` into the container, so
this works for a Docker Gateway too. Re-run it after upgrading SOLVENT. A skill
you have edited is kept unless you add `--force`. `python3 -m solvent connect`
on its own shows what is installed and what the Gateway still needs (`--json`
for scripts).

## 2. Give the Gateway SOLVENT's URL and token

| Variable | Value |
|---|---|
| `SOLVENT_URL` | Where the Gateway reaches SOLVENT. Required. |
| `SOLVENT_DASHBOARD_TOKEN` | SOLVENT's dashboard token. Needed for quotes, `/status` and reports; ordering and tracking work without it. |

The simplest place is `~/.openclaw/.env` on the Gateway host, which OpenClaw
reads as a fallback environment (Docker setups mount it too):

```bash
SOLVENT_URL=https://agent.example.com
SOLVENT_DASHBOARD_TOKEN=...
```

Or scope them to the skill in `~/.openclaw/openclaw.json`. The skill declares the
token as its primary env, so `apiKey` can take any OpenClaw secret ref:

```json5
{
  skills: {
    entries: {
      solvent: {
        env: { SOLVENT_URL: "https://agent.example.com" },
        apiKey: { source: "env", provider: "default", id: "SOLVENT_DASHBOARD_TOKEN" },
      },
    },
  },
}
```

`skills.entries` values reach host runs only. If your agent runs tools in a
Docker sandbox, pass both variables through `agents.defaults.sandbox.docker.env`
([OpenClaw skills config](https://docs.openclaw.ai/tools/skills-config)).

Which URL to use:

- **Gateway on the same host, not in Docker:** `http://127.0.0.1:8787` works.
- **Gateway in Docker** (OpenClaw's Hetzner guide), **or on another machine:**
  use SOLVENT's public https URL, the one Stripe and customers use. Inside a
  container, `127.0.0.1` is the container itself.

## 3. Check it

```bash
openclaw skills check     # Docker: docker compose run --rm openclaw-cli skills check
```

The skill needs `curl` on the Gateway's `PATH`. If `skills check` reports it
missing in a Docker image, bake it into the image: OpenClaw's Docker guide says
external binaries must be added at build time. Then start a new chat with
`/new` and try "ask SOLVENT for its status", or "get a SOLVENT quote for a
brief on lithium supply at $49".

## 4. Connect the phone to the Gateway

Pair the app with the Gateway by following
[OpenClaw's iOS guide](https://docs.openclaw.ai/platforms/ios). Its recommended
remote path is Tailscale Serve ([stable HTTPS URL](https://docs.openclaw.ai/gateway/stable-https-url)).
Then open the Control UI, go to **Nodes → Devices → Pair device → Create setup
code**, and scan the code in the app under **Settings → Gateway**.

Every field on the app's connect screen describes the OpenClaw Gateway, not
SOLVENT:

| App field | What it is |
|---|---|
| Host, Port, Use TLS | The Gateway's address (default port 18789). A setup code fills these in. |
| Discovery Domain | OpenClaw's wide-area DNS-SD domain (for example `openclaw.internal.`), for finding Gateways across a tailnet. |
| Gateway Auth Token, Gateway Password | The Gateway's own auth (`gateway.auth` in `openclaw.json`). |

SOLVENT's own pairing tokens (`/pair qr`), `SOLVENT_GATEWAY_PASSWORD` and
`/.well-known/solvent.json` are unrelated to these fields.

## 5. Get notified

The skill lets you ask SOLVENT things; pushes let SOLVENT tell you. SOLVENT
posts job events and health-alert changes to the Gateway's wake hook, which
drops them into the agent's main session: the chat the app shows.

Turn on the Gateway's hooks with a dedicated long random token, not the
Gateway's own auth token ([OpenClaw inbound webhooks](https://docs.openclaw.ai/automation/cron-jobs/webhooks)):

```bash
HOOK_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
openclaw config set hooks '{"enabled": true, "token": "'"$HOOK_TOKEN"'", "path": "/hooks", "allowedAgentIds": ["main"]}' --strict-json --merge
openclaw config validate && openclaw gateway restart
```

Then give SOLVENT's `serve` and `worker` these variables:

| Variable | Value |
|---|---|
| `SOLVENT_OPENCLAW_HOOKS_URL` | The Gateway's hooks base, e.g. `http://127.0.0.1:18789/hooks`. Plain http is accepted only to a loopback address; use https otherwise. |
| `SOLVENT_OPENCLAW_HOOKS_TOKEN` | The `hooks.token` above. |
| `SOLVENT_OPENCLAW_AGENT_ID` | Optional: the agent to notify (`main`), if the Gateway cannot pick one. |
| `SOLVENT_OPENCLAW_NOTIFY` | Optional comma list of what to push. Default: `paid, delivered, failed, refunded, refund_pending, spend_held, delivery_review_required, alerts`. Empty pushes nothing. |
| `SOLVENT_OPENCLAW_ALERTS_INTERVAL` | Seconds between health sweeps; default `900`, minimum `60`. |

`solvent notify --test` sends a test message, and `solvent notify` shows the
settings and the outbox. `solvent doctor` checks them too (`openclaw_push`).

What gets pushed:

- **Jobs:** paid, delivered, refunded, or failed after payment, plus anything
  waiting on you: a refund to review, a vendor spend held for approval, or a
  delivery to review. Each is pushed once per job.
- **Health:** the `solvent alerts` checks, swept by the worker. A check is
  pushed when it turns warn or critical, when it escalates and when it clears.
  A problem that persists is not repeated. `solvent alerts --notify openclaw`
  pushes a sweep's problems immediately, for cron.

Messages carry only what SOLVENT controls (job ids, amounts and alert
summaries), never a customer's topic or email: OpenClaw treats wake text as a
trusted system event. Pushes wait in an outbox in the treasury database and the
worker sends them, backing off while the Gateway is down. A push older than a
day is dropped.

## Security

- The dashboard token is full operator access to SOLVENT. Anyone who can
  instruct your OpenClaw agent, on any channel it listens on, can use the token
  through the skill. Keep untrusted channels away from the agent that has the
  skill: restrict it with an agent allowlist (`agents.entries.<id>.skills`).
  Or leave the token out, which allows ordering and tracking only.
- The skill tells the agent to send the token only to `SOLVENT_URL`, to keep
  user text inside quoted JSON bodies instead of the command line, and never to
  say a brief is paid before its job status does.

## Hermes Agent

The skill is a plain [AgentSkills](https://agentskills.io) `SKILL.md`, the
format Hermes Agent also loads. This is untested: install it with
`python3 -m solvent connect --install-skill --skills-dir ~/.hermes/skills` and
make `SOLVENT_URL` (and the token) available to the commands Hermes runs.
