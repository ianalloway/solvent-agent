"""Tests for bounded research tools."""

import unittest

from solvent.tools import ALLOWED_TOOLS, MAX_TOOL_CALLS, ToolContext, dispatch


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
        self.assertFalse(ctx.budget_exhausted)
        with self.assertRaises(RuntimeError):
            dispatch("web_search", {"query": "one more"}, ctx, lambda s, u: ("", {}))
        # hitting the cap must be observable, not silent
        self.assertTrue(ctx.budget_exhausted)

    def test_allowed_set(self):
        self.assertIn("market_data", ALLOWED_TOOLS)

    def test_market_data_offline_is_stub(self):
        from solvent.tools import market_data

        out = market_data("NVDA", live=False)
        self.assertIn("offline stub", out)

    def test_market_data_live_falls_back_to_stub_on_error(self):
        """live=True must degrade gracefully to the stub if the fetch fails."""
        from unittest.mock import patch

        import solvent.tools as t

        with patch.object(t.urllib.request, "urlopen", side_effect=OSError("no net")):
            out = t.market_data("NVDA", live=True)
        self.assertIn("NVDA", out)
        self.assertIn("offline stub", out)


if __name__ == "__main__":
    unittest.main()
