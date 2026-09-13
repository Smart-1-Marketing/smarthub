"""The client dashboard as a document: the same aggregate, laid out for print.

``build(agg)`` takes exactly what ``client_view.aggregate()`` returned for
the page -- the same tiles, the same product rows, the same Investment
figures from the same pricing call -- so the document a client downloads
cannot disagree with the page they downloaded it from. It computes nothing
about money of its own.

Plain reportlab, the way ``modules/scans/report_pdf.py`` is: a top-down
cursor with a page-break check before every block, in the Smart 1 palette.
"""
from __future__ import annotations

import io
import re

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas as _canvas

NAVY = HexColor("#0A2240")
BLUE = HexColor("#2A78D6")
SOFT = HexColor("#E3EEFB")
INK = HexColor("#0B0B0B")
MUTED = HexColor("#52514E")
LINE = HexColor("#E6E5E0")
WHITE = HexColor("#FFFFFF")

M = 48
PAGE_W, PAGE_H = letter
W = PAGE_W - M * 2
BOTTOM = PAGE_H - 60


def _clean(text) -> str:
    s = "" if text is None else str(text)
    s = (s.replace("’", "'").replace("‘", "'").replace("“", '"')
          .replace("”", '"').replace("—", "-").replace("–", "-")
          .replace("…", "..."))
    return re.sub(r"\s+", " ", s).strip()


def filename(agg: dict) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(agg.get("client_name") or "report").lower()).strip("-")
    return f"{slug or 'report'}-{agg.get('period', {}).get('key', 'report')}.pdf"


class _Doc:
    def __init__(self, title: str):
        self.buf = io.BytesIO()
        # Uncompressed streams: the document is small, and the figures in it
        # are then checkable as bytes against the page they came from.
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
        lines = simpleSplit(_clean(s), font, size, width or W)
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

    def tiles(self, tiles: list[dict]):
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
            self.c.drawString(x + 10, top - 16, _clean(t["label"])[:34])
            self.c.setFont("Helvetica-Bold", 18)
            self.c.setFillColor(INK)
            self.c.drawString(x + 10, top - 42, _clean(t["display"]))
        self.y += h + 12

    def bars(self, rows: list[dict]):
        for r in rows:
            self.need(18)
            top = PAGE_H - self.y
            self.c.setFont("Helvetica", 9.5)
            self.c.setFillColor(INK)
            self.c.drawString(M, top - 11, _clean(r["label"])[:34])
            bx, bw = M + 190, W - 190 - 70
            self.c.setFillColor(SOFT)
            self.c.rect(bx, top - 13, bw, 10, stroke=0, fill=1)
            self.c.setFillColor(BLUE)
            self.c.rect(bx, top - 13, bw * (r.get("share", 0) / 100), 10, stroke=0, fill=1)
            self.c.setFillColor(MUTED)
            self.c.drawRightString(M + W, top - 11, f"{r['impressions']:,}")
            self.y += 18

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
                s = _clean(cell)
                if not right:
                    s = simpleSplit(s, "Helvetica", 9, w - 6)[0] if s else ""
                (self.c.drawRightString if right else self.c.drawString)(x + w - 2 if right else x, top - 10, s)
                x += w
            self.y += 15

    def bytes(self) -> bytes:
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


def _line_chart(d: "_Doc", series: list[dict], field: str):
    """Twelve months as a line, the month in progress dashed and hollow --
    the same drawing the reach trend gets."""
    d.need(120)
    top = PAGE_H - d.y
    peak = max((t[field] for t in series), default=0) or 1
    gw = W / len(series)
    pts = []
    for i, t in enumerate(series):
        x = M + gw * i + gw / 2
        y = top - 100 + 80 * (t[field] / peak)
        pts.append((x, y))
        d.c.setFont("Helvetica", 7.5)
        d.c.setFillColor(MUTED)
        d.c.drawCentredString(x, top - 112, t["label"])
    d.c.setStrokeColor(BLUE)
    d.c.setLineWidth(1.8)
    segs = list(zip(pts, pts[1:]))
    for i, ((x1, y1), (x2, y2)) in enumerate(segs):
        last = i == len(segs) - 1 and series[-1].get("partial")
        d.c.setDash([3, 3] if last else [])
        d.c.line(x1, y1, x2, y2)
    d.c.setDash([])
    if series[-1].get("partial") and pts:
        d.c.setFillColor(WHITE)
        d.c.circle(pts[-1][0], pts[-1][1], 3, stroke=1, fill=1)
    d.c.setLineWidth(1)
    d.y += 122


def _organic(d: "_Doc", o: dict | None):
    """The organic-search section, from the same block the page draws. The
    labels are the block's own, so the document cannot name a product the
    page does not."""
    if not o:
        return
    L = o.get("labels") or {}
    ana = o.get("analytics") or {}
    search = o.get("search") or {}
    work = o.get("work") or {}
    d.rule()
    d.text(L.get("section", "Organic search"), size=14, bold=True, color=NAVY)
    d.text(o.get("period", {}).get("label", ""), size=9, color=MUTED, gap=8)
    if ana.get("measured"):
        cur, chg = ana.get("current") or {}, ana.get("change") or {}
        tiles = []
        for k in ("sessions", "users", "engaged", "key_events"):
            delta = chg.get(k)
            tiles.append({"label": L.get(k, k),
                          "display": f"{int(cur.get(k) or 0):,}"
                          + (f"  ({'+' if delta > 0 else ''}{delta}%)" if delta is not None else "")})
        d.tiles(tiles)
        if ana.get("trend"):
            d.text(L.get("trend", ""), size=11, bold=True, color=NAVY)
            d.text("The current month is shown to date, dashed, not projected", size=9, color=MUTED, gap=8)
            _line_chart(d, ana["trend"], "sessions")
    else:
        d.text(ana.get("note") or "Analytics could not be read for this period.", size=9.5, color=MUTED)
    d.space(6)
    d.text("Google search", size=11, bold=True, color=NAVY, gap=8)
    if search.get("measured"):
        cur, prev = search.get("current") or {}, search.get("previous") or {}
        d.table([("", W - 220, False), ("This period", 110, True), ("Period before", 110, True)], [
            [L.get("gsc_clicks", ""), f"{cur.get('clicks', 0):,}", f"{prev.get('clicks', 0):,}"],
            [L.get("gsc_impressions", ""), f"{cur.get('impressions', 0):,}", f"{prev.get('impressions', 0):,}"],
            [L.get("gsc_ctr", ""), f"{cur.get('ctr', 0)}%", f"{prev.get('ctr', 0)}%"],
            [L.get("gsc_position", ""), f"{cur.get('position', 0)}", f"{prev.get('position', 0)}"],
        ])
        for key, label in (("queries", L.get("queries", "")), ("pages", L.get("pages", ""))):
            rows = search.get(key) or []
            if rows:
                d.space(8)
                d.text(label, size=10, bold=True, color=NAVY, gap=6)
                d.table([("", W - 140, False), ("Clicks", 70, True), ("Shown", 70, True)],
                        [[r["key"], f"{r['clicks']:,}", f"{r['impressions']:,}"] for r in rows[:10]])
    else:
        d.text(search.get("note") or "", size=9.5, color=MUTED)
    d.space(8)
    d.text(L.get("work", "What we did"), size=11, bold=True, color=NAVY, gap=6)
    if work.get("rows"):
        d.table([("", W - 80, False), ("Count", 80, True)],
                [[w["label"], str(w["count"])] for w in work["rows"]])
        if work.get("latest_score") is not None:
            d.text(f"Latest site audit score: {work['latest_score']}", size=9, color=MUTED)
    else:
        d.text("Nothing to show for this period yet.", size=9.5, color=MUTED)
    gbp = o.get("gbp") or {}
    if gbp:
        d.space(6)
        d.text(f"{gbp.get('label', '')}: {gbp.get('note', '')}", size=9, color=MUTED)
    d.space(10)


def build(agg: dict) -> bytes:
    """The document. ``agg`` is ``client_view.aggregate()``'s answer, as is."""
    name = agg.get("client_name") or "Your campaign"
    d = _Doc(f"{name} — marketing report")
    d.text(name, size=20, bold=True, color=NAVY, gap=4)
    d.text(f"Marketing report from Smart 1 Marketing  ·  {agg.get('period', {}).get('label', '')}",
           size=10, color=MUTED, gap=10)

    d.tiles(agg.get("tiles") or [])

    platforms = agg.get("products") or []
    d.text("Where your reach came from", size=12, bold=True, color=NAVY)
    d.text("Impressions by product", size=9, color=MUTED, gap=8)
    if platforms:
        d.bars(platforms)
    else:
        d.text("Nothing delivered in this period yet.", size=9.5, color=MUTED)
    d.space(10)

    inv = agg.get("investment")
    if inv is not None:
        d.text("Investment by product", size=12, bold=True, color=NAVY, gap=8)
        rows = [[p["label"], p.get("investment") or "delivery only"] for p in platforms]
        rows.append(["Total", inv.get("total", "")])
        d.table([("Product", W - 120, False), ("Investment", 120, True)], rows)
        if inv.get("delivery_only"):
            d.text("A product showing delivery only is reported by reach here; "
                   "your Smart 1 rep can confirm its investment figure.", size=8.5, color=MUTED)
        d.space(10)

    trend = agg.get("trend") or []
    if trend:
        d.text("Reach over the last 12 months", size=12, bold=True, color=NAVY)
        d.text("Impressions per month, all products - the current month is shown "
               "to date, dashed, not projected", size=9, color=MUTED, gap=8)
        _line_chart(d, trend, "impressions")

    _organic(d, agg.get("organic"))

    table = agg.get("table") or []
    d.text("Product detail", size=12, bold=True, color=NAVY, gap=8)
    if table:
        d.table([("Product", 130, False), ("Campaign", W - 130 - 4 * 70, False),
                 ("Impressions", 70, True), ("Clicks", 70, True), ("CTR", 70, True),
                 ("Conversions", 70, True)],
                [[r["label"], r["campaign"], f"{r['impressions']:,}", f"{r['clicks']:,}",
                  r["ctr"], f"{r['conversions']:,}"] for r in table])
    else:
        d.text("Nothing delivered in this period yet.", size=9.5, color=MUTED)
    d.space(12)
    d.rule()
    rep = agg.get("rep_name") or "your Smart 1 rep"
    d.text(f"Updated {agg.get('updated_et', '')}  ·  Delivery data gathered by "
           f"Smart 1 Marketing. Questions? Talk to {rep}"
           + (f" at {agg['rep_email']}" if agg.get("rep_email") else "") + ".",
           size=8.5, color=MUTED)
    return d.bytes()
