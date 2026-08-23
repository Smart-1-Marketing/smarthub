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
from hub import creative_needs as hub_creative
from hub import current_marketing as hub_discovery
from hub import industries as hub_industries
from hub import proposal_spec as hub_spec
from hub import rate_card as hub_rate_card
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


# Columns added to `quotes` after the table was first created, as
# (column, type). create_all() creates missing TABLES, never missing columns,
# so the live database -- which has quotes in it already -- would keep working
# right up until the first query mentioning a delivery column.
_LATE_QUOTE_COLUMNS = [
    ("pdf_url", "VARCHAR(600)"),
    ("client_filed_as", "VARCHAR(64)"),
    ("suite_contact_id", "VARCHAR(64)"),
    ("suite_opportunity_id", "VARCHAR(64)"),
    ("delivered_at", "TIMESTAMP"),
]


def _add_missing_columns() -> None:
    """Add the columns above, asking first which ones are actually missing.

    This used to fire all five unconditionally and swallow the failures, on the
    reasoning that "already exists" is the normal case after the first boot. It
    is worse than that: all five are declared on the Quote model, so
    create_all() puts them on a fresh database too and ALL FIVE fail on EVERY
    boot, on every database, including the first. Two gunicorn workers, so ten
    Postgres ERROR lines per deploy that mean nothing -- and a log that always
    carries ten fake errors is a log nobody finds the real one in. CI surfaced
    it: the Postgres service prints its log at the end of every run.

    modules/image_picker/models.py had the identical bug and the identical fix.
    ADD COLUMN IF NOT EXISTS would be shorter, and is what modules/sites_admin
    uses -- but that module talks to Postgres directly, and this one shares the
    Hub engine, which is SQLite in local development, where the answer is
    `near "EXISTS": syntax error`. The inspector is the same answer on both.
    """
    from sqlalchemy import inspect as _inspect, text as _text
    try:
        have = {c["name"] for c in _inspect(engine).get_columns("quotes")}
    except Exception:                                   # noqa: BLE001
        return                                          # no table: nothing to alter
    for column, coltype in _LATE_QUOTE_COLUMNS:
        if column in have:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(_text(
                    f"ALTER TABLE quotes ADD COLUMN {column} {coltype}"))
        except Exception:                               # noqa: BLE001
            pass                                        # raced by the other worker


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
    # Video and audio each need their own answer before the plan is priced --
    # a Connected TV buy with no spot is a launch date nobody can hit. Display
    # is still the one blanket question below it.
    gaps.extend(hub_creative.gaps(s))
    gaps.extend(hub_discovery.gaps(s))
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
def _client_key(name, url):
    """Which client record this quote belongs to, joined the shared way.

    hub/client_key.py is the single place that decides what a client is --
    d:<domain> where there is a URL, n:<name-slug> where there is not. Matching
    on the name alone is what attributed "Acme" to whichever of Acme Plumbing,
    Acme Roofing and Acme Electric came out of a dict first.
    """
    try:
        from hub import client_key as hub_client_key
        return hub_client_key.client_key(name or "", url or "")
    except Exception:                                       # noqa: BLE001
        return ""


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
        # Derived on read, never stored. CLAUDE.md: a stored key goes stale the
        # moment a client is renamed in Knack, and create_all() would not add
        # the column to the live Postgres anyway -- every local test would pass
        # against a column that was silently absent in production.
        "client_key": _client_key(q.client, q.website),
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
        "growth": growth_options(state),
        "guardrails": compute_guardrails(state),
        "target_areas": campaign_areas(state),
        "creative": hub_creative.evaluate(state),
        "suggestions": hub_discovery.suggestions(state),
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


def growth_options(state) -> dict:
    """Two grounded routes to a bigger plan, for the foot of the proposal.

    A client who says yes usually asks "and what would more look like?", and
    the answer has always been improvised on a call. It belongs on the
    document -- but only if the numbers are defensible, so neither route
    invents one:

      * **Raise a budget.** The difference between what each line is quoted at
        and what the Accelerated package already prices it at. That figure is
        on the proposal two sections above; this just names the gap.

      * **Add a product.** Whatever discovery said they are missing (the
        We Suggest They Should list), priced at the rate card's own monthly
        minimum for that product -- the smallest honest number, not a guess at
        what they might spend.

    Every row is editable on the proposal. `custom` rows are ones the rep
    added or re-priced, and they are returned untouched.
    """
    state = state or {}
    months = max(1, int(state.get("months") or 1))
    items = state.get("items") or []
    on_plan = {str(i.get("product") or "").strip().lower() for i in items}

    # --- raising what is already there ---
    # The Accelerated package is built at 150% of budget and is already on the
    # document, so the uplift per line is arithmetic rather than opinion.
    increases = []
    accelerated = None
    for pkg in state.get("packages") or []:
        if str(pkg.get("name") or "").lower().startswith("acceler"):
            accelerated = pkg
            break
    by_product = {}
    for line in ((accelerated or {}).get("lines") or []):
        by_product[str(line.get("product") or "").strip().lower()] = line
    for item in items:
        now = float(item.get("dollars") or 0)
        target = float((by_product.get(str(item.get("product") or "").strip().lower())
                        or {}).get("dollars") or 0)
        if target > now > 0:
            increases.append({
                "product": item.get("product", ""),
                "category": item.get("category", ""),
                "current": round(now, 2),
                "suggested": round(target, 2),
                "uplift": round(target - now, 2),
                "campaign": round((target - now) * months, 2),
            })

    # --- adding what is not there ---
    additions = []
    seen = set()
    try:
        from hub import current_marketing as hub_marketing
        wanted = hub_marketing.suggestions(state)
    except Exception:                                       # noqa: BLE001
        wanted = []
    for suggestion in wanted:
        for name in suggestion.get("products") or []:
            key = str(name).strip().lower()
            if not key or key in on_plan or key in seen:
                continue
            seen.add(key)
            card = hub_rate_card.find(name) or {}
            # No card entry means no defensible price, so it is offered as
            # something to quote rather than with a number nobody can stand
            # behind.
            minimum = (hub_rate_card.minimum_for(name, card.get("category", ""))
                       if card else 0)
            additions.append({
                "product": card.get("product") or name,
                "category": card.get("category", ""),
                "why": suggestion.get("title", ""),
                "suggested": round(float(minimum), 2) if minimum else 0,
                "campaign": round(float(minimum) * months, 2) if minimum else 0,
                "rate": card.get("listed_rate", ""),
                "quoted": bool(minimum),
            })

    edits = state.get("growthEdits") or {}
    for row in increases:
        override = edits.get("inc:" + row["product"])
        if override is not None:
            try:
                row["suggested"] = round(float(override), 2)
                row["uplift"] = round(row["suggested"] - row["current"], 2)
                row["campaign"] = round(row["uplift"] * months, 2)
                row["custom"] = True
            except (TypeError, ValueError):
                pass
    for row in additions:
        override = edits.get("add:" + row["product"])
        if override is not None:
            try:
                row["suggested"] = round(float(override), 2)
                row["campaign"] = round(row["suggested"] * months, 2)
                row["quoted"] = row["suggested"] > 0
                row["custom"] = True
            except (TypeError, ValueError):
                pass

    dropped = set(state.get("growthDropped") or [])
    increases = [r for r in increases if ("inc:" + r["product"]) not in dropped]
    additions = [r for r in additions if ("add:" + r["product"]) not in dropped]

    return {
        "increases": increases,
        "additions": additions,
        "months": months,
        "increase_monthly": round(sum(r["uplift"] for r in increases), 2),
        "addition_monthly": round(sum(r["suggested"] for r in additions), 2),
        "any": bool(increases or additions),
    }


def investment_lines(state, q):
    """Platform, media and one-time production, kept apart.

    The specification requires recurring SaaS fees to be separated from media
    spend and one-time setup. Blending them is how a client comes to believe
    the platform stops costing money when they pause a campaign.
    """
    state = state or {}
    months = max(1, int(getattr(q, "months", 0) or state.get("months") or 1))
    monthly_media = float(getattr(q, "monthly_budget", 0) or 0) or \
        sum(float(i.get("dollars") or 0) for i in state.get("items") or [])

    lines = []
    suite = state.get("suiteTier") or {}
    if suite.get("include") is not False:
        tier = suite if suite.get("name") else hub_spec.suggested_tier(monthly_media)
        lines.append({"label": f"Smart 1 Suite — {tier['name']} ({tier.get('specs', '')})",
                      "amount": float(tier.get("monthly") or 0),
                      "recurs": "Monthly", "kind": "saas"})
    lines.append({"label": "Media spend", "amount": round(monthly_media, 2),
                  "recurs": "Monthly", "kind": "media"})

    creative = hub_creative.evaluate(state)
    for row in creative["media"]:
        if row["answer"] == hub_creative.CLIENT_PAYS and row["fee"]:
            lines.append({"label": f"{row['label'].split(' (')[0]} creative production",
                          "amount": float(row["fee"]), "recurs": "One-time",
                          "kind": "setup"})
    try:
        extra = float(str(state.get("creativeFee") or "0").replace("$", "").replace(",", ""))
    except ValueError:
        extra = 0.0
    if extra:
        lines.append({"label": "Display creative production", "amount": extra,
                      "recurs": "One-time", "kind": "setup"})

    recurring = sum(l["amount"] for l in lines if l["recurs"] == "Monthly")
    one_time = sum(l["amount"] for l in lines if l["recurs"] == "One-time")
    return {"lines": lines, "recurring_monthly": round(recurring, 2),
            "one_time": round(one_time, 2),
            "first_month": round(recurring + one_time, 2),
            "campaign_total": round(recurring * months + one_time, 2),
            "months": months}


def _head_style_rows():
    """The navy-header table style every generated section uses."""
    return [("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]


def _head_style():
    return TableStyle(_head_style_rows())


# What each medium does in the customer journey, and what it is judged on.
# Awareness channels measured on clicks is the single most common way a good
# campaign gets called a failure.
_CHANNEL_ROLE = {
    hub_creative.VIDEO: ("Top of funnel — builds awareness and trust on the "
                         "screens the household already watches",
                         "Completed views and completion rate"),
    hub_creative.AUDIO: ("Mid funnel — reaches the 79% of digital audio time "
                         "spent with no screen at all",
                         "Reach, frequency and listen-through"),
    hub_creative.SOCIAL: ("Mid funnel — consideration and re-engagement in feed",
                          "Engaged sessions and cost per lead"),
    hub_creative.DISPLAY: ("Full funnel — precision targeting and retargeting "
                           "against the people already in market",
                           "Click-through rate and cost per action"),
    hub_creative.OTHER: ("Supports the campaign", "Cost per action"),
}


def _channel_role(item):
    medium = hub_creative.medium_of(item)
    category = str(item.get("category") or "").upper()
    if "SEARCH ENGINE MARKETING" in category or "PAY PER CLICK" in category:
        return ("Bottom of funnel — captures demand that is already searching",
                "Cost per lead and conversion rate")
    if "SEARCH ENGINE OPTIMIZATION" in category:
        return ("Compounding — organic and AI-answer visibility",
                "Ranked terms, map-pack visibility and organic leads")
    if "RETARGETING" in category:
        return ("Bottom of funnel — brings back the people who already came",
                "Return visits and cost per conversion")
    return _CHANNEL_ROLE.get(medium, _CHANNEL_ROLE[hub_creative.OTHER])


def _creative_phrase(row):
    """What the creative gate decided, phrased for a client-facing document."""
    if row["answer"] == hub_creative.HAS:
        return "Supplied by the client"
    if row["answer"] == hub_creative.CLIENT_PAYS:
        return f"Produced by Smart 1 — {_money(row['fee'])} one-time"
    if row["answer"] == hub_creative.COMP:
        return "Produced by Smart 1 at no charge"
    return "To be confirmed before launch"


# Roughly how much a section costs in vertical space, so the scale is chosen
# from what the document actually contains rather than from a page count we
# would only learn after building it once.
_SECTION_WEIGHT = {"mediaplan": 3, "packages": 3, "roi": 4, "areas": 2,
                   "channels": 3, "creative": 2, "timeline": 2, "friction": 2,
                   "reach": 1, "kpis": 1, "cover": 0}

TYPE_SCALE_MIN = 0.82
TYPE_SCALE_FULL_UNDER = 38


def _type_scale(state) -> float:
    """How far to shrink the type so a full proposal stays compact.

    Estimated from the content: characters of prose, plus a weight per
    generated table and a row-count for the two that grow with the campaign.
    Building the PDF twice to measure the real page count would be exact, but
    reportlab gives no page count until it has built, and a rep waiting twice
    as long for a PDF would notice that far more than the half-point of type.
    """
    state = state or {}
    sections = [sec for sec in state.get("sections") or [] if sec.get("enabled", True)]
    prose = sum(len(str(sec.get("body") or "")) for sec in sections)
    weight = sum(_SECTION_WEIGHT.get(sec.get("kind"), 1) for sec in sections)
    # The two tables whose height follows the campaign rather than the outline.
    weight += len(state.get("items") or []) + len(campaign_areas(state))

    load = prose / 900.0 + weight

    # A freshly seeded proposal — the full outline, its default copy, one
    # product, one area — measures about 34. That is the baseline a normal
    # proposal sits at, so it must scale at 1.0: "lower the fonts when
    # necessary" means when there is more than usual in the document, not
    # always. Shrinking every proposal by default would just make the type
    # smaller and prove nothing.
    if load <= TYPE_SCALE_FULL_UNDER:
        return 1.0
    # Ease down to the floor rather than stepping: two proposals of similar
    # length should not come out at visibly different type sizes.
    return round(max(TYPE_SCALE_MIN, 1.0 - (load - TYPE_SCALE_FULL_UNDER) * 0.009), 3)


def build_proposal_pdf(q, state):
    title = f"S1M Proposal - {q.quote_number} - {q.client or 'Client'}"
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, rightMargin=0.55 * inch, leftMargin=0.55 * inch,
                            topMargin=0.55 * inch, bottomMargin=0.6 * inch, title=title)
    ss = getSampleStyleSheet()
    # Type scales down as the document grows, so a proposal with a lot in it
    # stays compact rather than sprawling. Bounded at 0.82: below that the
    # body copy stops being comfortable to read on paper, and a proposal
    # nobody wants to read is worse than one that runs an extra page.
    scale = _type_scale(state)

    def sized(size, leading):
        return round(size * scale, 1), round(leading * scale, 1)

    ts, tl = sized(21, 25)
    ss_, sl = sized(10.5, 13)
    hs, hl = sized(13, 16)
    bs, bl = sized(9.5, 13)
    ms, ml = sized(8, 10.5)

    st_title = ParagraphStyle("T", parent=ss["Title"], textColor=NAVY, fontSize=ts, leading=tl, alignment=TA_CENTER, spaceAfter=2)
    st_sub = ParagraphStyle("Sub", parent=ss["BodyText"], textColor=MUTED, fontSize=ss_, alignment=TA_CENTER, spaceAfter=round(10 * scale))
    st_h2 = ParagraphStyle("H2", parent=ss["Heading2"], textColor=NAVY, fontSize=hs, leading=hl, spaceBefore=round(13 * scale), spaceAfter=round(5 * scale))
    st_body = ParagraphStyle("B", parent=ss["BodyText"], fontSize=bs, leading=bl, spaceAfter=round(5 * scale))
    st_small = ParagraphStyle("S", parent=ss["BodyText"], fontSize=ms, leading=ml, textColor=MUTED)

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
            # Recurring platform licensing, recurring media and one-time
            # production, shown apart. A client reading one blended number
            # cannot tell what stops if they pause the media.
            invest = investment_lines(state, q)
            rows = [["", "Amount", "Recurs"]]
            for line in invest["lines"]:
                rows.append([_p(line["label"], st_small), _money(line["amount"]),
                             _p(line["recurs"], st_small)])
            rows.append(["Total first month", _money(invest["first_month"]), ""])
            rows.append([f"Total campaign ({q.months} mo)",
                         _money(invest["campaign_total"]), ""])
            it = Table(rows, colWidths=[4.2 * inch, 1.6 * inch, 1.6 * inch], repeatRows=1)
            style = _head_style_rows()
            for offset in (2, 1):
                style.append(("BACKGROUND", (0, len(rows) - offset),
                              (-1, len(rows) - offset), SOFT))
                style.append(("FONTNAME", (0, len(rows) - offset),
                              (-1, len(rows) - offset), "Helvetica-Bold"))
            it.setStyle(TableStyle(style))
            story += [it, Spacer(1, 8)]

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
        elif kind == "friction":
            picks = hub_discovery.suggestions(state)
            if picks:
                rows = [["We suggest they should", "Why"]]
                for pick in picks:
                    rows.append([_p(pick["title"], st_small),
                                 _p(pick["detail"], st_small)])
                ft = Table(rows, colWidths=[2.4 * inch, 5.0 * inch], repeatRows=1)
                ft.setStyle(_head_style())
                story.append(ft)
        elif kind == "channels":
            # Every channel with its role in the funnel and its KPI. The
            # specification forbids listing channels without that mapping.
            rows = [["Channel", "Role in the funnel", "Primary KPI"]]
            for item in state.get("items") or []:
                role, kpi = _channel_role(item)
                rows.append([_p(item.get("product") or "", st_small),
                             _p(role, st_small), _p(kpi, st_small)])
            if len(rows) > 1:
                ct = Table(rows, colWidths=[2.3 * inch, 3.1 * inch, 2.0 * inch], repeatRows=1)
                ct.setStyle(_head_style())
                story.append(ct)
        elif kind == "creative":
            plan = hub_creative.evaluate(state)
            if plan["media"]:
                rows = [["Medium", "Campaign spend", "Creative"]]
                for row in plan["media"]:
                    rows.append([_p(row["label"], st_small),
                                 _money(row["spend"]),
                                 _p(_creative_phrase(row), st_small)])
                kt = Table(rows, colWidths=[2.9 * inch, 1.5 * inch, 3.0 * inch], repeatRows=1)
                kt.setStyle(_head_style())
                story.append(kt)
        elif kind == "timeline":
            rows = [["Phase", "What happens"]]
            for phase in hub_spec.TIMELINE:
                rows.append([_p(f"{phase['phase']}\n{phase['title']}", st_small),
                             _p(phase["detail"], st_small)])
            tt = Table(rows, colWidths=[1.7 * inch, 5.7 * inch], repeatRows=1)
            tt.setStyle(_head_style())
            story.append(tt)
        elif kind == "roi":
            results = expected_results(state)
            rows = [["Product", "Monthly", f"Campaign ({results['months']} mo)",
                     "Rate", "Estimated delivery"]]
            for row in results["rows"]:
                # "Not impression-based" rather than a blank or a zero: a
                # management fee has no impressions, and either alternative
                # reads as a product that delivers nothing.
                delivery = (f"{row['units']:,} {row['unit_label']}" if row["units"]
                            else "Not impression-based")
                rows.append([_p(row["product"], st_small), _money(row["monthly"]),
                             _money(row["campaign"]), _p(row["rate"], st_small),
                             _p(delivery, st_small)])
            totals = results["totals"]
            summary = []
            if totals["impressions"]:
                summary.append(f"{totals['impressions']:,} impressions")
            if totals["views"]:
                summary.append(f"{totals['views']:,} video views")
            rows.append(["Campaign total", _money(totals["monthly"]),
                         _money(totals["campaign"]), "",
                         _p(" · ".join(summary) or "—", st_small)])
            rt = Table(rows, colWidths=[2.0 * inch, 1.0 * inch, 1.15 * inch,
                                        1.25 * inch, 2.0 * inch], repeatRows=1)
            style = _head_style_rows()
            style.append(("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), SOFT))
            style.append(("FONTNAME", (0, len(rows) - 1), (-1, len(rows) - 1),
                          "Helvetica-Bold"))
            rt.setStyle(TableStyle(style))
            story.append(rt)
            if results["unpriced"]:
                story.append(_p("Not included in the delivery totals (no "
                                "impression-based rate): "
                                + ", ".join(results["unpriced"]) + ".", st_small))
            if results["metrics"]:
                story.append(_p("Tracked and reported monthly in the Smart 1 Suite: "
                                + ", ".join(results["metrics"]) + ".", st_body))
            if results.get("traditional_note"):
                story.append(_p(results["traditional_note"], st_body))
        elif kind == "growth":
            growth = growth_options(state)
            if not growth["any"]:
                story.append(_p("Every product the discovery answers pointed at "
                                "is already on the plan above.", st_small))
            else:
                if growth["increases"]:
                    rows = [["Product", "Quoted", "Recommended", "More / month",
                             f"Over {growth['months']} mo"]]
                    for row in growth["increases"]:
                        rows.append([_p(row["product"], st_small),
                                     _money(row["current"]), _money(row["suggested"]),
                                     _money(row["uplift"]), _money(row["campaign"])])
                    rows.append(["Additional monthly", "", "",
                                 _money(growth["increase_monthly"]),
                                 _money(growth["increase_monthly"] * growth["months"])])
                    gt = Table(rows, colWidths=[2.6 * inch, 1.1 * inch, 1.25 * inch,
                                                1.2 * inch, 1.25 * inch], repeatRows=1)
                    style = _head_style_rows()
                    style.append(("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), SOFT))
                    style.append(("FONTNAME", (0, len(rows) - 1), (-1, len(rows) - 1),
                                  "Helvetica-Bold"))
                    gt.setStyle(TableStyle(style))
                    story.append(_p("Raise a budget already on the plan", st_small))
                    story.append(gt)
                if growth["additions"]:
                    rows = [["Product", "Why", "From",
                             f"Over {growth['months']} mo"]]
                    for row in growth["additions"]:
                        rows.append([
                            _p(row["product"], st_small), _p(row["why"], st_small),
                            _money(row["suggested"]) if row["quoted"] else _p("Quoted on request", st_small),
                            _money(row["campaign"]) if row["quoted"] else _p("—", st_small)])
                    at2 = Table(rows, colWidths=[2.3 * inch, 2.8 * inch, 1.1 * inch,
                                                 1.2 * inch], repeatRows=1)
                    at2.setStyle(TableStyle(_head_style_rows()))
                    story.append(_p("Add what the discovery answers pointed at", st_small))
                    story.append(at2)
                story.append(_p("Raising a line uses the Accelerated option above; "
                                "adding one starts at the rate-card minimum.", st_small))
        elif kind == "zips":
            # The trafficking reference, at the back. Monospaced and small on
            # purpose: it is a list to be checked against, not read.
            rows = [["Target Area", "ZIP Codes"]]
            for area in campaign_areas(state):
                codes = hub_areas.zip_list(area)
                if codes:
                    rows.append([_p(hub_areas.label(area), st_small),
                                 _p(", ".join(codes), st_small)])
            if len(rows) > 1:
                zt = Table(rows, colWidths=[2.1 * inch, 5.3 * inch], repeatRows=1)
                zt.setStyle(TableStyle(_head_style_rows()))
                story.append(zt)
            else:
                story.append(_p("No ZIP Codes were captured for this campaign. "
                                "The insertion order needs them before trafficking.",
                                st_small))


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
        elif kind == "friction":
            for pick in hub_discovery.suggestions(state):
                d.add_paragraph(f"We suggest they should {pick['title'][0].lower()}"
                                f"{pick['title'][1:]} — {pick['detail']}")
        elif kind == "channels":
            for item in state.get("items") or []:
                role, kpi = _channel_role(item)
                d.add_paragraph(f"{item.get('product') or ''} — {role}. Measured on: {kpi}.")
        elif kind == "creative":
            for row in hub_creative.evaluate(state)["media"]:
                d.add_paragraph(f"{row['label']} — {_money(row['spend'])} campaign. "
                                f"{_creative_phrase(row)}.")
        elif kind == "timeline":
            for phase in hub_spec.TIMELINE:
                d.add_paragraph(f"{phase['phase']} · {phase['title']} — {phase['detail']}")
        elif kind == "roi":
            results = expected_results(state)
            t4 = d.add_table(rows=1, cols=4)
            t4.style = "Light Grid Accent 1"
            hdr = t4.rows[0].cells
            for i, htxt in enumerate(["Product", "Monthly",
                                      f"Campaign ({results['months']} mo)",
                                      "Estimated delivery"]):
                hdr[i].text = htxt
                hdr[i].paragraphs[0].runs[0].font.bold = True
            for row in results["rows"]:
                cells = t4.add_row().cells
                cells[0].text = row["product"]
                cells[1].text = _money(row["monthly"])
                cells[2].text = _money(row["campaign"])
                cells[3].text = (f"{row['units']:,} {row['unit_label']}" if row["units"]
                                 else "Not impression-based")
            totals = results["totals"]
            bits = []
            if totals["impressions"]:
                bits.append(f"{totals['impressions']:,} impressions")
            if totals["views"]:
                bits.append(f"{totals['views']:,} video views")
            if bits:
                d.add_paragraph("Campaign delivery: " + " · ".join(bits))
            if results["metrics"]:
                d.add_paragraph("Tracked in the Smart 1 Suite: "
                                + ", ".join(results["metrics"]) + ".")
            if results.get("traditional_note"):
                d.add_paragraph(results["traditional_note"])
        elif kind == "reach" and state.get("estimates"):
            est = state.get("estimates") or {}
            d.add_paragraph(f"Estimated population {int(est.get('pop') or 0):,} · addressable audience "
                            f"{int(est.get('aud') or 0):,} · households {int(est.get('hh') or 0):,} · "
                            f"devices {int(est.get('dev') or 0):,}")
        elif kind == "growth":
            growth = growth_options(state)
            if not growth["any"]:
                d.add_paragraph("Every product the discovery answers pointed at "
                                "is already on the plan above.")
            else:
                for row in growth["increases"]:
                    d.add_paragraph(
                        f"{row['product']}: quoted {_money(row['current'])}, "
                        f"recommended {_money(row['suggested'])} "
                        f"(+{_money(row['uplift'])}/mo, "
                        f"{_money(row['campaign'])} over {growth['months']} months)")
                for row in growth["additions"]:
                    price = (f"from {_money(row['suggested'])}/mo"
                             if row["quoted"] else "quoted on request")
                    d.add_paragraph(f"{row['product']} — {row['why']} ({price})")
        elif kind == "zips":
            wrote = False
            for area in campaign_areas(state):
                codes = hub_areas.zip_list(area)
                if codes:
                    d.add_paragraph(f"{hub_areas.label(area)} — {', '.join(codes)}")
                    wrote = True
            if not wrote:
                d.add_paragraph("No ZIP Codes were captured for this campaign. "
                                "The insertion order needs them before trafficking.")

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
# Sections the AI may write copy for. The table-backed ones take an intro
# paragraph above their generated table; only the cover has nothing to say.
WRITABLE_KINDS = ("text", "friction", "areas", "reach", "channels", "mediaplan",
                  "creative", "packages", "kpis", "roi", "timeline", "growth")


def writable_sections(state):
    return [sec for sec in (state or {}).get("sections") or []
            if sec.get("kind") in WRITABLE_KINDS and sec.get("enabled", True)]


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


# =====================================================================
# Expected Results & ROI — computed, never written
# =====================================================================
def expected_results(state):
    """What the money actually buys, product by product, from the rate card.

    Directive: every proposal ends with this section. It is calculated here
    rather than asked of the model, because a projection written next to a
    media plan it contradicts is worse than no projection — and a model given
    a budget will produce impression counts that look authoritative and are
    invented.

    Anything without a CPM or CPV — a management fee, a flat monthly, a custom
    quote — reports no units at all rather than a plausible number. That is
    most of the value of doing it this way.
    """
    state = state or {}
    months = max(1, int(state.get("months") or 1))
    rows, totals = [], {"impressions": 0, "views": 0, "monthly": 0.0}
    unpriced = []

    for item in state.get("items") or []:
        try:
            monthly = float(item.get("dollars") or 0)
        except (TypeError, ValueError):
            monthly = 0.0
        # Monthly or one-time, and for how long. A line quoted at $3,000 that
        # is actually a one-off is $18,000 over a six-month flight if nobody
        # asks -- so the wizard asks, and the totals here read the answer
        # rather than assuming every line runs the whole campaign.
        basis = str(item.get("basis") or "monthly")
        try:
            term = int(item.get("termMonths") or months)
        except (TypeError, ValueError):
            term = months
        term = max(1, min(months, term))
        if basis == "one_time":
            line_campaign = round(monthly, 2)
            # A one-time cost still has to sit in a monthly plan somewhere, so
            # it is spread across the flight. Never dropped (the plan
            # under-reports what is owed) and never charged monthly (every
            # month of the campaign is overstated by the same amount).
            line_monthly = round(monthly / months, 2)
        else:
            line_campaign = round(monthly * term, 2)
            line_monthly = monthly
        totals["monthly"] += line_monthly
        product = hub_rate_card.find(item.get("label") or item.get("product") or "")
        if product is None:
            # Fall back to the rate the wizard carried, so an off-card product
            # a rep added by hand is still estimated rather than dropped.
            product = {"rate_type": item.get("rate"), "rate_value": item.get("rateValue"),
                       "listed_rate": item.get("rate") or ""}
        delivery = hub_rate_card.estimate_delivery(product, monthly)
        units = delivery.get("units")
        run = 1 if basis == "one_time" else term
        if units and delivery["unit_label"].startswith("impressions"):
            totals["impressions"] += units * run
        elif units:
            totals["views"] += units * run
        if not units:
            unpriced.append(str(item.get("product") or ""))
        rows.append({
            "product": item.get("product") or "",
            "category": item.get("category") or "",
            "medium": hub_creative.medium_of(item),
            "monthly": line_monthly,
            "campaign": line_campaign,
            "basis": basis,
            "term_months": 1 if basis == "one_time" else term,
            "rate": product.get("listed_rate") or "",
            "units": units,
            "unit_label": delivery.get("unit_label") or "",
            "note": delivery.get("note") or "",
        })

    totals["campaign"] = round(sum(r["campaign"] for r in rows), 2)
    return {
        "rows": rows, "months": months, "totals": totals,
        # Only says anything when they run traditional media and a posture has
        # been chosen; otherwise "" and the section renders without it.
        "traditional_note": hub_discovery.roi_note(state),
        # Named, so the section can say which products are not represented in
        # the headline number instead of quietly under-reporting.
        "unpriced": unpriced,
        "metrics": _tracked_metrics(state),
    }


def _tracked_metrics(state):
    """What we will report on, drawn from the campaign's own KPIs and media."""
    metrics = list((state or {}).get("kpis") or [])
    media = {hub_creative.medium_of(i) for i in (state or {}).get("items") or []}
    if hub_creative.VIDEO in media:
        metrics += ["Completed video views", "Video completion rate"]
    if hub_creative.AUDIO in media:
        metrics += ["Audio listen-through rate"]
    if any(m in media for m in (hub_creative.DISPLAY, hub_creative.SOCIAL)):
        metrics += ["Click-through rate", "Cost per click"]
    metrics += ["Cost per lead", "Lead-to-close rate (from the Smart 1 Suite)"]
    seen, out = set(), []
    for m in metrics:
        key = str(m).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(str(m).strip())
    return out[:10]


# =====================================================================
# Proposal sections — the Smart 1 13-part structure
# =====================================================================
def ensure_sections(state):
    """The proposal's sections, seeded from the Smart 1 specification.

    `hub.proposal_spec.OUTLINE` owns the structure; this fills the prose the
    rep can then edit or have the AI write. A quote saved against the old
    eight-section layout keeps its sections — its copy is real work — but
    gains any *required* section it is missing, because a proposal without
    Expected Results & ROI is one the specification does not permit.
    """
    existing = state.get("sections")
    if existing:
        have = {str(sec.get("id")) for sec in existing if isinstance(sec, dict)}
        seeded = _seeded_sections(state)
        for spec in hub_spec.OUTLINE:
            if spec["id"] in hub_spec.REQUIRED and spec["id"] not in have:
                addition = next(s for s in seeded if s["id"] == spec["id"])
                existing.append(addition)
        return state
    state["sections"] = _seeded_sections(state)
    return state


def _seeded_sections(state):
    client = state.get("client") or "the client"
    goals = ", ".join(state.get("objectives") or []) or "the campaign goals"
    areas = campaign_areas(state)
    where = hub_areas.summary(areas, limit=3) or "the target area"
    template = industry_template(state)
    months = max(1, int(state.get("months") or 1))
    segments = hub_spec.audience_segments_for(state.get("industry", ""))

    sections = hub_spec.default_sections()
    body = {}

    body["summary"] = (
        f"{client} has the demand; what is missing is a single system that captures it "
        f"and proves what it produced. This plan puts Smart 1 media in front of the "
        f"right households in {where} and routes every response into the Smart 1 Suite, "
        f"where it is answered, nurtured and measured. Over {months} months the goal is "
        f"{goals.lower()} — reported against business outcomes, not impressions.")
    if template:
        body["summary"] = template["intro"] + "\n\n" + body["summary"]

    body["objectives"] = (
        "Primary\n" + "\n".join(f"• {o}" for o in (state.get("objectives") or
                                                    ["To be confirmed with the client"]))
        + "\n\nSecondary\n"
        "• A lower, measurable cost per acquisition\n"
        "• One vendor and one dashboard instead of a stack of logins\n"
        "• Reporting the client can open themselves, at any time")

    body["friction"] = (
        "Most of the money lost in local marketing is lost after the click, not before "
        "it. The patterns worth checking here:\n"
        "• A bolted-on tech stack — separate tools for email, forms, reviews and calls, "
        "each with its own login and none of them talking to each other.\n"
        "• No central CRM, so an inbound lead cools while it waits for someone to "
        "notice it.\n"
        "• Fixed-schedule advertising that runs the same way regardless of weather, "
        "foot traffic or real-time intent.")

    if segments:
        body["areas"] = (
            "Targeting is built area by area from named third-party segments rather "
            "than broad demographics:\n"
            + "\n".join(f"• {seg}" for seg in segments))

    if template and template.get("channels"):
        body["channels"] = (
            "Each channel below has a job in the customer journey:\n"
            + "\n".join(f"• {ch}" for ch in template["channels"]))

    if template and template.get("triggers"):
        body["channels"] = (body.get("channels", "") +
                            "\n\nBudget is concentrated on the moments that produce "
                            "revenue: " + ", ".join(template["triggers"]) + ".").strip()

    body["mediaplan"] = (
        "The split below is weighted toward the stage of the funnel this campaign has "
        "to move. Every rate is the Smart 1 card rate — there is no markup between the "
        "line item and what runs.")

    body["creative"] = _creative_section_body(state)

    body["technology"] = (
        "The Smart 1 Suite is the central nervous system of this campaign. Every call, "
        "form, chat and message the media generates lands in one inbox, and the Suite "
        "goes to work immediately: Missed Call Text Back so a missed call becomes a "
        "conversation instead of a lost lead, automated text and email follow-up, "
        "online scheduling, and automated review requests that compound into local "
        "search visibility. The media creates the opportunity; the Suite is what turns "
        "it into revenue you can trace.")

    body["reporting"] = (
        "Optimisation is a routine, not a promise: bids adjusted against delivery and "
        "cost per action, negative keywords and placement exclusions updated, creative "
        "checked against performance and rotated before it fatigues. Everything reports "
        "into one live dashboard inside the Smart 1 Suite that you can open whenever "
        "you want, rather than waiting for a monthly PDF.")

    body["packages"] = (
        "Platform licensing, media spend and any one-time production are listed "
        "separately below so it is clear what recurs and what does not.")

    body["roi"] = (
        "The delivery figures below are calculated from the Smart 1 rate card at the "
        "budgets in this plan — they are what the money buys, not a forecast. The "
        "Smart 1 Suite is the single source of truth for what those impressions "
        "produced: every lead is attributed to the channel that created it, so the "
        "spend can be judged against the business, not against a click count.")

    body["next"] = "\n".join(f"{i}. {step}" for i, step in
                              enumerate(hub_spec.NEXT_STEPS, 1))

    for section in sections:
        if section["id"] in body and body[section["id"]]:
            section["body"] = body[section["id"]]
    return sections


def _creative_section_body(state):
    """Messaging themes plus what the creative gate actually decided."""
    lines = [
        "Three messaging themes carry the campaign, rotated so no one execution "
        "fatigues:",
        "• Problem / Agitate / Solution — name the frustration the customer already "
        "has, make it concrete, then resolve it.",
        "• Proof — the specific, verifiable reason to choose this business over the "
        "one down the road.",
        "• Urgency without gimmick — the real reason now is better than later "
        "(season, capacity, availability), never a manufactured countdown.",
    ]
    summary = hub_creative.summary_line(state)
    if summary:
        lines += ["", "Creative source: " + summary + "."]
    return "\n".join(lines)


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
    # The standing directives apply to a rewrite exactly as they do to a first
    # draft. They were not applied here at all, so "make it punchier" could
    # walk copy straight past the Smart 1 Labs exclusion or invent a statistic.
    guidance = hub_spec.guidance_for(str(body.get("section") or ""))
    prompt = (hub_spec.system_prompt(body.get("data") or {}) + "\n\n"
              "Rewrite the proposal section below. Instruction: " + instruction + ".\n"
              + (f"What this section is for: {guidance}\n" if guidance else "")
              + "Return only the rewritten text, no preamble.\n\n" + text)
    try:
        rewritten = _openai_response(prompt, 2500)
    except Exception as exc:                            # noqa: BLE001
        return jsonify({"ok": False, "error": "AI rewrite failed", "detail": str(exc)}), 502
    problems = hub_spec.violations(rewritten)
    if problems:
        # Returned unchanged rather than silently handing back copy that
        # breaks a rule: the rep asked for a rewrite, not for a rule waiver.
        return jsonify({"ok": True, "text": text, "warnings": problems
                        + ["The rewrite was discarded and your text kept."]})
    return jsonify({"ok": True, "text": rewritten, "warnings": []})


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

    # One section per request when the caller names one. The wizard writes the
    # proposal a section at a time so its loader can say which section is
    # being written rather than spinning on "generating…" for a minute, and so
    # a single failed section does not cost the other twelve.
    only = str(body.get("section") or "").strip()
    writable = [sec for sec in state.get("sections") or []
                if sec.get("kind") in WRITABLE_KINDS and sec.get("enabled", True)
                and (not only or sec.get("id") == only)]
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
        "creative_source": hub_creative.summary_line(state),
        # What they already run, what they don't, and where they stand on
        # their traditional media. `traditional_guidance` carries the posture
        # AND the instruction not to argue it -- a model handed "they want to
        # shift budget to digital" writes a case against radio, and a proposal
        # that opens by calling a client's existing spend wasted loses the
        # room before the media plan is read.
        "current_marketing": hub_discovery.for_prompt(state),
        "traditional_guidance": hub_discovery.guidance(state),
        "we_suggest": [s["title"] for s in hub_discovery.suggestions(state)],
        "suite_tier": hub_spec.suggested_tier(
            (state.get("selectedPackage") or {}).get("monthly")
            or state.get("budget") or 0)["name"],
        "sections_to_write": [{"id": sec.get("id"), "title": sec.get("title"),
                               "guidance": hub_spec.guidance_for(sec.get("id")),
                               "has_table": sec.get("kind") not in ("text", "cover"),
                               "current": sec.get("body") or ""} for sec in writable],
    }
    if template:
        facts["industry_voice"] = template["intro"]
        facts["demand_triggers"] = template["triggers"]
        facts["channels_we_sell_here"] = template["channels"]

    prompt = (
        # The standing directives, the audience segments and the operating
        # facts that apply to this campaign all come from hub.proposal_spec,
        # so the rules land in one place rather than in a prompt literal that
        # drifts every time someone edits this route.
        hub_spec.system_prompt(state) + "\n\n"
        "Write the narrative copy for the client-facing proposal described below.\n\n"
        "Output rules:\n"
        "- Return STRICT JSON only: {\"sections\": [{\"id\": \"...\", \"body\": \"...\"}]}, one entry per "
        "id in sections_to_write, and no other keys.\n"
        "- Each section answers the `guidance` given for it. Follow that guidance.\n"
        "- A section marked has_table:true is followed by a generated table of "
        "real numbers. Write the short paragraph that INTRODUCES it — say what "
        "the reader is about to look at and why it is shaped this way. Never "
        "restate the figures; they are directly below your copy and any number "
        "you write that differs from one of theirs is the error that matters.\n"
        "- Each section is 2 to 4 short paragraphs, plain professional English. No "
        "headings; keep bullet characters only where the current copy uses them.\n"
        "- Keep anything factual the current copy already states.\n\n"
        + json.dumps(facts, ensure_ascii=False))
    try:
        result = _json_from_ai(_openai_response(prompt, 2000 if only else 6000))
        written = {str(sec.get("id")): str(sec.get("body") or "")
                   for sec in (result.get("sections") or []) if sec.get("id")}
    except Exception as exc:                            # noqa: BLE001
        logger.exception("AI section draft failed")
        return jsonify({"ok": False, "error": "AI draft failed", "detail": str(exc)}), 502
    if not written:
        return jsonify({"ok": False, "error": "The AI returned no sections."}), 502

    # A directive is a rule, not a request. Copy that breaks one is dropped
    # rather than shown to a rep who would have to notice it -- the Smart 1
    # Labs exclusion is the whole reason this check exists.
    breaches = {}
    for sec_id, text in list(written.items()):
        problems = hub_spec.violations(text)
        if problems:
            breaches[sec_id] = problems
            del written[sec_id]
    if not written:
        return jsonify({"ok": False, "error": "Every section the AI returned broke a "
                                              "proposal rule and was discarded.",
                        "breaches": breaches}), 502
    return jsonify({"ok": True, "sections": written, "breaches": breaches})


# =====================================================================
# Campaign intelligence — served here, not fetched from the IO app
# =====================================================================
@app.post("/api/creative-check")
def api_creative_check():
    """What the creative gate still needs to be told about this media plan.

    Server-side as well as in the wizard, because the answer travels onto the
    insertion order and into the PDF -- and because the wizard's classifier is
    a mirror of `hub.creative_needs`, not the authority on it.
    """
    body = request.get_json(force=True) or {}
    state = body.get("data") or body
    result = hub_creative.evaluate(state)
    result["comp_confirm_under"] = hub_creative.COMP_CONFIRM_UNDER
    result["typical_production"] = hub_creative.TYPICAL_PRODUCTION
    result["summary"] = hub_creative.summary_line(state)
    return jsonify({"ok": True, **result})


@app.get("/api/proposal-spec")
def api_proposal_spec():
    """The Smart 1 proposal specification the wizard builds against."""
    return jsonify({
        "ok": True,
        "outline": [{"id": s["id"], "title": s["title"], "kind": s["kind"],
                     "purpose": s["purpose"], "required": s["id"] in hub_spec.REQUIRED}
                    for s in hub_spec.OUTLINE],
        "saas_tiers": hub_spec.SAAS_TIERS,
        "timeline": hub_spec.TIMELINE,
        "next_steps": hub_spec.NEXT_STEPS,
        "comp_confirm_under": hub_creative.COMP_CONFIRM_UNDER,
        "typical_production": hub_creative.TYPICAL_PRODUCTION,
        # The joint minimum rule, so the wizard blocks exactly what the IO
        # blocks rather than carrying a second opinion about the same number.
        "minimums": hub_rate_card.minimums_for_js(),
    })


@app.post("/api/ai/section-plan")
def api_section_plan():
    """The sections the wizard should ask for, in order, with their labels.

    Returned rather than derived in JavaScript so the loader counts exactly
    what the server will write -- a progress bar that says "3 of 9" while the
    server writes 13 is worse than no progress bar.
    """
    body = request.get_json(force=True) or {}
    state = body.get("data") or {}
    ensure_sections(state)
    return jsonify({"ok": True, "sections": [
        {"id": sec.get("id"), "title": sec.get("title"),
         "has_table": sec.get("kind") not in ("text", "cover"),
         "has_copy": bool((sec.get("body") or "").strip())}
        for sec in writable_sections(state)]})


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
