import unittest
from unittest.mock import patch

from solvent import nemotron
from solvent.nemotron import parse_tool_calls


class TestNemotronParse(unittest.TestCase):
    # --- formats the original parser already handled -----------------------
    def test_hermes_format(self):
        text = '<tool_call>{"name": "web_search", "arguments": {"query": "ai"}}</tool_call>'
        self.assertEqual(parse_tool_calls(text), [("web_search", {"query": "ai"})])

    def test_legacy_format(self):
        text = 'TOOL_CALL: market_data {"symbol": "NVDA"}'
        self.assertEqual(parse_tool_calls(text), [("market_data", {"symbol": "NVDA"})])

    # --- robustness gaps the rewrite closes --------------------------------
    def test_legacy_nested_arguments(self):
        """Nested objects must not be truncated by naive non-greedy matching."""
        text = 'TOOL_CALL: submit_brief {"topic": "x", "meta": {"a": 1}}'
        self.assertEqual(
            parse_tool_calls(text),
            [("submit_brief", {"topic": "x", "meta": {"a": 1}})],
        )

    def test_hermes_nested_arguments(self):
        text = '<tool_call>{"name": "submit_brief", "arguments": {"topic": "x", "opts": {"deep": true}}}</tool_call>'
        self.assertEqual(
            parse_tool_calls(text),
            [("submit_brief", {"topic": "x", "opts": {"deep": True}})],
        )

    def test_fenced_json_block(self):
        text = '```json\n{"name": "web_search", "arguments": {"query": "ai"}}\n```'
        self.assertEqual(parse_tool_calls(text), [("web_search", {"query": "ai"})])

    def test_openai_parameters_key(self):
        text = '{"name": "web_search", "parameters": {"query": "ai"}}'
        self.assertEqual(parse_tool_calls(text), [("web_search", {"query": "ai"})])

    def test_openai_function_envelope(self):
        text = '{"type": "function", "function": {"name": "market_data", "arguments": "{\\"symbol\\": \\"NVDA\\"}"}}'
        self.assertEqual(parse_tool_calls(text), [("market_data", {"symbol": "NVDA"})])

    def test_arguments_as_json_string(self):
        """Models often emit arguments as a JSON-encoded string, not an object."""
        text = '<tool_call>{"name": "web_search", "arguments": "{\\"query\\": \\"ai\\"}"}</tool_call>'
        got = parse_tool_calls(text)
        self.assertEqual(got, [("web_search", {"query": "ai"})])
        # critical: args must be a dict so downstream args.get(...) is safe
        self.assertIsInstance(got[0][1], dict)

    def test_multiple_calls_in_order(self):
        text = (
            '<tool_call>{"name": "web_search", "arguments": {"query": "a"}}</tool_call>'
            " then "
            '<tool_call>{"name": "market_data", "arguments": {"symbol": "NVDA"}}</tool_call>'
        )
        self.assertEqual(
            parse_tool_calls(text),
            [("web_search", {"query": "a"}), ("market_data", {"symbol": "NVDA"})],
        )

    def test_tool_calls_envelope(self):
        text = (
            '{"tool_calls": ['
            '{"name": "web_search", "arguments": {"query": "a"}},'
            '{"name": "summarize", "arguments": {"text": "t"}}]}'
        )
        self.assertEqual(
            parse_tool_calls(text),
            [("web_search", {"query": "a"}), ("summarize", {"text": "t"})],
        )

    def test_fenced_block_as_whole_message(self):
        text = '```json\n{"name": "market_data", "arguments": {"symbol": "NVDA"}}\n```'
        self.assertEqual(parse_tool_calls(text), [("market_data", {"symbol": "NVDA"})])

    # --- things that must NOT be mistaken for calls ------------------------
    def test_prose_without_calls(self):
        self.assertEqual(parse_tool_calls("# Brief\n\nSome prose. {not a call}"), [])

    def test_bare_data_object_is_not_a_call(self):
        """A JSON object lacking a name/arguments shape is not a tool call."""
        self.assertEqual(parse_tool_calls('Data: {"price": 100, "vol": 5}'), [])

    def test_json_embedded_in_brief_is_not_a_call(self):
        """Regression: a brief that *contains* call-shaped JSON must not parse.

        The bare-JSON fallback only fires when the whole message is one JSON
        object; JSON illustrated inside prose stays prose.
        """
        brief = (
            "## Example config\n"
            'Use this snippet: {"name": "deploy", "arguments": {"x": 1}} in your file.\n'
            "## Recommendation\nShip it."
        )
        self.assertEqual(parse_tool_calls(brief), [])

    def test_explicit_marker_suppresses_bare_fallback(self):
        """A real <tool_call> plus stray JSON elsewhere yields only the real call."""
        text = (
            '<tool_call>{"name": "web_search", "arguments": {"query": "ai"}}</tool_call>\n'
            'For reference: {"name": "noise", "arguments": {"y": 2}}'
        )
        self.assertEqual(parse_tool_calls(text), [("web_search", {"query": "ai"})])

    def test_empty_and_none(self):
        self.assertEqual(parse_tool_calls(""), [])
        self.assertEqual(parse_tool_calls("no json here at all"), [])

    def test_malformed_json_is_skipped(self):
        self.assertEqual(
            parse_tool_calls('<tool_call>{"name": "x", arguments:}</tool_call>'), []
        )


class TestLiveRetry(unittest.TestCase):
    def test_retries_transient_then_succeeds(self):
        import urllib.error

        calls = {"n": 0}

        def flaky(system, user):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.URLError("connection reset")
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }

        with (
            patch.object(nemotron, "_live_request", side_effect=flaky),
            patch.object(nemotron, "MAX_RETRIES", 3),
            patch.object(nemotron.time, "sleep", lambda *_: None),
        ):
            text, usage = nemotron._live_complete("sys", "usr")

        self.assertEqual(text, "ok")
        self.assertEqual(calls["n"], 3)
        self.assertFalse(usage["estimated"])

    def test_permanent_error_not_retried(self):
        import urllib.error

        calls = {"n": 0}

        def bad_key(system, user):
            calls["n"] += 1
            raise urllib.error.HTTPError(
                nemotron.ENDPOINT, 401, "Unauthorized", {}, None
            )

        with (
            patch.object(nemotron, "_live_request", side_effect=bad_key),
            patch.object(nemotron, "MAX_RETRIES", 3),
            patch.object(nemotron.time, "sleep", lambda *_: None),
        ):
            with self.assertRaises(urllib.error.HTTPError):
                nemotron._live_complete("sys", "usr")

        self.assertEqual(calls["n"], 1)  # not retried


if __name__ == "__main__":
    unittest.main()
