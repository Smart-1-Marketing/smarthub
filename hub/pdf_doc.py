"""The one-page, top-down PDF cursor -- shared plain-reportlab layout.

`modules/reports/client_pdf.py` and `modules/scans/report_pdf.py` each
carry their own near-identical copy of this: the same Smart 1 palette
(navy header bar, the same six hex colours), the same top-down cursor with
a page-break check before every block, the same tile/table/rule helpers.
Two copies is the drift `hub/storage.py` and `hub/images.py` exist to stop
-- a fix to how a tile wraps its label lands in one and not the other.

This is not a rewrite of either: neither is touched here, because a PDF a
client already receives is not something to risk regressing for a third
consumer's sake. It is the shared home the next module wanting a printable
page should reach for, and the next time either of those two is edited for
its own reason, `docs/claude/14` says to move it across then.

Plain reportlab. No new dependency -- `reportlab` is already in
`requirements.txt` for the modules above.
"""
from __future__ import annotations

import io
import re

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas as _canvas

__all__ = ["Doc", "clean", "NAVY", "BLUE", "SOFT", "INK", "MUTED", "LINE",
          "WHITE", "GOOD", "WARN", "BAD", "M", "PAGE_W", "PAGE_H", "W", "BOTTOM"]

NAVY = HexColor("#0A2240")
BLUE = HexColor("#2A78D6")
SOFT = HexColor("#E3EEFB")
INK = HexColor("#0B0B0B")
MUTED = HexColor("#52514E")
LINE = HexColor("#E6E5E0")
WHITE = HexColor("#FFFFFF")
GOOD = HexColor("#0A6B3C")
WARN = HexColor("#92400E")
BAD = HexColor("#9C2B25")

M = 48
PAGE_W, PAGE_H = letter
W = PAGE_W - M * 2
BOTTOM = PAGE_H - 60


def clean(text) -> str:
    """Smart quotes and dashes flattened to what Helvetica's base encoding
    actually has glyphs for -- the same normalisation client_pdf.py and
    report_pdf.py each apply before drawing a string."""
    s = "" if text is None else str(text)
    s = (s.replace("’", "'").replace("‘", "'").replace("“", '"')
          .replace("”", '"').replace("—", "-").replace("–", "-")
          .replace("…", "..."))
    return re.sub(r"\s+", " ", s).strip()


class Doc:
    """A one-page-at-a-time canvas: `need(h)` breaks the page before a block
    that would not fit rather than after, so nothing is ever drawn over the
    footer or the next page's own header bar."""

    def __init__(self, title: str):
        self.buf = io.BytesIO()
        # Uncompressed: the document is small, and a figure on it is then
        # checkable as bytes against the page it came from.
        self.c = _canvas.Canvas(self.buf, pagesize=letter, pageCompression=0)
        self.c.setTitle(title)
        self.c.setAuthor("Smart 1 Marketing")
        self.page = 0
        self.y = 0.0
        self._new_page()

    def _new_page(self):
        self.page += 1
        self.c.setFillColor(BLUE)
        self.c.rect(0, PAGE_H - 6, PAGE_W, 6, stroke=0, fill=1)
        self.c.setFont("Helvetica", 8)
        self.c.setFillColor(MUTED)
        self.c.drawString(M, 28, "Smart 1 Marketing  ·  smart1marketing.com")
        self.c.drawRightString(PAGE_W - M, 28, f"Page {self.page}")
        self.y = 44

    def need(self, h: float):
        if self.y + h > BOTTOM:
            self.c.showPage()
            self._new_page()

    def text(self, s: str, size=10, color=INK, bold=False, x=M, width=None, gap=3):
        font = "Helvetica-Bold" if bold else "Helvetica"
        lines = simpleSplit(clean(s), font, size, width or W)
        for line in lines:
            self.need(size + gap)
            self.c.setFont(font, size)
            self.c.setFillColor(color)
            self.c.drawString(x, PAGE_H - self.y - size, line)
            self.y += size + gap
        return len(lines)

    def space(self, h: float):
        self.y += h

    def rule(self):
        self.need(8)
        self.c.setStrokeColor(LINE)
        self.c.line(M, PAGE_H - self.y - 4, M + W, PAGE_H - self.y - 4)
        self.y += 10

    def banner(self, s: str, color=SOFT, text_color=INK):
        """A full-width note -- the next-action line, or a source that
        would not answer. Drawn as a block so it reads as one thing rather
        than as body text that happens to matter more."""
        self.need(26)
        top = PAGE_H - self.y
        self.c.setFillColor(color)
        self.c.roundRect(M, top - 22, W, 22, 5, stroke=0, fill=1)
        self.c.setFont("Helvetica-Bold", 10)
        self.c.setFillColor(text_color)
        self.c.drawString(M + 10, top - 15, clean(s)[:110])
        self.y += 28

    def tiles(self, tiles: list[dict]):
        """Each tile is {"label", "display", "color"}; color defaults to
        INK, so a tile stating a warning or a bad figure can say so in the
        same red the health strip already uses for it on screen."""
        n = max(1, len(tiles))
        gap = 10
        w = (W - gap * (n - 1)) / n
        h = 58
        self.need(h + 8)
        top = PAGE_H - self.y
        for i, t in enumerate(tiles):
            x = M + i * (w + gap)
            self.c.setFillColor(WHITE)
            self.c.setStrokeColor(LINE)
            self.c.roundRect(x, top - h, w, h, 8, stroke=1, fill=1)
            self.c.setFont("Helvetica", 8.5)
            self.c.setFillColor(MUTED)
            self.c.drawString(x + 10, top - 16, clean(t["label"])[:34])
            self.c.setFont("Helvetica-Bold", 18)
            self.c.setFillColor(t.get("color") or INK)
            self.c.drawString(x + 10, top - 42, clean(t["display"])[:16])
        self.y += h + 12

    def table(self, head: list[tuple[str, float, bool]], rows: list[list[str]]):
        """head: (label, width, right-aligned)."""
        self.need(20)
        top = PAGE_H - self.y
        self.c.setFont("Helvetica-Bold", 8.5)
        self.c.setFillColor(MUTED)
        x = M
        for label, w, right in head:
            (self.c.drawRightString if right else self.c.drawString)(x + w - 2 if right else x, top - 10, label)
            x += w
        self.y += 14
        self.rule()
        for row in rows:
            self.need(15)
            top = PAGE_H - self.y
            self.c.setFont("Helvetica", 9)
            self.c.setFillColor(INK)
            x = M
            for (label, w, right), cell in zip(head, row):
                s = clean(cell)
                if not right:
                    s = simpleSplit(s, "Helvetica", 9, w - 6)[0] if s else ""
                (self.c.drawRightString if right else self.c.drawString)(x + w - 2 if right else x, top - 10, s)
                x += w
            self.y += 15

    def bytes(self) -> bytes:
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()
