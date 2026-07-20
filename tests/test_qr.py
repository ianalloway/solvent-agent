"""Tests for the optional-dependency QR pairing module (solvent/qr.py).

qr.py deliberately degrades gracefully when the optional ``qrcode`` package is
not installed: render_token() still builds the pairing payload/URI and prints a
hint, while png_bytes()/_ascii_qr() return None/"" respectively. These tests
cover that logic and stay green whether or not qrcode is present.
"""

from __future__ import annotations

import importlib.util

import pytest

from solvent.qr import _ascii_qr, png_bytes, render_token

_QRCODE_AVAILABLE = importlib.util.find_spec("qrcode") is not None


def test_render_token_no_host_omits_uri() -> None:
    out = render_token("TOK123")
    assert "Pairing token: TOK123" in out
    assert "URI:" not in out
    assert "OpenClaw app" in out


def test_render_token_uri_uses_tls_for_https_ports() -> None:
    for port in (443, 8443):
        out = render_token("TOK123", host="pair.example.com", port=port)
        assert (
            f"URI: solvent://pair?host=pair.example.com&port={port}&tls=1&token=TOK123"
            in out
        )


def test_render_token_uri_omits_tls_for_other_ports() -> None:
    out = render_token("TOK123", host="pair.example.com", port=8080)
    assert (
        "URI: solvent://pair?host=pair.example.com&port=8080&tls=0&token=TOK123"
        in out
    )


@pytest.mark.skipif(_QRCODE_AVAILABLE, reason="qrcode installed: PNG path covered below")
def test_png_bytes_returns_none_when_qrcode_absent() -> None:
    assert png_bytes("TOK123") is None
    assert png_bytes("TOK123", host="h", port=443) is None


@pytest.mark.skipif(not _QRCODE_AVAILABLE, reason="qrcode not installed in this env")
def test_png_bytes_returns_png_when_qrcode_present() -> None:
    data = png_bytes("TOK123")
    assert data is not None
    assert data[:4] == b"\x89PNG"


@pytest.mark.skipif(_QRCODE_AVAILABLE, reason="qrcode installed")
def test_ascii_qr_empty_when_qrcode_absent() -> None:
    assert _ascii_qr("anything") == ""


@pytest.mark.skipif(not _QRCODE_AVAILABLE, reason="qrcode not installed in this env")
def test_ascii_qr_nonempty_when_qrcode_present() -> None:
    assert _ascii_qr("anything") != ""
