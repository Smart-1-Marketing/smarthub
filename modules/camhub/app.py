"""CamHub -- staff screens under the Hub login, the cam page public.

Mounted at /tools/camhub by wsgi.py. `PUBLIC_PREFIXES` names the two routes
a stranger may read: the cam page itself and its JSON, both served from
cache and neither able to change anything. The page is meant to be reached
on the client's own domain by a reverse proxy to this path, which is why it
sets its own X-Robots-Tag: the Hub's middleware stamps every other response
`noindex`, and a conditions page exists to be indexed.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, url_for

from hub.leads import client_ip

from . import (builder, outbox, portal as portal_lib, render as page_render,
               reports, seeds, sponsors, store, tracking)
from .models import boot_error, init_db

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"),
            static_folder=str(BASE_DIR / "static"))
log = logging.getLogger("hub")

PUBLIC_PREFIXES = ("/cam/", "/go/", "/portal/")
MOUNT = "/tools/camhub"

init_db()


def _user() -> str:
    return str(request.environ.get("smart1.user") or
               request.headers.get("X-Smart1-User") or "SmartHub user")[:120]


def _activity(type_: str, **extra) -> None:
    try:
        from hub import audit
        audit.log("camhub", type_, actor=_user(), **extra)
    except Exception:  # noqa: BLE001 -- activity logging must never break the action
        pass


def _db_or_503():
    err = boot_error()
    if err:
        return render_template("db_down.html", error=err), 503
    return None


def _missing(page: dict) -> list[str]:
    """What the operator still has to fill in, named on the staff screen
    rather than discovered on the live page."""
    cfg = page.get("config") or {}
    out = []
    if not page.get("cam_embed_url"):
        out.append("Cam stream embed URL — the page shows a placeholder until a stream that allows embedding is set.")
    if not cfg.get("canonical_url"):
        out.append("Canonical URL — the address on the client's own domain the page will be served at.")
    if not (cfg.get("business") or {}).get("url"):
        out.append("Business website URL — the LocalBusiness schema and the house ads link to it.")
    if not cfg.get("pool_datum_confirmed") and any(s["key"] == "pool_elevation" for s in store.list_sources(page["id"])):
        out.append("Pool elevation datum — confirm with the state park office before 'above normal pool' is shown.")
    return out


# ------------------------------------------------------------------ staff

@app.route("/")
def index():
    down = _db_or_503()
    if down:
        return down
    pages = store.list_pages()
    for p in pages:
        p["health"] = store.health_summary(p["id"])
        p["sources"] = store.health(p["id"])
        p["missing"] = _missing(p)
    seeds_available = [s for s in seeds.SEEDS if s not in {p["slug"] for p in pages}]
    return render_template("index.html", pages=pages, seeds_available=seeds_available,
                           mount=request.script_root or MOUNT)


@app.route("/pages/<slug>")
def page_detail(slug: str):
    down = _db_or_503()
    if down:
        return down
    page = store.get_page(slug)
    if not page:
        abort(404)
    cache = store.cache_for(page["id"])
    ctx = page_render.build(page, cache)
    start, end = tracking.month_bounds()
    month = tracking.stats(page["id"], start, end)
    names = {pl["id"]: (pl["name"] or pl["sponsor_name"]) for pl in sponsors.list_placements(page["id"])}
    return render_template("page.html", page=page, ctx=ctx, health=store.health(page["id"]),
                           cache=cache, missing=_missing(page), month=month, placement_names=names,
                           mount=request.script_root or MOUNT)


@app.route("/pages/<slug>/refresh", methods=["POST"])
def page_refresh(slug: str):
    down = _db_or_503()
    if down:
        return down
    try:
        result = store.refresh_page(slug, force=True)
    except LookupError:
        abort(404)
    _activity("refreshed", client=(store.get_page(slug) or {}).get("client_name"), slug=slug,
              ok=result["ok"], errors=len(result["errors"]))
    if request.is_json or request.args.get("format") == "json":
        return jsonify({"ok": True, **result})
    return redirect(url_for("page_detail", slug=slug))


@app.route("/provision/<slug>", methods=["POST"])
def provision(slug: str):
    down = _db_or_503()
    if down:
        return down
    if slug not in seeds.SEEDS:
        abort(404)
    spec = seeds.SEEDS[slug]
    # A wizard-only seed carries no sources of its own -- the buoy/tides
    # adapters resolve their configs from lat/lon, and freezing that in
    # a file would be a guess. Route it through the wizard, pre-populated,
    # rather than writing a page with an empty source list.
    if spec.get("wizard_seed") and not spec.get("sources"):
        return redirect(url_for("builder_wizard", seed=slug))
    result = seeds.provision(slug, fetch=True)
    _activity("provisioned", client=result["page"].get("client_name"), slug=slug,
              created=result["page"].get("created"), sources=result["sources"])
    if request.is_json or request.args.get("format") == "json":
        return jsonify({"ok": True, "slug": slug, "created": result["page"].get("created"),
                        "sources": result["sources"], "refresh": result.get("refresh")})
    return redirect(url_for("page_detail", slug=slug))


# ----------------------------------------------------------------- builder

_TIMEZONES = ("America/New_York", "America/Chicago", "America/Denver",
              "America/Los_Angeles", "America/Phoenix", "America/Anchorage",
              "Pacific/Honolulu")


@app.route("/builder")
def builder_wizard():
    """The Cam Builder screens. Every step -- address, location type, probe
    review, provision -- lives on one page so a person can go back to any of
    them without losing the ones after. Sprint 6, per docs/camhub-spec.md."""
    down = _db_or_503()
    if down:
        return down
    types = [{"key": k, "label": k.replace("_", " ").title(),
              "wants": list(builder.WANTS.get(k, ()))}
             for k in builder.LOCATION_TYPES]
    seed = (request.args.get("seed") or "").strip()[:80]
    return render_template("builder.html", mount=request.script_root or MOUNT,
                           types=types, timezones=list(_TIMEZONES),
                           seed=seed if seed in seeds.SEEDS else "")


@app.route("/builder/provision", methods=["POST"])
def builder_provision():
    """The wizard's POST: answers, the probe payload the browser just ran
    against, and a `picked` map deciding which candidate to keep per key.
    Idempotent by slug -- a second submit for the same slug updates in place.
    """
    down = _db_or_503()
    if down:
        return down
    payload = request.get_json(silent=True) or {}
    answers = payload.get("answers") or {}
    probe_result = payload.get("probe") or {}
    picked = payload.get("picked") or {}
    if not answers.get("slug") and not answers.get("title"):
        return jsonify({"ok": False, "error": "a slug or title is required"}), 400
    if not probe_result.get("lat") or not probe_result.get("lon"):
        return jsonify({"ok": False, "error": "the probe payload is missing coordinates"}), 400
    answers = {**answers, "picked": picked}
    try:
        spec = builder.spec_from_answers(answers, probe_result)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 400
    if not spec["sources"]:
        return jsonify({"ok": False, "error":
                        "no sources were picked from the probe -- go back and check at least one"}), 400
    try:
        result = seeds.provision_from_spec(spec, fetch=True)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 400
    _activity("wizard_provisioned", client=result["page"].get("client_name"),
              slug=spec["slug"], created=result["page"].get("created"),
              sources=result["sources"], location_type=spec["location_type"])
    return jsonify({"ok": True, "slug": spec["slug"], "sources": result["sources"],
                    "created": result["page"].get("created"),
                    "refresh": result.get("refresh")})


@app.route("/pages/<slug>/reprobe", methods=["POST"])
def page_reprobe(slug: str):
    """Re-run every adapter's probe against the page's current lat/lon and
    location type, and report what would change: new candidates, better
    distances, a source that stopped answering. Never writes -- turning a
    probe result into sources is the wizard's step 4."""
    down = _db_or_503()
    if down:
        return down
    page = store.get_page(slug)
    if not page:
        abort(404)
    try:
        result = builder.probe(float(page["lat"]), float(page["lon"]),
                               page.get("location_type") or "inland_lake")
    except (ValueError, TypeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    current = {row["key"]: row for row in store.list_sources(page["id"])}
    delta = []
    for group in result["confirmed"] + result["decide"]:
        key = group["key"]
        top = group["candidates"][0]
        was = current.get(key)
        if not was:
            delta.append({"key": key, "kind": "new", "label": top.get("label") or key,
                          "adapter": top["adapter"]})
        elif top["adapter"] != was["adapter"] or top.get("config") != was.get("config"):
            delta.append({"key": key, "kind": "changed", "label": top.get("label") or key,
                          "adapter": top["adapter"], "was": was["adapter"]})
    for a in result["absent"]:
        if a["key"] in current:
            delta.append({"key": a["key"], "kind": "missing", "reason": a["reason"]})
    return jsonify({"ok": True, "slug": slug, "probe": result, "delta": delta})


# ---------------------------------------------------------------- sponsors

@app.route("/sponsors", methods=["GET", "POST"])
def sponsors_index():
    down = _db_or_503()
    if down:
        return down
    error = warning = ""
    if request.method == "POST":
        try:
            row = sponsors.save_sponsor(request.form.to_dict())
            _activity("sponsor_saved", sponsor=row["name"], sponsor_id=row["id"])
            return redirect(url_for("sponsors_index", saved=row["id"]))
        except ValueError as exc:
            error = str(exc)
    rows = sponsors.list_sponsors()
    by_sponsor: dict[int, list] = {}
    for page in store.list_pages():
        for pl in sponsors.list_placements(page["id"]):
            if pl["sponsor_id"]:
                by_sponsor.setdefault(pl["sponsor_id"], []).append({**pl, "page_slug": page["slug"],
                                                                   "page_title": page["title"]})
    return render_template("sponsors.html", sponsors=rows, by_sponsor=by_sponsor, error=error,
                           warning=warning, editing=None, mount=request.script_root or MOUNT,
                           saved=request.args.get("saved"))


@app.route("/sponsors/<int:sponsor_id>", methods=["GET", "POST"])
def sponsor_edit(sponsor_id: int):
    down = _db_or_503()
    if down:
        return down
    editing = sponsors.get_sponsor(sponsor_id)
    if not editing:
        abort(404)
    error = ""
    if request.method == "POST":
        try:
            editing = sponsors.save_sponsor(request.form.to_dict(), sponsor_id)
            _activity("sponsor_saved", sponsor=editing["name"], sponsor_id=sponsor_id)
            return redirect(url_for("sponsors_index", saved=sponsor_id))
        except ValueError as exc:
            error = str(exc)
    by_sponsor: dict[int, list] = {}
    for page in store.list_pages():
        for pl in sponsors.list_placements(page["id"]):
            if pl["sponsor_id"] == sponsor_id:
                by_sponsor.setdefault(sponsor_id, []).append({**pl, "page_slug": page["slug"],
                                                             "page_title": page["title"]})
    return render_template("sponsors.html", sponsors=sponsors.list_sponsors(), by_sponsor=by_sponsor,
                           error=error, warning="", editing=editing, mount=request.script_root or MOUNT,
                           saved=None)


@app.route("/pages/<slug>/placements")
def placements_index(slug: str):
    down = _db_or_503()
    if down:
        return down
    page = store.get_page(slug)
    if not page:
        abort(404)
    rows = sponsors.list_placements(page["id"])
    slots = sponsors.select_slots(page)
    start, end = tracking.month_bounds()
    month = tracking.stats(page["id"], start, end)
    for r in rows:
        r["stats"] = month["placements"].get(r["id"])
    return render_template("placements.html", page=page, placements=rows, slots=slots, month=month,
                           mount=request.script_root or MOUNT, saved=request.args.get("saved"),
                           warning=request.args.get("warning", ""))


def _placement_form(page: dict, placement: dict | None, *, error: str = "", warnings=()):
    position = (placement or {}).get("position") or request.args.get("position") or "supporting"
    if position not in sponsors.POSITIONS:
        position = "supporting"
    form = {**(placement or {"position": position, "status": "draft", "animation": "static",
                             "weight": 1, "sort_order": 0, "is_house": False})}
    for key in sponsors.DRAFT_FIELDS + ("start_date", "end_date", "sponsor_id", "position", "status",
                                        "is_house", "weight", "sort_order"):
        if key in request.form:
            form[key] = request.form.get(key)
    form["is_house"] = bool(form.get("is_house")) and str(form.get("is_house")).lower() not in ("0", "false", "")
    token = sponsors.preview_token(page["id"], form["position"], (placement or {}).get("id"))
    return render_template("placement.html", page=page, placement=placement, form=form,
                           sponsors=sponsors.list_sponsors(), limits=sponsors.LIMITS,
                           animations=sponsors.ANIMATIONS, positions=sponsors.POSITIONS,
                           statuses=sponsors.STATUSES, preview_token=token, error=error,
                           warnings=list(warnings), mount=request.script_root or MOUNT)


def _placement_submit(page: dict, placement_id: int | None):
    data = request.form.to_dict()
    data["is_house"] = request.form.get("is_house") in ("1", "on", "true")
    try:
        for kind, field in (("image", "image_url"), ("logo", "logo_url")):
            up = request.files.get(f"{kind}_file")
            if up and up.filename:
                data[field] = sponsors.store_creative(up, kind=kind, page=page)
        row, warnings = sponsors.save_placement(data, page["id"], placement_id, actor=_user())
    except (ValueError, LookupError) as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 -- an upload that fails is a sentence on the form, not a 500
        return None, f"The creative could not be stored: {type(exc).__name__}: {exc}"
    _activity("placement_saved", client=page.get("client_name"), slug=page["slug"],
              placement_id=row["id"], position=row["position"], status=row["status"],
              sponsor=row.get("sponsor_name") or ("house" if row["is_house"] else ""))
    return (row, warnings), ""


@app.route("/pages/<slug>/placements/new", methods=["GET", "POST"])
def placement_new(slug: str):
    down = _db_or_503()
    if down:
        return down
    page = store.get_page(slug)
    if not page:
        abort(404)
    if request.method == "POST":
        result, error = _placement_submit(page, None)
        if error:
            return _placement_form(page, None, error=error), 400
        row, warnings = result
        return redirect(url_for("placement_edit", placement_id=row["id"], saved=1,
                                warning=" ".join(warnings)))
    return _placement_form(page, None)


@app.route("/placements/<int:placement_id>", methods=["GET", "POST"])
def placement_edit(placement_id: int):
    down = _db_or_503()
    if down:
        return down
    placement = sponsors.get_placement(placement_id)
    if not placement:
        abort(404)
    page = next((p for p in store.list_pages() if p["id"] == placement["page_id"]), None)
    if not page:
        abort(404)
    if request.method == "POST":
        if request.form.get("delete") == "1":
            sponsors.delete_placement(placement_id, page["id"])
            _activity("placement_deleted", client=page.get("client_name"), slug=page["slug"],
                      placement_id=placement_id)
            return redirect(url_for("placements_index", slug=page["slug"]))
        result, error = _placement_submit(page, placement_id)
        if error:
            return _placement_form(page, placement, error=error), 400
        row, warnings = result
        return redirect(url_for("placement_edit", placement_id=row["id"], saved=1,
                                warning=" ".join(warnings)))
    warnings = [w for w in [request.args.get("warning", "")] if w]
    return _placement_form(page, placement, warnings=warnings)


# ----------------------------------------------------------------- reports

_PERIOD_OPTIONS = 8


def _period_list(today=None):
    from datetime import date, timedelta
    today = today or date.today()
    # The last N monthly periods, most recent first. Prior month leads
    # because that is what the scheduler will pick up on the 1st.
    out = []
    d = today.replace(day=1)
    for _ in range(_PERIOD_OPTIONS):
        d = (d - timedelta(days=1)).replace(day=1)
        out.append({"value": f"{d.year:04d}-{d.month:02d}",
                    "label": f"{reports.MONTHS[d.month - 1]} {d.year}"})
    # Current month (to date) at the end -- rarely the one being sent
    # from here, but the person may want to preview it.
    now = today.replace(day=1)
    out.insert(0, {"value": f"{now.year:04d}-{now.month:02d}",
                   "label": f"{reports.MONTHS[now.month - 1]} {now.year} (to date)"})
    return out


def _reports_context(period: str, flash: str = ""):
    if not period:
        # Prior month is the default: the scheduler's own target.
        from datetime import date
        d = date.today().replace(day=1)
        prev = (d - _one_day()).replace(day=1)
        period = f"{prev.year:04d}-{prev.month:02d}"
    rows = outbox.list_rows(period=period)
    sponsors_list = _portal_directory()
    return {"rows": rows, "sponsors_list": sponsors_list,
            "periods": _period_list(), "active_period": period,
            "flash": flash, "mount": request.script_root or MOUNT}


def _one_day():
    from datetime import timedelta
    return timedelta(days=1)


def _portal_directory():
    from .models import Placement, session as db_session
    from sqlalchemy import func as _f, select as _s
    with db_session() as s:
        rows = s.execute(
            _s(Placement.sponsor_id, _f.count(Placement.id))
            .where(Placement.sponsor_id.isnot(None))
            .group_by(Placement.sponsor_id)
        ).all()
    counts = {sid: n for sid, n in rows}
    out = []
    for sp in sponsors.list_sponsors():
        n = counts.get(sp["id"], 0)
        if not n:
            continue
        token = portal_lib.mint(sp["id"])
        base = (request.host_url.rstrip("/") + (request.script_root or MOUNT)) \
            if request else (request.script_root or MOUNT)
        out.append({"id": sp["id"], "name": sp["name"], "email": sp.get("email") or "",
                    "placement_count": n,
                    "portal_url": f"{base}/portal/{token}"})
    return out


@app.route("/reports")
def reports_index():
    down = _db_or_503()
    if down:
        return down
    period = (request.args.get("period") or "").strip()[:7]
    return render_template("reports.html", **_reports_context(period))


@app.route("/reports/run", methods=["POST"])
def reports_run():
    down = _db_or_503()
    if down:
        return down
    period = (request.form.get("period") or "").strip()[:7]
    action = (request.form.get("action") or "").strip()
    try:
        year, month = outbox.parse_period(period)
    except ValueError as exc:
        return render_template("reports.html", **_reports_context(period, flash=str(exc))), 400
    flash = ""
    if action == "enqueue":
        result = outbox.enqueue_month(year, month, actor=_user())
        _activity("reports_enqueued", period=period,
                  created=len(result["created"]), skipped=len(result["skipped"]))
        flash = (f"Enqueued {len(result['created'])} row(s) for {period}; "
                 f"{len(result['skipped'])} already on file.")
    elif action == "render":
        rendered = failed = 0
        for row in outbox.list_rows(period=period):
            if row["status"] != "pending":
                continue
            try:
                outbox.render_row(row["id"])
                rendered += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                log.exception("camhub_report_render_failed id=%s error=%s", row["id"], exc)
        _activity("reports_rendered", period=period, rendered=rendered, failed=failed)
        flash = f"Rendered {rendered} pending row(s). {failed} failed."
    else:
        flash = "Choose Enqueue or Render."
    return redirect(url_for("reports_index", period=period, flashed=flash))


@app.route("/reports/<int:row_id>", methods=["POST"])
def reports_row(row_id: int):
    down = _db_or_503()
    if down:
        return down
    row = outbox.get_row(row_id)
    if row is None:
        abort(404)
    action = (request.form.get("action") or "").strip()
    period = row["period"]
    flash = ""
    try:
        if action == "render":
            outbox.render_row(row_id)
            _activity("report_rendered", period=period, sponsor=row["sponsor_name"])
            flash = f"Rendered {row['sponsor_name']} for {period}."
        elif action == "send":
            result = outbox.send_row(row_id)
            _activity("report_sent", period=period, sponsor=row["sponsor_name"],
                      sent=bool(result.get("sent")), reason=result.get("reason") or "")
            flash = (f"Sent {row['sponsor_name']} for {period}." if result.get("sent")
                     else (f"{row['sponsor_name']} for {period}: "
                           f"{result.get('detail') or result.get('reason') or 'not sent'}"))
    except Exception as exc:  # noqa: BLE001
        flash = f"{type(exc).__name__}: {exc}"
    return redirect(url_for("reports_index", period=period, flashed=flash))


@app.route("/reports/<int:sponsor_id>/<period>.pdf")
def reports_pdf(sponsor_id: int, period: str):
    """Regenerate the PDF for a sponsor and month, on demand. Reads
    the current rollup, so the download is always the up-to-date PDF
    -- the outbox row is only the delivery record."""
    down = _db_or_503()
    if down:
        return down
    try:
        year, month = outbox.parse_period(period)
    except ValueError:
        abort(400)
    try:
        report = reports.sponsor_monthly(int(sponsor_id), year, month)
    except LookupError:
        abort(404)
    pdf = reports.render_monthly_pdf(report)
    _activity("report_pdf", sponsor_id=int(sponsor_id), period=period,
              sponsor=report["sponsor"]["name"])
    resp = Response(pdf, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = (
        f'inline; filename="camhub-{report["sponsor"]["name"].replace(" ", "-")}-{period}.pdf"')
    return resp


# ------------------------------------------------------------- Client 360

@app.route("/api/client-card")
def api_client_card():
    """Client 360 pulls this to render its CamHub card. Answer even
    when the database is offline, so the card can say why rather
    than blank the record."""
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"measured": False, "reason": "name-required"}), 400
    from . import hub_card
    return jsonify(hub_card.for_client(name))


@app.route("/api/geocode")
def api_geocode():
    address = (request.args.get("address") or "").strip()
    if not address:
        return jsonify({"ok": False, "error": "address is required"}), 400
    try:
        return jsonify({"ok": True, "matches": builder.geocode(address)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 502


@app.route("/api/probe")
def api_probe():
    try:
        lat, lon = float(request.args.get("lat")), float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "lat and lon are required"}), 400
    location_type = request.args.get("type") or "inland_lake"
    try:
        return jsonify({"ok": True, **builder.probe(lat, lon, location_type)})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/health")
def health():
    err = boot_error()
    if err:
        return jsonify({"tool": "camhub", "ok": False, "error": err}), 503
    pages = store.list_pages()
    rows = [{"slug": p["slug"], "health": store.health_summary(p["id"]),
             "sources": [{"key": h["key"], "state": h["state"], "age_minutes": h["age_minutes"],
                          "error": h["last_error"]} for h in store.health(p["id"])]}
            for p in pages]
    ok = all(r["health"] != "red" for r in rows)
    return jsonify({"tool": "camhub", "ok": ok, "pages": rows}), 200 if ok else 503


# ----------------------------------------------------------------- public

@app.route("/cam/<slug>")
def cam(slug: str):
    if boot_error():
        return render_template("cam_missing.html", reason="temporarily unavailable"), 503
    page = store.get_page(slug)
    if not page or page.get("status") != "live":
        return render_template("cam_missing.html", reason="not found"), 404
    preview = None
    token = request.args.get("preview")
    if token:
        claim = sponsors.read_preview(token, page["id"])
        if claim is None:
            return render_template("cam_missing.html", reason="preview link expired"), 404
        preview = {"claim": claim, "draft": sponsors.decode_draft(request.args.get("draft", ""))}
    ctx = page_render.build(page, store.cache_for(page["id"]), preview=preview)
    ctx["go_base"] = (request.script_root or "") + "/go/"
    ctx["events_url"] = (request.script_root or "") + f"/cam/{slug}/events"
    ctx["tracking"] = not preview
    html = render_template("cam.html", **ctx)
    if preview:
        # A draft is for the editor's own iframe: never cached, never indexed.
        return Response(html, headers={"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store",
                                       "Content-Security-Policy": "frame-ancestors 'self'"})
    return Response(html, headers={
        # The middleware in wsgi.py adds noindex to everything that does not
        # say otherwise; this page says otherwise, on purpose.
        "X-Robots-Tag": "index, follow, max-image-preview:large",
        "Cache-Control": "public, max-age=60, stale-while-revalidate=300",
        "Content-Security-Policy": "frame-ancestors *",
    })


@app.route("/cam/<slug>/events", methods=["POST", "OPTIONS"])
def cam_events(slug: str):
    """The page's batch: one pageview and the impressions and clicks its
    script saw. Sent with sendBeacon, so the answer is never read; it is
    still honest about what it kept."""
    if request.method == "OPTIONS":
        resp = Response(status=204)
    else:
        page = store.get_page(slug) if not boot_error() else None
        if not page:
            resp = jsonify({"ok": False, "error": "not found"})
            resp.status_code = 404
        else:
            try:
                payload = request.get_json(force=True, silent=True)
                if payload is None:
                    raise ValueError("a JSON body is expected")
                result = tracking.ingest(page, payload, ip=client_ip(request),
                                         user_agent=request.headers.get("User-Agent", ""),
                                         referrer=request.headers.get("Referer", ""))
                resp = jsonify({"ok": True, **result})
                resp.status_code = 202
            except ValueError as exc:
                resp = jsonify({"ok": False, "error": str(exc)})
                resp.status_code = 400
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


@app.route("/go/<int:placement_id>")
def go(placement_id: int):
    """Every sold link on the page comes through here: the click is
    recorded and the visitor is sent on. A house link never does -- it is
    the client's own site, not a sponsor's."""
    if boot_error():
        abort(503)
    hit = tracking.click(placement_id, session_token=request.args.get("s", ""),
                         ip=client_ip(request), user_agent=request.headers.get("User-Agent", ""),
                         referrer=request.headers.get("Referer", ""))
    if not hit:
        abort(404)
    resp = redirect(hit["url"], code=302)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


# ----------------------------------------------------- sponsor portal

def _portal_sponsor_id(token: str) -> int | None:
    sid = portal_lib.read(token or "")
    if not sid:
        return None
    from .models import Sponsor, session as db_session
    with db_session() as s:
        return sid if s.get(Sponsor, int(sid)) is not None else None


def _portal_common(sid: int, token: str) -> dict:
    start = (request.args.get("start") or "").strip()[:10]
    end = (request.args.get("end") or "").strip()[:10]
    view = portal_lib.sponsor_view(sid, start=start, end=end)
    prefix = (request.script_root or "")
    return {"view": view,
            "csv_url": f"{prefix}/portal/{token}/csv?start={view['window']['start']}&end={view['window']['end']}",
            "chart_url": f"{prefix}/portal/{token}/chart.png?start={view['window']['start']}&end={view['window']['end']}"}


@app.route("/portal/<token>")
def portal_index(token: str):
    if boot_error():
        return render_template("cam_missing.html", reason="temporarily unavailable"), 503
    sid = _portal_sponsor_id(token)
    if sid is None:
        return render_template("cam_missing.html", reason="portal link expired"), 404
    ctx = _portal_common(sid, token)
    resp = Response(render_template("portal.html", **ctx))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.route("/portal/<token>/chart.png")
def portal_chart(token: str):
    """The daily-impressions chart as a PNG. Rendered on request so the
    portal never has to write files, and the same helper the PDF
    uses draws it."""
    if boot_error():
        abort(503)
    sid = _portal_sponsor_id(token)
    if sid is None:
        abort(404)
    start = (request.args.get("start") or "").strip()[:10]
    end = (request.args.get("end") or "").strip()[:10]
    view = portal_lib.sponsor_view(sid, start=start, end=end)
    png = reports.daily_chart_png(view["daily"])
    resp = Response(png, mimetype="image/png")
    resp.headers["X-Robots-Tag"] = "noindex"
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


@app.route("/portal/<token>/csv")
def portal_csv(token: str):
    if boot_error():
        abort(503)
    sid = _portal_sponsor_id(token)
    if sid is None:
        abort(404)
    start = (request.args.get("start") or "").strip()[:10]
    end = (request.args.get("end") or "").strip()[:10]
    view = portal_lib.sponsor_view(sid, start=start, end=end)
    try:
        text = reports.csv_for_sponsor(sid,
                                       view["window"]["start"], view["window"]["end"])
    except (LookupError, ValueError):
        abort(404)
    filename = (f"camhub-{view['sponsor']['name'].replace(' ', '-')}"
                f"-{view['window']['start']}-{view['window']['end']}.csv")
    resp = Response(text, mimetype="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.headers["X-Robots-Tag"] = "noindex"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.route("/cam/<slug>/data.json")
def cam_data(slug: str):
    if boot_error():
        return jsonify({"ok": False, "error": "temporarily unavailable"}), 503
    page = store.get_page(slug)
    if not page or page.get("status") != "live":
        return jsonify({"ok": False, "error": "not found"}), 404
    ctx = page_render.build(page, store.cache_for(page["id"]))
    body = {"ok": True, "slug": slug, "title": ctx["title"], "updated": ctx["updated_iso"],
            "current": {"temp_f": ctx["current_temp"], "text": ctx["current_text"]},
            "tiles": ctx["strip"]["tiles"], "strip_rendered": ctx["strip"]["rendered"],
            "verdict": ctx["verdict"], "advisories": ctx["advisories"],
            "forecast": ctx["forecast"]}
    resp = jsonify(body)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


if __name__ == "__main__":
    app.run("127.0.0.1", int(os.environ.get("PORT", "8016")), debug=True)
