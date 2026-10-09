"""HTTP server: Stripe webhooks, job API, interactive dashboard + chat."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager

from . import __version__
from .access import FailureLimiter, is_local_request, peer_host
from .agent import Solvent
from .delivery import is_safe_job_id, markdown_to_html, verify_delivery_token
from .event_hub import EventHub
from .gateway import Gateway, register_outbound
from .money import dollars_to_cents
from .paths import data_dir
from .paths import reports_dir as reports_dir_fn
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


#: Largest request body accepted on the public intake / pairing routes.
MAX_JOB_BODY_BYTES = 32 * 1024
MAX_PAIR_BODY_BYTES = 1024

#: Fields a public ``POST /jobs`` caller may set.  Anything else in the body is
#: dropped, so an anonymous caller cannot set internal flags such as
#: ``intake_approved`` (which skips the intake screen) or ``job_owner_session_id``.
PUBLIC_JOB_FIELDS = (
    "id",
    "topic",
    "budget_cents",
    "customer_email",
    "est_tokens",
    "market_data_calls",
    "web_search_calls",
    "context",
    "product",
)
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_FIELD_CHARS = 4_000

#: What the public intake says when it declines.  The internal reason (margin
#: floors, treasury state, other customers' job ids) is never returned.
_PUBLIC_INTAKE_DECLINES = {
    "duplicate": "an identical request was already submitted recently",
    "customer_burst": "too many recent requests from this customer; try again later",
    "blocked_domain": "this customer email domain is not accepted",
    "unreachable_customer": "a valid customer email is required",
    "oversized_order": "this order is above the automatic limit and needs manual review",
}
_PUBLIC_DECLINE_PREFIXES = (
    "security violation",
    "missing or",
    "missing ",
    "invalid ",
    "budget_cents must",
    "est_tokens",
    "market_data_calls",
    "web_search_calls",
    "unknown product",
    "order $",
)
_GENERIC_DECLINE = "this request can't be accepted right now"


def public_decline_reason(reason: object) -> str:
    """Map an internal decline reason to text that is safe to show an anonymous caller."""
    text = str(reason or "")
    if text.startswith("intake:"):
        rule = text.split(":", 1)[1].split("\u2014", 1)[0].strip()
        return _PUBLIC_INTAKE_DECLINES.get(rule, _GENERIC_DECLINE)
    if text.startswith(_PUBLIC_DECLINE_PREFIXES):
        return text
    return _GENERIC_DECLINE


def public_job_result(result: dict, job_id: str) -> dict:
    """The intake response for an anonymous caller: no costs, margins or emails."""
    if result.get("stage") == "declined":
        return {
            "stage": "declined",
            "job_id": job_id,
            "reason": public_decline_reason(result.get("reason")),
        }
    if result.get("url"):
        return {
            "stage": "invoice",
            "job_id": job_id,
            "status": "awaiting_payment",
            "url": result["url"],
            "checkout_url": result["url"],
            "session_id": result.get("session_id"),
            "amount_cents": result.get("amount_cents"),
            "simulated": result.get("simulated", False),
        }
    return {"stage": str(result.get("stage") or "received"), "job_id": job_id}


def public_job_view(row: dict, cancelled: bool = False) -> dict:
    """The unauthenticated view of a job: id and status only."""
    view: dict = {"job": {"id": row.get("id"), "status": row.get("status")}}
    if cancelled:
        view["cancelled"] = True
    return view


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
            'FastAPI is required for `solvent serve`. Install with: pip install -e ".[serve]"'
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
    dashboard_token = os.environ.get("SOLVENT_DASHBOARD_TOKEN", "").strip()

    def _has_dashboard_auth(req: Request) -> bool:
        token = req.headers.get("X-Solvent-Dashboard-Token", "") or req.query_params.get("token", "")
        # Compare as bytes: compare_digest raises TypeError on non-ASCII str.
        return bool(dashboard_token) and secrets.compare_digest(
            token.encode("utf-8"), dashboard_token.encode("utf-8")
        )

    def _require_dashboard_auth(req: Request) -> None:
        if not _has_dashboard_auth(req):
            raise HTTPException(403, "invalid dashboard token")

    async def _read_limited_json(req: Request, limit: int) -> object:
        """Read and parse a JSON body, refusing anything over ``limit`` bytes."""
        declared = req.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, "request body too large")
        chunks: list[bytes] = []
        size = 0
        async for chunk in req.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, "request body too large")
            chunks.append(chunk)
        try:
            return json.loads(b"".join(chunks) or b"null")
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "request body must be valid JSON") from None

    # Pairing tokens are short, so guessing them is slowed down: a few failed
    # verifications per peer, and a generous global ceiling (the peer address is
    # the proxy's when behind one, so a per-peer cap alone is not enough).
    pair_fail_peer = FailureLimiter(max_failures=5, window=300.0)
    pair_fail_global = FailureLimiter(max_failures=60, window=600.0)

    def _sanitize_status_data(data: dict) -> dict:
        sanitized = dict(data)
        if isinstance(sanitized.get("briefs"), dict):
            sanitized["briefs"] = {str(job_id): "" for job_id in sanitized["briefs"]}
        return sanitized

    def _refresh_status() -> dict:
        from . import dashboard

        return _sanitize_status_data(dashboard.build_status_data(agent.t.snapshot(), agent.log))

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
        data = _refresh_status()
        hub.publish("agent_event", {"event": event, "data": data})

    # The runner already calls agent._capture_event, which logs then forwards here.
    agent.on_event = _on_agent_event

    def _dashboard_outbound(external_id: str, text: str) -> None:
        hub.publish(
            "chat",
            {
                "role": "assistant",
                "text": text,
                "session_id": external_id,
                "channel": "dashboard",
            },
        )

    register_outbound("dashboard", _dashboard_outbound)

    @asynccontextmanager
    async def _app_lifespan(app: object):
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
                            data = _sanitize_status_data(
                                json.loads(status_path.read_text(encoding="utf-8"))
                            )
                            hub.publish("status", {"data": data})
                    from .notifications import drain_chat_outbox

                    for row in drain_chat_outbox():
                        if row.get("channel") != "dashboard":
                            continue
                        hub.publish(
                            "chat",
                            {
                                "role": "assistant",
                                "text": row.get("text", ""),
                                "session_id": row.get("external_id", ""),
                                "channel": "dashboard",
                            },
                        )
                except Exception:
                    pass
                await asyncio.sleep(2.0)

        task = asyncio.create_task(_poll_external_status())
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(title="SOLVENT", version="2.1", lifespan=_app_lifespan)
    app.state.webhook_log = webhook_log
    app.state.agent = agent

    @app.get("/health")
    def health(req: Request):
        # Public liveness probe: no financial detail unless the caller is authenticated.
        data: dict = {"status": "ok", "version": __version__}
        if _has_dashboard_auth(req):
            data["balance_cents"] = agent.t.balance_cents()
        return data

    @app.get("/api/pair/qr")
    def api_pair_qr(req: Request):
        """Generate an OpenClaw pairing token and return a QR code PNG (or JSON fallback).

        Minting a token is an operator action, so it needs the dashboard token.
        """
        _require_dashboard_auth(req)
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
        """Redeem a single-use pairing token (no login: the token is the credential)."""
        peer = peer_host(req) or "unknown"
        if pair_fail_peer.blocked(peer) or pair_fail_global.blocked("*"):
            raise HTTPException(429, "too many failed attempts; try again later", headers={"Retry-After": "300"})
        body = await _read_limited_json(req, MAX_PAIR_BODY_BYTES)
        token = body.get("token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise HTTPException(400, "token required")
        if not agent.t.verify_openclaw_token(token.strip()):
            pair_fail_peer.record_failure(peer)
            pair_fail_global.record_failure("*")
            raise HTTPException(403, "invalid or expired token")
        return {"verified": True}

    @app.get("/")
    def dashboard_page(req: Request):
        _require_dashboard_auth(req)
        from . import dashboard

        path = dashboard.render(agent.t.snapshot(), agent.log, live=True)
        return HTMLResponse(path.read_text(encoding="utf-8"))

    @app.get("/api/status")
    def api_status(req: Request):
        _require_dashboard_auth(req)
        return JSONResponse(_refresh_status())

    @app.get("/api/events")
    async def api_events(request: Request):
        _require_dashboard_auth(request)
        session_id = request.query_params.get("session_id", "")
        q = hub.subscribe()

        async def stream():
            try:
                yield EventHub.sse_format({"type": "hello", "ts": time.time()})
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=15.0)
                        if msg.get("type") == "chat":
                            if msg.get("channel") != "dashboard":
                                continue
                            target_session = msg.get("session_id", "")
                            if target_session and target_session != session_id:
                                continue
                        yield EventHub.sse_format(msg)
                    except asyncio.TimeoutError:
                        yield EventHub.sse_format({"type": "ping", "ts": time.time()})
            finally:
                hub.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/chat")
    async def api_chat(body: ChatBody, req: Request):
        _require_dashboard_auth(req)
        message = body.message.strip()
        if not message:
            raise HTTPException(400, "message required")
        session_id = body.session_id or "dashboard-default"
        reply = gateway.handle_inbound("dashboard", session_id, message, user_label="dashboard")
        _publish_status()
        return {"reply": reply, "session_id": session_id}

    @app.post("/api/job")
    async def api_job(body: JobBody, req: Request):
        _require_dashboard_auth(req)
        payload = body.model_dump(exclude_none=True)
        if not payload.get("id"):
            import uuid

            payload["id"] = "J" + uuid.uuid4().hex[:8]
        result = agent.enqueue_job(payload)
        _publish_status()
        return JSONResponse(result)

    @app.post("/jobs")
    async def create_job(req: Request):
        """Public intake endpoint: anyone may order a brief.

        Input is size-limited and restricted to ``PUBLIC_JOB_FIELDS`` (so internal
        flags cannot be set), an existing job id cannot be re-submitted, and the
        response carries only the checkout link / a sanitized decline: never costs,
        margins, treasury state or other customers' data.
        """
        raw = await _read_limited_json(req, MAX_JOB_BODY_BYTES)
        if not isinstance(raw, dict):
            raise HTTPException(422, "request body must be a JSON object")
        fields = {k: raw[k] for k in PUBLIC_JOB_FIELDS if raw.get(k) is not None}
        for key, value in fields.items():
            if isinstance(value, str) and len(value) > _MAX_FIELD_CHARS:
                raise HTTPException(422, f"{key} is too long")
        try:
            payload = JobBody(**fields).model_dump(exclude_none=True)
        except Exception:
            raise HTTPException(422, "invalid job fields") from None
        if payload.get("id"):
            if not _JOB_ID_RE.fullmatch(payload["id"]):
                raise HTTPException(422, "id must be 1-64 letters, digits, '_' or '-'")
            if agent.t.get_job(payload["id"]):
                raise HTTPException(409, "job id already exists; omit id to get a new one")
        else:
            import uuid

            payload["id"] = "J" + uuid.uuid4().hex[:8]
        result = agent.enqueue_job(payload)
        _publish_status()
        return JSONResponse(public_job_result(result, payload["id"]))

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str, req: Request):
        """Job status.  Anonymous callers (e.g. a customer returning from Stripe) get
        the id and status only; the full row and metrics need the dashboard token."""
        row = agent.t.get_job(job_id)
        if not row:
            raise HTTPException(404, "job not found")
        if not _has_dashboard_auth(req):
            return public_job_view(row, cancelled=req.query_params.get("cancelled") == "1")
        metrics = agent.t.get_metrics(job_id)
        return {"job": dict(row), "metrics": metrics}

    def _is_local_request(request: Request | None) -> bool:
        """Local-only gate; see :func:`solvent.access.is_local_request`."""
        return is_local_request(request)

    @app.get("/api/receipt/{job_id}")
    def get_receipt(job_id: str, token: str = "", request: Request = None):  # type: ignore[assignment]
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
        """Receive a Stripe event.

        The signature is verified FIRST.  Nothing from an unauthenticated body
        is parsed for logging, stored or applied: a bad/missing signature (or a
        missing ``STRIPE_WEBHOOK_SECRET``) is rejected and leaves no trace in the
        webhook log.  Only verified events are stored, and an event id is stored
        once, so a repeated id can never overwrite an earlier event.
        """
        if not stripe.webhook_secret:
            # Fail closed: with no secret we cannot authenticate anything.
            raise HTTPException(503, "stripe webhook endpoint is not configured")
        payload = await req.body()
        sig = req.headers.get("Stripe-Signature", "")
        try:
            event = stripe.verify_webhook(payload, sig)
        except Exception:
            raise HTTPException(400, "invalid webhook signature") from None

        event_id = event["id"]
        event_type = str(event.get("type", ""))
        if not webhook_log.record(event_id, event_type, payload, "received", verified=True):
            # Already seen.  Acknowledge (so Stripe stops retrying) unless the
            # earlier attempt did not finish, in which case process it again.
            if webhook_log.is_verified(event_id) and webhook_log.get_status(event_id) in (
                "processed",
                "skipped",
            ):
                return {"received": True, "duplicate": True}
        if not stripe.live:
            webhook_log.mark_skipped(event_id)
            return {"received": True, "ignored": True}
        try:
            payment = stripe.apply_webhook_event(event, treasury=agent.t)
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
    def list_briefs(request: Request = None):  # type: ignore[assignment]
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
    def get_brief_api(job_id: str, token: str = "", request: Request = None):  # type: ignore[assignment]
        if not is_safe_job_id(job_id):
            raise HTTPException(404, "brief not found")
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
    def api_webhooks_list(req: Request):
        _require_dashboard_auth(req)
        # Metadata only: raw payloads contain customer emails and are not exposed.
        return JSONResponse(webhook_log.list_public(50))

    @app.get("/api/webhooks/stats")
    def api_webhooks_stats(req: Request):
        _require_dashboard_auth(req)
        return JSONResponse(webhook_log.stats())

    @app.post("/api/webhooks/{event_id}/replay")
    async def api_webhooks_replay(event_id: str, req: Request):
        _require_dashboard_auth(req)
        stored = webhook_log.get_payload(event_id)
        if stored is None:
            raise HTTPException(404, f"No stored payload for event_id={event_id!r}")
        if not webhook_log.is_verified(event_id):
            raise HTTPException(409, "stored event was not signature-verified; refusing to replay")
        if not stripe.live:
            raise HTTPException(409, "stripe is not live; nothing to replay")
        try:
            # The stored body was signature-verified when it was received, so it
            # is re-dispatched internally; there is no signature bypass for
            # external callers (the public webhook route always verifies).
            payment = stripe.apply_webhook_event(json.loads(stored), treasury=agent.t)
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
        raise RuntimeError('uvicorn required: pip install -e ".[serve]"') from exc
    app = create_app(seed_cents=dollars_to_cents(args.seed), fresh=not args.keep_balance)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
