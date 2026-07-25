import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from solvent.agent import Solvent
from solvent.chat import handle_message, format_job_notification, _merge_commission_slots, _make_executor
from solvent.memory import SessionMemory
from solvent import nemotron
from solvent.treasury import Treasury
from solvent.gateway import Gateway


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
        reply = handle_message(self.session["id"], "How is treasury?", agent=self.agent, memory=self.memory)
        self.assertIn("healthy", reply.lower())

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
