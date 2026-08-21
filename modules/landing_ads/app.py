"""Smart 1 Hub — Landing Page Ads.

Ads for the landing pages we build: pick one of the Smart 1 suite apps (or
paste any URL), and the tool reads the page and writes the campaign around
what the page actually says — Google search ads, paid social for Meta and
TikTok, display banners in the sizes Image Creator already offers, and
streaming-audio scripts to the same word budgets Radio Promo enforces.

Same two modes as Radio Promo, for the same reason:

* **Spec** — no client attached. Files under ``spec/``.
* **Attached** — a client from the Hub registry, filed under their slug.

One read of the page produces one shared brief, and every format is written
from that brief. That is the point of doing this in one tool rather than four:
the Google headline, the TikTok hook and the :30 read make the same promise,
and none of them promise something the page won't honour.
"""
from __future__ import annotations

import base64
import io
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from . import VERSION, VERSION_DATE
from . import ai, store
from .catalog import (BANNER_SIZES, FORMATS, OBJECTIVES, page_by_slug)

try:
    from hub import audit as hub_audit
except Exception:                                            # noqa: BLE001
    hub_audit = None

BASE_DIR = Path(__file__).parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.config.update(JSON_SORT_KEYS=False)

MOUNT = "/tools/landing-ads"

_CLOUD_URL = (os.environ.get("CLOUDINARY_URL") or "").strip()
_CLOUD_NAME = (os.environ.get("CLOUDINARY_CLOUD_NAME") or "").strip()
_CLOUD_KEY = (os.environ.get("CLOUDINARY_API_KEY") or "").strip()
_CLOUD_SECRET = (os.environ.get("CLOUDINARY_API_SECRET") or "").strip()

try:
    import cloudinary
    import cloudinary.uploader
    if _CLOUD_URL.startswith("cloudinary://"):
        cloudinary.config(secure=True)
    elif _CLOUD_NAME and _CLOUD_KEY and _CLOUD_SECRET:
        cloudinary.config(cloud_name=_CLOUD_NAME, api_key=_CLOUD_KEY,
                          api_secret=_CLOUD_SECRET, secure=True)
except Exception:                                            # noqa: BLE001
    cloudinary = None


def cloud_ready() -> bool:
    return bool(cloudinary) and bool(_CLOUD_URL or (_CLOUD_NAME and _CLOUD_KEY and _CLOUD_SECRET))


def actor() -> str:
    user = request.environ.get("s1hub.user")
    if isinstance(user, dict):
        return user.get("email") or user.get("name") or "Team"
    return str(user or "Team")


def log(event: str, **extra):
    if hub_audit is not None:
        try:
            hub_audit.log("landing_ads", event, actor=actor(), **extra)
        except Exception:                                    # noqa: BLE001
            pass


def fail(message: str, code: int = 400):
    return jsonify({"ok": False, "error": message}), code


def body() -> dict:
    return request.get_json(silent=True) or {}


def _version() -> str:
    try:
        from hub import version as hub_version
        return hub_version.label()
    except Exception:                                        # noqa: BLE001
        return f"v{VERSION} · {VERSION_DATE}"


def _local_dir() -> str:
    path = os.path.join(store.data_dir(), "files")
    os.makedirs(path, exist_ok=True)
    return path


# -------------------------------------------------------------------- pages
@app.route("/")
def index():
    return render_template("index.html", version=_version(), pages=store.pages(),
                           formats=FORMATS, objectives=OBJECTIVES,
                           sizes=BANNER_SIZES, mount=MOUNT)


@app.route("/file/<path:name>")
def local_file(name: str):
    from flask import send_from_directory
    return send_from_directory(_local_dir(), name)


@app.route("/health")
def health():
    missing = [p["name"] for p in store.pages() if not p["url"]]
    return jsonify({"ok": True, "module": "landing_ads", "version": VERSION,
                    "ai": ai.ready(), "cloudinary": cloud_ready(),
                    "sets": len(store.all_sets()), "pages_without_a_url": missing})


@app.route("/api/pages", methods=["GET", "POST"])
def api_pages():
    if request.method == "POST":
        data = body()
        slug = (data.get("slug") or "").strip()
        if not page_by_slug(slug):
            return fail("That isn't one of the suite pages.")
        row = store.set_page_url(slug, data.get("url") or "")
        log("page.url", page=slug)
        return jsonify({"ok": True, "page": row})
    return jsonify({"ok": True, "pages": store.pages()})


@app.route("/api/clients")
def api_clients():
    try:
        from hub import clients_registry
    except Exception:                                        # noqa: BLE001
        return jsonify({"ok": True, "clients": []})
    return jsonify({"ok": True,
                    "clients": clients_registry.search_clients(
                        request.args.get("q", ""), limit=12)})


# ------------------------------------------------------------------ ad sets
@app.route("/api/sets", methods=["POST"])
def api_create():
    data = body()
    slug = (data.get("page_slug") or "").strip()
    url = (data.get("url") or "").strip()
    page = page_by_slug(slug) if slug else None
    if page and not url:
        url = next((p["url"] for p in store.pages() if p["slug"] == slug), "")
        if not url:
            return fail(f"No live URL is set for {page['name']} yet — set it in "
                        "Pages first, so the ads are written from the real page.")
    if not url:
        return fail("Pick a Smart 1 page or paste a URL.")
    data.update(url=url, page_name=(page or {}).get("name") or url,
                spec=not (data.get("client") or "").strip())
    row = store.create(data)
    log("set.create", set=row["id"], page=slug or url,
        kind="spec" if row["spec"] else "client")
    return jsonify({"ok": True, "set": row})


@app.route("/api/sets/<sid>")
def api_get(sid):
    row = store.get(sid)
    if not row:
        return fail("That ad set no longer exists.", 404)
    return jsonify({"ok": True, "set": row})


@app.route("/api/sets/<sid>/settings", methods=["POST"])
def api_settings(sid):
    if not store.get(sid):
        return fail("That ad set no longer exists.", 404)
    data = body()
    changes = {k: data[k] for k in ("client", "objective", "notes", "audience", "url")
               if k in data}
    row = store.update(sid, changes)
    if "client" in changes:
        log("set.attach", set=sid, client=changes["client"] or "(spec)")
    return jsonify({"ok": True, "set": row})


@app.route("/api/sets/<sid>/delete", methods=["POST"])
def api_delete(sid):
    ok = store.delete(sid)
    if ok:
        log("set.delete", set=sid)
    return jsonify({"ok": ok})


@app.route("/api/library")
def api_library():
    rows = store.library(request.args.get("q", ""), request.args.get("scope", "all"))
    slim = [{k: r.get(k) for k in ("id", "created_at", "updated_at", "spec",
                                   "client", "page_name", "url", "objective",
                                   "status")}
            | {"formats": sorted((r.get("formats") or {}).keys())} for r in rows]
    return jsonify({"ok": True, "sets": slim, "count": len(slim)})


# ------------------------------------------------------------------- brief
@app.route("/api/sets/<sid>/brief", methods=["POST"])
def api_brief(sid):
    row = store.get(sid)
    if not row:
        return fail("That ad set no longer exists.", 404)
    meta = page_by_slug(row.get("page_slug") or "") or {}
    try:
        brief = ai.brief_from_page(row["url"], meta, row.get("objective", ""),
                                   row.get("notes", ""), row.get("audience", ""))
    except ai.AIError as exc:
        return fail(str(exc), 502)
    row = store.update(sid, {"brief": brief, "status": "briefed"})
    log("set.brief", set=sid)
    return jsonify({"ok": True, "set": row})


# ----------------------------------------------------------------- generate
GENERATORS = {"google_rsa": ai.google_rsa, "paid_social": ai.paid_social,
              "display": ai.display, "streaming_audio": ai.streaming_audio}


@app.route("/api/sets/<sid>/generate", methods=["POST"])
def api_generate(sid):
    row = store.get(sid)
    if not row:
        return fail("That ad set no longer exists.", 404)
    if not row.get("brief"):
        return fail("Read the page first — every format is written from the brief.")
    wanted = [f for f in (body().get("formats") or []) if f in GENERATORS]
    if not wanted:
        return fail("Pick at least one ad format.")

    formats = dict(row.get("formats") or {})
    errors = {}
    for fid in wanted:
        try:
            formats[fid] = GENERATORS[fid](row["brief"], row.get("objective", ""))
        except ai.AIError as exc:
            errors[fid] = str(exc)
    if not formats and errors:
        return fail("; ".join(errors.values()), 502)
    row = store.update(sid, {"formats": formats, "status": "generated"})
    log("set.generate", set=sid, formats=",".join(wanted))
    return jsonify({"ok": True, "set": row, "errors": errors})


@app.route("/api/sets/<sid>/art", methods=["POST"])
def api_art(sid):
    """Background artwork for one display concept.

    Text is deliberately not rendered into the image — the headline, subhead,
    CTA and logo are set as live objects in Image Creator, which keeps type
    crisp and lets one concept be resized without regenerating anything.
    """
    row = store.get(sid)
    if not row:
        return fail("That ad set no longer exists.", 404)
    concept = body().get("concept") or {}
    direction = (concept.get("art_direction") or "").strip()
    if not direction:
        return fail("That concept has no art direction to work from.")
    try:
        from modules.radio_promo.ai import banner_art  # shared image plumbing
    except Exception:                                        # noqa: BLE001
        return fail("Image generation isn't available in this build.", 503)

    brand = {"colors": concept.get("colors") or []}
    try:
        art = banner_art(brand, "bold", concept.get("headline", ""))
    except Exception as exc:                                 # noqa: BLE001
        return fail(str(exc), 502)

    data = base64.b64decode(art["b64"])
    name = store.slugify(concept.get("name") or "concept", "concept")
    if cloud_ready():
        try:
            # Through hub.storage. Same folder and id, so existing art stays
            # where it is; the type comes from the filename instead of being
            # asserted as "image" regardless of what was actually generated.
            from hub import storage
            url = storage.put("landing_ads", f"{name}.png", data,
                              folder=store.cloud_folder(row),
                              public_id=f"{store.cloud_folder(row)}/{name}",
                              overwrite=False).url
        except Exception as exc:                             # noqa: BLE001
            print("landing_ads cloudinary upload failed:", exc)
            url = ""
    else:
        url = ""
    if not url:
        fname = f"{store.slugify(row['id'] + '-' + name, 'art')}.png"
        with open(os.path.join(_local_dir(), fname), "wb") as fh:
            fh.write(data)
        url = f"{MOUNT}/file/{fname}"

    art_rows = list(row.get("art") or [])
    art_rows.append({"concept": concept.get("name"), "url": url, "at": store.now()})
    row = store.update(sid, {"art": art_rows})
    return jsonify({"ok": True, "set": row, "url": url})


# ------------------------------------------------------------------ exports
@app.route("/api/sets/<sid>/export.csv")
def api_export(sid):
    """Flat CSV of every line of copy, for bulk upload or a review pass."""
    from flask import Response
    import csv
    row = store.get(sid)
    if not row:
        return fail("That ad set no longer exists.", 404)
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(["format", "field", "text", "characters", "over_limit"])
    fmt = row.get("formats") or {}

    for key in ("headlines", "descriptions", "callouts"):
        for item in (fmt.get("google_rsa") or {}).get(key, []):
            out.writerow(["google_rsa", key[:-1], item.get("text"),
                          item.get("chars"), "yes" if item.get("over") else ""])
    for platform in ("meta", "tiktok"):
        for ad in (fmt.get("paid_social") or {}).get(platform, []):
            for field in ("hook", "primary", "headline", "description"):
                out.writerow([platform, field, ad.get(field), len(str(ad.get(field) or "")), ""])
    for concept in (fmt.get("display") or {}).get("concepts", []):
        for field in ("headline", "subhead", "cta", "art_direction"):
            out.writerow(["display", f"{concept.get('name')} · {field}",
                          concept.get(field), len(str(concept.get(field) or "")), ""])
    audio = fmt.get("streaming_audio") or {}
    for slot in ("fifteen", "thirty"):
        part = audio.get(slot) or {}
        out.writerow(["streaming_audio", slot, part.get("script"),
                      len(str(part.get("script") or "")), ""])

    name = store.slugify(f"{row.get('page_name')}-{row.get('client') or 'spec'}", "ads")
    return Response(buf.getvalue(), mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{name}-ads.csv"'})


if __name__ == "__main__":
    app.run(port=5062, debug=True)
