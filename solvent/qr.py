"""QR code generation for OpenClaw pairing."""

from __future__ import annotations

import io


def _ascii_qr(data: str) -> str:
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(data)
        qr.make(fit=True)
        f = io.StringIO()
        qr.print_ascii(out=f)
        return f.getvalue()
    except ImportError:
        return ""


def _png_bytes(data: str) -> bytes | None:
    try:
        import qrcode
        img = qrcode.make(data)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        return None


def render_token(token: str, host: str = "", port: int = 0) -> str:
    """Return a text block with ASCII QR (if qrcode installed) + plain token."""
    payload = token
    if host:
        tls = "1" if port in (443, 8443) else "0"
        payload = f"solvent://pair?host={host}&port={port}&tls={tls}&token={token}"

    lines = [f"Pairing token: {token}"]
    if payload != token:
        lines.append(f"URI: {payload}")

    ascii_art = _ascii_qr(payload)
    if ascii_art:
        lines.append("")
        lines.append(ascii_art)
    else:
        lines.append("(Install qrcode[pil] for a scannable QR image)")

    lines.append(f"\nEnter this token in the OpenClaw app → Gateway Auth Token field.")
    return "\n".join(lines)


def png_bytes(token: str, host: str = "", port: int = 0) -> bytes | None:
    """Return raw PNG bytes for the QR code, or None if qrcode not installed."""
    payload = token
    if host:
        tls = "1" if port in (443, 8443) else "0"
        payload = f"solvent://pair?host={host}&port={port}&tls={tls}&token={token}"
    return _png_bytes(payload)
