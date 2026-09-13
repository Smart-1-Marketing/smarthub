"""Smart 1 Hub -- Reports module.

The ad-performance reporting foundation: a fact table fed by every platform
sync, the map from a platform's campaign to a Hub client, the budgets those
campaigns pace against, how each platform's raw spend is billed -- and the
one page a client sees, at a link of their own.

Mounted at ``/reports`` the way ``modules/scans`` is -- a standalone Flask
app behind ``wsgi.py``'s ``AuthGuard`` -- and it carries
``hub/blueprint_guard.py``'s gate as well, so a route added here is staff-only
whichever way the module is reached.

## The public URL

The client's live dashboard is ``/reports/r/c/<token>``, with
``/reports/r/c/<token>.pdf`` and ``/reports/r/c/<token>/data.json`` beside
it. That is the shape ``modules/scans`` exposes its report at (``/scans/r/
<token>``) and Smart 1 Ads its performance report (``/tools/ads/r/<token>``):
the module declares ``PUBLIC_PREFIXES = ("/r/c/",)`` mount-relative, and
``wsgi.py`` hands that one tuple to both ``AuthGuard`` (a client with no Hub
login can open it) and ``HubBar`` (the staff sidebar, help layer and feedback
tab are not injected into it). One declaration for both halves, read from
the module, so the mount and the module cannot disagree about what is
public. A second root-level mount for ``/r/c/`` was considered and refused:
it would be a second entry in ``wsgi.py``'s mount table for one module, and
a hub-app ``CHROMELESS`` entry cannot reach a mounted path at all -- the
first trap CLAUDE.md names.

The tables are in their own database. ``store.py`` says why and how.
"""
from __future__ import annotations

import logging
import os
import threading
import time as _time
from pathlib import Path

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, url_for)

from hub.webargs import clamp_int

from . import client_pdf, client_view, organic, pacing, products, store

try:                                   # the shared last-hop rule for a caller's address
    from hub import leads as hub_leads
except Exception:                      # noqa: BLE001 - standalone/dev fallback
    hub_leads = None

try:                                   # Hub activity log (present in the Hub)
    from hub import audit as hub_audit
except Exception:                      # noqa: BLE001 - standalone/dev fallback
    hub_audit = None

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))

# Mount-relative prefixes that sit outside the Hub login: the client's own
# dashboard, its PDF and its data. wsgi.py reads this tuple rather than
# repeating it, so the mount and the module cannot disagree about what is
# public -- and both halves of "public" (no login, no chrome) come from it.
PUBLIC_PREFIXES: tuple[str, ...] = ("/r/c/",)

# The public page, rate-limited per address the way the scan widget's
# pre-check is: a client refreshing is a handful of requests, a script
# hammering tokens is not, and a 429 costs the second nothing it should have.
PUBLIC_LIMIT = int(os.environ.get("REPORTS_PUBLIC_LIMIT") or 120)
PUBLIC_WINDOW = 600
_PUBLIC_HITS: dict[str, list] = {}
_PUBLIC_LOCK = threading.Lock()

# One gate for every route on this app -- hub/blueprint_guard.py's, installed
# on the app the way the blueprint modules install it. AuthGuard already
# stands in front of the mount; this is the same answer from inside, so a
# route here is staff-only even when the module is reached some other way.
try:
    from hub.blueprint_guard import install as _install_guard
    _install_guard(app, mount="/reports", public=PUBLIC_PREFIXES)
except Exception:                      # noqa: BLE001 - standalone, no Hub
    pass


def actor_name() -> str:
    return request.environ.get("s1hub.user") or "Unknown"


def client_ip() -> str:
    """The caller's address, LAST X-Forwarded-For hop -- hub.leads.client_ip's
    rule, because the first hop is client-supplied."""
    if hub_leads is not None:
        return hub_leads.client_ip(request)
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[-1].strip() if fwd else request.remote_addr or "")[:64]


def _public_allowed() -> bool:
    if PUBLIC_LIMIT <= 0:
        return True
    ip, now = client_ip(), _time.time()
    with _PUBLIC_LOCK:
        hits = [t for t in _PUBLIC_HITS.get(ip, []) if now - t < PUBLIC_WINDOW]
        if len(hits) >= PUBLIC_LIMIT:
            _PUBLIC_HITS[ip] = hits
            return False
        hits.append(now)
        _PUBLIC_HITS[ip] = hits
        if len(_PUBLIC_HITS) > 10000:
            _PUBLIC_HITS.clear()
    return True


def _log(event: str, **extra) -> None:
    """Write to the Hub-wide activity log; never breaks a request."""
    if hub_audit is None:
        return
    try:
        hub_audit.log("reports", event, actor=actor_name(), **extra)
    except Exception:                  # noqa: BLE001
        pass


@app.before_request
def _db_guard():
    """A database that was not reachable at boot should say so once, plainly,
    rather than answering a traceback on every route. db_error() re-asks a
    transient failure, so a recovery reaches every route below."""
    if store.db_error() and request.path not in ("/health",):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Reports database not ready. "
                                     + store.DB_BOOT_ERROR}), 503
        return (
            "<html><body style='font-family:system-ui;padding:40px'>"
            "<h2 style='color:#1a2e58'>Reports database not ready</h2>"
            f"<p>{store.DB_BOOT_ERROR}</p>"
            "<p>Check <code>REPORTS_DATABASE_URL</code> (or <code>DATABASE_URL</code>) "
            "and redeploy. <a href='/status'>System Status</a></p></body></html>", 503)
    return None


@app.route("/health")
def health():
    return jsonify({"ok": not store.db_error(), "binding": store.binding(),
                    "db_error": store.db_error() or None})


# ---------------------------------------------------------------- landing
@app.route("/")
def index():
    return render_template(
        "reports_index.html",
        platforms=store.platform_status(),
        unmapped=store.unmapped_count(),
        facts=store.fact_count(),
        binding=store.binding(),
        markups=[m for m in store.markups()
                 if m["markup"] is not None or m["cpm"] is not None],
        budgets=len(store.budget_lines(limit=1000)),
        clients=store.clients_with_campaigns(),
        native=_native_status(),
        rate_card=products.rate_card_products(),
    )


NATIVE_PULLS = (("ttd", "ttd"), ("google", "google_ads_perf"),
                ("stackadapt", "stackadapt"), ("audiogo", "audiogo"))


def _native_status() -> list[dict]:
    """The native pulls' status lines, each in its own try: a status line
    that raises costs the index, and the index is where a failing pull is
    seen."""
    out = []
    for label, mod in NATIVE_PULLS:
        try:
            import importlib
            m = importlib.import_module(f"modules.reports.{mod}")
            out.append({"platform": label, **m.status()})
        except Exception as exc:               # noqa: BLE001
            out.append({"platform": label, "connected": False,
                        "line": f"{label}: status could not be read ({type(exc).__name__})"})
    return out


# --------------------------------------------------------- provider check
@app.route("/provider-check")
def provider_check():
    """What the provider schema actually holds, against what the map expects.

    The column names in provider_map.py are placeholders until the first sync
    lands; this is the page they are corrected from. A plain table: every
    table present with its columns, and per platform whether the map
    resolves, the table is missing, or these columns are.
    """
    from . import normalize, provider_map
    tables = normalize.schema_tables()
    return render_template(
        "reports_provider_check.html",
        schema=provider_map.schema(), tables=tables,
        sources=normalize.check_sources(tables))


@app.route("/audiogo-check")
def audiogo_check():
    """What AudioGo's configured endpoint actually answers, against what
    audiogo_map.py expects -- the same pattern as provider-check. Calls the
    endpoint for yesterday and prints the raw JSON keys (the key itself
    never reaches a body) so the real names can be pasted into the map."""
    from . import audiogo
    return render_template("reports_audiogo_check.html", chk=audiogo.check())


# ---------------------------------------------------------------- mapping
def _resolve_client(name: str, key: str) -> tuple[str, str]:
    """The (key, display name) a mapping is filed under.

    The picker hands over both. A key typed by hand, or a name with no key
    beside it, is resolved through the client registry so the row carries
    the same key every other module's record uses -- and falls back to a
    name key when the registry cannot see the client, which is a mapping
    that still works and is marked as name-backed by its prefix.
    """
    name = (name or "").strip()
    key = (key or "").strip()
    if key and name:
        return key[:200], name[:300]
    try:
        from hub import client_key as ck
        from hub import clients_registry
        hit = clients_registry.find_client(name) if name else None
        if hit:
            return (ck.client_key(hit.get("name") or name, hit.get("url") or hit.get("domain") or "")
                    or ck.name_key(name), hit.get("name") or name)
        if key:
            return key[:200], (name or ck.key_label(key))[:300]
        return ck.name_key(name), name
    except Exception:                  # noqa: BLE001 - registry unavailable
        return (key or ("n:" + name.lower().replace(" ", "-")))[:200], name[:300]


@app.route("/unmapped")
def unmapped():
    days = clamp_int(request.args.get("days"), 30, 1, 365)
    limit = clamp_int(request.args.get("limit"), 200, 1, 1000)
    return render_template(
        "reports_unmapped.html",
        rows=store.unmapped_campaigns(days=days, limit=limit),
        days=days, shape=store.RENAME_SHAPE,
        products=products.catalog(),
        defaults=products.DEFAULT_PRODUCT_FOR_PLATFORM,
        recent=store.mapped_campaigns(limit=25),
        error=request.args.get("error", ""),
        saved=request.args.get("saved", ""),
    )


@app.route("/unmapped", methods=["POST"])
def map_campaign():
    f = request.form
    client_key, client_name = _resolve_client(f.get("client_name", ""),
                                              f.get("client_key", ""))
    if not client_name and not client_key:
        return redirect(url_for("unmapped", error="Pick a client first."))
    try:
        row = store.map_campaign(
            f.get("platform", ""), f.get("account_id", ""), f.get("campaign_id", ""),
            client=client_key, client_name=client_name,
            product=f.get("product", ""), mapped_by=actor_name(),
            campaign_name=f.get("campaign_name", ""),
            display_name=f.get("display_name") or None)
    except ValueError as exc:
        return redirect(url_for("unmapped", error=str(exc)))
    # client= so the mapping lands on that client's 360 activity.
    _log("campaign_mapped", client=client_name or client_key,
         client_key=row.client, platform=row.platform,
         campaign_id=row.campaign_id, product=row.product or None,
         detail=f"{store.platform_label(row.platform)} campaign "
                f"{f.get('campaign_name') or row.campaign_id} mapped to "
                f"{client_name or client_key}")
    return redirect(url_for("unmapped", saved=row.campaign_id))


@app.route("/api/clients")
def api_clients():
    """The client picker's type-ahead: name, domain and the Hub key.

    Reads hub/clients_registry directly rather than proxying the hub's own
    /api/clients/search, because the picker needs the key beside the name
    and only this module knows it wants one.
    """
    q = (request.args.get("q") or "").strip()
    limit = clamp_int(request.args.get("limit"), 12, 1, 50)
    try:
        from hub import client_key as ck
        from hub import clients_registry
        rows = clients_registry.search_clients(q, limit=limit)
    except Exception as exc:           # noqa: BLE001 - registry unavailable
        return jsonify({"clients": [], "error": f"{type(exc).__name__}: {exc}"})
    out = []
    for r in rows:
        name = r.get("name") or ""
        out.append({
            "name": name, "domain": r.get("domain") or "",
            "key": ck.client_key(name, r.get("url") or r.get("domain") or ""),
            "slug": r.get("slug") or "",
        })
    return jsonify({"clients": out})


# ---------------------------------------------------------------- markup
@app.route("/markup")
def markup():
    return render_template("reports_markup.html", rows=store.markups(),
                           error=request.args.get("error", ""),
                           saved=request.args.get("saved", ""))


@app.route("/markup", methods=["POST"])
def markup_save():
    """One form, every platform. A row with both boxes filled is refused
    whole, so nothing is written for any platform until every row is a
    single answer."""
    f = request.form
    wanted = []
    for p in store.PLATFORMS:
        pct = (f.get(f"markup_{p}") or "").strip()
        cpm = (f.get(f"cpm_{p}") or "").strip()
        if pct and cpm:
            return redirect(url_for("markup", error=(
                f"{store.platform_label(p)}: fill in Markup % or Fixed CPM, "
                "not both.")))
        if not pct and not cpm:
            wanted.append((p, None, None))
            continue
        try:
            markup_frac = store.markup_from_percent(pct) if pct else None
        except ValueError as exc:
            return redirect(url_for("markup", error=f"{store.platform_label(p)}: {exc}"))
        wanted.append((p, markup_frac, cpm or None))
    changed = []
    for p, frac, cpm in wanted:
        try:
            if frac is None and cpm is None:
                if store.clear_markup(p):
                    changed.append(p)
                continue
            store.set_markup(p, markup=frac, cpm=cpm, updated_by=actor_name())
            changed.append(p)
        except ValueError as exc:
            return redirect(url_for("markup", error=str(exc)))
    if changed:
        _log("markup_saved", detail=", ".join(store.platform_label(p) for p in changed))
    return redirect(url_for("markup", saved="1"))


# ---------------------------------------------------------------- budgets
@app.route("/budgets")
def budgets():
    limit = clamp_int(request.args.get("limit"), 200, 1, 1000)
    return render_template("reports_budgets.html", rows=store.budget_lines(limit=limit),
                           platforms=[(p, store.platform_label(p)) for p in store.PLATFORMS],
                           error=request.args.get("error", ""),
                           saved=request.args.get("saved", ""))


@app.route("/budgets", methods=["POST"])
def budget_add():
    f = request.form
    client_key, client_name = _resolve_client(f.get("client_name", ""),
                                              f.get("client_key", ""))
    if not client_name and not client_key:
        return redirect(url_for("budgets", error="Pick a client first."))
    try:
        row = store.add_budget_line(
            client=client_key, client_name=client_name,
            product=f.get("product", ""), platform=f.get("platform", ""),
            monthly_budget=f.get("monthly_budget", ""),
            flight_start=f.get("flight_start", ""), flight_end=f.get("flight_end", ""),
            notes=f.get("notes", ""), created_by=actor_name(),
            sold_amount=f.get("sold_amount", ""), owner=f.get("owner", ""),
            status=f.get("status", "active"))
    except ValueError as exc:
        return redirect(url_for("budgets", error=str(exc)))
    _log("budget_added", client=client_name or client_key, client_key=row.client,
         product=row.product, platform=row.platform,
         monthly_budget=str(row.monthly_budget),
         sold_amount=str(row.sold_amount) if row.sold_amount is not None else None,
         owner=row.owner or None,
         detail=f"{row.product} ${row.monthly_budget}/mo for {client_name or client_key}")
    return redirect(url_for("budgets", saved=str(row.id)))


@app.route("/budgets/<int:line_id>", methods=["POST"])
def budget_update(line_id):
    """Status, owner and sold amount on an existing line -- the three
    columns pacing and the cost report read."""
    f = request.form
    try:
        row = store.update_budget_line(line_id, status=f.get("status"), owner=f.get("owner"),
                                       sold_amount=f.get("sold_amount"),
                                       clear_sold=f.get("sold_amount", None) == "")
    except ValueError as exc:
        return redirect(url_for("budgets", error=str(exc)))
    if row is None:
        return redirect(url_for("budgets", error="That budget line is gone."))
    _log("budget_updated", client=row.client_name or row.client, client_key=row.client,
         product=row.product, status=row.status, owner=row.owner or None,
         sold_amount=str(row.sold_amount) if row.sold_amount is not None else None,
         detail=f"{row.product} line #{row.id} for {row.client_name or row.client}: "
                f"{row.status}, owner {row.owner or 'nobody'}, sold "
                f"{('$' + str(row.sold_amount)) if row.sold_amount is not None else 'not set'}")
    return redirect(url_for("budgets", saved=str(row.id)))


# ------------------------------------------------------- pacing and cost
#
# Staff only, and never linked from anything a client sees. Both read the
# latest pacing SNAPSHOT (modules/reports/pacing.py) rather than summing
# the fact table on every open.

@app.route("/pacing")
def pacing_board():
    a = request.args
    data = pacing.board(band=(a.get("band") or "")[:20], platform=(a.get("platform") or "")[:20],
                        owner=(a.get("owner") or "")[:160], client=(a.get("client") or "")[:200],
                        sort=(a.get("sort") or "band")[:20])
    return render_template("reports_pacing.html", b=data, bands=pacing.BANDS,
                           labels=pacing.BAND_LABELS, platform_label=store.platform_label)


@app.route("/pacing.csv")
def pacing_csv():
    a = request.args
    data = pacing.board(band=(a.get("band") or "")[:20], platform=(a.get("platform") or "")[:20],
                        owner=(a.get("owner") or "")[:160], client=(a.get("client") or "")[:200],
                        sort=(a.get("sort") or "band")[:20])
    return Response(pacing.board_csv(data), mimetype="text/csv", headers={
        "Content-Disposition": 'attachment; filename="pacing.csv"'})


@app.route("/cost")
def cost_report():
    month = (request.args.get("month") or "")[:7]
    return render_template("reports_cost.html", c=pacing.cost(month))


@app.route("/cost.csv")
def cost_csv():
    month = (request.args.get("month") or "")[:7]
    data = pacing.cost(month)
    return Response(pacing.cost_csv(data), mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="cost-{data["month"]["key"]}.csv"'})


# ==================================================================== public
#
# The client's own page. Three routes, one aggregate: the page and the PDF
# both render what client_view.aggregate() returned for the period, and
# data.json is that same dict -- so the spend rule (pricing.client_price)
# is applied in exactly one place and no template ever sees a raw figure.
# Revoked and never-existed answer differently on purpose here: a replaced
# link tells the client to ask for the new one rather than reading as a
# report that has gone, and a token that never existed is a plain 404.

_REPLACED = ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
             "<meta name='viewport' content='width=device-width, initial-scale=1'>"
             "<title>This report link has been replaced</title>"
             "<style>body{font:15px/1.5 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
             "color:#0b0b0b;background:#fcfcfb;margin:0}.wrap{max-width:520px;margin:80px auto;"
             "padding:0 20px}h1{font-size:20px}p{color:#52514e}</style></head><body>"
             "<div class='wrap'><h1>This report link has been replaced</h1>"
             "<p>A newer link was issued for this report. Contact your Smart 1 rep and "
             "they will send you the current one.</p></div></body></html>")


def _public_link(token: str):
    """(link, refusal) for a public request: the live row, or the response
    to send instead."""
    if not _public_allowed():
        return None, Response("Too many requests. Try again in a few minutes.",
                              status=429, mimetype="text/plain")
    link = store.get_link(token)
    if link is None:
        return None, ("That report link isn't valid.", 404)
    if not link.enabled:
        return None, Response(_REPLACED, status=410, mimetype="text/html")
    return link, None


@app.route("/r/c/<token>")
def client_dashboard(token):
    link, refusal = _public_link(token)
    if refusal is not None:
        return refusal
    period = (request.args.get("period") or "mtd")[:12]
    agg = client_view.aggregate(link, period)
    store.note_view(token)
    resp = Response(render_template("reports_client_public.html", a=agg, token=token))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.route("/r/c/<token>/data.json")
def client_dashboard_data(token):
    link, refusal = _public_link(token)
    if refusal is not None:
        return refusal
    period = (request.args.get("period") or "mtd")[:12]
    resp = jsonify(client_view.aggregate(link, period))
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


@app.route("/r/c/<token>.pdf")
def client_dashboard_pdf(token):
    link, refusal = _public_link(token)
    if refusal is not None:
        return refusal
    period = (request.args.get("period") or "mtd")[:12]
    agg = client_view.aggregate(link, period)
    try:
        pdf = client_pdf.build(agg)
    except Exception:                      # noqa: BLE001
        app.logger.exception("client dashboard PDF failed")
        return "We couldn't build that PDF.", 500
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{client_pdf.filename(agg)}"',
        "X-Robots-Tag": "noindex, nofollow"})


# ==================================================================== staff
#
# The internal view of one client: raw spend beside what the client is
# billed, the mapped campaigns, the budget lines pacing, and the link and
# its settings. Everything under /client/ is behind the guard.

def _client_name_for(client: str) -> str:
    for m in store.mapped_campaigns_for(client):
        if m["client_name"]:
            return m["client_name"]
    for b in store.budget_lines_for(client):
        if b["client_name"]:
            return b["client_name"]
    try:
        from hub import client_key as ck
        return ck.key_label(client)
    except Exception:                      # noqa: BLE001
        return client


def _public_url(token: str) -> str:
    try:
        from hub.config import public_base_origin
        base = public_base_origin()
    except Exception:                      # noqa: BLE001
        base = ""
    return f"{base or request.host_url.rstrip('/')}/reports/r/c/{token}"


@app.route("/client/<path:client>")
def client_page(client):
    from datetime import date
    today = date.today()
    link = store.link_for_client(client)
    period = (request.args.get("period") or "mtd")[:12]
    rng = client_view.period_range(period, today)
    return render_template(
        "reports_client.html",
        client=client, client_name=_client_name_for(client),
        link=link.as_dict() if link else None,
        public_url=_public_url(link.token) if link else "",
        totals=client_view.staff_totals(client, rng["start"], rng["end"], link),
        period=rng, period_key=period,
        preview=client_view.aggregate(link, period) if link else None,
        campaigns=store.mapped_campaigns_for(client),
        blank_products=client_view.blank_products(client),
        products=products.catalog(),
        pacing=client_view.pacing(client, today),
        platforms=[(p, client_view.PLATFORM_LABELS.get(p, p)) for p in store.PLATFORMS],
        markups={m["platform"]: m for m in store.markups()},
        contact=_primary_contact(_client_name_for(client)),
        push_ready=_push_ready(),
        seo=_seo_gate(client, _client_name_for(client)),
        error=request.args.get("error", ""), saved=request.args.get("saved", ""),
    )


def _seo_gate(client: str, name: str) -> dict:
    """The organic section's gate, for the settings row: product yes/no,
    analytics linked yes/no, search yes/no, and why. Never raises -- a gate
    that 500s the page it explains is worse than none."""
    try:
        g = organic.gate(client, name)
    except Exception as exc:                   # noqa: BLE001
        g = {"gated": False, "product": False, "ga4": False, "gsc": False,
             "why": [f"the gate could not be read ({type(exc).__name__})"], "errors": []}
    from urllib.parse import quote
    g["seo_page"] = "/seo/client?name=" + quote(name or "")
    g["gsc_staff_note"] = organic.GSC_MISSING_STAFF
    return g


def _primary_contact(client_name: str) -> dict:
    """The client's primary contact off the Hub profile (hub/seo.get_profile),
    or {} -- never invented."""
    try:
        from hub import seo as hub_seo
        prof = hub_seo.get_profile(client_name)
    except Exception:                      # noqa: BLE001
        return {}
    contacts = prof.get("contacts") or []
    prim = next((c for c in contacts if c.get("primary")), contacts[0] if contacts else {})
    return {k: str(prim.get(k) or "").strip() for k in ("name", "email", "phone")} if prim else {}


def _push_ready() -> dict:
    """Whether a push to Smart 1 Suite can write the link at all: the
    contacts connection and the report-URL custom field. Three refusals kept
    apart, because they send somebody to three different places."""
    try:
        from hub import ghl_contacts
    except Exception as exc:               # noqa: BLE001
        return {"ok": False, "why": f"Smart 1 Suite contacts are unavailable ({type(exc).__name__})."}
    if not ghl_contacts.configured():
        return {"ok": False, "why": ghl_contacts.why_not()}
    ids = ghl_contacts.report_field_ids()
    if not ids.get("report_url"):
        return {"ok": False, "why": ("GHL_LEAD_REPORT_URL_FIELD_ID is not set, so there is no "
                                     "contact field to write the link into.")}
    return {"ok": True, "why": ""}


def _parse_markup_form(f) -> dict:
    out = {}
    for p in store.PLATFORMS:
        pct = (f.get(f"link_markup_{p}") or "").strip()
        cpm = (f.get(f"link_cpm_{p}") or "").strip()
        if pct and cpm:
            raise ValueError(f"{client_view.PLATFORM_LABELS.get(p, p)}: fill in Markup % "
                             "or Fixed CPM, not both.")
        if pct:
            out[p] = {"markup": str(store.markup_from_percent(pct))}
        elif cpm:
            out[p] = {"cpm": cpm}
    return out


def _parse_view_form(f) -> dict:
    labels = {p: (f.get(f"label_{p}") or "").strip() for p in store.PLATFORMS}
    return {
        "platform_labels": {p: v for p, v in labels.items() if v},
        "hidden_platforms": [p for p in store.PLATFORMS if f.get(f"hide_{p}")],
        "logo_url": (f.get("logo_url") or "").strip(),
        "rep_name": (f.get("rep_name") or "").strip(),
        "rep_email": (f.get("rep_email") or "").strip(),
        "name_products": bool(f.get("name_products")),
    }


@app.route("/client/<path:client>/link", methods=["POST"])
def client_link(client):
    """Create or regenerate the link, or save its settings -- one form,
    three buttons, `action` says which."""
    f = request.form
    action = (f.get("action") or "save").strip()
    name = _client_name_for(client)
    back = url_for("client_page", client=client)
    try:
        if action in ("create", "regenerate"):
            link = store.create_link(client, client_name=name, created_by=actor_name())
            _log("report_link_created" if action == "create" else "report_link_regenerated",
                 client=name, client_key=client, token=link.token,
                 detail=f"{'Created' if action == 'create' else 'Regenerated'} the live "
                        f"report link for {name}")
            return redirect(back + "?saved=link")
        link = store.link_for_client(client)
        if link is None:
            return redirect(back + "?error=Create+a+link+first.")
        link = store.update_link(link.token, show_spend=bool(f.get("show_spend")),
                                 markup_json=_parse_markup_form(f),
                                 view_json=_parse_view_form(f))
        client_view.forget(link.token)
        _log("report_link_settings", client=name, client_key=client,
             show_spend=bool(link.show_spend),
             detail=f"Report link settings saved for {name}: investment "
                    f"{'shown' if link.show_spend else 'hidden'}, "
                    f"{len(link.markups)} platform pricing override(s)")
        return redirect(back + "?saved=settings")
    except ValueError as exc:
        return redirect(back + "?error=" + str(exc).replace(" ", "+"))


@app.route("/client/<path:client>/campaign", methods=["POST"])
def client_campaign(client):
    """Staff override of what a mapped campaign is called on the client's
    page, and of its product. The display name is the one thing on the
    public page that comes from a platform, so it is the one place a
    vendor's name can leak; an empty box goes back to the vendor-stripped
    default."""
    f = request.form
    name = _client_name_for(client)
    back = url_for("client_page", client=client)
    try:
        row = store.set_display(f.get("platform", ""), f.get("account_id", ""),
                                f.get("campaign_id", ""),
                                display_name=f.get("display_name", ""),
                                product=f.get("product", ""))
    except ValueError as exc:
        return redirect(back + "?error=" + str(exc).replace(" ", "+"))
    if row is None or row.client != client:
        return redirect(back + "?error=That+campaign+is+not+mapped+to+this+client.")
    leak = products.forbidden_hits(row.display_name or "")
    for token in [l.token for l in [store.link_for_client(client)] if l]:
        client_view.forget(token)
    _log("campaign_display_saved", client=name, client_key=client,
         platform=row.platform, campaign_id=row.campaign_id, product=row.product or None,
         detail=f"{store.platform_label(row.platform)} campaign {row.campaign_id} shown as "
                f"{row.display_name!r} under {row.product or 'no product'} for {name}")
    if leak:
        return redirect(back + "?error=" + (
            f"Saved, but the display name still names a vendor ({', '.join(leak)}); "
            "the client will read it.").replace(" ", "+"))
    return redirect(back + "?saved=campaign")


@app.route("/client/<path:client>/push", methods=["POST"])
def client_push(client):
    """Write the public URL onto the client's Smart 1 Suite contact.

    Through hub/ghl_contacts.upsert(), which matches on email inside the
    location -- so the address has to be the client's own primary contact
    off the Hub profile. With none on file nothing is written and the page
    says so: an upsert on a made-up address creates a stray contact, which
    is the one outcome here that cannot be undone from this screen. It is
    deliberately NOT hub/leads.capture_and_deliver(): a client we bill is
    not a lead, and a row in the leads panel would say otherwise.
    """
    name = _client_name_for(client)
    back = url_for("client_page", client=client)
    link = store.link_for_client(client)
    if link is None:
        return redirect(back + "?error=Create+a+link+first.")
    ready = _push_ready()
    if not ready["ok"]:
        return redirect(back + "?error=" + ready["why"].replace(" ", "+"))
    contact = _primary_contact(name)
    if not contact.get("email"):
        return redirect(back + "?error=" + (
            "No primary contact email is on this client's Hub profile, so there is no "
            "contact to write the link onto. Add one on Client 360 first.").replace(" ", "+"))
    url = _public_url(link.token)
    from hub import ghl_contacts
    result = ghl_contacts.upsert({
        "source": "reports", "page": name[:120],
        "fields": {"email": contact["email"], "name": contact.get("name") or name,
                   "company": name, "phone": contact.get("phone") or ""},
        "meta": {"report_url": url},
    })
    if result.get("ok"):
        _log("report_link_pushed", client=name, client_key=client,
             contact_id=result.get("contact_id"), report=url,
             detail=f"Report link written onto {name}'s Smart 1 Suite contact ({contact['email']})")
        return redirect(back + "?saved=pushed")
    _log("report_link_push_failed", client=name, client_key=client,
         detail=str(result.get("error") or "")[:300])
    return redirect(back + "?error=" + str(result.get("error") or "Smart 1 Suite refused the write.")
                    .replace(" ", "+")[:400])


if __name__ == "__main__":             # pragma: no cover - local run
    app.run(port=5099, debug=True)
