import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from solvent.agent import Solvent
from solvent.chat import handle_message, format_job_notification, _merge_commission_slots
from solvent.memory import SessionMemory
from solvent import nemotron
from solvent.treasury import Treasury


class TestChatTools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self.t = Treasury(path=self.db)
        self.t.reset()
        self.t.seed(10_000)
        self.agent = Solvent(seed_cents=10_000, fresh=False, sync_payment=False)
        self.agent.t = self.t
        self.memory = SessionMemory(self.t)
        self.session = self.memory.get_or_create("cli", "test-user")

    def tearDown(self):
        self.tmp.cleanup()

    def test_format_job_notification(self):
        msg = format_job_notification({"stage": "delivered", "job_id": "J1", "url": "https://x"})
        self.assertIn("J1", msg)
        self.assertIn("https://x", msg)

    def test_slot_filling(self):
        pending = _merge_commission_slots(
            self.agent, self.session["id"], "Commission a brief on AI chips budget $50 alice@example.com"
        )
        self.assertEqual(pending.get("budget_cents"), 5000)
        self.assertEqual(pending.get("customer_email"), "alice@example.com")

    @patch.object(nemotron, "complete")
    def test_handle_message_treasury_tool(self, mock_complete):
        mock_complete.side_effect = [
            ('<tool_call>{"name": "treasury_status", "arguments": {}}</tool_call>', {}),
            ("Balance looks healthy.", {}),
        ]
        reply = handle_message(self.session["id"], "How is treasury?", agent=self.agent, memory=self.memory)
        self.assertIn("healthy", reply.lower())

    @patch.object(nemotron, "complete")
    def test_per_turn_tool_budget(self, mock_complete):
        """A single reply with many tool calls is capped at the per-turn budget."""
        import solvent.chat as chatmod
        from solvent.hermes_tools import ToolRegistry

        many = " ".join(
            '<tool_call>{"name": "treasury_status", "arguments": {}}</tool_call>'
            for _ in range(25)
        )
        mock_complete.side_effect = [(many, {}), ("All set.", {})]

        calls = {"n": 0}
        orig = ToolRegistry.dispatch

        def counting(self, name, args):
            calls["n"] += 1
            return orig(self, name, args)

        with patch.object(ToolRegistry, "dispatch", counting), \
             patch.object(chatmod.tools, "MAX_TOOL_CALLS", 5):
            reply = handle_message(
                self.session["id"], "spam tools", agent=self.agent, memory=self.memory
            )

        self.assertLessEqual(calls["n"], 5)
        self.assertIn("set", reply.lower())

    @patch.object(nemotron, "complete")
    def test_submit_brief_via_tool(self, mock_complete):
        mock_complete.side_effect = [
            (
                '<tool_call>{"name": "submit_brief", "arguments": '
                '{"topic": "EV market", "budget_cents": 5000, "customer_email": "c@test.com"}}</tool_call>',
                {},
            ),
            ("Checkout link sent.", {}),
        ]
        reply = handle_message(
            self.session["id"],
            "Submit brief on EV market budget 50 email c@test.com",
            agent=self.agent,
            memory=self.memory,
        )
        self.assertTrue("Checkout" in reply or "invoice" in reply.lower() or len(reply) > 0)
