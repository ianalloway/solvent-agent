"""
pdf.py — render a brief as a PDF, with no dependencies.

The cost model has charged `pdf-render-saas` on every single job since the
first commit, and no PDF was ever produced. That is the kind of line item that
makes a P&L fiction: the agent was paying itself for a deliverable it did not
deliver, and the customer got markdown.

This writes the PDF by hand. A PDF is a text container with a byte-offset
table, so a single-font document needs no library — just care with the xref
offsets, which must be exact or readers reject the file. Base-14 Helvetica is
assumed present in every reader, so nothing is embedded and the output stays
small.

It is deliberately plain: headings, paragraphs, bullets and page numbers. A
research brief is read, not admired, and a renderer nobody can audit is worse
than a plain one everybody can.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Page geometry, in PostScript points (72 per inch) — US Letter.
PAGE_WIDTH = 612
PAGE_HEIGHT = 792
MARGIN = 54

BODY_SIZE = 10.5
BODY_LEADING = 15.0
#: Helvetica at 10.5pt averages ~5.2pt per character; this is the wrap width
#: that keeps a line inside the margins without measuring glyphs.
BODY_CHARS = 95

HEADING_SIZES = {1: 17.0, 2: 13.0, 3: 11.5}

_LINES_PER_PAGE = int((PAGE_HEIGHT - 2 * MARGIN) / BODY_LEADING)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|`)")


@dataclass
class _Line:
    """One laid-out line: its text, the font to set, and the size."""

    text: str
    bold: bool = False
    size: float = BODY_SIZE
    space_before: float = 0.0


def _escape(text: str) -> str:
    """Escape a PDF literal string."""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _latin1(text: str) -> str:
    """Base-14 fonts are Latin-1; anything else is transliterated or dropped."""
    swaps = {
        "—": "-",
        "–": "-",
        "…": "...",
        "·": "-",
        "→": "->",
        "←": "<-",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "█": "#",
        "✓": "ok",
        "⚠": "!",
    }
    for bad, good in swaps.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


def _wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap that never loses a long word."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def layout(markdown_text: str) -> list[_Line]:
    """Turn brief markdown into laid-out lines. Headings and bullets only."""
    lines: list[_Line] = []
    for raw in (markdown_text or "").splitlines():
        stripped = raw.strip()
        if not stripped:
            lines.append(_Line("", space_before=BODY_LEADING * 0.4))
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            level = min(len(heading.group(1)), 3)
            size = HEADING_SIZES[level]
            body = _EMPHASIS_RE.sub("", heading.group(2)).strip()
            for index, piece in enumerate(_wrap(body, int(BODY_CHARS * BODY_SIZE / size))):
                lines.append(
                    _Line(
                        piece,
                        bold=True,
                        size=size,
                        space_before=BODY_LEADING * 0.6 if index == 0 else 0.0,
                    )
                )
            continue

        bullet = _BULLET_RE.match(stripped)
        if bullet:
            body = _EMPHASIS_RE.sub("", bullet.group(1)).strip()
            wrapped = _wrap(body, BODY_CHARS - 4)
            lines.append(_Line(f"- {wrapped[0]}"))
            lines.extend(_Line(f"  {piece}") for piece in wrapped[1:])
            continue

        body = _EMPHASIS_RE.sub("", stripped)
        lines.extend(_Line(piece) for piece in _wrap(body, BODY_CHARS))
    return lines


def paginate(lines: list[_Line]) -> list[list[_Line]]:
    """Break laid-out lines into pages, never emitting an empty document."""
    pages: list[list[_Line]] = [[]]
    used = 0.0
    limit = PAGE_HEIGHT - 2 * MARGIN
    for line in lines:
        height = line.space_before + max(line.size + 4.5, BODY_LEADING)
        if used + height > limit and pages[-1]:
            pages.append([])
            used = 0.0
            # A heading's leading space is meaningless at the top of a page.
            line = _Line(line.text, line.bold, line.size)
            height = max(line.size + 4.5, BODY_LEADING)
        pages[-1].append(line)
        used += height
    return pages


def _content_stream(page: list[_Line], *, page_number: int, total: int, footer: str) -> bytes:
    """The drawing operators for one page."""
    out = ["BT", f"1 0 0 1 {MARGIN} {PAGE_HEIGHT - MARGIN} Tm"]
    current_font: tuple[str, float] | None = None
    for line in page:
        if line.space_before:
            out.append(f"0 -{line.space_before:.2f} Td")
        font = ("F2" if line.bold else "F1", line.size)
        if font != current_font:
            out.append(f"/{font[0]} {font[1]:.2f} Tf")
            current_font = font
        leading = max(line.size + 4.5, BODY_LEADING)
        out.append(f"{leading:.2f} TL")
        out.append(f"({_escape(_latin1(line.text))}) Tj")
        out.append("T*")
    out.append("ET")

    label = (
        f"{footer}  ·  page {page_number} of {total}"
        if footer
        else f"page {page_number} of {total}"
    )
    out += [
        "BT",
        "/F1 8.00 Tf",
        f"1 0 0 1 {MARGIN} {MARGIN - 18} Tm",
        "0.45 0.45 0.45 rg",
        f"({_escape(_latin1(label))}) Tj",
        "ET",
    ]
    return "\n".join(out).encode("latin-1", "replace")


def render(markdown_text: str, *, title: str = "", footer: str = "") -> bytes:
    """Render brief markdown as a complete PDF document."""
    pages = paginate(layout(markdown_text))
    total = len(pages)

    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)  # object numbers are 1-based

    font_regular = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    font_bold = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    )

    # The pages object has to know its kids before they exist, so reserve its
    # number now and fill the body in once the page objects are numbered.
    pages_obj = add(b"")
    page_numbers: list[int] = []
    for index, page in enumerate(pages, start=1):
        stream = _content_stream(page, page_number=index, total=total, footer=footer)
        content = add(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        page_numbers.append(
            add(
                f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox "
                f"[0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] /Resources << /Font "
                f"<< /F1 {font_regular} 0 R /F2 {font_bold} 0 R >> >> "
                f"/Contents {content} 0 R >>".encode("latin-1")
            )
        )
    kids = " ".join(f"{number} 0 R" for number in page_numbers)
    objects[pages_obj - 1] = f"<< /Type /Pages /Count {total} /Kids [{kids}] >>".encode("latin-1")

    catalog = add(f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode("latin-1"))
    info = add(
        b"<< /Title ("
        + _escape(_latin1(title or "Research brief")).encode("latin-1")
        + b") /Producer (SOLVENT) >>"
    )

    # Assemble, tracking the byte offset of every object for the xref table.
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)
