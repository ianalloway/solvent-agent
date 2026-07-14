"""Tests for solvent/memory.py — Hermes-style session memory."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from solvent.memory import SessionMemory
from solvent.treasury import Treasury


def _tmp_memory():
    d = tempfile.mkdtemp()
    t = Treasury(path=Path(d) / "test.db")
    return SessionMemory(treasury=t)


class TestSessionMemoryGetOrCreate(unittest.TestCase):
    def setUp(self):
        self.mem = _tmp_memory()

    def test_creates_new_session(self):
        session = self.mem.get_or_create("telegram", "user123")
        self.assertIsNotNone(session)
        self.assertIn("id", session)

    def test_returns_same_session_on_second_call(self):
        s1 = self.mem.get_or_create("telegram", "user123")
        s2 = self.mem.get_or_create("telegram", "user123")
        self.assertEqual(s1["id"], s2["id"])

    def test_different_channels_create_different_sessions(self):
        s1 = self.mem.get_or_create("telegram", "user123")
        s2 = self.mem.get_or_create("slack", "user123")
        self.assertNotEqual(s1["id"], s2["id"])

    def test_different_users_create_different_sessions(self):
        s1 = self.mem.get_or_create("telegram", "user_a")
        s2 = self.mem.get_or_create("telegram", "user_b")
        self.assertNotEqual(s1["id"], s2["id"])

    def test_user_label_stored(self):
        session = self.mem.get_or_create("telegram", "user123", user_label="Alice")
        self.assertEqual(session.get("user_label"), "Alice")

    def test_session_has_channel(self):
        session = self.mem.get_or_create("telegram", "user123")
        self.assertEqual(session.get("channel"), "telegram")

    def test_session_has_external_id(self):
        session = self.mem.get_or_create("telegram", "user123")
        self.assertEqual(session.get("external_id"), "user123")


class TestSessionMemoryAppend(unittest.TestCase):
    def setUp(self):
        self.mem = _tmp_memory()
        self.session = self.mem.get_or_create("telegram", "user123")
        self.sid = self.session["id"]

    def test_append_user_message(self):
        self.mem.append(self.sid, "user", "Hello!")
        history = self.mem.history(self.sid)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "Hello!")

    def test_append_assistant_message(self):
        self.mem.append(self.sid, "assistant", "Hi there!")
        history = self.mem.history(self.sid)
        self.assertEqual(history[0]["role"], "assistant")

    def test_messages_ordered_chronologically(self):
        self.mem.append(self.sid, "user", "First")
        self.mem.append(self.sid, "assistant", "Second")
        history = self.mem.history(self.sid)
        self.assertEqual(history[0]["content"], "First")
        self.assertEqual(history[1]["content"], "Second")

    def test_append_multiple_messages(self):
        for i in range(5):
            self.mem.append(self.sid, "user", f"msg {i}")
        history = self.mem.history(self.sid)
        self.assertEqual(len(history), 5)


class TestSessionMemoryHistory(unittest.TestCase):
    def setUp(self):
        self.mem = _tmp_memory()
        self.session = self.mem.get_or_create("telegram", "user123")
        self.sid = self.session["id"]

    def test_empty_history(self):
        self.assertEqual(self.mem.history(self.sid), [])

    def test_limit_respected(self):
        for i in range(10):
            self.mem.append(self.sid, "user", f"msg {i}")
        history = self.mem.history(self.sid, limit=3)
        self.assertEqual(len(history), 3)

    def test_history_returns_dicts_with_role_and_content(self):
        self.mem.append(self.sid, "user", "Hello")
        for msg in self.mem.history(self.sid):
            self.assertIn("role", msg)
            self.assertIn("content", msg)


class TestSessionMemoryFormatForPrompt(unittest.TestCase):
    def setUp(self):
        self.mem = _tmp_memory()
        self.session = self.mem.get_or_create("telegram", "user123")
        self.sid = self.session["id"]

    def test_empty_history_returns_empty_string(self):
        result = self.mem.format_for_prompt(self.sid)
        self.assertEqual(result, "")

    def test_formats_messages_with_role_prefix(self):
        self.mem.append(self.sid, "user", "Hello!")
        self.mem.append(self.sid, "assistant", "Hi!")
        result = self.mem.format_for_prompt(self.sid)
        self.assertIn("USER:", result)
        self.assertIn("ASSISTANT:", result)
        self.assertIn("Hello!", result)
        self.assertIn("Hi!", result)

    def test_format_is_newline_separated(self):
        self.mem.append(self.sid, "user", "One")
        self.mem.append(self.sid, "user", "Two")
        result = self.mem.format_for_prompt(self.sid)
        lines = result.split("\n")
        self.assertGreaterEqual(len(lines), 2)

    def test_limit_applied_to_format(self):
        for i in range(10):
            self.mem.append(self.sid, "user", f"msg {i}")
        full = self.mem.format_for_prompt(self.sid, limit=20)
        limited = self.mem.format_for_prompt(self.sid, limit=2)
        self.assertGreater(len(full), len(limited))


if __name__ == "__main__":
    unittest.main()
