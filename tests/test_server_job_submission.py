"""Regression tests for the hosted job-submission event path."""

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    pytest.skip("FastAPI test client is not installed", allow_module_level=True)

from solvent import server, treasury
from solvent.event_hub import EventHub
from solvent.webhook_log import WebhookLog


def test_job_submission_routes_publish_each_agent_event_once(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_DASHBOARD_TOKEN", "d" * 32)
    monkeypatch.setenv("SOLVENT_DELIVERY_SECRET", "test-delivery-secret-" * 2)
    monkeypatch.setenv("SOLVENT_FORCE_STRIPE_SIMULATE", "1")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(treasury, "DB_PATH", tmp_path / "solvent.db")
    monkeypatch.setattr(server, "WebhookLog", lambda: WebhookLog(":memory:"))

    published = []
    original_publish = EventHub.publish

    def capture_publish(self, event_type, payload=None):
        if event_type == "agent_event":
            published.append(payload["event"])
        return original_publish(self, event_type, payload)

    monkeypatch.setattr(EventHub, "publish", capture_publish)
    client = TestClient(server.create_app(fresh=True))

    for path, job_id, headers in (
        ("/jobs", "public-job", {}),
        ("/api/job", "dashboard-job", {"X-Solvent-Dashboard-Token": "d" * 32}),
    ):
        response = client.post(
            path,
            json={
                "id": job_id,
                "topic": "AI chip market",
                "budget_cents": 4900,
                "customer_email": f"{job_id}@example.test",
            },
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["simulated"] is True
        assert client.get(f"/jobs/{job_id}").json()["job"]["status"] == "awaiting_payment"

        events = [event for event in published if event.get("job_id") == job_id]
        assert [event["stage"] for event in events] == ["quote", "invoice"]


def test_declined_job_publishes_one_event_without_recursing(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_DASHBOARD_TOKEN", "d" * 32)
    monkeypatch.setattr(treasury, "DB_PATH", tmp_path / "solvent.db")
    monkeypatch.setattr(server, "WebhookLog", lambda: WebhookLog(":memory:"))
    published = []
    original_publish = EventHub.publish

    def capture_publish(self, event_type, payload=None):
        if event_type == "agent_event":
            published.append(payload["event"])
        return original_publish(self, event_type, payload)

    monkeypatch.setattr(EventHub, "publish", capture_publish)
    client = TestClient(server.create_app(fresh=True))

    response = client.post("/jobs", json={"id": "invalid-job", "topic": ""})
    assert response.status_code == 200
    assert response.json()["stage"] == "declined"
    assert [event["stage"] for event in published] == ["declined"]
