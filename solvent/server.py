"""HTTP server: Stripe webhooks, job API, hosted briefs."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .agent import Solvent
from .delivery import verify_delivery_token, markdown_to_html
from .stripe_client import StripeClient


def _require_fastapi():
    try:
        from fastapi import FastAPI, Request, HTTPException
        from fastapi.responses import HTMLResponse, JSONResponse
        return FastAPI, Request, HTTPException, HTMLResponse, JSONResponse
    except ImportError as exc:
        raise RuntimeError(
            "FastAPI is required for `solvent serve`. "
            "Install with: pip install -r requirements-serve.txt"
        ) from exc


def create_app(seed_cents: int = 10_000, fresh: bool = False) -> object:
    FastAPI, Request, HTTPException, HTMLResponse, JSONResponse = _require_fastapi()
    agent = Solvent(seed_cents=seed_cents, fresh=fresh, sync_payment=False)
    stripe = StripeClient()
    app = FastAPI(title="SOLVENT", version="2.0")

    @app.get("/health")
    def health():
        return {"status": "ok", "balance_cents": agent.t.balance_cents()}

    @app.post("/jobs")
    async def create_job(request: Request):
        body = await request.json()
        if not body.get("id"):
            import uuid
            body["id"] = "J" + uuid.uuid4().hex[:8]
        result = agent.enqueue_job(body)
        return JSONResponse(result)

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        row = agent.t.get_job(job_id)
        if not row:
            raise HTTPException(404, "job not found")
        metrics = agent.t.get_metrics(job_id)
        return {"job": dict(row), "metrics": metrics}

    @app.post("/webhooks/stripe")
    async def stripe_webhook(request: Request):
        payload = await request.body()
        sig = request.headers.get("Stripe-Signature", "")
        payment = stripe.process_webhook(payload, sig, treasury=agent.t)
        if payment and payment.get("job_id"):
            agent._runner.handle_webhook_payment(payment)
        return {"received": True}

    @app.get("/briefs/{job_id}")
    def get_brief(job_id: str, token: str = ""):
        if not verify_delivery_token(job_id, token):
            raise HTTPException(403, "invalid or expired delivery token")
            
        path_html = Path(__file__).resolve().parent.parent / "data" / "reports" / f"{job_id}.html"
        if path_html.is_file():
            return HTMLResponse(path_html.read_text(encoding="utf-8"))
            
        path_md = Path(__file__).resolve().parent.parent / "data" / "reports" / f"{job_id}.md"
        if path_md.is_file():
            return HTMLResponse(markdown_to_html(path_md.read_text(encoding="utf-8")))
            
        reports_dir = Path(__file__).resolve().parent.parent / "data" / "reports"
        if reports_dir.is_dir():
            for p in reports_dir.glob("*"):
                if job_id in p.stem and p.suffix in (".html", ".md"):
                    if p.suffix == ".html":
                        return HTMLResponse(p.read_text(encoding="utf-8"))
                    else:
                        return HTMLResponse(markdown_to_html(p.read_text(encoding="utf-8")))
                        
        raise HTTPException(404, "brief not found")

    @app.get("/api/briefs")
    def list_briefs():
        reports_dir = Path(__file__).resolve().parent.parent / "data" / "reports"
        if not reports_dir.is_dir():
            return []
        stems = set()
        for p in reports_dir.glob("*"):
            if p.suffix in (".md", ".html") and p.stem != ".gitkeep":
                stems.add(p.stem)
        return sorted(list(stems))

    @app.get("/api/briefs/{job_id}")
    def get_brief_api(job_id: str):
        path_html = Path(__file__).resolve().parent.parent / "data" / "reports" / f"{job_id}.html"
        if path_html.is_file():
            return HTMLResponse(path_html.read_text(encoding="utf-8"))
            
        path_md = Path(__file__).resolve().parent.parent / "data" / "reports" / f"{job_id}.md"
        if path_md.is_file():
            return HTMLResponse(markdown_to_html(path_md.read_text(encoding="utf-8")))
            
        reports_dir = Path(__file__).resolve().parent.parent / "data" / "reports"
        if reports_dir.is_dir():
            for p in reports_dir.glob("*"):
                if job_id in p.stem and p.suffix in (".html", ".md"):
                    if p.suffix == ".html":
                        return HTMLResponse(p.read_text(encoding="utf-8"))
                    else:
                        return HTMLResponse(markdown_to_html(p.read_text(encoding="utf-8")))
                        
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
