"""Hermes-style session memory backed by SQLite."""

from __future__ import annotations

import json

from .treasury import Treasury


def telegram_session_key(user_id: str, chat_id: str, chat_type: str, *, operator: bool) -> str:
    """Versioned scope avoids reusing histories populated with private memory.

    Include trust so revoking operator access cannot expose an old private reply.
    Pairing and rate limiting still use the authenticated user ID, not this key.
    """
    return "v2:" + json.dumps(
        ["operator" if operator else "customer", chat_type, chat_id, user_id],
        separators=(",", ":"),
    )


def telegram_chat_destination(external_id: str) -> str:
    """Resolve a scoped session's reply destination; retain legacy DM delivery."""
    if external_id.startswith("v2:"):
        _role, _kind, chat_id, _user_id = json.loads(external_id[3:])
        return chat_id
    return external_id


class SessionMemory:
    def __init__(self, treasury: Treasury | None = None):
        self.t = treasury or Treasury()

    def get_or_create(self, channel: str, external_id: str, user_label: str | None = None) -> dict:
        return self.t.get_or_create_chat_session(channel, external_id, user_label)

    def append(self, session_id: str, role: str, content: str) -> None:
        self.t.add_chat_message(session_id, role, content)

    def history(self, session_id: str, limit: int = 20) -> list[dict]:
        return self.t.get_chat_messages(session_id, limit=limit)

    def format_for_prompt(self, session_id: str, limit: int = 20) -> str:
        lines = []
        for msg in self.history(session_id, limit=limit):
            role = msg["role"].upper()
            lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)
