"""Tests for solvent/event_hub.py."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from solvent.event_hub import EventHub


# ---------------------------------------------------------------------------
# subscribe / unsubscribe
# ---------------------------------------------------------------------------

def test_subscribe_returns_queue():
    hub = EventHub()
    q = hub.subscribe()
    assert isinstance(q, asyncio.Queue)
    assert len(hub._queues) == 1


def test_unsubscribe_removes_queue():
    hub = EventHub()
    q = hub.subscribe()
    hub.unsubscribe(q)
    assert q not in hub._queues


def test_unsubscribe_unknown_queue_is_noop():
    hub = EventHub()
    q = asyncio.Queue()
    hub.unsubscribe(q)  # should not raise


def test_multiple_subscribers():
    hub = EventHub()
    q1 = hub.subscribe()
    q2 = hub.subscribe()
    assert len(hub._queues) == 2
    hub.unsubscribe(q1)
    assert len(hub._queues) == 1
    assert q2 in hub._queues


# ---------------------------------------------------------------------------
# publish (no event loop — put_nowait path)
# ---------------------------------------------------------------------------

def test_publish_delivers_to_subscriber():
    hub = EventHub()
    q = hub.subscribe()
    hub.publish("test_event", {"key": "value"})
    msg = q.get_nowait()
    assert msg["type"] == "test_event"
    assert msg["key"] == "value"


def test_publish_fans_out_to_all_subscribers():
    hub = EventHub()
    q1 = hub.subscribe()
    q2 = hub.subscribe()
    hub.publish("ping")
    assert q1.get_nowait()["type"] == "ping"
    assert q2.get_nowait()["type"] == "ping"


def test_publish_no_subscribers_is_noop():
    hub = EventHub()
    hub.publish("orphan")  # should not raise


def test_publish_none_payload():
    hub = EventHub()
    q = hub.subscribe()
    hub.publish("empty")
    msg = q.get_nowait()
    assert msg == {"type": "empty"}


def test_publish_queue_full_drops_message():
    hub = EventHub()
    q = asyncio.Queue(maxsize=1)
    with hub._lock:
        hub._queues.append(q)
    hub.publish("first")
    hub.publish("second")  # should not raise even when queue is full
    assert q.qsize() == 1
    assert q.get_nowait()["type"] == "first"


def test_publish_after_unsubscribe_not_delivered():
    hub = EventHub()
    q = hub.subscribe()
    hub.unsubscribe(q)
    hub.publish("ghost")
    assert q.empty()


# ---------------------------------------------------------------------------
# publish with running event loop (call_soon_threadsafe path)
# ---------------------------------------------------------------------------

def test_publish_via_loop_threadsafe():
    hub = EventHub()
    results: list[dict] = []

    async def _runner():
        q = hub.subscribe()
        hub.bind_loop(asyncio.get_event_loop())

        def _publish():
            hub.publish("async_event", {"x": 1})

        t = threading.Thread(target=_publish)
        t.start()
        t.join()
        await asyncio.sleep(0)  # let call_soon_threadsafe callbacks drain
        results.append(q.get_nowait())

    asyncio.run(_runner())
    assert results[0]["type"] == "async_event"
    assert results[0]["x"] == 1


# ---------------------------------------------------------------------------
# sse_format
# ---------------------------------------------------------------------------

def test_sse_format():
    data = {"type": "tick", "balance": 100}
    result = EventHub.sse_format(data)
    assert result.startswith("data: ")
    assert result.endswith("\n\n")
    parsed = json.loads(result[len("data: "):].strip())
    assert parsed == data


def test_sse_format_is_valid_json():
    hub = EventHub()
    q = hub.subscribe()
    hub.publish("metric", {"cents": 9999})
    msg = q.get_nowait()
    sse = EventHub.sse_format(msg)
    parsed = json.loads(sse[len("data: "):].strip())
    assert parsed["cents"] == 9999
