"""Smart 1 Hub — Background Remover.

Upload an image, get a clean PNG cut-out back, optionally resized, optionally
saved to Cloudinary against a client. Built for the everyday jobs: lifting a
product or a person off their background, turning a photographed logo into
something usable, and producing the transparent asset the Image Creator then
places on a canvas.

Cut-outs come from remove.bg. It is a paid API, so the module is deliberately
careful with credits:

* The account balance is read and shown before you spend anything.
* Files are validated and pre-resized locally, so a credit is never spent on
  something that was going to fail.
* Results are cached by content hash for the session — re-running the same
  image (a double-click, a retry after a resize tweak) is free.
* Batch is capped and reports exactly what each image cost.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import threading
import time
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, render_template, request

try:
    from hub import audit as hub_audit
except Exception:                                     # noqa: BLE001
    hub_audit = None

BASE_DIR = Path(__file__).parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024

MAX_FILES = 10
MAX_BYTES = 12 * 1024 * 1024
ALLOWED = {"image/jpeg", "image/png", "image/webp"}
API = "https://api.remove.bg/v1.0/removebg"

SIZE_PRESETS = {
    "auto": ("Full available resolution", 0),
    "2400": ("Max 2400px", 2400),
    "1600": ("Max 1600px", 1600),
    "1200": ("Max 1200px", 1200),
    "800": ("Max 800px", 800),
}

# content-hash -> (timestamp, png bytes). Keeps retries from costing a credit.
_results: dict[str, tuple[float, bytes]] = {}
_lock = threading.Lock()
_TTL = 45 * 60

_CLOUD_URL = (os.environ.get("CLOUDINARY_URL") or "").strip()
try:
    import cloudinary
    import cloudinary.uploader
    if _CLOUD_URL.startswith("cloudinary://"):
        cloudinary.config(secure=True)
        CLOUD_READY = True
    else:
        CLOUD_READY = False
except ImportError:                                   # pragma: no cover
    cloudinary = None
    CLOUD_READY = False

FOLDER = os.environ.get("BG_REMOVER_FOLDER", "smart1-cutouts")


def api_key() -> str:
    return (os.environ.get("REMOVE_BG_API_KEY") or "").strip()


def configured() -> bool:
    return bool(api_key())


def actor_name() -> str:
    return request.environ.get("s1hub.user") or "Unknown"


def _log(event: str, **extra):
    if hub_audit is not None:
        try:
            hub_audit.log("bg_remover", event, actor=actor_name(), **extra)
        except Exception:                             # noqa: BLE001
            pass


def _version() -> str:
    try:
        from hub import version
        return version.label()
    except Exception:                                 # noqa: BLE001
        return ""


def _slug(v: str, fallback: str = "cutout") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(v or "").lower()).strip("-")
    return s[:80] or fallback


def _sweep():
    cutoff = time.time() - _TTL
    with _lock:
        for k in [k for k, v in _results.items() if v[0] < cutoff]:
            _results.pop(k, None)


def resize_max_edge(data: bytes, max_edge: int) -> tuple[bytes, dict]:
    """Cap the longest edge before upload. remove.bg charges by output
    resolution, so this controls cost as well as file size."""
    info = {"resized": False, "from": None, "to": None}
    if not max_edge:
        return data, info
    try:
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)
            info["from"] = list(im.size)
            if max(im.size) <= max_edge:
                info["to"] = list(im.size)
                return data, info
            im.thumbnail((max_edge, max_edge), Image.LANCZOS)
            info["to"] = list(im.size)
            info["resized"] = True
            buf = io.BytesIO()
            if im.mode in ("RGBA", "LA", "P"):
                im.convert("RGBA").save(buf, "PNG", optimize=True)
            else:
                im.convert("RGB").save(buf, "JPEG", quality=92)
            return buf.getvalue(), info
    except Exception:                                 # noqa: BLE001
        return data, info


def _post_resize(png: bytes, max_edge: int) -> bytes:
    """Resize the finished cut-out while preserving its alpha channel."""
    if not max_edge:
        return png
    try:
        from PIL import Image
        with Image.open(io.BytesIO(png)) as im:
            if max(im.size) <= max_edge:
                return png
            im = im.convert("RGBA")
            im.thumbnail((max_edge, max_edge), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=True)
            return buf.getvalue()
    except Exception:                                 # noqa: BLE001
        return png


def call_remove_bg(data: bytes, size: str = "auto") -> bytes:
    key = api_key()
    if not key:
        raise RuntimeError("REMOVE_BG_API_KEY is not set.")
    r = requests.post(API, headers={"X-Api-Key": key},
                      files={"image_file": ("image", data)},
                      data={"size": size, "format": "png"}, timeout=90)
    if r.status_code == 402:
        raise RuntimeError("remove.bg is out of credits on this account.")
    if r.status_code == 403:
        raise RuntimeError("remove.bg rejected the API key.")
    if not r.ok:
        detail = ""
        try:
            errs = r.json().get("errors") or []
            detail = errs[0].get("title") or errs[0].get("detail") or ""
        except Exception:                             # noqa: BLE001
            detail = r.text[:140]
        raise RuntimeError(f"remove.bg {r.status_code}: {detail}")
    if not r.content.startswith(b"\x89PNG"):
        raise RuntimeError("remove.bg returned something that isn't a PNG.")
    return r.content


# =====================================================================
# Pages
# =====================================================================
@app.route("/")
def index():
    return render_template("index.html", version=_version(),
                           configured=configured(), cloud=CLOUD_READY,
                           presets=SIZE_PRESETS,
                           client=request.args.get("client", ""))


@app.route("/health")
def health():
    return jsonify({"ok": True, "configured": configured(),
                    "cloudinary": CLOUD_READY, "version": _version()})


@app.route("/api/account")
def api_account():
    """Credit balance, so nobody starts a batch of 10 with 3 credits left."""
    key = api_key()
    if not key:
        return jsonify({"configured": False})
    try:
        r = requests.get("https://api.remove.bg/v1.0/account",
                         headers={"X-Api-Key": key}, timeout=15)
        if not r.ok:
            return jsonify({"configured": True, "error": f"remove.bg {r.status_code}"})
        attrs = (r.json().get("data") or {}).get("attributes") or {}
        credits = attrs.get("credits") or {}
        return jsonify({"configured": True,
                        "total": credits.get("total"),
                        "subscription": credits.get("subscription"),
                        "payg": credits.get("payg"),
                        "free_calls": (attrs.get("api") or {}).get("free_calls")})
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"configured": True, "error": str(exc)})


# =====================================================================
# Removal
# =====================================================================
@app.route("/api/remove", methods=["POST"])
def api_remove():
    if not configured():
        return jsonify({"error": "REMOVE_BG_API_KEY isn't set, so cut-outs are "
                                 "unavailable. Add it and redeploy."}), 503

    uploads = [f for f in request.files.getlist("images") if f and f.filename]
    if not uploads:
        return jsonify({"error": "Choose at least one image."}), 400
    if len(uploads) > MAX_FILES:
        return jsonify({"error": f"Up to {MAX_FILES} images at a time."}), 400

    pre_key = (request.form.get("pre_resize") or "auto").strip()
    post_key = (request.form.get("post_resize") or "auto").strip()
    pre_edge = SIZE_PRESETS.get(pre_key, ("", 0))[1]
    post_edge = SIZE_PRESETS.get(post_key, ("", 0))[1]
    rb_size = (request.form.get("quality") or "auto").strip()
    if rb_size not in ("preview", "auto", "full"):
        rb_size = "auto"

    _sweep()
    results, errors, credits_used = [], [], 0

    for up in uploads:
        raw = up.read()
        if not raw:
            errors.append(f"{up.filename}: empty file.")
            continue
        if len(raw) > MAX_BYTES:
            errors.append(f"{up.filename}: over {MAX_BYTES // (1024*1024)} MB.")
            continue
        if (up.mimetype or "").lower() not in ALLOWED:
            errors.append(f"{up.filename}: only JPG, PNG and WebP are supported.")
            continue

        sized, pre_info = resize_max_edge(raw, pre_edge)
        digest = hashlib.sha256(sized + rb_size.encode()).hexdigest()

        with _lock:
            cached = _results.get(digest)
        if cached and cached[0] > time.time() - _TTL:
            png, billed = cached[1], False
        else:
            try:
                png = call_remove_bg(sized, rb_size)
                billed = True
                credits_used += 1
            except Exception as exc:                  # noqa: BLE001
                errors.append(f"{up.filename}: {exc}")
                continue
            with _lock:
                _results[digest] = (time.time(), png)

        out = _post_resize(png, post_edge)
        import base64
        dims = None
        try:
            from PIL import Image
            with Image.open(io.BytesIO(out)) as im:
                dims = list(im.size)
        except Exception:                             # noqa: BLE001
            pass

        results.append({
            "id": digest[:16],
            "original_name": up.filename[:200],
            "name": _slug(Path(up.filename).stem, "cutout") + ".png",
            "image": "data:image/png;base64," + base64.b64encode(out).decode(),
            "bytes": len(out),
            "original_bytes": len(raw),
            "dimensions": dims,
            "pre_resize": pre_info,
            "billed": billed,
        })

    if not results:
        return jsonify({"error": "Nothing could be processed. " + " ".join(errors)}), 400

    _log("backgrounds_removed", count=len(results), credits=credits_used)
    return jsonify({"ok": True, "results": results, "errors": errors,
                    "credits_used": credits_used})


@app.route("/api/save", methods=["POST"])
def api_save():
    """Push a finished cut-out to Cloudinary so it can be reused."""
    if not CLOUD_READY:
        return jsonify({"error": "Cloudinary isn't configured."}), 503
    body = request.get_json(silent=True) or {}
    image = body.get("image") or ""
    if not image.startswith("data:image"):
        return jsonify({"error": "Nothing to save."}), 400
    import base64
    try:
        raw = base64.b64decode(image.split(",", 1)[1])
    except Exception:                                 # noqa: BLE001
        return jsonify({"error": "That image couldn't be read."}), 400

    client = str(body.get("client") or "").strip()[:200]
    name = _slug(body.get("name") or "cutout", "cutout")
    try:
        res = cloudinary.uploader.upload(
            raw, folder=f"{FOLDER}/{_slug(client, 'unfiled')}", public_id=name,
            overwrite=False, unique_filename=True, use_filename=False,
            format="png",                              # PNG keeps the alpha
            context={"client": client, "source": "background-remover"})
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"error": f"Upload failed: {exc}"}), 502
    _log("cutout_saved", detail=client, name=name)
    return jsonify({"ok": True, "url": res.get("secure_url", ""),
                    "public_id": res.get("public_id", ""),
                    "width": res.get("width"), "height": res.get("height"),
                    "bytes": res.get("bytes")})


@app.route("/api/clients")
def api_clients():
    try:
        from hub import clients_registry
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"clients": [], "error": str(exc)})
    rows = clients_registry.search_clients(request.args.get("q", ""), limit=10)
    return jsonify({"clients": [{"name": r["name"], "slug": r["slug"],
                                 "domain": r.get("domain", ""),
                                 "is_house": r.get("is_house", False)} for r in rows]})
