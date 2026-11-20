# SOLVENT and OpenClaw on a Hetzner VPS

One Debian or Ubuntu VPS runs:

- **SOLVENT** `serve` and `worker` under systemd, listening on `127.0.0.1:8787`;
- **Caddy** in front of it at `https://agent.example.com`. Stripe webhooks,
  checkout redirects and customers' brief links need a public HTTPS URL;
- the **OpenClaw Gateway** in Docker, loopback-only on port 18789, which your
  phone reaches over Tailscale. SOLVENT plugs into it as a skill and pushes
  notifications to it ([OPENCLAW.md](OPENCLAW.md));
- a daily **backup** of the treasury, config and delivered briefs.

Replace `agent.example.com` with a DNS name that points at the VPS. Run the
commands as root.

## 1. Firewall

In a Hetzner Cloud Firewall, allow inbound TCP 22 from your admin network, and
TCP 80 and 443 from anywhere (Caddy's certificate challenge and HTTPS). Never
open 8787 or 18789.

## 2. Install SOLVENT

```bash
apt-get update && apt-get install -y python3-venv
useradd --system --create-home --home-dir /var/lib/solvent --shell /usr/sbin/nologin solvent
python3 -m venv /opt/solvent
/opt/solvent/bin/pip install "solvent-agent[all]"
```

## 3. Configure it

```bash
install -d -m 750 -o root -g solvent /etc/solvent
(umask 027; cat > /etc/solvent/solvent.env <<EOF
SOLVENT_HOME=/var/lib/solvent
SOLVENT_BASE_URL=https://agent.example.com
SOLVENT_LOCAL_ACCESS=never
SOLVENT_DELIVERY_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
SOLVENT_DASHBOARD_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
# STRIPE_API_KEY=sk_test_...
# STRIPE_WEBHOOK_SECRET=whsec_...
EOF
)
chgrp solvent /etc/solvent/solvent.env
```

`SOLVENT_LOCAL_ACCESS=never` matters behind any proxy (see
[PRODUCTION.md](PRODUCTION.md), *Running behind a reverse proxy*). Uncomment the
Stripe lines to take real test-mode payments; without them, payments are
simulated. Only test keys are accepted.

The environment applies because `SOLVENT_HOME` starts empty. If a saved
`.solvent/config.json` ever appears there (the onboarding wizard writes one), it
wins over the environment for `serve` and `worker`: its default `base_url` is
`http://127.0.0.1:8787`, and `stripe_test_mode: false` forces simulated payments.
`solvent doctor` flags a `base_url` mismatch. To fix it, run `solvent config set
base_url https://agent.example.com` (and `stripe_test_mode true`) as the
`solvent` user, with the environment from step 6 loaded.

A new home starts with zero recorded capital. Record your operating capital once
(this books $200; see *Safe restarts* in [PRODUCTION.md](PRODUCTION.md)):

```bash
sudo -u solvent SOLVENT_HOME=/var/lib/solvent /opt/solvent/bin/python -c \
  'from solvent.treasury import Treasury; Treasury().seed(20_000)'
```

## 4. Run it under systemd

`/etc/systemd/system/solvent-serve.service`:

```ini
[Unit]
Description=SOLVENT API, webhooks and dashboard
After=network-online.target
Wants=network-online.target

[Service]
User=solvent
Group=solvent
EnvironmentFile=/etc/solvent/solvent.env
WorkingDirectory=/var/lib/solvent
ExecStart=/opt/solvent/bin/python -m solvent serve --host 127.0.0.1 --port 8787
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/solvent

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/solvent-worker.service` is the same, except for these two
lines:

```ini
Description=SOLVENT job worker
ExecStart=/opt/solvent/bin/python -m solvent worker
```

```bash
systemctl daemon-reload
systemctl enable --now solvent-serve solvent-worker
```

## 5. Put Caddy in front

Install Caddy (`apt-get install -y caddy` on current Debian and Ubuntu, or see
[caddyserver.com/docs/install](https://caddyserver.com/docs/install)), then set
`/etc/caddy/Caddyfile` to:

```
agent.example.com {
	reverse_proxy 127.0.0.1:8787
}
```

```bash
systemctl reload caddy
```

For Stripe, add the webhook endpoint `https://agent.example.com/webhooks/stripe`
as described in *Stripe setup* in [PRODUCTION.md](PRODUCTION.md).

## 6. Check it

```bash
curl -fsS https://agent.example.com/health
sudo -u solvent bash -c 'set -a; . /etc/solvent/solvent.env; set +a; /opt/solvent/bin/solvent doctor'
```

`base_url` and `public_exposure` should both be `[OK]`. Other checks may fail
until optional pieces are configured, such as `NVIDIA_API_KEY` or Telegram. The
dashboard is at `https://agent.example.com/`; sign in with the dashboard token
from `/etc/solvent/solvent.env`.

## 7. Add the OpenClaw Gateway

Set up the Gateway on the same VPS with
[OpenClaw's Hetzner guide](https://docs.openclaw.ai/install/hetzner), which runs
it in Docker. Keep port 18789 loopback-only, as that guide says. Then plug
SOLVENT in, from root's home where that setup keeps `~/.openclaw`:

```bash
SOLVENT_BASE_URL=https://agent.example.com /opt/solvent/bin/solvent connect --install-skill
cat >> /root/.openclaw/.env <<EOF
SOLVENT_URL=https://agent.example.com
SOLVENT_DASHBOARD_TOKEN=$(grep '^SOLVENT_DASHBOARD_TOKEN=' /etc/solvent/solvent.env | cut -d= -f2-)
EOF
chown --reference=/root/.openclaw/openclaw.json /root/.openclaw/.env
chmod 600 /root/.openclaw/.env
```

Use the public https URL even though both run on one VPS: inside the Gateway's
container, `127.0.0.1` is the container. The `chown` gives the file the same
owner as the Gateway's own config, so the container user can read it. Restart
the Gateway (`docker compose restart` in the OpenClaw checkout), then run
`docker compose run --rm openclaw-cli skills check`. If it reports `curl`
missing, add it to the image as OpenClaw's Docker guide describes.

## 8. Pair your phone

Pair the OpenClaw app with the Gateway, not with SOLVENT. Install Tailscale on
the VPS and the phone, give the Gateway a tailnet-only HTTPS URL
([stable HTTPS URL](https://docs.openclaw.ai/gateway/stable-https-url)), and
scan a setup code from the Control UI
([iOS guide](https://docs.openclaw.ai/platforms/ios)). Then ask your agent "ask
SOLVENT for its status".

## 9. Get notified

So SOLVENT can tell your phone about payments, deliveries, refunds and health
alerts ([OPENCLAW.md](OPENCLAW.md), *Get notified*), turn on the Gateway's hooks
and give SOLVENT the hook token. Run these from the OpenClaw checkout:

```bash
HOOK_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
docker compose run --rm openclaw-cli config set hooks \
  '{"enabled": true, "token": "'"$HOOK_TOKEN"'", "path": "/hooks", "allowedAgentIds": ["main"]}' \
  --strict-json --merge
docker compose run --rm openclaw-cli config validate && docker compose restart
cat >> /etc/solvent/solvent.env <<EOF
SOLVENT_OPENCLAW_HOOKS_URL=http://127.0.0.1:18789/hooks
SOLVENT_OPENCLAW_HOOKS_TOKEN=$HOOK_TOKEN
EOF
systemctl restart solvent-serve solvent-worker
sudo -u solvent bash -c 'set -a; . /etc/solvent/solvent.env; set +a; /opt/solvent/bin/solvent notify --test'
```

Here SOLVENT runs on the host, so `127.0.0.1:18789` reaches the Gateway through
its loopback port mapping, and the hook never leaves the machine.

## 10. Back it up

The treasury is your books. `solvent backup` snapshots it, the webhook log, the
saved config and policies, the agent workspace and the delivered briefs into
`/var/lib/solvent/backups`, consistently even while `serve` and `worker` write.
Archives are owner-only, and the newest 14 are kept.

`/etc/systemd/system/solvent-backup.service`:

```ini
[Unit]
Description=SOLVENT backup

[Service]
Type=oneshot
User=solvent
Group=solvent
EnvironmentFile=/etc/solvent/solvent.env
ExecStart=/opt/solvent/bin/python -m solvent backup --keep 14
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/solvent
```

`/etc/systemd/system/solvent-backup.timer`:

```ini
[Unit]
Description=Daily SOLVENT backup

[Timer]
OnCalendar=daily
RandomizedDelaySec=15m
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl daemon-reload
systemctl enable --now solvent-backup.timer
```

A backup on the same disk does not survive losing the server. Copy
`/var/lib/solvent/backups` somewhere else as well, for example to a Hetzner
Storage Box with rsync or borg over SSH.

To restore, verify the archive, unpack it into a new directory, and swap it in
with the services stopped:

```bash
A=/var/lib/solvent/backups/solvent-backup-YYYYMMDDTHHMMSSZ.tar.gz
/opt/solvent/bin/solvent backup --verify "$A"
systemctl stop solvent-serve solvent-worker
/opt/solvent/bin/solvent backup --restore "$A" --into /var/lib/solvent.restored
chown -R solvent:solvent /var/lib/solvent.restored
mv /var/lib/solvent /var/lib/solvent.old && mv /var/lib/solvent.restored /var/lib/solvent
systemctl start solvent-serve solvent-worker
```

Payments that arrived after the backup are not in it. Once the services are
back, run `solvent reconcile` against Stripe (see [PRODUCTION.md](PRODUCTION.md)).
