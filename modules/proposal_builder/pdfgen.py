"""Branded proposal PDF — reportlab port of lib/pdf.js (Smart 1 canonical palette).

Renders the same block model: heading, text, stats, list, tiers, table, cta,
with the blue header band, section rules, tier cards with RECOMMENDED badge,
striped tables, navy CTA panel, and the phone/domain footer on every page.
"""
import io

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as _canvas

NAVY = HexColor("#0A2240")
BLUE = HexColor("#009ED2")
GREEN = HexColor("#2DBB72")
GOLD = HexColor("#F0B429")
INK = HexColor("#25364B")
MUTED = HexColor("#68798C")
LINE = HexColor("#DBE5ED")
BG = HexColor("#F3F7FA")
WHITE = HexColor("#FFFFFF")
CTA_SUB = HexColor("#D6E0EE")

M = 54
PAGE_W, PAGE_H = letter  # 612 x 792
W = PAGE_W - M * 2
BOTTOM_LIMIT = 728  # pdf.js works top-down; we mirror with y-from-top coords


class _Doc:
    """Tiny top-down layout shim over a reportlab canvas (pdf.js-style y axis)."""

    def __init__(self, buf, title):
        self.c = _canvas.Canvas(buf, pagesize=letter)
        self.c.setTitle(title)
        self.c.setAuthor("Smart 1 Marketing")
        self.y = 40.0  # distance from top
        self.pages = 0
        self._start_page(first=True)

    def _start_page(self, first=False):
        self.pages += 1
        if first:
            # blue band across the top of page 1
            self.rect(0, 0, PAGE_W, 8, BLUE)
            self.y = 40
        else:
            self.y = 64

    def add_page(self):
        self.c.showPage()
        self._start_page()

    def ensure(self, h):
        if self.y + h > BOTTOM_LIMIT:
            self.add_page()

    # --- primitives (x from left, y from top) ---
    def _ty(self, y):
        return PAGE_H - y

    def rect(self, x, y, w, h, fill, radius=0, stroke=None, line_width=1):
        self.c.saveState()
        if fill is not None:
            self.c.setFillColor(fill)
        if stroke is not None:
            self.c.setStrokeColor(stroke)
            self.c.setLineWidth(line_width)
        mode_fill = 1 if fill is not None else 0
        mode_stroke = 1 if stroke is not None else 0
        if radius:
            self.c.roundRect(x, self._ty(y) - h, w, h, radius, stroke=mode_stroke, fill=mode_fill)
        else:
            self.c.rect(x, self._ty(y) - h, w, h, stroke=mode_stroke, fill=mode_fill)
        self.c.restoreState()

    def circle(self, x, y, r, fill):
        self.c.saveState()
        self.c.setFillColor(fill)
        self.c.circle(x, self._ty(y), r, stroke=0, fill=1)
        self.c.restoreState()

    def hline(self, x1, x2, y, color, width=1):
        self.c.saveState()
        self.c.setStrokeColor(color)
        self.c.setLineWidth(width)
        self.c.line(x1, self._ty(y), x2, self._ty(y))
        self.c.restoreState()

    def wrap(self, text, font, size, width):
        words = str(text or "").replace("\r", "").split()
        if not words:
            return [""]
        lines, cur = [], words[0]
        for w in words[1:]:
            if stringWidth(cur + " " + w, font, size) <= width:
                cur += " " + w
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        return lines

    def text(self, text, x, y, font, size, color, width=None, line_gap=2, align="left", max_lines=None):
        """Draw wrapped text at (x, y-from-top). Returns height used."""
        self.c.saveState()
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        paragraphs = str(text or "").split("\n")
        lines = []
        for p in paragraphs:
            lines.extend(self.wrap(p, font, size, width or W))
        if max_lines:
            lines = lines[:max_lines]
        lh = size + line_gap
        yy = y
        for ln in lines:
            baseline = self._ty(yy) - size
            if align == "center" and width:
                self.c.drawCentredString(x + width / 2, baseline, ln)
            elif align == "right" and width:
                self.c.drawRightString(x + width, baseline, ln)
            else:
                self.c.drawString(x, baseline, ln)
            yy += lh
        self.c.restoreState()
        return len(lines) * lh

    def link(self, x, y, w, h, url):
        self.c.linkURL(url, (x, self._ty(y) - h, x + w, self._ty(y)), relative=0)


def _section_head(d, text):
    h = d.text(text, M, d.y, "Helvetica-Bold", 13, NAVY, width=W)
    d.hline(M, M + 34, d.y + h + 2, BLUE, 2)
    d.y += h + 10


def _wordmark(d, x, y):
    d.rect(x, y, 20, 20, BLUE, radius=5)
    d.circle(x + 10, y + 10, 3.4, WHITE)
    tx = x + 26
    ty = y + 5
    baseline = d._ty(ty) - 11
    c = d.c
    c.saveState()
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(NAVY)
    c.drawString(tx, baseline, "SMART")
    w1 = stringWidth("SMART", "Helvetica-Bold", 11)
    c.setFillColor(BLUE)
    c.drawString(tx + w1, baseline, "1")
    w2 = w1 + stringWidth("1", "Helvetica-Bold", 11)
    c.setFillColor(NAVY)
    c.drawString(tx + w2, baseline, "MARKETING")
    c.restoreState()


def build_proposal_pdf(record: dict) -> bytes:
    biz = (record.get("customer") or {}).get("business_name") or "Proposal"
    title = f"{biz} \u2014 Smart 1 Marketing Proposal"

    # Pass 1: render to count pages.
    d1 = _Doc(io.BytesIO(), title)
    _render_blocks(d1, record)
    total = d1.pages

    # Pass 2: render again, stamping the footer (with correct total) on every page.
    buf = io.BytesIO()
    d = _Doc(buf, title)

    def _footer(doc):
        doc.hline(M, M + W, 742, LINE, 0.5)
        doc.text("Smart 1 Marketing \u00b7 (614) 536-0768 \u00b7 smart1marketing.com",
                 M, 748, "Helvetica", 7.5, MUTED, width=W, max_lines=1)
        doc.text(f"{doc.pages} / {total}", M, 748, "Helvetica", 7.5, MUTED,
                 width=W, align="right", max_lines=1)

    orig_add_page = d.add_page

    def add_page_with_footer():
        _footer(d)
        orig_add_page()

    d.add_page = add_page_with_footer
    _render_blocks(d, record)
    _footer(d)
    d.c.showPage()
    d.c.save()
    return buf.getvalue()


def _render_blocks(d, record):
    """Re-run the block renderer on a fresh doc (used by the footer pass)."""
    # Reuse build logic by temporarily inlining: simplest is to re-dispatch.
    # To avoid duplicating the switch above, we call the same code path via a
    # tiny trampoline. (The first pass exists only to count pages.)
    for block in record.get("blocks") or []:
        _RENDERERS[block.get("type", "")](d, block.get("data") or {})


# Build renderer table from the first-pass implementations by re-defining them
# as standalone functions (kept in sync with the switch above).
def _r_heading(d, data):
    d.ensure(90)
    _wordmark(d, M, d.y)
    d.y += 26
    h = d.text(data.get("title", ""), M, d.y, "Helvetica-Bold", 21, NAVY, width=W)
    d.y += h + 2
    if data.get("subtitle"):
        d.y += d.text(data["subtitle"], M, d.y, "Helvetica", 10, MUTED, width=W)
    d.y += 6
    d.hline(M, M + W, d.y, BLUE, 2)
    d.y += 16


def _r_text(d, data):
    d.ensure(70)
    if data.get("heading"):
        _section_head(d, data["heading"])
    d.y += d.text(data.get("body", ""), M, d.y, "Helvetica", 10.5, INK, width=W, line_gap=3) + 12


def _r_stats(d, data):
    items = (data.get("items") or [])[:4]
    d.ensure(96)
    if data.get("heading"):
        _section_head(d, data["heading"])
    gap, n = 10, max(len(items), 1)
    cw = (W - gap * (n - 1)) / n
    y0 = d.y
    for i, it in enumerate(items):
        x = M + i * (cw + gap)
        d.rect(x, y0, cw, 62, BG, radius=6)
        d.rect(x, y0, cw, 4, BLUE, radius=2)
        d.text(str(it.get("value", "")), x + 10, y0 + 14, "Helvetica-Bold", 15, NAVY, width=cw - 20, max_lines=1)
        d.text(str(it.get("label", "")).upper(), x + 10, y0 + 38, "Helvetica", 8.5, MUTED, width=cw - 20, max_lines=2)
    d.y = y0 + 74


def _r_list(d, data):
    items = data.get("items") or []
    d.ensure(40 + len(items) * 16)
    if data.get("heading"):
        _section_head(d, data["heading"])
    for it in items:
        label = it if isinstance(it, str) else f"{it.get('label', '')}: {it.get('value', '')}"
        d.circle(M + 4, d.y + 5, 2.5, GREEN)
        d.y += d.text(label, M + 16, d.y, "Helvetica", 10.5, INK, width=W - 16, line_gap=2) + 3
        if d.y > BOTTOM_LIMIT:
            d.add_page()
    d.y += 10


def _r_tiers(d, data):
    tiers = (data.get("tiers") or [])[:3]
    d.ensure(200)
    if data.get("heading"):
        _section_head(d, data["heading"])
    gap, n = 10, max(len(tiers), 1)
    cw = (W - gap * (n - 1)) / n
    y0 = d.y + 8
    max_h = 0
    try:
        rec_idx = int(data.get("recommended") or 0)
    except (TypeError, ValueError):
        rec_idx = 0
    for i, t in enumerate(tiers):
        x = M + i * (cw + gap)
        rec = rec_idx == i
        feats = (t.get("features") or [])[:6]
        h = 86 + len(feats) * 14
        max_h = max(max_h, h)
        d.rect(x, y0, cw, h, None, radius=8, stroke=BLUE if rec else LINE, line_width=2 if rec else 1)
        if rec:
            d.rect(x + cw / 2 - 46, y0 - 8, 92, 16, GOLD, radius=8)
            d.text("RECOMMENDED", x + cw / 2 - 46, y0 - 6, "Helvetica-Bold", 7.5, NAVY, width=92, align="center", max_lines=1)
        d.text(t.get("name", ""), x + 10, y0 + 12, "Helvetica-Bold", 12, NAVY, width=cw - 20, max_lines=1)
        d.text(t.get("price", ""), x + 10, y0 + 28, "Helvetica-Bold", 13, BLUE, width=cw - 20, max_lines=1)
        d.text(t.get("blurb", ""), x + 10, y0 + 46, "Helvetica", 8.5, MUTED, width=cw - 20, max_lines=2)
        for j, f in enumerate(feats):
            d.text("• " + str(f), x + 10, y0 + 64 + j * 14, "Helvetica", 8, INK, width=cw - 20, max_lines=1)
    d.y = y0 + max_h + 16


def _r_table(d, data):
    cols = data.get("columns") or []
    rows = data.get("rows") or []
    d.ensure(50 + len(rows) * 22)
    if data.get("heading"):
        _section_head(d, data["heading"])
    n = max(len(cols), 1)
    cw = W / n
    y = d.y
    d.rect(M, y, W, 20, NAVY)
    for i, c in enumerate(cols):
        d.text(str(c).upper(), M + i * cw + 8, y + 6, "Helvetica-Bold", 8.5, WHITE, width=cw - 16, max_lines=1)
    y += 20
    for ri, r in enumerate(rows):
        if y > 710:
            d.add_page()
            y = d.y
        if ri % 2 == 0:
            d.rect(M, y, W, 20, BG)
        for i, cell in enumerate(r):
            d.text(str(cell if cell is not None else ""), M + i * cw + 8, y + 6, "Helvetica", 8.5, INK, width=cw - 16, max_lines=1)
        y += 20
    d.y = y + 14


def _r_cta(d, data):
    d.ensure(96)
    y0 = d.y
    d.rect(M, y0, W, 78, NAVY, radius=8)
    d.text(data.get("heading") or "Next Step", M + 18, y0 + 14, "Helvetica-Bold", 13, WHITE, width=W - 200, max_lines=1)
    d.text(data.get("body", ""), M + 18, y0 + 34, "Helvetica", 9.5, CTA_SUB, width=W - 200, max_lines=3)
    bw = 150
    bx = M + W - bw - 18
    by = y0 + 24
    d.rect(bx, by, bw, 30, GOLD, radius=6)
    d.text(data.get("button") or "Get started", bx, by + 10, "Helvetica-Bold", 9, NAVY, width=bw, align="center", max_lines=1)
    if data.get("url"):
        d.link(bx, by, bw, 30, data["url"])
    d.y = y0 + 92


_RENDERERS = {
    "heading": _r_heading, "text": _r_text, "stats": _r_stats, "list": _r_list,
    "tiers": _r_tiers, "table": _r_table, "cta": _r_cta,
}
_RENDERERS.setdefault("", lambda d, data: None)


def _noop(d, data):
    return None


class _DefaultDict(dict):
    def __missing__(self, key):
        return _noop


_RENDERERS = _DefaultDict(_RENDERERS)
