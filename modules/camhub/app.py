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
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, url_for

from . import builder, render as page_render, seeds, store
from .models import boot_error, init_db

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"),
            static_folder=str(BASE_DIR / "static"))
log = logging.getLogger("hub")

PUBLIC_PREFIXES = ("/cam/",)
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
    return render_template("page.html", page=page, ctx=ctx, health=store.health(page["id"]),
                           cache=cache, missing=_missing(page),
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
    ctx = page_render.build(page, store.cache_for(page["id"]))
    html = render_template("cam.html", **ctx)
    return Response(html, headers={
        # The middleware in wsgi.py adds noindex to everything that does not
        # say otherwise; this page says otherwise, on purpose.
        "X-Robots-Tag": "index, follow, max-image-preview:large",
        "Cache-Control": "public, max-age=60, stale-while-revalidate=300",
        "Content-Security-Policy": "frame-ancestors *",
    })


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
