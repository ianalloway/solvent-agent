"""Hermes-style progressive tool disclosure bridge."""

from __future__ import annotations

import json
from typing import Callable

BRIDGE_TOOLS = frozenset({"tool_search", "tool_describe", "tool_call"})
DISCLOSURE_THRESHOLD = 10


class ToolRegistry:
    """Session-scoped tool catalog with optional bridge mode."""

    def __init__(
        self,
        tools: dict[str, dict],
        executor: Callable[[str, dict], str],
        *,
        force_bridge: bool = False,
    ):
        self._all = tools
        self._executor = executor
        self._allowed = set(tools.keys())
        self._use_bridge = force_bridge or len(tools) > DISCLOSURE_THRESHOLD

    def visible_tools(self) -> list[str]:
        if self._use_bridge:
            return sorted(BRIDGE_TOOLS)
        return sorted(self._allowed)

    def describe_catalog(self) -> str:
        if not self._use_bridge:
            lines = []
            for name, meta in self._all.items():
                lines.append(f"- {name}: {meta.get('description', '')}")
            return "\n".join(lines)
        return (
            "Tools are accessed via tool_search, tool_describe, tool_call. "
            f"Catalog size: {len(self._all)} tools."
        )

    def tool_search(self, query: str) -> str:
        q = (query or "").lower()
        matches = []
        for name, meta in self._all.items():
            hay = f"{name} {meta.get('description', '')} {meta.get('category', '')}".lower()
            if not q or q in hay:
                matches.append({"name": name, "description": meta.get("description", "")})
        return json.dumps(matches[:15], indent=2)

    def tool_describe(self, name: str) -> str:
        if name in BRIDGE_TOOLS:
            schemas = {
                "tool_search": {"query": "string"},
                "tool_describe": {"name": "string"},
                "tool_call": {"name": "string", "arguments": "object"},
            }
            return json.dumps({"name": name, "parameters": schemas[name]})
        meta = self._all.get(name)
        if not meta:
            return json.dumps({"error": f"unknown tool {name!r}"})
        return json.dumps({
            "name": name,
            "description": meta.get("description", ""),
            "parameters": meta.get("parameters", {}),
            "category": meta.get("category", ""),
        })

    def tool_call(self, name: str, arguments: dict | None = None) -> str:
        arguments = arguments or {}
        if name in BRIDGE_TOOLS:
            if name == "tool_search":
                return self.tool_search(arguments.get("query", ""))
            if name == "tool_describe":
                return self.tool_describe(arguments.get("name", ""))
            if name == "tool_call":
                return self.tool_call(
                    arguments.get("name", ""),
                    arguments.get("arguments") or {},
                )
        if name not in self._allowed:
            return json.dumps({"error": f"tool {name!r} not in session allowlist"})
        try:
            return self._executor(name, arguments)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def dispatch(self, name: str, arguments: dict) -> str:
        if self._use_bridge and name not in BRIDGE_TOOLS:
            return self.tool_call(name, arguments)
        if name in BRIDGE_TOOLS:
            return self.tool_call(name, arguments)
        return self.tool_call(name, arguments)
