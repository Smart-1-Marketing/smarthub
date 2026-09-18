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

from . import builder, render as page_render, seeds, sponsors, store, tracking
from .models import boot_error, init_db

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"),
            static_folder=str(BASE_DIR / "static"))
log = logging.getLogger("hub")

PUBLIC_PREFIXES = ("/cam/", "/go/")
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
    result = seeds.provision(slug, fetch=True)
    _activity("provisioned", client=result["page"].get("client_name"), slug=slug,
              created=result["page"].get("created"), sources=result["sources"])
    if request.is_json or request.args.get("format") == "json":
        return jsonify({"ok": True, "slug": slug, "created": result["page"].get("created"),
                        "sources": result["sources"], "refresh": result.get("refresh")})
    return redirect(url_for("page_detail", slug=slug))


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
