"""Smart 1 Hub — Image Creator.

A browser-based graphics editor with Fabric.js as the editing engine. The
external APIs only supply assets and intelligence; the design itself stays a
set of editable Fabric objects rather than one flattened image, which is what
lets a saved project be reopened and changed later.

Mounted in the Hub rather than built as a separate React service so it shares
one login, one deploy, and — the reason that matters — direct access to the
client registry, the SEO image gallery, the Brandfetch cache and Insites scan
assets. Every API key stays server-side; the browser only ever talks to these
proxy endpoints.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from flask import Flask, Response, jsonify, render_template, request, send_file

try:
    from hub import audit as hub_audit
except Exception:                                     # noqa: BLE001
    hub_audit = None

from . import animation, assets, photo_search, projects, qc, review_spec, share_store
from hub.webargs import clamp_int


def _settings():
    """Hub settings, read at call time.

    At import the value is frozen at boot, which is how a key added on Render
    without a redeploy reads as absent. Wrapped because this module is also run
    standalone in development, where hub is not importable.
    """
    try:
        from hub.config import settings
        return settings
    except Exception:                                 # noqa: BLE001
        class _Fallback:
            openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
            openai_model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            openai_image_model = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
            brandfetch_key = (os.environ.get("BRANDFETCH_API")
                              or os.environ.get("BRANDFETCH_API_KEY") or "").strip()
        return _Fallback()

BASE_DIR = Path(__file__).parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"),
            static_folder=str(BASE_DIR / "static"), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024

CANVAS_PRESETS = [
    {"key": "custom", "label": "Custom", "w": 0, "h": 0, "group": ""},
    {"key": "ig-square", "label": "Instagram Post", "w": 1080, "h": 1080, "group": "Social"},
    {"key": "ig-portrait", "label": "Instagram Portrait", "w": 1080, "h": 1350, "group": "Social"},
    {"key": "ig-story", "label": "Story / Reel", "w": 1080, "h": 1920, "group": "Social"},
    {"key": "og", "label": "Facebook / OG Share", "w": 1200, "h": 630, "group": "Social"},
    # "unit" names the hub.creative_specs id for this size, which is where
    # its dimensions AND its file-weight ceiling actually come from (see
    # qc.py) -- never restated here as a number, because that is the exact
    # drift this codebase has already paid for once (Half Page enforced at
    # 150 KB against a published 250 KB).
    {"key": "mrec", "label": "Medium Rectangle", "w": 300, "h": 250, "group": "Display ads",
     "unit": "medium_rectangle"},
    {"key": "leaderboard", "label": "Leaderboard", "w": 728, "h": 90, "group": "Display ads",
     "unit": "leaderboard"},
    {"key": "mobile-banner", "label": "Mobile Banner", "w": 320, "h": 50, "group": "Display ads",
     "unit": "mobile_banner_320"},
    {"key": "halfpage", "label": "Half Page", "w": 300, "h": 600, "group": "Display ads",
     "unit": "half_page"},
    {"key": "billboard", "label": "Billboard", "w": 970, "h": 250, "group": "Display ads",
     "unit": "rising_star"},
]

# Everything under /review/ is a client-facing link with no Hub login — the
# arrangement modules/ads_builder and modules/scans already use. Read from
# here by wsgi.py, which hands it to BOTH AuthGuard (so it is reachable) and
# HubBar (so the sidebar, help layer and feedback tab are not injected into a
# page a client reads), so the mount and the module cannot disagree about
# what is public.
PUBLIC_PREFIXES = ("/review/",)
MOUNT = "/tools/image-creator"


def actor_name() -> str:
    return request.environ.get("s1hub.user") or "Unknown"


def _log(event: str, **extra):
    if hub_audit is not None:
        try:
            hub_audit.log("image_creator", event, actor=actor_name(), **extra)
        except Exception:                             # noqa: BLE001
            pass


def _version() -> str:
    try:
        from hub import version
        return version.label()
    except Exception:                                 # noqa: BLE001
        return ""


def ai_ready() -> bool:
    return bool(_settings().openai_key)


def brandfetch_ready() -> bool:
    """Is the logo lookup switched on?

    Through hub.config: this screen read BRANDFETCH_API_KEY and
    modules/ads_builder/logo.py reads the same setting through settings, so on
    a deployment naming it BRANDFETCH_API the Ads logo lookup worked and this
    one reported it off — the same key, two answers, on two pages.
    """
    return bool(_settings().brandfetch_key)


def bg_remove_ready() -> bool:
    try:
        from modules.bg_remover import app as bg_remover
        return bg_remover.configured()
    except Exception:                                 # noqa: BLE001
        return False


# =====================================================================
# Pages
# =====================================================================
@app.route("/")
def index():
    return render_template(
        "index.html", version=_version(), presets=CANVAS_PRESETS,
        providers=photo_search.configured(), ai=ai_ready(),
        cloud=projects.cloud_ready(),
        brandfetch=brandfetch_ready(),
        bg_remove=bg_remove_ready(),
        prefill_client=request.args.get("client", ""),
        open_project=request.args.get("project", ""),
        fonts=assets.POPULAR_FONTS)


@app.route("/projects")
def projects_page():
    return render_template("projects.html", version=_version(),
                           client=request.args.get("client", ""))


@app.route("/health")
def health():
    return jsonify({"ok": True, "version": _version(),
                    "photo_providers": photo_search.configured(),
                    "cloudinary": projects.cloud_ready(), "ai": ai_ready()})


# =====================================================================
# Photos — one search, every provider
# =====================================================================
@app.route("/api/photos/search")
def api_photo_search():
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = max(6, min(int(request.args.get("perPage", 24)), 60))
    except (TypeError, ValueError):
        page, per_page = 1, 24
    out = photo_search.search(
        request.args.get("q", ""), page=page, per_page=per_page,
        orientation=request.args.get("orientation", "any"),
        color=request.args.get("color", ""),
        sort=request.args.get("sort", "relevant"))
    status = 502 if out.get("all_failed") else 200
    return jsonify(out), status


@app.route("/api/photos/use", methods=["POST"])
def api_photo_use():
    """Called when a stock image is actually placed. Records attribution and
    pings Unsplash's required download endpoint."""
    body = request.get_json(silent=True) or {}
    photo_search.track_download(body.get("photo") or {})
    return jsonify({"ok": True})


@app.route("/api/photos/proxy")
def api_photo_proxy():
    """Fabric needs pixel access to export a canvas, and stock CDNs don't all
    send permissive CORS headers. Proxying keeps the canvas untainted."""
    import requests as _rq
    url = request.args.get("url", "")
    if not url.startswith("https://"):
        return jsonify({"error": "https URLs only."}), 400
    allowed = ("pexels.com", "pixabay.com", "unsplash.com", "cloudinary.com",
               "brandfetch.io", "brandfetch.com", "iconify.design",
               "githubusercontent.com")
    host = url.split("/")[2].lower()
    if not any(host == d or host.endswith("." + d) for d in allowed):
        return jsonify({"error": "That host isn't allowed."}), 403
    try:
        r = _rq.get(url, timeout=20, stream=True)
        r.raise_for_status()
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"error": str(exc)}), 502
    ctype = r.headers.get("Content-Type", "image/jpeg")
    if not ctype.startswith("image/"):
        return jsonify({"error": "Not an image."}), 415
    return Response(r.content, mimetype=ctype,
                    headers={"Cache-Control": "public, max-age=86400",
                             "Access-Control-Allow-Origin": "*"})


# =====================================================================
# Icons, brands, fonts
# =====================================================================
@app.route("/api/icons/search")
def api_icons():
    return jsonify(assets.search_icons(request.args.get("q", ""),
                                       clamp_int(request.args.get("limit"), 60, 1, 500)))


@app.route("/api/icons/svg")
def api_icon_svg():
    svg = assets.icon_svg(request.args.get("prefix", ""), request.args.get("name", ""),
                          request.args.get("color", ""))
    if svg is None:
        return jsonify({"error": "Icon not found."}), 404
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.route("/api/brands/search")
def api_brands():
    # The client, when the editor knows one, so the lookup is filed
    # against the client record as well as the domain cache — Client
    # 360 reads the first and this tool searches by the second.
    return jsonify(assets.brand_lookup(request.args.get("q", ""),
                                       request.args.get("client", "")))


@app.route("/api/fonts")
def api_fonts():
    return jsonify({"fonts": assets.font_list(request.args.get("q", ""))})


# =====================================================================
# Smart 1's own assets — the reason this isn't a generic editor
# =====================================================================
@app.route("/api/assets/gallery")
def api_gallery_assets():
    client = request.args.get("client", "")
    return jsonify({
        "assets": assets.gallery_assets(client, request.args.get("q", "")),
        # Offered whether or not a search turns anything up, and whether or
        # not this client has a gallery yet — the resolver behind it (see
        # modules/image_picker/app.py::gallery_for_client) always lands
        # somewhere real: their full gallery if one exists, or everything
        # else the Hub holds for them if it does not. A "Client gallery"
        # chip with no way to reach the actual client gallery is the thing
        # this route exists to stop being true.
        "gallery_url": ("/tools/image-picker/gallery/for-client?name="
                        + quote(client)) if client.strip() else "",
    })


@app.route("/api/assets/scans")
def api_scan_assets():
    return jsonify({"assets": assets.scan_assets(
        request.args.get("client", ""), request.args.get("domain", ""))})


@app.route("/api/clients")
def api_clients():
    try:
        from hub import clients_registry
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"clients": [], "error": str(exc)})
    rows = clients_registry.search_clients(request.args.get("q", ""), limit=12)
    return jsonify({"clients": [{
        "name": r["name"], "slug": r["slug"], "domain": r.get("domain", ""),
        "url": r.get("url", ""), "is_house": r.get("is_house", False),
        "is_seo": r.get("is_seo", False)} for r in rows]})


# =====================================================================
# AI
# =====================================================================
def _openai_json(system: str, user: str, timeout: int = 60):
    import json as _json

    import requests as _rq
    key = _settings().openai_key
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    r = _rq.post("https://api.openai.com/v1/chat/completions",
                 headers={"Authorization": f"Bearer {key}",
                          "Content-Type": "application/json"},
                 json={"model": _settings().openai_model,
                       "response_format": {"type": "json_object"},
                       "temperature": 0.6,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}]},
                 timeout=timeout)
    if not r.ok:
        raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:160]}")
    try:  # record spend so /diagnostics doesn't under-report
        from hub import ai as _hub_ai
        _hub_ai.note_usage("image_creator", r.json(), purpose="copy")
    except Exception:  # noqa: BLE001
        pass
    return _json.loads(r.json()["choices"][0]["message"]["content"])


_SEARCH_PROMPT = """You turn a plain-English description of a wanted photo into
stock-library search terms. Stock sites match on literal subject matter, not
feelings, so convert the intent into concrete visible things.

Return JSON only: {"queries": [str, str, str]}
Three to five short queries, two to four words each, most literal first.
No punctuation, no quotes, no boolean operators."""


@app.route("/api/ai/photo-queries", methods=["POST"])
def api_ai_photo_queries():
    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "Describe the photo you're looking for."}), 400
    try:
        out = _openai_json(_SEARCH_PROMPT, prompt)
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"error": str(exc)}), 502
    queries = [str(q).strip() for q in (out.get("queries") or []) if str(q).strip()][:5]
    return jsonify({"queries": queries})


_COPY_PROMPT = """You are a direct-response copywriter for a local-business
marketing agency. Rewrite the supplied text for a graphic.

Return JSON only: {"options": [str, str, str]}
Three options. Keep them short enough to sit on an image — headlines under 8
words, CTAs under 5. No quotation marks, no emoji, no invented offers,
guarantees, prices or claims."""


@app.route("/api/ai/copy", methods=["POST"])
def api_ai_copy():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    mode = (body.get("mode") or "rewrite").strip()
    if not text:
        return jsonify({"error": "Select some text first."}), 400
    instruction = {
        "rewrite": "Rewrite it more persuasively.",
        "shorten": "Make it substantially shorter.",
        "headline": "Turn it into a strong headline.",
        "cta": "Turn it into a short call to action.",
    }.get(mode, "Rewrite it more persuasively.")
    try:
        out = _openai_json(_COPY_PROMPT, f"{instruction}\n\nText: {text}")
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"error": str(exc)}), 502
    return jsonify({"options": [str(o) for o in (out.get("options") or [])][:3]})


@app.route("/api/ai/image", methods=["POST"])
def api_ai_image():
    """Generate an image or background with OpenAI and return it as a data URL
    so it drops straight onto the canvas."""
    import requests as _rq
    key = _settings().openai_key
    if not key:
        return jsonify({"error": "OPENAI_API_KEY is not set."}), 503
    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "Describe the image you want."}), 400
    style = (body.get("style") or "").strip().lower()
    style_hint = {
        "photo": "Photorealistic, natural lighting, shot on a real camera.",
        "illustration": "Clean vector-style illustration, flat colors.",
        "graphic": "Bold graphic design, simple shapes, high contrast.",
        "abstract": "Abstract, no recognizable objects or text.",
        "gradient": "Smooth color gradient, no objects, no text.",
        "texture": "Subtle repeating texture, no focal subject, no text.",
    }.get(style, "")
    size = body.get("size") if body.get("size") in (
        "1024x1024", "1536x1024", "1024x1536") else "1024x1024"
    full = prompt + (" " + style_hint if style_hint else "")
    if body.get("background"):
        full += (" Suitable as a background: keep the composition uncluttered "
                 "with clear space, and include no words or lettering.")
    payload = {"model": _settings().openai_image_model,
               "prompt": full[:3800], "size": size, "n": 1}
    if body.get("transparent"):
        payload["background"] = "transparent"
    model = payload["model"]

    def _note(ok):
        """Record the spend. An image is billed per press and this route was
        recording nothing, so every generation here was invisible on the usage
        page -- while the two text routes beside it, which go through
        `_openai_json`, were tracked. `untracked_openai_modules()` read the
        *file* and found `from hub import ai` in that helper, so the whole
        module was exempted and the check reported it clean: the string
        satisfying the check, which is the `for_module(` failure one provider
        over.

        The model is passed explicitly because an images response carries no
        `usage` block -- `openai_cost()` prices anything named `gpt-image*`
        per image, and without the name there is nothing to price. A refused
        call keeps its row with `ok=False`: it spent nothing and is out of
        every billable total, but a wall of them is what a spent allowance
        looks like from this side.
        """
        try:
            from hub import ai as _hub_ai
            _hub_ai.note_usage("image_creator", {}, model=model,
                               purpose="image", ok=ok)
        except Exception:                             # noqa: BLE001
            pass

    try:
        r = _rq.post("https://api.openai.com/v1/images/generations",
                     headers={"Authorization": f"Bearer {key}",
                              "Content-Type": "application/json"},
                     json=payload, timeout=180)
        if not r.ok:
            _note(False)
            return jsonify({"error": f"OpenAI {r.status_code}: {r.text[:200]}"}), 502
        item = (r.json().get("data") or [{}])[0]
        _note(True)
    except Exception as exc:                          # noqa: BLE001
        _note(False)
        return jsonify({"error": str(exc)}), 502

    if item.get("b64_json"):
        return jsonify({"image": f"data:image/png;base64,{item['b64_json']}"})
    if item.get("url"):
        return jsonify({"image": item["url"], "needs_proxy": True})
    return jsonify({"error": "No image came back."}), 502


# =====================================================================
# Logo background removal — reuses the Hub's existing Background Remover
# tool (remove.bg) instead of duplicating it, so a logo upload doesn't need
# a separate trip out of the editor. §19 of the dev outline.
# =====================================================================
@app.route("/api/logos/remove-background", methods=["POST"])
def api_logo_remove_background():
    import base64 as _b64
    body = request.get_json(silent=True) or {}
    data_url = (body.get("image") or "").strip()
    if not data_url.startswith("data:image"):
        return jsonify({"error": "No image to process."}), 400
    try:
        from modules.bg_remover import app as bg_remover
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"error": f"Background Remover isn't available: {exc}"}), 503
    if not bg_remover.configured():
        return jsonify({"error": "REMOVE_BG_API_KEY isn't set, so background removal is off."}), 503
    try:
        _, b64 = data_url.split(",", 1)
        raw = _b64.b64decode(b64)
    except (ValueError, TypeError):
        return jsonify({"error": "That image couldn't be read."}), 400
    if len(raw) > bg_remover.MAX_BYTES:
        return jsonify({"error": "That logo is too large for background removal."}), 400
    try:
        png = bg_remover.call_remove_bg(raw)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"error": f"Background removal failed: {exc}"}), 502
    _log("logo_bg_removed")
    return jsonify({"image": "data:image/png;base64," + _b64.b64encode(png).decode()})


# =====================================================================
# Export optimization — a real compress/strip-metadata pass before a
# finished export leaves the browser, the gap noted against the spec's
# Sharp-based description (§37: Sharp isn't used anywhere in this build,
# Pillow only ever touches small preview thumbnails). The client calls this
# after rendering the canvas locally and falls back to the untouched
# client-rendered export if this fails for any reason — same "one piece
# failing doesn't break the whole feature" pattern the photo search uses.
# =====================================================================
@app.route("/api/export/optimize", methods=["POST"])
def api_export_optimize():
    import base64 as _b64
    import io as _io

    body = request.get_json(silent=True) or {}
    data_url = (body.get("image") or "").strip()
    if not data_url.startswith("data:image"):
        return jsonify({"error": "Nothing to optimize."}), 400
    fmt = (body.get("format") or "png").strip().lower()
    if fmt not in ("png", "jpeg", "webp"):
        fmt = "png"
    try:
        quality = max(40, min(int(body.get("quality", 92)), 100))
    except (TypeError, ValueError):
        quality = 92
    try:
        _, b64 = data_url.split(",", 1)
        raw = _b64.b64decode(b64)
    except (ValueError, TypeError):
        return jsonify({"error": "That image couldn't be read."}), 400
    if len(raw) > 40 * 1024 * 1024:
        return jsonify({"error": "That export is too large to optimize."}), 400
    try:
        from PIL import Image
        with Image.open(_io.BytesIO(raw)) as im:
            buf = _io.BytesIO()
            if fmt == "jpeg":
                im.convert("RGB").save(buf, "JPEG", quality=quality,
                                       optimize=True, progressive=True)
                mime = "image/jpeg"
            elif fmt == "webp":
                im.save(buf, "WEBP", quality=quality, method=6)
                mime = "image/webp"
            else:
                # Re-saving through Pillow with optimize=True applies a real
                # deflate pass — canvas.toDataURL's own PNG output is
                # otherwise noticeably larger than it needs to be.
                im.save(buf, "PNG", optimize=True)
                mime = "image/png"
        out = buf.getvalue()
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"error": f"Optimization failed: {exc}"}), 502
    # Re-saving through Pillow never carries EXIF/XMP forward unless it's
    # explicitly passed through, so this also satisfies "strip metadata
    # before export" — for free, as a side effect of the compression pass.
    return jsonify({"image": f"data:{mime};base64," + _b64.b64encode(out).decode(),
                    "bytes": len(out), "original_bytes": len(raw)})


# =====================================================================
# Projects
# =====================================================================
@app.route("/api/projects", methods=["GET"])
def api_projects_list():
    return jsonify({"projects": projects.search_projects(
        request.args.get("q", ""), request.args.get("client", ""),
        clamp_int(request.args.get("limit"), 100, 1, 500)),
        "cloudinary": projects.cloud_ready()})


@app.route("/api/projects", methods=["POST"])
def api_projects_save():
    body = request.get_json(silent=True) or {}
    try:
        record = projects.save_project(
            name=body.get("name", ""), canvas=body.get("canvas") or {},
            preview=body.get("preview", ""), client=body.get("client", ""),
            client_slug=body.get("client_slug", ""), tags=body.get("tags", ""),
            pid=body.get("id", ""), width=body.get("width", 0),
            height=body.get("height", 0), actor=actor_name())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"error": f"Save failed: {exc}"}), 500
    _log("project_saved", detail=record["name"], client=record.get("client", ""),
         project=record["id"])
    return jsonify({"ok": True, "project": record})


@app.route("/api/projects/<pid>")
def api_project_get(pid):
    canvas = projects.get_canvas(pid)
    if canvas is None:
        return jsonify({"error": "Project not found."}), 404
    row = next((r for r in projects.load_index() if r.get("id") == pid), {})
    return jsonify({"project": row, "canvas": canvas})


@app.route("/api/projects/<pid>/delete", methods=["POST"])
def api_project_delete(pid):
    ok = projects.delete_project(pid)
    if ok:
        _log("project_deleted", project=pid)
    return jsonify({"ok": ok, "projects": projects.search_projects()})


@app.route("/api/projects/<pid>/preview")
def api_project_preview(pid):
    path = projects.local_preview_path(pid)
    if not path:
        return jsonify({"error": "No preview."}), 404
    return send_file(path, mimetype="image/webp")


@app.route("/api/export/cloudinary", methods=["POST"])
def api_export_cloudinary():
    body = request.get_json(silent=True) or {}
    try:
        out = projects.export_to_cloudinary(
            body.get("image", ""), body.get("name", "export"),
            body.get("client", ""), body.get("project", ""))
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"error": f"Upload failed: {exc}"}), 502
    _log("export_saved", detail=body.get("name", ""), client=body.get("client", ""))
    return jsonify({"ok": True, **out})


def _preset_for(key: str) -> dict:
    return next((p for p in CANVAS_PRESETS if p["key"] == key), CANVAS_PRESETS[0])


def _preset_for_canvas(body: dict, canvas: dict) -> dict:
    """Which preset a QC or export call is judged against.

    An explicit ``preset`` key wins when the caller sends one; otherwise the
    canvas's own dimensions are matched against the presets exactly — the
    editor tracks no "which preset is this" state of its own once a size has
    been applied, so a 300x250 canvas is the Medium Rectangle whether it got
    there from the size picker or from typing the numbers in by hand.
    """
    key = str(body.get("preset") or "").strip()
    if key:
        return _preset_for(key)
    width = int((canvas or {}).get("width") or 0)
    height = int((canvas or {}).get("height") or 0)
    for p in CANVAS_PRESETS:
        if p.get("w") and p.get("h") and int(p["w"]) == width and int(p["h"]) == height:
            return p
    return CANVAS_PRESETS[0]


# =====================================================================
# QC — one block/advisory panel, the same shape Commercial Builder and
# Magic Resize already draw theirs in.
# =====================================================================
@app.route("/api/qc/check", methods=["POST"])
def api_qc_check():
    body = request.get_json(silent=True) or {}
    canvas = body.get("canvas") or {}
    if not isinstance(canvas, dict):
        return jsonify({"error": "Nothing to check."}), 400
    preset = _preset_for_canvas(body, canvas)
    size_bytes = clamp_int(body.get("size_bytes"), 0, 0, 200 * 1024 * 1024)
    fmt = str(body.get("format") or "png").strip().lower()
    return jsonify(qc.run(preset, canvas, size_bytes=size_bytes, fmt=fmt))


# =====================================================================
# Animated GIF export — the browser interpolates each object's entrance and
# rasterises one frame per timestamp (Fabric's own renderer, the only place
# the canvas — fonts, groups, crops — actually exists); this assembles the
# finished sequence with Pillow. See modules/image_creator/animation.py.
# =====================================================================
@app.route("/api/export/animated", methods=["POST"])
def api_export_animated():
    body = request.get_json(silent=True) or {}
    frames = body.get("frames") or []
    if not isinstance(frames, list) or not frames:
        return jsonify({"error": "No animated frames were captured — give at "
                                 "least one object an entrance first."}), 400
    frame_ms = clamp_int(body.get("frameMs"), animation.FRAME_INTERVAL_MS, 20, 2000)
    try:
        gif_bytes = animation.assemble_gif(frames, frame_ms=frame_ms)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"error": f"Animation export failed: {exc}"}), 502

    canvas = body.get("canvas") or {}
    preset = _preset_for_canvas(body, canvas)
    objects = canvas.get("objects") or []
    width = int(canvas.get("width") or preset.get("w") or 0)
    height = int(canvas.get("height") or preset.get("h") or 0)
    findings = (qc.check_dimensions_and_weight(
                    preset, width=width, height=height,
                    size_bytes=len(gif_bytes), fmt="gif")
               + qc.check_animation_duration(objects))

    _log("animated_export", detail=body.get("name", ""), client=body.get("client", ""))
    import base64 as _b64
    return jsonify({
        "ok": True,
        "image": "data:image/gif;base64," + _b64.b64encode(gif_bytes).decode(),
        "bytes": len(gif_bytes),
        "frames": len(frames),
        "duration_ms": frame_ms * max(0, len(frames) - 1),
        "qc": {"result": qc.verdict(findings), "findings": findings},
    })


# =====================================================================
# Client review link — a rep sends a graphic for approval, a client answers
# with no Hub login. Ported from modules/commercial_builder's review link
# (the more complete of the two prior implementations of this pattern).
# =====================================================================
def _share_url(token: str) -> str:
    """Absolute where the Hub knows its own address, root-relative otherwise
    — the same fallback modules/ads_builder's `_share_url` uses, since a
    guessed host is worse than a relative link and PUBLIC_BASE_URL unset
    means this process does not know its own address."""
    try:
        from hub.config import public_base_origin
        base = public_base_origin()
    except Exception:                                 # noqa: BLE001
        base = ""
    return f"{base}{MOUNT}/review/{token}" if base else f"{MOUNT}/review/{token}"


def _project_row(pid: str) -> dict | None:
    return next((r for r in projects.load_index() if r.get("id") == pid), None)


@app.route("/api/projects/<pid>/reviews", methods=["GET"])
def api_reviews_list(pid):
    rows = share_store.list_shares(pid)
    for r in rows:
        r["url"] = _share_url(r["token"])
        r["verdict"] = review_spec.verdict(r["decisions"])
        r["round_state"] = review_spec.round_state(r["round"])
    live = next((r for r in rows if not r["revoked"]), None)
    return jsonify({
        "reviews": rows,
        "current": live,
        "standing": (live or {}).get("verdict") or review_spec.verdict([]),
        "next_round": review_spec.round_state(len(rows) + 1),
    })


@app.route("/api/projects/<pid>/reviews", methods=["POST"])
def api_reviews_send(pid):
    row = _project_row(pid)
    if not row:
        return jsonify({"error": "Save this project before sending it for review."}), 404

    body = request.get_json(silent=True) or {}
    variants = body.get("variants")
    if not variants and row.get("preview_url"):
        # The project's own current preview, offered by default so sending a
        # review does not first require a separate export step. Once Magic
        # Resize can hand this route its own per-size renders, several land
        # here instead of one.
        variants = [{"label": row.get("name", ""), "url": row["preview_url"]}]
    if not variants:
        return jsonify({"error": "There is nothing to review yet — save the "
                                 "project (or export it) first."}), 400

    data = share_store.create_share(pid, created_by=actor_name(),
                                    message=str(body.get("message") or ""),
                                    variants=variants)
    data["url"] = _share_url(data["token"])
    state = review_spec.round_state(data["round"])
    data["round_state"] = state
    if state["over"]:
        _log("review_rounds_exceeded", project=pid, client=row.get("client", ""),
             detail=f"{state['label']} on {row.get('name', 'a graphic')}")
    _log("review_sent", project=pid, client=row.get("client", ""),
        detail=f"{state['label']} · {row.get('name', 'Untitled')}")
    return jsonify({"ok": True, "review": data})


@app.route("/api/projects/<pid>/reviews/<int:share_id>/revoke", methods=["POST"])
def api_reviews_revoke(pid, share_id):
    ok = share_store.revoke_share(pid, share_id)
    if ok:
        row = _project_row(pid) or {}
        _log("review_revoked", project=pid, client=row.get("client", ""))
    return jsonify({"ok": ok})


@app.route("/api/reviews/waiting")
def api_reviews_waiting():
    """Every live round across every project, sorted into who is waiting on
    whom. Never raises — a card drawing a clean zero over a table that would
    not answer is the confident wrong answer this is written to avoid."""
    try:
        rows = share_store.reviews_waiting()
        index = {r.get("id"): r for r in projects.load_index()}
        inbox_rows = []
        for r in rows:
            proj = index.get(r["project_id"], {})
            inbox_rows.append({
                "project_id": r["project_id"], "title": proj.get("name", ""),
                "client": proj.get("client", ""), "round_no": r["round_no"],
                "sent_at": r["sent_at"], "sent_by": r["sent_by"],
                "answered": r["verdict"]["answered"], "comments": r["comments"],
                "outcome": r["verdict"]["outcome"], "color": r["verdict"]["color"],
                "by": r["verdict"]["by"], "conflicting": r["verdict"]["conflicting"],
                "filed": False,
            })
        return jsonify({"ok": True, **review_spec.inbox(inbox_rows)})
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"ok": True, **review_spec.inbox_unmeasured(str(exc))})


# ---------------------------------------------------------------------------
# The public half — reached with nothing but the token, no Hub session.
# ---------------------------------------------------------------------------
def _live_share_or_none(token: str) -> dict | None:
    share = share_store.get_share(token)
    if not share or share["revoked"]:
        return None
    return share


@app.route("/review/<token>")
def page_client_review(token):
    share = _live_share_or_none(token)
    if not share:
        # Deliberately the same answer for revoked, deleted and never-existed
        # — a client-facing page must not confirm which tokens are real.
        return render_template("review_gone.html"), 404
    row = _project_row(share["project_id"]) or {}
    share_store.note_opened(token)
    return render_template(
        "review.html", token=token, share=share, project=row,
        outcomes=review_spec.OUTCOMES, outcome_labels=review_spec.OUTCOME_LABELS,
        round_state=review_spec.round_state(share["round"]))


@app.route("/review/<token>/comment", methods=["POST"])
def api_client_comment(token):
    share = _live_share_or_none(token)
    if not share:
        return jsonify({"error": "This review link is no longer available."}), 404
    clean = review_spec.clean_comment(request.get_json(silent=True) or {})
    if not clean["text"]:
        return jsonify({"error": "Type your note first."}), 400
    if not clean["reviewer_name"]:
        return jsonify({"error": "Please add your name — a note nobody can "
                                 "attribute is one we cannot come back to "
                                 "you about."}), 400
    updated = share_store.add_comment(token, clean["text"], clean["reviewer_name"],
                                      clean["reviewer_email"])
    row = _project_row(share["project_id"]) or {}
    _log("review_commented", project=share["project_id"], client=row.get("client", ""),
        detail=f"{clean['reviewer_name']} left a note on round {share['round']}")
    return jsonify({"ok": True, "comments": (updated or {}).get("comments", [])})


@app.route("/review/<token>/decide", methods=["POST"])
def api_client_decide(token):
    share = _live_share_or_none(token)
    if not share:
        return jsonify({"error": "This review link is no longer available."}), 404
    body = request.get_json(silent=True) or {}
    outcome = str(body.get("outcome") or "")
    if not review_spec.is_outcome(outcome):
        return jsonify({"error": "Choose one of the three answers."}), 400
    name = str(body.get("name") or "").strip()
    email = str(body.get("email") or "").strip()
    if review_spec.decision_requires_name(outcome) and (not name or "@" not in email):
        return jsonify({"error": "Please add your name and email. We record "
                                 "who signed a graphic off."}), 400
    updated = share_store.record_decision(token, outcome, name, email, body.get("note"))
    decisions = (updated or {}).get("decisions", [])
    resolved = review_spec.verdict(decisions)
    row = _project_row(share["project_id"]) or {}
    _log("review_answered", project=share["project_id"], client=row.get("client", ""),
        detail=f"{review_spec.OUTCOME_LABELS.get(outcome, outcome)} — {name}")
    return jsonify({"ok": True, "label": review_spec.OUTCOME_LABELS[outcome],
                    "verdict": resolved, "decisions": decisions})
