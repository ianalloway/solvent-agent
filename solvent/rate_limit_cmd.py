"""
rate_limit_cmd.py — CLI for inspecting and managing per-user rate limits.

Usage:
    solvent rate-limit stats <user_key>           current counters + ban status
    solvent rate-limit check <user_key>           test if allowed (non-recording)
    solvent rate-limit ban <user_key> [opts]      impose a temporary ban
    solvent rate-limit unban <user_key>           lift an active ban
    solvent rate-limit list-banned                list all active bans
    solvent rate-limit cleanup                    purge records older than 24 h
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from typing import Optional


_DEFAULT_DB = ".solvent/rate_limits.db"


def _fmt_ts(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _limiter(db_path: str = _DEFAULT_DB):
    from .rate_limit import RateLimiter
    try:
        from .config import load_config
        cfg = load_config()
    except Exception:
        cfg = None

    kwargs: dict = {"db_path": db_path}
    if cfg:
        kwargs["burst_limit"] = cfg.rate_burst_limit
        kwargs["hourly_limit"] = cfg.rate_hourly_limit
        kwargs["daily_limit"] = cfg.rate_daily_limit
    return RateLimiter(**kwargs)


def _active_bans(rl) -> list[dict]:
    now = time.time()
    rows = rl._conn.execute(
        "SELECT user_key, expires_at, reason FROM rate_bans WHERE expires_at > ? ORDER BY expires_at",
        (now,),
    ).fetchall()
    return [{"user_key": r[0], "expires_at": r[1], "reason": r[2]} for r in rows]


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------

def cmd_stats(user_key: str, *, db_path: str = _DEFAULT_DB, as_json: bool = False) -> int:
    rl = _limiter(db_path)
    s = rl.stats(user_key)
    ban_str = (
        f"until {_fmt_ts(s['ban_expires'])} ({s.get('ban_reason', '')})"
        if s["is_banned"] and s["ban_expires"]
        else "no"
    )
    if as_json:
        print(json.dumps({**s, "user_key": user_key}, indent=2, default=str))
    else:
        print(f"Rate-limit stats for {user_key!r}")
        print(f"  burst   {s['burst_count']} / {rl.burst_limit}  (window {rl.burst_window}s)")
        print(f"  hourly  {s['hourly_count']} / {rl.hourly_limit}")
        print(f"  daily   {s['daily_count']} / {rl.daily_limit}")
        print(f"  banned  {ban_str}")
    return 0


def cmd_check(user_key: str, *, db_path: str = _DEFAULT_DB, as_json: bool = False) -> int:
    rl = _limiter(db_path)
    s = rl.stats(user_key)
    now = time.time()

    allowed = True
    reason = ""
    if s["is_banned"]:
        allowed = False
        remaining = max(0, int((s["ban_expires"] or now) - now))
        reason = f"Banned (expires in {remaining}s)"
    elif s["burst_count"] >= rl.burst_limit:
        allowed = False
        reason = f"Burst limit ({s['burst_count']}/{rl.burst_limit})"
    elif s["hourly_count"] >= rl.hourly_limit:
        allowed = False
        reason = f"Hourly limit ({s['hourly_count']}/{rl.hourly_limit})"
    elif s["daily_count"] >= rl.daily_limit:
        allowed = False
        reason = f"Daily limit ({s['daily_count']}/{rl.daily_limit})"

    if as_json:
        print(json.dumps({"user_key": user_key, "allowed": allowed, "reason": reason}))
    else:
        status = "ALLOWED" if allowed else f"BLOCKED — {reason}"
        print(f"check {user_key!r}: {status}")
    return 0 if allowed else 1


def cmd_ban(
    user_key: str,
    *,
    duration: int = 3600,
    reason: str = "",
    db_path: str = _DEFAULT_DB,
    as_json: bool = False,
) -> int:
    rl = _limiter(db_path)
    rl.ban(user_key, duration_seconds=duration, reason=reason)
    expires_at = time.time() + duration
    if as_json:
        print(json.dumps({"user_key": user_key, "banned": True, "expires_at": expires_at, "reason": reason}))
    else:
        print(f"Banned {user_key!r} for {duration}s (until {_fmt_ts(expires_at)}). Reason: {reason or '(none)'}")
    return 0


def cmd_unban(user_key: str, *, db_path: str = _DEFAULT_DB, as_json: bool = False) -> int:
    rl = _limiter(db_path)
    was_banned = rl.is_banned(user_key)
    rl.unban(user_key)
    if as_json:
        print(json.dumps({"user_key": user_key, "unbanned": True, "was_banned": was_banned}))
    else:
        if was_banned:
            print(f"Unbanned {user_key!r}.")
        else:
            print(f"{user_key!r} was not banned.")
    return 0


def cmd_list_banned(*, db_path: str = _DEFAULT_DB, as_json: bool = False) -> int:
    rl = _limiter(db_path)
    bans = _active_bans(rl)
    if as_json:
        print(json.dumps(bans, indent=2, default=str))
        return 0
    if not bans:
        print("(no active bans)")
        return 0
    print(f"{'User key':<32}  {'Expires':<20}  Reason")
    print("-" * 72)
    for b in bans:
        print(f"{b['user_key']:<32}  {_fmt_ts(b['expires_at']):<20}  {b['reason'] or ''}")
    return 0


def cmd_cleanup(*, db_path: str = _DEFAULT_DB, as_json: bool = False) -> int:
    rl = _limiter(db_path)
    before_events = rl._conn.execute("SELECT COUNT(*) FROM rate_events").fetchone()[0]
    before_bans = rl._conn.execute("SELECT COUNT(*) FROM rate_bans").fetchone()[0]
    rl.cleanup()
    after_events = rl._conn.execute("SELECT COUNT(*) FROM rate_events").fetchone()[0]
    after_bans = rl._conn.execute("SELECT COUNT(*) FROM rate_bans").fetchone()[0]
    removed_events = before_events - after_events
    removed_bans = before_bans - after_bans
    if as_json:
        print(json.dumps({"removed_events": removed_events, "removed_bans": removed_bans}))
    else:
        print(f"Cleanup complete: removed {removed_events} old event(s), {removed_bans} expired ban(s).")
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        prog="solvent rate-limit",
        description="Inspect and manage per-user rate limits.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--json", dest="as_json", action="store_true", help="output JSON")
    p.add_argument("--db", dest="db_path", default=_DEFAULT_DB, metavar="PATH",
                   help=f"rate-limit DB path (default: {_DEFAULT_DB})")

    sub = p.add_subparsers(dest="cmd", metavar="<command>")

    # stats
    sp = sub.add_parser("stats", help="show counters + ban status for a user")
    sp.add_argument("user_key", help="user identifier (e.g. telegram:12345)")

    # check
    sp = sub.add_parser("check", help="test if a user would be allowed (non-recording)")
    sp.add_argument("user_key")

    # ban
    sp = sub.add_parser("ban", help="impose a temporary ban")
    sp.add_argument("user_key")
    sp.add_argument("--duration", type=int, default=3600, metavar="SECONDS",
                    help="ban duration in seconds (default 3600)")
    sp.add_argument("--reason", default="", help="human-readable reason")

    # unban
    sp = sub.add_parser("unban", help="lift an active ban")
    sp.add_argument("user_key")

    # list-banned
    sub.add_parser("list-banned", help="list all active bans")

    # cleanup
    sub.add_parser("cleanup", help="purge rate events older than 24 h and expired bans")

    args = p.parse_args()
    kw = {"db_path": args.db_path, "as_json": args.as_json}

    if args.cmd == "stats":
        sys.exit(cmd_stats(args.user_key, **kw))
    elif args.cmd == "check":
        sys.exit(cmd_check(args.user_key, **kw))
    elif args.cmd == "ban":
        sys.exit(cmd_ban(args.user_key, duration=args.duration, reason=args.reason, **kw))
    elif args.cmd == "unban":
        sys.exit(cmd_unban(args.user_key, **kw))
    elif args.cmd == "list-banned":
        sys.exit(cmd_list_banned(**kw))
    elif args.cmd == "cleanup":
        sys.exit(cmd_cleanup(**kw))
    else:
        p.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
