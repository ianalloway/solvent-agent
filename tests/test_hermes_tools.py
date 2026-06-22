import json
import unittest

from solvent.hermes_tools import ToolRegistry, DISCLOSURE_THRESHOLD


class TestHermesTools(unittest.TestCase):
    def _executor(self, name: str, args: dict) -> str:
        return json.dumps({"tool": name, "args": args})

    def test_direct_mode_small_catalog(self):
        tools = {f"t{i}": {"description": f"tool {i}", "category": "x", "parameters": {}} for i in range(5)}
        reg = ToolRegistry(tools, self._executor)
        visible = reg.visible_tools()
        self.assertNotIn("tool_search", visible)
        self.assertEqual(len(visible), 5)

    def test_bridge_mode_large_catalog(self):
        tools = {
            f"t{i}": {"description": f"tool {i}", "category": "x", "parameters": {}}
            for i in range(DISCLOSURE_THRESHOLD + 2)
        }
        reg = ToolRegistry(tools, self._executor)
        self.assertIn("tool_search", reg.visible_tools())
        found = json.loads(reg.dispatch("tool_search", {"query": "t1"}))
        self.assertTrue(any(x["name"] == "t1" for x in found))

    def test_tool_call_dispatch(self):
        reg = ToolRegistry(
            {"echo": {"description": "echo", "category": "test", "parameters": {"x": "string"}}},
            self._executor,
        )
        out = json.loads(reg.dispatch("echo", {"x": "hi"}))
        self.assertEqual(out["tool"], "echo")
