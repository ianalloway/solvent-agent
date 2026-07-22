"""Unit tests for the thread-safe SSE event hub.

EventHub fans out sync-published events to async SSE subscribers. These tests
exercise the pure wiring (subscribe / publish / unsubscribe / sse_format)
without a running event loop: when no loop is bound, ``publish`` falls back to
``Queue.put_nowait`` so messages land synchronously.
"""

import asyncio
import json

from solvent.event_hub import EventHub


def test_subscribe_returns_queue() -> None:
    hub = EventHub()
    q = hub.subscribe()
    assert isinstance(q, asyncio.Queue)
    hub.unsubscribe(q)


def test_publish_delivers_to_subscriber_without_loop() -> None:
    hub = EventHub()
    q = hub.subscribe()
    hub.publish("tick", {"n": 1})
    assert q.qsize() == 1
    msg = q.get_nowait()
    assert msg["type"] == "tick"
    assert msg["n"] == 1
    hub.unsubscribe(q)


def test_publish_merges_payload_into_message() -> None:
    hub = EventHub()
    q = hub.subscribe()
    hub.publish("job", {"job_id": "j1", "stage": "fulfill"})
    msg = q.get_nowait()
    assert msg["type"] == "job"
    assert msg["job_id"] == "j1"
    assert msg["stage"] == "fulfill"
    hub.unsubscribe(q)


def test_publish_fanout_to_multiple_subscribers() -> None:
    hub = EventHub()
    q1 = hub.subscribe()
    q2 = hub.subscribe()
    hub.publish("event", {"x": "y"})
    assert q1.get_nowait()["type"] == "event"
    assert q2.get_nowait()["type"] == "event"
    hub.unsubscribe(q1)
    hub.unsubscribe(q2)


def test_unsubscribe_stops_delivery() -> None:
    hub = EventHub()
    q = hub.subscribe()
    hub.unsubscribe(q)
    hub.publish("gone", {})
    # No subscriber remains, so nothing is queued onto the detached queue.
    assert q.qsize() == 0


def test_sse_format() -> None:
    out = EventHub.sse_format({"a": 1})
    assert out.startswith("data: ")
    assert out.endswith("\n\n")
    payload = json.loads(out[len("data: ") :])
    assert payload == {"a": 1}
