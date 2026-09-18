"""What a sponsor sees at the end of the month.

Two deliverables sit on top of the rollup: a monthly PDF that lands in
the sponsor's inbox on the 1st, and a read-only portal they can check
whenever they want. Both read `camhub_daily_stats` -- never raw events
-- so a hundred sponsors on the 1st is still one query per placement.

The report answers the four questions a sponsor asks and preempts the
fifth ("how do I know this is real"):

- **Impressions, clicks, CTR** for the month
- **Month over month** with the prior month beside them
- **A daily impressions chart** across the month, drawn in pure PIL so
  the scheduler never brings in matplotlib
- **Their creative as it actually ran** -- sponsors forget what they
  are running, and a refresh is an engagement touchpoint
- **A one-line footnote** on what an impression is, so the number
  survives the "prove it" question

Viewable share -- the fraction of pageviews the unit was actually
viewable on -- is on every row, per the spec: publishing that gap is
what makes the presenting slot obviously worth more.
"""
from __future__ import annotations

import calendar
import csv
import io
import logging
from datetime import date, timedelta

from sqlalchemy import select

from .models import DailyStat, Placement, Sponsor, session
from .tracking import stats as _page_stats

log = logging.getLogger("hub")

MONTHS = ("January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December")

# 60 days matches the spec's "renewal note when the end date is inside
# 60 days" -- a report going out on the 1st with a flight ending in
# March lets the sale be closed in February rather than reopened cold.
RENEWAL_WINDOW_DAYS = 60


def month_range(year: int, month: int) -> tuple[str, str]:
    """The ISO first-of-month and last-of-month for the year/month."""
    year, month = int(year), int(month)
    if not (1 <= month <= 12):
        raise ValueError(f"month must be 1-12, got {month}")
    last = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"


def previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _label(y: int, m: int) -> str:
    return f"{MONTHS[m - 1]} {y}"


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 2) if d else 0.0


def _delta(now: int, was: int) -> dict:
    """A number, its prior figure and how to draw it. `direction` is
    "up", "down" or "flat" so the PDF and the portal read from the
    same field and the arrow never disagrees with the sign."""
    diff = int(now) - int(was)
    pct = round(100.0 * diff / was, 1) if was else (100.0 if now else 0.0)
    direction = "up" if diff > 0 else ("down" if diff < 0 else "flat")
    return {"now": int(now), "was": int(was), "diff": diff, "pct": pct,
            "direction": direction}


def _pct_delta(now: float, was: float) -> dict:
    """Same shape for percentages -- CTR and viewable share."""
    diff = round(float(now) - float(was), 2)
    direction = "up" if diff > 0 else ("down" if diff < 0 else "flat")
    return {"now": float(now), "was": float(was), "diff": diff,
            "direction": direction}


def _placement_row(page_stats: dict, placement: dict) -> dict:
    """One placement's numbers, joined to its own row so a sponsor's
    slot label appears whether or not it had any impressions."""
    pl_id = placement["id"]
    per = (page_stats.get("placements") or {}).get(pl_id) or {}
    return {"placement_id": pl_id,
            "name": placement.get("name") or placement.get("sponsor_name") or "Placement",
            "position": placement.get("position") or "supporting",
            "sort_order": placement.get("sort_order") or 0,
            "impressions": int(per.get("impressions") or 0),
            "clicks": int(per.get("clicks") or 0),
            "ctr": _pct(int(per.get("clicks") or 0), int(per.get("impressions") or 0)),
            "unique_sessions": int(per.get("unique_sessions") or 0),
            "filtered": int(per.get("filtered") or 0),
            "viewable_share": per.get("viewable_share")}


def _flight_status(placement: dict, today: date) -> dict:
    """Days elapsed, days remaining and a renewal note when the end
    date is inside the 60-day window. Open-ended flights say so."""
    start = placement.get("start_date") or ""
    end = placement.get("end_date") or ""
    parts: dict = {"start": start, "end": end,
                   "elapsed": None, "remaining": None,
                   "state": "open", "renewal_note": ""}
    try:
        start_d = date.fromisoformat(start) if start else None
    except ValueError:
        start_d = None
    try:
        end_d = date.fromisoformat(end) if end else None
    except ValueError:
        end_d = None
    if start_d:
        parts["elapsed"] = max(0, (today - start_d).days)
    if end_d:
        remaining = (end_d - today).days
        parts["remaining"] = remaining
        if remaining < 0:
            parts["state"] = "ended"
        elif remaining <= RENEWAL_WINDOW_DAYS:
            parts["state"] = "renewing"
            parts["renewal_note"] = (
                f"Flight ends {end} -- {remaining} days from today.")
        else:
            parts["state"] = "live"
    elif start_d and start_d > today:
        parts["state"] = "scheduled"
    return parts


# --------------------------------------------------------------- stats

def sponsor_monthly(sponsor_id: int, year: int, month: int,
                    today: date | None = None) -> dict:
    """Everything the sponsor's report and portal read.

    One payload rather than several queries so the PDF and the portal
    draw the same number. `today` is only used by the flight-status
    fields; the stats window is the whole month regardless."""
    today = today or date.today()
    with session() as s:
        sponsor = s.get(Sponsor, int(sponsor_id))
        if sponsor is None:
            raise LookupError(f"sponsor {sponsor_id} not found")
        pls = s.execute(select(Placement).where(Placement.sponsor_id == int(sponsor_id))
                        ).scalars().all()
        placements = [{
            "id": pl.id, "page_id": pl.page_id, "position": pl.position,
            "name": pl.name, "sponsor_name": sponsor.name,
            "headline": pl.headline, "body": pl.body, "cta_label": pl.cta_label,
            "url": pl.url, "logo_url": pl.logo_url, "image_url": pl.image_url,
            "alt_text": pl.alt_text, "start_date": pl.start_date,
            "end_date": pl.end_date, "sort_order": pl.sort_order,
        } for pl in pls]
    if not placements:
        raise LookupError(f"sponsor {sponsor_id} has no placements")

    start, end = month_range(year, month)
    prev_y, prev_m = previous_month(year, month)
    p_start, p_end = month_range(prev_y, prev_m)

    # One tracking.stats() call per page the sponsor sits on, so a
    # sponsor with slots on two pages sees a report that adds them up.
    pages = sorted({pl["page_id"] for pl in placements})
    now_by_pl: dict[int, dict] = {}
    was_by_pl: dict[int, dict] = {}
    page_now: dict[int, dict] = {}
    page_was: dict[int, dict] = {}
    for pid in pages:
        page_now[pid] = _page_stats(pid, start, end)
        page_was[pid] = _page_stats(pid, p_start, p_end)
    for pl in placements:
        now_by_pl[pl["id"]] = _placement_row(page_now[pl["page_id"]], pl)
        was_by_pl[pl["id"]] = _placement_row(page_was[pl["page_id"]], pl)

    def sum_over(rows: dict) -> dict:
        imp = sum(r["impressions"] for r in rows.values())
        clk = sum(r["clicks"] for r in rows.values())
        return {"impressions": imp, "clicks": clk, "ctr": _pct(clk, imp),
                "unique_sessions": sum(r["unique_sessions"] for r in rows.values()),
                "filtered": sum(r["filtered"] for r in rows.values())}

    totals_now = sum_over(now_by_pl)
    totals_was = sum_over(was_by_pl)

    # Viewable share is the share of pageviews the sponsor's units were
    # viewable on. For a sponsor on one page this equals the placement's
    # own viewable_share; for a sponsor on several pages it is the
    # weighted average of them, which is what the sentence "the unit was
    # viewable on N% of pageviews" means when there is more than one.
    def viewable_share(rows_by_pl: dict, pages_view: dict) -> float | None:
        views = sum(int(((pages_view[pid].get("page") or {}).get("pageviews") or 0)) for pid in pages)
        if not views:
            return None
        weighted = 0
        for pl_id, r in rows_by_pl.items():
            weighted += int(r["impressions"] or 0)
        return round(100.0 * weighted / views, 1) if views else None

    v_now = viewable_share(now_by_pl, page_now)
    v_was = viewable_share(was_by_pl, page_was)

    # Daily impressions across the month for the chart: one figure per
    # day, summed across every placement this sponsor holds.
    days = _day_series(start, end)
    daily = {d: 0 for d in days}
    for pid in pages:
        for pl in [p for p in placements if p["page_id"] == pid]:
            row = (page_now[pid].get("placements") or {}).get(pl["id"]) or {}
            for d, cell in (row.get("daily") or {}).items():
                if d in daily:
                    daily[d] += int(cell.get("impressions") or 0)
    daily_series = [{"day": d, "impressions": daily[d]} for d in days]

    # Flight status, most-critical first: an ended flight leads, a
    # renewing one is next, and open-ended ones are last so a sponsor
    # who has to renew reads that at the top of their own report.
    flights = [{**_flight_status(pl, today), "placement_id": pl["id"],
                "name": pl["name"] or sponsor.name,
                "position": pl["position"]} for pl in placements]
    order = {"ended": 0, "renewing": 1, "live": 2, "scheduled": 3, "open": 4}
    flights.sort(key=lambda f: order.get(f["state"], 9))

    placement_rows = []
    for pl in placements:
        n = now_by_pl[pl["id"]]
        w = was_by_pl[pl["id"]]
        placement_rows.append({
            "id": pl["id"], "page_id": pl["page_id"], "name": n["name"],
            "position": pl["position"], "sort_order": pl["sort_order"] or 0,
            "now": n, "was": w,
            "impressions_delta": _delta(n["impressions"], w["impressions"]),
            "clicks_delta": _delta(n["clicks"], w["clicks"]),
            "ctr_delta": _pct_delta(n["ctr"], w["ctr"]),
            "creative": {"headline": pl["headline"] or "",
                         "body": pl["body"] or "",
                         "cta_label": pl["cta_label"] or "",
                         "url": pl["url"] or "",
                         "logo_url": pl["logo_url"] or "",
                         "image_url": pl["image_url"] or "",
                         "alt_text": pl["alt_text"] or ""}
        })
    placement_rows.sort(key=lambda r: (0 if r["position"] == "presenting" else 1,
                                       r["sort_order"], r["id"]))

    return {
        "sponsor": {"id": int(sponsor.id), "name": sponsor.name,
                    "category": sponsor.category or "",
                    "email": sponsor.email or "",
                    "contact_name": sponsor.contact_name or "",
                    "website": sponsor.website or ""},
        "period": {"year": int(year), "month": int(month),
                   "label": _label(int(year), int(month)),
                   "start": start, "end": end,
                   "prior_label": _label(prev_y, prev_m),
                   "prior_start": p_start, "prior_end": p_end,
                   "days": len(days)},
        "totals": {
            "now": {**totals_now, "viewable_share": v_now},
            "was": {**totals_was, "viewable_share": v_was},
            "impressions": _delta(totals_now["impressions"], totals_was["impressions"]),
            "clicks": _delta(totals_now["clicks"], totals_was["clicks"]),
            "ctr": _pct_delta(totals_now["ctr"], totals_was["ctr"]),
            "viewable_share": _pct_delta(v_now or 0, v_was or 0) if v_now is not None or v_was is not None else None,
        },
        "placements": placement_rows,
        "flights": flights,
        "daily": daily_series,
        "footnote": ("A viewable impression is what the page's script "
                     "reports after at least half of the ad has been on "
                     "screen for a continuous second (the IAB display "
                     "standard). Crawler traffic, instant clicks and "
                     "clicks on unscrolled pages are excluded."),
    }


def _day_series(start: str, end: str) -> list[str]:
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def sponsor_range(sponsor_id: int, start: str, end: str) -> dict:
    """The portal's date-range window: the sponsor's placements over any
    start..end days, with the same shape sponsor_monthly() produces so
    the portal renders one template."""
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    if d1 < d0:
        raise ValueError("end must be on or after start")
    with session() as s:
        sponsor = s.get(Sponsor, int(sponsor_id))
        if sponsor is None:
            raise LookupError(f"sponsor {sponsor_id} not found")
        pls = s.execute(select(Placement).where(Placement.sponsor_id == int(sponsor_id))
                        ).scalars().all()
        placements = [{"id": pl.id, "page_id": pl.page_id, "position": pl.position,
                       "name": pl.name, "sponsor_name": sponsor.name,
                       "sort_order": pl.sort_order or 0,
                       "start_date": pl.start_date, "end_date": pl.end_date}
                      for pl in pls]
    if not placements:
        raise LookupError(f"sponsor {sponsor_id} has no placements")
    pages = sorted({pl["page_id"] for pl in placements})
    per_page = {pid: _page_stats(pid, start, end) for pid in pages}
    days = _day_series(start, end)
    daily = {d: 0 for d in days}
    per_pl: list[dict] = []
    tot_imp = tot_clk = 0
    for pl in placements:
        stats = _placement_row(per_page[pl["page_id"]], pl)
        per_pl.append(stats)
        tot_imp += stats["impressions"]
        tot_clk += stats["clicks"]
        row = (per_page[pl["page_id"]].get("placements") or {}).get(pl["id"]) or {}
        for d, cell in (row.get("daily") or {}).items():
            if d in daily:
                daily[d] += int(cell.get("impressions") or 0)
    pv = sum(int(((per_page[pid].get("page") or {}).get("pageviews") or 0)) for pid in pages)
    viewable = round(100.0 * tot_imp / pv, 1) if pv else None
    return {
        "sponsor": {"id": int(sponsor.id), "name": sponsor.name,
                    "category": sponsor.category or ""},
        "window": {"start": start, "end": end, "days": len(days)},
        "totals": {"impressions": tot_imp, "clicks": tot_clk,
                   "ctr": _pct(tot_clk, tot_imp),
                   "viewable_share": viewable},
        "placements": sorted(per_pl, key=lambda r: (0 if r["position"] == "presenting" else 1,
                                                    r["sort_order"], r["placement_id"])),
        "daily": [{"day": d, "impressions": daily[d]} for d in days],
    }


# ------------------------------------------------------------- daily chart

def daily_chart_png(series: list[dict], *, width: int = 720, height: int = 220,
                    accent: str = "#1d4ed8") -> bytes:
    """A plain bars-across-a-baseline chart, drawn with Pillow.

    Matplotlib would draw a prettier one, but the scheduler runs this
    on the same thread as the refresh job and a monthly report should
    not be the reason a lake gauge misses a poll. The chart's job is
    to show shape -- weekend peaks, weather spikes -- and to answer
    "which day was it that spiked", not to be a research figure.
    """
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (int(width), int(height)), "#ffffff")
    dr = ImageDraw.Draw(im)
    padding_top, padding_side, padding_bottom = 20, 30, 32
    inner_w = width - 2 * padding_side
    inner_h = height - padding_top - padding_bottom
    n = max(1, len(series))
    peak = max((int(r.get("impressions") or 0) for r in series), default=0)
    # A rounded ceiling so the axis doesn't say "2,341" -- either
    # nothing (peak 0), or the next round number above peak.
    ceiling = _round_ceiling(peak)
    bar_w = inner_w / n
    # Baseline
    baseline_y = height - padding_bottom
    dr.line([(padding_side, baseline_y), (width - padding_side, baseline_y)],
            fill="#d1d5db", width=1)
    # Gridlines at 25/50/75/100%
    for share in (0.25, 0.5, 0.75, 1.0):
        y = int(baseline_y - inner_h * share)
        dr.line([(padding_side, y), (width - padding_side, y)],
                fill="#eef2f7", width=1)
    for i, row in enumerate(series):
        v = max(0, int(row.get("impressions") or 0))
        h = int(inner_h * (v / ceiling)) if ceiling else 0
        x0 = padding_side + i * bar_w + max(0.5, bar_w * 0.12)
        x1 = padding_side + (i + 1) * bar_w - max(0.5, bar_w * 0.12)
        y0 = baseline_y - h
        dr.rectangle([x0, y0, x1, baseline_y], fill=accent)
    # Axis labels: the peak at top, zero at baseline, and every 5th day
    try:
        font = ImageFont.load_default()
    except Exception:  # noqa: BLE001
        font = None
    dr.text((4, padding_top - 8), f"{ceiling:,}" if ceiling else "0",
            fill="#334155", font=font)
    dr.text((4, baseline_y - 6), "0", fill="#334155", font=font)
    for i, row in enumerate(series):
        if i == 0 or i == len(series) - 1 or (i + 1) % 7 == 0:
            x = padding_side + i * bar_w + bar_w / 2
            day_num = row.get("day", "")[-2:]
            dr.text((x - 6, baseline_y + 4), day_num, fill="#64748b", font=font)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _round_ceiling(peak: int) -> int:
    """Next 'nice' number at or above peak (1, 2, 5 x 10^n), so the
    axis reads as a real chart's would."""
    if peak <= 0:
        return 0
    import math
    mag = 10 ** int(math.floor(math.log10(peak)))
    for m in (1, 2, 5, 10):
        if m * mag >= peak:
            return m * mag
    return peak


# ------------------------------------------------------------- PDF

def render_monthly_pdf(report: dict, *, brand: str = "Smart 1 Marketing") -> bytes:
    """One-page PDF from a `sponsor_monthly()` payload.

    reportlab-only; the chart is a Pillow PNG embedded as a Flowable
    Image. The whole page is drawn from `report` -- no second query
    here -- so the PDF and the portal never disagree.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (Image as PdfImage, PageBreak, Paragraph,
                                    SimpleDocTemplate, Spacer, Table, TableStyle)

    sponsor = report["sponsor"]
    period = report["period"]
    totals = report["totals"]
    flights = report["flights"]
    placements = report["placements"]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER,
                            leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=0.55 * inch, bottomMargin=0.55 * inch,
                            title=f"{sponsor['name']} -- {period['label']}",
                            author=brand)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("H1", parent=styles["Heading1"], fontSize=18,
                              leading=22, textColor=colors.HexColor("#1a2e58"),
                              spaceAfter=4))
    styles.add(ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12,
                              leading=15, textColor=colors.HexColor("#1a2e58"),
                              spaceBefore=10, spaceAfter=4))
    styles.add(ParagraphStyle("Sub", parent=styles["Normal"], fontSize=10,
                              leading=13, textColor=colors.HexColor("#475569"),
                              spaceAfter=0))
    styles.add(ParagraphStyle("Body", parent=styles["Normal"], fontSize=10,
                              leading=13))
    styles.add(ParagraphStyle("Small", parent=styles["Normal"], fontSize=8.5,
                              leading=11, textColor=colors.HexColor("#64748b")))

    story: list = []
    story.append(Paragraph(f"{sponsor['name']} -- Monthly Report", styles["H1"]))
    story.append(Paragraph(f"{period['label']} &nbsp;&middot;&nbsp; {brand}",
                           styles["Sub"]))
    story.append(Spacer(1, 6))

    # Headline table: three numbers with prior-month figures beside them.
    def cell(label, now, was, direction):
        arrow = "▲" if direction == "up" else ("▼" if direction == "down" else "—")
        return [Paragraph(f"<b>{label}</b>", styles["Sub"]),
                Paragraph(f'<font size="16" color="#1a2e58"><b>{now}</b></font> '
                          f'<font size="10" color="#475569">{arrow} vs {was}</font>',
                          styles["Body"])]
    imp = totals["impressions"]
    clk = totals["clicks"]
    ctr = totals["ctr"]
    headline = Table([
        [cell("Viewable impressions",
              f"{imp['now']:,}", f"{imp['was']:,}", imp["direction"])[0],
         cell("Clicks", f"{clk['now']:,}", f"{clk['was']:,}", clk["direction"])[0],
         cell("Click-through rate", f"{totals['now']['ctr']:.2f}%",
              f"{totals['was']['ctr']:.2f}%", ctr["direction"])[0]],
        [cell("Viewable impressions",
              f"{imp['now']:,}", f"{imp['was']:,}", imp["direction"])[1],
         cell("Clicks", f"{clk['now']:,}", f"{clk['was']:,}", clk["direction"])[1],
         cell("Click-through rate", f"{totals['now']['ctr']:.2f}%",
              f"{totals['was']['ctr']:.2f}%", ctr["direction"])[1]],
    ], colWidths=[2.4 * inch, 2.4 * inch, 2.4 * inch])
    headline.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 6),
                                  ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                                  ("TOPPADDING", (0, 0), (-1, -1), 3),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                                  ("LINEBELOW", (0, 0), (-1, 0), 0.4,
                                   colors.HexColor("#e2e8f0"))]))
    story.append(headline)

    if totals.get("viewable_share") is not None and totals["now"]["viewable_share"] is not None:
        vs = totals["now"]["viewable_share"]
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"<b>Viewable on {vs:.1f}%</b> of pageviews. "
            "The share of visits your unit was actually on screen -- the "
            "number a presenting slot should be judged against.",
            styles["Small"]))

    # Daily chart.
    story.append(Paragraph("Daily impressions", styles["H2"]))
    chart = daily_chart_png(report["daily"])
    story.append(PdfImage(io.BytesIO(chart), width=7.2 * inch, height=2.2 * inch))

    # Per-placement rows.
    story.append(Paragraph("Placements", styles["H2"]))
    tbl = [["Placement", "Position", "Impressions", "Clicks", "CTR", "Viewable on"]]
    for row in placements:
        vs = row["now"].get("viewable_share")
        tbl.append([row["name"], row["position"].title(),
                    f"{row['now']['impressions']:,}",
                    f"{row['now']['clicks']:,}",
                    f"{row['now']['ctr']:.2f}%",
                    f"{vs}%" if vs is not None else "—"])
    ptbl = Table(tbl, colWidths=[2.5 * inch, 1.0 * inch, 1.3 * inch, 0.9 * inch, 0.7 * inch, 0.9 * inch])
    ptbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f6fa")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1a2e58")),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(ptbl)

    # Flight status.
    story.append(Paragraph("Flight status", styles["H2"]))
    ftbl = [["Placement", "Start", "End", "Days elapsed", "Days remaining", "State"]]
    for f in flights:
        state = f["state"]
        state_label = {"live": "Running", "renewing": "Renewing soon",
                       "ended": "Ended", "scheduled": "Scheduled",
                       "open": "Open-ended"}[state]
        ftbl.append([f["name"], f["start"] or "—", f["end"] or "—",
                     str(f["elapsed"]) if f["elapsed"] is not None else "—",
                     str(f["remaining"]) if f["remaining"] is not None else "—",
                     state_label])
    ftable = Table(ftbl, colWidths=[2.3 * inch, 0.9 * inch, 0.9 * inch, 1.05 * inch, 1.15 * inch, 1.0 * inch])
    ftable.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f6fa")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1a2e58")),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
        ("ALIGN", (3, 1), (4, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(ftable)
    for f in flights:
        if f["renewal_note"]:
            story.append(Spacer(1, 4))
            story.append(Paragraph(f"<b>{f['name']}:</b> {f['renewal_note']}",
                                   styles["Body"]))

    # Creative preview. Each placement gets its own line block; images
    # too large to fetch are omitted rather than blocking the report.
    story.append(PageBreak())
    story.append(Paragraph("Creative as it ran", styles["H1"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Sponsors forget what they are running. Refresh the copy or the "
        "image, and the next report will show that visitors are seeing.",
        styles["Sub"]))
    for row in placements:
        story.append(Spacer(1, 10))
        creative = row["creative"]
        story.append(Paragraph(f"<b>{row['name']}</b> &middot; "
                               f"{row['position'].title()}", styles["H2"]))
        blocks = []
        if creative["image_url"]:
            img = _fetch_image(creative["image_url"], max_bytes=2 * 1024 * 1024)
            if img:
                blocks.append(PdfImage(io.BytesIO(img), width=2.4 * inch, height=1.5 * inch))
        text = []
        if creative["headline"]:
            text.append(f"<b>{creative['headline']}</b>")
        if creative["body"]:
            text.append(creative["body"])
        if creative["cta_label"] and creative["url"]:
            text.append(f"<i>{creative['cta_label']}</i>: {creative['url']}")
        if not blocks and not text:
            story.append(Paragraph("(no creative on file for this placement)",
                                   styles["Small"]))
            continue
        text_flow = Paragraph("<br/><br/>".join(text) or "&nbsp;", styles["Body"])
        if blocks:
            story.append(Table([[blocks[0], text_flow]],
                               colWidths=[2.6 * inch, 4.6 * inch]))
        else:
            story.append(text_flow)

    story.append(Spacer(1, 12))
    story.append(Paragraph(report["footnote"], styles["Small"]))
    doc.build(story)
    return buf.getvalue()


def _fetch_image(url: str, *, max_bytes: int = 2 * 1024 * 1024,
                 timeout: float = 5.0) -> bytes | None:
    """A best-effort image fetch. A failure is a missing image in the
    PDF, never an exception -- the report ships without it."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SmartHub-CamReports/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            head = resp.read(max_bytes + 1)
            if len(head) > max_bytes:
                return None
            return head
    except Exception as exc:  # noqa: BLE001
        log.info("camhub_report_image_skipped url=%s error=%s", url, type(exc).__name__)
        return None


# ------------------------------------------------------------- CSV

def csv_for_sponsor(sponsor_id: int, start: str, end: str) -> str:
    """The download the portal offers. One row per placement per day
    over the window, plus a totals footer. Reads only the rollup so a
    year of history takes the same query a month does.

    The output is CSV so a sponsor can drop it into a spreadsheet
    without asking their agency for help -- that is why the portal
    exists in the first place."""
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    if d1 < d0:
        raise ValueError("end must be on or after start")
    with session() as s:
        sponsor = s.get(Sponsor, int(sponsor_id))
        if sponsor is None:
            raise LookupError(f"sponsor {sponsor_id} not found")
        pls = s.execute(select(Placement).where(Placement.sponsor_id == int(sponsor_id))
                        ).scalars().all()
        placement_map = {pl.id: {"name": pl.name or sponsor.name,
                                 "position": pl.position,
                                 "page_id": pl.page_id} for pl in pls}
        if not placement_map:
            raise LookupError(f"sponsor {sponsor_id} has no placements")
        rows = s.execute(select(DailyStat).where(
            DailyStat.placement_id.in_(list(placement_map)),
            DailyStat.day >= start, DailyStat.day <= end).order_by(
            DailyStat.day, DailyStat.placement_id)).scalars().all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["day", "placement", "position", "impressions",
                     "clicks", "ctr", "unique_sessions"])
    tot_i = tot_c = tot_u = 0
    for r in rows:
        pl = placement_map.get(r.placement_id) or {}
        imp = int(r.impressions or 0)
        clk = int(r.clicks or 0)
        writer.writerow([r.day, _csv_safe(pl.get("name", "")),
                         _csv_safe(pl.get("position", "")),
                         imp, clk, f"{_pct(clk, imp):.2f}",
                         int(r.unique_sessions or 0)])
        tot_i += imp
        tot_c += clk
        tot_u += int(r.unique_sessions or 0)
    writer.writerow([])
    writer.writerow(["total", "", "", tot_i, tot_c,
                     f"{_pct(tot_c, tot_i):.2f}", tot_u])
    return buf.getvalue()


# CWE-1236: a sponsor whose name starts with `=`, `+`, `-`, `@`, or a
# leading tab or carriage return could execute a spreadsheet formula the
# moment the CSV is opened in Excel or Sheets. csv.writer quotes commas
# and newlines; it does not defend against this. Every operator-typed or
# person-supplied cell that goes into the CSV writes through this.
_CSV_INJECTION_LEADS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(cell: str) -> str:
    s = "" if cell is None else str(cell)
    if s and s[0] in _CSV_INJECTION_LEADS:
        return "'" + s
    return s
