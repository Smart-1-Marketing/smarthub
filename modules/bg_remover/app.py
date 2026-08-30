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

MAX_FILES = 10
MAX_BYTES = 12 * 1024 * 1024

# Two caps that contradicted each other, and only one of them was on screen.
# The tool offers ten images at twelve megabytes each -- which is 120 MB, and
# Flask was set to refuse the request body at 40. So a batch inside every rule
# the page states was refused by the framework before the view ran, and
# MAX_CONTENT_LENGTH raises before any of this module's own carefully worded
# per-file messages can be reached: what came back was Werkzeug's HTML 413
# page, which `.then(r => r.json())` cannot parse, so the page reported
# "Failed: SyntaxError" and said nothing about sending fewer at a time.
#
# It is derived from the two numbers the tool actually offers rather than
# typed again, with room for the multipart boundaries and the form fields, so
# the framework cannot go back to refusing what the screen invites. And the
# handler below answers in the same JSON shape every other refusal here uses,
# because a limit reached from any other direction must still arrive as a
# sentence rather than as a parse error.
MAX_BATCH_BYTES = MAX_FILES * MAX_BYTES
app.config["MAX_CONTENT_LENGTH"] = MAX_BATCH_BYTES + 2 * 1024 * 1024
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
#
# In memory AND on the shared data disk, because this one is per process and
# gunicorn runs two workers. The docstring above promises that re-running the
# same image -- a double-click, a retry after a resize tweak -- is free, and
# on this deployment it was free about half the time: the second request lands
# on whichever worker the load balancer picks, that worker's dict is empty,
# and remove.bg is asked again and charges again. Every screen reports a clean
# success either way, and the only evidence is a credit balance that falls
# faster than the number of cut-outs anybody made.
#
# This is the `_state`-is-per-process trap the scheduler panel, the client
# registry's two-minute cache and suite_panel's double-submit claim have each
# had to undo, on the one module whose own docstring opens by saying it is
# deliberately careful with credits -- so here it costs money rather than a
# confusing panel. suite_panel moved its claim to a file on the shared disk;
# so does this.
#
# The disk copy is a cache and nothing else: there is nothing to restore, a
# wiped disk costs a credit on the next retry and no data at all -- so it is
# written as plain files rather than through hub/jsonstore.py, which mirrors
# into the database and is for state whose loss matters. It is bounded on
# total size as well as on age, because an unbounded cache on the 5 GB disk
# takes the whole Hub with it.
_results: dict[str, tuple[float, bytes]] = {}
_lock = threading.Lock()
_TTL = 45 * 60
_CACHE_MAX_BYTES = 200 * 1024 * 1024


def _cache_dir() -> str | None:
    """Where the shared copy lives, or None if we cannot have one.

    Never raises. A cache that can break the tool it accelerates is worse than
    no cache, so every failure here costs a credit on a retry and nothing else.
    """
    try:
        from hub import jsonstore
        path = os.path.join(jsonstore.data_dir("bg_remover"), "cache")
        os.makedirs(path, exist_ok=True)
        return path
    except Exception:                                 # noqa: BLE001
        return None


def _cache_get(digest: str) -> bytes | None:
    with _lock:
        hit = _results.get(digest)
    if hit and hit[0] > time.time() - _TTL:
        return hit[1]
    folder = _cache_dir()
    if not folder:
        return None
    path = os.path.join(folder, digest + ".png")
    try:
        if os.path.getmtime(path) <= time.time() - _TTL:
            return None
        with open(path, "rb") as fh:
            png = fh.read()
    except OSError:
        return None
    if not png:
        return None
    with _lock:                       # warm this worker, so the next is local
        _results[digest] = (time.time(), png)
    return png


def _cache_put(digest: str, png: bytes) -> None:
    with _lock:
        _results[digest] = (time.time(), png)
    folder = _cache_dir()
    if not folder:
        return
    tmp = os.path.join(folder, digest + ".tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(png)
        os.replace(tmp, os.path.join(folder, digest + ".png"))
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _disk_sweep(cutoff: float) -> None:
    """Drop what has expired, then the oldest until the cache is under its cap."""
    folder = _cache_dir()
    if not folder:
        return
    try:
        entries = []
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_mtime < cutoff or name.endswith(".tmp"):
                try:
                    os.unlink(path)
                except OSError:
                    pass
                continue
            entries.append((st.st_mtime, st.st_size, path))
        total = sum(e[1] for e in entries)
        for _, size, path in sorted(entries):
            if total <= _CACHE_MAX_BYTES:
                break
            try:
                os.unlink(path)
                total -= size
            except OSError:
                pass
    except OSError:
        pass

# Through hub.config, which also composes the URL from CLOUDINARY_CLOUD_NAME /
# CLOUDINARY_API_KEY / CLOUDINARY_API_SECRET when only those are set.
try:
    from hub.config import settings as _hub_cfg
    _CLOUD_URL = _hub_cfg.cloudinary_url
except Exception:                                     # noqa: BLE001
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
    """The remove.bg key, under whichever name it is set.

    Read through hub.config at call time, not os.environ at import: this
    deployment names provider keys three different ways (REMOVE_BG_API,
    REMOVE_BG_API_KEY, REMOVEBG_API_KEY) and a module that knows one of them
    reports "not configured" over a key that is plainly there, with the
    Background Remover disabled and nothing saying why.
    """
    try:
        from hub.config import settings
        return (settings.remove_bg_key or "").strip()
    except Exception:                                 # noqa: BLE001
        return (os.environ.get("REMOVE_BG_API")
                or os.environ.get("REMOVE_BG_API_KEY")
                or os.environ.get("REMOVEBG_API_KEY") or "").strip()


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


def _dimensions(data: bytes) -> tuple[int | None, int | None]:
    """How big an image is, or (None, None) where it cannot be read.

    One reading, because two call sites want it and only one of them had it:
    the result panel measured every cut-out and the save path asked a
    StoredAsset for a `width` it does not carry. Never raises -- a size we
    could not read is an absent size, and absent must not read as zero.
    """
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return int(im.size[0]), int(im.size[1])
    except Exception:                                 # noqa: BLE001
        return None, None


def _sweep():
    cutoff = time.time() - _TTL
    with _lock:
        for k in [k for k, v in _results.items() if v[0] < cutoff]:
            _results.pop(k, None)
    _disk_sweep(cutoff)


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
    # Only a successful call spends a credit. Failures above raise before this,
    # so a rejected key or an out-of-credits response is never counted as usage.
    try:
        from hub import quotas as _q
        _q.record("removebg", module="bg_remover")
    except Exception:                                 # noqa: BLE001
        pass
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


@app.errorhandler(413)
def _too_large(_exc):
    """A refusal the page can read, in the words somebody can act on."""
    return jsonify({"error": (
        f"That batch is larger than the {MAX_BATCH_BYTES // (1024 * 1024)} MB "
        f"this tool takes at once. Send fewer images, or up to "
        f"{MAX_FILES} of {MAX_BYTES // (1024 * 1024)} MB each.")}), 413


@app.route("/health")
def health():
    return jsonify({"ok": True, "configured": configured(),
                    "cloudinary": CLOUD_READY, "version": _version(),
                    "max_files": MAX_FILES,
                    "max_file_mb": MAX_BYTES // (1024 * 1024),
                    "max_batch_mb": MAX_BATCH_BYTES // (1024 * 1024)})


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

        cached = _cache_get(digest)
        if cached:
            png, billed = cached, False
        else:
            try:
                png = call_remove_bg(sized, rb_size)
                billed = True
                credits_used += 1
            except Exception as exc:                  # noqa: BLE001
                errors.append(f"{up.filename}: {exc}")
                continue
            _cache_put(digest, png)

        out = _post_resize(png, post_edge)
        import base64
        w, h = _dimensions(out)
        dims = [w, h] if w else None

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
    # Measured here, from the bytes about to be stored, because the row this
    # files into the client's gallery carries a width and a height and every
    # cut-out this tool has ever filed carried neither. The call below asked a
    # StoredAsset for a width, which it has no field for and never has -- so
    # both were None on every save, silently, while api_remove measured the
    # identical bytes two functions earlier and threw the answer away. Not
    # read back from the browser: what is stored is what should be described,
    # and a dimension the page supplies is one the page can be wrong about.
    width, height = _dimensions(raw)
    try:
        # Through hub.storage. The .png on the name is what carries the
        # "keep the alpha" intent now — the shared derivation reads the
        # extension, so format= is no longer asserted separately from it.
        from hub import storage
        _folder = f"{FOLDER}/{_slug(client, 'unfiled')}"
        _asset = storage.put("cutouts", f"{name}.png", raw,
                             folder=_folder, public_id=f"{_folder}/{name}",
                             overwrite=False,
                             context={"client": client,
                                      "source": "background-remover"})
        res = {"secure_url": _asset.url, "public_id": _asset.public_id,
               "bytes": _asset.bytes}
    except Exception as exc:                          # noqa: BLE001
        return jsonify({"error": f"Upload failed: {exc}"}), 502
    # `client=`, not `detail=`. The comment below explains at length that a
    # cut-out has to reach the client's gallery or it is absent from the one
    # page somebody opens to see what we have made for them -- and the
    # activity-log half was landing in `detail`, which work_log() does not
    # read, so the row was written and then dropped before the record. Half
    # the fix was done and the other half was one keyword away.
    _log("cutout_saved", client=client or None, name=name)
    # Into the client's gallery, not only into a Cloudinary folder. The folder
    # was the only record that a cut-out belonged to anybody, and no screen
    # reads Cloudinary folders -- so every cut-out this tool has ever made was
    # absent from the one page somebody opens to see what we have produced for
    # a client. A cut-out with no client named lands under `unfiled/` and is
    # filed nowhere, which is what hub/image_audit.py now reports.
    gallery = {}
    if client:
        try:
            from modules.image_picker.filing import file_asset
            gallery = file_asset(
                client_name=client, public_id=res.get("public_id", ""),
                url=res.get("secure_url", ""), kind="cutout",
                filename=f"{name}.png",
                alt=f"{name.replace('-', ' ')} cut-out for {client}",
                provider="bg_remover", saved_by=actor_name(),
                width=width, height=height,
                size_bytes=res.get("bytes"))
        except Exception as exc:                      # noqa: BLE001
            gallery = {"ok": False, "error": str(exc)}
    return jsonify({"ok": True, "url": res.get("secure_url", ""),
                    "filed": bool(gallery.get("ok")),
                    "gallery_url": gallery.get("gallery_url", ""),
                    "public_id": res.get("public_id", ""),
                    "width": width, "height": height,
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
