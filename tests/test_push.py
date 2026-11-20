"""Operator pushes to an OpenClaw Gateway: config, messages, outbox, delivery, alerts."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from solvent import push
from solvent.alerts import CRITICAL, OK, WARN, Alert
from solvent.treasury import Treasury

TOKEN = "hook-token-" + "x" * 32
PUSH_ENV = (push.HOOKS_URL_ENV, push.HOOKS_TOKEN_ENV, push.AGENT_ID_ENV, push.EVENTS_ENV)


class _Gateway:
    """A stand-in for the Gateway's hooks endpoint that records every request."""

    def __init__(self):
        self.requests: list[dict] = []
        self.status, self.headers = 200, {}
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                gateway.requests.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "body": json.loads(self.rfile.read(length) or b"null"),
                    }
                )
                self.send_response(gateway.status)
                for name, value in gateway.headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true, "mode": "now", "eventOutcome": "queued"}')

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hooks"
        threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        ).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def gateway():
    g = _Gateway()
    yield g
    g.close()


@pytest.fixture
def env(tmp_path, monkeypatch, gateway):
    """Pushes on, pointed at the local stand-in Gateway, in an isolated home."""
    from solvent import treasury as treasury_mod

    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setattr(treasury_mod, "DB_PATH", tmp_path / "solvent.db")
    for name in PUSH_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(push.HOOKS_URL_ENV, gateway.url)
    monkeypatch.setenv(push.HOOKS_TOKEN_ENV, TOKEN)
    monkeypatch.setenv(push.AGENT_ID_ENV, "main")
    return monkeypatch


@pytest.fixture
def t(env, tmp_path):
    return Treasury(tmp_path / "solvent.db")


# --- configuration -------------------------------------------------------------


def test_pushes_are_off_until_both_url_and_token_are_set():
    assert push.config_error({}) is None and push.sink({}) is None
    only_url = {push.HOOKS_URL_ENV: "http://127.0.0.1:18789/hooks"}
    assert push.config_error(only_url) == f"{push.HOOKS_TOKEN_ENV} is not set"
    assert push.sink(only_url) is None


@pytest.mark.parametrize(
    "url,problem",
    [
        ("ftp://127.0.0.1/hooks", "must be an http(s) URL"),
        ("http://203.0.113.5:18789/hooks", "plain http to another host"),
        ("https://user:pw@gw.example.ts.net/hooks", "must not carry credentials"),
        ("https://gw.example.ts.net/hooks?token=x", "must not carry credentials"),
        ("http://127.0.0.1:notaport/hooks", "not a valid URL"),
    ],
)
def test_unsafe_or_broken_hook_urls_are_refused(url, problem):
    env = {push.HOOKS_URL_ENV: url, push.HOOKS_TOKEN_ENV: TOKEN}
    assert problem in push.config_error(env)
    assert push.sink(env) is None


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:18789/hooks/", "https://gw.example.ts.net/hooks", "http://localhost/h"],
)
def test_loopback_http_and_remote_https_are_accepted(url):
    env = {push.HOOKS_URL_ENV: url, push.HOOKS_TOKEN_ENV: TOKEN}
    assert push.config_error(env) is None
    assert push.sink(env).wake_url == url.rstrip("/") + "/wake"


def test_enabled_events_default_list_and_empty():
    assert push.enabled_events({}) == frozenset(push.DEFAULT_EVENTS)
    assert push.enabled_events({push.EVENTS_ENV: " Paid, alerts "}) == {"paid", "alerts"}
    assert push.enabled_events({push.EVENTS_ENV: ""}) == frozenset()


# --- messages ------------------------------------------------------------------


@pytest.mark.parametrize(
    "event,kind,fragment",
    [
        ({"stage": "paid", "amount": 4900}, "paid", "was paid ($49.00)"),
        ({"stage": "delivered", "url": "https://x/briefs/J1"}, "delivered", "was delivered"),
        ({"stage": "refunded", "amount": 4900}, "refunded", "was refunded ($49.00)"),
        ({"stage": "refund_pending", "reason": "r"}, "refund_pending", "solvent reconcile"),
        ({"stage": "spend_held", "amount": 3000, "memo": "m"}, "spend_held", "held for approval"),
        ({"stage": "delivery_review_required"}, "delivery_review_required", "needs review"),
        ({"stage": "booked", "status": "failed"}, "failed", "failed after payment"),
    ],
)
def test_job_messages(event, kind, fragment):
    got_kind, text = push.job_message({**event, "job_id": "J1"})
    assert got_kind == kind and fragment in text and "J1" in text


@pytest.mark.parametrize(
    "event",
    [
        {"stage": "declined", "job_id": "J1"},
        {"stage": "booked", "job_id": "J1", "status": "completed"},
        {"stage": "paid", "job_id": "J1; rm -rf /"},
        {"stage": "paid"},
    ],
)
def test_routine_events_and_unsafe_ids_are_not_pushed(event):
    assert push.job_message(event) is None


def test_messages_never_carry_customer_or_free_text():
    secret = "ZEBRACORN"
    for stage in ("paid", "refunded", "refund_pending", "spend_held", "delivery_review_required"):
        event = {"stage": stage, "job_id": "J1", "amount": 100, "topic": secret}
        event.update(reason=secret, memo=secret, customer_email=f"{secret}@x.test", url=secret)
        assert secret not in push.job_message(event)[1]


# --- queueing --------------------------------------------------------------------


def test_publish_is_a_no_op_while_pushes_are_off(t, env):
    env.delenv(push.HOOKS_TOKEN_ENV)
    assert push.publish_job_event(t, {"stage": "paid", "job_id": "J1"}) is False
    assert t.push_summary()["pending"] == 0


def test_publish_queues_each_event_kind_once_per_job(t):
    event = {"stage": "paid", "job_id": "J1", "amount": 4900}
    assert push.publish_job_event(t, event) is True
    assert push.publish_job_event(t, event) is False
    assert push.publish_job_event(t, {**event, "job_id": "J2"}) is True
    assert t.push_summary()["pending"] == 2


def test_publish_respects_the_event_list(t, env):
    env.setenv(push.EVENTS_ENV, "delivered")
    assert push.publish_job_event(t, {"stage": "paid", "job_id": "J1"}) is False
    assert push.publish_job_event(t, {"stage": "delivered", "job_id": "J1"}) is True


def test_a_real_job_queues_paid_and_delivered_without_the_topic(env, tmp_path):
    from solvent.agent import Solvent

    env.setenv("SOLVENT_FORCE_STRIPE_SIMULATE", "1")
    env.setenv("SOLVENT_DELIVERY_SECRET", "x" * 40)
    env.delenv("NVIDIA_API_KEY", raising=False)
    agent = Solvent(seed_cents=50_000, fresh=True)
    job = {"id": "J-push", "topic": "Zebracorn lithium outlook", "budget_cents": 4900}
    agent.handle_job({**job, "customer_email": "buyer@example.test"})
    rows = agent.t.due_pushes(time.time() + 1, 10)
    assert [r["dedupe_key"] for r in rows] == ["paid:J-push", "delivered:J-push"]
    assert not any("Zebracorn" in r["text"] or "buyer@" in r["text"] for r in rows)


# --- delivery --------------------------------------------------------------------


def test_delivery_posts_a_wake_with_the_hook_token(t, gateway):
    push.publish_job_event(t, {"stage": "paid", "job_id": "J1", "amount": 4900})
    assert push.deliver_due(t) == {"sent": 1, "failed": 0}
    (request,) = gateway.requests
    assert request["path"] == "/hooks/wake"
    assert request["auth"] == f"Bearer {TOKEN}"
    assert request["body"] == {
        "text": "SOLVENT: job J1 was paid ($49.00); fulfilment is starting.",
        "mode": "now",
        "agentId": "main",
    }
    assert t.push_summary() == {"pending": 0, "sent": 1, "failed": 0, "last_error": None}
    assert push.deliver_due(t) == {"sent": 0, "failed": 0}


def test_a_failure_backs_off_every_pending_push(t, gateway):
    gateway.status, gateway.headers = 503, {"Retry-After": "120"}
    for job_id in ("J1", "J2"):
        push.publish_job_event(t, {"stage": "delivered", "job_id": job_id})
    now = time.time()
    assert push.deliver_due(t, now=now) == {"sent": 0, "failed": 1}
    assert len(gateway.requests) == 1
    assert t.due_pushes(now + 119, 10) == []
    assert len(t.due_pushes(now + 121, 10)) == 2
    assert t.push_summary()["last_error"] == "HTTP 503"

    gateway.status, gateway.headers = 200, {}
    assert push.deliver_due(t, now=now + 121) == {"sent": 2, "failed": 0}


def test_an_unreachable_gateway_is_retried_then_given_up(t, env):
    env.setenv(push.HOOKS_URL_ENV, "http://127.0.0.1:9/hooks")
    t.enqueue_push("SOLVENT: x")
    now = time.time()
    for _ in range(push.MAX_ATTEMPTS):
        assert push.deliver_due(t, now=now)["failed"] == 1
        now += 3601
    summary = t.push_summary()
    assert summary["failed"] == 1 and summary["pending"] == 0
    assert summary["last_error"].startswith("unreachable")


def test_stale_pushes_are_given_up(t, gateway):
    gateway.status = 500
    t.enqueue_push("SOLVENT: x")
    push.deliver_due(t, now=time.time() + push.TTL_SECONDS + 1)
    assert t.push_summary()["failed"] == 1


def test_redirects_are_not_followed(t, gateway):
    gateway.status, gateway.headers = 302, {"Location": "http://127.0.0.1:9/steal"}
    t.enqueue_push("SOLVENT: x")
    assert push.deliver_due(t) == {"sent": 0, "failed": 1}
    assert t.push_summary()["last_error"] == "HTTP 302"


def test_old_pushes_are_pruned(t, gateway):
    t.enqueue_push("SOLVENT: x")
    push.deliver_due(t)
    push.deliver_due(t, now=time.time() + push.RETENTION_SECONDS + 1)
    assert t.push_summary() == {"pending": 0, "sent": 0, "failed": 0, "last_error": None}


# --- alerts ----------------------------------------------------------------------


def test_alert_changes_are_pushed_once(t):
    cash = lambda severity, message: Alert("cash", severity, message, "Add capital.")  # noqa: E731
    assert push.publish_alert_changes(t, [cash(OK, "fine")]) == 0
    assert push.publish_alert_changes(t, [cash(WARN, "20 days of runway")]) == 1
    assert push.publish_alert_changes(t, [cash(WARN, "19 days of runway")]) == 0
    assert push.publish_alert_changes(t, [cash(CRITICAL, "5 days of runway")]) == 1
    assert push.publish_alert_changes(t, [cash(OK, "balance $900.00")]) == 1
    texts = [r["text"] for r in t.due_pushes(time.time() + 1, 10)]
    assert texts == [
        "SOLVENT: warn alert, cash: 20 days of runway. Add capital.",
        "SOLVENT: critical alert, cash: 5 days of runway. Add capital.",
        "SOLVENT: resolved: cash is back to normal (balance $900.00).",
    ]


def test_a_problem_seen_first_is_pushed(t):
    assert push.publish_alert_changes(t, [Alert("stuck_jobs", CRITICAL, "1 paid job")]) == 1


def test_alert_pushes_can_be_switched_off(t, env):
    env.setenv(push.EVENTS_ENV, "paid")
    assert push.publish_alert_changes(t, [Alert("cash", CRITICAL, "low")]) == 0


def test_pump_sweeps_alerts_on_an_interval_and_delivers(t, gateway, monkeypatch):
    sweeps = []
    monkeypatch.setattr(
        "solvent.alerts.run_checks", lambda tr: sweeps.append(1) or [Alert("cash", WARN, "low")]
    )
    clock = iter([0.0, 10.0, 901.0])
    pump = push.Pump(clock=lambda: next(clock))
    assert pump.tick(t) == {"sent": 1, "failed": 0}
    pump.tick(t)
    pump.tick(t)
    assert len(sweeps) == 2 and len(gateway.requests) == 1


def test_alerts_notify_openclaw_pushes_problems_now(t, gateway):
    from solvent.alerts import notify, summarise

    data = summarise([Alert("cash", OK, "fine"), Alert("stuck_jobs", WARN, "2 jobs", "Run it.")])
    assert notify(data, "openclaw") == 1
    assert gateway.requests[0]["body"]["text"] == "SOLVENT: warn alert, stuck_jobs: 2 jobs. Run it."


# --- CLI, worker and doctor --------------------------------------------------------


def test_notify_cli_test_and_status(t, gateway, capsys):
    push.main(["--test"])
    out = capsys.readouterr().out
    assert "OpenClaw push: on" in out and "Sent 1, failed 0." in out
    assert gateway.requests[0]["body"]["text"].startswith("SOLVENT: test notification")
    push.main(["--json"])
    data = json.loads(capsys.readouterr().out)
    assert (
        data["outbox"]["sent"] == 1 and data["delivery"] is None and TOKEN not in json.dumps(data)
    )


def test_notify_cli_refuses_to_send_while_off(t, env, capsys):
    env.delenv(push.HOOKS_URL_ENV)
    with pytest.raises(SystemExit) as exc:
        push.main(["--flush"])
    assert exc.value.code == 2
    assert f"{push.HOOKS_URL_ENV} is not set" in capsys.readouterr().err


def test_notify_cli_exits_nonzero_when_delivery_fails(t, gateway):
    gateway.status = 401
    with pytest.raises(SystemExit) as exc:
        push.main(["--test"])
    assert exc.value.code == 1


def test_worker_pass_delivers_queued_pushes(t, gateway, env):
    from solvent.worker import run_worker

    env.setenv(push.EVENTS_ENV, "paid")
    push.publish_job_event(t, {"stage": "paid", "job_id": "J1", "amount": 100})
    run_worker(once=True)
    assert [r["body"]["text"] for r in gateway.requests] == [
        "SOLVENT: job J1 was paid ($1.00); fulfilment is starting."
    ]


def test_doctor_reports_push_configuration(t, env):
    from solvent.doctor import run_checks

    check = next(c for c in run_checks() if c["name"] == "openclaw_push")
    assert check["ok"] and check["detail"].startswith("on, to http://127.0.0.1:")
    env.setenv(push.HOOKS_URL_ENV, "http://203.0.113.5:18789/hooks")
    check = next(c for c in run_checks() if c["name"] == "openclaw_push")
    assert not check["ok"] and "plain http" in check["detail"]
