"""Smart 1 Hub — GPT Ads Builder.

Builds one GPT ad pack for a client we manage, and hands ad operations a
single file containing everything their requirement sheet asks for.

## The bottleneck this is aimed at

The sheet ad ops works from lists five things — a square image, copy options, a
landing page confirmed live and mobile-friendly, brand requirements, and offer
details. None of that is hard. What makes it slow is that it arrives in five
places: the image in a Slack thread, the copy in a doc, the URL in an email,
the brand colours in whoever's memory, and the offer in the client's own words
three weeks ago. A pack goes over with four of the five, comes back, and the
launch date moves.

So the flow is: pick a client, and the pack arrives already knowing who they
are — their brand kit, their gallery, their site. Everything after that is
filling gaps rather than assembling from nothing, and the tool will not call a
pack ready while a gap is still open.

    client  ->  offer & brand  ->  copy  ->  image  ->  landing check  ->  ZIP

Each step is separately re-runnable and none is destructive: generating copy
appends options rather than replacing them, and attaching an image never
discards the one already there without being told to.

## What is checked rather than trusted

`hub/gpt_ads_spec.py` holds the rules and explains each one. Two are worth
repeating here because they are the reason this is a tool rather than a form:

* **The image is measured.** Its real pixels go through
  ``hub/creative_specs.py`` — the same judgement the insertion order and the
  gallery use. A 1200x628 crop attached as "the square one" is refused here,
  not discovered by ad ops.

* **The landing page is fetched.** "Confirmed live and mobile-friendly" is
  answered by requesting the page, and a check that could not run says *not
  measured* rather than showing a tick.

## Storage

One file per pack under ``jsonstore.data_dir("gpt_ads")``, plus an index, both
through ``hub.jsonstore`` so they are mirrored into the database — the Render
disk is outside Render's backup, and a pack that existed only there would
vanish on a resize with nothing reading as an error. Deletes go through
``jsonstore.delete_json``; a bare ``os.remove`` leaves the database copy to be
restored on the next read, so the delete undoes itself.

The image bytes are *not* in that JSON. They live in Cloudinary through
``hub.storage``, with a local copy under ``images/`` used only to build the ZIP
without a round trip. That local copy is a cache: it is deliberately not
mirrored, and it is rebuilt by re-fetching the Cloudinary URL if it is missing.
"""
from __future__ import annotations

import io
import os
import re
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from hub import gpt_ads_spec as spec
from hub import images as hub_images
from hub import jsonstore, storage

try:
    from hub import audit as hub_audit
except Exception:                                     # noqa: BLE001
    hub_audit = None

BASE_DIR = Path(__file__).parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))

MODULE = "gpt_ads"
STORAGE_KIND = "gpt_ads"
MAX_PACKS = 400
MAX_UPLOAD_BYTES = 12 * 1024 * 1024

_lock = threading.Lock()


# ------------------------------------------------------------------ storage
def _dir() -> str:
    return jsonstore.data_dir("gpt_ads")


def _image_cache_dir() -> str:
    # A cache, not state: every file here is a copy of something already in
    # Cloudinary, and _image_bytes() re-fetches from that URL when it is
    # missing. That is why it is a plain write rather than jsonstore — there is
    # nothing here that losing the disk would lose.
    return jsonstore.data_dir("gpt_ads", "images")


def _index_path() -> str:
    return os.path.join(_dir(), "index.json")


def _pack_path(pack_id: str) -> str:
    return os.path.join(_dir(), f"ad-{pack_id}.json")


def _read_index() -> list[dict]:
    rows = jsonstore.read_json(_index_path(), default=[])
    return rows if isinstance(rows, list) else []


def _write_index(rows: list[dict]) -> None:
    jsonstore.write_json(_index_path(), rows[:MAX_PACKS], indent=1)


def _summarise(pack: dict) -> dict:
    """The index row. Counts are recomputed rather than stored twice — a
    summary that can disagree with the thing it summarises eventually does."""
    state = spec.readiness(pack)
    return {
        "id": pack["id"], "client": pack.get("client", ""),
        "domain": pack.get("domain", ""), "campaign": pack.get("campaign", ""),
        "status": pack.get("status", "draft"),
        "ready": state["ready"], "block": state["block"], "warn": state["warn"],
        "complete": state["complete"], "total": state["total"],
        "has_image": bool((pack.get("image") or {}).get("url")),
        "created_at": pack.get("created_at", ""),
        "created_by": pack.get("created_by", ""),
        "updated_at": pack.get("updated_at", ""),
    }


def load_pack(pack_id: str) -> dict | None:
    if not re.match(r"^[a-z0-9]{6,24}$", str(pack_id or "")):
        return None
    pack = jsonstore.read_json(_pack_path(pack_id), default=None)
    return pack if isinstance(pack, dict) and pack.get("id") else None


def save_pack(pack: dict) -> dict:
    pack["updated_at"] = _now()
    pack["updated_by"] = actor_name()
    spec.revalidate(pack)
    with _lock:
        jsonstore.write_json(_pack_path(pack["id"]), pack, indent=1)
        rows = [r for r in _read_index() if r.get("id") != pack["id"]]
        rows.insert(0, _summarise(pack))
        _write_index(rows)
    return pack


def delete_pack(pack_id: str) -> bool:
    with _lock:
        rows = _read_index()
        remaining = [r for r in rows if r.get("id") != pack_id]
        if len(remaining) == len(rows):
            return False
        # jsonstore.delete_json, never os.remove: removing only the file leaves
        # the database copy to be restored by the next read.
        jsonstore.delete_json(_pack_path(pack_id))
        _write_index(remaining)
    for name in os.listdir(_image_cache_dir()) if os.path.isdir(_image_cache_dir()) else []:
        if name.startswith(pack_id + "."):
            try:
                os.remove(os.path.join(_image_cache_dir(), name))
            except OSError:
                pass
    return True


# ------------------------------------------------------------------ helpers
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def actor_name() -> str:
    return request.environ.get("s1hub.user") or "Unknown"


def _new_id() -> str:
    return format(int(time.time() * 1000), "x")[-9:] + os.urandom(2).hex()


def _log(event: str, **extra):
    if hub_audit is None:
        return
    try:
        # audit.log()'s first positional is `module`; extras use tool=.
        hub_audit.log(MODULE, event, actor=actor_name(), **extra)
    except Exception:                                 # noqa: BLE001
        pass


def _version() -> str:
    try:
        from hub import version
        return version.label()
    except Exception:                                 # noqa: BLE001
        return ""


def _fail(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def _str(value, limit: int = 4000) -> str:
    return str(value if value is not None else "")[:limit]


def _body() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return request.form.to_dict() if request.form else {}


def _pack_from_body(data: dict | None = None):
    """Every write takes the pack id in the body rather than the URL.

    That keeps each route a literal path ``tools/linkcheck.py`` can verify
    against the route table — a URL built as ``"…/api/ads/" + id`` is invisible
    to it, which is how seven landing pages shipped with dead lead capture.
    """
    data = _body() if data is None else data
    return load_pack(_str(data.get("id"), 40).strip())


def _ok(pack: dict, **extra):
    payload = {"ok": True, "ad": pack, "readiness": spec.readiness(pack)}
    payload.update(extra)
    return jsonify(payload)


# ------------------------------------------------------------------ context
def _client_context(client: str, url: str = "") -> dict:
    """What the Hub already knows about this client — one shared reader.

    `hub/client_context.tool_context()`. This module and the GPT Ads Builder
    carried the same forty lines character for character, plus the same
    `_gallery_images` under them, which is the second copy CLAUDE.md names as
    a failure twice: the image-resize rule and the Pexels key each had to be
    found and fixed in more than one place, and the second one was missed.

    Moving it also gained the half neither copy had — the client's own site
    scan. Most of the local businesses this tool is used for publish no brand
    record anywhere, and their last Insites audit knows the logo, the palette
    their pages actually paint, the business name their site uses and their
    phone number.
    """
    from hub.client_context import tool_context
    return tool_context(client, url)


# ------------------------------------------------------------------ images
def _cache_image(pack_id: str, data: bytes, ext: str) -> str:
    try:
        os.makedirs(_image_cache_dir(), exist_ok=True)
        path = os.path.join(_image_cache_dir(), f"{pack_id}.{ext}")
        with open(path, "wb") as fh:
            fh.write(data)
        return path
    except OSError:
        return ""


def _image_bytes(pack: dict) -> tuple[bytes, str]:
    """The attached image's bytes, from the cache or from its URL.

    Returns ``(b"", reason)`` rather than raising: an export that cannot embed
    the file must still hand over the copy, the brief and the URL, and say
    plainly that the image is missing from the ZIP.
    """
    image = pack.get("image") or {}
    if not image.get("url"):
        return b"", "No image attached."
    ext = spec.image_extension(image)
    path = os.path.join(_image_cache_dir(), f"{pack['id']}.{ext}")
    if os.path.isfile(path):
        try:
            with open(path, "rb") as fh:
                return fh.read(), ""
        except OSError:
            pass
    url = str(image["url"])
    if not url.startswith("https://"):
        return b"", "The image is stored on this server's disk rather than " \
                    "Cloudinary and its local copy is gone."
    try:
        import requests
        r = requests.get(url, timeout=20)
        if r.status_code >= 400:
            return b"", f"The image URL returned HTTP {r.status_code}."
        data = r.content or b""
    except Exception as exc:                          # noqa: BLE001
        return b"", f"Could not fetch the image ({type(exc).__name__})."
    if data:
        _cache_image(pack["id"], data, ext)
    return data, "" if data else "The image URL returned nothing."


def _attach_image(pack: dict, data: bytes, *, filename: str, source: str,
                  alt: str = "", public_id: str = "", url: str = "") -> tuple[dict, str]:
    """Measure, store, file and attach one image. Returns (pack, error).

    The measurement happens first and on the bytes. Everything downstream —
    the readiness gate, the brief, the manifest — reads what was measured here,
    so a file that is not 1:1 is refused at the door rather than becoming a red
    flag somebody exports anyway.
    """
    try:
        width, height = hub_images.dimensions(data)
    except Exception:                                 # noqa: BLE001
        return pack, "That file could not be read as an image."
    if not (width and height):
        return pack, "That file could not be measured, so we cannot tell " \
                     "whether it is 1:1."
    if width != height:
        return pack, (f"That image is {width}x{height}. GPT ads require 1:1 — "
                      "crop it square in the Image Optimizer & Resizer "
                      "(/tools/image/) and attach it again.")

    fmt = (os.path.splitext(filename)[1] or "").lower().lstrip(".") or "png"
    if fmt == "jpeg":
        fmt = "jpg"

    stored_url, stored_id, mirror_failed = url, public_id, False
    if not stored_url:
        try:
            asset = storage.put(STORAGE_KIND, filename, data,
                                client=pack.get("client", ""),
                                subpath=pack["id"],
                                context={"client": pack.get("client", ""),
                                         "tool": "gpt_ads"})
            stored_url, stored_id = asset.url, asset.public_id
            # A disk fallback URL is not a link anyone outside this container
            # can open, and it is on the disk that is not backed up. Say so
            # rather than presenting it as a stored asset.
            mirror_failed = asset.backend != "cloudinary"
        except Exception as exc:                      # noqa: BLE001
            return pack, f"Could not store that image ({type(exc).__name__})."

    _cache_image(pack["id"], data, spec.image_extension({"format": fmt, "url": stored_url}))

    pack["image"] = {
        "url": stored_url, "public_id": stored_id,
        "width": width, "height": height, "bytes": len(data), "format": fmt,
        "source": source, "alt": _str(alt, 500),
        "mirror_failed": mirror_failed,
        "attached_at": _now(), "attached_by": actor_name(),
    }

    # Into the client's gallery, the way every other finished asset in the Hub
    # is filed — recording the public_id Cloudinary already has rather than
    # re-uploading it.
    if stored_url.startswith("https://") and pack.get("client"):
        try:
            from modules.image_picker.filing import file_asset
            file_asset(client_name=pack["client"], public_id=stored_id,
                       url=stored_url, kind="gpt_ad",
                       label=f"GPT ad — {pack.get('campaign') or 'square'}",
                       filename=filename, alt=alt, width=width, height=height,
                       size_bytes=len(data), provider=source or "gpt_ads",
                       saved_by=actor_name())
        except Exception:                             # noqa: BLE001
            pass                                      # the pack still has it
    return pack, ""


# =====================================================================
# Pages
# =====================================================================
@app.route("/")
def index():
    # Everything the page needs arrives as one JSON blob in a
    # <script type="application/json"> tag rather than as Jinja interpolated
    # into JavaScript, so tools/jscheck.py can hand the real script block to
    # node --check instead of skipping it.
    boot = {"spec": spec.spec_payload(),
            "client": request.args.get("client", "")[:200],
            "url": request.args.get("url", "")[:300],
            "open": request.args.get("ad", "")[:40]}
    return render_template("index.html", version=_version(), boot=boot)


@app.route("/health")
def health():
    return jsonify({"ok": True, "version": _version(),
                    "packs": len(_read_index())})


# =====================================================================
# Clients and context
# =====================================================================
@app.route("/api/clients")
def api_clients():
    q = _str(request.args.get("q", ""), 80).strip()
    try:
        from hub import clients_registry
        rows = clients_registry.search_clients(q, limit=12) if q else \
            clients_registry.all_clients()[:12]
    except Exception as exc:                          # noqa: BLE001
        return _fail(f"Client list unavailable ({type(exc).__name__}).", 502)
    out = [{"name": r.get("name", ""), "url": r.get("url", ""),
            "domain": r.get("domain", ""), "live": bool(r.get("live"))}
           for r in rows if r.get("name")]
    return jsonify({"ok": True, "clients": out})


@app.route("/api/context")
def api_context():
    client = _str(request.args.get("client", ""), 200).strip()
    if not client:
        return _fail("Pick a client first.")
    return jsonify({"ok": True,
                    "context": _client_context(
                        client, _str(request.args.get("url", ""), 300))})


# =====================================================================
# Packs
# =====================================================================
@app.route("/api/ads")
def api_list():
    return jsonify({"ok": True, "ads": _read_index()[:120]})


@app.route("/api/ads/create", methods=["POST"])
def api_create():
    data = _body()
    client = _str(data.get("client"), 200).strip()
    if not client:
        return _fail("Pick a client first.")
    context = _client_context(client, _str(data.get("url"), 300))
    pack = {
        "id": _new_id(),
        "client": client,
        "url": context.get("url", ""),
        "domain": context.get("domain", ""),
        "campaign": _str(data.get("campaign"), 200),
        "status": "draft",
        "offer": {"summary": "", "pricing": "", "product": "",
                  "eligibility": "", "expires": "", "restrictions": ""},
        # Prefilled from Brandfetch where we have it, blank where we do not.
        # A blank field a rep fills in is worth more than a plausible one they
        # skim past.
        "brand": {"logo_url": context.get("logo", ""),
                  "logo_usage": "", "colors": context.get("colors", []),
                  "tone": "", "claims": "", "disclaimer": "", "legal": "",
                  "avoid": "", "approver_name": "", "approver_email": ""},
        "landing": {"url": spec.normalise_url(context.get("url", "")),
                    "tracking": "", "check": {}},
        "copy": {"headlines": [], "bodies": [], "ctas": []},
        "image": {},
        "image_brief": "",
        "notes": "",
        "created_at": _now(),
        "created_by": actor_name(),
    }
    save_pack(pack)
    _log("pack_created", client=client, campaign=pack["campaign"])
    return _ok(pack, context=context)


@app.route("/api/ads/load", methods=["POST"])
def api_load():
    pack = _pack_from_body()
    if not pack:
        return _fail("That ad pack no longer exists.", 404)
    return _ok(pack, context=_client_context(pack.get("client", ""),
                                             pack.get("url", "")))


@app.route("/api/ads/save", methods=["POST"])
def api_save():
    data = _body()
    pack = _pack_from_body(data)
    if not pack:
        return _fail("That ad pack no longer exists.", 404)

    pack["campaign"] = _str(data.get("campaign", pack.get("campaign", "")), 200)
    pack["notes"] = _str(data.get("notes", pack.get("notes", "")), 6000)
    pack["image_brief"] = _str(data.get("image_brief", pack.get("image_brief", "")), 1000)

    if isinstance(data.get("offer"), dict):
        for key in ("summary", "pricing", "product", "eligibility", "expires",
                    "restrictions"):
            if key in data["offer"]:
                pack["offer"][key] = _str(data["offer"][key], 3000)

    if isinstance(data.get("brand"), dict):
        for key in ("logo_url", "logo_usage", "tone", "claims", "disclaimer",
                    "legal", "avoid", "approver_name", "approver_email"):
            if key in data["brand"]:
                pack["brand"][key] = _str(data["brand"][key], 3000)
        if "colors" in data["brand"]:
            pack["brand"]["colors"] = [_str(c, 40) for c in
                                       (data["brand"]["colors"] or [])][:8]

    if isinstance(data.get("landing"), dict):
        if "url" in data["landing"]:
            new_url = spec.normalise_url(data["landing"]["url"])
            if new_url != pack["landing"].get("url"):
                # The previous check belonged to the previous URL. Keeping it
                # would show a green "live" tick for a page nobody has fetched.
                pack["landing"]["check"] = {}
            pack["landing"]["url"] = new_url
        if "tracking" in data["landing"]:
            pack["landing"]["tracking"] = _str(data["landing"]["tracking"], 600)

    if isinstance(data.get("copy"), dict):
        for kind in ("headlines", "bodies", "ctas"):
            if kind not in data["copy"]:
                continue
            rows = []
            for item in (data["copy"][kind] or [])[:spec.MAX_OPTIONS]:
                text = _str(item.get("text") if isinstance(item, dict) else item, 500).strip()
                if text:
                    rows.append({"text": text, "flags": []})
            pack["copy"][kind] = rows

    if "image_alt" in data and pack.get("image", {}).get("url"):
        pack["image"]["alt"] = _str(data["image_alt"], 500)

    save_pack(pack)
    return _ok(pack)


@app.route("/api/ads/delete", methods=["POST"])
def api_delete():
    data = _body()
    pack = _pack_from_body(data)
    if not pack:
        return _fail("That ad pack no longer exists.", 404)
    delete_pack(pack["id"])
    _log("pack_deleted", client=pack.get("client", ""), pack=pack["id"])
    return jsonify({"ok": True})


@app.route("/api/ads/status", methods=["POST"])
def api_status():
    data = _body()
    pack = _pack_from_body(data)
    if not pack:
        return _fail("That ad pack no longer exists.", 404)
    wanted = _str(data.get("status"), 20)
    if wanted not in spec.STATUSES:
        return _fail("Unknown status.")
    state = spec.readiness(pack)
    if wanted == "ready" and not state["ready"]:
        return _fail(f"{state['block']} thing(s) would send this pack straight "
                     "back. They are listed against the deliverable they "
                     "belong to.")
    pack["status"] = wanted
    save_pack(pack)
    _log("pack_" + wanted, client=pack.get("client", ""),
         campaign=pack.get("campaign", ""))
    return _ok(pack)


# =====================================================================
# Copy — one request per kind
# =====================================================================
@app.route("/api/ads/copy", methods=["POST"])
def api_copy():
    """Write one kind of copy. The browser loops over the three so the loader
    can name what it is on and one failure costs one kind."""
    data = _body()
    pack = _pack_from_body(data)
    if not pack:
        return _fail("That ad pack no longer exists.", 404)
    kind = _str(data.get("kind"), 20)
    if kind not in ("headlines", "bodies", "ctas"):
        return _fail("Unknown copy type.")
    try:
        count = int(data.get("count") or spec.TARGET_OPTIONS[kind])
    except (TypeError, ValueError):
        count = spec.TARGET_OPTIONS[kind]
    count = max(1, min(spec.MAX_OPTIONS, count))

    from hub import ai
    context = _client_context(pack.get("client", ""), pack.get("url", ""))
    messages = spec.copy_messages(pack, kind, context, count)
    try:
        result = ai.chat_json(messages, module=MODULE, purpose=f"gptad:{kind}",
                              max_tokens=700, temperature=0.7)
    except Exception as exc:                          # noqa: BLE001
        # The provider's own wording never reaches the screen — it has echoed
        # key prefixes before. hub/ai.py already logged the real error.
        return _fail(f"Couldn't write those options ({type(exc).__name__}). "
                     "The rest of the pack is unaffected — try again.", 502)

    options = [o for o in (result.get("options") or []) if str(o).strip()]
    if not options:
        return _fail("The model returned no options. Try again.", 502)
    if kind == "ctas":
        # The list is the point: a CTA the platform does not offer is one ad
        # ops has to translate, and they will guess.
        known = {c.lower(): c for c in spec.CTA_OPTIONS}
        options = [known[str(o).strip().lower()] for o in options
                   if str(o).strip().lower() in known]
        if not options:
            return _fail("None of the returned calls to action are on the "
                         "approved list. Try again, or pick from the list.", 502)

    existing = {str(r.get("text", "")).strip().lower()
                for r in pack["copy"].get(kind) or []}
    added = 0
    for option in options:
        text = _str(option, 500).strip()
        if not text or text.lower() in existing:
            continue
        if len(pack["copy"][kind]) >= spec.MAX_OPTIONS:
            break
        # Appended, never replacing: an option a rep already edited and kept is
        # the one thing here a model cannot reproduce.
        pack["copy"][kind].append({"text": text, "flags": []})
        existing.add(text.lower())
        added += 1

    save_pack(pack)
    _log("copy_written", client=pack.get("client", ""), kind=kind, added=added)
    return _ok(pack, added=added)


# =====================================================================
# The square image
# =====================================================================
@app.route("/api/ads/image/generate", methods=["POST"])
def api_image_generate():
    data = _body()
    pack = _pack_from_body(data)
    if not pack:
        return _fail("That ad pack no longer exists.", 404)
    if "image_brief" in data:
        pack["image_brief"] = _str(data["image_brief"], 1000)

    from hub import ai
    context = _client_context(pack.get("client", ""), pack.get("url", ""))
    try:
        raw = ai.image(spec.image_prompt(pack, context), module=MODULE,
                       purpose="gptad:square", size="1024x1024")
    except Exception as exc:                          # noqa: BLE001
        return _fail(f"Image generation failed ({type(exc).__name__}). You can "
                     "still upload one or pick from the client's gallery.", 502)
    if not raw:
        return _fail("The image service returned nothing.", 502)

    # Through the shared optimiser, so the "cap the longest edge before
    # converting" rule stays in one place. JPEG at 92 rather than the house
    # default: this is the deliverable, and the sheet asks for the
    # highest-quality version available.
    try:
        processed = hub_images.optimise(raw, max_edge=1024, fmt="JPEG", quality=92)
        data_out = processed.data
    except Exception:                                 # noqa: BLE001
        data_out = raw

    pack, error = _attach_image(
        pack, data_out, filename=f"{spec.pack_filename(pack)}.jpg",
        source="generated", alt=_str(pack.get("image_brief"), 500))
    if error:
        return _fail(error, 502)
    save_pack(pack)
    _log("image_generated", client=pack.get("client", ""),
         campaign=pack.get("campaign", ""))
    return _ok(pack)


@app.route("/api/ads/image/upload", methods=["POST"])
def api_image_upload():
    pack = _pack_from_body(request.form.to_dict())
    if not pack:
        return _fail("That ad pack no longer exists.", 404)
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return _fail("No file arrived.")
    raw = upload.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        return _fail(f"That file is over {MAX_UPLOAD_BYTES // (1024*1024)} MB.")
    if not hub_images.is_image(upload.filename):
        return _fail("That is not an image file.")

    # The bytes are stored exactly as they arrived. "Provide the
    # highest-quality brand-approved version available" is on the requirement
    # sheet, and re-encoding an approved brand asset to save a few KB is how a
    # logo picks up compression artefacts nobody approved.
    pack, error = _attach_image(pack, raw, filename=upload.filename,
                               source="uploaded",
                               alt=_str(request.form.get("alt"), 500))
    if error:
        return _fail(error)
    save_pack(pack)
    _log("image_uploaded", client=pack.get("client", ""),
         file=upload.filename[:120])
    return _ok(pack)


@app.route("/api/ads/image/gallery", methods=["POST"])
def api_image_gallery():
    """Attach one of the client's existing images.

    Its bytes are fetched and measured rather than trusting the width and
    height the gallery row carries — those are what the provider reported when
    it was saved, and a wrong one here means a rejected ad.
    """
    data = _body()
    pack = _pack_from_body(data)
    if not pack:
        return _fail("That ad pack no longer exists.", 404)
    url = _str(data.get("url"), 700).strip()
    if not url.startswith("https://"):
        return _fail("That image has no stored URL.")

    try:
        import requests
        r = requests.get(url, timeout=20)
        raw = r.content if r.status_code < 400 else b""
    except Exception as exc:                          # noqa: BLE001
        return _fail(f"Could not read that image ({type(exc).__name__}).", 502)
    if not raw:
        return _fail("That image could not be downloaded, so it cannot be "
                     "measured.", 502)

    name = os.path.basename(url.split("?")[0]) or "gallery-image.jpg"
    pack, error = _attach_image(pack, raw, filename=name, source="gallery",
                               alt=_str(data.get("alt"), 500),
                               public_id=_str(data.get("public_id"), 400),
                               url=url)
    if error:
        return _fail(error)
    save_pack(pack)
    _log("image_from_gallery", client=pack.get("client", ""))
    return _ok(pack)


@app.route("/api/ads/image/clear", methods=["POST"])
def api_image_clear():
    pack = _pack_from_body()
    if not pack:
        return _fail("That ad pack no longer exists.", 404)
    pack["image"] = {}
    save_pack(pack)
    return _ok(pack)


# =====================================================================
# The landing page
# =====================================================================
@app.route("/api/ads/landing/check", methods=["POST"])
def api_landing_check():
    data = _body()
    pack = _pack_from_body(data)
    if not pack:
        return _fail("That ad pack no longer exists.", 404)
    if "url" in data:
        pack["landing"]["url"] = spec.normalise_url(data["url"])
    if "tracking" in data:
        pack["landing"]["tracking"] = _str(data["tracking"], 600)
    if not pack["landing"].get("url"):
        return _fail("Enter the destination URL first.")
    pack["landing"]["check"] = spec.check_landing_page(pack["landing"]["url"])
    save_pack(pack)
    _log("landing_checked", client=pack.get("client", ""),
         status=pack["landing"]["check"].get("status", 0))
    return _ok(pack)


# =====================================================================
# The handoff
# =====================================================================
def _export_pack(pack: dict) -> tuple[bytes, str]:
    """The ZIP ad operations receives.

    Everything on the requirement sheet, in one file, plus a manifest saying
    what is in it. When the image cannot be embedded the ZIP still goes — with
    the reason written into the brief and the manifest, because a pack that
    silently arrives without its image is one ad ops assumes they missed.
    """
    base = spec.pack_filename(pack)
    image_bytes, image_error = _image_bytes(pack)
    image_name = ""
    if image_bytes:
        image_name = f"{base}-1x1.{spec.image_extension(pack.get('image'))}"

    brief = spec.handoff_brief(pack, image_filename=image_name)
    if image_error:
        brief += ("\nIMAGE NOT INCLUDED IN THIS ZIP\n" + "-" * 60 + "\n"
                  + image_error + "\n"
                  + (f"It is at: {(pack.get('image') or {}).get('url')}\n"
                     if (pack.get("image") or {}).get("url") else ""))

    manifest = spec.manifest(pack, image_filename=image_name)
    if image_error:
        manifest["image"]["file"] = ""
        manifest["image"]["not_included"] = image_error

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{base}/README-handoff.txt", brief)
        zf.writestr(f"{base}/ad-copy.csv", spec.copy_csv(pack))
        zf.writestr(f"{base}/manifest.json", spec.as_json(manifest))
        if image_bytes:
            zf.writestr(f"{base}/{image_name}", image_bytes)
    return buf.getvalue(), base


@app.route("/api/export.zip", methods=["POST"])
def api_export_zip():
    """A form POST, not a link, so the URL stays a literal linkcheck verifies
    and the pack id travels in the body."""
    pack = _pack_from_body(request.form.to_dict() or _body())
    if not pack:
        return _fail("That ad pack no longer exists.", 404)
    data, base = _export_pack(pack)
    state = spec.readiness(pack)
    _log("exported", client=pack.get("client", ""),
         campaign=pack.get("campaign", ""), ready=state["ready"],
         outstanding=state["block"])
    return Response(data, mimetype="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{base}.zip"'})


@app.route("/api/export.csv", methods=["POST"])
def api_export_csv():
    pack = _pack_from_body(request.form.to_dict() or _body())
    if not pack:
        return _fail("That ad pack no longer exists.", 404)
    _log("exported_copy", client=pack.get("client", ""))
    return Response(spec.copy_csv(pack), mimetype="text/csv", headers={
        "Content-Disposition":
            f'attachment; filename="{spec.pack_filename(pack)}-copy.csv"'})
