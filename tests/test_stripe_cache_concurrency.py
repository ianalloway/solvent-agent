"""Verified payment storage must survive independent server/worker clients."""

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from solvent.paths import config_path
from solvent.stripe_client import StripeClient


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)


def payment(job, ref="pi_first"):
    return {"paid": True, "job_id": job, "stripe_ref": ref, "amount_cents": 5000}


def test_already_cached_payment_refreshes_after_another_client_corrects_it():
    worker, webhooks = StripeClient(), StripeClient()
    webhooks._cache_webhook_payment("job:order", payment("order"))
    assert worker.get_cached_payment("order") == payment("order")
    webhooks._cache_webhook_payment("job:order", payment("order", "pi_corrected"))
    assert worker.get_cached_payment("order") == payment("order", "pi_corrected")


def test_unrelated_write_never_overwrites_a_newer_snapshot():
    stale, webhooks = StripeClient(), StripeClient()
    stale._cache_webhook_payment("job:order", payment("order"))
    webhooks._cache_webhook_payment("job:order", payment("order", "pi_corrected"))
    stale._cache_webhook_payment("job:other", payment("other"))
    assert StripeClient().get_cached_payment("order") == payment("order", "pi_corrected")


def test_removal_is_observed_and_not_resurrected():
    worker = StripeClient()
    worker._cache_webhook_payment("job:order", payment("order"))
    config_path("stripe_payments.json").write_text("{}", encoding="utf-8")
    assert worker.get_cached_payment("order") is None
    worker._cache_webhook_payment("job:other", payment("other"))
    assert StripeClient().get_cached_payment("order") is None


def test_parallel_thread_writers_retain_every_payment():
    clients = [StripeClient() for _ in range(16)]
    barrier = threading.Barrier(len(clients))

    def write(index):
        barrier.wait(timeout=5)
        clients[index]._cache_webhook_payment(f"job:{index}", payment(str(index)))

    with ThreadPoolExecutor(max_workers=len(clients)) as executor:
        list(executor.map(write, range(len(clients))))
    saved = json.loads(config_path("stripe_payments.json").read_text())
    assert set(saved) == {f"job:{index}" for index in range(len(clients))}


def test_parallel_process_writers_retain_every_payment(tmp_path):
    ready = tmp_path / "start"
    script = """
import sys, time
from pathlib import Path
from solvent.stripe_client import StripeClient
client = StripeClient()
while not Path(sys.argv[1]).exists():
    time.sleep(0.01)
job = sys.argv[2]
client._cache_webhook_payment('job:' + job, {'paid': True, 'job_id': job})
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(ready), str(index)],
            env=dict(os.environ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(8)
    ]
    try:
        ready.touch()
        for process in processes:
            _, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stderr
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    saved = json.loads(config_path("stripe_payments.json").read_text())
    assert set(saved) == {f"job:{index}" for index in range(len(processes))}


@pytest.mark.parametrize("bad", ['{"partial":', "[]", '{"job:bad": null}'])
def test_damaged_storage_is_not_overwritten(bad):
    client = StripeClient()
    path = config_path("stripe_payments.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError):
        client._cache_webhook_payment("job:order", payment("order"))
    assert path.read_text() == bad


def test_failed_replace_does_not_publish_unpersisted_payment(monkeypatch):
    client = StripeClient()
    client._cache_webhook_payment("job:order", payment("order"))

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("solvent.stripe_client.os.replace", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        client._cache_webhook_payment("job:order", payment("order", "pi_unpersisted"))
    assert client.get_cached_payment("order") == payment("order")
    assert not list(config_path("stripe_payments.json").parent.glob("*.tmp"))


def test_invalid_cache_entries_are_not_trusted_on_startup():
    path = config_path("stripe_payments.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"job:order": null}', encoding="utf-8")
    assert StripeClient().get_cached_payment("order") is None
