"""OpenClaw app connection helpers: Connect-screen values, exposure checks, CLI."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from urllib.parse import urlsplit

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def parse_endpoint(base_url: str) -> tuple[str, int, bool]:
    """Split a base URL into (host, port, tls), defaulting the port from the scheme."""
    raw = (base_url or "").strip()
    if "://" not in raw:
        raw = "http://" + raw
    parts = urlsplit(raw)
    tls = parts.scheme == "https"
    host = parts.hostname or ""
    try:
        port = parts.port or (443 if tls else 80)
    except ValueError:
        port = 443 if tls else 80
    return host, port, tls


def is_loopback(host: str) -> bool:
    return host.lower() in _LOOPBACK


def exposure_warnings(env: Mapping[str, str] | None = None) -> list[str]:
    """Problems that matter once the server is reachable from the internet."""
    env = os.environ if env is None else env
    host, _port, tls = parse_endpoint(env.get("SOLVENT_BASE_URL", ""))
    if not host or is_loopback(host):
        return []
    warnings = []
    if not tls:
        warnings.append("SOLVENT_BASE_URL is plain http on a public host; terminate TLS")
    if not env.get("SOLVENT_GATEWAY_PASSWORD", "").strip():
        warnings.append("SOLVENT_GATEWAY_PASSWORD is unset; pairing is open to anyone")
    if not env.get("SOLVENT_DASHBOARD_TOKEN", "").strip():
        warnings.append("SOLVENT_DASHBOARD_TOKEN is unset; the dashboard API is unprotected")
    return warnings


def connect_info(env: Mapping[str, str] | None = None) -> dict:
    """The values the OpenClaw app's Connect screen asks for."""
    env = os.environ if env is None else env
    host, port, tls = parse_endpoint(env.get("SOLVENT_BASE_URL", ""))
    scheme = "https" if tls else "http"
    default_port = 443 if tls else 80
    netloc = host if port == default_port else f"{host}:{port}"
    return {
        "host": host,
        "port": port,
        "tls": tls,
        "discovery_url": f"{scheme}://{netloc}/.well-known/solvent.json",
        "gateway_password_set": bool(env.get("SOLVENT_GATEWAY_PASSWORD", "").strip()),
        "warnings": exposure_warnings(env),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="solvent connect",
        description="Show the OpenClaw app Connect values and mint a pairing QR.",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--no-token", action="store_true", help="do not mint a pairing token")
    parser.add_argument(
        "--revoke", action="store_true", help="revoke every outstanding pairing token and exit"
    )
    args = parser.parse_args()

    from .treasury import Treasury

    treasury = Treasury()
    if args.revoke:
        count = treasury.revoke_openclaw_tokens()
        print(f"Revoked {count} outstanding pairing token(s).")
        return

    info = connect_info()
    token = None if args.no_token else treasury.create_openclaw_token(ttl=600)
    if args.json:
        print(json.dumps({**info, "token": token}, indent=2))
        return

    print("OpenClaw → Connect")
    print(f"  Host:                 {info['host'] or '(set SOLVENT_BASE_URL)'}")
    print(f"  Port:                 {info['port']}")
    print(f"  Use TLS:              {'on' if info['tls'] else 'off'}")
    print(f"  Discovery Domain:     {info['discovery_url']}")
    pw = "set SOLVENT_GATEWAY_PASSWORD" if not info["gateway_password_set"] else "(your password)"
    print(f"  Gateway Password:     {pw}")
    for warning in info["warnings"]:
        print(f"  WARNING: {warning}")
    if token:
        from . import qr

        print()
        print(qr.render_token(token, host=info["host"], port=info["port"]))
        print("Token expires in 10 minutes and works once.")


if __name__ == "__main__":
    main()
