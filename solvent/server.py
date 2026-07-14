"""HTTP server: Stripe webhooks, job API, interactive dashboard + chat."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time

from .agent import Solvent
from .gateway import Gateway, register_outbound
from .delivery import verify_delivery_token, markdown_to_html, is_safe_job_id
from .event_hub import EventHub
from .paths import data_dir, reports_dir as reports_dir_fn
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
            "FastAPI is required for `solvent serve`. "
            "Install with: pip install -r requirements-serve.txt"
        ) from exc


def create_app(seed_cents: int = 10_000, fresh: bool = False) -> object:
    FastAPI, HTTPException, HTMLResponse, JSONResponse, StreamingResponse = _require_fastapi()
    hub = EventHub()
    agent = Solvent(seed_cents=seed_cents, fresh=fresh, sync_payment=False)
    gateway = Gateway(agent=agent)
    stripe = StripeClient()
    webhook_log = WebhookLog()
    status_lock = threading.Lock()
    last_status_json = ""

    def _refresh_status() -> dict:
        from . import dashboard
        return dashboard.build_status_data(agent.t.snapshot(), agent.log)

    def _publish_status() -> None:
        nonlocal last_status_json
        data = _refresh_status()
        payload = json.dumps(data, sort_keys=True)
        with status_lock:
            if payload == last_status_json:
                return
            last_status_json = payload
        hub.publish("status", {"data": data})

    def _on_agent_event(event: dict) -> None:
        agent._capture_event(event)
        data = _refresh_status()
        hub.publish("agent_event", {"event": event, "data": data})

    agent._runner.on_event = _on_agent_event
    agent.on_event = _on_agent_event

    def _dashboard_outbound(external_id: str, text: str) -> None:
        hub.publish("chat", {"role": "assistant", "text": text, "session_id": external_id})

    register_outbound("dashboard", _dashboard_outbound)

    app = FastAPI(title="SOLVENT", version="2.1")
    app.state.webhook_log = webhook_log

    @app.on_event("startup")
    async def _startup():
        hub.bind_loop(asyncio.get_running_loop())
        from . import dashboard
        dashboard.render(agent.t.snapshot(), agent.log, live=True)

        async def _poll_external_status():
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

        asyncio.create_task(_poll_external_status())

    @app.get("/health")
    def health():
        return {"status": "ok", "balance_cents": agent.t.balance_cents()}

    @app.get("/api/pair/qr")
    def api_pair_qr():
        """Generate an OpenClaw pairing token and return a QR code PNG (or JSON fallback)."""
        import os
        token = agent.t.create_openclaw_token(ttl=600)
        base_url = os.environ.get("SOLVENT_BASE_URL", "")
        host = base_url.replace("https://", "").replace("http://", "").split("/")[0]
        try:
            port = int(host.split(":")[-1]) if ":" in host else 443
            host_name = host.split(":")[0]
        except ValueError:
            port = 443
            host_name = host
        from . import qr as _qr
        png = _qr.png_bytes(token, host=host_name, port=port)
        if png:
            from starlette.responses import Response
            return Response(content=png, media_type="image/png")
        return JSONResponse({"token": token, "note": "install qrcode[pil] for PNG output"})

    @app.post("/api/pair/verify")
    async def api_pair_verify(req: Request):
        body = await req.json()
        token = (body.get("token") or "").strip()
        if not token:
            raise HTTPException(400, "token required")
        ok = agent.t.verify_openclaw_token(token)
        if not ok:
            raise HTTPException(403, "invalid or expired token")
        return {"verified": True}

    @app.get("/")
    def dashboard_page():
        from . import dashboard
        path = dashboard.render(agent.t.snapshot(), agent.log, live=True)
        return HTMLResponse(path.read_text(encoding="utf-8"))

    @app.get("/api/status")
    def api_status():
        return JSONResponse(_refresh_status())

    @app.get("/api/events")
    async def api_events():
        q = hub.subscribe()

        async def stream():
            try:
                yield EventHub.sse_format({"type": "hello", "ts": time.time()})
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield EventHub.sse_format(msg)
                    except asyncio.TimeoutError:
                        yield EventHub.sse_format({"type": "ping", "ts": time.time()})
            finally:
                hub.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/chat")
    async def api_chat(body: ChatBody):
        message = body.message.strip()
        if not message:
            raise HTTPException(400, "message required")
        session_id = body.session_id or "dashboard-default"
        reply = gateway.handle_inbound("dashboard", session_id, message, user_label="dashboard")
        _publish_status()
        return {"reply": reply, "session_id": session_id}

    @app.post("/api/job")
    async def api_job(body: JobBody):
        payload = body.model_dump(exclude_none=True)
        if not payload.get("id"):
            import uuid
            payload["id"] = "J" + uuid.uuid4().hex[:8]
        result = agent.enqueue_job(payload)
        _publish_status()
        return JSONResponse(result)

    @app.post("/jobs")
    async def create_job(body: JobBody):
        payload = body.model_dump(exclude_none=True)
        if not payload.get("id"):
            import uuid
            payload["id"] = "J" + uuid.uuid4().hex[:8]
        result = agent.enqueue_job(payload)
        _publish_status()
        return JSONResponse(result)

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        row = agent.t.get_job(job_id)
        if not row:
            raise HTTPException(404, "job not found")
        metrics = agent.t.get_metrics(job_id)
        return {"job": dict(row), "metrics": metrics}

    def _is_local_request(request: Request | None) -> bool:
        if request is None:
            return False
        client_host = getattr(request.client, "host", "") if request.client else ""
        return client_host in ("127.0.0.1", "::1", "localhost", "testclient")

    @app.get("/api/receipt/{job_id}")
    def get_receipt(job_id: str, token: str = "", request: Request = None):
        """Return a plaintext job receipt.

        Access requires either:
        - A valid delivery token (``?token=...``), OR
        - The request originating from localhost (127.0.0.1 / ::1).
        """

        row = agent.t.get_job(job_id)
        if not row:
            raise HTTPException(404, "job not found")

        # Auth: localhost OR valid delivery token
        if not _is_local_request(request):
            if not verify_delivery_token(job_id, token):
                raise HTTPException(403, "invalid or expired delivery token")

        from .receipt import build_receipt
        job_dict = dict(row)
        pnl = agent.t.job_pnl_cents(job_id)
        balance = agent.t.balance_cents()
        text = build_receipt(job_dict, pnl, balance)

        from starlette.responses import PlainTextResponse
        return PlainTextResponse(text)

    @app.post("/webhooks/stripe")
    async def stripe_webhook(req: Request):
        payload = await req.body()
        sig = req.headers.get("Stripe-Signature", "")
        event_id = json.loads(payload).get("id", "unknown") if payload else "unknown"
        event_type = json.loads(payload).get("type", "") if payload else ""
        webhook_log.record(event_id, event_type, payload, "received")
        try:
            payment = stripe.process_webhook(payload, sig, treasury=agent.t)
            if payment and payment.get("job_id"):
                agent._runner.handle_webhook_payment(payment)
                _publish_status()
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
        reports_dir = reports_dir_fn().resolve()

        path_html = (reports_dir / f"{job_id}.html").resolve()
        try:
            path_html.relative_to(reports_dir)
            if path_html.is_file():
                return HTMLResponse(path_html.read_text(encoding="utf-8"))
        except ValueError:
            raise HTTPException(404, "brief not found") from None

        path_md = (reports_dir / f"{job_id}.md").resolve()
        try:
            path_md.relative_to(reports_dir)
            if path_md.is_file():
                return HTMLResponse(markdown_to_html(path_md.read_text(encoding="utf-8")))
        except ValueError:
            raise HTTPException(404, "brief not found") from None

        raise HTTPException(404, "brief not found")

    @app.get("/api/briefs")
    def list_briefs(request: Request = None):
        if not _is_local_request(request):
            raise HTTPException(403, "brief listing is only available locally")
        reports_dir = reports_dir_fn()
        if not reports_dir.is_dir():
            return []
        stems = set()
        for p in reports_dir.glob("*"):
            if p.suffix in (".md", ".html") and p.stem != ".gitkeep":
                stems.add(p.stem)
        return sorted(list(stems))

    @app.get("/api/briefs/{job_id}")
    def get_brief_api(job_id: str, token: str = "", request: Request = None):
        if not _is_local_request(request) and not verify_delivery_token(job_id, token):
            raise HTTPException(403, "invalid or expired delivery token")
        reports_dir = reports_dir_fn().resolve()

        path_html = (reports_dir / f"{job_id}.html").resolve()
        try:
            path_html.relative_to(reports_dir)
            if path_html.is_file():
                return HTMLResponse(path_html.read_text(encoding="utf-8"))
        except ValueError:
            raise HTTPException(404, "brief not found") from None

        path_md = (reports_dir / f"{job_id}.md").resolve()
        try:
            path_md.relative_to(reports_dir)
            if path_md.is_file():
                return HTMLResponse(markdown_to_html(path_md.read_text(encoding="utf-8")))
        except ValueError:
            raise HTTPException(404, "brief not found") from None

        raise HTTPException(404, "brief not found")

    # ------------------------------------------------------------------
    # Webhook monitoring + replay endpoints
    # ------------------------------------------------------------------

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
                _publish_status()
            webhook_log.mark_processed(event_id)
        except Exception as exc:
            webhook_log.mark_error(event_id, str(exc))
            raise HTTPException(500, str(exc)) from exc
        return {"replayed": True, "event_id": event_id}

    return app


def main():
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
        raise RuntimeError("uvicorn required: pip install -r requirements-serve.txt") from exc
    app = create_app(seed_cents=int(args.seed * 100), fresh=not args.keep_balance)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
