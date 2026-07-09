"""
sessions_cmd.py — CLI for viewing SOLVENT chat session history.

Shows all chat sessions stored in the treasury DB, with optional filtering
by channel and the ability to print the message history for a single session.

Usage:
    solvent sessions                        list recent sessions (all channels)
    solvent sessions --channel telegram     filter by channel
    solvent sessions --show <session_id>    print message history
    solvent sessions --json                 output raw JSON
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Optional


def _fmt_ts(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M")


def _list_all_sessions(treasury, channel: Optional[str], limit: int) -> list[dict]:
    """Return sessions across all channels, or filtered to one channel."""
    with treasury.lock():
        with treasury._conn() as conn:
            if channel:
                rows = conn.execute(
                    "SELECT * FROM chat_sessions WHERE channel = ? "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (channel, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM chat_sessions ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
    return [dict(r) for r in rows]


def show_sessions(
    treasury=None,
    *,
    n: int = 20,
    channel: Optional[str] = None,
    show_id: Optional[str] = None,
    as_json: bool = False,
) -> None:
    if treasury is None:
        from .treasury import Treasury
        treasury = Treasury()

    # Show single session with full message history
    if show_id:
        session = treasury.get_chat_session(show_id)
        if not session:
            print(f"(session {show_id!r} not found)")
            return
        messages = treasury.get_chat_messages(show_id, limit=200)
        if as_json:
            print(json.dumps({"session": session, "messages": messages}, indent=2, default=str))
            return
        label = session.get("user_label") or session.get("external_id", "?")
        print(f"Session {show_id}  [{session['channel']}]  {label}")
        print(f"Created {_fmt_ts(session['created_at'])}  "
              f"Updated {_fmt_ts(session['updated_at'])}\n")
        if not messages:
            print("  (no messages)")
        for msg in messages:
            role = msg["role"].upper()
            ts = _fmt_ts(msg["ts"])
            content = (msg["content"] or "")[:120]
            if len(msg.get("content", "")) > 120:
                content += "…"
            print(f"  {ts}  {role:<9} {content}")
        return

    sessions = _list_all_sessions(treasury, channel, limit=n)

    if not sessions:
        print("(no chat sessions)")
        return

    if as_json:
        print(json.dumps(sessions, indent=2, default=str))
        return

    print(f"  {'ID':<32}  {'Channel':<10}  {'User':<20}  {'Updated':<16}  Msgs")
    print("  " + "-" * 88)
    for s in sessions:
        sid = s.get("id", "")[:32]
        ch = (s.get("channel") or "")[:10]
        user = (s.get("user_label") or s.get("external_id") or "")[:20]
        updated = _fmt_ts(s.get("updated_at", 0))
        # Count messages efficiently
        with treasury.lock():
            with treasury._conn() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?", (s["id"],)
                ).fetchone()[0]
        print(f"  {sid:<32}  {ch:<10}  {user:<20}  {updated:<16}  {count}")


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        description="View SOLVENT chat session history.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-n", "--lines", type=int, default=20, metavar="N",
                   help="number of sessions to show (default 20)")
    p.add_argument("--channel", metavar="CHANNEL",
                   help="filter by channel (telegram, web, …)")
    p.add_argument("--show", metavar="SESSION_ID", dest="show_id",
                   help="print full message history for a session")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="output raw JSON")
    args = p.parse_args()

    show_sessions(
        n=args.lines,
        channel=args.channel,
        show_id=args.show_id,
        as_json=args.as_json,
    )


if __name__ == "__main__":
    main()
