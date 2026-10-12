"""Tests for the PDF deliverable (solvent.pdf) and its delivery."""

from __future__ import annotations

import io
from pathlib import Path
from unittest import mock

import pytest

from solvent import pdf

BRIEF = """# Research Brief: AI inference chips, 2026

## Executive summary
Demand is up 24% year on year.

## Key data points
- Market growing 18-24% YoY.
- A bullet long enough to need wrapping: """ + "detail " * 40 + """

## Recommendation
Selective exposure.
"""


def _xref_offsets(data: bytes) -> list[int]:
    """Every offset in the xref table, which readers reject if wrong."""
    tail = data.rsplit(b"startxref\n", 1)[1]
    start = int(tail.split(b"\n")[0])
    rows = data[start:].split(b"\n")
    count = int(rows[1].split()[1])
    return [int(row.split()[0]) for row in rows[3 : count + 2]]


def test_a_rendered_brief_is_a_well_formed_pdf():
    data = pdf.render(BRIEF, title="J1")
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")
    assert b"/Type /Catalog" in data
    assert b"/Type /Pages" in data


def test_every_xref_offset_points_at_its_own_object():
    data = pdf.render(BRIEF, title="J1")
    for number, offset in enumerate(_xref_offsets(data), start=1):
        assert data[offset:].startswith(f"{number} 0 obj".encode())


def test_a_long_brief_paginates():
    long_brief = "# Big\n\n" + "\n\n".join(f"Paragraph {i} with figures {i}%." for i in range(200))
    data = pdf.render(long_brief)
    assert data.count(b"/Type /Page ") > 1
    assert b"page 1 of" in data


def test_an_empty_brief_still_produces_one_page():
    data = pdf.render("")
    assert data.startswith(b"%PDF")
    assert data.count(b"/Type /Page ") == 1


def test_pdf_syntax_in_the_text_is_escaped():
    data = pdf.render("# Title\n\nA (parenthesis) and a backslash \\ in the body.")
    assert rb"\(parenthesis\)" in data
    assert rb"\\" in data


def test_non_latin1_text_is_transliterated_not_crashed():
    data = pdf.render("# Café — naïve\n\nAn em dash — and an arrow → survive.")
    assert data.startswith(b"%PDF")
    assert b"-" in data  # the em dash became a hyphen


def test_headings_use_the_bold_font():
    data = pdf.render("# Heading\n\nBody text.")
    assert b"/F2" in data  # Helvetica-Bold is set for the heading
    assert b"/F1" in data


def test_layout_wraps_long_lines_and_keeps_bullets():
    lines = pdf.layout("- " + "word " * 60)
    assert len(lines) > 1
    assert lines[0].text.startswith("- ")
    assert lines[1].text.startswith("  ")


def test_layout_strips_markdown_emphasis():
    assert "**" not in pdf.layout("Some **bold** and `code`.")[0].text


def test_the_footer_carries_the_job():
    data = pdf.render(BRIEF, footer="SOLVENT - J1")
    assert b"SOLVENT - J1" in data


@pytest.mark.skipif(
    not Path("/usr/local/lib/python3.11/dist-packages/pypdf").exists(),
    reason="pypdf not installed",
)
def test_an_independent_parser_reads_the_output():
    import sys
    import types

    # This machine's `cryptography` build is broken; pypdf only needs it for
    # encrypted files, so stub the import out to use the parser itself.
    fake = types.ModuleType("cryptography")
    fake.exceptions = types.ModuleType("cryptography.exceptions")
    fake.exceptions.UnsupportedAlgorithm = type("UnsupportedAlgorithm", (Exception,), {})
    sys.modules.setdefault("cryptography", fake)
    sys.modules.setdefault("cryptography.exceptions", fake.exceptions)
    try:
        from pypdf import PdfReader
    except Exception:  # pragma: no cover - environment dependent
        pytest.skip("pypdf unusable here")

    reader = PdfReader(io.BytesIO(pdf.render(BRIEF, title="J1 brief")))
    assert len(reader.pages) >= 1
    assert "Executive summary" in reader.pages[0].extract_text()
    assert reader.metadata.title == "J1 brief"


# --- fulfilment and delivery ------------------------------------------------


def _tool_ctx():
    return type("C", (), {"total_calls": 0, "market_data_calls": 0, "web_search_calls": 0})()


def test_fulfilment_renders_the_pdf_it_charges_for(tmp_path, monkeypatch):
    from solvent import service

    monkeypatch.setattr(service, "OUTPUT_DIR", tmp_path)
    with mock.patch(
        "solvent.nemotron.research_brief",
        return_value=(BRIEF, {"total_tokens": 300}, _tool_ctx()),
    ):
        result = service.fulfill({"id": "J1", "topic": "AI inference chips, 2026"})

    assert result["pdf_path"]
    rendered = Path(result["pdf_path"])
    assert rendered.is_file()
    assert rendered.read_bytes().startswith(b"%PDF")
    # The vendor line that pays for it is still billed.
    assert any(vendor == "pdf-render-saas" for vendor, _, _ in result["resources_used"])


def test_a_pdf_failure_never_loses_a_paid_brief(tmp_path, monkeypatch):
    from solvent import service

    monkeypatch.setattr(service, "OUTPUT_DIR", tmp_path)
    with (
        mock.patch(
            "solvent.nemotron.research_brief",
            return_value=(BRIEF, {"total_tokens": 300}, _tool_ctx()),
        ),
        mock.patch("solvent.pdf.render", side_effect=RuntimeError("render blew up")),
    ):
        result = service.fulfill({"id": "J1", "topic": "AI inference chips"})

    assert result["pdf_path"] is None
    assert Path(result["deliverable_path"]).is_file()


def test_the_brief_email_links_and_attaches_the_pdf(tmp_path, monkeypatch):
    from solvent import delivery

    monkeypatch.setattr(delivery, "OUTBOX_DIR", tmp_path / "outbox")
    brief = tmp_path / "J1.md"
    brief.write_text(BRIEF, encoding="utf-8")
    rendered = tmp_path / "J1.pdf"
    rendered.write_bytes(pdf.render(BRIEF))

    result = delivery.send_brief_email("a@x.example", "J1", brief, "https://host/briefs/J1?token=t", rendered)
    body = Path(result["path"]).read_text(encoding="utf-8")

    assert result["attached"] is True
    assert "format=pdf" in body
    assert "X-Attachment: J1.pdf" in body


def test_without_a_pdf_the_email_does_not_promise_one(tmp_path, monkeypatch):
    from solvent import delivery

    monkeypatch.setattr(delivery, "OUTBOX_DIR", tmp_path / "outbox")
    brief = tmp_path / "J1.md"
    brief.write_text(BRIEF, encoding="utf-8")

    result = delivery.send_brief_email("a@x.example", "J1", brief, "https://host/briefs/J1?token=t")
    body = Path(result["path"]).read_text(encoding="utf-8")

    assert result["attached"] is False
    assert "format=pdf" not in body


def test_smtp_delivery_attaches_the_file(tmp_path, monkeypatch):
    from solvent import delivery

    brief = tmp_path / "J1.md"
    brief.write_text(BRIEF, encoding="utf-8")
    rendered = tmp_path / "J1.pdf"
    rendered.write_bytes(pdf.render(BRIEF))

    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    sent: dict = {}

    class _Server:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            pass

        def login(self, *args):
            pass

        def sendmail(self, from_addr, to, message):
            sent["message"] = message

    with mock.patch("solvent.delivery.smtplib.SMTP", return_value=_Server()):
        result = delivery.send_brief_email(
            "a@x.example", "J1", brief, "https://host/briefs/J1?token=t", rendered
        )

    assert result["attached"] is True
    assert "application/pdf" in sent["message"]
    assert 'filename="J1.pdf"' in sent["message"]


def test_the_hosted_route_serves_the_pdf(tmp_path, monkeypatch):
    """The same token-gated route serves the PDF, so no new public surface."""
    fastapi = pytest.importorskip("fastapi")
    del fastapi
    from fastapi.testclient import TestClient

    from solvent.delivery import make_delivery_token
    from solvent.server import create_app

    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_DELIVERY_SECRET", "x" * 40)
    monkeypatch.setenv("SOLVENT_DASHBOARD_TOKEN", "d" * 40)

    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "J1.pdf").write_bytes(pdf.render(BRIEF, title="J1"))
    (reports / "J1.html").write_text("<p>html brief</p>", encoding="utf-8")

    client = TestClient(create_app())
    token = make_delivery_token("J1")

    served = client.get(f"/briefs/J1?token={token}&format=pdf")
    assert served.status_code == 200
    assert served.headers["content-type"] == "application/pdf"
    assert served.content.startswith(b"%PDF")

    # Without the format the HTML brief is still what you get.
    assert "html brief" in client.get(f"/briefs/J1?token={token}").text

    # And the PDF is no less protected than the brief itself.
    assert client.get("/briefs/J1?format=pdf").status_code == 403
    assert client.get(f"/briefs/MISSING?token={make_delivery_token('MISSING')}&format=pdf").status_code == 404
