"""The client's monthly Google Ads report, as a PDF.

Deliberately plain reportlab and the same palette `modules/scans/report_pdf.py`
uses, so a site audit and a performance report arriving in the same inbox look
like they came from the same company. No HTML-to-PDF engine, no headless
browser, no new dependency — the standing rule on this project.

Two things it will not draw.

**A comparison nothing measured.** Where the earlier window refused, the
change column is a dash and the page says which window could not be read,
rather than printing an arrow over a month nobody counted.

**A figure with no caveat under it.** Conversions are what the account's own
tracking recorded, and a client reading a conversion count as "enquiries" is
reading a different number from the one on the page.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas as _canvas

NAVY = HexColor("#0A2240")
BLUE = HexColor("#009ED2")
GREEN = HexColor("#2DBB72")
RED = HexColor("#DC2626")
INK = HexColor("#25364B")
MUTED = HexColor("#68798C")
LINE = HexColor("#DBE5ED")
WHITE = HexColor("#FFFFFF")

M = 50
PAGE_W, PAGE_H = letter
W = PAGE_W - M * 2
BOTTOM = PAGE_H - 56


def _clean(text) -> str:
    s = "" if text is None else str(text)
    for bad, good in (("’", "'"), ("‘", "'"), ("“", '"'),
                      ("”", '"'), ("—", "-"), ("–", "-"),
                      ("…", "...")):
        s = s.replace(bad, good)
    return " ".join(s.split())


def _money(value, prefix="$") -> str:
    if value is None:
        return "not measured"
    if prefix == "$":
        return f"${value:,.2f}"
    return f"{value:,.0f}" if float(value) == int(value) else f"{value:,.2f}"


class Doc:
    def __init__(self, title: str, subtitle: str):
        self.buf = io.BytesIO()
        self.c = _canvas.Canvas(self.buf, pagesize=letter)
        self.c.setTitle(title)
        self.c.setAuthor("Smart 1 Marketing")
        self.c.setSubject(subtitle)
        self.subtitle = subtitle
        self.page = 0
        self.y = 0.0
        self._new_page(first=True)

    def _new_page(self, first=False):
        self.page += 1
        self.c.setFillColor(BLUE)
        self.c.rect(0, PAGE_H - 7, PAGE_W, 7, stroke=0, fill=1)
        self.y = 46 if first else 52
        self.c.setFont("Helvetica", 8)
        self.c.setFillColor(MUTED)
        self.c.drawString(M, 30, _clean(self.subtitle)[:95])
        self.c.drawRightString(PAGE_W - M, 30, f"Page {self.page}")
        self.c.setStrokeColor(LINE)
        self.c.setLineWidth(0.6)
        self.c.line(M, 42, PAGE_W - M, 42)

    def _ty(self, y):
        return PAGE_H - y

    def need(self, height):
        if self.y + height > BOTTOM:
            self.c.showPage()
            self._new_page()

    def text(self, s, x=M, size=10, font="Helvetica", color=INK, width=None):
        s = _clean(s)
        if not s:
            return
        width = width or (PAGE_W - M - x)
        lines = simpleSplit(s, font, size, width)
        self.need(len(lines) * size * 1.35)
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        for i, line in enumerate(lines):
            self.c.drawString(x, self._ty(self.y + size + i * size * 1.35), line)
        self.y += len(lines) * size * 1.35

    def rule(self):
        self.c.setStrokeColor(LINE)
        self.c.setLineWidth(0.8)
        self.c.line(M, self._ty(self.y), PAGE_W - M, self._ty(self.y))
        self.y += 8

    def save(self) -> bytes:
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


def _cover(d: Doc, r: dict):
    d.c.setFillColor(NAVY)
    d.c.rect(0, PAGE_H - 118, PAGE_W, 118, stroke=0, fill=1)
    d.c.setFont("Helvetica-Bold", 9)
    d.c.setFillColor(BLUE)
    d.c.drawString(M, d._ty(34), "SMART 1 MARKETING")
    d.c.setFont("Helvetica-Bold", 22)
    d.c.setFillColor(WHITE)
    d.c.drawString(M, d._ty(62), _clean(r.get("client_name") or r.get("account_name")
                                        or "Google Ads performance"))
    d.c.setFont("Helvetica", 11)
    d.c.setFillColor(HexColor("#C7D8E8"))
    d.c.drawString(M, d._ty(84), f"Google Ads performance, {r.get('period_label', '')}")
    d.y = 142


def _headline(d: Doc, section: dict, compared: bool):
    d.text(section["title"], size=14, font="Helvetica-Bold", color=NAVY)
    d.y += 4
    for row in section["rows"]:
        d.need(30)
        d.c.setFont("Helvetica", 10)
        d.c.setFillColor(MUTED)
        d.c.drawString(M, d._ty(d.y + 10), _clean(row["label"]))
        d.c.setFont("Helvetica-Bold", 13)
        d.c.setFillColor(INK)
        d.c.drawRightString(PAGE_W - M - 120, d._ty(d.y + 11),
                            _money(row["value"], row["prefix"]))
        change = row.get("change_percent")
        d.c.setFont("Helvetica", 9)
        if change is None:
            # A dash rather than a zero, and the page says why below.
            d.c.setFillColor(MUTED)
            d.c.drawRightString(PAGE_W - M, d._ty(d.y + 11),
                                "-" if compared else "no comparison")
        else:
            d.c.setFillColor(GREEN if change >= 0 else RED)
            d.c.drawRightString(PAGE_W - M, d._ty(d.y + 11), f"{change:+.1f}%")
        d.y += 22
    d.y += 6
    if compared:
        d.text(f"Compared with {_clean(section.get('previous_label', ''))}"
               if section.get("previous_label") else
               "Compared with the previous 30 days.", size=8.5,
               font="Helvetica-Oblique", color=MUTED)
    else:
        # Never a silent absence: a report with no change column reads as a
        # first month, and this one may be a window that refused.
        d.text("The previous period could not be read, so nothing on this page "
               "is compared with it.", size=8.5, font="Helvetica-Oblique", color=MUTED)
    d.y += 10


def _campaigns(d: Doc, section: dict):
    d.need(40)
    d.text(section["title"], size=14, font="Helvetica-Bold", color=NAVY)
    d.y += 4
    if not section["rows"]:
        d.text("No campaign returned performance in this window.", size=10, color=MUTED)
        d.y += 8
        return
    cols = ((M, "Campaign", "l"), (M + 250, "Spend", "r"), (M + 330, "Clicks", "r"),
            (M + 400, "Conv.", "r"), (PAGE_W - M, "Cost/conv.", "r"))
    d.c.setFont("Helvetica-Bold", 8.5)
    d.c.setFillColor(MUTED)
    for x, label, align in cols:
        (d.c.drawString if align == "l" else d.c.drawRightString)(
            x, d._ty(d.y + 9), label)
    d.y += 14
    d.rule()
    for row in section["rows"]:
        d.need(18)
        d.c.setFont("Helvetica", 9)
        d.c.setFillColor(INK)
        d.c.drawString(M, d._ty(d.y + 9), _clean(row["name"])[:44])
        d.c.drawRightString(M + 250, d._ty(d.y + 9), _money(row["cost"]))
        d.c.drawRightString(M + 330, d._ty(d.y + 9), f"{row['clicks']:,}")
        d.c.drawRightString(M + 400, d._ty(d.y + 9), _money(row["conversions"], ""))
        d.c.drawRightString(PAGE_W - M, d._ty(d.y + 9),
                            _money(row["cost_per_conversion"]))
        d.y += 16
    d.y += 6
    if section.get("note"):
        d.text(section["note"], size=8.5, font="Helvetica-Oblique", color=MUTED)
        d.y += 6


def build(r: dict) -> bytes:
    """A finished report dict in, PDF bytes out."""
    name = r.get("client_name") or r.get("account_name") or "Google Ads"
    d = Doc(f"Google Ads performance - {name}",
            f"{name} · Google Ads performance · {r.get('period_label', '')}")
    _cover(d, r)
    for section in r.get("sections", []):
        if section["key"] == "headline":
            _headline(d, {**section, "previous_label": r.get("previous_label")},
                      bool(r.get("compared")))
        elif section["key"] == "campaigns":
            _campaigns(d, section)

    if r.get("errors"):
        # Named rather than left as a thin report: a page that quietly gets
        # shorter cannot be told from a month with less in it.
        d.need(40)
        d.rule()
        d.text("Not measured", size=11, font="Helvetica-Bold", color=NAVY)
        for key, message in r["errors"].items():
            d.text(f"{key}: {message}", size=8.5, color=MUTED)
        d.y += 8

    d.need(50)
    d.rule()
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    d.text(f"{_clean(r.get('caveat', ''))} Prepared by Smart 1 Marketing on {stamp}.",
           size=8, font="Helvetica-Oblique", color=MUTED, width=W)
    return d.save()
