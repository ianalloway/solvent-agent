"""Tests for the research_brief plan -> act -> synthesize loop."""

import unittest
from unittest.mock import patch

from solvent import nemotron


def _tool(name="web_search", query="q"):
    return f'<tool_call>{{"name":"{name}","arguments":{{"query":"{query}"}}}}</tool_call>'


class TestResearchBrief(unittest.TestCase):
    def setUp(self):
        self._prev_offline = nemotron._force_offline
        self.addCleanup(setattr, nemotron, "_force_offline", self._prev_offline)

    def test_offline_stub_returns_on_first_round(self):
        """No mocking: the deterministic stub yields a brief immediately."""
        nemotron.configure(model="offline")
        text, usage, ctx = nemotron.research_brief("NVIDIA datacenter demand")
        self.assertIn("##", text)
        self.assertEqual(ctx.total_calls, 0)

    @patch.object(nemotron, "complete")
    def test_runs_tool_then_synthesizes(self, mock_complete):
        mock_complete.side_effect = [
            (_tool(), {"total_tokens": 5}),
            ("## Brief\n\nFindings.", {"total_tokens": 10}),
        ]
        text, usage, ctx = nemotron.research_brief("AI chips")
        self.assertIn("## Brief", text)
        self.assertEqual(ctx.web_search_calls, 1)
        self.assertFalse(ctx.budget_exhausted)
        self.assertEqual(mock_complete.call_count, 2)
        self.assertEqual(usage["total_tokens"], 15)

    @patch.object(nemotron, "complete")
    def test_stops_when_budget_exhausted(self, mock_complete):
        three = " ".join(_tool(query=f"q{i}") for i in range(3))
        mock_complete.side_effect = [
            (three, {"total_tokens": 1}),
            ("## Final\n\nDone.", {"total_tokens": 1}),
        ]
        with patch.object(nemotron.tools, "MAX_TOOL_CALLS", 2):
            text, usage, ctx = nemotron.research_brief("topic")
        self.assertTrue(ctx.budget_exhausted)
        self.assertEqual(ctx.total_calls, 2)  # capped, not 3
        self.assertIn("Final", text)
        self.assertEqual(mock_complete.call_count, 2)  # one gather round + synth
        self.assertEqual(usage["total_tokens"], 2)

    @patch.object(nemotron, "complete")
    def test_counts_every_round_and_nested_summarize_call(self, mock_complete):
        summary_call = (
            '<tool_call>{"name":"summarize","arguments":{"text":"research notes"}}</tool_call>'
        )
        mock_complete.side_effect = [
            (
                summary_call,
                {
                    "prompt_tokens": 1200,
                    "completion_tokens": 800,
                    "total_tokens": 2000,
                    "estimated": False,
                },
            ),
            (
                "Summary",
                {
                    "prompt_tokens": 1300,
                    "completion_tokens": 700,
                    "total_tokens": 2000,
                    "estimated": True,
                },
            ),
            (
                "## Final\n\nDone.",
                {
                    "prompt_tokens": 1400,
                    "completion_tokens": 600,
                    "total_tokens": 2000,
                    "estimated": False,
                },
            ),
        ]

        text, usage, ctx = nemotron.research_brief("AI chips")

        self.assertIn("Final", text)
        self.assertEqual(ctx.total_calls, 1)
        self.assertEqual(mock_complete.call_count, 3)
        self.assertEqual(usage["prompt_tokens"], 3900)
        self.assertEqual(usage["completion_tokens"], 2100)
        self.assertEqual(usage["total_tokens"], 6000)
        self.assertTrue(usage["estimated"])


if __name__ == "__main__":
    unittest.main()
