"""Smart 1 Hub — Media Calculators.

Mounted at /tools/calculators/. Two audiences:

  * Hub staff  — the index and leads pages, behind the normal Hub login.
  * Prospects  — the calculator pages and their two API routes, public so the
                 calculators can be embedded on smart1marketing.com or a
                 Smart 1 Sites page.

The gate is server-side. /api/<slug>/estimate returns headline metrics only and
stores the full result under an unguessable token; /api/<slug>/unlock trades a
validated name, email and phone for the rest. Nothing withheld is ever sent to
the browser before the contact is captured, so the CSS-blur "gate" the suite
audit found bypassable with one devtools click can't happen here.
"""

import csv
import io
import json
import re
import time
from collections import deque
from functools import wraps

from flask import (Blueprint, Response, abort, current_app, jsonify,
                   render_template, request)

from . import catalog, store
from hub.webargs import clamp_int

# Activity logging. Guarded so this module still runs standalone, but
# inside the Hub every action is attributable — this module published calculators and captured leads,
# and an unattributable change to a client's account is one nobody can
# explain later.
try:
    from hub import audit as _hub_audit
    _audit = _hub_audit.for_module("calculators")
except Exception:  # noqa: BLE001
    def _audit(*a, **k):  # no-op outside the Hub
        return None


bp = Blueprint("calculators", __name__, template_folder="templates")

MOUNT = "/tools/calculators"

# Routes below the mount that must stay outside the Hub login. Register these
# with the Hub's AuthGuard when merging (see README).
PUBLIC_PREFIXES = ("/c/", "/embed/", "/api/", "/embed.js")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")


# --- Client IP and rate limiting ---------------------------------------------

def client_ip():
    """The last X-Forwarded-For hop, not the first.

    The first entry is client-supplied. Three suite apps trusted it and a
    spoofed header walked straight through their limiter, 20 requests, zero
    429s. Behind Render there is exactly one trusted proxy, so take the last.
    """
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.remote_addr or "unknown"


_hits = {}


def rate_limit(limit, window_seconds, bucket):
    """Small in-process limiter. Enough for one Render web service.

    If the Hub grows to multiple workers, move this to Redis — but an
    unauthenticated compute endpoint should never ship with no limiter at all.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = (bucket, client_ip())
            now = time.time()
            q = _hits.setdefault(key, deque())
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                retry = int(window_seconds - (now - q[0])) + 1
                resp = jsonify({
                    "ok": False,
                    "error": "You've run a lot of estimates in a short window. "
                             "Try again in about {} second(s).".format(retry),
                })
                resp.status_code = 429
                resp.headers["Retry-After"] = str(retry)
                return resp
            q.append(now)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def _db_guard():
    if store.DB_BOOT_ERROR:
        return jsonify({"ok": False, "error": "Calculators are temporarily unavailable. "
                                              "Try again in a moment."}), 503
    return None


# --- Public pages ------------------------------------------------------------

def _calc_or_404(slug):
    calc = catalog.get(slug)
    if not calc:
        abort(404)
    return calc


@bp.get("/c/<slug>")
def public_page(slug):
    """Standalone hosted page — the link you can send or run an ad to."""
    calc = _calc_or_404(slug)
    return render_template("calculator.html", calc=calc, mount=MOUNT, embedded=False,
                           source=request.args.get("src", ""))


@bp.get("/embed/<slug>")
def embed_page(slug):
    """Chrome-free version for an iframe on smart1marketing.com or Sites."""
    calc = _calc_or_404(slug)
    return render_template("calculator.html", calc=calc, mount=MOUNT, embedded=True,
                           source=request.args.get("src", ""))


@bp.get("/embed.js")
def embed_js():
    """Auto-resizes the iframe so the page never shows an inner scrollbar."""
    js = """(function(){
  window.addEventListener('message', function(e){
    var d = e.data || {};
    if (!d || d.type !== 's1calc:height') return;
    var frames = document.querySelectorAll('iframe[data-s1calc]');
    for (var i = 0; i < frames.length; i++) {
      if (frames[i].contentWindow === e.source) frames[i].style.height = d.height + 'px';
    }
  });
})();"""
    return Response(js, mimetype="application/javascript",
                    headers={"Cache-Control": "public, max-age=3600"})


# --- Public API --------------------------------------------------------------

@bp.post("/api/<slug>/estimate")
@rate_limit(30, 300, "estimate")
def api_estimate(slug):
    guard = _db_guard()
    if guard:
        return guard
    calc = _calc_or_404(slug)
    payload = request.get_json(silent=True) or {}
    inputs = payload.get("inputs") or {}

    try:
        result = catalog.run(slug, inputs)
    except catalog.CalcError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception:
        # Never hand a raw Python exception to a prospect. Two suite apps
        # concatenated the server's `detail` field into the page and printed an
        # OpenAI key prefix on a public lead-gen form.
        current_app.logger.exception("calculator %s failed", slug)
        return jsonify({"ok": False, "error": "We couldn't build that estimate. "
                                              "Check the numbers and try again."}), 500

    token = store.save_estimate(slug, inputs, result, client_ip(),
                                payload.get("source") or request.referrer or "")
    return jsonify({
        "ok": True,
        "token": token,
        "metrics": result["metrics"],
        "teaser_note": result.get("teaser_note", ""),
    })


@bp.post("/api/<slug>/unlock")
@rate_limit(20, 300, "unlock")
def api_unlock(slug):
    guard = _db_guard()
    if guard:
        return guard
    _calc_or_404(slug)
    payload = request.get_json(silent=True) or {}

    if payload.get("website"):          # honeypot, hidden in the form
        return jsonify({"ok": True, "detail": {"summary": "", "table": None, "next_steps": []}})

    contact = {
        "name": str(payload.get("name") or "").strip(),
        "email": str(payload.get("email") or "").strip(),
        "company": str(payload.get("company") or "").strip(),
        "phone": str(payload.get("phone") or "").strip(),
    }
    errors = {}
    if len(contact["name"]) < 2:
        errors["name"] = "Enter your name."
    if not EMAIL_RE.match(contact["email"]):
        errors["email"] = "Enter a working email address."
    digits = re.sub(r"\D", "", contact["phone"])
    # Phone is required here. It was collected and validated in zero of the
    # eleven suite apps, which is why so many captured leads weren't callable.
    if len(digits) < 10:
        errors["phone"] = "Enter a phone number with at least 10 digits."
    if errors:
        return jsonify({"ok": False, "errors": errors,
                        "error": "Check the highlighted fields."}), 400

    row = store.load(payload.get("token"))
    if not row or row.slug != slug:
        return jsonify({"ok": False, "error": "That estimate expired. "
                                              "Run it again and we'll unlock the plan."}), 404

    row = store.save_contact(row.token, contact)
    result = json.loads(row.result_json or "{}")

    # Stored first, sent second. A webhook failure can't lose the lead.
    store.send_webhook(current_app, row, catalog.get(slug)["title"])

    return jsonify({"ok": True, "detail": result.get("detail", {})})


# --- Hub-side pages (behind the Hub login) -----------------------------------

@bp.get("/")
def index():
    stats = {} if store.DB_BOOT_ERROR else store.counts()
    calcs = catalog.all_calculators()
    return render_template("index.html", calcs=calcs, mount=MOUNT,
                           stats=stats, db_error=store.DB_BOOT_ERROR,
                           # What the code does, not what one legacy env var
                           # says. The old banner read a webhook that stopped
                           # being the route and cried wolf on every page load.
                           delivery=store.delivery_status(
                               current_app, [c["slug"] for c in calcs]),
                           base_url=request.url_root.rstrip("/"))


@bp.get("/leads")
def leads_page():
    slug = request.args.get("slug") or ""
    rows = [] if store.DB_BOOT_ERROR else store.leads(slug or None, clamp_int(request.args.get("limit"), 200, 1, 1000))
    return render_template("leads.html", rows=rows, calcs=catalog.all_calculators(),
                           mount=MOUNT, active=slug, db_error=store.DB_BOOT_ERROR)


@bp.get("/leads.csv")
def leads_csv():
    rows = store.leads(request.args.get("slug") or None, 1000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Created", "Unlocked", "Calculator", "Name", "Email", "Phone",
                "Company", "Source page", "Est. value", "Sent to Suite"])
    for r in rows:
        w.writerow([r["created_at"], r["unlocked_at"], r["slug"], r["name"], r["email"],
                    r["phone"], r["company"], r["source"], round(r["lead_value"] or 0),
                    r["webhook_status"]])
    return Response(buf.getvalue(), mimetype="text/csv", headers={
        "Content-Disposition": "attachment; filename=smart1-calculator-leads.csv"})


@bp.post("/leads/retry")
def leads_retry():
    titles = {c["slug"]: c["title"] for c in catalog.all_calculators()}
    out = store.retry_failed(current_app, titles)
    return jsonify({"ok": True, **out})


@bp.get("/api/health")
def health():
    return jsonify({
        "ok": not bool(store.DB_BOOT_ERROR),
        "calculators": [c["slug"] for c in catalog.all_calculators()],
        "database": "error" if store.DB_BOOT_ERROR else "ok",
        "delivery": store.delivery_status(
            current_app, [c["slug"] for c in catalog.all_calculators()]),
    })
