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
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import (Flask, jsonify, make_response, redirect,
                   render_template, request, Response)
from sqlalchemy import Column, DateTime, Integer, String, Text, func, or_
from sqlalchemy.orm import declarative_base

from . import (audit_fields, insites_client, leads as widget_state,
               linkcheck, report_pdf, reports, site_health, widget)
from .insites_client import InsitesError, is_configured
from hub.extensions import create_all_metadata, session_factory, shared_engine
from hub.webargs import clamp_int

try:                                   # Hub activity log (present in the Hub)
    from hub import audit as hub_audit
except Exception:                      # noqa: BLE001 - standalone/dev fallback
    hub_audit = None

# v1.42.0 put every lead in one store with one webhook and one panel. The
# widget writes there like every other capture point; it does not keep a
# second lead book, a second webhook or a second panel.
try:
    from hub import leads as hub_leads
except Exception:                      # noqa: BLE001 - standalone/dev fallback
    hub_leads = None

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
# The engine comes from hub/extensions, which owns the URL, the pool and the
# DDL lock for every module. This file used to resolve DATABASE_URL and call
# create_engine() itself, which meant a second connection pool against the same
# Postgres as the rest of the Hub -- and, with no DATABASE_URL set, a SQLite
# file at modules/scans/smart1_scans.db, i.e. inside the container image, which
# Render discards on every deploy. Same two backends, one pool, and the
# fallback file now lands on the mounted disk.
engine = shared_engine()
SessionLocal = session_factory()
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

    @property
    def client_key(self) -> str:
        """The Hub-wide client key for the site this scan is about.

        Scans got the identity question right by accident: `domain_key` is
        already a domain, and a domain is exactly what the rest of the Hub
        wants to join on. This exposes it in the shared form so a scan can be
        matched to an ad proposal, an image gallery and an access request
        without every caller knowing that scans happen to key on hostname.
        """
        try:
            from hub.client_key import client_key as _key
            return _key(self.business_name or "", self.domain_key or "")
        except Exception:                                 # noqa: BLE001
            return f"d:{self.domain_key}" if self.domain_key else ""


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
# create_all_metadata takes the Postgres advisory lock, so the two gunicorn
# workers no longer race each other into a duplicate-key stack trace, and it
# reports rather than raises.
DB_BOOT_ERROR = ""
for _metadata in (Base.metadata,
                  # The widget's placements and run state ride the same engine,
                  # so a deploy that can reach the scans database can always
                  # serve the embed too.
                  widget_state.Base.metadata):
    _err = create_all_metadata(_metadata)
    if _err and not DB_BOOT_ERROR:
        DB_BOOT_ERROR = _err

# Columns added to the widget tables after they first shipped. `create_all()`
# creates missing tables and never adds a column to an existing one, so on the
# live Postgres these would be silently absent while every local test passed --
# the trap CLAUDE.md names at length. Asked-then-added rather than fired
# blindly, because the columns are declared on the models too: on a fresh
# database create_all() has already put them there, and firing unconditionally
# means two workers printing a Postgres ERROR per column on every deploy, which
# is how a log stops being one anybody finds the real error in. The inspector
# answers the same on SQLite and Postgres, which `ADD COLUMN IF NOT EXISTS`
# does not.
_LATE_WIDGET_COLUMNS = [
    ("scan_widgets", "kind", "VARCHAR(16)"),
    ("scan_widget_runs", "kind", "VARCHAR(16)"),
    ("scan_widget_runs", "intake_json", "TEXT"),
]


def _add_missing_columns() -> None:
    from sqlalchemy import inspect as _inspect, text as _text
    engine = shared_engine()
    inspector = _inspect(engine)
    for table, column, coltype in _LATE_WIDGET_COLUMNS:
        try:
            have = {c["name"] for c in inspector.get_columns(table)}
        except Exception:                               # noqa: BLE001
            continue                                    # no table: nothing to alter
        if column in have:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(_text(
                    f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))
        except Exception:                               # noqa: BLE001
            pass                                        # raced by the other worker


if not DB_BOOT_ERROR:
    try:
        _add_missing_columns()
    except Exception as _mc_exc:                        # noqa: BLE001
        DB_BOOT_ERROR = DB_BOOT_ERROR or f"{type(_mc_exc).__name__}: {_mc_exc}"

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
    no leading www., no path, no user:pass@ prefix.

    This was its own implementation — the fourth in the codebase doing the
    same job with slightly different edges. It kept the query string and
    accepted anything host-shaped, where hub/client_context wanted a real
    domain, so a scan's key and a client registry's key for one site could
    disagree and the two would never join. There is now one definition and
    this calls it. For any real domain the answer is unchanged, so stored
    keys still match; a value that is not a domain now returns "" instead of
    a fragment, which looks_like_domain() below already treats as a reject.
    """
    from hub.client_context import canonical_domain
    return canonical_domain(url_or_host)


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
                               reports_menu=(reports.available(raw or {})
                                             if s.status == "complete" else []),
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
        limit = clamp_int(request.args.get("limit"), 100, 1, 500)
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


@app.route("/api/scans/recover-errored", methods=["POST"])
def api_recover_errored():
    """Pull back scans that show as errored but finished on Insites.

    These are rows whose report id was never stored, so nothing local could
    ever resolve them. Asking Insites to audit the domain again returns the
    existing report (HTTP 303) whenever one is recent enough, which costs no
    credit — ``reused`` counts how many came back that way, and ``restarted``
    how many had no report left and began a genuine new audit.
    """
    from . import insites_client
    db = SessionLocal()
    reused = restarted = failed = 0
    errors = []
    try:
        rows = (db.query(Scan)
                .filter(Scan.status == "error",
                        or_(Scan.insites_report_id.is_(None),
                            Scan.insites_report_id == ""))
                .all())
        for s_row in rows:
            try:
                resp = insites_client.start_audit(s_row.domain_key,
                                                  name=s_row.business_name or "")
            except InsitesError as exc:
                failed += 1
                errors.append(f"{s_row.domain_key}: {exc}")
                continue
            rid = resp.get("reportId") or resp.get("report_id")
            if not rid:
                failed += 1
                errors.append(f"{s_row.domain_key}: no report id returned")
                continue
            s_row.insites_report_id = _text(rid, 80)
            s_row.status = "running"
            s_row.error_message = ""
            if resp.get("reused"):
                reused += 1
            else:
                restarted += 1
            db.commit()
            _try_immediate_fetch(db, s_row)
    finally:
        db.close()

    _log("scans_recovered", reused=reused, restarted=restarted, failed=failed)
    return jsonify({
        "reused": reused, "restarted": restarted, "failed": failed,
        "errors": errors[:5],
        "message": (f"{reused} pulled back from existing Insites reports (no "
                    f"credits spent), {restarted} re-audited, {failed} could "
                    f"not be recovered."),
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


def latest_payload_for_domain(url_or_host: str) -> dict:
    """The stored Insites payload for a domain's most recent complete scan.

    Used by Client 360 to pull social profiles a scan found. Returns {} rather
    than raising: a client with no scan is the normal case, not an error, and
    the caller only wants to add to what it already has.
    """
    key = domain_key(url_or_host or "")
    if not key:
        return {}
    try:
        db = SessionLocal()
    except Exception:                                   # noqa: BLE001
        return {}
    try:
        row = (db.query(Scan)
                 .filter(Scan.domain_key == key, Scan.status == "complete")
                 .order_by(Scan.id.desc())
                 .first())
        if not row or not row.raw_report:
            return {}
        import json as _json
        return _json.loads(row.raw_report) or {}
    except Exception:                                   # noqa: BLE001
        return {}
    finally:
        try:
            db.close()
        except Exception:                               # noqa: BLE001
            pass


def _callback_url(public_id: str):
    """Where Insites should POST the finished audit, or None if we have no
    public URL to be called back on (local dev)."""
    if not PUBLIC_BASE_URL:
        return None
    tok = f"?token={CALLBACK_TOKEN}" if CALLBACK_TOKEN else ""
    return f"{PUBLIC_BASE_URL}/scans/api/callback/{public_id}{tok}"


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

        on_completion = _callback_url(public_id)

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
        db.commit()
        _log("scan_started", detail=key, scan=s.public_id)

        # Fetch straight away when no callback is coming: either there's no
        # public URL to call back to (local dev), or Insites reused an existing
        # report — a reused report is already finished, so no callback will
        # ever fire and waiting for one leaves the row stuck.
        reused = bool(resp.get("reused")) or resp.get("status") in ("success", "existing")
        if rid and (on_completion is None or reused):
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
        from . import insites_client
        if not s.insites_report_id:
            # No id recorded — the state that left completed audits looking
            # errored. Re-asking Insites for this domain returns the existing
            # report (HTTP 303, no credit) when one exists, which recovers the
            # row; only if none exists does this start a real audit, so it is
            # behind an explicit ?recover=1 rather than the normal refresh.
            if not request.args.get("recover"):
                return jsonify({
                    "error": "This scan has no Insites report id, so there is "
                             "nothing to fetch. Use Recover to pull the "
                             "existing Insites report for this domain.",
                    "recoverable": True,
                }), 400
            try:
                resp = insites_client.start_audit(
                    s.domain_key, on_completion=_callback_url(s.public_id),
                    name=s.business_name or "")
            except InsitesError as exc:
                return jsonify({"error": f"Could not recover this scan: {exc}"}), 502
            rid = resp.get("reportId") or resp.get("report_id")
            if not rid:
                return jsonify({"error": "Insites gave no report id for that "
                                         "domain, so it can't be recovered."}), 502
            s.insites_report_id = _text(rid, 80)
            s.status = "running"
            s.error_message = ""
            db.commit()
            _log("scan_recovered", detail=s.domain_key, scan=s.public_id,
                 reused=bool(resp.get("reused")))
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


# =====================================================================
# Reports — the eight client-facing audits built from a stored scan
# =====================================================================

def _latest_complete(db, domain: str):
    """The newest completed scan for a domain, or None."""
    key = domain_key(domain or "")
    if not key:
        return None
    return (db.query(Scan)
              .filter(Scan.domain_key == key, Scan.status == "complete")
              .order_by(Scan.id.desc()).first())


def _built_report(s: Scan, key: str):
    """(report_dict, error_response) — one of the two is always None."""
    if key not in reports.REPORT_INDEX:
        return None, (jsonify({"error": "Unknown report."}), 404)
    if s.status != "complete":
        return None, (jsonify({"error": "That scan hasn't finished."}), 409)
    try:
        return reports.report(_report_of(s), key), None
    except Exception as exc:                    # noqa: BLE001
        return None, (jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500)


@app.route("/report/<public_id>/<key>")
def page_report(public_id, key):
    """One audit, on screen. ``key`` is a report key from reports.REPORTS."""
    db = SessionLocal()
    try:
        s = db.query(Scan).filter(Scan.public_id == public_id).first()
        if not s:
            return "Scan not found.", 404
        if key not in reports.REPORT_INDEX:
            return "Unknown report.", 404
        if s.status != "complete":
            return redirect(f"/scans/scan/{public_id}")
        r, err = _built_report(s, key)
        if err is not None:
            return err
        client = (request.args.get("client") or "").strip()
        # client= so the view lands on that client's 360 activity, per the
        # Hub convention for anything that produces client-facing work.
        _log("report_viewed", detail=s.domain_key, scan=s.public_id,
             report=key, client=client or None)
        return render_template("report.html", r=r, scan=scan_to_row(s),
                               menu=reports.available(_report_of(s)),
                               client=client)
    finally:
        db.close()


@app.route("/report/<public_id>/<key>.pdf")
def page_report_pdf(public_id, key):
    """The same audit as a branded PDF, for sending to the client."""
    db = SessionLocal()
    try:
        s = db.query(Scan).filter(Scan.public_id == public_id).first()
        if not s:
            return jsonify({"error": "Scan not found."}), 404
        r, err = _built_report(s, key)
        if err is not None:
            return err
        try:
            pdf = report_pdf.build(r)
        except Exception as exc:                # noqa: BLE001
            return jsonify({"error": f"PDF failed: {type(exc).__name__}: {exc}"}), 500
        _log("report_pdf", detail=s.domain_key, scan=s.public_id, report=key,
             client=(request.args.get("client") or "").strip() or None)
        return Response(pdf, mimetype="application/pdf", headers={
            "Content-Disposition":
                f'attachment; filename="{report_pdf.filename(r)}"'})
    finally:
        db.close()


@app.route("/report/by-domain/<key>")
def page_report_by_domain(key):
    """Deep link from a client record, which knows a domain and not a scan id.

    Redirects to the newest completed scan for that domain so the client pages
    never have to store scan ids of their own.
    """
    domain = request.args.get("domain", "")
    client = (request.args.get("client") or "").strip()
    db = SessionLocal()
    try:
        s = _latest_complete(db, domain)
        if not s:
            return redirect("/scans/?q=" + (domain_key(domain) or ""))
        suffix = ".pdf" if request.args.get("pdf") else ""
        tail = f"?client={client}" if client and not suffix else ""
        return redirect(f"/scans/report/{s.public_id}/{key}{suffix}{tail}")
    finally:
        db.close()


@app.route("/api/reports/<public_id>")
def api_reports(public_id):
    """The report menu for one scan, with live counts on each entry."""
    db = SessionLocal()
    try:
        s = db.query(Scan).filter(Scan.public_id == public_id).first()
        if not s:
            return jsonify({"error": "Not found."}), 404
        payload = _report_of(s) if s.status == "complete" else {}
        return jsonify({"public_id": s.public_id, "status": s.status,
                        "reports": reports.available(payload) if payload else []})
    finally:
        db.close()


@app.route("/api/reports/by-domain")
def api_reports_by_domain():
    """Everything a client page needs to draw the audit list for a domain.

    ``{"found": false}`` when the client has never been scanned — that's a
    normal state for a new client, not an error, and the card offers a scan.
    """
    domain = request.args.get("domain", "")
    key = domain_key(domain)
    if not key:
        return jsonify({"found": False, "error": "No domain given."})
    db = SessionLocal()
    try:
        s = _latest_complete(db, key)
        if not s:
            return jsonify({"found": False, "domain": key})
        payload = _report_of(s)
        lc = (db.query(LinkCheck)
                .filter(LinkCheck.scan_public_id == s.public_id)
                .order_by(LinkCheck.id.desc()).first())
        health = site_health.summary(payload)
        return jsonify({
            "found": True, "domain": key, "public_id": s.public_id,
            "scan_url": f"/scans/scan/{s.public_id}",
            "base_url": f"/scans/report/{s.public_id}/",
            "score": s.overall_score, "tier": s.tier,
            "business": s.detected_name or s.business_name or "",
            "scanned_at": _iso(s.completed_at or s.created_at),
            "reports": reports.available(payload),
            # The client pages show the fix list and run the link check from
            # here too, so this is deliberately the one call they need.
            "fixes": health["fixes"], "speed": health["speed"],
            "linkcheck": _linkcheck_row(lc),
        })
    finally:
        db.close()


@app.route("/api/reports/<public_id>/<key>.json")
def api_report_json(public_id, key):
    """The finished report as data — for anything that wants to re-render it."""
    db = SessionLocal()
    try:
        s = db.query(Scan).filter(Scan.public_id == public_id).first()
        if not s:
            return jsonify({"error": "Not found."}), 404
        r, err = _built_report(s, key)
        if err is not None:
            return err
        return jsonify(r)
    finally:
        db.close()


# =====================================================================
# Public scan widget — the embeddable lead driver
# =====================================================================
#
# The flow, and why it is in this order:
#
#   1. A visitor types a domain. We run our OWN pre-check (widget.precheck) —
#      robots.txt, schema, sitemap, titles, content. Free, about five seconds,
#      no Insites credit. They see a score and the three worst findings.
#   2. To see the rest they give name, business, email and phone. The lead
#      goes to hub.leads — the one store, the one webhook, the one panel —
#      tagged with the placement it came from.
#   3. ONLY THEN does a paid Insites audit start, so no anonymous visitor and
#      no bot can ever spend a credit. A domain already audited recently
#      reuses that audit and spends nothing at all.
#   4. The visitor sees the SEO & AEO report and its PDF through an
#      unguessable token. They never see the other seven audits; that data is
#      what the sales conversation is made of.

REUSE_DAYS = int(os.environ.get("SCANS_WIDGET_REUSE_DAYS", "30") or 30)

# The one report a converted lead is allowed to see.
PUBLIC_REPORT_KEY = "seo_aeo"

# Mount-relative prefixes that sit outside the Hub login. wsgi.py reads this
# tuple rather than repeating it, so the mount and the module cannot disagree.
PUBLIC_PREFIXES = ("/api/callback", "/w/", "/embed", "/api/w/", "/r/")


def client_ip() -> str:
    """The caller's address, taking the LAST X-Forwarded-For hop.

    Same rule and same reason as hub.leads.client_ip: the first hop is
    client-supplied, so trusting it lets one machine present as a new visitor
    on every request.
    """
    if hub_leads is not None:
        return hub_leads.client_ip(request)
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[-1].strip() if fwd else request.remote_addr or "")[:64]


# The free pre-check hits someone else's website, so it gets its own ceiling.
# Lead capture is limited by hub.leads.rate_check, which already governs every
# other public capture point in the Hub.
_PRECHECK_HITS: dict[str, list] = {}
_PRECHECK_LOCK = threading.Lock()
PRECHECK_LIMIT = int(os.environ.get("SCANS_WIDGET_PRECHECK_LIMIT") or 12)
PRECHECK_WINDOW = 600


def _precheck_allowed() -> bool:
    if PRECHECK_LIMIT <= 0:
        return True
    ip, now = client_ip(), _time.time()
    with _PRECHECK_LOCK:
        hits = [t for t in _PRECHECK_HITS.get(ip, [])
                if now - t < PRECHECK_WINDOW]
        if len(hits) >= PRECHECK_LIMIT:
            _PRECHECK_HITS[ip] = hits
            return False
        hits.append(now)
        _PRECHECK_HITS[ip] = hits
        if len(_PRECHECK_HITS) > 5000:
            for k in [k for k, v in _PRECHECK_HITS.items()
                      if not any(now - t < PRECHECK_WINDOW for t in v)][:2500]:
                _PRECHECK_HITS.pop(k, None)
    return True


def _widget_or_none(db, slug: str):
    return (db.query(widget_state.ScanWidget)
              .filter(widget_state.ScanWidget.slug == slug).first())


def _widget_config(db, slug: str):
    w = _widget_or_none(db, slug)
    if w is None or not w.active:
        return None
    return w.as_row(PUBLIC_BASE_URL)


def _widget_template(cfg: dict) -> str:
    """Which page a placement serves. Two kinds, two templates.

    Not one template with a branch in it: the two ask the visitor for
    different things in a different order, and a page that is half one form
    and half the other is how a required question ends up rendered but never
    read.
    """
    return ("widget_audit.html" if cfg.get("kind") == "audit"
            else "widget.html")


def _intake_questions(cfg: dict) -> list:
    """The questions an audit placement asks, straight from the one list.

    `hub/website_audit.py` owns them, so the customer form, the staff form and
    the proposal prefill cannot disagree about what was asked. An import that
    fails costs the extra questions and not the widget — the contact fields
    are what makes it a lead.
    """
    if cfg.get("kind") != "audit":
        return []
    try:
        from hub.website_audit import questions
        return questions("customer")
    except Exception:                                   # noqa: BLE001
        app.logger.exception("audit intake questions unavailable")
        return []


def _base_url() -> str:
    return PUBLIC_BASE_URL or request.url_root.rstrip("/")


def _run_by_token(db, token: str):
    return (db.query(widget_state.ScanRun)
              .filter(widget_state.ScanRun.token == (token or "")).first())


# ---------------------------------------------------------------- pages

@app.route("/w/<slug>")
def widget_page(slug):
    """Hosted standalone page — the link to run ads to."""
    db = SessionLocal()
    try:
        cfg = _widget_config(db, slug)
    finally:
        db.close()
    if cfg is None:
        return "This scan isn't available.", 404
    return render_template(_widget_template(cfg), w=cfg, embedded=False,
                           intake=_intake_questions(cfg),
                           source=request.args.get("src", ""))


@app.route("/embed/<slug>")
def widget_embed(slug):
    """Chrome-free version for an iframe on a client's site."""
    db = SessionLocal()
    try:
        cfg = _widget_config(db, slug)
    finally:
        db.close()
    if cfg is None:
        return "This scan isn't available.", 404
    resp = make_response(render_template(_widget_template(cfg), w=cfg,
                                         embedded=True,
                                         intake=_intake_questions(cfg),
                                         source=request.args.get("src", "")))
    # Framed on other people's domains by design, so the default SAMEORIGIN
    # rules have to be relaxed for this route and no other.
    resp.headers["Content-Security-Policy"] = "frame-ancestors *"
    resp.headers.pop("X-Frame-Options", None)
    return resp


@app.route("/embed.js")
def widget_embed_js():
    """Auto-resizes the iframe so the host page never shows a scrollbar."""
    js = """(function(){
  window.addEventListener('message', function(e){
    var d = e.data || {};
    if (!d || d.type !== 's1scan:height') return;
    var frames = document.querySelectorAll('iframe[data-s1scan]');
    for (var i = 0; i < frames.length; i++) {
      if (frames[i].contentWindow === e.source) {
        frames[i].style.height = d.height + 'px';
      }
    }
  });
})();"""
    return Response(js, mimetype="application/javascript",
                    headers={"Cache-Control": "public, max-age=3600"})


# ---------------------------------------------------------------- public API

@app.route("/api/w/<slug>/check", methods=["POST"])
def api_widget_check(slug):
    """Step 1 — the free pre-check. No Insites credit is spent here."""
    if DB_BOOT_ERROR:
        return jsonify({"ok": False, "error": "The scanner is temporarily "
                                              "unavailable. Try again shortly."}), 503
    if not _precheck_allowed():
        return jsonify({"ok": False, "error": "That's a lot of checks from one "
                                              "place. Give it a few minutes."}), 429

    body = request.get_json(silent=True) or {}
    db = SessionLocal()
    try:
        cfg = _widget_config(db, slug)
        if cfg is None:
            return jsonify({"ok": False, "error": "This scan isn't available."}), 404
        try:
            result = widget.precheck(body.get("domain") or "")
        except widget.PrecheckError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 422
        except Exception:                          # noqa: BLE001
            # Never hand a raw traceback to a stranger on a client's website.
            app.logger.exception("widget precheck failed")
            return jsonify({"ok": False, "error": "We couldn't read that site "
                                                  "just now. Try again in a "
                                                  "moment."}), 500

        row = widget_state.ScanRun(
            token=widget_state.new_token(),
            widget_slug=slug, tag=cfg["tag"], created_at=_now(),
            domain=result["domain"][:255], site_url=result["url"][:500],
            source=_text(body.get("source") or request.referrer or "", 300),
            ip=client_ip(),
            user_agent=_text(request.headers.get("User-Agent", ""), 300),
            precheck_score=result["score"],
            precheck_json=json.dumps(result),
            scan_status="not_started")
        db.add(row)
        db.commit()
        _log("widget_check", detail=result["domain"], widget=slug,
             score=result["score"])
        return jsonify({"ok": True, "token": row.token,
                        "result": widget.teaser(result)})
    finally:
        db.close()


def _recent_scan_for(db, key: str):
    """A completed audit for this domain young enough to reuse."""
    cutoff = _now().replace(tzinfo=None) - timedelta(days=REUSE_DAYS)
    return (db.query(Scan)
              .filter(Scan.domain_key == key, Scan.status == "complete",
                      Scan.created_at >= cutoff)
              .order_by(Scan.id.desc()).first())


def _start_widget_scan(db, row) -> None:
    """Start (or reuse) the paid audit for a converted lead.

    Never raises: the lead is already stored and already sent, and a failure
    to buy an audit must not turn a captured lead into an error page.
    """
    key = domain_key(row.domain or "")
    if not key:
        row.scan_status = "error"
        db.commit()
        return
    with _LOCK:
        existing = _recent_scan_for(db, key)
        if existing is not None:
            row.scan_public_id = existing.public_id
            row.scan_status = existing.status
            row.scan_reused = 1
            db.commit()
            _log("widget_scan_reused", detail=key, widget=row.widget_slug,
                 scan=existing.public_id)
            return
        if not is_configured():
            row.scan_status = "unconfigured"
            db.commit()
            return
        public_id = _new_public_id()
        s = Scan(public_id=public_id, domain_key=key,
                 input_url=_text(row.site_url or key, 600),
                 business_name=_text(row.company, 300),
                 source="widget", source_url=_text(row.source, 600),
                 requested_by=_text(row.email, 160),
                 status="running", created_at=_now())
        db.add(s)
        db.commit()
        row.scan_public_id = public_id
        row.scan_status = "running"
        db.commit()

    on_completion = None
    if PUBLIC_BASE_URL:
        tok = f"?token={CALLBACK_TOKEN}" if CALLBACK_TOKEN else ""
        on_completion = f"{PUBLIC_BASE_URL}/scans/api/callback/{public_id}{tok}"
    try:
        resp = insites_client.start_audit(
            key, on_completion=on_completion, name=row.company or "",
            phone=row.phone or "")
        rid = resp.get("reportId") or resp.get("report_id")
        if rid:
            s.insites_report_id = _text(rid, 80)
            db.commit()
        _log("scan_started", detail=key, scan=s.public_id, via="widget")
        if rid and (on_completion is None or resp.get("status") == "success"):
            _try_immediate_fetch(db, s)
            row.scan_status = s.status
            db.commit()
    except InsitesError as exc:
        s.status = "error"
        s.error_message = str(exc)[:600]
        row.scan_status = "error"
        db.commit()
    except Exception as exc:                       # noqa: BLE001
        app.logger.exception("widget scan start failed")
        s.status = "error"
        s.error_message = f"{type(exc).__name__}: {exc}"[:600]
        row.scan_status = "error"
        db.commit()


def _capture_lead(row, cfg) -> None:
    """Hand the lead to hub.leads — the one store, webhook and panel.

    Tagged with the placement so the panel answers "which scan did they run?",
    and carrying the report PDF link so the rep has it without leaving the CRM.
    """
    if hub_leads is None:
        return
    pdf_url = ""
    # Only the AI-visibility placement has a PDF. The audit report is a page,
    # and pointing a lead at `/r/<token>.pdf` for an audit run would hand
    # somebody the SEO & AEO document instead -- a different report under the
    # link that promises this one, which is worse than no link at all.
    if row.scan_public_id and widget_state.kind_of(getattr(row, "kind", "")) != "audit":
        pdf_url = f"{_base_url()}/scans/r/{row.token}.pdf"
    try:
        pre = json.loads(row.precheck_json or "{}")
    except (TypeError, ValueError):
        pre = {}
    issues = [f["label"] for f in pre.get("findings", [])
              if f.get("state") == "bad"][:5]
    fields = {
        "name": row.name, "email": row.email, "phone": row.phone,
        "company": row.company,
        "website": row.site_url or row.domain,
        "ai_visibility_score": row.precheck_score,
        "top_issues": "; ".join(issues),
        "source_page": row.source or "",
    }
    # What the business itself answered on an audit placement. Flat strings,
    # because hub.leads cleans and truncates every value and a nested one
    # would arrive in the Suite as the repr of a dict. Only what was actually
    # answered goes on: a key written empty reads in the CRM as a question
    # asked and answered "nothing".
    if widget_state.kind_of(getattr(row, "kind", "")) == "audit":
        fields.pop("ai_visibility_score", None)
        fields.pop("top_issues", None)
        try:
            for key, value in (json.loads(row.intake_json or "{}") or {}).items():
                if value:
                    fields[key] = value
        except (TypeError, ValueError):
            pass
    try:
        result = hub_leads.capture_and_deliver(
            source=("website_audit"
                    if widget_state.kind_of(getattr(row, "kind", "")) == "audit"
                    else "scan_widget"),
            page=row.tag or row.widget_slug or "scan",
            fields=fields,
            pdf_url=pdf_url,
            client=row.company or "",
            meta={"widget": row.widget_slug or "", "tag": row.tag or "",
                  "kind": widget_state.kind_of(getattr(row, "kind", "")),
                  "domain": row.domain or "",
                  "scan_public_id": row.scan_public_id or "",
                  "report_url": (f"{_base_url()}/scans/r/{row.token}"
                                 if row.scan_public_id else "")})
        row.lead_id = str(result.get("lead_id") or "")[:32]
    except Exception:                              # noqa: BLE001
        # hub.leads never raises by design; if it somehow does, the run row
        # still exists and the lead can be recovered from it.
        app.logger.exception("widget lead capture failed")


@app.route("/api/w/<slug>/unlock", methods=["POST"])
def api_widget_unlock(slug):
    """Step 2 — contact details in, full pre-check out, paid audit begins."""
    if DB_BOOT_ERROR:
        return jsonify({"ok": False, "error": "The scanner is temporarily "
                                              "unavailable."}), 503

    body = request.get_json(silent=True) or {}
    # Honeypot: a hidden field no human fills in. Answer 200 so a bot learns
    # nothing from the difference.
    if (body.get("company_url") or "").strip():
        return jsonify({"ok": True, "findings": [], "scan": {"status": "queued"}})

    contact, errors = widget_state.validate_contact(body)
    if errors:
        return jsonify({"ok": False, "errors": errors,
                        "error": "Check the highlighted fields."}), 400

    db = SessionLocal()
    try:
        cfg = _widget_config(db, slug)
        row = _run_by_token(db, body.get("token"))
        if row is None or row.widget_slug != slug or cfg is None:
            return jsonify({"ok": False, "error": "That check expired. Run it "
                                                  "again and we'll unlock the "
                                                  "report."}), 404

        first_unlock = row.unlocked_at is None
        if first_unlock and hub_leads is not None:
            # The Hub's shared ceiling on public lead capture.
            allowed, wait = hub_leads.rate_check(client_ip())
            if not allowed:
                return jsonify({"ok": False, "error":
                                "Too many submissions from one place. Try "
                                f"again in about {max(1, wait // 60)} minutes."}), 429

        row.name, row.email = contact["name"], contact["email"]
        row.phone, row.company = contact["phone"], contact["company"]
        row.unlocked_at = row.unlocked_at or _now()
        db.commit()

        try:
            pre = json.loads(row.precheck_json or "{}")
        except (TypeError, ValueError):
            pre = {}

        if first_unlock:
            _log("widget_lead", detail=row.domain, widget=slug,
                 client=row.company or None)
            # The credit is spent here and nowhere earlier. Started before the
            # lead is filed so the filed lead can carry the report link.
            _start_widget_scan(db, row)
            db.refresh(row)
            _capture_lead(row, cfg)
            db.commit()

        return jsonify({
            "ok": True,
            "findings": pre.get("findings", []),
            "score": pre.get("score"),
            "band": pre.get("band"),
            "headline": pre.get("headline"),
            "scan": {"status": row.scan_status or "pending",
                     "reused": bool(row.scan_reused)},
            "report_url": f"/scans/r/{row.token}",
        })
    finally:
        db.close()


@app.route("/api/w/<slug>/audit", methods=["POST"])
def api_widget_audit(slug):
    """The full-audit placement, in one step.

    The AI-visibility widget is two steps because the first one is free and
    instant: a five-second pre-check earns the right to ask for a name. A full
    audit has no such teaser — there is nothing to show a stranger before an
    Insites credit has been spent — so this asks once, for the contact *and*
    the handful of answers a crawler cannot get at, and starts the audit
    behind it.

    The order is deliberate and is the same one the other widget uses: the
    **lead is filed first**, because a lead is a lead whether or not Insites
    ever answers, and a business that has just told us what they sell must not
    be lost to a provider timeout. The audit attaches to the row when it
    lands.
    """
    if DB_BOOT_ERROR:
        return jsonify({"ok": False, "error": "The audit is temporarily "
                                              "unavailable."}), 503
    body = request.get_json(silent=True) or {}
    # Honeypot, answered 200 so a bot learns nothing from the difference.
    if (body.get("company_url") or "").strip():
        return jsonify({"ok": True, "token": "", "scan": {"status": "queued"}})

    if not _precheck_allowed():
        return jsonify({"ok": False, "error": "That's a lot of audits from one "
                                              "place. Give it a few minutes."}), 429

    db = SessionLocal()
    try:
        cfg = _widget_config(db, slug)
        if cfg is None or cfg.get("kind") != "audit":
            return jsonify({"ok": False,
                            "error": "This audit isn't available."}), 404

        domain = widget.normalise_domain(body.get("domain") or "")
        contact, intake, errors = widget_state.validate_audit(body)
        if not domain:
            errors["domain"] = ("Enter the address of the website, like "
                                "example.com.")
        if errors:
            return jsonify({"ok": False, "errors": errors,
                            "error": "Check the highlighted fields."}), 400

        if hub_leads is not None:
            allowed, wait = hub_leads.rate_check(client_ip())
            if not allowed:
                return jsonify({"ok": False, "error":
                                "Too many submissions from one place. Try "
                                f"again in about {max(1, wait // 60)} "
                                "minutes."}), 429

        row = widget_state.ScanRun(
            token=widget_state.new_token(),
            widget_slug=slug, tag=cfg["tag"], kind="audit",
            created_at=_now(), unlocked_at=_now(),
            name=contact["name"], email=contact["email"],
            phone=contact["phone"], company=contact["company"],
            domain=domain[:255], site_url=f"https://{domain}"[:500],
            source=_text(body.get("source") or request.referrer or "", 300),
            ip=client_ip(),
            user_agent=_text(request.headers.get("User-Agent", ""), 300),
            intake_json=json.dumps(intake),
            scan_status="pending")
        db.add(row)
        db.commit()
        _log("widget_audit_request", detail=domain, widget=slug,
             client=row.company or None)

        # The credit is spent here and nowhere earlier, and the lead is filed
        # after the audit is started so the filed lead can carry its link.
        _start_widget_scan(db, row)
        db.refresh(row)
        _capture_lead(row, cfg)
        db.commit()

        return jsonify({
            "ok": True, "token": row.token, "domain": domain,
            "scan": {"status": row.scan_status or "pending",
                     "reused": bool(row.scan_reused)},
            "report_url": f"/scans/r/{row.token}",
            "note": ("We already had a recent audit of this website, so yours "
                     "is ready now." if row.scan_reused else
                     "We are reading the website now — it usually takes a "
                     "minute or two."),
        })
    finally:
        db.close()


@app.route("/api/w/<slug>/status")
def api_widget_status(slug):
    """Step 3 — has the paid audit landed yet? Polled by the widget."""
    db = SessionLocal()
    try:
        row = _run_by_token(db, request.args.get("token"))
        if row is None or row.widget_slug != slug or row.unlocked_at is None:
            return jsonify({"ok": False, "error": "Unknown check."}), 404
        s = None
        if row.scan_public_id:
            s = db.query(Scan).filter(Scan.public_id == row.scan_public_id).first()
        if s is not None and s.status != row.scan_status:
            row.scan_status = s.status
            db.commit()
        ready = bool(s is not None and s.status == "complete")
        return jsonify({"ok": True, "status": row.scan_status or "pending",
                        "ready": ready,
                        "report_url": f"/scans/r/{row.token}" if ready else "",
                        "pdf_url": f"/scans/r/{row.token}.pdf" if ready else ""})
    finally:
        db.close()


def _lead_report(db, token: str):
    """(report, run) for a converted lead, or (None, ...).

    Only ever builds ``seo_aeo``. The other seven audits stay behind the Hub
    login — that data is what the sales conversation is made of, and handing
    it over for an email address gives away the reason to call.
    """
    row = _run_by_token(db, token)
    if row is None or row.unlocked_at is None:
        return None, None
    # A converted lead whose audit hasn't attached yet gets the waiting page,
    # not a 404. They have given us their details and been promised a report.
    if not row.scan_public_id:
        return None, row
    s = db.query(Scan).filter(Scan.public_id == row.scan_public_id).first()
    if s is None or s.status != "complete":
        return None, row
    try:
        return reports.report(_report_of(s), PUBLIC_REPORT_KEY), row
    except Exception:                              # noqa: BLE001
        app.logger.exception("lead report build failed")
        return None, row


def _audit_view(row):
    """The audit as a customer reads it, or None if it is not ready.

    Built through `hub/website_audit.py` — the same reading of the same audit
    the Hub's own Website Audit tool and Client 360 show, so a prospect and a
    rep cannot be looking at two different answers about one website. What is
    on the customer's page is a *subset* decided here rather than a second
    description of the audit: the spend block, what is worth fixing and the
    reference groups. The discovery mapping and the proposal prefill stay on
    our side of the wall — they are the reason to call, and handing them over
    for an email address gives that away, which is the same line
    `_lead_report` draws for the other placement.
    """
    if not row.scan_public_id:
        return None
    try:
        from hub import website_audit
        intake = json.loads(row.intake_json or "{}") or {}
    except Exception:                                   # noqa: BLE001
        app.logger.exception("audit view unavailable")
        return None
    try:
        payload = website_audit.audit(row.domain or "", intake=intake)
    except Exception:                                   # noqa: BLE001
        app.logger.exception("audit view build failed")
        return None
    if not payload.get("found"):
        return None
    # Stripped here rather than left to the template. A subset a renderer
    # merely happens not to print is one the next renderer prints: the
    # discovery mapping and the prefill are the reason to call, and handing
    # them over for an email address gives that away.
    payload.pop("discovery", None)
    payload.pop("discovery_note", None)
    return payload


@app.route("/r/<token>")
def widget_report(token):
    """The converted lead's own report, on an unguessable link."""
    db = SessionLocal()
    try:
        row = _run_by_token(db, token)
        if row is None or row.unlocked_at is None:
            return "That report link isn't valid.", 404
        if widget_state.kind_of(getattr(row, "kind", "")) == "audit":
            payload = _audit_view(row)
            if payload is None:
                return render_template("widget_waiting.html",
                                       lead=row.as_row(),
                                       status=row.scan_status or "pending")
            return render_template("widget_audit_report.html", a=payload,
                                   token=token, lead=row.as_row())
        r, row = _lead_report(db, token)
        if row is None:
            return "That report link isn't valid.", 404
        if r is None:
            return render_template("widget_waiting.html", lead=row.as_row(),
                                   status=row.scan_status or "pending")
        return render_template("widget_report.html", r=r, token=token,
                               lead=row.as_row())
    finally:
        db.close()


@app.route("/r/<token>.pdf")
def widget_report_pdf(token):
    """The same report as a PDF the lead can keep or forward."""
    db = SessionLocal()
    try:
        run = _run_by_token(db, token)
        if run is not None and widget_state.kind_of(getattr(run, "kind", "")) == "audit":
            # Said rather than 404'd: the audit is a page, and somebody who
            # guessed this address should be told where the document is.
            return ("The website audit is a page rather than a PDF — open "
                    f"/scans/r/{token} and print it from there."), 404
        r, row = _lead_report(db, token)
        if r is None:
            return "That report isn't ready yet.", 404
        try:
            pdf = report_pdf.build(r)
        except Exception:                          # noqa: BLE001
            app.logger.exception("lead PDF failed")
            return "We couldn't build that PDF.", 500
        _log("widget_pdf", detail=row.domain, widget=row.widget_slug,
             client=row.company or None)
        return Response(pdf, mimetype="application/pdf", headers={
            "Content-Disposition":
                f'inline; filename="{report_pdf.filename(r)}"'})
    finally:
        db.close()


# =====================================================================
# Placement admin (Hub side, behind the login)
# =====================================================================
#
# This is a tool in its own right — it has a tile on /tools under Landing
# Pages, because a widget dropped on a client's site is a lead-capture page by
# another name and nobody looking for one thinks to open Site Scans first. It
# lives here rather than as a hub blueprint because the placements and their
# runs are on the scans engine and the pages the placements serve are the
# routes above; a second home for it would be a second description of what a
# placement is.


def _placement_rows(db):
    """(rows, stats_error). Every placement with what it has produced.

    The counts come back as a pair, so a database that would not answer is
    reported as *not measured* rather than drawn as a column of zeroes —
    "nobody has used this placement" is the finding somebody deletes a
    placement over.
    """
    rows = (db.query(widget_state.ScanWidget)
              .order_by(widget_state.ScanWidget.id.desc()).all())
    stats, err = widget_state.placement_stats_result(db, rows)
    out = []
    for w in rows:
        row = w.as_row(_base_url())
        row["stats"] = None if stats is None else stats.get(w.slug)
        out.append(row)
    return out, err


@app.route("/widgets")
def widgets_page():
    db = SessionLocal()
    try:
        widgets, stats_error = _placement_rows(db)
        return render_template(
            "widgets.html",
            widgets=widgets, stats_error=stats_error,
            base_url=_base_url(), defaults=widget_state.DEFAULTS,
            kinds=widget_state.KINDS,
            reuse_days=REUSE_DAYS,
            # The Hub picks the route; the widget has none of its own. Ask what
            # it resolved to rather than for a webhook URL that no longer
            # delivers anything.
            leads_route=(hub_leads.delivery_mode() if hub_leads else "none"),
            insites_ok=is_configured(), warnings=config_warnings())
    finally:
        db.close()


@app.route("/api/widgets", methods=["POST"])
def api_widget_save():
    """Create a placement, or edit one named by ``slug``.

    Two rules, both of which were a way to lose a live placement quietly:

    **An edit names the placement it is editing.** Without that this route
    upserted on whatever the name slugified to, so a second placement called
    "Smart 1 home page" silently replaced the first — same address, new
    headline, and the only sign was that the list did not get longer.

    **The address never changes.** The slug is in the three lines of embed code
    sitting on a client's site; renaming it takes the widget off that page and
    reads here as a successful save. The internal name and the lead tag are
    editable, the address is not.
    """
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if len(name) < 2:
        return jsonify({"error": "Give this placement a name."}), 400

    editing = widget_state.slugify(body.get("slug") or "") if body.get("slug") else ""

    db = SessionLocal()
    try:
        if editing:
            w = _widget_or_none(db, editing)
            if w is None:
                return jsonify({"error": "That placement no longer exists. "
                                         "Reload the page."}), 404
            slug = w.slug
        else:
            slug = widget_state.slugify(body.get("new_slug") or name)
            existing = _widget_or_none(db, slug)
            if existing is not None:
                return jsonify({
                    "error": f"\u201c{existing.name}\u201d already uses the "
                             f"address /{slug}. Open it and press Edit, or "
                             f"give this one a different name.",
                    "slug": slug}), 409
            w = widget_state.ScanWidget(
                slug=slug, created_at=_now(),
                kind=widget_state.kind_of(body.get("kind")),
                created_by=actor_name()[:120])
            db.add(w)

        # The kind is fixed at creation, for the same reason the address is:
        # both are in the three lines of embed code already pasted on somebody
        # else's website. Changing the kind swaps what a live placement serves
        # a visitor -- an AI check becomes a form asking what they sell -- and
        # reads here as a successful save.
        if editing and body.get("kind") \
                and widget_state.kind_of(body["kind"]) != widget_state.kind_of(w.kind):
            return jsonify({
                "error": "A placement's kind cannot be changed. The embed on "
                         "the client's site would start serving a different "
                         "form to the same visitors. Create a new placement "
                         "and pause this one."}), 409

        tag = widget_state.slugify(body.get("tag") or slug)
        w.name = name[:200]
        w.tag = tag[:64]
        w.headline = _text(body.get("headline"), 300)
        w.subhead = _text(body.get("subhead"), 500)
        w.button_label = _text(body.get("button_label"), 80)
        w.accent = _text(body.get("accent"), 16)
        if not editing:
            w.active = 0 if body.get("active") is False else 1
        elif body.get("active") is not None:
            w.active = 1 if body.get("active") else 0
        db.commit()
        _log("widget_edited" if editing else "widget_saved",
             detail=slug, tag=tag)
        return jsonify({"ok": True, "widget": w.as_row(_base_url())})
    finally:
        db.close()


@app.route("/api/widgets/<slug>/toggle", methods=["POST"])
def api_widget_toggle(slug):
    """Pause or resume. A paused placement still answers on its address —
    ``_widget_config`` returns None for it, so the page says the scan is not
    available rather than 404ing an embed a client can still see."""
    db = SessionLocal()
    try:
        w = _widget_or_none(db, slug)
        if w is None:
            return jsonify({"error": "Unknown placement."}), 404
        w.active = 0 if w.active else 1
        db.commit()
        _log("widget_toggled", detail=slug, active=bool(w.active))
        return jsonify({"ok": True, "widget": w.as_row(_base_url())})
    finally:
        db.close()


@app.route("/api/widgets/<slug>", methods=["DELETE"])
def api_widget_delete(slug):
    """Delete a placement. The leads it produced are not deleted with it.

    Three things this is careful about, because delete is the one action on
    this page nothing undoes:

    **A placement that has captured leads is refused once.** The reply names
    the count and the caller has to say ``confirm`` — deleting is how somebody
    tidies a list, and the row worth keeping looks exactly like the row worth
    removing until you are told what came off it.

    **Runs are kept.** They are the evidence of where a real person in the
    Leads panel came from, and the lead itself lives in ``hub.leads``, which
    this route has no business editing. The count simply stops being
    reachable from this page.

    **The embed stops working.** Whatever is pasted on the client's site
    starts answering "this scan isn't available", so the confirmation says so
    and Pause is offered as the reversible half of the same intent.
    """
    body = request.get_json(silent=True) or {}
    db = SessionLocal()
    try:
        w = _widget_or_none(db, slug)
        if w is None:
            return jsonify({"error": "Unknown placement."}), 404

        stats, err = widget_state.placement_stats_result(db, [w])
        if err:
            # Refusing is the safe half: a placement deleted while we could not
            # tell whether anyone had used it is a decision made on no
            # information at all.
            return jsonify({"error": "We couldn't read this placement's leads, "
                                     "so we haven't deleted it. Try again in a "
                                     "moment.", "detail": err}), 503
        leads = int((stats.get(slug) or {}).get("leads") or 0)
        if leads and not body.get("confirm"):
            return jsonify({
                "error": f"This placement has captured {leads} "
                         f"lead{'' if leads == 1 else 's'}.",
                "leads": leads, "confirm_required": True}), 409

        db.delete(w)
        db.commit()
        _log("widget_deleted", detail=slug, tag=w.tag or "", leads=leads)
        return jsonify({"ok": True, "deleted": slug, "leads_kept": leads})
    finally:
        db.close()
