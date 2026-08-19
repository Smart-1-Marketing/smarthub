"""Smart 1 Hub — Scans module.

Runs Insites website audits, stores the full result in the Hub database, and
lists them in a searchable table. This is the data foundation the report
generators, Leads, Client 360 scan button, and the embed widget all build on.

Design choices that matter:

* **Callback-first.** When we start an audit we hand Insites an ``on_completion``
  URL pointing back at ``/scans/api/callback``. Insites POSTs the finished audit
  to us; we store it. There is no polling loop that can hang — the exact failure
  mode that plagued the old standalone funnel is structurally impossible here.
  A manual "refresh" fetch is still available as a fallback.

* **SQLite locally, Postgres on Render** — same dual-mode pattern as
  sales_builder, so this is testable offline and persists in production via
  ``DATABASE_URL``.

* **Auth** is the Hub's shared login (the wsgi AuthGuard runs in front of this
  mount); the one open exception is the callback endpoint, which Insites calls
  server-to-server and authenticates with a shared-secret token instead.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, request, render_template, Response
from sqlalchemy import (Column, DateTime, Integer, String, Text, create_engine,
                        func, or_)
from sqlalchemy.orm import declarative_base, sessionmaker

from . import audit_fields, linkcheck, site_health
from .insites_client import InsitesError, is_configured

try:                                   # Hub activity log (present in the Hub)
    from hub import audit as hub_audit
except Exception:                      # noqa: BLE001 - standalone/dev fallback
    hub_audit = None

BASE_DIR = Path(__file__).parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))

# A shared secret so Insites' server-to-server callback can't be forged by a
# random POST. The callback is the ONE endpoint outside the Hub login, so it
# fails CLOSED: with no token configured every callback is refused rather than
# accepting anonymous writes. render.yaml generates the value automatically.
CALLBACK_TOKEN = (os.environ.get("SCANS_CALLBACK_TOKEN") or "").strip()

# The externally-reachable base URL of the Hub, so we can tell Insites where to
# POST the finished audit. On Render set PUBLIC_BASE_URL to the service URL.
PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")


def config_warnings() -> list[str]:
    """Shown on the Scans page so a half-configured deploy explains itself
    instead of silently leaving every scan stuck on 'running'."""
    out = []
    if not PUBLIC_BASE_URL:
        out.append(
            "PUBLIC_BASE_URL is not set, so Insites has nowhere to deliver "
            "finished audits. Scans will keep checking themselves from this "
            "page, but set it to this service's public URL and redeploy so "
            "results arrive on their own.")
    elif not CALLBACK_TOKEN:
        out.append(
            "SCANS_CALLBACK_TOKEN is not set, so the completion callback is "
            "refused for safety. Scans still finish via the status check on "
            "this page. Set the token (any long random string) and redeploy.")
    return out


# =====================================================================
# Database (SQLite locally, Postgres on Render via DATABASE_URL)
# =====================================================================
_db_url = os.getenv("DATABASE_URL", "").strip()
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
if not _db_url:
    _db_url = "sqlite:///" + str(BASE_DIR / "smart1_scans.db")
engine = create_engine(
    _db_url, future=True, pool_pre_ping=True,
    connect_args={"check_same_thread": False} if _db_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
Base = declarative_base()


class Scan(Base):
    __tablename__ = "scans"
    id = Column(Integer, primary_key=True, autoincrement=True)
    # our own short id used in URLs (stable, unlike Insites' long hash)
    public_id = Column(String(40), unique=True, nullable=False, index=True)
    # bare hostname, lowercased, www-stripped -> the dedupe/duplicate key
    domain_key = Column(String(255), index=True, nullable=False)
    input_url = Column(String(600), default="")
    business_name = Column(String(300), default="")
    # Insites' own report id / hash (needed to re-fetch or add competitors)
    insites_report_id = Column(String(80), index=True, default="")
    status = Column(String(20), default="running", index=True)  # running|complete|error
    overall_score = Column(Integer, nullable=True)
    tier = Column(String(20), default="")
    detected_name = Column(String(300), default="")
    detected_phone = Column(String(80), default="")
    detected_address = Column(String(500), default="")
    primary_industry = Column(String(200), default="")
    analysis_country = Column(String(8), default="")
    pages_analysed = Column(Integer, nullable=True)
    # who/what kicked it off: "hub" (staff) or "widget" (embed) etc.
    source = Column(String(30), default="hub", index=True)
    source_url = Column(String(600), default="")     # for widget: the host page
    requested_by = Column(String(160), default="")    # staff name or lead email
    error_message = Column(String(600), default="")
    # the entire raw Insites audit payload, as JSON text
    raw_report = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    completed_at = Column(DateTime, nullable=True)


class LinkCheck(Base):
    """One run of the on-demand broken-link crawl for a scan.

    Kept in its own table rather than as columns on Scan so it can be re-run
    without touching the audit, and so an existing deployment picks it up
    from create_all() without a column migration.
    """
    __tablename__ = "scan_link_checks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    scan_public_id = Column(String(40), index=True, nullable=False)
    domain_key = Column(String(255), index=True, default="")
    status = Column(String(20), default="running", index=True)  # running|complete|error
    broken_count = Column(Integer, nullable=True)
    pages_checked = Column(Integer, nullable=True)
    links_checked = Column(Integer, nullable=True)
    truncated = Column(Integer, default=0)
    error_message = Column(String(600), default="")
    result_json = Column(Text, nullable=True)
    requested_by = Column(String(160), default="")
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime, nullable=True)


# A database that isn't up yet must not take the module offline for the life
# of the worker \u2014 capture the problem and let every request explain it, the
# same way the Sites admin does.
DB_BOOT_ERROR = ""
try:
    Base.metadata.create_all(engine)
except Exception as _db_exc:  # noqa: BLE001
    DB_BOOT_ERROR = f"{type(_db_exc).__name__}: {_db_exc}"

# Serialises the duplicate-check-then-insert in api_new_scan so a double-click
# can't spend two Insites credits on the same domain.
_LOCK = threading.Lock()


# ------------------------------------------------------------------ helpers
def _now():
    return datetime.now(timezone.utc)


def _iso(dt) -> str | None:
    """UTC timestamps rendered with an explicit offset.

    The column is naive, so a bare .isoformat() reads as *local* time in the
    browser and shows scans hours in the future. Stamp the zone we stored in.
    """
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _text(value, limit: int) -> str:
    """Coerce anything the API (or a caller) hands us to a trimmed string.

    Insites returning a number where we expect a name used to raise
    TypeError mid-callback and strand the scan on 'running' forever.
    """
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()[:limit]


def actor_name() -> str:
    return request.environ.get("s1hub.user") or "Unknown"


def domain_key(url_or_host: str) -> str:
    """Normalise any URL/host to a bare dedupe key: lowercased, no scheme,
    no leading www., no path, no user:pass@ prefix."""
    s = (url_or_host or "").strip().lower()
    if "//" not in s:
        s = "//" + s
    host = urlparse(s if "://" in s else "http:" + s).netloc or s.strip("/")
    host = host.split("/")[0]
    if "@" in host:                       # strip userinfo (user:pass@host)
        host = host.rsplit("@", 1)[1]
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def looks_like_domain(value: str) -> bool:
    """Guard the credit spend: reject email addresses and other non-hosts
    before we pay Insites to audit them."""
    raw = (value or "").strip()
    if "@" in raw and "//" not in raw:    # a bare email, not a URL
        return False
    key = domain_key(raw)
    if not key or "." not in key or key.startswith(".") or key.endswith("."):
        return False
    if " " in key or "_" in key.split(".")[-1]:
        return False
    return len(key.rsplit(".", 1)[-1]) >= 2


def _new_public_id() -> str:
    return "scan_" + secrets.token_hex(6)


def _log(event: str, **extra):
    """Write to the Hub-wide activity log.

    hub.audit exposes log(module, type_, actor=..., **extra) \u2014 an earlier
    call to a non-existent record() meant scans never appeared on /activity.
    """
    if hub_audit is not None:
        try:
            hub_audit.log("scans", event, actor=actor_name(), **extra)
        except Exception:  # noqa: BLE001 - never let logging break a request
            pass


def scan_to_row(s: Scan) -> dict:
    """Compact dict for the table / API listing."""
    return {
        "public_id": s.public_id,
        "domain": s.domain_key,
        "input_url": s.input_url,
        "business_name": s.business_name or s.detected_name or "",
        "status": s.status,
        "score": s.overall_score,
        "tier": s.tier,
        "source": s.source,
        "requested_by": s.requested_by,
        "error_message": s.error_message or "",
        "created_at": _iso(s.created_at),
        "completed_at": _iso(s.completed_at),
    }


def looks_like_report(report) -> bool:
    """Does this payload actually resemble an Insites audit?

    The callback is server-to-server, so a malformed or unrelated POST must
    not be written over a real scan and marked complete.
    """
    if not isinstance(report, dict) or not report:
        return False
    summ = audit_fields.summarise(report)
    if summ["overall_score"] is not None or summ["report_id"]:
        return True
    # no score yet, but recognisably an audit body
    known = ("meta", "domain", "checkpoints", "overall_score", "sections",
             "local_presence", "amount_of_content", "analysed_page_count")
    return any(k in report for k in known)


def _apply_report(s: Scan, report: dict):
    """Fill a Scan row from a completed raw audit payload.

    Every value is coerced through _text: the API occasionally returns a
    number or an object where a string is expected, and slicing that raised
    TypeError mid-callback, leaving the scan stranded on 'running'.
    """
    summ = audit_fields.summarise(report)
    s.status = "complete"
    s.overall_score = summ["overall_score"]
    s.tier = audit_fields.tier_for_score(summ["overall_score"])
    s.detected_name = _text(summ["detected_name"], 300)
    s.detected_phone = _text(summ["detected_phone"], 80)
    s.detected_address = _text(summ["detected_address"], 500)
    s.primary_industry = _text(summ["primary_industry"], 200)
    s.analysis_country = _text(summ["analysis_country"], 8)
    try:
        s.pages_analysed = int(summ["pages_analysed"]) if summ["pages_analysed"] else None
    except (TypeError, ValueError):
        s.pages_analysed = None
    if summ["report_id"]:
        s.insites_report_id = _text(summ["report_id"], 80)
    if not s.business_name and s.detected_name:
        s.business_name = s.detected_name
    s.error_message = ""
    try:
        s.raw_report = json.dumps(report)
    except (TypeError, ValueError):
        s.raw_report = json.dumps({"unserialisable": True})
    s.completed_at = _now()


# =====================================================================
# Pages
# =====================================================================
@app.before_request
def _db_guard():
    """A database that wasn't reachable at boot shouldn't produce tracebacks
    on every route — say so once, plainly."""
    if DB_BOOT_ERROR and request.path not in ("/health",):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Scans database not ready. " + DB_BOOT_ERROR}), 503
        return (
            "<html><body style='font-family:system-ui;padding:40px'>"
            "<h2 style='color:#1a2e58'>Scans database not ready</h2>"
            f"<p>{DB_BOOT_ERROR}</p>"
            "<p>Check <code>DATABASE_URL</code> and redeploy. "
            "<a href='/status'>System Status</a></p></body></html>", 503)
    return None


@app.route("/")
def index():
    return render_template("scans.html", configured=is_configured(),
                           warnings=config_warnings())


@app.route("/scan/<public_id>")
def scan_detail(public_id):
    """Single scan view. Phase 1: shows summary + raw data; Phase 2 adds the
    per-category report menu (SEO/AEO, Content & UX, Ads, Social, …)."""
    db = SessionLocal()
    try:
        s = db.query(Scan).filter(Scan.public_id == public_id).first()
        if not s:
            return "Scan not found.", 404
        raw = json.loads(s.raw_report) if s.raw_report else None
        health = site_health.summary(raw or {})
        last_check = (db.query(LinkCheck)
                        .filter(LinkCheck.scan_public_id == s.public_id)
                        .order_by(LinkCheck.id.desc()).first())
        return render_template("scan_detail.html", scan=scan_to_row(s),
                               fixes=health["fixes"], speed=health["speed"],
                               linkcheck_state=(last_check.status
                                                if last_check else ""),
                               detected={
                                   "name": s.detected_name, "phone": s.detected_phone,
                                   "address": s.detected_address,
                                   "industry": s.primary_industry,
                                   "country": s.analysis_country,
                                   "pages": s.pages_analysed,
                               },
                               insites_report_id=s.insites_report_id,
                               raw_json=json.dumps(raw, indent=2) if raw else "")
    finally:
        db.close()


@app.route("/health")
def health():
    return jsonify({"ok": True, "insites_configured": is_configured()})


# =====================================================================
# API
# =====================================================================
@app.route("/api/scans")
def api_list():
    """List/search scans. Query params: q (text), status, limit."""
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    try:
        limit = max(1, max(1, min(500, int(request.args.get("limit") or 100))))
    except (TypeError, ValueError):
        limit = 100

    db = SessionLocal()
    try:
        query = db.query(Scan)
        if status:
            query = query.filter(Scan.status == status)
        if q:
            like = f"%{q.lower()}%"
            query = query.filter(or_(
                func.lower(Scan.domain_key).like(like),
                func.lower(Scan.business_name).like(like),
                func.lower(Scan.detected_name).like(like),
                func.lower(Scan.input_url).like(like),
                func.lower(Scan.requested_by).like(like),
            ))
        rows = query.order_by(Scan.created_at.desc()).limit(limit).all()
        return jsonify({"scans": [scan_to_row(r) for r in rows]})
    finally:
        db.close()


@app.route("/api/check")
def api_check():
    """Duplicate check: is this domain already scanned? Returns the most
    recent existing scan (if any) so the UI can offer a link instead of
    re-running and spending a credit."""
    key = domain_key(request.args.get("url", ""))
    if not key:
        return jsonify({"exists": False})
    db = SessionLocal()
    try:
        existing = (db.query(Scan)
                    .filter(Scan.domain_key == key)
                    .order_by(Scan.created_at.desc())
                    .first())
        if not existing:
            return jsonify({"exists": False, "domain": key})
        return jsonify({"exists": True, "domain": key, "scan": scan_to_row(existing)})
    finally:
        db.close()


@app.route("/bulk")
def page_bulk():
    return render_template("bulk.html")


@app.route("/api/scans/reap-stuck", methods=["POST"])
def api_reap_stuck():
    """Resolve scans that can never complete.

    A row stuck on "running" is either still genuinely in progress, or it is
    unresolvable — no Insites report id was stored, so there is nothing to
    poll and no callback was registered. The second kind sits there looking
    like work in progress forever, which is worse than an error because
    nobody investigates it.

    Anything with a report id is re-pulled from Insites first; only rows with
    no id, older than the grace window, are marked errored.
    """
    from . import insites_client
    body = request.get_json(silent=True) or {}
    grace = max(5, min(1440, int(body.get("older_than_minutes") or 30)))
    cutoff = _now() - timedelta(minutes=grace)

    db = SessionLocal()
    resolved, errored, still_running = 0, 0, 0
    try:
        rows = db.query(Scan).filter(Scan.status == "running").all()
        for s_row in rows:
            created = s_row.created_at
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created and created > cutoff:
                still_running += 1
                continue
            if s_row.insites_report_id:
                try:
                    _try_immediate_fetch(db, s_row)
                    if s_row.status == "complete":
                        resolved += 1
                    else:
                        still_running += 1
                    continue
                except Exception:  # noqa: BLE001
                    pass
                still_running += 1
                continue
            s_row.status = "error"
            s_row.error_message = (
                "No Insites report id was recorded for this scan, so it could "
                "never resolve. This affected bulk scans started before the "
                "fix. Re-run it — no credit was charged twice.")
            errored += 1
        db.commit()
    finally:
        db.close()

    _log("reap_stuck", resolved=resolved, errored=errored,
         still_running=still_running)
    return jsonify({
        "resolved": resolved, "errored": errored,
        "still_running": still_running,
        "message": (f"{resolved} completed, {errored} marked unresolvable, "
                    f"{still_running} still genuinely running."),
    })


@app.route("/api/bulk/plan", methods=["POST"])
def api_bulk_plan():
    """Read-only. Works out what a bulk run would cost before anything spends."""
    from . import bulk
    b = request.get_json(silent=True) or {}
    try:
        return jsonify(bulk.plan(
            cap=max(1, min(500, int(b.get("cap") or bulk.DEFAULT_CAP))),
            fresh_days=max(0, min(365, int(b.get("fresh_days") or bulk.DEFAULT_FRESH_DAYS))),
            include_house=bool(b.get("include_house")),
            only_live=bool(b.get("only_live"))))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Could not build the plan ({type(exc).__name__})."}), 500


@app.route("/api/bulk/run", methods=["POST"])
def api_bulk_run():
    """Starts the scans in a reviewed plan. THIS SPENDS CREDITS."""
    from . import bulk
    b = request.get_json(silent=True) or {}
    payload, code = bulk.run(str(b.get("plan_id") or ""),
                             requested_by=actor_name(),
                             allow_over_quota=bool(b.get("allow_over_quota")))
    return jsonify(payload), code


@app.route("/api/scans", methods=["POST"])
def api_new_scan():
    """Start a new audit. Body: {url, business_name?, phone?, address?, ...}.

    Creates a 'running' Scan row immediately and asks Insites to call us back
    on completion. Returns the new scan's public_id so the UI can track it.
    """
    if not is_configured():
        return jsonify({"error": "Insites API key is not configured. Add "
                        "INSITES_API_KEY to the environment."}), 503

    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "A website URL is required."}), 400

    if not looks_like_domain(url):
        if "@" in url and "//" not in url:
            return jsonify({"error": "That looks like an email address, not a "
                                     "website. Enter the site you want audited."}), 422
        return jsonify({"error": "That doesn't look like a valid domain."}), 422
    key = domain_key(url)

    force = bool(body.get("force"))
    db = SessionLocal()
    try:
        # Check-then-insert under a lock: without it a double-click or two
        # staff submitting the same domain both pass the check and spend two
        # Insites credits on one audit.
        with _LOCK:
            if not force:
                existing = (db.query(Scan)
                            .filter(Scan.domain_key == key,
                                    Scan.status.in_(("running", "complete")))
                            .order_by(Scan.created_at.desc())
                            .first())
                if existing:
                    return jsonify({
                        "duplicate": True,
                        "message": f"{key} has already been scanned.",
                        "scan": scan_to_row(existing),
                    }), 200

            public_id = _new_public_id()
            s = Scan(
                public_id=public_id,
                domain_key=key,
                input_url=_text(url, 600),
                business_name=_text(body.get("business_name"), 300),
                source=_text(body.get("source"), 30) or "hub",
                source_url=_text(body.get("source_url"), 600),
                requested_by=_text(body.get("requested_by"), 160) or actor_name()[:160],
                status="running",
                created_at=_now(),
            )
            db.add(s)
            db.commit()

        # Build the callback URL Insites will POST to on completion.
        on_completion = None
        if PUBLIC_BASE_URL:
            tok = f"?token={CALLBACK_TOKEN}" if CALLBACK_TOKEN else ""
            on_completion = f"{PUBLIC_BASE_URL}/scans/api/callback/{public_id}{tok}"

        from . import insites_client
        try:
            resp = insites_client.start_audit(
                key,
                on_completion=on_completion,
                name=body.get("business_name", ""),
                phone=body.get("phone", ""),
                address=body.get("address", ""),
                city=body.get("city", ""),
                state=body.get("state", ""),
                zip_code=body.get("zip", ""),
                country_code=body.get("country_code", ""),
                products=body.get("products", ""),
                locations=body.get("locations", ""),
            )
        except InsitesError as exc:
            s.status = "error"
            s.error_message = str(exc)[:600]
            db.commit()
            code = exc.status if exc.status in (400, 422) else 502
            return jsonify({"error": str(exc), "scan": scan_to_row(s)}), code

        rid = resp.get("reportId") or resp.get("report_id")
        if rid:
            s.insites_report_id = _text(rid, 80)
        # 303 => recent results already exist; try an immediate fetch below.
        db.commit()
        _log("scan_started", detail=key, scan=s.public_id)

        # If we have no public callback URL (e.g. local dev), or Insites said
        # results already exist, attempt an immediate fetch so the row can
        # complete without a callback.
        if rid and (on_completion is None or resp.get("status") == "success"):
            _try_immediate_fetch(db, s)

        return jsonify({"ok": True, "scan": scan_to_row(s)}), 202
    finally:
        db.close()


def _try_immediate_fetch(db, s: Scan):
    """Best-effort synchronous fetch (used when no callback URL is available).
    Silently leaves the scan 'running' if it isn't ready yet."""
    if not s.insites_report_id:
        return
    from . import insites_client
    try:
        status, report = insites_client.fetch_report(s.insites_report_id)
        if status == "complete" and report:
            _apply_report(s, report)
            db.commit()
            _log("scan_completed", detail=s.domain_key, scan=s.public_id,
                 score=s.overall_score, via="immediate")
    except InsitesError:
        pass  # stays 'running'; a later refresh or callback will finish it


@app.route("/api/callback/<public_id>", methods=["POST"])
def api_callback(public_id):
    """Insites posts the finished audit here (server-to-server).

    This is the only endpoint outside the Hub login, so it is deliberately
    strict:

    * **Fails closed.** No token configured means no callback accepted —
      an open endpoint would let anyone overwrite a scan with junk.
    * **Constant-time token compare**, since the token rides in the query
      string and lands in proxy logs.
    * **Validates the payload.** A body that doesn't look like an audit is
      rejected without touching the stored scan.
    * **Never marks a scan errored.** A bad callback leaves the row running
      so the status check can still finish it — an audit we already paid for
      must not be written off because one POST arrived malformed.
    """
    if not CALLBACK_TOKEN:
        return jsonify({"error": "Callbacks are disabled: SCANS_CALLBACK_TOKEN "
                                 "is not configured."}), 503
    if not secrets.compare_digest(str(request.args.get("token") or ""), CALLBACK_TOKEN):
        return jsonify({"error": "Invalid callback token."}), 403

    body = request.get_json(silent=True)
    if body is None:                       # wrong/missing content-type
        try:
            body = json.loads(request.get_data(as_text=True) or "{}")
        except ValueError:
            body = {}
    report = body.get("report") if isinstance(body, dict) else None
    report = report or body                # some callbacks post it at top level

    db = SessionLocal()
    try:
        s = db.query(Scan).filter(Scan.public_id == public_id).first()
        if not s:
            return jsonify({"error": "Unknown scan."}), 404
        if not looks_like_report(report):
            # leave the row alone — 'running' is recoverable, 'error' is not
            return jsonify({"error": "Callback did not contain an audit report."}), 400
        _apply_report(s, report)
        db.commit()
        _log("scan_completed", detail=s.domain_key, scan=s.public_id,
             score=s.overall_score, via="callback")
        return jsonify({"ok": True})
    finally:
        db.close()


@app.route("/api/scans/<public_id>/refresh", methods=["POST"])
def api_refresh(public_id):
    """Manually re-fetch a still-running scan from Insites (callback fallback)."""
    db = SessionLocal()
    try:
        s = db.query(Scan).filter(Scan.public_id == public_id).first()
        if not s:
            return jsonify({"error": "Unknown scan."}), 404
        # A completed scan can still be re-pulled with ?force=1 — the way back
        # from a report that landed wrong.
        if s.status == "complete" and not request.args.get("force"):
            return jsonify({"ok": True, "status": s.status, "scan": scan_to_row(s)})
        if not s.insites_report_id:
            return jsonify({"error": "This scan has no Insites report id yet, so "
                                     "there is nothing to fetch. Re-run it."}), 400
        from . import insites_client
        try:
            status, report = insites_client.fetch_report(s.insites_report_id)
        except InsitesError as exc:
            return jsonify({"error": str(exc)}), 502
        if status == "complete" and report:
            _apply_report(s, report)
            db.commit()
            _log("scan_completed", detail=s.domain_key, scan=s.public_id,
                 score=s.overall_score, via="refresh")
        elif s.status == "error":
            # it's alive on Insites' side after all — put it back in play
            s.status = "running"
            s.error_message = ""
            db.commit()
        return jsonify({"ok": True, "status": s.status, "scan": scan_to_row(s)})
    finally:
        db.close()


@app.route("/api/scans/<public_id>")
def api_get(public_id):
    """Full scan detail incl. the raw report (for the report generators)."""
    db = SessionLocal()
    try:
        s = db.query(Scan).filter(Scan.public_id == public_id).first()
        if not s:
            return jsonify({"error": "Not found."}), 404
        row = scan_to_row(s)
        row["insites_report_id"] = s.insites_report_id
        row["raw_report"] = json.loads(s.raw_report) if s.raw_report else None
        row["detected"] = {
            "name": s.detected_name, "phone": s.detected_phone,
            "address": s.detected_address, "industry": s.primary_industry,
            "country": s.analysis_country, "pages": s.pages_analysed,
        }
        return jsonify(row)
    finally:
        db.close()


@app.route("/api/scans/<public_id>/download")
def api_download(public_id):
    """Download the raw audit JSON."""
    db = SessionLocal()
    try:
        s = db.query(Scan).filter(Scan.public_id == public_id).first()
        if not s or not s.raw_report:
            return jsonify({"error": "No report available."}), 404
        fname = f"{s.domain_key}-{s.public_id}.json"
        return Response(
            s.raw_report, mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
    finally:
        db.close()


# =====================================================================
# Site health — the fix list and the speed summary
# =====================================================================
def _report_of(s: Scan) -> dict:
    """The stored audit as a dict, or {} — never raises on bad JSON."""
    if not s.raw_report:
        return {}
    try:
        report = json.loads(s.raw_report)
    except (TypeError, ValueError):
        return {}
    return report if isinstance(report, dict) else {}


def _linkcheck_row(lc: "LinkCheck | None") -> dict:
    if lc is None:
        return {"status": "none"}
    out = {
        "status": lc.status,
        "broken_count": lc.broken_count,
        "pages_checked": lc.pages_checked,
        "links_checked": lc.links_checked,
        "truncated": bool(lc.truncated),
        "error": lc.error_message or "",
        "started_at": _iso(lc.started_at),
        "finished_at": _iso(lc.finished_at),
    }
    if lc.result_json:
        try:
            out["result"] = json.loads(lc.result_json)
        except (TypeError, ValueError):
            out["result"] = None
    return out


@app.route("/api/scans/<public_id>/health")
def api_health(public_id):
    """Broken links / images to fix, plus the speed summary and detail.

    One endpoint feeds the scan page, Client 360 and anything else that wants
    the actionable half of an audit without parsing 440 raw fields.
    """
    db = SessionLocal()
    try:
        s = db.query(Scan).filter(Scan.public_id == public_id).first()
        if not s:
            return jsonify({"error": "Not found."}), 404
        lc = (db.query(LinkCheck)
                .filter(LinkCheck.scan_public_id == s.public_id)
                .order_by(LinkCheck.id.desc()).first())
        health = site_health.summary(_report_of(s))
        return jsonify({
            "scan": scan_to_row(s),
            "fixes": health["fixes"],
            "speed": health["speed"],
            "linkcheck": _linkcheck_row(lc),
        })
    finally:
        db.close()


@app.route("/api/health/by-domain")
def api_health_by_domain():
    """Latest completed scan for a domain, summarised — for Client 360.

    Returns ``{"found": false}`` rather than a 404 when a client has never
    been scanned: the card wants to offer a scan, not show an error.
    """
    key = domain_key(request.args.get("domain", ""))
    if not key:
        return jsonify({"found": False, "error": "No domain given."})
    db = SessionLocal()
    try:
        s = (db.query(Scan)
               .filter(Scan.domain_key == key, Scan.status == "complete")
               .order_by(Scan.id.desc()).first())
        if not s:
            return jsonify({"found": False, "domain": key})
        lc = (db.query(LinkCheck)
                .filter(LinkCheck.scan_public_id == s.public_id)
                .order_by(LinkCheck.id.desc()).first())
        health = site_health.summary(_report_of(s))
        return jsonify({
            "found": True,
            "domain": key,
            "public_id": s.public_id,
            "url": f"/scans/scan/{s.public_id}",
            "score": s.overall_score,
            "tier": s.tier,
            "scanned_at": _iso(s.completed_at or s.created_at),
            "fixes": health["fixes"],
            "speed": health["speed"],
            "linkcheck": _linkcheck_row(lc),
        })
    finally:
        db.close()


def _run_linkcheck(check_id: int, domain: str, pages: list):
    """Background worker: crawl, then write the result back.

    Runs in a thread with its own session — the request that started it has
    already returned by the time this finishes.
    """
    result, error = None, ""
    try:
        result = linkcheck.run(domain, pages)
        error = result.get("error") or ""
    except Exception as exc:                   # noqa: BLE001 - never lose the row
        error = f"{type(exc).__name__}: {exc}"
    db = SessionLocal()
    try:
        lc = db.query(LinkCheck).filter(LinkCheck.id == check_id).first()
        if lc is None:
            return
        if error or result is None:
            lc.status = "error"
            lc.error_message = _text(error or "The check produced no result.", 600)
        else:
            lc.status = "complete"
            lc.broken_count = result.get("broken_count")
            lc.pages_checked = result.get("pages_checked")
            lc.links_checked = result.get("links_checked")
            lc.truncated = 1 if result.get("truncated") else 0
            try:
                lc.result_json = json.dumps(result)
            except (TypeError, ValueError):
                lc.result_json = None
        lc.finished_at = _now()
        db.commit()
        _log("linkcheck_finished", detail=domain, scan=lc.scan_public_id,
             broken=lc.broken_count, status=lc.status)
    except Exception:                          # noqa: BLE001
        db.rollback()
    finally:
        db.close()


@app.route("/api/scans/<public_id>/linkcheck", methods=["GET", "POST"])
def api_linkcheck(public_id):
    """GET the last broken-link crawl; POST to run a fresh one.

    The audit gives counts only, so this is the step that produces the
    addresses. It hits the customer's live site, so it never runs on its own
    — someone has to ask for it.
    """
    db = SessionLocal()
    try:
        s = db.query(Scan).filter(Scan.public_id == public_id).first()
        if not s:
            return jsonify({"error": "Unknown scan."}), 404
        lc = (db.query(LinkCheck)
                .filter(LinkCheck.scan_public_id == s.public_id)
                .order_by(LinkCheck.id.desc()).first())

        if request.method == "GET":
            return jsonify(_linkcheck_row(lc))

        # Don't start a second crawl over the top of one already walking the
        # site — that doubles the load on the customer's server for nothing.
        if lc is not None and lc.status == "running":
            # The column comes back naive from the database (same reason _iso
            # exists); subtracting it from an aware "now" raises.
            started = lc.started_at or _now()
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            age = (_now() - started).total_seconds()
            if age < linkcheck.DEADLINE * 2:
                return jsonify(_linkcheck_row(lc))
            lc.status = "error"
            lc.error_message = "The previous check stopped without finishing."
            lc.finished_at = _now()
            db.commit()

        pages = site_health.fixes(_report_of(s)).get("pages_discovered") or []
        pages = [str(p) for p in pages if p][:linkcheck.MAX_PAGES]
        row = LinkCheck(scan_public_id=s.public_id, domain_key=s.domain_key,
                        status="running", requested_by=actor_name())
        db.add(row)
        db.commit()
        _log("linkcheck_started", detail=s.domain_key, scan=s.public_id,
             pages=len(pages))
        threading.Thread(target=_run_linkcheck,
                         args=(row.id, s.domain_key, pages),
                         daemon=True).start()
        return jsonify(_linkcheck_row(row))
    finally:
        db.close()


@app.route("/api/scans/<public_id>/linkcheck.csv")
def api_linkcheck_csv(public_id):
    """The broken list as CSV — the form it gets handed over in."""
    db = SessionLocal()
    try:
        s = db.query(Scan).filter(Scan.public_id == public_id).first()
        if not s:
            return jsonify({"error": "Unknown scan."}), 404
        lc = (db.query(LinkCheck)
                .filter(LinkCheck.scan_public_id == s.public_id,
                        LinkCheck.status == "complete")
                .order_by(LinkCheck.id.desc()).first())
        if not lc or not lc.result_json:
            return jsonify({"error": "No completed check to export yet."}), 404
        try:
            result = json.loads(lc.result_json)
        except (TypeError, ValueError):
            return jsonify({"error": "Stored result is unreadable."}), 500
        fname = linkcheck.csv_filename(s.domain_key)
        return Response(
            linkcheck.to_csv(result), mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
    finally:
        db.close()
