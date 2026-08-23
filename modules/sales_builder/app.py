"""
Smart 1 Sales Builder — the Proposal Builder
============================================
Dashboard, proposal wizard and IO hand-off, in one module.

**This is now the only proposal builder.** There were two: this one, and a
standalone block-based builder at /sales/proposals. They shared no code, no
storage and no idea of what a campaign is, so the same client could be quoted
differently depending on which tool a rep happened to open, and only this one
produced anything an insertion order could read. /sales/proposals now redirects
here; its saved proposals stay readable and can be imported as quotes (see
`/api/legacy/proposals`).

What came over from the standalone builder:

- the industry library (channels, demand triggers, intro copy) — now
  `hub.industries`, used for the default proposal sections and the AI draft,
- AI narrative section copy, rather than only an AI *setup* draft,
- the branded PDF uploaded to Cloudinary so there is a link to send, not just
  a blob in the database,
- filing the finished proposal onto the client's record, so it appears on
  Client 360 next to everything else we've sent them,
- pushing an opportunity into Smart 1 Suite when a proposal goes to a client.

Also here:

- Stores every quote (full builder state + generated PDF) in a database
  (SQLite by default; set DATABASE_URL for Render Postgres).
- Assigns quote numbers in the Q-10200 series (own counter, IO-style).
- Word (.docx) export; the latest PDF is archived with the quote.
- "What's still missing before this can become an IO" gap lists.
- Multiple target areas per campaign, via `hub.target_areas` — the same shape
  the IO builder reads, so a three-location client survives the hand-off.

The AI helper routes (audience estimate, business description, landing-page
review, ZIP-radius lookup) are served **here**. They used to be fetched from
`IO_API_BASE + "/sales/builder/api/..."`, a path that exists on neither this
app nor the IO app, so every one of those buttons had been silently falling
back to its placeholder. The IO conversion still calls the IO API for the
order number and the two IO PDFs, because those belong to the IO.
"""

import json
import os
import re
import threading
import logging
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file
from flask_cors import CORS
from sqlalchemy import (Column, DateTime, Integer, LargeBinary, String, Text,
                        func, or_)
from sqlalchemy.orm import declarative_base

# ---- PDF (reportlab, same stack as the IO app) ----
from reportlab.lib import colors as rl_colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)
from xml.sax.saxutils import escape as xml_escape

# ---- Word export ----
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor
from hub.extensions import create_all_metadata, session_factory, shared_engine
from hub.webargs import clamp_int

# Shared services. Per the migration rule in CLAUDE.md: this module was being
# edited anyway, so it moves onto the shared implementations rather than
# keeping local copies of geography, prompt and Suite logic.
from hub import business_description as hub_desc
from hub import industries as hub_industries
from hub import target_areas as hub_areas

# Activity logging. Guarded so this module still runs standalone, but
# inside the Hub every action is attributable — this module created client quotes,
# and an unattributable change to a client's account is one nobody can
# explain later.
try:
    from hub import audit as _hub_audit
    _audit = _hub_audit.for_module("sales_builder")
except Exception:  # noqa: BLE001
    def _audit(*a, **k):  # no-op outside the Hub
        return None


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CORS(app, resources={r"/api/*": {"origins": "*"}}, methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])

# =====================================================================
# Database (SQLite locally, Postgres on Render via DATABASE_URL)
# =====================================================================
# One engine for the whole Hub, from hub/extensions -- see the note in
# modules/scans/app.py. Quotes are client work: a second pool with its own
# SQLite fallback inside the container image meant "back up the database" had a
# different answer here than everywhere else.
engine = shared_engine()
SessionLocal = session_factory()
Base = declarative_base()

QUOTE_BASE = 10199  # first quote is Q-10200 (matches the IO number family)
VALID_STATUSES = ["Draft", "Sent", "Approved", "Lost", "Expired", "Converted"]


class Counter(Base):
    __tablename__ = "counters"
    name = Column(String(50), primary_key=True)
    value = Column(Integer, nullable=False, default=QUOTE_BASE)


class Quote(Base):
    __tablename__ = "quotes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    quote_number = Column(String(20), unique=True, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="Draft", index=True)
    client = Column(String(200), default="")
    website = Column(String(300), default="")
    industry = Column(String(100), default="")
    salesperson = Column(String(120), default="")
    data = Column(Text, default="{}")           # full builder state (JSON)
    monthly_budget = Column(Integer, default=0)
    months = Column(Integer, default=1)
    total_budget = Column(Integer, default=0)
    package = Column(String(40), default="")
    products_summary = Column(String(500), default="")
    goals_summary = Column(String(300), default="")
    geo_summary = Column(String(300), default="")
    revision = Column(Integer, default=1)
    io_number = Column(String(20), default="")
    io_client_pdf_url = Column(String(600), default="")
    io_internal_pdf_url = Column(String(600), default="")
    converted_at = Column(DateTime, nullable=True)
    pdf_blob = Column(LargeBinary, nullable=True)
    pdf_filename = Column(String(300), default="")
    pdf_generated_at = Column(DateTime, nullable=True)
    # Delivery: where the client-facing copy lives, and what it created in
    # Smart 1 Suite. The blob above is the archive; a blob cannot be emailed,
    # which is why the standalone builder uploaded to Cloudinary and this one
    # now does too.
    pdf_url = Column(String(600), default="")
    client_filed_as = Column(String(64), default="")     # hub.proposals record id
    suite_contact_id = Column(String(64), default="")
    suite_opportunity_id = Column(String(64), default="")
    delivered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class Activity(Base):
    __tablename__ = "activity"
    id = Column(Integer, primary_key=True, autoincrement=True)
    quote_id = Column(Integer, index=True, nullable=True)
    icon = Column(String(10), default="•")
    text = Column(String(500), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# Advisory-locked, and reported rather than raised: a database slow to wake
# must not take the module offline for the life of the worker.
DB_BOOT_ERROR = create_all_metadata(Base.metadata)
if DB_BOOT_ERROR:
    logger.error("sales_builder: table creation reported: %s", DB_BOOT_ERROR)


def _add_missing_columns() -> None:
    """Add columns introduced after the quotes table was first created.

    create_all() creates missing tables, never missing columns, so the live
    database — which has quotes in it already — would keep working right up
    until the first query mentioning a delivery column. Each statement is
    attempted and its failure ignored: "already exists" is the normal case on
    every boot after the first.
    """
    from sqlalchemy import text as _text
    for sql in (
        "ALTER TABLE quotes ADD COLUMN pdf_url VARCHAR(600)",
        "ALTER TABLE quotes ADD COLUMN client_filed_as VARCHAR(64)",
        "ALTER TABLE quotes ADD COLUMN suite_contact_id VARCHAR(64)",
        "ALTER TABLE quotes ADD COLUMN suite_opportunity_id VARCHAR(64)",
        "ALTER TABLE quotes ADD COLUMN delivered_at TIMESTAMP",
    ):
        try:
            with engine.begin() as conn:
                conn.execute(_text(sql))
        except Exception:                               # noqa: BLE001
            pass


if not DB_BOOT_ERROR:
    _add_missing_columns()

_COUNTER_LOCK = threading.Lock()


def next_quote_number(db):
    """Allocate the next quote number: Q-10200, Q-10201, ... (own counter)."""
    with _COUNTER_LOCK:
        row = db.get(Counter, "quote_number")
        if row is None:
            row = Counter(name="quote_number", value=QUOTE_BASE)
            db.add(row)
        row.value = int(row.value) + 1
        db.flush()
        return f"Q-{row.value}"


def log_activity(db, quote_id, icon, text):
    db.add(Activity(quote_id=quote_id, icon=icon, text=text[:490]))


# =====================================================================
# Gap check — what a finished IO still needs that a proposal may not have
# (mirrors the required pieces of the IO flow; rule-based so it works
#  with or without AI)
# =====================================================================
def compute_gaps(state):
    gaps = []
    s = state or {}

    def blank(v):
        return v in (None, "", [], {})

    if not hub_areas.normalize(s.get("targetAreas")) and not hub_areas.from_legacy(s):
        gaps.append({"key": "geo", "label": "At least one target area to run in"})
    if blank(s.get("clientContactName")) and blank(s.get("clientContactEmail")):
        gaps.append({"key": "contact", "label": "Client contact name, email, and phone"})
    if blank(s.get("startDate")):
        gaps.append({"key": "dates", "label": "Exact campaign start date (and end date or ongoing)"})
    if blank(s.get("creativeSource")):
        gaps.append({"key": "creative", "label": "Creative assets — client provides, or Smart 1 builds (creative fee)"})
    tp = s.get("trackingPlan") or {}
    if blank(tp.get("primaryConversion")) or blank(tp.get("ga4")):
        gaps.append({"key": "tracking", "label": "Tracking plan (primary conversion, GA4, call tracking, verifier)"})
    if blank(s.get("landingUrl")):
        gaps.append({"key": "landing", "label": "Final landing page URL"})
    if blank(s.get("salesContact")) or blank(s.get("salesEmail")):
        gaps.append({"key": "sales", "label": "Smart 1 sales contact name and email"})
    if blank(s.get("managementFeeAck")):
        gaps.append({"key": "fees", "label": "Management fee confirmation (and creative fee if Smart 1 builds)"})
    return gaps


# Guardrails (subset of the IO builder's rules, checked server-side too)
def compute_guardrails(state):
    warns = []
    s = state or {}
    items = s.get("items") or []
    budget = float(s.get("budget") or 0)
    months = int(s.get("months") or 1)
    for it in items:
        cat = (it.get("category") or "").upper()
        dollars = float(it.get("dollars") or 0)
        if "SEARCH ENGINE MARKETING" in cat and 0 < dollars < 1500:
            warns.append(f"Search budget ${dollars:,.0f}/mo is below the $1,500 monthly minimum.")
    if budget > 0 and len(items) > 4 and budget / max(len(items), 1) < 750:
        warns.append("Budget is split across many products — consider fewer products for impact.")
    if months < 3:
        warns.append("Campaign term under 3 months — most Smart 1 programs need 3+ months to optimize.")
    if (s.get("creativeSource") or "").lower().startswith("smart 1") and not s.get("creativeFee"):
        warns.append("Smart 1 is building creative but no creative fee is included.")
    return warns


# =====================================================================
# Serialization
# =====================================================================
def quote_json(q, include_data=False):
    state = {}
    try:
        state = json.loads(q.data or "{}")
    except Exception:
        state = {}
    out = {
        "id": q.id,
        "quote_number": q.quote_number,
        "status": q.status,
        "client": q.client,
        "website": q.website,
        "industry": q.industry,
        "salesperson": q.salesperson,
        "monthly_budget": q.monthly_budget,
        "months": q.months,
        "total_budget": q.total_budget,
        "package": q.package,
        "products_summary": q.products_summary,
        "goals_summary": q.goals_summary,
        "geo_summary": q.geo_summary,
        "revision": q.revision,
        "io_number": q.io_number,
        "io_client_pdf_url": q.io_client_pdf_url,
        "io_internal_pdf_url": q.io_internal_pdf_url,
        "has_pdf": bool(q.pdf_blob),
        "pdf_filename": q.pdf_filename,
        "created_at": q.created_at.isoformat() if q.created_at else "",
        "updated_at": q.updated_at.isoformat() if q.updated_at else "",
        "converted_at": q.converted_at.isoformat() if q.converted_at else "",
        "gaps": compute_gaps(state),
        "guardrails": compute_guardrails(state),
        "target_areas": campaign_areas(state),
        "pdf_url": q.pdf_url or "",
        "suite_opportunity_id": q.suite_opportunity_id or "",
        "delivered_at": q.delivered_at.isoformat() if q.delivered_at else "",
    }
    if include_data:
        out["data"] = state
    return out


def campaign_areas(state):
    """Every target area on a quote, old records included.

    Quotes saved before multi-area targeting have their geography in
    `geoType` / `geo` / `radius` only, so `from_legacy` reads those rather
    than a migration rewriting rows nobody has re-opened. A quote that has
    both keeps the explicit list: it was edited more recently than it was
    imported.
    """
    state = state or {}
    return hub_areas.normalize(state.get("targetAreas")) or hub_areas.from_legacy(state)


def summarize_into(q, state):
    """Pull list/summary columns out of the saved state."""
    q.client = str(state.get("client") or "")[:200]
    q.website = str(state.get("url") or "")[:300]
    q.industry = str(state.get("industry") or "")[:100]
    q.salesperson = str(state.get("salesContact") or "")[:120]
    q.months = int(state.get("months") or 1)
    sel = state.get("selectedPackage") or {}
    q.monthly_budget = int(sel.get("monthly") or state.get("budget") or 0)
    q.total_budget = int(sel.get("total") or (q.monthly_budget * q.months))
    q.package = str(sel.get("name") or "")[:40]
    items = state.get("items") or []
    q.products_summary = " · ".join([str(i.get("product") or "") for i in items])[:500]
    q.goals_summary = ", ".join(state.get("objectives") or [])[:300]
    # The list column says how many places this campaign runs in, not just the
    # first one -- "Carmel showroom + 3 more" is the difference between a
    # single-location quote and a four-rooftop one at a glance.
    areas = campaign_areas(state)
    q.geo_summary = (hub_areas.summary(areas) or str(state.get("geo") or ""))[:300]


# =====================================================================
# Routes — core
# =====================================================================
def _io_api_base():
    """Where the IO builder's own routes live.

    It is mounted inside the Hub at /tools/io, so the default is that mount:
    same origin, same login, no cold start. IO_API_BASE still overrides it if
    the IO ever moves back to its own service.

    This used to default to the external Render URL, and the callers then
    appended "/sales/builder/api/..." to it -- a path that exists on neither
    app. Every conversion call 404'd.
    """
    return os.getenv("IO_API_BASE", "/tools/io").rstrip("/")


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        # engine.url, not a module-level _db_url -- that name was never
        # defined, so this route raised NameError and answered 500 to every
        # "is the Sales Builder up?" check ever made against it.
        "database": engine.url.get_backend_name(),
        "suite": _suite_status(),
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "io_api_base": _io_api_base(),
    })


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/config")
def api_config():
    return jsonify({
        "io_api_base": os.getenv("IO_API_BASE", "https://insertionordersmart.onrender.com"),
        # Now mounted inside the Hub rather than iframed from Render, so it
        # shares the login and can reach the client registry. The external URL
        # still works as an override if the standalone app is ever needed.
        "io_app_url": os.getenv("IO_APP_URL", "/tools/io/?embed=1"),
        "ai_enabled": bool(os.getenv("OPENAI_API_KEY")),
    })


# ---- Quotes CRUD ----
@app.post("/api/quotes")
def create_quote():
    body = request.get_json(force=True) or {}
    state = body.get("data") or {}
    db = SessionLocal()
    try:
        q = Quote(quote_number=next_quote_number(db),
                  status=body.get("status") or "Draft",
                  data=json.dumps(state, ensure_ascii=False))
        summarize_into(q, state)
        db.add(q)
        db.flush()
        log_activity(db, q.id, "🆕", f"{q.quote_number} created — {q.client or 'new quote'}")
        db.commit()
        return jsonify({"ok": True, "quote": quote_json(q, include_data=True)})
    finally:
        db.close()


@app.get("/api/quotes")
def list_quotes():
    qstr = (request.args.get("q") or "").strip().lower()
    status = (request.args.get("status") or "").strip()
    limit = clamp_int(request.args.get("limit"), 50, 1, 500)
    db = SessionLocal()
    try:
        query = db.query(Quote)
        if status and status != "all":
            query = query.filter(Quote.status == status)
        if qstr:
            like = f"%{qstr}%"
            query = query.filter(or_(
                func.lower(Quote.quote_number).like(like),
                func.lower(Quote.client).like(like),
                func.lower(Quote.website).like(like),
                func.lower(Quote.products_summary).like(like),
                func.lower(Quote.io_number).like(like),
            ))
        rows = query.order_by(Quote.updated_at.desc()).limit(limit).all()
        return jsonify({"ok": True, "quotes": [quote_json(r) for r in rows]})
    finally:
        db.close()


@app.get("/api/uploaded-proposals")
def list_uploaded_proposals():
    """Proposals written outside this tool and uploaded to a client record.

    Kept as a separate call rather than merged into /api/quotes because the
    two are genuinely different objects: a quote has a number, a revision and
    a status this tool owns, while an uploaded proposal is a file with a date.
    Flattening them into one row shape would mean inventing values for half
    the columns, and an invented "Draft" on a document nobody here drafted is
    exactly the sort of confident wrong answer to avoid. The front end merges
    them for display and keeps them labelled.
    """
    limit = clamp_int(request.args.get("limit"), 100, 1, 500)
    try:
        from hub import proposals as hub_proposals
    except Exception:                                   # noqa: BLE001
        # Standalone, outside the Hub. An empty list is the honest answer.
        return jsonify({"ok": True, "proposals": [], "available": False})
    try:
        rows = hub_proposals.all_proposals(limit=limit,
                                           q=(request.args.get("q") or "").strip())
    except Exception as exc:                            # noqa: BLE001
        logger.warning("uploaded proposals unavailable: %s", exc)
        return jsonify({"ok": True, "proposals": [], "available": False,
                        "note": "Uploaded proposals could not be read."})
    return jsonify({"ok": True, "proposals": rows, "available": True})


@app.get("/api/quotes/<int:qid>")
def get_quote(qid):
    db = SessionLocal()
    try:
        q = db.get(Quote, qid)
        if not q:
            return jsonify({"ok": False, "error": "Quote not found"}), 404
        return jsonify({"ok": True, "quote": quote_json(q, include_data=True)})
    finally:
        db.close()


@app.put("/api/quotes/<int:qid>")
def update_quote(qid):
    body = request.get_json(force=True) or {}
    db = SessionLocal()
    try:
        q = db.get(Quote, qid)
        if not q:
            return jsonify({"ok": False, "error": "Quote not found"}), 404
        if "data" in body:
            state = body.get("data") or {}
            q.data = json.dumps(state, ensure_ascii=False)
            summarize_into(q, state)
            q.revision = (q.revision or 1) + (1 if body.get("bump_revision") else 0)
        if "status" in body:
            new_status = body["status"]
            if new_status not in VALID_STATUSES:
                return jsonify({"ok": False, "error": "Invalid status"}), 400
            if new_status != q.status:
                log_activity(db, q.id, {"Sent": "📤", "Approved": "✅", "Lost": "❌",
                                        "Converted": "🔁", "Expired": "⏰"}.get(new_status, "•"),
                             f"{q.quote_number} → {new_status} — {q.client}")
            q.status = new_status
        db.commit()
        return jsonify({"ok": True, "quote": quote_json(q, include_data=True)})
    finally:
        db.close()


@app.post("/api/quotes/<int:qid>/duplicate")
def duplicate_quote(qid):
    db = SessionLocal()
    try:
        src = db.get(Quote, qid)
        if not src:
            return jsonify({"ok": False, "error": "Quote not found"}), 404
        state = json.loads(src.data or "{}")
        # New quotes start clean of decision/IO fields
        for k in ("startDate", "ioPayload",):
            state.pop(k, None)
        q = Quote(quote_number=next_quote_number(db), status="Draft",
                  data=json.dumps(state, ensure_ascii=False))
        summarize_into(q, state)
        db.add(q)
        db.flush()
        log_activity(db, q.id, "📋", f"{q.quote_number} duplicated from {src.quote_number} — {q.client}")
        db.commit()
        return jsonify({"ok": True, "quote": quote_json(q, include_data=True)})
    finally:
        db.close()


@app.post("/api/quotes/<int:qid>/converted")
def mark_converted(qid):
    """Called by the convert wizard after the IO API has issued the order
    number and generated the IO PDFs. Links everything back to the quote."""
    body = request.get_json(force=True) or {}
    db = SessionLocal()
    try:
        q = db.get(Quote, qid)
        if not q:
            return jsonify({"ok": False, "error": "Quote not found"}), 404
        q.io_number = str(body.get("io_number") or "")[:20]
        q.io_client_pdf_url = str(body.get("client_pdf_url") or "")[:600]
        q.io_internal_pdf_url = str(body.get("internal_pdf_url") or "")[:600]
        q.status = "Converted"
        q.converted_at = datetime.now(timezone.utc)
        if "data" in body:
            q.data = json.dumps(body["data"], ensure_ascii=False)
            summarize_into(q, body["data"])
        log_activity(db, q.id, "🔁",
                     f"{q.quote_number} → IO #{q.io_number} — {q.client}"
                     + (", sent to Smart 1 Suite" if body.get("submitted") else ""))
        db.commit()
        return jsonify({"ok": True, "quote": quote_json(q)})
    finally:
        db.close()


# ---- Dashboard ----
@app.get("/api/dashboard")
def dashboard():
    db = SessionLocal()
    try:
        rows = db.query(Quote).all()
        now = datetime.now(timezone.utc)

        def aware(dt):
            if dt is None:
                return now
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        open_rows = [r for r in rows if r.status in ("Draft", "Sent")]
        sent_rows = [r for r in rows if r.status == "Sent"]
        approved = [r for r in rows if r.status == "Approved"]
        conv_month = [r for r in rows if r.status == "Converted" and r.converted_at
                      and aware(r.converted_at).month == now.month and aware(r.converted_at).year == now.year]
        decided_90 = [r for r in rows if r.status in ("Approved", "Converted", "Lost")
                      and (now - aware(r.updated_at)).days <= 90]
        won_90 = [r for r in decided_90 if r.status in ("Approved", "Converted")]
        avg_days_out = round(sum((now - aware(r.updated_at)).days for r in sent_rows) / len(sent_rows)) if sent_rows else 0

        acts = db.query(Activity).order_by(Activity.created_at.desc()).limit(12).all()
        stale = [r for r in sent_rows if (now - aware(r.updated_at)).days >= 7]
        nudges = []
        for r in stale[:3]:
            nudges.append({"tag": "FOLLOW-UP NUDGE", "text": f"{r.client} ({r.quote_number}) was sent "
                          f"{(now - aware(r.updated_at)).days} days ago with no response. Consider a follow-up."})
        for r in approved[:3]:
            g = compute_gaps(json.loads(r.data or "{}"))
            nudges.append({"tag": "READY TO CONVERT", "text": f"{r.client} ({r.quote_number}) is approved. "
                          + (f"{len(g)} answers are still needed before the IO is complete." if g
                             else "Everything needed for the IO is already on file.")})
        for r in [x for x in rows if x.status == "Draft"][:5]:
            for w in compute_guardrails(json.loads(r.data or "{}"))[:1]:
                nudges.append({"tag": "PRICING GUARDRAIL", "text": f"{r.client or r.quote_number}: {w}"})

        return jsonify({"ok": True, "stats": {
            "open_count": len(open_rows),
            "open_monthly": sum(r.monthly_budget or 0 for r in open_rows),
            "awaiting_count": len(sent_rows),
            "awaiting_avg_days": avg_days_out,
            "approved_count": len(approved),
            "converted_month_count": len(conv_month),
            "converted_month_monthly": sum(r.monthly_budget or 0 for r in conv_month),
            "win_rate_90d": round(100 * len(won_90) / len(decided_90)) if decided_90 else 0,
        }, "activity": [{"icon": a.icon, "text": a.text,
                         "when": a.created_at.isoformat() if a.created_at else ""} for a in acts],
            "nudges": nudges[:6]})
    finally:
        db.close()


# =====================================================================
# Branded proposal PDF
# =====================================================================
NAVY = rl_colors.HexColor("#14284b")
BLUE = rl_colors.HexColor("#1f63ae")
GOLD = rl_colors.HexColor("#e5a323")
LINE = rl_colors.HexColor("#d5dee9")
SOFT = rl_colors.HexColor("#eef3f8")
MUTED = rl_colors.HexColor("#53657a")


def _p(text, style):
    return Paragraph(xml_escape(str(text or "")).replace("\n", "<br/>"), style)


def _money(n):
    try:
        return "$" + f"{float(n):,.0f}"
    except Exception:
        return str(n)


def build_proposal_pdf(q, state):
    title = f"S1M Proposal - {q.quote_number} - {q.client or 'Client'}"
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, rightMargin=0.55 * inch, leftMargin=0.55 * inch,
                            topMargin=0.55 * inch, bottomMargin=0.6 * inch, title=title)
    ss = getSampleStyleSheet()
    st_title = ParagraphStyle("T", parent=ss["Title"], textColor=NAVY, fontSize=21, leading=25, alignment=TA_CENTER, spaceAfter=2)
    st_sub = ParagraphStyle("Sub", parent=ss["BodyText"], textColor=MUTED, fontSize=10.5, alignment=TA_CENTER, spaceAfter=10)
    st_h2 = ParagraphStyle("H2", parent=ss["Heading2"], textColor=NAVY, fontSize=13, leading=16, spaceBefore=13, spaceAfter=5)
    st_body = ParagraphStyle("B", parent=ss["BodyText"], fontSize=9.5, leading=13, spaceAfter=5)
    st_small = ParagraphStyle("S", parent=ss["BodyText"], fontSize=8, leading=10.5, textColor=MUTED)

    story = []
    # Branded header band
    band = Table([[Paragraph('<font color="#ffffff"><b>SMART 1 MARKETING</b></font>', ParagraphStyle("bnd", fontSize=13, leading=16, alignment=TA_CENTER))]],
                 colWidths=[7.4 * inch], rowHeights=[0.42 * inch])
    band.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [band, Spacer(1, 14),
              Paragraph(xml_escape(f"Marketing Proposal — {q.client or 'Client'}"), st_title),
              Paragraph(xml_escape(f"Quote {q.quote_number}  ·  {datetime.now().strftime('%B %d, %Y')}"
                                   + (f"  ·  Prepared by {state.get('salesContact')}" if state.get("salesContact") else "")), st_sub)]

    # Meta table
    sel = state.get("selectedPackage") or {}
    areas = campaign_areas(state)
    meta = [["Client", q.client or ""],
            ["Website", q.website or ""],
            ["Campaign Goals", ", ".join(state.get("objectives") or [])],
            ["Target Area" if len(areas) < 2 else f"Target Areas ({len(areas)})",
             "\n".join(hub_areas.names(areas)) or q.geo_summary or ""],
            ["Term", f"{q.months} months"],
            ["Monthly Investment", _money(sel.get("monthly") or q.monthly_budget)],
            ["Total Investment", _money(sel.get("total") or q.total_budget)]]
    meta = [[row[0], _p(row[1], st_body)] for row in meta]
    t = Table(meta, colWidths=[1.55 * inch, 5.85 * inch])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), SOFT), ("TEXTCOLOR", (0, 0), (0, -1), NAVY),
                           ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                           ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [t]

    # Sections (editable in the builder; sensible defaults if absent)
    for sec in state.get("sections") or []:
        if not sec.get("enabled", True):
            continue
        story.append(Paragraph(xml_escape(sec.get("title") or ""), st_h2))
        if sec.get("body"):
            story.append(_p(sec.get("body"), st_body))
        kind = sec.get("kind")
        if kind == "areas":
            if areas:
                rows = [["Target area", "Coverage", "Est. population"]]
                for area in areas:
                    population = hub_areas.estimated_population(area)
                    zips = hub_areas.zip_list(area.get("zips"))
                    coverage = area.get("notes") or (f"{len(zips)} ZIP Codes" if zips else area["type"])
                    rows.append([_p(hub_areas.label(area), st_small),
                                 _p(coverage, st_small),
                                 # "Not measured", never a zero. An area we
                                 # could not size must not read as an area
                                 # with nobody in it.
                                 f"{population:,}" if population else "Not measured"])
                at = Table(rows, colWidths=[3.5 * inch, 2.2 * inch, 1.7 * inch], repeatRows=1)
                at.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY),
                                        ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
                                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                                        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                                        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
                                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
                story.append(at)
                if len(areas) > 1:
                    story.append(_p("Populations are shown per area and are not de-duplicated "
                                    "where areas overlap.", st_small))
        elif kind == "reach":
            est = state.get("estimates") or {}
            if est:
                rows = [["Est. Population", "Addressable Audience", "Households", "Devices"],
                        [f"{int(est.get('pop') or 0):,}", f"{int(est.get('aud') or 0):,}",
                         f"{int(est.get('hh') or 0):,}", f"{int(est.get('dev') or 0):,}"]]
                rt = Table(rows, colWidths=[1.85 * inch] * 4)
                rt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
                                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                                        ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.4, LINE),
                                        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
                story.append(rt)
            auds = state.get("audiences") or []
            if auds:
                story.append(_p("Audience layers: " + ", ".join(auds), st_small))
        elif kind == "mediaplan":
            items = state.get("items") or []
            if items:
                rows = [["Product", "Category", "Rate", "Monthly", f"Total ({q.months} mo)"]]
                for i in items:
                    rows.append([_p(i.get("product") or "", st_small), _p(i.get("category") or "", st_small),
                                 _p((i.get("rate") or "Managed") if not i.get("rateValue")
                                    else f"{i.get('rate')} {i.get('rateValue')}", st_small),
                                 _money(i.get("dollars") or 0), _money((i.get("dollars") or 0) * q.months)])
                mt = Table(rows, colWidths=[2.3 * inch, 1.7 * inch, 1.1 * inch, 1.15 * inch, 1.15 * inch], repeatRows=1)
                mt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
                                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8),
                                        ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                        ("ALIGN", (3, 1), (-1, -1), "RIGHT"), ("TOPPADDING", (0, 0), (-1, -1), 5),
                                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
                story.append(mt)
        elif kind == "packages":
            pkgs = state.get("packages") or []
            if pkgs:
                selname = (state.get("selectedPackage") or {}).get("name")
                head = ["", *[p.get("name", "") + (" ★" if p.get("name") == selname else "") for p in pkgs]]
                rows = [head,
                        ["Monthly", *[_money(p.get("monthly")) for p in pkgs]],
                        [f"Total ({q.months} mo)", *[_money(p.get("total")) for p in pkgs]],
                        ["Est. impressions / mo tier", *[f"{int(p.get('impr') or 0):,}" for p in pkgs]]]
                pt = Table(rows, colWidths=[1.9 * inch] + [1.83 * inch] * len(pkgs))
                style = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
                         ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                         ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                         ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"), ("TEXTCOLOR", (0, 1), (0, -1), NAVY),
                         ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
                for ci, p in enumerate(pkgs):
                    if p.get("name") == selname:
                        style.append(("BACKGROUND", (ci + 1, 1), (ci + 1, -1), rl_colors.HexColor("#fff5df")))
                pt.setStyle(TableStyle(style))
                story.append(pt)
                story.append(_p("★ recommended package", st_small))
        elif kind == "kpis":
            kpis = state.get("kpis") or []
            if kpis:
                story.append(_p("KPIs: " + ", ".join(kpis), st_body))

    # Footer
    story += [Spacer(1, 16),
              _p("Smart 1 Marketing  ·  smart1marketing.com  ·  This proposal is valid for 30 days. "
                 "Rates follow the current Smart 1 rate card; final schedules are confirmed on the insertion order.", st_small)]
    doc.build(story)
    return buf.getvalue(), title


@app.get("/api/quotes/<int:qid>/pdf")
def quote_pdf(qid):
    db = SessionLocal()
    try:
        q = db.get(Quote, qid)
        if not q:
            return jsonify({"ok": False, "error": "Quote not found"}), 404
        state = json.loads(q.data or "{}")
        ensure_sections(state)
        pdf_bytes, title = build_proposal_pdf(q, state)
        q.pdf_blob = pdf_bytes
        q.pdf_filename = title + ".pdf"
        q.pdf_generated_at = datetime.now(timezone.utc)
        log_activity(db, q.id, "📄", f"PDF generated — {title}.pdf")
        db.commit()
        return send_file(BytesIO(pdf_bytes), mimetype="application/pdf",
                         as_attachment=request.args.get("download") == "1",
                         download_name=title + ".pdf")
    finally:
        db.close()


@app.get("/api/quotes/<int:qid>/pdf/archived")
def quote_pdf_archived(qid):
    """The last saved copy from the database (what 'save the PDF to the DB' returns)."""
    db = SessionLocal()
    try:
        q = db.get(Quote, qid)
        if not q or not q.pdf_blob:
            return jsonify({"ok": False, "error": "No archived PDF for this quote yet"}), 404
        return send_file(BytesIO(q.pdf_blob), mimetype="application/pdf",
                         as_attachment=False, download_name=q.pdf_filename or "proposal.pdf")
    finally:
        db.close()


# =====================================================================
# Word (.docx) export
# =====================================================================
def build_proposal_docx(q, state):
    d = Document()
    for section in d.sections:
        section.top_margin = Inches(0.7)
        section.bottom_margin = Inches(0.7)
        section.left_margin = Inches(0.8)
        section.right_margin = Inches(0.8)

    def hcolor(p, size, color, bold=True, center=False):
        run = p.runs[0] if p.runs else p.add_run("")
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color
        if center:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    NAVY_D = RGBColor(0x14, 0x28, 0x4B)
    MUTED_D = RGBColor(0x53, 0x65, 0x7A)

    p = d.add_paragraph("SMART 1 MARKETING")
    hcolor(p, 16, NAVY_D, center=True)
    p = d.add_paragraph(f"Marketing Proposal — {q.client or 'Client'}")
    hcolor(p, 20, NAVY_D, center=True)
    p = d.add_paragraph(f"Quote {q.quote_number}  ·  {datetime.now().strftime('%B %d, %Y')}")
    hcolor(p, 10, MUTED_D, bold=False, center=True)

    sel = state.get("selectedPackage") or {}
    areas = campaign_areas(state)
    table = d.add_table(rows=0, cols=2)
    table.style = "Light Grid Accent 1"
    for label, val in [("Client", q.client), ("Website", q.website),
                       ("Campaign Goals", ", ".join(state.get("objectives") or [])),
                       ("Target Area" if len(areas) < 2 else f"Target Areas ({len(areas)})",
                        "\n".join(hub_areas.names(areas)) or q.geo_summary),
                       ("Term", f"{q.months} months"),
                       ("Monthly Investment", _money(sel.get("monthly") or q.monthly_budget)),
                       ("Total Investment", _money(sel.get("total") or q.total_budget))]:
        row = table.add_row().cells
        row[0].text = label
        row[1].text = str(val or "")
        row[0].paragraphs[0].runs[0].font.bold = True

    for sec in state.get("sections") or []:
        if not sec.get("enabled", True):
            continue
        h = d.add_heading(sec.get("title") or "", level=2)
        for run in h.runs:
            run.font.color.rgb = NAVY_D
        if sec.get("body"):
            d.add_paragraph(str(sec.get("body")))
        kind = sec.get("kind")
        if kind == "areas" and areas:
            t0 = d.add_table(rows=1, cols=3)
            t0.style = "Light Grid Accent 1"
            hdr = t0.rows[0].cells
            for i, htxt in enumerate(["Target area", "Coverage", "Est. population"]):
                hdr[i].text = htxt
                hdr[i].paragraphs[0].runs[0].font.bold = True
            for area in areas:
                population = hub_areas.estimated_population(area)
                zips = hub_areas.zip_list(area.get("zips"))
                row = t0.add_row().cells
                row[0].text = hub_areas.label(area)
                row[1].text = area.get("notes") or (f"{len(zips)} ZIP Codes" if zips else area["type"])
                row[2].text = f"{population:,}" if population else "Not measured"
        elif kind == "mediaplan" and state.get("items"):
            t2 = d.add_table(rows=1, cols=4)
            t2.style = "Light Grid Accent 1"
            hdr = t2.rows[0].cells
            for i, htxt in enumerate(["Product", "Category", "Monthly", f"Total ({q.months} mo)"]):
                hdr[i].text = htxt
                hdr[i].paragraphs[0].runs[0].font.bold = True
            for it in state.get("items") or []:
                row = t2.add_row().cells
                row[0].text = str(it.get("product") or "")
                row[1].text = str(it.get("category") or "")
                row[2].text = _money(it.get("dollars") or 0)
                row[3].text = _money((it.get("dollars") or 0) * q.months)
        elif kind == "packages" and state.get("packages"):
            pkgs = state.get("packages") or []
            t3 = d.add_table(rows=1, cols=1 + len(pkgs))
            t3.style = "Light Grid Accent 1"
            hdr = t3.rows[0].cells
            hdr[0].text = ""
            for i, pkg in enumerate(pkgs):
                hdr[i + 1].text = pkg.get("name", "")
                hdr[i + 1].paragraphs[0].runs[0].font.bold = True
            for label, key in [("Monthly", "monthly"), ("Total", "total")]:
                row = t3.add_row().cells
                row[0].text = label
                for i, pkg in enumerate(pkgs):
                    row[i + 1].text = _money(pkg.get(key))
        elif kind == "kpis" and state.get("kpis"):
            d.add_paragraph("KPIs: " + ", ".join(state.get("kpis") or []))
        elif kind == "reach" and state.get("estimates"):
            est = state.get("estimates") or {}
            d.add_paragraph(f"Estimated population {int(est.get('pop') or 0):,} · addressable audience "
                            f"{int(est.get('aud') or 0):,} · households {int(est.get('hh') or 0):,} · "
                            f"devices {int(est.get('dev') or 0):,}")

    p = d.add_paragraph("Smart 1 Marketing · smart1marketing.com · This proposal is valid for 30 days.")
    hcolor(p, 8, MUTED_D, bold=False, center=True)
    buf = BytesIO()
    d.save(buf)
    return buf.getvalue()


@app.get("/api/quotes/<int:qid>/docx")
def quote_docx(qid):
    db = SessionLocal()
    try:
        q = db.get(Quote, qid)
        if not q:
            return jsonify({"ok": False, "error": "Quote not found"}), 404
        state = json.loads(q.data or "{}")
        ensure_sections(state)
        blob = build_proposal_docx(q, state)
        log_activity(db, q.id, "📝", f"Word copy exported — {q.quote_number}")
        db.commit()
        return send_file(BytesIO(blob),
                         mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         as_attachment=True,
                         download_name=f"S1M Proposal - {q.quote_number} - {q.client or 'Client'}.docx")
    finally:
        db.close()


# =====================================================================
# Default proposal sections (used when the builder hasn't customized them)
# =====================================================================
def industry_template(state):
    """The `hub.industries` entry that best fits this quote, or None.

    The library is keyed by our own industry slugs (boat, ski, legal...) while
    the wizard offers a broader list, so this matches on the label as well.
    Returning None is a normal answer — most industries have no template, and
    a generic proposal is better than one that claims a ski resort's demand
    triggers because the name nearly matched.
    """
    raw = str((state or {}).get("industry") or "").strip().lower()
    if not raw:
        return None
    if raw in hub_industries.INDUSTRIES:
        return hub_industries.INDUSTRIES[raw]
    for key, entry in hub_industries.INDUSTRIES.items():
        label = str(entry.get("label") or "").lower()
        if raw == label or (len(raw) > 3 and (raw in label or key in raw)):
            return entry
    return None


def ensure_sections(state):
    if state.get("sections"):
        return state
    client = state.get("client") or "the client"
    goals = ", ".join(state.get("objectives") or []) or "the campaign goals"
    areas = campaign_areas(state)
    where = hub_areas.summary(areas, limit=3) or "the target area"
    template = industry_template(state)

    # The strategy paragraph says how many places this runs in. A four-location
    # client whose proposal talks about "the target area" reads as though we
    # were quoting one of their rooftops.
    if len(areas) > 1:
        strategy = (f"This program is built around {goals.lower()}, combining the Smart 1 products "
                    f"below across {len(areas)} target areas — {where} — with the frequency needed "
                    f"to drive results in each.")
    else:
        strategy = (f"This program is built around {goals.lower()}, combining the Smart 1 products "
                    f"below to reach the right audience in {where} with the frequency needed to "
                    f"drive results.")
    if template:
        strategy = template["intro"] + "\n\n" + strategy

    state["sections"] = [
        {"id": "about", "title": f"About {client}", "kind": "text", "enabled": True,
         "body": state.get("description") or ""},
        {"id": "strategy", "title": "Campaign Strategy", "kind": "text", "enabled": True,
         "body": strategy},
        {"id": "areas", "title": "Target Areas", "kind": "areas", "enabled": True, "body": ""},
        {"id": "reach", "title": "Target Audience & Reach", "kind": "reach", "enabled": True, "body": ""},
        {"id": "mediaplan", "title": "Recommended Media Plan", "kind": "mediaplan", "enabled": True, "body": ""},
        {"id": "packages", "title": "Investment Options", "kind": "packages", "enabled": True, "body": ""},
        {"id": "kpis", "title": "How We Measure Success", "kind": "kpis", "enabled": True, "body":
            "Reporting is provided monthly with the KPIs below, reviewed together to optimize the plan."},
        {"id": "landing", "title": "Landing Page Recommendation", "kind": "text", "enabled": True,
         "body": state.get("landing") or ""},
        {"id": "next", "title": "Next Steps", "kind": "text", "enabled": True,
         "body": "1. Approve the recommended package.\n2. Smart 1 finalizes the insertion order and "
                 "creative plan.\n3. Campaign launches after tracking is verified."},
    ]
    if template and template.get("triggers"):
        # Demand triggers are the most persuasive thing in the industry
        # library and the standalone builder's proposals always led with them.
        state["sections"].insert(3, {
            "id": "triggers", "title": "Demand Triggers We Activate On",
            "kind": "text", "enabled": True,
            "body": "Budget is concentrated on the moments that produce revenue:\n"
                    + "\n".join("• " + str(t) for t in template["triggers"])})
    return state


# =====================================================================
# AI routes (optional — need OPENAI_API_KEY; same pattern as the IO app)
# =====================================================================
def _openai_response(prompt, max_output_tokens=6000):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OpenAI is not configured. Add OPENAI_API_KEY.")
    payload = {"model": os.getenv("OPENAI_MODEL", "gpt-5-mini"), "input": prompt,
               "tools": [{"type": "web_search"}], "max_output_tokens": max_output_tokens}
    r = requests.post("https://api.openai.com/v1/responses",
                      headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                      json=payload, timeout=120)
    r.raise_for_status()
    parts = []
    try:  # record spend so /diagnostics doesn't under-report
        from hub import ai as _hub_ai
        _hub_ai.note_usage("sales_builder", r.json(), purpose="quote")
    except Exception:  # noqa: BLE001
        pass
    for item in r.json().get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in ("output_text", "text") and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts).strip()


def _json_from_ai(text):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S)
    return json.loads(cleaned)


@app.post("/api/ai/draft-proposal")
def ai_draft_proposal():
    """One sentence -> a pre-filled proposal draft the salesperson reviews."""
    body = request.get_json(force=True) or {}
    ask = str(body.get("prompt") or "").strip()
    if not ask:
        return jsonify({"ok": False, "error": "A prompt is required"}), 400
    catalog = body.get("categories") or []
    prompt = (
        "You are a senior media planner at Smart 1 Marketing. From the request below, draft a campaign "
        "proposal setup. Return STRICT JSON only with keys: client (string), url (string, may be empty), "
        "industry (one of: Healthcare / Dental, Home Services, Legal, Automotive, Real Estate, Retail / "
        "E-comm, Restaurant / Hospitality, Financial Services, Fitness / Wellness, Education, B2B / "
        "Professional, Other), objectives (array from: Brand Awareness, Lead Generation, Website Traffic, "
        "Store Visits, Phone Calls, Conversions, Recruitment, Event Promotion), geoType (one of: City/ZIP "
        "+ Radius, DMA, Statewide, National, Other), geo (string), radius (number, only for radius type), "
        "budget (number, monthly dollars), months (number), categories (array of up to 4 product "
        "categories chosen ONLY from this list: " + ", ".join(catalog) + "), rationale (short string).\n\n"
        "Request: " + ask)
    try:
        result = _json_from_ai(_openai_response(prompt, 3000))
        return jsonify({"ok": True, "draft": result})
    except Exception as exc:
        logger.exception("AI draft failed")
        return jsonify({"ok": False, "error": "AI draft failed", "detail": str(exc)}), 502


@app.post("/api/ai/rewrite")
def ai_rewrite():
    """Rewrite a proposal section's copy per an instruction."""
    body = request.get_json(force=True) or {}
    text = str(body.get("text") or "").strip()
    instruction = str(body.get("instruction") or "make it clearer and more client-friendly").strip()
    if not text:
        return jsonify({"ok": False, "error": "Text is required"}), 400
    prompt = ("Rewrite the following marketing-proposal section for Smart 1 Marketing. Instruction: "
              + instruction + ". Keep it factual, professional, and client-facing. Return only the "
              "rewritten text, no preamble.\n\n" + text)
    try:
        return jsonify({"ok": True, "text": _openai_response(prompt, 2500)})
    except Exception as exc:
        return jsonify({"ok": False, "error": "AI rewrite failed", "detail": str(exc)}), 502


@app.post("/api/ai/draft-sections")
def ai_draft_sections():
    """Write the proposal's narrative copy from the campaign already built.

    The standalone builder's one real advantage was this: it produced a
    proposal a rep could send, not a form a rep then had to write into. It did
    it from an industry template and nothing else, though, so the prose never
    matched the media plan underneath it. This writes from both — the industry
    library for voice and demand triggers, the actual products, budget, target
    areas and KPIs for substance.

    Only `text` sections are written. The tables (media plan, packages, reach,
    target areas) are generated from the campaign data and must never be
    narrated by a model — that is how a proposal ends up describing a budget
    it does not contain.
    """
    body = request.get_json(force=True) or {}
    state = body.get("data") or {}
    ensure_sections(state)
    template = industry_template(state)
    areas = campaign_areas(state)
    writable = [sec for sec in state.get("sections") or []
                if sec.get("kind") == "text" and sec.get("enabled", True)]
    if not writable:
        return jsonify({"ok": False, "error": "This proposal has no text sections to write."}), 400

    facts = {
        "client": state.get("client") or "",
        "website": state.get("url") or "",
        "industry": state.get("industry") or "",
        "business_description": state.get("description") or "",
        "objectives": state.get("objectives") or [],
        "kpis": state.get("kpis") or [],
        "target_areas": hub_areas.for_prompt(areas),
        "target_area_count": len(areas),
        "audiences": state.get("audiences") or [],
        "exclusions": state.get("exclusions") or [],
        "months": state.get("months") or 1,
        "monthly_budget": (state.get("selectedPackage") or {}).get("monthly") or state.get("budget") or 0,
        "products": [{"product": i.get("product"), "category": i.get("category"),
                      "monthly": i.get("dollars")} for i in (state.get("items") or [])],
        "landing_page": state.get("landingUrl") or "",
        "landing_recommendation": state.get("landing") or "",
        "sections_to_write": [{"id": sec.get("id"), "title": sec.get("title"),
                               "current": sec.get("body") or ""} for sec in writable],
    }
    if template:
        facts["industry_voice"] = template["intro"]
        facts["demand_triggers"] = template["triggers"]
        facts["channels_we_sell_here"] = template["channels"]

    prompt = (
        "You are a senior media strategist at Smart 1 Marketing, a full-service digital agency "
        "(geofenced display, Connected TV, streaming audio, digital out-of-home, weather-triggered "
        "advertising). Write the narrative copy for the client-facing proposal described below.\n\n"
        "Rules:\n"
        "- Return STRICT JSON only: {\"sections\": [{\"id\": \"...\", \"body\": \"...\"}]}, one entry per "
        "id in sections_to_write, and no other keys.\n"
        "- Use ONLY the facts given. Do not invent statistics, client results, awards, case studies, "
        "impression counts, or prices. Every number in the plan is already in the tables the client "
        "will see next to your copy; contradicting one is worse than omitting it.\n"
        "- When target_area_count is above 1, write about all of the areas rather than one of them.\n"
        "- Each section is 2 to 4 short paragraphs, plain professional English, second person about "
        "the client's business. No headings, no bullet characters unless the current copy uses them.\n"
        "- Keep anything factual the current copy already states.\n\n"
        + json.dumps(facts, ensure_ascii=False))
    try:
        result = _json_from_ai(_openai_response(prompt, 5000))
        written = {str(sec.get("id")): str(sec.get("body") or "")
                   for sec in (result.get("sections") or []) if sec.get("id")}
    except Exception as exc:                            # noqa: BLE001
        logger.exception("AI section draft failed")
        return jsonify({"ok": False, "error": "AI draft failed", "detail": str(exc)}), 502
    if not written:
        return jsonify({"ok": False, "error": "The AI returned no sections."}), 502
    return jsonify({"ok": True, "sections": written})


# =====================================================================
# Campaign intelligence — served here, not fetched from the IO app
# =====================================================================
@app.get("/api/industries")
def api_industries():
    """The industry library, for the wizard's picker."""
    return jsonify({"ok": True, "industries": hub_industries.industry_list()})


@app.post("/api/generate-business-description")
def api_business_description():
    """A Google Business Profile description, to the IO builder's rules.

    Identical prompt to /tools/io — it lives in hub.business_description now.
    Before this route existed, the wizard's Generate button called a path on
    the IO *app* that has never had it, so the placeholder ran every time and
    the description on a proposal was never the one on the IO.
    """
    body = request.get_json(force=True) or {}
    urls = [str(u).strip() for u in (body.get("urls") or []) if str(u).strip()]
    if not urls:
        return jsonify({"ok": False, "error": "A website URL is required."}), 400
    areas = hub_areas.normalize(body.get("areas") or body.get("targetAreas")) \
        or hub_areas.from_legacy(body)
    prompt = hub_desc.prompt_for(urls, client=body.get("client", ""),
                                 industry=body.get("industry", ""), areas=areas,
                                 brandfetch=body.get("brandfetch") or {},
                                 geo=str(body.get("geo") or ""))
    try:
        description = _openai_response(prompt, 5000)
    except Exception as exc:                            # noqa: BLE001
        return jsonify({"ok": False, "error": "Description request failed",
                        "detail": str(exc)}), 502
    if not description:
        return jsonify({"ok": False, "error": "The AI returned no description."}), 502
    return jsonify({"ok": True, "description": description,
                    "warnings": hub_desc.check(description)})


@app.post("/api/review-landing-page")
def api_review_landing_page():
    """Conversion review of the landing page, before the campaign is priced."""
    body = request.get_json(force=True) or {}
    url = str(body.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "A landing-page URL is required."}), 400
    prompt = (
        f"Review this campaign landing page: {url}\n"
        f"Client: {str(body.get('client') or '')}\n"
        f"Product or use: {str(body.get('product') or 'Campaign landing page')}\n"
        f"Campaign goals: {', '.join(str(o) for o in (body.get('objectives') or []))}\n"
        "Visit the page and evaluate it as a conversion-focused landing page. Determine whether it has "
        "a clear primary call to action above the fold and throughout the page. Review message match, "
        "headline clarity, offer clarity, forms, phone calls, buttons, mobile usability, page speed "
        "signals, trust indicators, testimonials, privacy language, tracking readiness, distractions, "
        "and whether the conversion action is easy to complete. Return a concise internal note with "
        "these headings: CTA Status, Strengths, Required Fixes Before Launch, Recommended Improvements, "
        "Tracking Checks. Be specific and practical. If the page cannot be accessed, say so clearly.")
    try:
        return jsonify({"ok": True, "review": _openai_response(prompt, 5000), "url": url})
    except Exception as exc:                            # noqa: BLE001
        return jsonify({"ok": False, "error": "Landing-page review failed",
                        "detail": str(exc)}), 502


@app.post("/api/zipcodes-in-radius")
def api_zipcodes_in_radius():
    """The ZIP Codes a radius touches — per target area, not per campaign.

    Same lookup the IO builder runs. Having it here means the ZIP list is
    attached to the area it belongs to while the proposal is still being
    built, rather than being rebuilt at IO time against whichever origin
    happened to be typed first.
    """
    body = request.get_json(force=True) or {}
    origin = str(body.get("origin") or "").strip()
    radius = str(body.get("radius") or "").strip()
    if not origin or not radius:
        return jsonify({"ok": False, "error": "An origin and a radius are required."}), 400
    prompt = (
        f"Find the complete list of United States ZIP Codes whose geographic polygon is fully or "
        f"partially touched by a {radius}-mile radius centered on {origin}. Include a ZIP Code whenever "
        f"any portion of that ZIP Code area intersects the radius, not only when its centroid is inside. "
        "Use current authoritative geographic sources where possible. Return only five-digit ZIP Codes, "
        "comma-separated, sorted ascending, with no commentary. Be exhaustive and do not intentionally "
        "omit any matching ZIP Code.")
    try:
        zips = hub_areas.zip_list(_openai_response(prompt, 12000))
    except Exception as exc:                            # noqa: BLE001
        return jsonify({"ok": False, "error": "ZIP-radius lookup failed",
                        "detail": str(exc)}), 502
    if not zips:
        return jsonify({"ok": False, "error": "No ZIP Codes were returned."}), 502
    return jsonify({"ok": True, "zipcodes": ", ".join(zips), "count": len(zips),
                    "warning": "AI-assisted ZIP-radius results should be reviewed before "
                               "trafficking — ZIP boundaries and radius intersections change."})


@app.post("/api/estimate-audience")
def api_estimate_audience():
    """Size every target area on the campaign, and total them.

    Each area is sized on its own. A campaign covering Carmel, Fishers and an
    Indianapolis DMA buy is three different reach questions, and one merged
    answer hides that two of them overlap heavily while the third does not.

    Anything the AI cannot size falls back to the shared geometric estimate,
    and an area neither can size is returned as null — "not measured", never
    a confident zero.
    """
    body = request.get_json(force=True) or {}
    areas = hub_areas.normalize(body.get("areas") or body.get("targetAreas")) \
        or hub_areas.from_legacy(body)
    if not areas:
        return jsonify({"ok": False, "error": "Add a target area first."}), 400

    demographics = {"gender": body.get("gender") or "Both",
                    "ages": body.get("ages") or [],
                    "income": body.get("income") or [],
                    "industry": body.get("industry") or ""}
    prompt = (
        "You are a media planner sizing advertising reach for United States geographies. For EACH "
        "target area below, estimate population, addressable audience after the stated demographic "
        "filters, households, and reachable devices. Return STRICT JSON only: "
        "{\"areas\": [{\"id\": \"...\", \"population\": n, \"addressable_audience\": n, "
        "\"households\": n, \"devices\": n, \"rationale\": \"one short sentence\"}]}. "
        "Use whole numbers. If an area cannot be sized from real data, omit it from the array rather "
        "than guessing — an omitted area is reported as not measured, which is correct, and an "
        "invented one is not.\n\n"
        + json.dumps({"demographics": demographics,
                      "areas": [{"id": a["id"], "area": hub_areas.label(a),
                                 "type": a["type"], "radius_miles": a["radius"],
                                 "zip_codes": a["zips"]} for a in areas]},
                     ensure_ascii=False))
    by_id, ai_used = {}, False
    try:
        for row in (_json_from_ai(_openai_response(prompt, 4000)).get("areas") or []):
            if row.get("id"):
                by_id[str(row["id"])] = row
        ai_used = bool(by_id)
    except Exception as exc:                            # noqa: BLE001
        logger.warning("audience estimate fell back to the built-in model: %s", exc)

    out, totals = [], {"pop": 0, "aud": 0, "hh": 0, "dev": 0}
    measured = 0
    for area in areas:
        row = by_id.get(area["id"]) or {}
        population = int(row.get("population") or 0) or (hub_areas.estimated_population(area) or 0)
        if not population:
            out.append({"id": area["id"], "label": hub_areas.label(area),
                        "measured": False, "note": "Not measured — this area could "
                                                   "not be sized from what was entered."})
            continue
        audience = int(row.get("addressable_audience") or 0) or int(population * 0.85)
        households = int(row.get("households") or 0) or int(audience / 1.9)
        devices = int(row.get("devices") or 0) or int(audience * 2.3)
        measured += 1
        out.append({"id": area["id"], "label": hub_areas.label(area), "measured": True,
                    "pop": population, "aud": audience, "hh": households, "dev": devices,
                    "ai": bool(row), "rationale": str(row.get("rationale") or "")})
        totals["pop"] += population
        totals["aud"] += audience
        totals["hh"] += households
        totals["dev"] += devices

    note = ""
    if len(areas) > 1:
        note = ("Areas are totalled without deducting overlap, so nearby areas "
                "double-count the people they share.")
    unmeasured = [row["label"] for row in out if not row["measured"]]
    return jsonify({"ok": True, "areas": out, "ai": ai_used,
                    "totals": totals if measured else None,
                    "measured": measured, "unmeasured": unmeasured, "note": note})


# =====================================================================
# Delivery — the PDF the client gets, filed and pushed to Smart 1 Suite
# =====================================================================
def _suite_status():
    try:
        from hub import suite_opportunity
        return suite_opportunity.status()
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "problems": [f"Suite helper unavailable ({type(exc).__name__})."]}


@app.get("/api/suite/status")
def api_suite_status():
    """Whether an opportunity will actually be created, in words."""
    return jsonify(_suite_status())


def _upload_proposal_pdf(quote_number, client, pdf_bytes, revision):
    """Put the client-facing PDF somewhere it can be linked to.

    Through hub.storage rather than a local cloudinary.config() call, per the
    migration rule. Returns "" when Cloudinary is not configured — the archive
    copy in the database is still there, and the caller says so rather than
    handing anyone a dead link.
    """
    try:
        from hub import storage
        from hub.config import settings
        safe = storage.slug(f"{quote_number}-{client or 'client'}", "quote")
        public_id = f"{settings.folder('proposals')}/quotes/{safe}-r{revision}.pdf"
        return storage.put("proposals", public_id, pdf_bytes,
                           public_id=public_id, overwrite=True).url or ""
    except Exception as exc:                            # noqa: BLE001
        logger.warning("proposal PDF upload failed: %s", exc)
        return ""


@app.post("/api/quotes/<int:qid>/deliver")
def deliver_quote(qid):
    """Send a proposal to a client: PDF, filed on the client, opportunity in Suite.

    Three things happen, in an order chosen so a later failure never loses an
    earlier success:

      1. The branded PDF is generated and archived on the quote (always works).
      2. It is uploaded and filed on the client's Hub record, so it shows up on
         Client 360 with everything else that client has been sent.
      3. An opportunity is created in Smart 1 Suite.

    Step 3 is the one that can come back asking a question. If Suite holds no
    contact for this client, the response is `needs_contact` — the proposal is
    already saved and filed at that point, and the rep supplies a name plus an
    email or phone and posts again. That is the whole reason this is not done
    silently in the background: inventing a contact from a business name is
    how a Suite account fills with records nobody can call.
    """
    body = request.get_json(silent=True) or {}
    db = SessionLocal()
    try:
        q = db.get(Quote, qid)
        if not q:
            return jsonify({"ok": False, "error": "Quote not found"}), 404
        state = json.loads(q.data or "{}")
        ensure_sections(state)

        pdf_bytes, title = build_proposal_pdf(q, state)
        q.pdf_blob = pdf_bytes
        q.pdf_filename = title + ".pdf"
        q.pdf_generated_at = datetime.now(timezone.utc)

        url = q.pdf_url
        if not url or body.get("regenerate", True):
            url = _upload_proposal_pdf(q.quote_number, q.client, pdf_bytes, q.revision or 1) or q.pdf_url
        q.pdf_url = url or ""

        filed, filed_note = None, ""
        if q.client:
            try:
                from hub import proposals as hub_proposals
                # One filed document per revision. Pressing Send twice on the
                # same revision -- a double click, or answering the Suite
                # contact question -- must not leave the client's record
                # showing the same proposal three times; each of those rows
                # looks like a separate thing we sent them.
                prior_id, _, prior_rev = (q.client_filed_as or "").partition("@")
                if prior_id and prior_rev == str(q.revision or 1):
                    hub_proposals.delete_proposal(q.client, prior_id)
                filed = hub_proposals.add_proposal(
                    q.client, title + ".pdf", pdf_bytes,
                    title=f"{q.quote_number} — {q.package or 'Marketing Proposal'}"
                          + (f" (rev {q.revision})" if (q.revision or 1) > 1 else ""),
                    note=f"Built in the Proposal Builder. {q.geo_summary or ''}".strip(),
                    actor=request.environ.get("s1hub.user") or state.get("salesContact") or "",
                    value=str(q.monthly_budget or 0), term="monthly", status="sent")
                q.client_filed_as = f"{filed.get('id') or ''}@{q.revision or 1}"[:64]
            except Exception as exc:                    # noqa: BLE001
                logger.warning("filing the proposal on the client failed: %s", exc)
                filed_note = (f"The proposal could not be filed on {q.client}'s record "
                              f"({type(exc).__name__}). It is still saved here.")
        else:
            filed_note = "No client name on this quote, so it was not filed on a client record."

        contact = body.get("contact") or {
            "name": state.get("clientContactName") or "",
            "email": state.get("clientContactEmail") or "",
            "phone": state.get("clientContactPhone") or "",
        }
        if q.suite_contact_id and not contact.get("id"):
            contact["id"] = q.suite_contact_id

        # The PDF is built and filed by this point. A Suite problem -- even the
        # helper failing to import -- reports itself; it never costs the work
        # already done.
        try:
            from hub import suite_opportunity
            suite = suite_opportunity.push_proposal(
                client=q.client, title=f"{q.client} — {q.package or 'Marketing'} Proposal "
                                       f"({q.quote_number})",
                value=float(q.monthly_budget or 0), contact=contact,
                website=q.website, pdf_url=q.pdf_url,
                opportunity_id=q.suite_opportunity_id or "")
        except Exception as exc:                        # noqa: BLE001
            logger.warning("Suite push failed: %s", exc)
            suite = {"ok": False, "reason": f"Smart 1 Suite is unreachable "
                                            f"({type(exc).__name__})."}
        if suite.get("ok"):
            q.suite_opportunity_id = str(suite.get("opportunity_id") or "")[:64]
            found = suite.get("contact") or {}
            q.suite_contact_id = str(found.get("id") or "")[:64]
            # Remember the contact, so converting this quote to an IO does not
            # ask for it a second time. setdefault would not do: the wizard
            # seeds these keys as empty strings, so they are present-but-blank
            # rather than absent.
            for key, value in (("clientContactName", found.get("name")),
                               ("clientContactEmail", found.get("email")),
                               ("clientContactPhone", found.get("phone"))):
                if value and not state.get(key):
                    state[key] = value
            q.data = json.dumps(state, ensure_ascii=False)

        q.delivered_at = datetime.now(timezone.utc)
        if q.status == "Draft":
            q.status = "Sent"
        log_activity(db, q.id, "📤",
                     f"{q.quote_number} delivered — {q.client}"
                     + (f", Suite opportunity {q.suite_opportunity_id}" if suite.get("ok")
                        else ", no Suite opportunity"))
        db.commit()
        try:
            _audit("proposal_delivered", client=q.client, quote=q.quote_number,
                   suite=bool(suite.get("ok")))
        except Exception:                               # noqa: BLE001
            pass
        return jsonify({"ok": True, "quote": quote_json(q), "pdf_url": q.pdf_url,
                        "filed": filed, "filed_note": filed_note, "suite": suite})
    finally:
        db.close()


# =====================================================================
# The retired standalone builder's archive
# =====================================================================
@app.get("/api/legacy/proposals")
def legacy_proposals():
    """Proposals built in the old /sales/proposals tool.

    Read-only, and listed beside the quotes rather than merged into them: they
    have no quote number, no revision and no campaign data, and giving them
    invented ones would make the pipeline read as bigger and better-specified
    than it is.
    """
    try:
        from modules.proposal_builder import store as legacy_store
    except Exception as exc:                            # noqa: BLE001
        return jsonify({"ok": True, "proposals": [], "available": False,
                        "note": f"The old builder's archive is unreadable ({type(exc).__name__})."})
    try:
        rows = legacy_store.search_proposals(
            q=request.args.get("q", ""),
            limit=clamp_int(request.args.get("limit"), 50, 1, 200))
    except Exception as exc:                            # noqa: BLE001
        logger.warning("legacy proposal index unreadable: %s", exc)
        return jsonify({"ok": True, "proposals": [], "available": False,
                        "note": "The old builder's archive could not be read."})
    return jsonify({"ok": True, "proposals": rows, "available": True})


@app.post("/api/legacy/proposals/<pid>/import")
def legacy_import(pid):
    """Reopen an old standalone proposal as a quote in this builder.

    What carries over is what was structured: the customer, the industry, the
    recommended package and its monthly price, and the narrative blocks as
    editable text sections. What does not carry over is a media plan, because
    the old proposals never had one — the products were named inside prose.
    The imported quote therefore lands as a Draft with its gaps listed, which
    is an honest description of it.
    """
    try:
        from modules.proposal_builder import store as legacy_store
    except Exception:                                   # noqa: BLE001
        return jsonify({"ok": False, "error": "The old builder's archive is unavailable."}), 503
    record = legacy_store.get_proposal(pid)
    if not record:
        return jsonify({"ok": False, "error": "No such proposal."}), 404

    customer = record.get("customer") or {}
    sections, monthly = [], record.get("monthly_investment") or 0
    for i, block in enumerate(record.get("blocks") or []):
        data = block.get("data") or {}
        title = str(data.get("heading") or data.get("title") or "").strip()
        parts = [str(data.get("body") or "").strip()]
        for item in (data.get("items") or []):
            if isinstance(item, dict):
                parts.append(f"• {item.get('label', '')}: {item.get('value', '')}")
            else:
                parts.append(f"• {item}")
        text = "\n".join(p for p in parts if p)
        if title or text:
            sections.append({"id": f"legacy{i}", "title": title or f"Section {i + 1}",
                             "kind": "text", "enabled": True, "body": text})

    city, st = customer.get("city", ""), customer.get("state", "")
    where = ", ".join(x for x in (city, st) if x) or customer.get("zip", "")
    areas = hub_areas.normalize([{"name": where, "origin": where}]) if where else []

    state = {
        "client": customer.get("business_name", ""),
        "url": customer.get("website", ""),
        "industry": record.get("industry_label") or record.get("industry") or "",
        "description": "", "descriptionMode": "I have one",
        "objectives": [], "kpis": [], "exclusions": [], "audiences": [], "items": [],
        "targetAreas": areas,
        "budget": int(float(monthly or 0)), "months": 12,
        "clientContactName": customer.get("contact_name", ""),
        "clientContactEmail": customer.get("contact_email", ""),
        "clientContactPhone": customer.get("contact_phone", ""),
        "salesContact": customer.get("salesperson", ""),
        "sections": sections or None,
        "importedFrom": f"proposal-builder:{pid}",
    }
    hub_areas.apply_to_legacy(state, areas)

    db = SessionLocal()
    try:
        q = Quote(quote_number=next_quote_number(db), status="Draft",
                  data=json.dumps(state, ensure_ascii=False))
        summarize_into(q, state)
        q.package = str(record.get("recommended_package") or "")[:40]
        q.pdf_url = str(record.get("pdf_url") or "")[:600]
        db.add(q)
        db.flush()
        log_activity(db, q.id, "📥",
                     f"{q.quote_number} imported from the old Proposal Builder — {q.client}")
        db.commit()
        return jsonify({"ok": True, "quote": quote_json(q, include_data=True),
                        "note": "Goals, products and budget were never structured in the old "
                                "builder, so they are blank rather than guessed. The copy came "
                                "across as editable sections."})
    finally:
        db.close()


@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    logger.exception("Unhandled application error")
    return jsonify({"error": "Internal server error", "type": type(exc).__name__, "message": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8001")), debug=False)
