"""HTTP server: Stripe webhooks, job API, interactive dashboard + chat."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path

from .agent import Solvent
from .dashboard_chat import CHAT_PANEL_CSS, CHAT_PANEL_HTML, LIVE_CLIENT_JS
from .gateway import Gateway, register_outbound
from .delivery import verify_delivery_token, markdown_to_html, is_safe_job_id
from .event_hub import EventHub
from .stripe_client import StripeClient

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

    @app.on_event("startup")
    async def _startup():
        hub.bind_loop(asyncio.get_running_loop())
        from . import dashboard
        dashboard.render(agent.t.snapshot(), agent.log, live=True)

        async def _poll_external_status():
            status_path = Path(__file__).resolve().parent.parent / "data" / "dashboard_status.json"
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

    @app.post("/webhooks/stripe")
    async def stripe_webhook(req: Request):
        payload = await req.body()
        sig = req.headers.get("Stripe-Signature", "")
        payment = stripe.process_webhook(payload, sig, treasury=agent.t)
        if payment and payment.get("job_id"):
            agent._runner.handle_webhook_payment(payment)
            _publish_status()
        return {"received": True}

    @app.get("/briefs/{job_id}")
    def get_brief(job_id: str, token: str = ""):
        if not is_safe_job_id(job_id):
            raise HTTPException(404, "brief not found")
        if not verify_delivery_token(job_id, token):
            raise HTTPException(403, "invalid or expired delivery token")
        reports_dir = (Path(__file__).resolve().parent.parent / "data" / "reports").resolve()
        
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
    def list_briefs():
        raise HTTPException(403, "brief listing is not available")

    @app.get("/api/briefs/{job_id}")
    def get_brief_api(job_id: str, token: str = ""):
        if not is_safe_job_id(job_id):
            raise HTTPException(404, "brief not found")
        if not verify_delivery_token(job_id, token):
            raise HTTPException(403, "invalid or expired delivery token")
        reports_dir = (Path(__file__).resolve().parent.parent / "data" / "reports").resolve()
        
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
