"""The signed MSA as a PDF.

reportlab, like every other document in the Hub. The agreement text comes from
`app.MSA_BODY` — this file only lays it out, so the contract can never differ
between what someone read on screen and what they downloaded.

The signature block records who signed, when, and from what address. A typed
name is only as good as the record around it.
"""
from __future__ import annotations

from datetime import datetime
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
        "h": ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=11.5,
                            textColor=NAVY, spaceBefore=13, spaceAfter=5,
                            leading=15),
        "p": ParagraphStyle("p", fontName="Helvetica", fontSize=9.8,
                            textColor=INK, leading=14.2, spaceAfter=7),
        "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8.4,
                                textColor=MUTED, leading=11.5),
        "sig": ParagraphStyle("sig", fontName="Helvetica-Bold", fontSize=13,
                              textColor=INK, leading=17),
    }


def _furniture(canvas, doc):
    """Header band and footer, drawn inside the margins.

    Footers below the bottom margin make reportlab and pdfkit alike append
    blank pages — the stadium PDF shipped nine pages, six of them empty, for
    exactly this reason.
    """
    canvas.saveState()
    w, h = letter
    canvas.setFillColor(NAVY)
    canvas.rect(0, h - 0.72 * inch, w, 0.72 * inch, fill=1, stroke=0)
    canvas.setFillColor(CYAN)
    canvas.circle(MARGIN + 5, h - 0.36 * inch, 5, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 9.5)
    canvas.drawString(MARGIN + 17, h - 0.4 * inch, "SMART 1 MARKETING")
    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(w - MARGIN, h - 0.4 * inch,
                           "Master Services Agreement")
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(MARGIN, 0.5 * inch, "smart1marketing.com")
    canvas.drawRightString(w - MARGIN, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build_msa_pdf(sections, launch_times, rate_card_note, company: str,
                  address: str, signer: str, signed_at: datetime,
                  ip: str = "") -> bytes:
    buf = BytesIO()
    w, h = letter
    doc = BaseDocTemplate(buf, pagesize=letter,
                          leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=1.0 * inch, bottomMargin=0.8 * inch,
                          title=f"MSA — {company}", author="Smart 1 Marketing")
    frame = Frame(MARGIN, 0.8 * inch, w - 2 * MARGIN,
                  h - 1.8 * inch, id="body")
    doc.addPageTemplates([PageTemplate(id="std", frames=[frame],
                                       onPage=_furniture)])
    st = _styles()
    flow = []

    flow.append(Paragraph("Master Services Agreement", ParagraphStyle(
        "title", fontName="Helvetica-Bold", fontSize=17, textColor=NAVY,
        leading=21, spaceAfter=4)))
    flow.append(Paragraph(
        f"{company} &middot; {address}", st["small"]))
    flow.append(Spacer(1, 12))

    for sec in sections:
        flow.append(Paragraph(sec["heading"], st["h"]))
        for para in sec["paragraphs"]:
            flow.append(Paragraph(para, st["p"]))

    # Launch times, in the document rather than behind a link — the signature
    # covers them, so they have to be part of what was signed.
    flow.append(Paragraph("Campaign Launch Times", st["h"]))
    rows = [[Paragraph("<b>Product</b>", st["small"]),
             Paragraph("<b>Typical launch</b>", st["small"])]]
    for product, timing in launch_times:
        rows.append([Paragraph(product, st["small"]),
                     Paragraph(timing, st["small"])])
    table = Table(rows, colWidths=[2.1 * inch, w - 2 * MARGIN - 2.1 * inch])
    table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    flow.append(table)
    flow.append(Spacer(1, 8))

    flow.append(Paragraph("Rate Card", st["h"]))
    flow.append(Paragraph(rate_card_note, st["p"]))

    # --- signature ---------------------------------------------------
    flow.append(Spacer(1, 16))
    flow.append(Paragraph("Accepted and agreed", st["h"]))
    flow.append(Paragraph(
        "The signer confirms they have read and agree to the rate card and to "
        "the campaign launch times set out above.", st["p"]))
    flow.append(Spacer(1, 6))
    flow.append(Paragraph(signer, st["sig"]))
    flow.append(Paragraph(
        f"for {company}", st["small"]))
    flow.append(Spacer(1, 4))
    stamp = signed_at.strftime("%d %B %Y at %H:%M UTC")
    flow.append(Paragraph(
        f"Signed electronically on {stamp}"
        + (f" from {ip}" if ip else "")
        + ". A typed signature is accepted as an original.", st["small"]))

    doc.build(flow)
    return buf.getvalue()
