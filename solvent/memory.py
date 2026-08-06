"""Hermes-style session memory backed by SQLite."""

from __future__ import annotations

from .treasury import Treasury


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
