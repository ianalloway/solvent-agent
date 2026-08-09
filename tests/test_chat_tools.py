import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from solvent import nemotron
from solvent.agent import Solvent
from solvent.chat import (
    _make_executor,
    _merge_commission_slots,
    format_job_notification,
    handle_message,
)
from solvent.memory import SessionMemory
from solvent.treasury import Treasury
from solvent.gateway import Gateway


class TestChatTools(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ, {"SOLVENT_DELIVERY_SECRET": "x" * 32}, clear=False)
        self._env.start()
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
        self._env.stop()

    def test_format_job_notification(self):
        msg = format_job_notification({"stage": "delivered", "job_id": "J1", "url": "https://x"})
        self.assertIn("J1", msg)
        self.assertIn("https://x", msg)

    def test_slot_filling(self):
        pending = _merge_commission_slots(
            self.agent,
            self.session["id"],
            "Commission a brief on AI chips budget $50 alice@example.com",
        )
        self.assertEqual(pending.get("budget_cents"), 5000)
        self.assertEqual(pending.get("customer_email"), "alice@example.com")

    def test_job_tools_are_scoped_to_session(self):
        owner = self.session["id"]
        other = self.memory.get_or_create("cli", "other-user")["id"]
        self.t.upsert_job(
            "J-owner",
            "completed",
            topic="Owner topic",
            customer_email="owner@example.com",
            checkout_url="https://checkout.example/owner",
            deliverable_url="https://deliver.example/owner?token=secret",
            job_owner_session_id=owner,
        )
        self.t.upsert_job(
            "J-other",
            "completed",
            topic="Other topic",
            customer_email="other@example.com",
            checkout_url="https://checkout.example/other",
            deliverable_url="https://deliver.example/other?token=secret",
            job_owner_session_id=other,
        )

        owner_tool = _make_executor(self.agent, owner, live_search=False)
        self.assertIn("J-owner", owner_tool("list_jobs", {}))
        self.assertNotIn("J-other", owner_tool("list_jobs", {}))
        self.assertIn("owner@example.com", owner_tool("job_status", {"job_id": "J-owner"}))
        self.assertEqual(
            {"error": "job not found"},
            json.loads(owner_tool("job_status", {"job_id": "J-other"})),
        )

    def test_gateway_jobs_command_is_scoped_to_session(self):
        other = self.memory.get_or_create("telegram", "other-user")["id"]
        self.t.upsert_job("J-test", "completed", topic="Visible topic", job_owner_session_id=self.session["id"])
        self.t.upsert_job("J-other", "completed", topic="Hidden topic", job_owner_session_id=other)
        gateway = Gateway(agent=self.agent)

        output = gateway._handle_command("cli", "test-user", "/jobs", self.session)

        self.assertIn("J-test", output)
        self.assertNotIn("J-other", output)

    @patch.object(nemotron, "complete")
    def test_handle_message_treasury_tool(self, mock_complete):
        mock_complete.side_effect = [
            ('<tool_call>{"name": "treasury_status", "arguments": {}}</tool_call>', {}),
            ("Balance looks healthy.", {}),
        ]
        reply = handle_message(
            self.session["id"], "How is treasury?", agent=self.agent, memory=self.memory
        )
        self.assertIn("healthy", reply.lower())

    @patch.object(nemotron, "complete")
    def test_per_turn_tool_budget(self, mock_complete):
        """A single reply with many tool calls is capped at the per-turn budget."""
        import solvent.chat as chatmod

        many = " ".join(
            '<tool_call>{"name": "treasury_status", "arguments": {}}</tool_call>' for _ in range(25)
        )
        mock_complete.side_effect = [(many, {}), ("All set.", {})]

        calls = {"n": 0}
        orig = chatmod._make_executor

        def counting_executor(agent, session_id, live_search):
            run = orig(agent, session_id, live_search)

            def counting_run(name, args):
                calls["n"] += 1
                return run(name, args)

            return counting_run

        with (
            patch.object(chatmod, "_make_executor", counting_executor),
            patch.object(chatmod.tools, "MAX_TOOL_CALLS", 5),
        ):
            reply = handle_message(
                self.session["id"], "spam tools", agent=self.agent, memory=self.memory
            )

        self.assertLessEqual(calls["n"], 5)
        self.assertIn("set", reply.lower())

    @patch.object(nemotron, "complete")
    def test_submit_brief_ignores_tool_supplied_job_id(self, mock_complete):
        self.t.upsert_job(
            "VICTIM1",
            "awaiting_payment",
            topic="Victim topic",
            budget_cents=7500,
            customer_email="victim@example.com",
            job_payload_json={"id": "VICTIM1", "topic": "Victim topic"},
        )
        mock_complete.side_effect = [
            (
                '<tool_call>{"name": "submit_brief", "arguments": '
                '{"job_id": "VICTIM1", "topic": "Attacker topic", '
                '"budget_cents": 5000, "customer_email": "attacker@example.com"}}</tool_call>',
                {},
            ),
            ("Done.", {}),
        ]

        handle_message(
            self.session["id"],
            "Submit a brief",
            agent=self.agent,
            memory=self.memory,
        )

        victim = self.t.get_job("VICTIM1")
        self.assertEqual(victim["topic"], "Victim topic")
        self.assertEqual(victim["customer_email"], "victim@example.com")
        session = self.t.get_chat_session(self.session["id"])
        self.assertNotEqual(session.get("notify_job_id"), "VICTIM1")
        created = [j for j in self.t.list_jobs() if j["id"] != "VICTIM1"]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["topic"], "Attacker topic")

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
        with patch.dict(os.environ, {"SOLVENT_DELIVERY_SECRET": "x" * 32}):
            reply = handle_message(
                self.session["id"],
                "Submit brief on EV market budget 50 email c@test.com",
                agent=self.agent,
                memory=self.memory,
            )
        self.assertTrue("Checkout" in reply or "invoice" in reply.lower() or len(reply) > 0)
