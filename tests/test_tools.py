"""Tests for bounded research tools."""

import unittest

from solvent.tools import ToolContext, dispatch, MAX_TOOL_CALLS, ALLOWED_TOOLS


class TestTools(unittest.TestCase):
    def test_allowlist(self):
        ctx = ToolContext()
        result = dispatch("web_search", {"query": "AI chips"}, ctx, lambda s, u: ("ok", {}))
        self.assertIn("offline web_search", result)
        self.assertEqual(ctx.web_search_calls, 1)

    def test_rejects_unknown_tool(self):
        ctx = ToolContext()
        with self.assertRaises(ValueError):
            dispatch("hack_the_planet", {}, ctx, lambda s, u: ("", {}))

    def test_max_calls(self):
        ctx = ToolContext()
        for i in range(MAX_TOOL_CALLS):
            dispatch("web_search", {"query": f"q{i}"}, ctx, lambda s, u: ("", {}))
        with self.assertRaises(RuntimeError):
            dispatch("web_search", {"query": "one more"}, ctx, lambda s, u: ("", {}))

    def test_allowed_set(self):
        self.assertIn("market_data", ALLOWED_TOOLS)


if __name__ == "__main__":
    unittest.main()
