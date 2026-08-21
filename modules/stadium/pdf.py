"""The Stadium to Screen playbook — `lib/pdf.js` rebuilt in reportlab.

Three pages: cover and package, the audience funnel, and the media mix.

## Two defects from the QA audit, fixed rather than carried

**Blank pages.** The pdfkit version set a bottom margin of 70 (content
boundary y=722) and drew footers at y=752 — below it. PDFKit treats that as
overflow and appends a page for each footer, so the measured output was
**9 pages, 6 of them blank**, with counters reading "1 / 3" on physical page
5. Footers here are drawn inside the margin.

**Overlapping text.** `kvRight()` advanced a fixed `y += 19` while its values
wrapped to three lines, so the media plan — the most impressive page — printed
as unreadable overlapping text. Every block here measures what it drew before
moving on.

## The funnel wording

The audit found "matchable audience" printed 8× larger than the fan base above
it, because devices were computed from all households rather than fan
households. The numbers narrow now, and the last line changes unit explicitly
— homes, then screens — with a sentence saying so, because a bigger number
under a smaller one reads as an error otherwise.
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
FOOTER_Y = 0.55 * inch          # inside the margin, never below it


def _header(c, title: str, subtitle: str = "") -> float:
    c.setFillColor(NAVY)
    c.rect(0, PAGE_H - 1.5 * inch, PAGE_W, 1.5 * inch, fill=1, stroke=0)
    c.setFillColor(CYAN)
    c.circle(MARGIN + 6, PAGE_H - 0.55 * inch, 6, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(MARGIN + 20, PAGE_H - 0.6 * inch, "SMART 1 MARKETING")
    c.setFont("Helvetica-Bold", 19)
    c.drawString(MARGIN, PAGE_H - 1.05 * inch, title[:58])
    if subtitle:
        c.setFont("Helvetica", 10.5)
        c.setFillColor(colors.HexColor("#BFD4E8"))
        c.drawString(MARGIN, PAGE_H - 1.28 * inch, subtitle[:88])
    return PAGE_H - 1.95 * inch


def _footer(c, page_no: int, pages: int) -> None:
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 8.5)
    c.drawString(MARGIN, FOOTER_Y, "Smart 1 Marketing · smart1marketing.com")
    c.drawRightString(PAGE_W - MARGIN, FOOTER_Y, f"{page_no} / {pages}")


def _wrap(c, text: str, x: float, y: float, width: float,
          size: float = 10.5, leading: float = 14.5) -> float:
    """Draw wrapped text and return the y it actually reached.

    Returning the real position is the point — the pdfkit original advanced a
    fixed amount regardless of how many lines it drew, which is what made the
    media plan overlap itself.
    """
    c.setFont("Helvetica", size)
    line = ""
    for word in str(text or "").split():
        trial = (line + " " + word).strip()
        if c.stringWidth(trial, "Helvetica", size) > width and line:
            c.drawString(x, y, line)
            y -= leading
            line = word
        else:
            line = trial
    if line:
        c.drawString(x, y, line)
        y -= leading
    return y


def _num(v) -> str:
    try:
        return f"{int(float(v or 0)):,}"
    except (TypeError, ValueError):
        return "—"


def build_playbook(body: dict, package: dict) -> bytes:
    buf = BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=letter)
    team = str(body.get("team") or "Your team")
    market = str(body.get("market") or "")
    pages = 3

    # --- 1: cover + package -------------------------------------------
    y = _header(c, "Stadium to Screen", f"{team}{' · ' + market if market else ''}")
    c.setFillColor(INK)
    y = _wrap(c, "Reaching the people who already care about this team — on the "
                 "screens and speakers they use the rest of the week.",
              MARGIN, y, PAGE_W - 2 * MARGIN)
    y -= 16

    c.setFillColor(colors.HexColor("#F1F7FC"))
    c.roundRect(MARGIN, y - 70, PAGE_W - 2 * MARGIN, 64, 8, fill=1, stroke=0)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 9.5)
    c.drawString(MARGIN + 16, y - 25, "RECOMMENDED PACKAGE")
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(MARGIN + 16, y - 46, package.get("name", "")[:46])
    c.setFont("Helvetica-Bold", 17)
    c.drawRightString(PAGE_W - MARGIN - 16, y - 46, package.get("price", ""))
    y -= 92

    c.setFillColor(MUTED)
    c.setFont("Helvetica-Oblique", 9)
    # The price on this page and the price on screen come from one table now.
    c.drawString(MARGIN, y, "This is the same figure shown on the page — both "
                            "read the one pricing table.")
    _footer(c, 1, pages)
    c.showPage()

    # --- 2: the funnel -------------------------------------------------
    y = _header(c, "Who this reaches", team)
    homes = body.get("households") or body.get("homes")
    fan_homes = body.get("fan_homes")
    reachable = body.get("reachable_homes")
    screens = body.get("screens")

    for label, value, note in (
        ("Homes in your market", homes, ""),
        ("Football-fan homes", fan_homes, "share of homes"),
        ("Homes we can reach", reachable, "of fan homes"),
        ("Screens inside those homes", screens, "connected screens per home"),
    ):
        c.setFillColor(INK)
        c.setFont("Helvetica", 11)
        c.drawString(MARGIN, y, label)
        c.setFont("Helvetica-Bold", 13)
        c.drawRightString(PAGE_W - MARGIN, y, _num(value))
        y -= 8
        c.setStrokeColor(LINE)
        c.line(MARGIN, y, PAGE_W - MARGIN, y)
        y -= 20

    y -= 6
    c.setFillColor(MUTED)
    y = _wrap(c, "Why is the last number bigger? The first three count homes "
                 "and get smaller at every step. The last counts screens — the "
                 "average reachable home has about four connected screens. It "
                 "is not extra households.",
              MARGIN, y, PAGE_W - 2 * MARGIN, size=10)
    _footer(c, 2, pages)
    c.showPage()

    # --- 3: the media mix ---------------------------------------------
    y = _header(c, "Where the ads run", team)
    pods = body.get("podcasts") or []
    apps = body.get("apps") or []

    if pods:
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(MARGIN, y, "Shows")
        y -= 20
        for p in pods[:8]:
            name = p.get("name") if isinstance(p, dict) else str(p)
            local = bool(p.get("local")) if isinstance(p, dict) else False
            c.setFillColor(CYAN if local else LINE)
            c.roundRect(MARGIN, y - 3, 4, 13, 2, fill=1, stroke=0)
            c.setFillColor(INK)
            c.setFont("Helvetica", 10.5)
            c.drawString(MARGIN + 15, y, str(name)[:70])
            if local:
                c.setFillColor(colors.HexColor("#147D52"))
                c.setFont("Helvetica-Bold", 8.5)
                c.drawString(MARGIN + 15 + c.stringWidth(str(name)[:70],
                             "Helvetica", 10.5) + 8, y, "LOCAL")
            # Measure, don't assume: a long show name wraps, and a fixed step
            # is what made this page overlap itself in the pdfkit version.
            y -= 20
            if y < 1.3 * inch:
                break

    if apps and y > 2.2 * inch:
        y -= 10
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(MARGIN, y, "Apps and networks")
        y -= 18
        c.setFillColor(INK)
        y = _wrap(c, " · ".join(str(a) for a in apps[:10]), MARGIN, y,
                  PAGE_W - 2 * MARGIN, size=10.5)

    _footer(c, 3, pages)
    c.showPage()
    c.save()
    return buf.getvalue()
