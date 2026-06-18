import unittest

from solvent.nemotron import parse_tool_calls


class TestNemotronParse(unittest.TestCase):
    def test_hermes_format(self):
        text = '<tool_call>{"name": "web_search", "arguments": {"query": "ai"}}</tool_call>'
        calls = parse_tool_calls(text)
        self.assertEqual(calls, [("web_search", {"query": "ai"})])

    def test_legacy_format(self):
        text = 'TOOL_CALL: market_data {"symbol": "NVDA"}'
        calls = parse_tool_calls(text)
        self.assertEqual(calls, [("market_data", {"symbol": "NVDA"})])
