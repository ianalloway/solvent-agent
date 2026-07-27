"""OpenClaw-style pairing for Telegram DMs."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from .treasury import Treasury

ALLOWLIST_PATH = Path(".solvent/telegram_allowlist.json")


def _load_allowlist() -> set[str]:
    if not ALLOWLIST_PATH.is_file():
        return set()
    with contextlib.suppress(json.JSONDecodeError, OSError):
        data = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(x) for x in data}
    return set()


def dm_policy() -> str:
    return os.environ.get("SOLVENT_TELEGRAM_DM_POLICY", "pairing").strip().lower()


def is_allowed(user_id: str, username: str | None = None) -> bool:
    policy = dm_policy()
    if policy == "open":
        return True
    if policy == "disabled":
        return False
    allow = _load_allowlist()
    cfg_allow = os.environ.get("SOLVENT_TELEGRAM_ALLOW_FROM", "")
    if cfg_allow:
        allow.update(x.strip() for x in cfg_allow.split(",") if x.strip())
    if policy == "allowlist":
        return user_id in allow or (username and username.lstrip("@") in allow)
    # pairing: must be paired in DB
    t = Treasury()
    return t.is_user_paired(user_id)


def request_pairing(user_id: str, username: str | None = None) -> str:
    t = Treasury()
    t.get_or_create_chat_session("telegram", user_id, username)
    return t.create_pairing_code(user_id, username)


def approve(code: str) -> dict | None:
    t = Treasury()
    row = t.approve_pairing(code)
    if not row:
        return None
    allow = _load_allowlist()
    allow.add(str(row["user_id"]))
    ALLOWLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    ALLOWLIST_PATH.write_text(
        json.dumps(sorted(allow), indent=2) + "\n", encoding="utf-8"
    )
    return row


def list_pending() -> list[dict]:
    return Treasury().list_pending_pairings()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="SOLVENT Telegram pairing")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list pending pairing codes")
    ap = sub.add_parser("approve", help="approve a pairing code")
    ap.add_argument("code", help="pairing code e.g. TG-ABC123")
    args = parser.parse_args()
    if args.cmd == "list":
        pending = list_pending()
        if not pending:
            print("No pending pairings.")
            return
        for p in pending:
            print(f"{p['code']}  user={p['user_id']}  @{p.get('username') or '?'}")
    elif args.cmd == "approve":
        row = approve(args.code)
        if row:
            print(f"Approved {row['user_id']} (@{row.get('username')})")
        else:
            print("Invalid or expired code.")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
