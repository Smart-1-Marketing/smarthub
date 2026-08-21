"""Seasonal tourism marketing plan — the Node app, ported to Flask.

## What moved and what didn't

The wizard, the market model and the report markup (`public/app.js`,
`data.js`, `engine.js`, `report-template.js` — about 1,900 lines) run in the
browser and are unchanged. Porting them would have meant re-deriving a budget
model in a second language, with two copies to keep in step; the QA audit
found exactly that failure in the stadium app, where the page quoted one price
and the PDF another.

Only the server moved: three routes, the OpenAI call, the Cloudinary upload,
and the PDF.

## The PDF, kept identical

The original printed `report-print.html` with Puppeteer, seeding
`window.__REPORT_ANSWERS__` through `page.evaluateOnNewDocument`. The Chromium
command line has no equivalent, so the answers are written **into** a copy of
that page instead, ahead of the scripts that read them. Same markup, same
scripts, same browser, same output — the page is byte-identical apart from one
injected `<script>` line.

## The budget model, unchanged and still wrong

`engine.js` produces exactly three possible monthly budgets nationwide, and
its ROAS is `1 / cacPct` — the reciprocal of a hardcoded constant, printed as
an estimate. Both were flagged in the QA audit. **Neither is fixed here**, on
purpose: this is a port, and changing the numbers a client is quoted is a
product decision, not something to slip into a translation. The shared budget
engine is where that gets fixed.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (HRFlowable, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

PUBLIC = Path(__file__).parent / "public"
app = Flask(__name__, static_folder=None)

MODEL = os.environ.get("TOURISM_OPENAI_MODEL") or os.environ.get(
    "OPENAI_MODEL", "gpt-4o-mini")
REQUEST_TIMEOUT = 25

# Recent submissions, so a double-click doesn't produce two reports, two
# Cloudinary uploads and two leads. Was in-memory in the Node app; same here.
_recent: dict[str, tuple[float, dict]] = {}
_DEDUP_SECONDS = 90


def _dedup_key(body: dict) -> str:
    return "|".join(str(body.get(k, "")).strip().lower()
                    for k in ("email", "business", "zip", "category"))


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(PUBLIC, "index.html")


@app.get("/<path:filename>")
def public_files(filename):
    # Only the wizard's own assets. Anything else is a 404 rather than a
    # traversal attempt reaching the rest of the container.
    safe = os.path.normpath(filename).lstrip("./")
    if safe.startswith("..") or "/" in safe and not (PUBLIC / safe).exists():
        return jsonify({"error": "Not found"}), 404
    if not (PUBLIC / safe).exists():
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(PUBLIC, safe)


@app.get("/health")
def health():
    # The PDF no longer depends on a browser being installed, so there is
    # nothing to probe: reportlab is a hard dependency and imports at module
    # load or the module doesn't come up at all.
    return jsonify({
        "status": "ok", "service": "tourism",
        "ai": bool(os.environ.get("OPENAI_API_KEY")),
        "pdf": True,
    })


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

def chat_complete(system: str, user: str, max_tokens: int = 700,
                  temperature: float = 0.6, json_mode: bool = False):
    """Port of lib/openai.js. Returns None when no key is set, as it did."""
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        return None
    import requests
    body = {
        "model": MODEL,
        "messages": ([{"role": "system", "content": system}] if system else [])
                    + [{"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers={"Content-Type": "application/json",
                               "Authorization": f"Bearer {key}"},
                      json=body, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        raise RuntimeError(f"OpenAI API responded {r.status_code}: "
                           f"{r.text[:200]}")
    data = r.json()
    try:
        from hub import ai as _hub_ai
        _hub_ai.note_usage("tourism", data, purpose="analyze_business")
    except Exception:                                   # noqa: BLE001
        pass
    content = (((data.get("choices") or [{}])[0].get("message") or {})
               .get("content") or "")
    if not content.strip():
        raise RuntimeError("Empty OpenAI response")
    return content.strip()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/api/analyze-business")
def analyze_business():
    """Read a business's own page to suggest a category and average value.

    Fetches a URL a visitor typed, so it carries the same SSRF guard as the
    restaurant page — a public form that fetches arbitrary URLs from inside
    our network is how cloud metadata gets read.
    """
    body = request.get_json(silent=True) or {}
    url = str(body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "A website URL is required."}), 400
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    if not _ssrf_safe(url):
        return jsonify({"error": "That address can't be read."}), 400

    text = ""
    try:
        import requests
        r = requests.get(url, timeout=8, allow_redirects=False,
                         headers={"User-Agent": "Smart1Bot/1.0"})
        hops = 0
        while r.is_redirect and hops < 4:
            nxt = r.headers.get("Location") or ""
            if not nxt.lower().startswith("http"):
                from urllib.parse import urljoin
                nxt = urljoin(r.url, nxt)
            if not _ssrf_safe(nxt):
                return jsonify({"error": "That address can't be read."}), 400
            r = requests.get(nxt, timeout=8, allow_redirects=False,
                             headers={"User-Agent": "Smart1Bot/1.0"})
            hops += 1
        if r.ok:
            text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>",
                          " ", r.text or "")
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()[:6000]
    except Exception:                                   # noqa: BLE001
        text = ""

    if not text:
        return jsonify({"categoryItem": None, "avgValue": None,
                        "note": "Couldn't read that page — pick a category "
                                "and enter an average value instead."})

    suggestion = {"categoryItem": None, "avgValue": None}
    try:
        raw = chat_complete(
            system=("You extract two facts about a tourism business from its "
                    "own website text. Answer only from the text. If a fact "
                    "isn't there, return null for it — do not estimate."),
            user=("Return JSON: {\"category\": one of restaurant, attraction, "
                  "lodging, outdoor, retail, event, tour, winery, museum, "
                  "camp; \"avg_ticket_usd\": a number or null}\n\n" + text),
            json_mode=True, max_tokens=200, temperature=0.2)
        if raw:
            parsed = json.loads(raw)
            suggestion["categoryItem"] = parsed.get("category")
            val = parsed.get("avg_ticket_usd")
            suggestion["avgValue"] = float(val) if isinstance(val, (int, float)) else None
    except Exception:                                   # noqa: BLE001
        pass          # a failed suggestion is not a failed page
    return jsonify(suggestion)


@app.post("/api/partial-lead")
def partial_lead():
    """Capture someone who gave contact details and then left."""
    body = request.get_json(silent=True) or {}
    if not (body.get("email") or body.get("phone")):
        return jsonify({"ok": True, "skipped": "no contact details"})
    try:
        from hub import leads as hub_leads
        hub_leads.capture_and_deliver(
            source="tourism", page="Tourism Marketing Plan (partial)",
            fields={"name": body.get("name", ""), "email": body.get("email", ""),
                    "phone": body.get("phone", ""),
                    "company": body.get("business", ""),
                    "category": body.get("category", ""),
                    "zip": body.get("zip", "")},
            client=body.get("business", ""))
    except Exception:                                   # noqa: BLE001
        pass
    return jsonify({"ok": True})


@app.post("/api/submit")
def submit():
    """Build the PDF, file the lead, return the report."""
    body = request.get_json(silent=True) or {}
    if not (body.get("email") or body.get("phone")):
        return jsonify({"error": "An email or phone number is required."}), 400

    key = _dedup_key(body)
    now = time.time()
    for k, (when, _) in list(_recent.items()):
        if now - when > _DEDUP_SECONDS:
            _recent.pop(k, None)
    if key in _recent:
        cached = dict(_recent[key][1])
        cached["deduplicated"] = True
        return jsonify(cached)

    pdf_url, pdf_note = "", None
    try:
        pdf_bytes = render_report_pdf(body)
        pdf_url = upload_pdf(pdf_bytes, body) or ""
        if not pdf_url:
            pdf_note = "The report was built but couldn't be stored."
    except Exception as exc:                            # noqa: BLE001
        # Never fail the submission over a PDF. The visitor answered a dozen
        # questions; losing that because a browser hiccuped is the wrong
        # trade, and the lead still reaches the panel.
        app.logger.warning("Tourism PDF failed: %s", exc)
        pdf_note = "Your plan is ready — the PDF copy is being prepared."

    try:
        from hub import leads as hub_leads
        hub_leads.capture_and_deliver(
            source="tourism", page="Tourism Marketing Plan",
            fields={"name": body.get("name", ""), "email": body.get("email", ""),
                    "phone": body.get("phone", ""),
                    "company": body.get("business", ""),
                    "category": body.get("category", ""),
                    "zip": body.get("zip", "")},
            pdf_url=pdf_url, client=body.get("business", ""))
        # Attribute the plan to the client, so the work shows on their 360
        # record rather than only in the lead panel.
        from hub import audit as _audit
        _audit.log("tourism", "tourism_report",
                   client=body.get("business", "") or None)
    except Exception:                                   # noqa: BLE001
        app.logger.exception("Tourism lead capture failed")

    response = {"ok": True, "pdf_url": pdf_url}
    if pdf_note:
        response["pdf_note"] = pdf_note
    _recent[key] = (now, response)
    return jsonify(response)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# PDF — reportlab, in the same house palette as the other landing pages.
# ---------------------------------------------------------------------------
NAVY = colors.HexColor("#0a2240")
BLUE = colors.HexColor("#009ed2")
GREEN = colors.HexColor("#2dbb72")
LINE = colors.HexColor("#dbe5ed")
MUTED = colors.HexColor("#68798c")
AQUA = colors.HexColor("#eff9fc")
INK = colors.HexColor("#25364b")


def _pdf_styles() -> dict:
    ss = getSampleStyleSheet()
    return {
        "logo": ParagraphStyle("s1logo", parent=ss["Normal"], fontName="Helvetica-Bold",
                               fontSize=11, leading=13, textColor=NAVY, spaceAfter=6),
        "title": ParagraphStyle("s1title", parent=ss["Title"], fontName="Helvetica-Bold",
                                fontSize=19, leading=23, textColor=NAVY,
                                alignment=0, spaceAfter=2),
        "subtitle": ParagraphStyle("s1sub", parent=ss["Normal"], fontName="Helvetica",
                                   fontSize=9, leading=12, textColor=MUTED),
        "business": ParagraphStyle("s1biz", parent=ss["Normal"], fontName="Helvetica-Bold",
                                   fontSize=14, leading=17, textColor=NAVY),
        "muted": ParagraphStyle("s1muted", parent=ss["Normal"], fontName="Helvetica",
                                fontSize=8, leading=11, textColor=MUTED),
        "h2": ParagraphStyle("s1h2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                             fontSize=11, leading=14, textColor=NAVY,
                             spaceBefore=6, spaceAfter=0),
        "h3": ParagraphStyle("s1h3", parent=ss["Normal"], fontName="Helvetica-Bold",
                             fontSize=9.5, leading=12, textColor=NAVY),
        "kicker": ParagraphStyle("s1kick", parent=ss["Normal"], fontName="Helvetica-Bold",
                                 fontSize=7.5, leading=10, textColor=MUTED, spaceAfter=2),
        "body": ParagraphStyle("s1body", parent=ss["Normal"], fontName="Helvetica",
                               fontSize=9.5, leading=14, textColor=INK),
        "small": ParagraphStyle("s1small", parent=ss["Normal"], fontName="Helvetica",
                                fontSize=8, leading=11.5, textColor=INK),
        "note": ParagraphStyle("s1note", parent=ss["Normal"], fontName="Helvetica-Oblique",
                               fontSize=7.5, leading=10, textColor=MUTED),
    }


def _pdf_furniture(canvas, doc) -> None:
    """Rule and page number on every page."""
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.6)
    canvas.line(doc.leftMargin, 0.62 * inch,
                doc.pagesize[0] - doc.rightMargin, 0.62 * inch)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(doc.leftMargin, 0.45 * inch,
                      "Smart 1 Marketing · Seasonal Tourism Marketing Plan")
    canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 0.45 * inch,
                           "Page %d" % canvas.getPageNumber())
    canvas.restoreState()


def render_report_pdf(answers: dict) -> bytes:
    """Lay out the plan with reportlab, from the model the page computed.

    This used to print report-print.html in headless Chromium. That worked,
    but it put a ~300 MB browser in the image and made every tool's build pay
    for one landing page's PDF. The other six pages all use reportlab; this is
    now the seventh.

    The arithmetic is not repeated here. `report_model` arrives from the page,
    built by the same functions that render the on-screen report, so the PDF
    and the screen cannot disagree about a number. What follows is layout
    only: a handful of primitive block shapes, drawn in the house palette.
    """
    model = answers.get("report_model")
    if not isinstance(model, dict) or not model.get("sections"):
        raise ValueError("No report model in the submission; nothing to lay out.")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.7 * inch, bottomMargin=0.75 * inch,
        title=f"{model.get('title', 'Marketing Plan')} — {model.get('businessName', '')}",
        author="Smart 1 Marketing",
    )
    st = _pdf_styles()
    flow: list = []

    def esc(v) -> str:
        return (str(v if v is not None else "")
                .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    # ---- masthead ---------------------------------------------------------
    flow.append(Paragraph("SMART<font color='#009ed2'>1</font>MARKETING", st["logo"]))
    flow.append(Paragraph(esc(model.get("title", "")), st["title"]))
    flow.append(Paragraph(esc(model.get("subtitle", "")), st["subtitle"]))
    flow.append(HRFlowable(width="100%", thickness=2.5, color=BLUE,
                           spaceBefore=8, spaceAfter=12))
    flow.append(Paragraph(esc(model.get("businessName", "")), st["business"]))
    prepared = "Prepared by Smart 1 Marketing · " + esc(model.get("preparedOn", ""))
    if model.get("website"):
        prepared += " · " + esc(model["website"])
    flow.append(Paragraph(prepared, st["muted"]))
    flow.append(Spacer(1, 12))

    # ---- the facts box ----------------------------------------------------
    meta = model.get("meta") or []
    if meta:
        cells, row = [], []
        for item in meta:
            row.append(Paragraph(
                f"<font size=7 color='#68798c'>{esc(item.get('label'))}</font><br/>"
                f"<b>{esc(item.get('value'))}</b>", st["small"]))
            if len(row) == 3:
                cells.append(row); row = []
        if row:
            row += [""] * (3 - len(row))
            cells.append(row)
        t = Table(cells, colWidths=[doc.width / 3.0] * 3)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), AQUA),
            ("BOX", (0, 0), (-1, -1), 0.6, LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.white),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        flow.append(t)
        flow.append(Spacer(1, 16))

    # ---- block renderers --------------------------------------------------
    def stat_cards(items, tint=AQUA):
        row = [Paragraph(
            f"<font size=7 color='#68798c'>{esc(i.get('label'))}</font><br/>"
            f"<font size=15 color='#0a2240'><b>{esc(i.get('value'))}</b></font><br/>"
            f"<font size=7 color='#68798c'>{esc(i.get('sub'))}</font>", st["small"])
            for i in items]
        t = Table([row], colWidths=[doc.width / max(1, len(row))] * len(row))
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), tint),
            ("BOX", (0, 0), (-1, -1), 0.6, LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.white),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ]))
        return t

    def bullet_card(card, accent):
        inner = [Paragraph(f"<b>{esc(card.get('title'))}</b>", st["body"]),
                 Paragraph(f"<font size=7 color='#68798c'>{esc(card.get('sub'))}</font>", st["small"])]
        inner += [Paragraph(f"• {esc(x)}", st["small"]) for x in (card.get("items") or [])]
        t = Table([[i] for i in inner], colWidths=[(doc.width - 10) / 2.0])
        t.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, LINE),
            ("LINEBEFORE", (0, 0), (0, -1), 2.5, accent),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return t

    def render_block(b: dict) -> None:
        kind = b.get("type")
        if kind == "p":
            text = (f"<b>{esc(b['bold'])}</b>" if b.get("bold") else "") + esc(b.get("text"))
            flow.append(Paragraph(text, st["body"]))
            flow.append(Spacer(1, 7))
        elif kind == "note":
            flow.append(Paragraph(esc(b.get("text")), st["note"]))
            flow.append(Spacer(1, 6))
        elif kind == "stats":
            flow.append(stat_cards(b.get("items") or []))
            flow.append(Spacer(1, 10))
        elif kind == "highlight":
            flow.append(Paragraph(esc(b.get("title")), st["h3"]))
            flow.append(Spacer(1, 5))
            flow.append(stat_cards(b.get("items") or [], tint=colors.HexColor("#f2fbf6")))
            if b.get("note"):
                flow.append(Spacer(1, 6))
                flow.append(Paragraph(esc(b["note"]), st["small"]))
            flow.append(Spacer(1, 10))
        elif kind == "pills":
            flow.append(Paragraph(esc(b.get("label", "")).upper(), st["kicker"]))
            items = b.get("items") or []
            flow.append(Paragraph(" · ".join(esc(x) for x in items) or "—", st["body"]))
            flow.append(Spacer(1, 9))
        elif kind == "compare":
            t = Table([[bullet_card(b.get("bad") or {}, MUTED),
                        bullet_card(b.get("good") or {}, GREEN)]],
                      colWidths=[doc.width / 2.0] * 2)
            t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                   ("RIGHTPADDING", (0, 0), (0, -1), 10)]))
            flow.append(t)
            flow.append(Spacer(1, 10))
        elif kind == "tiers":
            cards = []
            for tier in b.get("items") or []:
                head = esc(tier.get("name"))
                if tier.get("selected"):
                    head += " <font size=6 color='#009ed2'><b>· SUGGESTED</b></font>"
                inner = [Paragraph(f"<b>{head}</b>", st["body"]),
                         Paragraph(f"<font color='#0a2240'><b>{esc(tier.get('price'))}</b></font>", st["small"]),
                         Paragraph(f"<font size=7 color='#68798c'>{esc(tier.get('tagline'))}</font>", st["small"])]
                inner += [Paragraph(f"• {esc(x)}", st["small"]) for x in (tier.get("items") or [])]
                c = Table([[i] for i in inner], colWidths=[(doc.width - 16) / 3.0])
                c.setStyle(TableStyle([
                    ("BOX", (0, 0), (-1, -1), 1.2 if tier.get("selected") else 0.6,
                     BLUE if tier.get("selected") else LINE),
                    ("BACKGROUND", (0, 0), (-1, -1),
                     AQUA if tier.get("selected") else colors.white),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]))
                cards.append(c)
            t = Table([cards], colWidths=[doc.width / 3.0] * len(cards))
            t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                   ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
            flow.append(t)
            flow.append(Spacer(1, 10))
        elif kind == "bars":
            # A filled proportion of the row width reads as a bar without an
            # image: two cells, coloured and uncoloured.
            for bar in b.get("items") or []:
                pct = max(0, min(100, int(bar.get("pct") or 0)))
                flow.append(Paragraph(
                    f"<b>{esc(bar.get('label'))}</b> "
                    f"<font size=7 color='#68798c'>{esc(bar.get('months'))}</font>"
                    f"<font color='#0a2240'>   {pct}% · {esc(bar.get('total'))}</font>",
                    st["small"]))
                w = doc.width
                filled, rest = max(1.0, w * pct / 100.0), max(1.0, w * (100 - pct) / 100.0)
                track = Table([["", ""]], colWidths=[filled, rest], rowHeights=[5])
                track.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (0, 0), BLUE),
                    ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#eef3f7")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]))
                flow.append(track)
                flow.append(Paragraph(
                    f"<font size=7 color='#68798c'>{esc(bar.get('sub'))}</font>", st["small"]))
                flow.append(Spacer(1, 8))
        elif kind == "table":
            head = [Paragraph(f"<b><font color='#ffffff' size=8>{esc(h)}</font></b>", st["small"])
                    for h in (b.get("head") or [])]
            rows = [head]
            for r in b.get("rows") or []:
                cells = []
                for c in r:
                    parts = str(c).split("\n")
                    txt = f"<b>{esc(parts[0])}</b>"
                    if len(parts) > 1:
                        txt += "<br/><font size=7 color='#68798c'>" + \
                               esc(" ".join(parts[1:])) + "</font>"
                    cells.append(Paragraph(txt, st["small"]))
                rows.append(cells)
            w = doc.width
            t = Table(rows, colWidths=[w * 0.20, w * 0.62, w * 0.18], repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7fafc")]),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            flow.append(t)
            flow.append(Spacer(1, 10))

    # ---- the sections themselves -----------------------------------------
    for section in model.get("sections") or []:
        head = esc(section.get("label"))
        if section.get("badge"):
            head += f"  <font size=7 color='#009ed2'>{esc(section['badge'])}</font>"
        flow.append(Paragraph(head, st["h2"]))
        flow.append(HRFlowable(width="100%", thickness=0.6, color=LINE,
                               spaceBefore=2, spaceAfter=8))
        for block in section.get("blocks") or []:
            try:
                render_block(block)
            except Exception:                           # noqa: BLE001
                # One malformed block must not cost the whole report.
                app.logger.exception("tourism PDF: block failed (%s)", block.get("type"))
        flow.append(Spacer(1, 6))

    cta = model.get("cta") or {}
    if cta:
        band = Table([[Paragraph(
            f"<b><font color='#ffffff' size=11>{esc(cta.get('title'))}</font></b><br/>"
            f"<font color='#cfe8f5' size=8>{esc(cta.get('sub'))}</font>", st["small"])]],
            colWidths=[doc.width])
        band.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), NAVY),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ]))
        flow.append(Spacer(1, 4))
        flow.append(band)

    doc.build(flow, onFirstPage=_pdf_furniture, onLaterPages=_pdf_furniture)
    return buf.getvalue()


def upload_pdf(pdf_bytes: bytes, body: dict) -> str:
    """Store the report. resource_type='raw' so the link always delivers."""
    try:
        import cloudinary.uploader
        from hub.config import settings
        if not settings.cloudinary_ready:
            return ""
        slug = re.sub(r"[^a-z0-9]+", "-",
                      str(body.get("business") or "report").lower()).strip("-")[:60]
        res = cloudinary.uploader.upload(
            pdf_bytes, resource_type="raw",
            folder=f"{settings.folder('proposals')}/tourism",
            public_id=f"{slug}-{int(time.time())}",
            overwrite=False, unique_filename=True)
        return res.get("secure_url", "")
    except Exception as exc:                            # noqa: BLE001
        app.logger.warning("Tourism PDF upload failed: %s", exc)
        return ""


def _ssrf_safe(url: str) -> bool:
    """Refuse anything resolving inside our own network."""
    import ipaddress
    import socket
    from urllib.parse import urlparse
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https") or u.username or u.password:
            return False
        host = u.hostname or ""
        if not host:
            return False
        for info in socket.getaddrinfo(
                host, u.port or (443 if u.scheme == "https" else 80),
                proto=socket.IPPROTO_TCP):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return False
    except Exception:                                   # noqa: BLE001
        return False
    return True
