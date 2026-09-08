"""The script set as a PDF -- a draft, and it says so on every page.

Text by default is the deliverable (see the build spec); this is the printable
copy of it. Every concept carries the DRAFT watermark rule from the module
docstring one level up: nothing here has ever claimed a human wrote it, and
the legal-line slot prints "Supplied by client" rather than being left blank
with no explanation, so a rep does not mistake an empty box for an oversight.
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

NAVY = colors.HexColor("#0D2340")
CYAN = colors.HexColor("#3EC6F0")
INK = colors.HexColor("#1B2733")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#E2E8F0")

MARGIN = 0.85 * inch


def _styles() -> dict:
    return {
        "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=15,
                                textColor=NAVY, spaceAfter=4, leading=19),
        "meta": ParagraphStyle("meta", fontName="Helvetica", fontSize=9,
                               textColor=MUTED, spaceAfter=14, leading=12),
        "h": ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=12,
                            textColor=NAVY, spaceBefore=14, spaceAfter=4,
                            leading=15),
        "label": ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=8.6,
                                textColor=MUTED, spaceBefore=8, spaceAfter=2,
                                leading=11),
        "p": ParagraphStyle("p", fontName="Helvetica", fontSize=9.8,
                            textColor=INK, leading=14.6, spaceAfter=4),
        "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8.6,
                                textColor=MUTED, leading=12),
        "flag": ParagraphStyle("flag", fontName="Helvetica-Oblique", fontSize=8.6,
                               textColor=colors.HexColor("#8A5300"), leading=11.5),
    }


def _furniture(canvas, doc):
    canvas.saveState()
    w, h = letter
    canvas.setFillColor(NAVY)
    canvas.rect(0, h - 0.7 * inch, w, 0.7 * inch, fill=1, stroke=0)
    canvas.setFillColor(CYAN)
    canvas.circle(MARGIN + 5, h - 0.35 * inch, 5, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 9.5)
    canvas.drawString(MARGIN + 17, h - 0.39 * inch, "SMART 1 MARKETING — DRAFT")
    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(w - MARGIN, h - 0.39 * inch, "Radio / Audio Scripts")
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(MARGIN, 0.5 * inch,
                      "Draft — nothing here has been read or approved by the client.")
    canvas.drawRightString(w - MARGIN, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


def _tag_table(styles, tag: dict) -> Table:
    rows = [["Name", tag.get("name") or "—"],
           ["Offer", tag.get("offer") or "(none supplied)"],
           ["Call to action", tag.get("cta") or "—"],
           ["Phone (spoken)", tag.get("phone_spoken") or "—"],
           ["Web address (spoken)", tag.get("url_spoken") or "—"]]
    data = [[Paragraph(f"<b>{r[0]}</b>", styles["small"]),
            Paragraph(r[1], styles["small"])] for r in rows]
    t = Table(data, colWidths=[1.5 * inch, 4.5 * inch])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def build_script_pdf(row: dict) -> bytes:
    styles = _styles()
    buf = BytesIO()
    w, h = letter
    client = row.get("client_name") or "(no client on file)"
    doc = BaseDocTemplate(buf, pagesize=letter,
                          leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=1.0 * inch, bottomMargin=0.8 * inch,
                          title=f"Radio scripts — {client}",
                          author="Smart 1 Marketing")
    frame = Frame(MARGIN, 0.8 * inch, w - 2 * MARGIN,
                 h - 1.9 * inch, id="f")
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=_furniture)])

    story = [Paragraph(client, styles["title"])]
    market = row.get("market") or (row.get("brief") or {}).get("market")
    when = row.get("created_at") or datetime.now(timezone.utc).isoformat()
    meta_bits = [b for b in (market, f"generated {when}") if b]
    story.append(Paragraph(" · ".join(meta_bits), styles["meta"]))

    for i, concept in enumerate(row.get("concepts") or [], start=1):
        story.append(Paragraph(f"Concept {i}: {concept.get('idea') or '(untitled)'}",
                               styles["h"]))
        if concept.get("talent_direction"):
            story.append(Paragraph("Talent direction", styles["label"]))
            story.append(Paragraph(concept["talent_direction"], styles["p"]))
        if concept.get("sfx_notes"):
            story.append(Paragraph("SFX / bed notes", styles["label"]))
            story.append(Paragraph(concept["sfx_notes"], styles["p"]))
        scripts = concept.get("scripts") or {}
        for length in ("60", "30", "15"):
            text = scripts.get(length)
            if not text:
                continue
            story.append(Paragraph(f":{length}", styles["label"]))
            story.append(Paragraph(text.replace("\n", "<br/>"), styles["p"]))
        story.append(Paragraph("Tag", styles["label"]))
        story.append(_tag_table(styles, concept.get("tag") or {}))
        story.append(Paragraph("Legal line", styles["label"]))
        story.append(Paragraph(concept.get("legal_line_label")
                               or "Supplied by client", styles["small"]))
        for flag in concept.get("flags") or []:
            story.append(Paragraph("⚠ " + str(flag.get("message") or ""),
                                   styles["flag"]))
        story.append(Spacer(1, 10))

    doc.build(story)
    return buf.getvalue()


__all__ = ["build_script_pdf"]
