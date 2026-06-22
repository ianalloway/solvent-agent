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


def _delivery_secret() -> str:
    return os.environ.get("SOLVENT_DELIVERY_SECRET", "solvent-dev-secret-change-me")


def make_delivery_token(job_id: str, ts: float | None = None) -> str:
    ts = ts or time.time()
    payload = f"{job_id}:{int(ts)}"
    sig = hmac.new(_delivery_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{int(ts)}.{sig}"


def verify_delivery_token(job_id: str, token: str, max_age_seconds: int = 7 * 86400) -> bool:
    if not token or "." not in token:
        return False
    ts_str, sig = token.split(".", 1)
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    if abs(time.time() - ts) > max_age_seconds:
        return False
    payload = f"{job_id}:{ts}"
    expected = hmac.new(_delivery_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def hosted_brief_url(base_url: str, job_id: str) -> str:
    base = base_url.rstrip("/")
    token = make_delivery_token(job_id)
    return f"{base}/briefs/{job_id}?token={token}"


def _parse_bold(text: str) -> str:
    escaped = _esc(text)
    return re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', escaped)


def markdown_to_html(
    md: str,
    job_id: str | None = None,
    topic: str | None = None,
    timestamp: float | None = None,
) -> str:
    """Minimal markdown → HTML without external deps, formatted beautifully."""
    # Try to extract topic from first H1 if topic is None
    if not topic and md.startswith("# "):
        first_line = md.splitlines()[0]
        topic = first_line[2:].strip()
        if topic.startswith("Research Brief: Research topic:"):
            topic = topic.replace("Research Brief: Research topic:", "").strip()
        elif topic.startswith("Research Brief:"):
            topic = topic.replace("Research Brief:", "").strip()

    # Generate collapsible metadata block
    gen_time = timestamp or time.time()
    date_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(gen_time))
    
    rows = []
    if job_id:
        rows.append(f"<div style='font-weight:600;'>Job ID:</div><div style='font-family:monospace;'>{_esc(job_id)}</div>")
    if topic:
        rows.append(f"<div style='font-weight:600;'>Topic:</div><div>{_esc(topic)}</div>")
    rows.append(f"<div style='font-weight:600;'>Generated At:</div><div>{date_str}</div>")
    
    metadata_rows = "\n".join(rows)
    metadata_html = f"""
    <details class="metadata-details" style="margin-bottom: 2rem; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 12px; padding: 0.75rem 1rem; cursor: pointer; transition: background 0.2s;">
      <summary style="font-weight: 600; font-size: 0.8rem; color: #fbbf24; outline: none; list-style: none; display: flex; justify-content: space-between; align-items: center;">
        <span style="display: flex; align-items: center; gap: 0.5rem;">📊 RESEARCH METADATA</span>
        <span class="chevron" style="font-size: 0.7rem; transition: transform 0.2s; display: inline-block;">▼</span>
      </summary>
      <div style="margin-top: 0.75rem; font-size: 0.75rem; color: #9ca3af; display: grid; grid-template-columns: auto 1fr; gap: 0.5rem 1rem; border-top: 1px solid rgba(255,255,255,0.04); padding-top: 0.75rem;">
        {metadata_rows}
      </div>
    </details>
    """

    lines = md.splitlines()
    html_lines: list[str] = []
    
    in_table = False
    in_list = False
    
    for line in lines:
        line_stripped = line.strip()
        
        # Handle lists
        if line_stripped.startswith("- "):
            if in_table:
                html_lines.append("</table></div>")
                in_table = False
            if not in_list:
                html_lines.append("<ul style='list-style-type:disc; padding-left:1.5rem; margin-bottom:1.25rem;'>")
                in_list = True
            content = _parse_bold(line_stripped[2:])
            html_lines.append(f"<li style='margin-bottom:0.5rem; color:#9ca3af;'>{content}</li>")
            continue
        elif in_list:
            html_lines.append("</ul>")
            in_list = False
 
        # Handle tables
        if line_stripped.startswith("|") and line_stripped.endswith("|"):
            if not in_list:
                pass
            else:
                html_lines.append("</ul>")
                in_list = False
                
            if not in_table:
                html_lines.append("<div style='overflow-x:auto; margin:1.5rem 0;'><table style='width:100%; border-collapse:collapse; background:rgba(0,0,0,0.2); border-radius:12px; overflow:hidden;'>")
                in_table = True
            cells = [c.strip() for c in line_stripped.split("|")[1:-1]]
            
            # If it's a separator line (like |---|---|), skip it
            if all(all(char in "- :" for char in cell) for cell in cells):
                continue
            
            # Determine if this is a header row
            is_header = len(html_lines) > 0 and html_lines[-1].startswith("<div style='overflow-x:auto")
            tag = "th" if is_header else "td"
            style = "padding:0.75rem 1rem; border:1px solid rgba(255,255,255,0.08); text-align:left;"
            if is_header:
                style += " background:rgba(255,255,255,0.03); font-weight:600; color:#ffffff;"
            else:
                style += " color:#9ca3af;"
                
            row_html = "<tr>"
            for cell in cells:
                cell_parsed = _parse_bold(cell)
                row_html += f"<{tag} style='{style}'>{cell_parsed}</{tag}>"
            row_html += "</tr>"
            html_lines.append(row_html)
            continue
        elif in_table:
            html_lines.append("</table></div>")
            in_table = False
 
        # Handle headers
        if line_stripped.startswith("# "):
            html_lines.append(f"<h1 style='font-size:1.875rem; font-weight:800; color:#ffffff; margin-top:0; margin-bottom:1rem; line-height:1.2;'>{_esc(line_stripped[2:])}</h1>")
        elif line_stripped.startswith("## "):
            html_lines.append(f"<h2 style='font-size:1.35rem; font-weight:700; color:#ffffff; margin-top:2rem; margin-bottom:1rem; border-bottom:1px solid rgba(255,255,255,0.04); padding-bottom:0.5rem;'>{_esc(line_stripped[3:])}</h2>")
        elif line_stripped.startswith("### "):
            html_lines.append(f"<h3 style='font-size:1.1rem; font-weight:600; color:#f3f4f6; margin-top:1.5rem; margin-bottom:0.75rem;'>{_esc(line_stripped[4:])}</h3>")
        elif line_stripped == "":
            html_lines.append("<br/>")
        else:
            content = _parse_bold(line)
            html_lines.append(f"<p style='margin-top:0; margin-bottom:1.25rem; color:#9ca3af;'>{content}</p>")
            
    if in_list:
        html_lines.append("</ul>")
    if in_table:
        html_lines.append("</table></div>")
        
    body = "\n".join(html_lines)
    
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset='utf-8'>
  <title>SOLVENT Research Brief</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    body {{
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      background-color: #070a13;
      color: #d1d5db;
      max-width: 800px;
      margin: 0 auto;
      padding: 2rem 1.5rem;
      line-height: 1.75;
    }}
    .brief-card {{
      background: linear-gradient(180deg, #0f1322 0%, #0c0f1b 100%);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 24px;
      padding: 2.5rem;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
    }}
    .header-bar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      padding-bottom: 1.25rem;
      margin-bottom: 2rem;
    }}
    .logo {{
      font-weight: 800;
      font-size: 1.15rem;
      color: #fbbf24;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }}
    .badge {{
      background: rgba(251, 191, 36, 0.08);
      border: 1px solid rgba(251, 191, 36, 0.25);
      color: #fbbf24;
      padding: 0.25rem 0.75rem;
      border-radius: 9999px;
      font-size: 0.7rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .action-buttons {{
      display: flex;
      gap: 0.75rem;
      margin-top: 2rem;
      justify-content: flex-end;
    }}
    .btn {{
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.08);
      color: #ffffff;
      padding: 0.5rem 1rem;
      border-radius: 12px;
      font-size: 0.8rem;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      transition: all 0.2s;
    }}
    .btn:hover {{
      background: rgba(255, 255, 255, 0.08);
      border-color: rgba(255, 255, 255, 0.15);
    }}
    details[open] .chevron {{
      transform: rotate(180deg);
    }}
    @media print {{
      body {{
        background-color: #ffffff;
        color: #000000;
        padding: 0;
      }}
      .brief-card {{
        border: none;
        box-shadow: none;
        padding: 0;
        background: none;
      }}
      .btn, .action-buttons, .metadata-details {{
        display: none;
      }}
      h1, h2, h3 {{
        color: #000000 !important;
      }}
      p, li {{
        color: #333333 !important;
      }}
    }}
  </style>
</head>
<body>
  <div class="brief-card">
    <div class="header-bar">
      <div class="logo">🪙 SOLVENT BRIEF</div>
      <div class="badge">Verified Research</div>
    </div>
    
    {metadata_html}
    
    <div id="brief-content">
{body}
    </div>
 
    <div class="action-buttons">
      <button id="copy-btn" class="btn" onclick="copyBrief()">
        Copy Content
      </button>
      <button class="btn" onclick="window.print()">
        Print to PDF
      </button>
    </div>
  </div>
 
  <script>
    function copyBrief() {{
      const content = document.getElementById('brief-content').innerText;
      navigator.clipboard.writeText(content).then(() => {{
        const btn = document.getElementById('copy-btn');
        const originalText = btn.innerHTML;
        btn.innerHTML = '✓ Copied!';
        setTimeout(() => {{
          btn.innerHTML = originalText;
        }}, 2000);
      }});
    }}
  </script>
</body>
</html>"""


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
