"""Hosted brief delivery and optional SMTP email."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

OUTBOX_DIR = Path(__file__).resolve().parent.parent / "data" / "outbox"
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_PLACEHOLDER_DELIVERY_SECRETS = {
    "solvent-dev-secret-change-me",
    "change-me-in-production",
}


def is_safe_job_id(job_id: str) -> bool:
    return bool(_SAFE_JOB_ID.fullmatch(job_id))


def is_safe_job_id(job_id: str) -> bool:
    """Reject path traversal in brief URLs."""
    return bool(job_id) and "/" not in job_id and ".." not in job_id and "\\" not in job_id


def _delivery_secret() -> str:
    secret = os.environ.get("SOLVENT_DELIVERY_SECRET", "").strip()
    if len(secret) < 32 or secret in _PLACEHOLDER_DELIVERY_SECRETS:
        raise RuntimeError(
            "SOLVENT_DELIVERY_SECRET must be set to a non-placeholder value "
            "with at least 32 characters"
        )
    return secret


def make_delivery_token(job_id: str, ts: float | None = None) -> str:
    if not is_safe_job_id(job_id):
        raise ValueError("unsafe job_id")
    ts = ts or time.time()
    payload = f"{job_id}:{int(ts)}"
    sig = hmac.new(_delivery_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{int(ts)}.{sig}"


def verify_delivery_token(job_id: str, token: str, max_age_seconds: int = 7 * 86400) -> bool:
    if not is_safe_job_id(job_id) or not token or "." not in token:
        return False
    ts_str, sig = token.split(".", 1)
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    if abs(time.time() - ts) > max_age_seconds:
        return False
    payload = f"{job_id}:{ts}"
    try:
        secret = _delivery_secret()
    except RuntimeError:
        return False
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def hosted_brief_url(base_url: str, job_id: str) -> str:
    base = base_url.rstrip("/")
    token = make_delivery_token(job_id)
    return f"{base}/briefs/{job_id}?token={token}"


def markdown_to_html(md: str) -> str:
    """Minimal markdown → HTML without external deps."""
    lines = md.splitlines()
    html_lines: list[str] = []
    for line in lines:
        if line.startswith("# "):
            html_lines.append(f"<h1>{_esc(line[2:])}</h1>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{_esc(line[3:])}</h2>")
        elif line.startswith("### "):
            html_lines.append(f"<h3>{_esc(line[4:])}</h3>")
        elif line.startswith("- "):
            html_lines.append(f"<li>{_esc(line[2:])}</li>")
        elif line.strip() == "":
            html_lines.append("<br/>")
        else:
            html_lines.append(f"<p>{_esc(line)}</p>")
    body = "\n".join(html_lines)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>SOLVENT Research Brief</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;"
        "line-height:1.6;color:#1a1a2e}h1{color:#0f3460}h2{color:#16213e}</style>"
        "</head><body>" + body + "</body></html>"
    )


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def send_brief_email(
    to_email: str,
    job_id: str,
    brief_path: Path,
    hosted_url: str,
) -> dict:
    """Send brief via SMTP or write to outbox in simulate mode."""
    text = brief_path.read_text(encoding="utf-8") if brief_path.is_file() else ""
    subject = f"Your SOLVENT research brief ({job_id})"
    body = (
        f"Your research brief is ready.\n\n"
        f"View online: {hosted_url}\n\n"
        f"---\n{text[:8000]}"
    )
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
        eml_path = OUTBOX_DIR / f"{job_id}.eml"
        eml_path.write_text(
            f"To: {to_email}\nSubject: {subject}\n\n{body}",
            encoding="utf-8",
        )
        return {"simulated": True, "path": str(eml_path), "to": to_email}
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    from_addr = os.environ.get("SMTP_FROM", user or "agent@solvent.local")
    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        if user and password:
            server.login(user, password)
        server.sendmail(from_addr, [to_email], msg.as_string())
    return {"simulated": False, "to": to_email}
