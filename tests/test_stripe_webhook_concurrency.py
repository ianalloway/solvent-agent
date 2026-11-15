"""Concurrent Stripe redelivery must book a checkout exactly once.

Stripe retries webhooks and can deliver the same event (or a second event for
the same session) while the first delivery is still being processed. These
drive ``/webhooks/stripe`` from several threads at once.
"""

import threading

from tests import test_stripe_webhook_booking as booking
from tests.test_stripe_webhook_booking import _event, _open_checkout, _post, _revenue

client = booking.client

THREADS = 8


def _fire(client, payloads):
    barrier = threading.Barrier(len(payloads))
    codes = []

    def send(payload):
        barrier.wait()
        try:
            codes.append(_post(client, payload).status_code)
        except Exception as exc:  # a server error must fail the test, not vanish
            codes.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=send, args=(p,)) for p in payloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return codes


def test_simultaneous_redelivery_of_one_event_books_once(client):
    price = _open_checkout(client, "race-same")
    payload = _event("evt_race_same", "race-same", price)

    codes = _fire(client, [payload] * THREADS)

    assert len(codes) == THREADS and all(code == 200 for code in codes), codes
    assert _revenue(client, "race-same") == [price]


def test_simultaneous_distinct_events_for_one_session_book_once(client):
    # Two different event ids for the same paid session (e.g. completed and
    # async_payment_succeeded) racing each other.
    price = _open_checkout(client, "race-pair")
    payloads = [_event(f"evt_race_pair_{i}", "race-pair", price) for i in range(THREADS)]

    codes = _fire(client, payloads)

    assert len(codes) == THREADS and all(code == 200 for code in codes), codes
    assert _revenue(client, "race-pair") == [price]


def test_webhook_log_survives_concurrent_writers():
    """The log's one shared SQLite connection must not interleave transactions."""
    from solvent.webhook_log import WebhookLog

    log = WebhookLog(":memory:")
    errors = []
    barrier = threading.Barrier(THREADS)

    def write(worker):
        barrier.wait()
        try:
            for i in range(50):
                event_id = f"evt_{worker}_{i}"
                log.record(event_id, "checkout.session.completed", b"{}", "received", verified=True)
                log.mark_processed(event_id)
                log.get_status(event_id)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=write, args=(w,)) for w in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert errors == []
    assert log.stats()["processed"] == THREADS * 50
