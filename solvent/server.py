"""FastAPI surface for jobs, Stripe webhooks, briefs, dashboard, and chat."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path

from .agent import Solvent
from .delivery import is_safe_job_id, markdown_to_html, verify_delivery_token
from .event_hub import EventHub
from .gateway import Gateway, register_outbound
from .paths import data_dir, reports_dir
from .stripe_client import StripeClient
from .webhook_log import WebhookLog

try:
    from starlette.requests import Request
except ImportError:
    Request = object  # type: ignore[misc,assignment]

try:
    from pydantic import BaseModel
except ImportError:
    BaseModel = object  # type: ignore[misc,assignment]


class ChatBody(BaseModel):
    message: str
    session_id: str | None = None


class JobBody(BaseModel):
    id: str | None = None
    topic: str | None = None
    budget_cents: int | None = None
    customer_email: str | None = None
    est_tokens: int | None = None
    market_data_calls: int | None = None
    web_search_calls: int | None = None
    context: str | None = None

    model_config = {"extra": "allow"}


def _require_fastapi():
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

        return FastAPI, HTTPException, HTMLResponse, JSONResponse, StreamingResponse
    except ImportError as exc:
        raise RuntimeError(
            "FastAPI is required for `solvent serve`. Install with: "
            "pip install 'solvent-agent[serve]'"
        ) from exc


def _pairing_host() -> tuple[str, int]:
    base_url = os.environ.get("SOLVENT_BASE_URL", "")
    host = base_url.replace("https://", "").replace("http://", "").split("/")[0]
    try:
        port = int(host.split(":")[-1]) if ":" in host else 443
    except ValueError:
        port = 443
    return host.split(":")[0], port


def _job_payload(body: JobBody) -> dict:
    payload = body.model_dump(exclude_none=True)
    if not payload.get("id"):
        import uuid

        payload["id"] = "J" + uuid.uuid4().hex[:8]
    return payload


def _brief_path(job_id: str) -> Path | None:
    if not is_safe_job_id(job_id):
        return None

    root = reports_dir().resolve()
    for suffix in (".html", ".md"):
        path = (root / f"{job_id}{suffix}").resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return None
        if path.is_file():
            return path
    return None


def create_app(seed_cents: int = 10_000, fresh: bool = False) -> object:
    FastAPI, HTTPException, HTMLResponse, JSONResponse, StreamingResponse = _require_fastapi()

    hub = EventHub()
    agent = Solvent(seed_cents=seed_cents, fresh=fresh, sync_payment=False)
    gateway = Gateway(agent=agent)
    stripe = StripeClient()
    webhook_log = WebhookLog()
    status_lock = threading.Lock()
    last_status_json = ""

    def refresh_status() -> dict:
        from . import dashboard

        return dashboard.build_status_data(agent.t.snapshot(), agent.log)

    def publish_status() -> None:
        nonlocal last_status_json
        data = refresh_status()
        payload = json.dumps(data, sort_keys=True)
        with status_lock:
            if payload == last_status_json:
                return
            last_status_json = payload
        hub.publish("status", {"data": data})

    def publish_agent_event(event: dict) -> None:
        # StageRunner already routes through agent._capture_event. This callback
        # only publishes; calling capture here would recurse and duplicate events.
        hub.publish("agent_event", {"event": event, "data": refresh_status()})

    agent.on_event = publish_agent_event

    def dashboard_outbound(external_id: str, text: str) -> None:
        hub.publish(
            "chat",
            {"role": "assistant", "text": text, "session_id": external_id},
        )

    register_outbound("dashboard", dashboard_outbound)

    app = FastAPI(title="SOLVENT", version="2.1")
    app.state.agent = agent
    app.state.webhook_log = webhook_log

    @app.on_event("startup")
    async def startup() -> None:
        hub.bind_loop(asyncio.get_running_loop())
        from . import dashboard

        dashboard.render(agent.t.snapshot(), agent.log, live=True)

        async def poll_external_status() -> None:
            status_path = data_dir() / "dashboard_status.json"
            last_mtime = 0.0
            while True:
                try:
                    if status_path.is_file():
                        mtime = status_path.stat().st_mtime
                        if mtime > last_mtime:
                            last_mtime = mtime
                            data = json.loads(status_path.read_text(encoding="utf-8"))
                            hub.publish("status", {"data": data})

                    from .notifications import drain_chat_outbox

                    for row in drain_chat_outbox():
                        hub.publish(
                            "chat",
                            {
                                "role": "assistant",
                                "text": row.get("text", ""),
                                "session_id": row.get("external_id", ""),
                                "channel": row.get("channel", ""),
                            },
                        )
                except Exception:
                    pass
                await asyncio.sleep(2.0)

        asyncio.create_task(poll_external_status())

    @app.get("/health")
    def health():
        return {"status": "ok", "balance_cents": agent.t.balance_cents()}

    @app.get("/api/pair/qr")
    def api_pair_qr():
        token = agent.t.create_openclaw_token(ttl=600)
        host, port = _pairing_host()
        from . import qr

        png = qr.png_bytes(token, host=host, port=port)
        if png:
            from starlette.responses import Response

            return Response(content=png, media_type="image/png")
        return JSONResponse(
            {"token": token, "note": "install solvent-agent[qr] for PNG output"}
        )

    @app.post("/api/pair/verify")
    async def api_pair_verify(request: Request):
        body = await request.json()
        token = (body.get("token") or "").strip()
        if not token:
            raise HTTPException(400, "token required")
        if not agent.t.verify_openclaw_token(token):
            raise HTTPException(403, "invalid or expired token")
        return {"verified": True}

    @app.get("/")
    def dashboard_page():
        from . import dashboard

        path = dashboard.render(agent.t.snapshot(), agent.log, live=True)
        return HTMLResponse(path.read_text(encoding="utf-8"))

    @app.get("/api/status")
    def api_status():
        return JSONResponse(refresh_status())

    @app.get("/api/events")
    async def api_events():
        queue = hub.subscribe()

        async def stream():
            try:
                yield EventHub.sse_format({"type": "hello", "ts": time.time()})
                while True:
                    try:
                        message = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield EventHub.sse_format(message)
                    except asyncio.TimeoutError:
                        yield EventHub.sse_format({"type": "ping", "ts": time.time()})
            finally:
                hub.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/chat")
    async def api_chat(body: ChatBody):
        message = body.message.strip()
        if not message:
            raise HTTPException(400, "message required")
        session_id = body.session_id or "dashboard-default"
        reply = gateway.handle_inbound(
            "dashboard",
            session_id,
            message,
            user_label="dashboard",
        )
        publish_status()
        return {"reply": reply, "session_id": session_id}

    @app.post("/api/job")
    @app.post("/jobs")
    def enqueue(body: JobBody):
        result = agent.enqueue_job(_job_payload(body))
        publish_status()
        return JSONResponse(result)

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        row = agent.t.get_job(job_id)
        if not row:
            raise HTTPException(404, "job not found")
        return {"job": dict(row), "metrics": agent.t.get_metrics(job_id)}

    @app.get("/api/receipt/{job_id}")
    def get_receipt(job_id: str, token: str = "", request: Request = None):
        row = agent.t.get_job(job_id)
        if not row:
            raise HTTPException(404, "job not found")

        client_host = ""
        if request is not None and request.client:
            client_host = getattr(request.client, "host", "")
        is_local = client_host in ("127.0.0.1", "::1", "localhost", "testclient")
        if not is_local and not verify_delivery_token(job_id, token):
            raise HTTPException(403, "invalid or expired delivery token")

        from .receipt import build_receipt
        from starlette.responses import PlainTextResponse

        text = build_receipt(
            dict(row),
            agent.t.job_pnl_cents(job_id),
            agent.t.balance_cents(),
        )
        return PlainTextResponse(text)

    @app.post("/webhooks/stripe")
    async def stripe_webhook(request: Request):
        payload = await request.body()
        signature = request.headers.get("Stripe-Signature", "")
        try:
            envelope = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            envelope = {}
        event_id = envelope.get("id", "unknown")
        event_type = envelope.get("type", "")
        webhook_log.record(event_id, event_type, payload, "received")

        try:
            payment = stripe.process_webhook(payload, signature, treasury=agent.t)
            if payment and payment.get("job_id"):
                agent._runner.handle_webhook_payment(payment)
                publish_status()
            webhook_log.mark_processed(event_id)
        except Exception as exc:
            webhook_log.mark_error(event_id, str(exc))
            raise
        return {"received": True}

    @app.get("/briefs/{job_id}")
    def get_brief(job_id: str, token: str = ""):
        if not is_safe_job_id(job_id):
            raise HTTPException(404, "brief not found")
        if not verify_delivery_token(job_id, token):
            raise HTTPException(403, "invalid or expired delivery token")
        path = _brief_path(job_id)
        if path is None:
            raise HTTPException(404, "brief not found")
        if path.suffix == ".html":
            return HTMLResponse(path.read_text(encoding="utf-8"))
        return HTMLResponse(markdown_to_html(path.read_text(encoding="utf-8")))

    @app.get("/api/briefs")
    def list_briefs():
        root = reports_dir()
        stems = {
            path.stem
            for path in root.glob("*")
            if path.suffix in (".md", ".html") and path.stem != ".gitkeep"
        }
        return sorted(stems)

    @app.get("/api/briefs/{job_id}")
    def get_brief_api(job_id: str):
        path = _brief_path(job_id)
        if path is None:
            raise HTTPException(404, "brief not found")
        if path.suffix == ".html":
            return HTMLResponse(path.read_text(encoding="utf-8"))
        return HTMLResponse(markdown_to_html(path.read_text(encoding="utf-8")))

    @app.get("/api/webhooks")
    def api_webhooks_list():
        return JSONResponse(webhook_log.list_recent(50))

    @app.get("/api/webhooks/stats")
    def api_webhooks_stats():
        return JSONResponse(webhook_log.stats())

    @app.post("/api/webhooks/{event_id}/replay")
    async def api_webhooks_replay(event_id: str):
        stored = webhook_log.get_payload(event_id)
        if stored is None:
            raise HTTPException(404, f"No stored payload for event_id={event_id!r}")
        try:
            payment = stripe.process_webhook(stored, sig_header="", treasury=agent.t)
            if payment and payment.get("job_id"):
                agent._runner.handle_webhook_payment(payment)
                publish_status()
            webhook_log.mark_processed(event_id)
        except Exception as exc:
            webhook_log.mark_error(event_id, str(exc))
            raise HTTPException(500, str(exc)) from exc
        return {"replayed": True, "event_id": event_id}

    return app


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="SOLVENT HTTP server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("SOLVENT_PORT", "8787")))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--seed", type=float, default=100.0)
    parser.add_argument("--keep-balance", action="store_true")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "uvicorn is required for `solvent serve`. Install with: "
            "pip install 'solvent-agent[serve]'"
        ) from exc

    app = create_app(seed_cents=int(args.seed * 100), fresh=not args.keep_balance)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
