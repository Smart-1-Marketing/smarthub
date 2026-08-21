"""The RV proposal PDF — `proposal.js` translated from pdfkit to reportlab.

Three pages: a cover, the seasonal month-by-month plan, and the channel mix.
The palette and layout follow the original; reportlab and pdfkit do not lay
text out identically, so this is a faithful rebuild rather than a byte-for-byte
copy. Tourism's PDF stayed identical because it was HTML printed by a browser;
this one is drawn programmatically, so the same trick isn't available.

reportlab rather than adding a second PDF library: it is already a Hub
dependency and is what the other landing pages use.
"""
from __future__ import annotations

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as rl_canvas

NAVY = colors.HexColor("#0D2340")
CYAN = colors.HexColor("#3EC6F0")
INK = colors.HexColor("#1B2733")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#E2E8F0")

PAGE_W, PAGE_H = letter
MARGIN = 0.75 * inch


def _header(c: rl_canvas.Canvas, title: str, subtitle: str = "") -> float:
    """Navy band with the wordmark. Returns the y to start writing at."""
    c.setFillColor(NAVY)
    c.rect(0, PAGE_H - 1.5 * inch, PAGE_W, 1.5 * inch, fill=1, stroke=0)
    c.setFillColor(CYAN)
    c.circle(MARGIN + 6, PAGE_H - 0.55 * inch, 6, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(MARGIN + 20, PAGE_H - 0.6 * inch, "SMART 1 MARKETING")
    c.setFont("Helvetica-Bold", 19)
    c.drawString(MARGIN, PAGE_H - 1.05 * inch, title[:60])
    if subtitle:
        c.setFont("Helvetica", 10.5)
        c.setFillColor(colors.HexColor("#BFD4E8"))
        c.drawString(MARGIN, PAGE_H - 1.28 * inch, subtitle[:90])
    return PAGE_H - 1.9 * inch


def _footer(c: rl_canvas.Canvas, page_no: int, pages: int) -> None:
    """Drawn well above the bottom edge.

    The stadium app put its footer below the bottom margin, which made PDFKit
    append a blank page per footer — nine pages, six of them empty. Worth not
    repeating.
    """
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 8.5)
    c.drawString(MARGIN, 0.55 * inch, "Smart 1 Marketing · smart1marketing.com")
    c.drawRightString(PAGE_W - MARGIN, 0.55 * inch, f"{page_no} / {pages}")


def _wrap(c: rl_canvas.Canvas, text: str, x: float, y: float,
          width: float, size: float = 10.5, leading: float = 14.5) -> float:
    """Draw wrapped text, returning the new y."""
    c.setFont("Helvetica", size)
    words = str(text or "").split()
    line = ""
    for w in words:
        trial = (line + " " + w).strip()
        if c.stringWidth(trial, "Helvetica", size) > width and line:
            c.drawString(x, y, line)
            y -= leading
            line = w
        else:
            line = trial
    if line:
        c.drawString(x, y, line)
        y -= leading
    return y


def build_proposal_pdf(payload: dict, estimate: dict) -> bytes:
    buf = BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=letter)
    dealer = str(payload.get("dealership_name") or "Your Dealership")
    total_pages = 3

    # --- page 1: cover + summary -------------------------------------
    y = _header(c, "RV Demand Plan", dealer)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(MARGIN, y, "Market summary")
    y -= 18
    c.setFillColor(INK)
    y = _wrap(c, estimate.get("market_summary", ""), MARGIN, y,
              PAGE_W - 2 * MARGIN)
    y -= 14

    # Recommended package, in a card
    c.setFillColor(colors.HexColor("#F1F7FC"))
    c.roundRect(MARGIN, y - 68, PAGE_W - 2 * MARGIN, 62, 8, fill=1, stroke=0)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 9.5)
    c.drawString(MARGIN + 16, y - 24, "RECOMMENDED PACKAGE")
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(MARGIN + 16, y - 48,
                 str(estimate.get("recommended_package", "")))
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 10)
    c.drawRightString(PAGE_W - MARGIN - 16, y - 48,
                      f"Annual {estimate.get('annual_total_display', '')}")
    y -= 90

    if estimate.get("estimate_source") == "fallback":
        # Never present a deterministic estimate as a market analysis.
        c.setFillColor(colors.HexColor("#8A5A00"))
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(MARGIN, y,
                     "Built from regional seasonality rather than a live "
                     "market read.")
    _footer(c, 1, total_pages)
    c.showPage()

    # --- page 2: month by month --------------------------------------
    y = _header(c, "Seasonal plan", f"{dealer} · 12 months")
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(MARGIN, y, "MONTH")
    c.drawString(MARGIN + 2.2 * inch, y, "SEASON")
    c.drawRightString(PAGE_W - MARGIN, y, "BUDGET")
    y -= 6
    c.setStrokeColor(LINE)
    c.line(MARGIN, y, PAGE_W - MARGIN, y)
    y -= 18

    for row in (estimate.get("monthly_plan") or []):
        season = str(row.get("season") or "")
        c.setFillColor(INK)
        c.setFont("Helvetica", 10.5)
        c.drawString(MARGIN, y, str(row.get("month", "")))
        pill = (colors.HexColor("#147D52") if season == "Peak"
                else colors.HexColor("#A06A00") if season == "Shoulder"
                else MUTED)
        c.setFillColor(pill)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(MARGIN + 2.2 * inch, y, season)
        c.setFillColor(INK)
        c.setFont("Helvetica", 10.5)
        c.drawRightString(PAGE_W - MARGIN, y,
                          str(row.get("budget_display", "")))
        y -= 19
        c.setStrokeColor(LINE)
        c.line(MARGIN, y + 6, PAGE_W - MARGIN, y + 6)
        if y < 1.2 * inch:
            break

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(PAGE_W - MARGIN, y - 6,
                      f"Annual total  {estimate.get('annual_total_display', '')}")
    _footer(c, 2, total_pages)
    c.showPage()

    # --- page 3: channels --------------------------------------------
    y = _header(c, "Channel mix", dealer)
    c.setFillColor(INK)
    y = _wrap(c, "These are the channels we recommend and sell. Anything "
                 "outside this list isn't part of the plan.",
              MARGIN, y, PAGE_W - 2 * MARGIN)
    y -= 12
    for ch in (estimate.get("channels") or []):
        c.setFillColor(CYAN)
        c.roundRect(MARGIN, y - 4, 4, 14, 2, fill=1, stroke=0)
        c.setFillColor(INK)
        c.setFont("Helvetica", 11)
        c.drawString(MARGIN + 16, y, str(ch))
        y -= 24
        if y < 1.2 * inch:
            break
    _footer(c, 3, total_pages)
    c.showPage()

    c.save()
    return buf.getvalue()
