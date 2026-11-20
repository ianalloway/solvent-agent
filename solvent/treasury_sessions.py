"""Treasury storage for chat sessions, Telegram pairing and OpenClaw tokens.

Mixed into :class:`solvent.treasury.Treasury`; methods use its ``lock`` and
``_conn``. Import ``Treasury``, not these classes.
"""

from __future__ import annotations

import json
import time
import uuid


class _SessionsMixin:
    # ---- chat sessions (gateway / Hermes memory) -----------------------
    def get_or_create_chat_session(
        self, channel: str, external_id: str, user_label: str | None = None
    ) -> dict:
        sid = f"{channel}:{external_id}"
        with self.lock():
            with self._conn() as conn:
                with conn:
                    row = conn.execute(
                        "SELECT * FROM chat_sessions WHERE channel = ? AND external_id = ?",
                        (channel, external_id),
                    ).fetchone()
                    ts = time.time()
                    if row:
                        return dict(row)
                    conn.execute(
                        """
                        INSERT INTO chat_sessions
                        (id, channel, external_id, user_label, paired, created_at, updated_at)
                        VALUES (?, ?, ?, ?, 0, ?, ?)
                    """,
                        (sid, channel, external_id, user_label, ts, ts),
                    )
                    return {
                        "id": sid,
                        "channel": channel,
                        "external_id": external_id,
                        "user_label": user_label,
                        "paired": 0,
                        "created_at": ts,
                        "updated_at": ts,
                    }

    def update_chat_session(self, session_id: str, **kwargs) -> None:
        with self.lock(), self._conn() as conn, conn:
            fields = ["updated_at = ?"]
            params: list = [time.time()]
            for col in (
                "user_label",
                "paired",
                "pending_job_json",
                "notify_job_id",
            ):
                if col in kwargs:
                    val = kwargs[col]
                    if col == "pending_job_json" and isinstance(val, dict):
                        val = json.dumps(val)
                    fields.append(f"{col} = ?")
                    params.append(val)
            params.append(session_id)
            conn.execute(
                f"UPDATE chat_sessions SET {', '.join(fields)} WHERE id = ?",
                params,
            )

    def get_chat_session(self, session_id: str) -> dict | None:
        with self.lock(), self._conn() as conn:
            row = conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
            return dict(row) if row else None

    def list_chat_sessions_by_channel(self, channel: str) -> list[dict]:
        with self.lock():
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM chat_sessions WHERE channel = ? ORDER BY updated_at DESC",
                    (channel,),
                ).fetchall()
                return [dict(r) for r in rows]

    def add_chat_message(self, session_id: str, role: str, content: str) -> None:
        with self.lock():
            with self._conn() as conn:
                with conn:
                    conn.execute(
                        "INSERT INTO chat_messages (id, session_id, role, content, ts) VALUES (?, ?, ?, ?, ?)",
                        (
                            "cm_" + uuid.uuid4().hex[:12],
                            session_id,
                            role,
                            content,
                            time.time(),
                        ),
                    )

    def get_chat_messages(self, session_id: str, limit: int = 20) -> list[dict]:
        with self.lock(), self._conn() as conn:
            rows = conn.execute(
                "SELECT role, content, ts FROM chat_messages WHERE session_id = ? "
                "ORDER BY ts DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def create_pairing_code(
        self, user_id: str, username: str | None = None, ttl: float = 3600
    ) -> str:
        code = "TG-" + uuid.uuid4().hex[:6].upper()
        now = time.time()
        with self.lock():
            with self._conn() as conn:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO telegram_pairing (code, user_id, username, expires_at, approved, created_at)
                        VALUES (?, ?, ?, ?, 0, ?)
                    """,
                        (code, user_id, username, now + ttl, now),
                    )
        return code

    def approve_pairing(self, code: str) -> dict | None:
        with self.lock(), self._conn() as conn, conn:
            row = conn.execute(
                "SELECT * FROM telegram_pairing WHERE code = ?", (code.upper(),)
            ).fetchone()
            if not row:
                return None
            if row["expires_at"] < time.time():
                return None
            conn.execute(
                "UPDATE telegram_pairing SET approved = 1 WHERE code = ?",
                (code.upper(),),
            )
            conn.execute(
                "UPDATE chat_sessions SET paired = 1, updated_at = ? "
                "WHERE channel = 'telegram' AND external_id = ?",
                (time.time(), row["user_id"]),
            )
            return dict(row)

    def list_pending_pairings(self) -> list[dict]:
        now = time.time()
        with self.lock():
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM telegram_pairing WHERE approved = 0 AND expires_at > ? ORDER BY created_at",
                    (now,),
                ).fetchall()
                return [dict(r) for r in rows]

    def is_user_paired(self, user_id: str) -> bool:
        with self.lock():
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT paired FROM chat_sessions WHERE channel = 'telegram' AND external_id = ?",
                    (user_id,),
                ).fetchone()
                if row and row["paired"]:
                    return True
                prow = conn.execute(
                    "SELECT approved FROM telegram_pairing WHERE user_id = ? AND approved = 1",
                    (user_id,),
                ).fetchone()
                return bool(prow)

    def create_openclaw_token(self, ttl: float = 600) -> str:
        # 128 bits of entropy (32 hex) — short 32-bit OC-XXXXXXXX tokens were
        # guessable on the public /api/pair/verify endpoint within the TTL.
        token = "OC-" + uuid.uuid4().hex.upper()
        now = time.time()
        with self.lock():
            with self._conn() as conn:
                with conn:
                    conn.execute(
                        "INSERT INTO openclaw_tokens (token, expires_at, used, created_at) VALUES (?, ?, 0, ?)",
                        (token, now + ttl, now),
                    )
        return token

    def verify_openclaw_token(self, token: str) -> bool:
        """Return True and mark used if the token is valid, unused and unexpired.

        The check and the single-use mark are one UPDATE, so two concurrent (or
        cross-process) redemptions of the same token cannot both succeed.
        """
        now = time.time()
        with self.lock(), self._conn() as conn:
            with conn:
                cur = conn.execute(
                    "UPDATE openclaw_tokens SET used = 1 "
                    "WHERE token = ? AND used = 0 AND expires_at >= ?",
                    (token, now),
                )
            return cur.rowcount == 1
