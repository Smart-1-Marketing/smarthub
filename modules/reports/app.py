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
from datetime import date as _date, timedelta as _timedelta
from decimal import Decimal
from pathlib import Path

from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   request, url_for)

from hub.webargs import clamp_int

from . import automap, client_pdf, client_view, organic, pacing, products, quarantine, reconcile, store, suite_email, youtube

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
        pending=store.pending_count(),
        held=quarantine.counts(),
        drifting=reconcile.drifting(),
        facts=store.fact_count(),
        binding=store.binding(),
        markups=[m for m in store.markups()
                 if m["markup"] is not None or m["cpm"] is not None],
        budgets=store.budget_line_count(),
        clients=store.clients_with_campaigns(),
        native=_native_status(),
        refresh=_refresh_note(),
        history=_history_rows(),
        rate_card=products.rate_card_products(),
        health=_health_by_platform(),
        provider=_provider_gate(),
        ask_chips=_ask_chips("reports_trends"),
        error=request.args.get("error", ""), saved=request.args.get("saved", ""),
    )


def _ask_chips(placement: str, client: str = "") -> list:
    """Ask SmartHub recipe chips for one of this module's pages.

    Through hub.ask_recipes rather than a question typed into the template:
    this module has its own Jinja environment and cannot see the hub globals,
    so the list is built here and passed in as plain data. A Hub that could
    not be imported costs the page its chips and nothing else.
    """
    try:
        from hub import ask_recipes
        return ask_recipes.staff_chips(placement, client)
    except Exception:                                   # noqa: BLE001
        return []


def _provider_gate() -> dict:
    """Which platforms resolve against the provider schema and are waiting
    for somebody to confirm the map -- the reason a table that is plainly
    there has 'Not run yet' beside it. {platform: state} plus a count, or
    {} when the schema cannot be read: the index must render either way."""
    try:
        from . import normalize
        by = {c["platform"]: c for c in normalize.check_sources()}
    except Exception:                      # noqa: BLE001
        return {}
    waiting = {p: c["confirmation"]["state"] for p, c in by.items()
               if c["status"] == "resolved" and c["confirmation"]["state"] != "confirmed"}
    return {"waiting": waiting, "count": len(waiting)}


def _health_by_platform() -> dict:
    """health.feeds() keyed by platform, or {} -- the index must render
    when the health reading cannot."""
    try:
        from . import health
        return {p["platform"]: p for p in health.feeds()["platforms"]}
    except Exception:                      # noqa: BLE001
        return {}


NATIVE_PULLS = (("ttd", "ttd"), ("google", "google_ads_perf"),
                ("stackadapt", "stackadapt"), ("audiogo", "audiogo"),
                ("bing", "bing"), ("groundtruth", "groundtruth"),
                ("amazon_dsp", "amazon_dsp"), ("callrail", "callrail"))


def _refresh_note() -> dict:
    """The last manual refresh, from hub.scheduler's note -- {} when the Hub
    cannot be imported, and the index renders without the line."""
    try:
        from hub import scheduler
        return scheduler.refresh_note()
    except Exception:                      # noqa: BLE001
        return {}


@app.route("/refresh/<platform>", methods=["POST"])
def refresh_native(platform: str):
    """Pull one platform, or all of them, from its own API now. A POST,
    because it spends API calls, and a background one: the pull is minutes
    of platform requests and the click returns at once. The nightly job is
    what runs, narrowed to the platform asked for, so the button cannot
    disagree with 3 AM about what a pull is."""
    from urllib.parse import quote
    names = ([p for p, _m in NATIVE_PULLS] if platform == "all"
             else [p for p, _m in NATIVE_PULLS if p == platform])
    if not names:
        abort(404)
    try:
        from hub import scheduler
        res = scheduler.refresh_native(names, actor=actor_name())
    except Exception as exc:                   # noqa: BLE001
        app.logger.exception("reports: refresh could not start")
        return redirect(url_for("index") + "?error="
                        + quote(f"The refresh could not start ({type(exc).__name__})."))
    _log("reports_refresh", detail=("started " if res["started"] else "refused: ")
         + (", ".join(res["platforms"]) if res["started"] else res["note"]))
    key = "saved" if res["started"] else "error"
    return redirect(url_for("index") + f"?{key}=" + quote(res["note"]))


def _history_rows() -> list[dict]:
    """The History card's rows, or [] -- the index renders without it."""
    try:
        from . import backfill
        return backfill.rows()
    except Exception:                      # noqa: BLE001
        app.logger.exception("reports: history rows could not be read")
        return []


@app.route("/backfill/<platform>", methods=["POST"])
def backfill_pull(platform: str):
    """Thirty more days of history for one platform, or all of them, now.
    A POST because it spends API calls; background because a window is a
    full pull's worth of them. The nightly job runs the same code."""
    from urllib.parse import quote
    from . import backfill
    names = list(backfill.PULLS) if platform == "all" else [platform]
    if platform != "all" and platform not in backfill.PLATFORMS:
        abort(404)
    try:
        from hub import scheduler
        res = scheduler.backfill_now(names, actor=actor_name())
    except Exception as exc:                   # noqa: BLE001
        app.logger.exception("reports: history pull could not start")
        return redirect(url_for("index") + "?error="
                        + quote(f"The history pull could not start ({type(exc).__name__})."))
    _log("reports_backfill", detail=("started " if res["started"] else "refused: ")
         + (", ".join(res["platforms"]) if res["started"] else res["note"]))
    key = "saved" if res["started"] else "error"
    return redirect(url_for("index") + f"?{key}=" + quote(res["note"]) + "#history")


@app.route("/backfill/<platform>/nightly", methods=["POST"])
def backfill_nightly(platform: str):
    """Pull another window every night until the platform has nothing
    older -- on or off, for one platform or all of them."""
    from urllib.parse import quote
    from . import backfill
    on = (request.form.get("on") or "").strip().lower() in ("1", "true", "on", "yes")
    names = list(backfill.PULLS) if platform == "all" else [platform]
    if platform != "all" and platform not in backfill.PLATFORMS:
        abort(404)
    try:
        for name in names:
            backfill.set_nightly(name, on)
    except ValueError as exc:
        return redirect(url_for("index") + "?error=" + quote(str(exc)) + "#history")
    _log("reports_backfill_nightly", detail=f"{'on' if on else 'off'} for {', '.join(names)}")
    return redirect(url_for("index") + "?saved="
                    + quote(f"Nightly history {'on' if on else 'off'} for {', '.join(names)}.") + "#history")


def _native_status() -> list[dict]:
    """The native pulls' status lines, each in its own try: a status line
    that raises costs the index, and the index is where a failing pull is
    seen."""
    out = []
    for label, mod in NATIVE_PULLS:
        try:
            import importlib
            m = importlib.import_module(f"modules.reports.{mod}")
            from . import provider_fields
            out.append({"platform": label, "label": store.platform_label(label),
                        "check_label": provider_fields.CHECK_LABELS.get(label), **m.status()})
        except Exception as exc:               # noqa: BLE001
            out.append({"platform": label, "label": store.platform_label(label), "connected": False,
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
    sources = normalize.check_sources(tables)
    # A sample row under each resolved platform's map: the confirmation is
    # taken against real values, never against plausible names.
    samples = {s["platform"]: normalize.sample_row(s["platform"])
               for s in sources if s["status"] == "resolved"}
    from . import provider_fields
    return render_template(
        "reports_provider_check.html",
        schema=provider_map.schema(), tables=tables,
        sources=sources, samples=samples, subnav=provider_fields.nav(""),
        error=request.args.get("error", ""), saved=request.args.get("saved", ""))


@app.route("/provider-check/<platform>")
def provider_page(platform: str):
    """One provider: the field map its pull expects, what the platform
    documents that the pull does not read, and what has to be true on
    Render for it to run. Drawn from provider_fields.py, which reads the
    map off the module that does the pull; the live check page, where one
    exists, is linked rather than repeated. An unknown platform is a 404
    with the submenu on it, not a 500."""
    from . import normalize, provider_fields
    platform = (platform or "").strip().lower()
    try:
        tables = normalize.schema_tables()
    except Exception:                      # noqa: BLE001 - the page must render without the schema
        tables = {}
    pg = provider_fields.page(platform, tables)
    if pg is None:
        return render_template("reports_provider_page.html", pg=None, subnav=provider_fields.nav(""),
                               unknown=platform, error="", saved=""), 404
    return render_template("reports_provider_page.html", pg=pg, subnav=pg["nav"], unknown="",
                           error=request.args.get("error", ""), saved=request.args.get("saved", ""))


@app.route("/provider-check/confirm", methods=["POST"])
def provider_confirm():
    """A person has looked at the sample row under a platform's map and
    stands behind it. Recorded against the map's fingerprint, so editing
    the map afterwards retires this rather than carrying it onto columns
    nobody looked at. Only a RESOLVED map can be confirmed -- confirming a
    map whose columns are not on the table is confirming nothing."""
    from . import normalize, provider_map
    platform = (request.form.get("platform") or "").strip().lower()
    back = url_for("provider_check")
    if platform not in provider_map.PLATFORM_SOURCES:
        return redirect(back + "?error=" + f"Unknown platform {platform!r}.".replace(" ", "+"))
    src = next(c for c in normalize.check_sources() if c["platform"] == platform)
    if src["status"] != "resolved":
        return redirect(back + "?error=" + (
            f"{src['label']} does not resolve ({src['status'].replace('_', ' ')}), so there is "
            "nothing to confirm yet.").replace(" ", "+"))
    row = store.confirm_provider(platform, by=actor_name(),
                                 fingerprint=provider_map.fingerprint(platform), table=src["table"])
    _log("provider_map_confirmed", platform=platform, table=src["table"],
         fingerprint=row["fingerprint"],
         detail=f"{src['label']}'s provider column map ({src['table']}) confirmed against a "
                f"sample row; the hourly normalize reads it from the next run")
    return redirect(back + f"?saved={platform}")


@app.route("/provider-check/withdraw", methods=["POST"])
def provider_withdraw():
    """Take a confirmation back: the normalize stops reading the platform
    on its next run and says so on the watermark."""
    platform = (request.form.get("platform") or "").strip().lower()
    back = url_for("provider_check")
    try:
        gone = store.withdraw_provider(platform)
    except ValueError as exc:
        return redirect(back + "?error=" + str(exc).replace(" ", "+"))
    if gone is None:
        return redirect(back + "?error=" + f"{store.platform_label(platform)} was not confirmed.".replace(" ", "+"))
    _log("provider_map_withdrawn", platform=platform,
         detail=f"{store.platform_label(platform)}'s provider column map confirmation "
                f"(by {gone['by']}) withdrawn; the normalize stops reading it")
    return redirect(back + f"?saved={platform}-withdrawn")


# --------------------------------------------------------------- reconcile
@app.route("/reconcile")
def reconcile_page():
    """Our month against the platform's own, per platform, as the nightly
    run last measured it -- and a button to measure now."""
    rows = store.reconcile_rows(months=3)
    months = sorted({r["month"] for r in rows}, reverse=True)
    return render_template(
        "reports_reconcile.html",
        rows=rows, months=months, labels=reconcile.STATE_LABELS,
        tolerance=reconcile.TOLERANCE_PCT, source=reconcile.TOLERANCE_SOURCE,
        not_measurable=reconcile.NOT_MEASURABLE,
        error=request.args.get("error", ""), saved=request.args.get("saved", ""))


@app.route("/reconcile/run", methods=["POST"])
def reconcile_run():
    """Measure now. A POST, because it reaches Google and the platforms:
    a GET that spends API calls is one a reload or a prefetch fires."""
    try:
        res = reconcile.run(actor=actor_name())
    except Exception as exc:                   # noqa: BLE001
        app.logger.exception("reports: reconcile run failed")
        return redirect(url_for("reconcile_page") + "?error="
                        + f"The reconcile could not run ({type(exc).__name__}).".replace(" ", "+"))
    _log("reports_reconcile_run",
         detail=f"reconciled {', '.join(res['months'])}: {res['agree']} agree, {res['drift']} drift, "
                f"{res['not_measured']} not measured")
    return redirect(url_for("reconcile_page") + "?saved=run")


# -------------------------------------------------------------- quarantine
@app.route("/quarantine")
def quarantine_page():
    """Rows a sync proposed that cannot be true, held for a person: the
    figures, the rule each broke with the numbers behind it, how many
    hourly runs have proposed it, and Accept / Discard."""
    return render_template(
        "reports_quarantine.html",
        held=quarantine.held(), decided=quarantine.decided(),
        rules=quarantine.RULES, source=quarantine.RULES_SOURCE,
        multiplier=quarantine.SPIKE_MULTIPLIER, baseline_days=quarantine.BASELINE_DAYS,
        baseline_min_days=quarantine.BASELINE_MIN_DAYS,
        baseline_min_spend=quarantine.BASELINE_MIN_SPEND,
        error=request.args.get("error", ""), saved=request.args.get("saved", ""))


@app.route("/quarantine/decide", methods=["POST"])
def quarantine_decide():
    """Accept writes the held row -- that row -- into the fact table;
    discard drops it. Either remembers the figures, so the same figure
    arriving again on the next sync is not raised again."""
    f = request.form
    back = url_for("quarantine_page")
    try:
        row = quarantine.decide(f.get("platform", ""), f.get("account_id", ""),
                                f.get("campaign_id", ""), f.get("date", ""),
                                action=f.get("action", ""), by=actor_name())
    except ValueError as exc:
        return redirect(back + "?error=" + str(exc).replace(" ", "+"))
    _log("quarantine_" + row["status"], platform=row["platform"], campaign_id=row["campaign_id"],
         day=row["date"], rule=row["rule"],
         detail=f"{row['platform_label']} campaign {row['campaign_name'] or row['campaign_id']} "
                f"on {row['date']} ({row['rule_label']}: {row['reason']}) {row['status']}")
    if row["status"] == "accepted":
        # The accepted row may be a confirmed campaign's, and the client's
        # page holds its answer for a quarter of an hour per worker.
        m = store.campaign_map(row["platform"], row["account_id"], row["campaign_id"])
        if m is not None:
            link = store.link_for_client(m["client"])
            if link:
                client_view.forget(link.token)
    return redirect(back + f"?saved={row['status']}")


# ---------------------------------------------------------------- upload
# A CSV export is the one feed every platform has, whatever its API does.
# 20 MB is a year of campaign-days for the biggest account here several
# times over; a file past it is refused by name rather than read into
# memory on a worker two gunicorn processes share.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@app.route("/upload", methods=["POST"])
def upload_csv():
    """A platform's own CSV export, into the fact table, as ``source="csv"``.

    The parser was written for AudioGo and reads the ordinary columns every
    export carries, so it is the one reader here for every platform; the
    platform is the form's and is checked before anything lands. The rows
    go through ``store.upsert_rows`` -- the one door, so a row that cannot
    be true is held in quarantine rather than filed -- and the watermark
    says ``csv`` wrote it, because a hand upload is not the sync and the
    index should not read as though the feed had run. The result is named
    in the notice: written, held, skipped, all three, because "uploaded" is
    a claim about the file and the client's page shows what was written.
    """
    from datetime import date
    from urllib.parse import quote
    from .parsers import audiogo_csv
    back = url_for("index")

    def refuse(msg: str):
        return redirect(back + "?error=" + quote(msg))

    try:
        platform = store.check_platform(request.form.get("platform", ""))
    except ValueError as exc:
        return refuse(str(exc))
    f = request.files.get("file")
    if f is None or not (f.filename or "").strip():
        return refuse("Choose a CSV file to upload.")
    name = (f.filename or "").strip()[:160]
    data = f.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return refuse(f"{name} is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB; "
                      "split the export by month and upload each part.")
    parsed = audiogo_csv.parse(data, platform=platform)
    if parsed["error"]:
        return refuse(f"{name}: {parsed['error']}")
    if not parsed["rows"]:
        return refuse(f"{name} carried no usable row ({parsed['skipped']} skipped for "
                      "a missing day, account or campaign).")
    report: dict = {}
    written = store.upsert_rows(parsed["rows"], report=report, today=date.today())
    store.record_sync(platform, rows=written, error="", source="csv")
    # A written row may be a confirmed campaign's, and the client's page
    # holds its answer for a quarter of an hour per worker.
    touched = {(r["platform"], r["account_id"], r["campaign_id"]) for r in parsed["rows"]}
    for m in store.campaign_maps_by_key(touched):
        link = store.link_for_client(m["client"])
        if link:
            client_view.forget(link.token)
    held = int(report.get("quarantined") or 0)
    _log("csv_uploaded", platform=platform, filename=name, rows=written,
         quarantined=held, skipped=int(parsed["skipped"]), size=len(data),
         detail=f"{store.platform_label(platform)}: {written} campaign-days written from "
                f"{name}, {held} held in quarantine, {parsed['skipped']} skipped")
    msg = (f"{name}: {written} campaign-day{'' if written == 1 else 's'} written for "
           f"{store.platform_label(platform)}")
    if held:
        msg += f", {held} held in quarantine for a person to decide"
    if parsed["skipped"]:
        msg += f", {parsed['skipped']} skipped for a missing day, account or campaign"
    return redirect(back + "?saved=" + quote(msg + "."))


@app.route("/audiogo-check")
def audiogo_check():
    """What AudioGo's configured endpoint actually answers, against what
    audiogo_map.py expects -- the same pattern as provider-check. Calls the
    endpoint for yesterday and prints the raw JSON keys (the key itself
    never reaches a body) so the real names can be pasted into the map."""
    from . import audiogo, provider_fields
    chk = audiogo.check()
    return render_template("reports_audiogo_check.html", chk=chk,
                           subnav=provider_fields.nav("audiogo"),
                           unread=_unread("audiogo", chk), documented=provider_fields.DOCUMENTED["audiogo"])


@app.route("/groundtruth-check")
def groundtruth_check():
    """What GroundTruth's configured endpoint actually answers, against what
    groundtruth_map.py expects -- the audiogo-check pattern, for the
    platform whose documentation the Hub's own environment cannot reach.
    Calls nothing while GROUND_TRUTH_API_BASE is unset: the key is never
    sent to a host nobody has confirmed."""
    from . import groundtruth, provider_fields
    chk = groundtruth.check()
    return render_template("reports_groundtruth_check.html", chk=chk,
                           subnav=provider_fields.nav("groundtruth"),
                           unread=_unread("groundtruth", chk),
                           documented=provider_fields.DOCUMENTED["groundtruth"])


@app.route("/callrail-check")
def callrail_check():
    """What CallRail's calls endpoint actually answers, against what
    callrail_map.py expects -- the groundtruth-check pattern for call
    tracking. Lists the accounts the key sees, reads one page of
    yesterday's calls for the first, and prints the raw keys with the
    caller's own details masked. Calls nothing while CALLRAIL_API_BASE is
    unset: the key is never sent to a host nobody has confirmed."""
    from . import callrail, provider_fields
    chk = callrail.check()
    return render_template("reports_callrail_check.html", chk=chk,
                           subnav=provider_fields.nav("callrail"),
                           unread=_unread("callrail", chk),
                           documented=provider_fields.DOCUMENTED["callrail"])


@app.route("/amazon-check")
def amazon_check():
    """What the Amazon DSP entity actually answers, against what
    amazon_dsp.FIELD_MAP expects -- the groundtruth-check pattern, for the
    platform whose paths Amazon is mid-way through moving. Walks the
    preflight ladder so an unmet claim is named as itself rather than as a
    refusal, and calls nothing at all while the connection is unconfigured
    or unconsented: a credential is not sent to find out whether it is set.
    """
    from . import amazon_dsp, provider_fields
    chk = amazon_dsp.check()
    return render_template("reports_amazon_check.html", chk=chk,
                           subnav=provider_fields.nav("amazon_dsp"),
                           unread=_unread("amazon_dsp", chk),
                           documented=provider_fields.DOCUMENTED["amazon_dsp"])


def _unread(platform: str, chk: dict) -> dict:
    """Answered and not read, for a live check page: the row keys the
    endpoint returned that the map does not name. ``measured`` is False
    until a row has come back, and the box says so rather than printing an
    empty list as a clean bill."""
    from . import provider_fields
    keys = []
    answer = chk.get("answer") if isinstance(chk, dict) else None
    if isinstance(answer, dict):
        keys = list(answer.get("row_keys") or [])
    elif isinstance(chk.get("sample"), dict):        # the Amazon page carries one raw row
        keys = list(chk["sample"].keys())
    # "names", not "keys": a dict key called keys is shadowed by dict.keys in Jinja.
    return {"measured": bool(keys), "names": provider_fields.unread_from_answer(platform, keys)}


# ---------------------------------------------------------------- mapping
def _resolve_client(name: str, key: str) -> tuple[str, str]:
    """The (key, display name) a mapping or a line is filed under. The rule
    is ``store.resolve_client`` -- one reader for these two forms and for
    the proposal adapter, which has no request and cannot import this
    app. Kept under its old name so the two call sites read as they did."""
    return store.resolve_client(name, key)


@app.route("/unmapped")
def unmapped():
    days = clamp_int(request.args.get("days"), 30, 1, 365)
    limit = clamp_int(request.args.get("limit"), 200, 1, 1000)
    rows = store.unmapped_campaigns(days=days, limit=limit)
    pending = store.pending_mappings()
    # The likeness beside every row: the picker opens on the client the
    # name looks like, and a pending row filed by likeness says why. Laid
    # over at read time, never stored -- a registry that cannot be read
    # leaves the lists empty and the page says so.
    likeness = automap.annotate(rows, pending)
    # ?client=<key> works one client's queue: the proposals filed under
    # them and the unmapped campaigns that look like theirs. The pacing
    # board's "look like theirs" link lands here.
    client_filter = (request.args.get("client") or "").strip()[:200]
    client_filter_name = ""
    if client_filter:
        pending = [m for m in pending if m.get("client") == client_filter]
        rows = [r for r in rows if any(s["key"] == client_filter for s in r.get("suggestions") or ())]
        client_filter_name = (next((m.get("client_name") for m in pending if m.get("client_name")), "")
                              or next((s["name"] for r in rows for s in r["suggestions"] if s["key"] == client_filter), "")
                              or _client_name_for(client_filter))
    return render_template(
        "reports_unmapped.html",
        rows=rows, pending=pending,
        client_filter=client_filter, client_filter_name=client_filter_name,
        likeness_error=likeness.get("error", ""),
        file_pct=int(round(automap.FUZZY_FILE_SCORE * 100)),
        aliases=store.campaign_aliases(), alias_file_count=automap.ALIAS_FILE_COUNT,
        days=days, shape=store.RENAME_SHAPE,
        products=products.catalog(),
        defaults=products.DEFAULT_PRODUCT_FOR_PLATFORM,
        recent=store.mapped_campaigns(limit=25),
        error=request.args.get("error", ""),
        saved=request.args.get("saved", ""),
    )


@app.route("/unmapped", methods=["POST"])
def map_campaign():
    """File a campaign under a client, or move one: the same press on an
    unmapped row and on a proposal the auto-mapper filed under the wrong
    client (the queue's and the client page's Move to). A person's mapping
    replaces the row and is confirmed by the making, so a moved proposal
    is on the right client's page from now and the wrong client's never
    had it."""
    f = request.form
    back = _mapping_back(f)
    client_key, client_name = _resolve_client(f.get("client_name", ""),
                                              f.get("client_key", ""))
    if not client_name and not client_key:
        return redirect(back + "?error=Pick+a+client+first.")
    try:
        before = store.campaign_map(f.get("platform", ""), f.get("account_id", ""),
                                    f.get("campaign_id", ""))
    except ValueError:
        before = None
    try:
        row = store.map_campaign(
            f.get("platform", ""), f.get("account_id", ""), f.get("campaign_id", ""),
            client=client_key, client_name=client_name,
            product=f.get("product", ""), mapped_by=actor_name(),
            campaign_name=f.get("campaign_name", ""),
            display_name=f.get("display_name") or None)
    except ValueError as exc:
        return redirect(back + "?error=" + str(exc).replace(" ", "+"))
    moved = before is not None and before.get("client") != row.client
    automap.forget_likely()
    # A person's filing teaches what this campaign calls the client.
    automap.learn(f.get("campaign_name") or row.display_name or "", client=row.client,
                  client_name=client_name or row.client_name or "", by=actor_name())
    # client= so the mapping lands on that client's 360 activity.
    _log("campaign_mapped", client=client_name or client_key,
         client_key=row.client, platform=row.platform,
         campaign_id=row.campaign_id, product=row.product or None,
         detail=f"{store.platform_label(row.platform)} campaign "
                f"{f.get('campaign_name') or row.campaign_id} mapped to "
                f"{client_name or client_key}"
                + (f" (moved from {before.get('client_name') or before.get('client')}"
                   f"{', which the auto-mapper had proposed' if before.get('pending') else ''})"
                   if moved else ""))
    if moved:
        # The page that was showing the proposal is a page whose cache
        # holds a campaign that is no longer theirs.
        for token in [l.token for l in [store.link_for_client(before["client"])] if l]:
            client_view.forget(token)
    if f.get("back") == "client":
        return redirect(back + "?saved=" + ("moved" if moved else "campaign"))
    return redirect(url_for("unmapped", saved=("moved" if moved else row.campaign_id)))


def _mapping_back(f) -> str:
    """Where a Confirm / Not theirs press goes back to: the client's own
    staff page when it was pressed there, the queue otherwise. Read from a
    form field naming which, never a URL the browser supplied."""
    if f.get("back") == "client" and f.get("client"):
        return url_for("client_page", client=f.get("client"))
    return url_for("unmapped")


@app.route("/unmapped/confirm", methods=["POST"])
def confirm_mapping():
    """A person stands behind a mapping the auto-mapper proposed. From this
    press the campaign's rows reach the client's page, its PDF and its
    data -- store.facts_for() reads confirmed mappings and nothing else."""
    f = request.form
    back = _mapping_back(f)
    try:
        row = store.confirm_mapping(f.get("platform", ""), f.get("account_id", ""),
                                    f.get("campaign_id", ""), by=actor_name())
    except ValueError as exc:
        return redirect(back + "?error=" + str(exc).replace(" ", "+"))
    if row is None:
        return redirect(back + "?error=That+campaign+is+not+mapped.")
    name = row.client_name or row.client
    try:
        taught_from = (store.campaign_map(row.platform, row.account_id, row.campaign_id) or {}).get("campaign_name") or ""
    except Exception:                  # noqa: BLE001 - a lesson is not the confirmation
        taught_from = ""
    automap.learn(taught_from, client=row.client, client_name=name, by=actor_name())
    _log("campaign_confirmed", client=name, client_key=row.client,
         platform=row.platform, campaign_id=row.campaign_id, product=row.product or None,
         detail=f"{store.platform_label(row.platform)} campaign {row.campaign_id} confirmed "
                f"as {name}'s ({row.product or 'no product'}); it is on their page from now")
    return redirect(back + "?saved=confirmed")


@app.route("/unmapped/confirm-many", methods=["POST"])
def confirm_many():
    """Confirm the proposals a person ticked, each on its own row of the
    activity log: a bulk press is still one person standing behind each
    campaign, and the record says so per campaign. A key that is not
    mapped or cannot be read is counted and named, never a stop for the
    rest."""
    f = request.form
    keys = [k for k in f.getlist("keys") if k]
    back = url_for("unmapped") + (("?client=" + f.get("client", "")) if f.get("client") else "")
    if not keys:
        return redirect(back + ("&" if "?" in back else "?") + "error=Tick+at+least+one+campaign+first.")
    done, skipped = 0, []
    for key in keys[:500]:
        parts = key.split("|", 2)
        if len(parts) != 3:
            skipped.append(key)
            continue
        platform, account_id, campaign_id = parts
        try:
            row = store.confirm_mapping(platform, account_id, campaign_id, by=actor_name())
        except ValueError:
            row = None
        if row is None:
            skipped.append(campaign_id)
            continue
        name = row.client_name or row.client
        try:
            taught_from = (store.campaign_map(row.platform, row.account_id, row.campaign_id) or {}).get("campaign_name") or ""
        except Exception:              # noqa: BLE001 - a lesson is not the confirmation
            taught_from = ""
        automap.learn(taught_from, client=row.client, client_name=name, by=actor_name())
        _log("campaign_confirmed", client=name, client_key=row.client,
             platform=row.platform, campaign_id=row.campaign_id, product=row.product or None,
             detail=f"{store.platform_label(row.platform)} campaign {row.campaign_id} confirmed "
                    f"as {name}'s ({row.product or 'no product'}) in a batch of {len(keys)}; "
                    f"it is on their page from now")
        done += 1
    automap.forget_likely()
    sep = "&" if "?" in back else "?"
    if skipped:
        return redirect(back + sep + f"saved=confirmed-{done}&error=" +
                        f"{len(skipped)}+not+confirmed+(not+mapped+or+unreadable):+{'+'.join(skipped[:5])}")
    return redirect(back + sep + f"saved=confirmed-{done}")


@app.route("/unmapped/refuse", methods=["POST"])
def refuse_mapping():
    """Not theirs: the proposal is deleted, the refusal remembered so the
    auto-mapper does not re-file the same name under the same client, and
    the campaign is back on the unmapped queue for a person to file."""
    f = request.form
    back = _mapping_back(f)
    try:
        gone = store.refuse_mapping(f.get("platform", ""), f.get("account_id", ""),
                                    f.get("campaign_id", ""), by=actor_name())
    except ValueError as exc:
        return redirect(back + "?error=" + str(exc).replace(" ", "+"))
    if gone is None:
        return redirect(back + "?error=That+campaign+is+not+mapped.")
    name = gone["client_name"] or gone["client"]
    automap.forget_likely()
    automap.forget(gone["campaign_name"], client=gone["client"], client_name=name)
    _log("campaign_refused", client=name, client_key=gone["client"],
         platform=gone["platform"], campaign_id=gone["campaign_id"],
         product=gone["product"] or None,
         detail=f"{store.platform_label(gone['platform'])} campaign "
                f"{gone['campaign_name'] or gone['campaign_id']} is not {name}'s; "
                f"the auto-mapper's filing was refused and it is back on the unmapped queue")
    for token in [l.token for l in [store.link_for_client(gone["client"])] if l]:
        client_view.forget(token)
    return redirect(url_for("unmapped") + "?saved=refused" if f.get("back") != "client"
                    else back + "?saved=refused")


@app.route("/unmapped/alias/forget", methods=["POST"])
def forget_alias():
    """Forget one learned name for one client. The queue's Forget button."""
    f = request.form
    alias, client = (f.get("alias") or "").strip(), (f.get("client") or "").strip()
    if not (alias and client):
        return redirect(url_for("unmapped") + "?error=Which+alias%3F")
    if not store.forget_alias(alias, client):
        return redirect(url_for("unmapped") + "?error=That+alias+is+not+on+file.")
    _log("campaign_alias_forgotten", client=f.get("client_name") or client, client_key=client,
         detail=f"the learned campaign name {alias!r} for {f.get('client_name') or client} was forgotten")
    return redirect(url_for("unmapped") + "?saved=forgotten#aliases")


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
def _rule_text(markup, cpm) -> str:
    """One pricing rule as a person reads it: "15%", "$12.50 CPM" or "at cost"."""
    if markup is not None:
        return f"{store._plain(Decimal(str(markup)) * 100)}%"
    if cpm is not None:
        return f"${Decimal(str(cpm)):,.2f} CPM"
    return "at cost"


def _markup_page(**extra):
    try:
        pages = store.pages_on_platform_rule()
    except Exception:                      # noqa: BLE001 - a count is not the page
        pages = None
    return render_template("reports_markup.html", rows=store.markups(),
                           pages=pages, max_pct=store._plain(store.MARKUP_MAX_PCT),
                           cpm_max=store._plain(store.CPM_MAX),
                           error=request.args.get("error", ""),
                           saved=request.args.get("saved", ""), **extra)


@app.route("/markup")
def markup():
    return _markup_page()


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
            cpm_amount = store.check_cpm(cpm) if cpm else None
        except ValueError as exc:
            return redirect(url_for("markup", error=f"{store.platform_label(p)}: {exc}"))
        wanted.append((p, markup_frac, cpm_amount))
    # What this save changes, and which live client pages read each changed
    # rule -- said before anything is written. A platform rule is global:
    # a markup saved here moves the Investment figure on every client page
    # that reads it, at once, with nothing on those pages saying so. A
    # change reaching at least one such page is shown and confirmed rather
    # than saved on the first press; a change reaching none saves as it
    # always did, because a confirmation on every press is one nobody reads.
    current = {m["platform"]: m for m in store.markups()}
    reach = []
    try:
        pages = store.pages_on_platform_rule()
    except Exception:                      # noqa: BLE001
        pages = {}
    for p, frac, cpm in wanted:
        was = current.get(p) or {}
        was_m = Decimal(str(was["markup"])) if was.get("markup") is not None else None
        was_c = Decimal(str(was["cpm"])) if was.get("cpm") is not None else None
        if (frac, cpm) == (was_m, was_c):
            continue
        on = pages.get(p) or []
        if on:
            reach.append({"platform": p, "label": store.platform_label(p),
                          "before": _rule_text(was_m, was_c), "after": _rule_text(frac, cpm),
                          "pages": on})
    if reach and (f.get("confirm") or "") != "1":
        posted = [(k, v) for k, v in f.items() if k.startswith(("markup_", "cpm_"))]
        return _markup_page(pending=reach, posted=posted)
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
                           total=store.budget_line_count(),
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
                           labels=pacing.BAND_LABELS, platform_label=store.platform_label,
                           ask_chips=_ask_chips("reports_pacing",
                                                (a.get("client") or "")[:200]))


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
        pdf = client_view.pdf_bytes(link, period)
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
    held = quarantine.held_for_client(client)
    return render_template(
        "reports_client.html",
        client=client, client_name=_client_name_for(client),
        link=link.as_dict() if link else None,
        public_url=_public_url(link.token) if link else "",
        totals=client_view.staff_totals(client, rng["start"], rng["end"], link),
        period=rng, period_key=period,
        preview=client_view.aggregate(link, period) if link else None,
        campaigns=store.mapped_campaigns_for(client),
        pending=[m for m in store.mapped_campaigns_for(client) if m.get("pending")],
        # The notice names three held rows and says how many more there are.
        # That count is arithmetic the route does, not the template: CodeQL
        # reads {{ a|b - 3 }} in an HTML file as the filter call (b - 3)(a) and
        # reports a number being invoked, on every push, for ever.
        held=held, held_more=max(0, len(held) - 3),
        blank_products=client_view.blank_products(client),
        products=products.catalog(),
        pacing=client_view.pacing(client, today),
        platforms=[(p, client_view.PLATFORM_LABELS.get(p, p)) for p in store.PLATFORMS],
        markups={m["platform"]: m for m in store.markups()},
        contact=_primary_contact(_client_name_for(client)),
        push_ready=_push_ready(),
        seo=_seo_gate(client, _client_name_for(client)),
        youtube=youtube.staff_gate(client, _client_name_for(client)),
        email=suite_email.staff_gate(client, _client_name_for(client)),
        summary_month=_summary_month_for(rng),
        summary_saved=(store.exec_summary_for(link, _summary_month_for(rng))
                       if link else None),
        error=request.args.get("error", ""), saved=request.args.get("saved", ""),
    )


def _summary_month_for(rng: dict) -> str:
    """Which month the summary box on the staff page is about -- the same
    reading client_view uses, so the form and the client's page cannot
    disagree about which month is being edited."""
    return client_view._summary_month(rng, _date.today())


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
            out[p] = {"cpm": str(store.check_cpm(cpm))}
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


# --------------------------------------------------------- executive summary
# Two presses, never one. Generating writes nothing; saving publishes what is
# on the screen, under the name of the person who pressed it. The client's own
# page reads the saved row and can reach no AI call at all -- a public route
# that could is a stranger spending our credits, and a paragraph about a
# client's results that nobody read before it was published is the worse half
# of the same problem.

@app.post("/client/<path:client>/summary/draft")
def client_summary_draft(client):
    """Ask SmartHub for this month's executive summary. Staff only, nothing
    saved: the answer comes back to the screen for a person to read, edit or
    throw away."""
    link = store.link_for_client(client)
    if link is None:
        return jsonify({"ok": False, "error": "This client has no live link yet."}), 400
    body = request.get_json(silent=True) or {}
    month = str(request.form.get("month") or body.get("month") or "")[:7]
    if not store._month_key(month):
        return jsonify({"ok": False, "error": "Choose the month the summary is about."}), 400
    try:
        from hub import ask_recipes, ask_smarthub, demo as hub_demo
        from hub import identity as hub_identity
    except Exception as exc:                        # noqa: BLE001
        return jsonify({"ok": False,
                        "error": f"Ask SmartHub is unavailable ({type(exc).__name__})."}), 503
    # This route reaches ask_smarthub.ask() directly rather than through
    # /api/ask-smarthub, so it carries that route's demo gate itself: a demo
    # session spending real AI credits is the thing hub/demo.py exists to
    # stop, and a second door into the same call is how a gate gets bypassed
    # while every screen reports success. The role comes off the cookie the
    # mounted request already carries, because the environ holds the user's
    # name and not their role.
    try:
        hub_demo.guard("openai.text", hub_identity.user_from_environ(request.environ))
    except hub_demo.DemoBlocked as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    # Which recipe this button drafts is the `client_dashboard` PLACEMENT's
    # answer, not a key typed here. The placement is then load-bearing rather
    # than a claim in hub/ask_recipes.py that nothing reads -- and changing
    # which recipe belongs on a client's dashboard changes this button.
    offered = ask_recipes.for_placement("client_dashboard",
                                        ask_recipes.BASELINE_STAFF)
    if not offered:
        return jsonify({"ok": False,
                        "error": "No summary recipe is available for the "
                                 "client dashboard."}), 503
    recipe = offered[0]
    name = _client_name_for(client)
    question = ask_recipes.fill(recipe, name,
                                period_text=_period_for_month(month))
    try:
        result = ask_smarthub.ask(
            question, role="member", actor=actor_name(),
            context={"client": name, "path": request.path,
                     "page_title": "Client report"},
            recipe=recipe.key)
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    except RuntimeError as exc:
        text = str(exc)
        if text.startswith("RATE_LIMIT:"):
            return jsonify({"ok": False,
                            "error": "Too many questions just now. Try again shortly."}), 429
        return jsonify({"ok": False, "error": "Ask SmartHub could not answer."}), 503
    except Exception as exc:                        # noqa: BLE001
        return jsonify({"ok": False,
                        "error": f"Ask SmartHub could not answer ({type(exc).__name__})."}), 503
    _log("reports_summary_drafted", client=name, client_key=client, month=month)
    return jsonify({"ok": True, "month": month, "text": result.get("answer") or "",
                    "sources": result.get("sources") or [],
                    "note": ("This is a draft. Read it, edit anything that is not "
                             "right, and press Save to publish it on the client's "
                             "page. Nothing is published until you do.")})


@app.post("/client/<path:client>/summary")
def client_summary_save(client):
    """Publish, or take down, the words that are on the screen."""
    link = store.link_for_client(client)
    if link is None:
        return _back(client, error="This client has no live link yet.")
    f = request.form
    month = (f.get("month") or "")[:7]
    action = (f.get("action") or "save").strip()
    try:
        if action == "remove":
            store.clear_exec_summary(link.token, month)
            _log("reports_summary_removed", client=_client_name_for(client),
                 client_key=client, month=month)
            saved = "The summary was taken off the client's page."
        else:
            entry = store.save_exec_summary(
                link.token, month=month, text=f.get("text", ""), by=actor_name())
            _log("reports_summary_saved", client=_client_name_for(client),
                 client_key=client, month=month, chars=len(entry["text"]))
            saved = "The summary is on the client's page."
    except ValueError as exc:
        return _back(client, error=str(exc))
    # The link's updated_at is in the client page's cache key, so the saved
    # words reach both workers at once rather than one of them in fifteen
    # minutes; forget() empties this worker's copy so the preview beside the
    # form is right immediately.
    client_view.forget(link.token)
    return _back(client, saved=saved)


def _back(client: str, *, error: str = "", saved: str = ""):
    """Back to this client's staff page, carrying one sentence."""
    from urllib.parse import quote
    target = url_for("client_page", client=client)
    if error:
        return redirect(f"{target}?error={quote(error[:200])}")
    return redirect(f"{target}?saved={quote(saved[:200])}")


def _period_for_month(month: str) -> str:
    """How a month is named in the question the summary is generated from.

    Last month is called "last month", because hub/periods.py resolves that
    name and the planner is told to use it. Any other month carries its own
    ISO dates in the words, computed HERE -- the planner is told never to
    compute a date, so a question about an arbitrary month has to hand over
    the days it means rather than leave the model to work them out.
    """
    today = _date.today()
    previous = today.replace(day=1) - _timedelta(days=1)
    if month == f"{previous:%Y-%m}":
        return "last month"
    key = store._month_key(month)
    if not key:
        return "last month"
    import calendar
    year, number = int(key[:4]), int(key[5:7])
    start = _date(year, number, 1)
    end = _date(year, number, calendar.monthrange(year, number)[1])
    return (f"{start:%B %Y} (the period from {start.isoformat()} "
            f"to {end.isoformat()})")


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
