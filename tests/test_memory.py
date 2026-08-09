"""Tests for solvent.memory.SessionMemory."""

from __future__ import annotations

import unittest

from solvent.memory import SessionMemory


class TestSessionMemory(unittest.TestCase):
    def setUp(self):
        self.mem = SessionMemory()

    def test_get_or_create_returns_session(self):
        sess = self.mem.get_or_create("telegram", "U-001", user_label="Ian")
        self.assertIn("id", sess)
        self.assertEqual(sess["channel"], "telegram")
        self.assertEqual(sess["external_id"], "U-001")

    def test_get_or_create_is_idempotent(self):
        first = self.mem.get_or_create("telegram", "U-002")
        second = self.mem.get_or_create("telegram", "U-002")
        self.assertEqual(first["id"], second["id"])

    def test_append_and_history_roundtrip(self):
        sess = self.mem.get_or_create("telegram", "U-003")
        sid = sess["id"]
        self.mem.append(sid, "user", "hello")
        self.mem.append(sid, "assistant", "hi there")
        history = self.mem.history(sid)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[1]["content"], "hi there")

    def test_history_limit_is_respected(self):
        sess = self.mem.get_or_create("telegram", "U-004")
        sid = sess["id"]
        for i in range(5):
            self.mem.append(sid, "user", f"m{i}")
        history = self.mem.history(sid, limit=3)
        self.assertEqual(len(history), 3)

    def test_format_for_prompt_uppercases_roles(self):
        sess = self.mem.get_or_create("telegram", "U-005")
        sid = sess["id"]
        self.mem.append(sid, "user", "what is 2+2?")
        self.mem.append(sid, "assistant", "4")
        prompt = self.mem.format_for_prompt(sid)
        self.assertIn("USER: what is 2+2?", prompt)
        self.assertIn("ASSISTANT: 4", prompt)


if __name__ == "__main__":
    unittest.main()
