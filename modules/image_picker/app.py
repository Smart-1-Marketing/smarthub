"""
Image Picker -- Flask blueprint, mounted at /tools/image-picker.

Two audiences, one picker UI:

  staff   /tools/image-picker/            behind the Hub login
          /tools/image-picker/c/<id>      pick on behalf of a client
  client  /tools/image-picker/pick/<tok>  share link, no login

Security posture, informed by the suite QA audit:

* **Search results are HMAC-signed.** The browser sends chosen images back at
  save time; if we trusted the URLs it posted, this endpoint would be an SSRF
  hole exactly like the one found in smart1restaurant. Every result is signed on
  the way out and verified on the way in, and the host is checked against a
  provider allow-list on top of that. A tampered item is refused, not fetched.
* **Rate limiting reads the LAST X-Forwarded-For hop**, not the first. The audit
  found three apps trusting the client-supplied first hop, which meant a forged
  header bypassed the limiter entirely.
* **No raw exception text reaches the browser.** Errors are logged with detail
  and returned as plain sentences. The audit caught an OpenAI 401 printing an
  API key prefix onto a public lead page.
* **Share tokens are `secrets.token_urlsafe(32)`**, not a slug plus a timestamp.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone
from collections import defaultdict, deque
from functools import wraps
from urllib.parse import urlparse

from flask import (
    Blueprint, abort, current_app, g, jsonify, redirect,
    render_template, request, url_for,
)
from sqlalchemy import func, select

from . import cloudinary_sink, filing, ghl, providers, taxonomy
from .models import (
    DB_BOOT_ERROR, PickerClient, SavedImage, already_saved, get_client,
    get_client_by_token, init_db, new_token, session, slugify, unique_slug,
)

# Activity logging. Guarded so this module still runs standalone, but
# inside the Hub every action is attributable — this module saves images to a client library, and
# an unattributable change to a client's account is one nobody can
# explain later.
try:
    from hub import audit as _hub_audit
    _audit = _hub_audit.for_module("image_picker")
except Exception:  # noqa: BLE001
    def _audit(*a, **k):  # no-op outside the Hub
        return None


log = logging.getLogger(__name__)

bp = Blueprint(
    "image_picker",
    __name__,
    url_prefix="/tools/image-picker",
    template_folder="templates",
    static_folder="static",
)

MAX_SAVE_BATCH = 24

# Hosts we will let Cloudinary fetch from. Belt and braces alongside the HMAC.
ALLOWED_IMAGE_HOSTS = {
    "images.pexels.com", "www.pexels.com", "pexels.com",
    "pixabay.com", "cdn.pixabay.com", "www.pixabay.com",
    "images.unsplash.com", "unsplash.com", "plus.unsplash.com",
}


# --------------------------------------------------------------------------- #
# Signing
# --------------------------------------------------------------------------- #

def _secret() -> bytes:
    key = (
        os.environ.get("IMAGE_PICKER_SIGNING_KEY")
        or os.environ.get("SECRET_KEY")
        or current_app.config.get("SECRET_KEY")
        or ""
    )
    if not key:
        # Deliberately loud. An unsigned build would accept arbitrary URLs.
        raise RuntimeError("SECRET_KEY or IMAGE_PICKER_SIGNING_KEY must be set")
    return str(key).encode()


def _signable(item: dict) -> bytes:
    return json.dumps(
        {
            "id": item.get("id"),
            "provider": item.get("provider"),
            "provider_image_id": item.get("provider_image_id"),
            "full": item.get("full"),
            "preview": item.get("preview"),
            "download_location": item.get("download_location", ""),
        },
        sort_keys=True, separators=(",", ":"),
    ).encode()


def sign_item(item: dict) -> str:
    return hmac.new(_secret(), _signable(item), hashlib.sha256).hexdigest()[:32]


def verify_item(item: dict) -> bool:
    given = str(item.get("sig") or "")
    if not given:
        return False
    return hmac.compare_digest(given, sign_item(item))


def host_allowed(url: str) -> bool:
    try:
        parsed = urlparse(url or "")
    except Exception:  # noqa: BLE001
        return False
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_IMAGE_HOSTS


# --------------------------------------------------------------------------- #
# Auth + rate limiting
# --------------------------------------------------------------------------- #

def client_ip() -> str:
    """Last X-Forwarded-For hop -- the one the proxy actually wrote.

    MERGE NOTE: if the Hub already applies `ProxyFix(x_for=1)`, delete this and
    use `request.remote_addr`. Do not take XFF[0]; it is client-supplied.
    """
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[-1].strip()
    return request.remote_addr or "unknown"


_BUCKETS: dict[str, deque] = defaultdict(deque)


def rate_limit(limit: int, window: int):
    def deco(fn):
        @wraps(fn)
        def inner(*a, **kw):
            key = f"{fn.__name__}:{client_ip()}"
            now = time.time()
            q = _BUCKETS[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return jsonify({
                    "ok": False,
                    "error": "That's a lot of requests in a short time. Give it a minute and try again.",
                }), 429
            q.append(now)
            return fn(*a, **kw)
        return inner
    return deco


def hub_login_ok() -> bool:
    """Is there a signed-in Hub user?

    This was left as a merge placeholder looking for a Flask session key the
    Hub never sets, so it always returned False, redirected to /login, and the
    Hub redirected straight back -- ERR_TOO_MANY_REDIRECTS on every visit.

    It now reads the Hub's own signed cookies. Still fails closed: an
    unrecognised deployment gets a locked door, not an open one.
    """
    # 1. A real user account (v10+).
    try:
        from hub import identity
        if identity.user_from_environ(request.environ):
            return True
    except Exception:  # noqa: BLE001
        pass
    # 2. The legacy shared-password cookie, still valid during migration.
    try:
        from hub import auth as hub_auth
        if hub_auth.user_from_environ(request.environ):
            return True
    except Exception:  # noqa: BLE001
        pass
    # 3. Flask session, for standalone use.
    try:
        from flask import session as flask_session
        if flask_session.get("hub_user") or flask_session.get("logged_in"):
            return True
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("IMAGE_PICKER_DEV_OPEN") == "1"


def hub_user() -> str:
    try:
        from flask import session as flask_session
        return str(flask_session.get("hub_user") or flask_session.get("user") or "hub")
    except Exception:  # noqa: BLE001
        return "hub"


def staff_only(fn):
    @wraps(fn)
    def inner(*a, **kw):
        if not hub_login_ok():
            if request.path.startswith(f"{bp.url_prefix}/api/"):
                return jsonify({"ok": False, "error": "Sign in to continue."}), 401
            return redirect("/login?next=" + request.path)
        return fn(*a, **kw)
    return inner


def db_guard(fn):
    """Surface a slow/absent database per request rather than at import."""
    @wraps(fn)
    def inner(*a, **kw):
        if DB_BOOT_ERROR:
            log.error("image_picker db unavailable: %s", DB_BOOT_ERROR)
            msg = "The image library is temporarily unavailable. Try again in a moment."
            if request.path.startswith(f"{bp.url_prefix}/api/"):
                return jsonify({"ok": False, "error": msg}), 503
            return render_template("picker_error.html", message=msg), 503
        return fn(*a, **kw)
    return inner


def resolve_scope():
    """Work out which client this request is for, and whether it is staff.

    Share token wins when present, so a client link never depends on a Hub
    session, and a staff session never leaks into someone else's client.
    """
    token = request.values.get("t") or getattr(g, "share_token", None)
    db = session()
    if token:
        client = get_client_by_token(db, token)
        if not client or not client.share_enabled:
            abort(404)
        return db, client, False
    if not hub_login_ok():
        abort(401)
    raw_id = request.values.get("client_id")
    if not raw_id:
        abort(400)
    try:
        client = get_client(db, int(raw_id))
    except (TypeError, ValueError):
        abort(400)
    if not client:
        abort(404)
    return db, client, True


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #

@bp.route("/")
@staff_only
@db_guard
def admin():
    db = session()
    clients = db.execute(select(PickerClient).order_by(PickerClient.name)).scalars().all()
    counts = {}
    for c in clients:
        counts[c.id] = db.execute(
            select(SavedImage).where(SavedImage.client_id == c.id)
        ).scalars().all().__len__()
    return render_template(
        "picker_admin.html",
        clients=[c.to_dict(include_secrets=True) for c in clients],
        counts=counts,
        industries=taxonomy.public_industries(),
        industry_labels={i["key"]: f"{i['icon']} {i['label']}" for i in taxonomy.INDUSTRIES},
        providers_on=providers.configured_providers(),
        cloudinary_on=cloudinary_sink.configured(),
        ghl_on=ghl.configured(),
        version=os.environ.get("HUB_VERSION", ""),
    )


@bp.route("/c/<int:client_id>")
@staff_only
@db_guard
def staff_picker(client_id: int):
    db = session()
    client = get_client(db, client_id)
    if not client:
        abort(404)
    return render_template(
        "picker.html",
        client=client.to_dict(include_secrets=True),
        industries=taxonomy.public_industries(),
        is_staff=True,
        share_token="",
        providers_on=providers.configured_providers(),
    )


@bp.route("/pick/<token>")
@db_guard
def client_picker(token: str):
    db = session()
    client = get_client_by_token(db, token)
    if not client or not client.share_enabled:
        return render_template(
            "picker_error.html",
            message="This image link is no longer active. Ask your Smart 1 contact for a new one.",
        ), 404
    return render_template(
        "picker.html",
        client=client.to_dict(),
        industries=taxonomy.public_industries(),
        is_staff=False,
        share_token=token,
        providers_on=providers.configured_providers(),
        **_widget_ctx(client, token=token),
    )


@bp.route("/gallery/<int:client_id>")
@staff_only
@db_guard
def staff_gallery(client_id: int):
    db = session()
    client = get_client(db, client_id)
    if not client:
        abort(404)
    return render_template(
        "picker_gallery.html",
        client=client.to_dict(include_secrets=True),
        is_staff=True,
        share_token="",
        **_widget_ctx(client, token=""),
    )


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

@bp.route("/api/health")
def api_health():
    return jsonify({
        "ok": not DB_BOOT_ERROR,
        "database": "error" if DB_BOOT_ERROR else "ok",
        "providers": providers.configured_providers(),
        "cloudinary": cloudinary_sink.configured(),
        "ghl": ghl.configured(),
    })


@bp.route("/api/taxonomy")
def api_taxonomy():
    return jsonify({"ok": True, "industries": taxonomy.public_industries()})


@bp.route("/api/search", methods=["POST"])
@rate_limit(limit=40, window=60)
@db_guard
def api_search():
    db, client, is_staff = resolve_scope()
    body = request.get_json(silent=True) or {}

    industry_key = str(body.get("industry") or client.industry_key or "general")
    kind = str(body.get("kind") or "").strip()      # topic | service | search
    key = str(body.get("collection") or "").strip()
    free_text = str(body.get("q") or "").strip()[:120]
    orientation = str(body.get("orientation") or "").strip() or None
    if orientation not in (None, "landscape", "portrait", "square"):
        orientation = None
    try:
        page = max(1, min(12, int(body.get("page") or 1)))
    except (TypeError, ValueError):
        page = 1

    queries: list[str] = []
    negatives = list(taxonomy.GLOBAL_NEGATIVE)
    label = ""

    if kind in ("topic", "service") and key:
        coll = taxonomy.collection(industry_key, "topics" if kind == "topic" else "services", key)
        if not coll:
            return jsonify({"ok": False, "error": "That category isn't available for this industry."}), 400
        queries = list(coll["queries"])
        negatives += coll.get("negative", [])
        label = coll["label"]
    elif free_text:
        ind = taxonomy.industry(industry_key)
        # Blend the client's industry into a bare search so "team photo" for an
        # HVAC client does not return generic office stock.
        queries = [free_text]
        if ind and ind["key"] != "general":
            queries.append(f"{free_text} {ind['label'].lower()}")
        kind, label = "search", free_text
    else:
        return jsonify({"ok": False, "error": "Pick a category or type what you're looking for."}), 400

    if not providers.any_provider_configured():
        return jsonify({
            "ok": False,
            "error": "Photo search isn't switched on yet. Your Smart 1 contact can enable it.",
            "results": [],
        }), 503

    found = providers.search(
        queries, per_page=14, page=page, orientation=orientation,
        negatives=negatives, limit=36, seed=client.id,
    )

    results = []
    for item in found["results"]:
        item = dict(item)
        item["sig"] = sign_item(item)
        item.pop("tags", None)          # internal filtering aid only
        results.append(item)

    if not results and all(v != "ok" for v in found["providers"].values()):
        return jsonify({
            "ok": False,
            "error": "Photo search is having trouble right now. Try again in a minute.",
            "results": [],
        }), 502

    return jsonify({
        "ok": True,
        "results": results,
        "label": label,
        "kind": kind,
        "page": page,
        "providers": found["providers"],
    })


@bp.route("/api/save", methods=["POST"])
@rate_limit(limit=12, window=60)
@db_guard
def api_save():
    db, client, is_staff = resolve_scope()
    body = request.get_json(silent=True) or {}
    items = body.get("items") or []
    if not isinstance(items, list) or not items:
        return jsonify({"ok": False, "error": "Nothing selected to save."}), 400
    if len(items) > MAX_SAVE_BATCH:
        return jsonify({
            "ok": False,
            "error": f"You can save up to {MAX_SAVE_BATCH} images at a time.",
        }), 400

    if not cloudinary_sink.configured():
        return jsonify({
            "ok": False,
            "error": "Image saving isn't switched on yet. Your Smart 1 contact can enable it.",
        }), 503

    collection_kind = str(body.get("kind") or "")[:20]
    collection_key = str(body.get("collection") or "")[:80]
    collection_label = str(body.get("label") or "")[:200]
    actor = hub_user() if is_staff else "client"

    saved, skipped, failed = [], [], []

    for raw in items:
        if not isinstance(raw, dict):
            continue

        if not verify_item(raw):
            failed.append({"id": raw.get("id"), "error": "This image couldn't be verified. Search again and reselect it."})
            continue

        source = raw.get("full") or raw.get("preview") or ""
        if not host_allowed(source):
            failed.append({"id": raw.get("id"), "error": "That image source isn't allowed."})
            continue

        provider = str(raw.get("provider") or "")[:40]
        pid = str(raw.get("provider_image_id") or "")[:120]

        existing = already_saved(db, client.id, provider, pid)
        if existing:
            skipped.append(existing.to_dict())
            continue

        alt = str(raw.get("alt_text") or raw.get("alt") or "")[:300]
        filename = cloudinary_sink.seo_filename(
            alt=alt,
            collection_label=collection_label,
            client_name=client.name,
            fallback=f"{client.slug}-{collection_key or 'image'}",
        )

        row = SavedImage(
            client_id=client.id,
            provider=provider,
            provider_image_id=pid,
            source_url=str(raw.get("source_url") or "")[:900],
            author=str(raw.get("author") or "")[:200],
            author_url=str(raw.get("author_url") or "")[:900],
            alt_text=alt,
            filename=filename,
            industry_key=client.industry_key,
            collection_kind=collection_kind,
            collection_key=collection_key,
            collection_label=collection_label,
            saved_by=actor,
        )

        try:
            up = cloudinary_sink.upload_from_url(
                image_url=source,
                folder=client.folder(),
                public_id=filename,
                context={
                    "alt": alt,
                    "client": client.name,
                    "industry": client.industry_key,
                    "collection": collection_label,
                    "provider": provider,
                    "author": row.author or "",
                    "source": row.source_url or "",
                },
                tags=["smart1-image-picker", client.slug, client.industry_key],
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("cloudinary upload failed for client %s", client.id)
            failed.append({"id": raw.get("id"),
                           "error": "That image couldn't be saved. Try another one."})
            continue

        row.cloudinary_public_id = up["public_id"]
        # Cloudinary de-collides public_ids (unique_filename=True), so the name
        # it created is not always the name we asked for -- several stock photos
        # for one query often carry near-identical alt text. Record what really
        # exists, or the gallery shows a filename nobody can find.
        if up.get("public_id"):
            row.filename = str(up["public_id"]).rsplit("/", 1)[-1]
        row.cloudinary_url = up["delivery_url"]
        row.width, row.height, row.bytes = up["width"], up["height"], up["bytes"]

        # Unsplash API terms: ping download_location when a photo is actually
        # used. Browsing does not count; this does.
        if provider == "unsplash" and raw.get("download_location"):
            providers.trigger_unsplash_download(str(raw["download_location"]))

        # Persist BEFORE the outbound push. The QA audit found nine apps whose
        # only record of a lead was a fire-and-forget webhook.
        db.add(row)
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("could not persist saved image for client %s", client.id)
            failed.append({"id": raw.get("id"),
                           "error": "That image couldn't be saved. Try again."})
            continue

        pushed = ghl.push_image(
            client,
            file_url=row.cloudinary_url,
            name=f"{filename}.webp",
        )
        row.ghl_status = pushed["status"]
        row.ghl_file_id = pushed["file_id"] or None
        row.ghl_url = pushed["url"] or None
        row.ghl_error = pushed["error"] or None
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

        saved.append(row.to_dict())

    return jsonify({
        "ok": True,
        "saved": saved,
        "skipped": skipped,
        "failed": failed,
        "summary": _save_summary(len(saved), len(skipped), len(failed), saved),
    })


def _save_summary(n_saved: int, n_skipped: int, n_failed: int, saved: list[dict]) -> str:
    """Plain-English result. Never claims a Suite delivery that didn't happen."""
    if not n_saved and n_skipped and not n_failed:
        return "Already in your gallery — nothing new to add."
    if not n_saved:
        return "Nothing was saved this time."

    noun = "image" if n_saved == 1 else "images"
    sent = sum(1 for s in saved if s.get("ghl_status") == "sent")
    parts = [f"Saved {n_saved} {noun} to your gallery."]
    if sent == n_saved:
        parts.append("All of them are in your Smart 1 Suite media library too.")
    elif sent:
        parts.append(f"{sent} of them reached your Smart 1 Suite media library.")
    else:
        parts.append("They're in your gallery; the Suite copy is still pending.")
    if n_skipped:
        parts.append(f"{n_skipped} were already saved.")
    if n_failed:
        parts.append(f"{n_failed} couldn't be saved.")
    return " ".join(parts)


@bp.route("/api/saved")
@db_guard
def api_saved():
    db, client, is_staff = resolve_scope()
    try:
        limit = max(1, min(500, int(request.args.get("limit") or 50)))
    except (TypeError, ValueError):
        limit = 60
    limit = max(1, min(200, limit))     # clamp BOTH ends -- ?limit=-1 was a 500 in Scans

    rows = db.execute(
        select(SavedImage)
        .where(SavedImage.client_id == client.id)
        .order_by(SavedImage.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return jsonify({
        "ok": True,
        "client": client.to_dict(include_secrets=is_staff),
        "images": [r.to_dict() for r in rows],
    })


@bp.route("/api/saved/<int:image_id>/delete", methods=["POST"])
@db_guard
def api_delete(image_id: int):
    db, client, is_staff = resolve_scope()
    row = db.get(SavedImage, image_id)
    if not row or row.client_id != client.id:
        return jsonify({"ok": False, "error": "That image isn't in this gallery."}), 404
    if row.cloudinary_public_id:
        cloudinary_sink.destroy(row.cloudinary_public_id)
    db.delete(row)
    db.commit()
    # The Suite copy is intentionally left alone: once it is in the client's
    # media library it may already be used in a funnel or an email.
    return jsonify({"ok": True, "removed": image_id,
                    "note": "Removed from the gallery. Any copy already in Smart 1 Suite stays there."})


@bp.route("/api/saved/bulk-delete", methods=["POST"])
@db_guard
def api_bulk_delete():
    """Remove several images at once.

    Deliberately separate from the single delete rather than the UI looping
    over it: a loop that fails halfway leaves the caller unsure what went and
    what stayed, and each call would re-resolve the gallery.

    The Cloudinary asset goes with the row. That is the point of the warning
    the UI shows first — for an image the client uploaded, our copy is very
    often the only copy, and there is nothing to restore it from.
    """
    db, client, is_staff = resolve_scope()
    body = request.get_json(silent=True) or {}
    ids = body.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify({"ok": False, "error": "Nothing was selected."}), 400
    try:
        ids = [int(i) for i in ids][:200]
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "That selection wasn't valid."}), 400

    removed, missed = [], []
    for image_id in ids:
        row = db.get(SavedImage, image_id)
        # Scoped to this gallery, so a guessed id cannot reach another client's
        # images through a share link.
        if not row or row.client_id != client.id:
            missed.append(image_id)
            continue
        if row.cloudinary_public_id:
            cloudinary_sink.destroy(row.cloudinary_public_id)
        db.delete(row)
        removed.append(image_id)
    db.commit()
    _audit("gallery_bulk_delete", client=client.name, count=len(removed))
    return jsonify({
        "ok": True, "removed": removed, "missed": missed,
        "note": (f"{len(removed)} file(s) removed. Any copy already in Smart 1 "
                 f"Suite stays there."),
    })


@bp.route("/api/saved/download.zip")
@db_guard
def api_bulk_download():
    """Several files as one zip.

    The alternative — opening each in a tab — is blocked by every popup
    blocker once there is more than one, so a browser silently delivers the
    first file and nothing else.

    Files are fetched from our own Cloudinary and streamed into the zip. A file
    that cannot be fetched is skipped and named in a MISSING.txt inside the
    archive rather than failing the whole download, because a partial set with
    an explanation beats an error page.
    """
    db, client, is_staff = resolve_scope()
    raw = (request.args.get("ids") or "").strip()
    try:
        ids = [int(i) for i in raw.split(",") if i.strip()][:200]
    except ValueError:
        return jsonify({"ok": False, "error": "That selection wasn't valid."}), 400
    if not ids:
        return jsonify({"ok": False, "error": "Nothing was selected."}), 400

    import io as _io
    import zipfile
    import requests as _rq

    buf = _io.BytesIO()
    missing, used = [], set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for image_id in ids:
            row = db.get(SavedImage, image_id)
            if not row or row.client_id != client.id or not row.cloudinary_url:
                missing.append(str(image_id))
                continue
            name = (row.filename or f"{row.provider}-{image_id}").strip() or f"file-{image_id}"
            if "." not in name.rsplit("/", 1)[-1]:
                name += ".pdf" if row.resource_type == "raw" else ".jpg"
            name = name.replace("/", "-")
            # Two files can genuinely share a name; without this the zip keeps
            # only the last one and the count silently drops.
            base, dot, ext = name.rpartition(".")
            n, candidate = 2, name
            while candidate.lower() in used:
                candidate = f"{base}-{n}.{ext}" if dot else f"{name}-{n}"
                n += 1
            used.add(candidate.lower())
            try:
                r = _rq.get(row.cloudinary_url, timeout=25)
                r.raise_for_status()
                z.writestr(candidate, r.content)
            except Exception:                           # noqa: BLE001
                missing.append(candidate)
        if missing:
            z.writestr("MISSING.txt",
                       "These files could not be fetched and are not in this "
                       "zip:\n\n" + "\n".join(missing) + "\n")

    buf.seek(0)
    _audit("gallery_download", client=client.name, count=len(ids) - len(missing))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    fname = f"{client.slug}-images-{stamp}.zip"
    from flask import Response
    return Response(
        buf.getvalue(), mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"',
                 "Content-Length": str(buf.getbuffer().nbytes)})


@bp.route("/api/saved/<int:image_id>/retry-suite", methods=["POST"])
@staff_only
@db_guard
def api_retry_suite(image_id: int):
    db = session()
    row = db.get(SavedImage, image_id)
    if not row:
        return jsonify({"ok": False, "error": "Image not found."}), 404
    client = get_client(db, row.client_id)
    if not client or not row.cloudinary_url:
        return jsonify({"ok": False, "error": "Nothing to send."}), 400

    pushed = ghl.push_image(client, file_url=row.cloudinary_url,
                            name=f"{row.filename or 'image'}.webp")
    row.ghl_status = pushed["status"]
    row.ghl_file_id = pushed["file_id"] or None
    row.ghl_url = pushed["url"] or None
    row.ghl_error = pushed["error"] or None
    db.commit()
    return jsonify({"ok": True, "image": row.to_dict()})


# --------------------------------------------------------------------------- #
# Client admin API (staff)
# --------------------------------------------------------------------------- #

# ---------------------------------------------------------------------------
# Client uploads — the Cloudinary upload widget
#
# The point of this half of the module is the opposite of the search half:
# instead of us finding stock photos for a client, the client sends us their
# own. Getting files out of a small business is the actual bottleneck — they
# have them in Drive, on a phone, in Dropbox, in an old email — so the widget
# is configured with every source Cloudinary offers rather than a file input.
#
# Uploads are SIGNED, not unsigned. An unsigned preset is a public write
# endpoint into our Cloudinary account that anyone who views source can reuse;
# signing means the browser can only upload what we authorised, into the folder
# we chose, for as long as the signature is valid.
# ---------------------------------------------------------------------------

# Everything the widget can offer. "local" and "url" always work; the rest
# light up as each is connected in the Cloudinary console, and a source that
# isn't connected is simply not shown, so listing them all costs nothing.
# Deliberately just these three.
#
# The widget can also offer Google Drive, Dropbox, Instagram and Facebook, but
# each works by having the *client* sign into their personal account through
# our Cloudinary application — we become the middleman holding an OAuth
# relationship to a customer's private Drive, for the sake of a file they
# could have dragged in. That is a liability we do not want and a support
# burden when a token lapses.
#
# The stock tabs (Shutterstock, Getty, iStock) are worse: they bill our
# Cloudinary account for anything a client clicks. The search half of this
# module already covers licensed stock, deliberately, through providers we
# chose.
#
# Local, camera and URL need no account, no consent screen and no billing
# relationship, and between them cover every way a small business actually has
# a photo: on the computer, on the phone, or already on a page somewhere. Set
# PICKER_UPLOAD_SOURCES to override if that ever changes.
WIDGET_SOURCES = [s.strip() for s in (
    os.environ.get("PICKER_UPLOAD_SOURCES") or "local,camera,url"
).split(",") if s.strip()]

# Images plus the documents a client sends alongside them. A brochure PDF is
# part of "their pictures" as far as they are concerned, and refusing it means
# it arrives by email instead and is lost.
WIDGET_FORMATS = ["png", "jpg", "jpeg", "gif", "webp", "avif", "heic", "heif",
                  "svg", "bmp", "tif", "tiff", "pdf"]

MAX_UPLOAD_BYTES = int(os.environ.get("PICKER_MAX_UPLOAD_MB", "25")) * 1024 * 1024


def _upload_folder(client) -> str:
    """Where this gallery's own uploads land."""
    # client.folder() is the same folder the search half saves into, so a
    # gallery is one place in Cloudinary rather than two.
    return f"{client.folder()}/uploads"


def _widget_ctx(client, token: str) -> dict:
    """What the upload panel needs to render. Kept in one place so the staff
    gallery and the client's own link cannot drift apart."""
    return {
        "upload_token": token or "",
        "client_id": client.id,
        "sources": WIDGET_SOURCES,
        "formats": WIDGET_FORMATS,
        "max_bytes": MAX_UPLOAD_BYTES,
    }


@bp.route("/api/upload-signature", methods=["POST"])
@db_guard
def api_upload_signature():
    """Sign one widget upload.

    Open to a valid share token as well as staff, because the client doing the
    uploading has no Hub login — that is the entire reason the share link
    exists. What a token buys is narrow: a signature for one folder, on the
    parameters we chose, for one upload.
    """
    body = request.get_json(silent=True) or {}
    client = _client_from_token_or_staff(body.get("token"))
    if client is None:
        return jsonify({"ok": False, "error": "That link is not valid."}), 403
    if not cloudinary_sink.configured():
        return jsonify({"ok": False,
                        "error": "Uploads aren't switched on yet. Your Smart 1 "
                                 "contact can enable them."}), 503

    import cloudinary.utils
    cloudinary_sink._configure()
    # The widget sends back whatever it was signed for, so the folder is fixed
    # here rather than taken from the request — otherwise a caller could sign
    # an upload into any folder in the account.
    params = {
        "timestamp": int(time.time()),
        "folder": _upload_folder(client),
        "tags": f"client_upload,{client.slug}",
    }
    signature = cloudinary.utils.api_sign_request(
        params, os.environ.get("CLOUDINARY_API_SECRET", ""))
    return jsonify({"ok": True, "signature": signature,
                    "api_key": os.environ.get("CLOUDINARY_API_KEY", ""),
                    "cloud_name": os.environ.get("CLOUDINARY_CLOUD_NAME", ""),
                    **params})


@bp.route("/api/uploads", methods=["POST"])
@db_guard
def api_record_upload():
    """Record what the widget just stored, so it appears in the gallery.

    Cloudinary already has the file by the time this runs; this only writes the
    row. It therefore never fetches the URL it is given — the asset is verified
    by having come back from a signed upload into our own folder, and anything
    outside that folder is refused rather than trusted.
    """
    body = request.get_json(silent=True) or {}
    client = _client_from_token_or_staff(body.get("token"))
    if client is None:
        return jsonify({"ok": False, "error": "That link is not valid."}), 403

    public_id = str(body.get("public_id") or "").strip()
    url = str(body.get("secure_url") or "").strip()
    if not public_id or not url.startswith("https://"):
        return jsonify({"ok": False, "error": "That upload didn't complete."}), 400
    if not public_id.startswith(_upload_folder(client)):
        # Only assets from this gallery's own signed folder.
        return jsonify({"ok": False, "error": "That file isn't part of this "
                                              "gallery."}), 400

    # The widget reports which source the visitor picked. Storing it is what
    # lets the gallery group by where a file came from.
    source = str(body.get("source") or "local").strip().lower()[:40]
    if source not in WIDGET_SOURCES:
        source = "local"

    db = session()
    existing = db.execute(
        select(SavedImage).where(SavedImage.client_id == client.id,
                                 SavedImage.provider == source,
                                 SavedImage.provider_image_id == public_id)
    ).scalar_one_or_none()
    if existing:
        return jsonify({"ok": True, "duplicate": True,
                        "image": existing.to_dict()})

    rtype = str(body.get("resource_type") or "image").strip().lower()
    img = SavedImage(
        client_id=client.id,
        provider=source,
        provider_image_id=public_id,
        source_url=url,
        filename=str(body.get("original_filename") or "")[:300] or None,
        alt_text=str(body.get("alt") or "")[:500] or None,
        resource_type="raw" if rtype not in ("image", "video") else rtype,
        cloudinary_public_id=public_id,
        cloudinary_url=url,
        width=body.get("width") or None,
        height=body.get("height") or None,
        bytes=body.get("bytes") or None,
        ghl_status="pending",
        saved_by=(g.get("hub_user") or "client"),
        collection_kind="upload",
        collection_label="Client upload",
    )
    db.add(img)
    db.commit()

    # Straight on to Suite, same as a picked image. A file the client sent is
    # the one most likely to be wanted in their media library, and leaving it
    # only in our gallery means someone has to move it by hand later.
    #
    # push_image never raises and returns "skipped" when the gallery has no
    # Suite location — which is the normal case for a prospect, and why the
    # upload is committed before this runs rather than after.
    pushed = ghl.push_image(client, file_url=img.cloudinary_url,
                            name=(img.filename or public_id.rsplit("/", 1)[-1]))
    img.ghl_status = pushed["status"]
    img.ghl_file_id = pushed["file_id"] or None
    img.ghl_url = pushed["url"] or None
    img.ghl_error = pushed["error"] or None
    try:
        db.commit()
    except Exception:                                   # noqa: BLE001
        db.rollback()
    _audit("client_upload", client=client.name, source=source,
           filename=img.filename or public_id)
    return jsonify({"ok": True, "image": img.to_dict()})


def _client_from_token_or_staff(token):
    """The gallery this request is allowed to write to, or None.

    A share token identifies one gallery. Staff may pass a client_id instead,
    which is why this is not simply a token lookup.
    """
    tok = str(token or "").strip()
    if tok:
        return get_client_by_token(session(), tok)
    if not g.get("hub_user"):
        return None
    try:
        cid = int((request.get_json(silent=True) or {}).get("client_id") or 0)
    except (TypeError, ValueError):
        return None
    return get_client(session(), cid) if cid else None


# --------------------------------------------------------------------------- #
# The gallery, as seen from elsewhere in the Hub
#
# The IO builder needs two things from here: to show a client's existing
# creative while an order is being built, and to file newly-uploaded creative
# where the client will find it later. Before this, creative attached to an IO
# went to Cloudinary and nowhere else — the gallery a client actually looks at
# never learned the file existed.
#
# Both endpoints are staff-only and take a client *name*, because that is what
# the IO builder has. Name matching is a known trap in this codebase, so it is
# deliberately narrow: exact slug match, and creation only when explicitly
# asked for. A near-miss returns nothing rather than the wrong client's photos.
# --------------------------------------------------------------------------- #

@bp.route("/api/staff/gallery")
@staff_only
@db_guard
def api_staff_gallery():
    """One client's gallery, for a tool that only knows their name."""
    db = session()
    client = filing.gallery_for_name(db, request.args.get("client"))
    if client is None:
        return jsonify({"ok": True, "client": None, "images": [],
                        "note": "No gallery exists for that client yet."})
    try:
        limit = int(request.args.get("limit") or 60)
    except (TypeError, ValueError):
        limit = 60
    limit = max(1, min(200, limit))     # clamp BOTH ends

    q = select(SavedImage).where(SavedImage.client_id == client.id)
    kind = str(request.args.get("kind") or "").strip()
    if kind:
        q = q.where(SavedImage.collection_kind == kind)
    rows = db.execute(
        q.order_by(SavedImage.created_at.desc()).limit(limit)
    ).scalars().all()

    # What folders this gallery actually has, so a caller can offer them
    # without guessing which of the five pipelines has run for this client.
    folders = db.execute(
        select(SavedImage.collection_kind, func.count(SavedImage.id))
        .where(SavedImage.client_id == client.id)
        .group_by(SavedImage.collection_kind)
    ).all()
    return jsonify({
        "ok": True,
        "client": client.to_dict(include_secrets=True),
        "gallery_url": f"/tools/image-picker/gallery/{client.id}",
        "folders": [{"kind": k or "upload",
                     "label": filing.KIND_LABELS.get(k or "upload", k or "Other"),
                     "count": n} for k, n in folders],
        "images": [r.to_dict() for r in rows],
    })


@bp.route("/api/staff/file", methods=["POST"])
@staff_only
@db_guard
def api_staff_file():
    """File an already-uploaded asset into a client's gallery.

    A thin wrapper: the work is in filing.file_asset, which the blog and SEO
    pipelines call directly rather than making an HTTP request to their own
    process.
    """
    body = request.get_json(silent=True) or {}
    if not str(body.get("client") or "").strip():
        return jsonify({"ok": False, "error": "Give the client a name."}), 400
    out = filing.file_asset(
        client_name=body.get("client"),
        public_id=body.get("public_id"), url=body.get("url"),
        kind=body.get("collection_kind") or "upload",
        label=body.get("collection_label") or "",
        key=body.get("collection_key") or "",
        filename=body.get("filename") or "", alt=body.get("alt") or "",
        resource_type=body.get("resource_type") or "image",
        width=body.get("width"), height=body.get("height"),
        size_bytes=body.get("bytes"), spec=body.get("spec"),
        provider=body.get("provider") or "",
        saved_by=(g.get("hub_user") or "system"))
    if not out.get("ok"):
        return jsonify(out), 400
    if not out.get("duplicate"):
        _audit(out["image"].get("collection_kind") or "gallery_file",
               client=str(body.get("client")),
               filename=out["image"].get("filename") or "")
    return jsonify(out)


@bp.route("/api/clients", methods=["POST"])
@staff_only
@db_guard
def api_create_client():
    db = session()
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()[:200]
    if not name:
        return jsonify({"ok": False, "error": "Give the client a name."}), 400

    industry_key = str(body.get("industry_key") or "").strip()
    if not taxonomy.industry(industry_key):
        industry_key = taxonomy.guess_industry(body.get("industry_text") or name)

    hub_id = str(body.get("hub_client_id") or "").strip() or None
    # Attached to a Hub record, or standing alone until one exists. Trusting
    # the caller's word would let a gallery claim to be filed against a client
    # it was never linked to.
    kind = "client" if hub_id else "prospect"

    client = PickerClient(
        name=name,
        slug=unique_slug(db, name),
        industry_key=industry_key,
        kind=kind,
        hub_client_id=hub_id,
        ghl_location_id=str(body.get("ghl_location_id") or "").strip() or None,
        ghl_location_token=str(body.get("ghl_location_token") or "").strip() or None,
        share_token=new_token(),
    )
    db.add(client)
    db.commit()
    return jsonify({"ok": True, "client": client.to_dict(include_secrets=True)})


@bp.route("/api/clients/<int:client_id>", methods=["POST"])
@staff_only
@db_guard
def api_update_client(client_id: int):
    db = session()
    client = get_client(db, client_id)
    if not client:
        return jsonify({"ok": False, "error": "Client not found."}), 404
    body = request.get_json(silent=True) or {}

    if "name" in body and str(body["name"]).strip():
        client.name = str(body["name"]).strip()[:200]
    if "industry_key" in body and taxonomy.industry(str(body["industry_key"])):
        client.industry_key = str(body["industry_key"])
    if "ghl_location_id" in body:
        client.ghl_location_id = str(body["ghl_location_id"]).strip()[:120] or None
    if "ghl_location_token" in body:
        tok = str(body["ghl_location_token"]).strip()
        # Empty string means "leave it"; the UI sends a literal "-" to clear.
        if tok == "-":
            client.ghl_location_token = None
        elif tok:
            client.ghl_location_token = tok
    if "ghl_enabled" in body:
        client.ghl_enabled = bool(body["ghl_enabled"])
    if "share_enabled" in body:
        client.share_enabled = bool(body["share_enabled"])
    if "share_note" in body:
        client.share_note = str(body["share_note"])[:600]
    if "cloudinary_folder" in body:
        client.cloudinary_folder = str(body["cloudinary_folder"]).strip()[:300] or None

    db.commit()
    return jsonify({"ok": True, "client": client.to_dict(include_secrets=True)})


@bp.route("/api/clients/<int:client_id>/rotate-link", methods=["POST"])
@staff_only
@db_guard
def api_rotate_link(client_id: int):
    db = session()
    client = get_client(db, client_id)
    if not client:
        return jsonify({"ok": False, "error": "Client not found."}), 404
    client.share_token = new_token()
    db.commit()
    return jsonify({"ok": True, "client": client.to_dict(include_secrets=True)})


@bp.route("/api/clients/<int:client_id>/probe-suite", methods=["POST"])
@staff_only
@db_guard
def api_probe_suite(client_id: int):
    db = session()
    client = get_client(db, client_id)
    if not client:
        return jsonify({"ok": False, "error": "Client not found."}), 404
    result = ghl.probe(client.ghl_location_id or "", client.ghl_location_token)
    return jsonify({"ok": True, "probe": result})


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

def register_image_picker(app) -> None:
    """Mount the module. Call from the Hub's app factory."""
    init_db(app)
    app.register_blueprint(bp)
    if DB_BOOT_ERROR:
        app.logger.error("image_picker started with a database error: %s", DB_BOOT_ERROR)
