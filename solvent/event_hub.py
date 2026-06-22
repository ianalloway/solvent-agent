"""Thread-safe event hub for dashboard SSE broadcasts."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any


class EventHub:
    """Fan-out hub: sync publishers (agent/worker) → async SSE subscribers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: list[asyncio.Queue] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            if q in self._queues:
                self._queues.remove(q)

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        message = {"type": event_type, **(payload or {})}
        with self._lock:
            targets = list(self._queues)
            loop = self._loop
        for q in targets:
            try:
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(q.put_nowait, message)
                else:
                    q.put_nowait(message)
            except asyncio.QueueFull:
                pass

    @staticmethod
    def sse_format(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"
