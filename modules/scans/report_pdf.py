"""Branded PDF for a scan report — the version that gets emailed to a client.

Same content as the on-screen report, laid out for print in the Smart 1
palette (the one ``modules/proposal_builder/pdfgen`` already uses, so an audit
and a proposal arriving in the same inbox look like they came from the same
company).

Deliberately plain reportlab: no HTML-to-PDF engine, no headless browser, no
extra dependency. The layout is a top-down cursor with a page-break check
before every block, which is all a document of stacked cards needs.
"""
from __future__ import annotations

import io
import re
from datetime import datetime, timezone

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas as _canvas

NAVY = HexColor("#0A2240")
BLUE = HexColor("#009ED2")
GREEN = HexColor("#2DBB72")
GOLD = HexColor("#F0B429")
RED = HexColor("#DC2626")
INK = HexColor("#25364B")
MUTED = HexColor("#68798C")
LINE = HexColor("#DBE5ED")
BG = HexColor("#F3F7FA")
WHITE = HexColor("#FFFFFF")

STATE_COLOR = {"ok": GREEN, "bad": RED, "warn": GOLD,
               "info": HexColor("#9AAABC"), "unknown": HexColor("#9AAABC")}
STATE_MARK = {"ok": "✓", "bad": "×", "warn": "!", "info": "•",
              "unknown": "•"}

M = 50                        # page margin
PAGE_W, PAGE_H = letter       # 612 x 792
W = PAGE_W - M * 2
BOTTOM = PAGE_H - 56          # last usable y (from top)


def _clean(text) -> str:
    """PDF-safe single-line-ish text: no control characters, no stray markup."""
    s = "" if text is None else str(text)
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-").replace("…", "...")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


class Doc:
    """Top-down cursor over a reportlab canvas."""

    def __init__(self, title: str, subtitle: str):
        self.buf = io.BytesIO()
        self.c = _canvas.Canvas(self.buf, pagesize=letter)
        self.c.setTitle(title)
        self.c.setAuthor("Smart 1 Marketing")
        self.c.setSubject(subtitle)
        self.title = title
        self.subtitle = subtitle
        self.page = 0
        self.y = 0.0
        self._new_page(first=True)

    # ---- page plumbing --------------------------------------------------
    def _new_page(self, first=False):
        self.page += 1
        self.c.setFillColor(BLUE)
        self.c.rect(0, PAGE_H - 7, PAGE_W, 7, stroke=0, fill=1)
        self.y = 46 if first else 52
        self._footer()

    def _footer(self):
        self.c.setFont("Helvetica", 8)
        self.c.setFillColor(MUTED)
        self.c.drawString(M, 30, _clean(self.subtitle)[:95])
        self.c.drawRightString(PAGE_W - M, 30, f"Page {self.page}")
        self.c.setStrokeColor(LINE)
        self.c.setLineWidth(0.6)
        self.c.line(M, 42, PAGE_W - M, 42)

    def page_break(self):
        self.c.showPage()
        self._new_page()

    def need(self, height: float):
        if self.y + height > BOTTOM:
            self.page_break()

    # ---- primitives (y measured from the top of the page) ---------------
    def _ty(self, y):
        return PAGE_H - y

    def text(self, s, x, size=10, font="Helvetica", color=INK, width=None,
             leading=None, advance=True):
        s = _clean(s)
        if not s:
            return 0.0
        width = width or (PAGE_W - M - x)
        leading = leading or size * 1.35
        lines = simpleSplit(s, font, size, width)
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        for i, ln in enumerate(lines):
            self.c.drawString(x, self._ty(self.y + size + i * leading), ln)
        h = len(lines) * leading
        if advance:
            self.y += h
        return h

    def height_of(self, s, size, font, width) -> float:
        s = _clean(s)
        if not s:
            return 0.0
        return len(simpleSplit(s, font, size, width)) * size * 1.35

    def rect(self, x, y, w, h, fill=None, stroke=None, radius=0):
        if fill is not None:
            self.c.setFillColor(fill)
        if stroke is not None:
            self.c.setStrokeColor(stroke)
            self.c.setLineWidth(0.8)
        if radius:
            self.c.roundRect(x, self._ty(y + h), w, h, radius,
                             stroke=1 if stroke is not None else 0,
                             fill=1 if fill is not None else 0)
        else:
            self.c.rect(x, self._ty(y + h), w, h,
                        stroke=1 if stroke is not None else 0,
                        fill=1 if fill is not None else 0)

    def rule(self, color=LINE, width=0.8):
        self.c.setStrokeColor(color)
        self.c.setLineWidth(width)
        self.c.line(M, self._ty(self.y), PAGE_W - M, self._ty(self.y))

    def pill(self, label, x, y, color, bg):
        self.c.setFont("Helvetica-Bold", 8)
        w = self.c.stringWidth(_clean(label), "Helvetica-Bold", 8) + 16
        self.c.setFillColor(bg)
        self.c.roundRect(x, self._ty(y + 14), w, 14, 7, stroke=0, fill=1)
        self.c.setFillColor(color)
        self.c.drawString(x + 8, self._ty(y + 10.5), _clean(label))
        return w

    def save(self) -> bytes:
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


# ==========================================================================
# Blocks
# ==========================================================================

def _cover(d: Doc, r: dict):
    d.rect(0, 0, PAGE_W, 132, fill=NAVY)
    d.c.setFont("Helvetica-Bold", 9)
    d.c.setFillColor(BLUE)
    d.c.drawString(M, d._ty(34), "SMART 1 MARKETING")
    d.c.setFont("Helvetica-Bold", 23)
    d.c.setFillColor(WHITE)
    d.c.drawString(M, d._ty(64), _clean(r["title"]))
    d.c.setFont("Helvetica", 11)
    d.c.setFillColor(HexColor("#C7D8E8"))
    line = _clean(r.get("business") or r.get("domain") or "")
    if r.get("domain") and r.get("business"):
        line += "  ·  " + _clean(r["domain"])
    d.c.drawString(M, d._ty(84), line[:90])
    when = (r.get("scanned_at") or "")[:10]
    d.c.drawString(M, d._ty(100), f"Site audit of {when}" if when else "Site audit")

    # score block on the right of the band
    if r.get("overall_score") is not None:
        d.c.setFillColor(WHITE)
        d.c.roundRect(PAGE_W - M - 108, d._ty(112), 108, 78, 9, stroke=0, fill=1)
        d.c.setFont("Helvetica-Bold", 30)
        d.c.setFillColor(NAVY)
        d.c.drawCentredString(PAGE_W - M - 54, d._ty(70), str(r["overall_score"]))
        d.c.setFont("Helvetica", 8)
        d.c.setFillColor(MUTED)
        d.c.drawCentredString(PAGE_W - M - 54, d._ty(84), "OVERALL SITE SCORE")
        if r.get("tier"):
            d.c.setFont("Helvetica-Bold", 9)
            d.c.setFillColor(BLUE)
            d.c.drawCentredString(PAGE_W - M - 54, d._ty(100), _clean(r["tier"]).upper())

    d.y = 156
    d.text(r.get("blurb", ""), M, 11, "Helvetica", INK, width=W - 130)
    d.y += 10
    d.text(r.get("headline", ""), M, 12, "Helvetica-Bold", NAVY, width=W)
    d.y += 12

    x = M
    for label, count, col, bg in (
            (f"{r['passed']} passing", r["passed"], HexColor("#12794B"), HexColor("#ECFDF3")),
            (f"{r['warned']} to improve", r["warned"], HexColor("#94620A"), HexColor("#FEF7E7")),
            (f"{r['failed']} to fix", r["failed"], HexColor("#B01919"), HexColor("#FDECEC"))):
        x += d.pill(label, x, d.y, col, bg) + 8
    d.y += 30


def _priorities(d: Doc, r: dict):
    items = r.get("priorities") or []
    if not items:
        return
    d.need(70)
    d.text("Fix these first", M, 15, "Helvetica-Bold", NAVY)
    d.y += 2
    d.rule(BLUE, 1.6)
    d.y += 12
    for i, p in enumerate(items, 1):
        body_h = d.height_of(p["detail"], 9.5, "Helvetica", W - 30)
        d.need(body_h + 34)
        d.c.setFillColor(NAVY)
        d.c.circle(M + 8, d._ty(d.y + 6), 8, stroke=0, fill=1)
        d.c.setFont("Helvetica-Bold", 8.5)
        d.c.setFillColor(WHITE)
        d.c.drawCentredString(M + 8, d._ty(d.y + 9), str(i))
        d.text(p["section"].upper(), M + 24, 7.5, "Helvetica-Bold", MUTED, width=W - 30)
        d.text(p["label"], M + 24, 10.5, "Helvetica-Bold", NAVY, width=W - 30)
        d.text(p["detail"], M + 24, 9.5, "Helvetica", INK, width=W - 30)
        d.y += 10
    d.y += 6


def _stats(d: Doc, stats: list):
    if not stats:
        return
    d.need(46)
    x, h = M, 34
    for st in stats[:5]:
        label, value = _clean(st["label"]), _clean(st["value"])
        w = max(d.c.stringWidth(value, "Helvetica-Bold", 13),
                d.c.stringWidth(label, "Helvetica", 7.5)) + 20
        w = min(w, 150)
        if x + w > PAGE_W - M:
            break
        d.rect(x, d.y, w, h, fill=BG, stroke=LINE, radius=6)
        col = {"ok": GREEN, "bad": RED, "warn": HexColor("#94620A")}.get(st["band"], NAVY)
        d.c.setFont("Helvetica-Bold", 13)
        d.c.setFillColor(col)
        d.c.drawString(x + 9, d._ty(d.y + 17), value[:22])
        d.c.setFont("Helvetica", 7.5)
        d.c.setFillColor(MUTED)
        d.c.drawString(x + 9, d._ty(d.y + 27), label[:26])
        x += w + 7
    d.y += h + 12


def _checks(d: Doc, checks: list):
    for c in checks:
        detail = c.get("detail") or ""
        value = _clean(c.get("value") or "")
        vw = d.c.stringWidth(value, "Helvetica-Bold", 9.5) + 10 if value else 0
        body_w = W - 20 - vw
        h = (d.height_of(c["label"], 10, "Helvetica-Bold", body_w)
             + d.height_of(detail, 9, "Helvetica", body_w) + 10)
        d.need(h + 4)
        top = d.y
        col = STATE_COLOR.get(c["state"], MUTED)
        d.c.setFillColor(col)
        d.c.circle(M + 6, d._ty(top + 6), 5.5, stroke=0, fill=1)
        d.c.setFont("Helvetica-Bold", 7)
        d.c.setFillColor(WHITE)
        d.c.drawCentredString(M + 6, d._ty(top + 8.5), STATE_MARK.get(c["state"], "."))
        if value:
            d.c.setFont("Helvetica-Bold", 9.5)
            d.c.setFillColor(NAVY)
            d.c.drawRightString(PAGE_W - M, d._ty(top + 9), value)
        d.text(c["label"], M + 20, 10, "Helvetica-Bold", NAVY, width=body_w)
        if detail:
            d.text(detail, M + 20, 9, "Helvetica", MUTED, width=body_w)
        d.y = max(d.y, top + 16) + 5
        d.rule(HexColor("#EEF3F8"), 0.5)
        d.y += 5


def _table(d: Doc, t: dict, max_rows=12):
    rows = (t.get("rows") or [])[:max_rows]
    if not rows:
        return
    cols = t.get("columns") or []
    n = max(1, len(cols))
    cw = [W / n] * n
    if n > 1:                     # give the first column the extra room
        cw = [W * 0.4] + [(W * 0.6) / (n - 1)] * (n - 1)
    d.need(50)
    d.text(t.get("title", ""), M, 9.5, "Helvetica-Bold", NAVY)
    d.y += 2
    d.c.setFont("Helvetica-Bold", 7)
    d.c.setFillColor(MUTED)
    x = M
    for i, col in enumerate(cols):
        d.c.drawString(x, d._ty(d.y + 7), _clean(col).upper()[:26])
        x += cw[i]
    d.y += 11
    d.rule(LINE, 0.6)
    d.y += 5
    for row in rows:
        heights = [d.height_of(cell, 8, "Helvetica", cw[i] - 6)
                   for i, cell in enumerate(row[:n])]
        h = max(heights or [10])
        d.need(h + 8)
        x, top = M, d.y
        for i, cell in enumerate(row[:n]):
            d.y = top
            d.text(cell, x, 8, "Helvetica", INK, width=cw[i] - 6)
            x += cw[i]
        d.y = top + h + 5
        d.rule(HexColor("#EEF3F8"), 0.4)
        d.y += 4
    if t.get("note"):
        d.text(t["note"], M, 7.5, "Helvetica-Oblique", MUTED, width=W)
    d.y += 8


def _section(d: Doc, s: dict):
    d.need(72)
    d.y += 4
    label = ("NOT MEASURED" if not s["measured"]
             else (f"{s['failed']} TO FIX" if s["failed"]
                   else (f"{s['warned']} TO IMPROVE" if s["warned"] else "CLEAR")))
    col, bg = {
        "ok": (HexColor("#12794B"), HexColor("#ECFDF3")),
        "bad": (HexColor("#B01919"), HexColor("#FDECEC")),
        "warn": (HexColor("#94620A"), HexColor("#FEF7E7")),
    }.get(s["severity"], (MUTED, HexColor("#EEF3F8")))
    top = d.y
    d.text(s["title"], M, 14, "Helvetica-Bold", NAVY, width=W - 110)
    pw = d.c.stringWidth(label, "Helvetica-Bold", 8) + 16
    d.pill(label, PAGE_W - M - pw, top + 2, col, bg)
    d.y += 2
    d.rule(BLUE, 1.4)
    d.y += 10
    d.text(s["verdict"], M, 10, "Helvetica", INK if s["measured"] else MUTED, width=W)
    d.y += 8
    _stats(d, s.get("stats") or [])
    _checks(d, s.get("checks") or [])
    for t in (s.get("tables") or []):
        _table(d, t)
    if s.get("note"):
        d.text(s["note"], M, 8.5, "Helvetica-Oblique", MUTED, width=W)
        d.y += 6
    d.y += 10


def build(r: dict) -> bytes:
    """A finished report dict in, PDF bytes out."""
    subtitle = f"{r.get('business') or r.get('domain') or ''} · {r.get('title', '')}"
    d = Doc(f"{r.get('title', 'Audit')} - {r.get('business') or r.get('domain') or ''}",
            subtitle)
    _cover(d, r)
    _priorities(d, r)
    for s in r.get("sections", []):
        _section(d, s)
    d.need(50)
    d.y += 6
    d.rule(LINE)
    d.y += 8
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    d.text(f"Prepared by Smart 1 Marketing on {stamp} from the site audit run on "
           f"{(r.get('scanned_at') or '')[:10] or 'the date shown'}. Automated "
           "checks - a finding here is a starting point for a conversation, not "
           "a legal or engineering opinion.", M, 8, "Helvetica-Oblique", MUTED,
           width=W)
    return d.save()


def filename(r: dict) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-",
                  (r.get("business") or r.get("domain") or "audit")).strip("-")
    key = re.sub(r"[^a-z0-9]+", "-", r.get("key", "report"))
    return f"{base}-{key}.pdf"[:120]
