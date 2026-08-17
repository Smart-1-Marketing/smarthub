"""Page Image Optimizer — Flask blueprint.

Flow:
    scan a page  ->  choose images  ->  optimize 5  ->  edit  ->  save
                          ^                                        |
                          +----------------- continue -------------+
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, Response, jsonify, render_template, request

from . import archive, naming, optimizer, settings, store
from .scanner import ScanError, scan_page, safe_url

log = logging.getLogger(__name__)

bp = Blueprint(
    "page_image_optimizer",
    __name__,
    template_folder="templates",
    static_folder=None,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _payload():
    return request.get_json(silent=True) or {}


def _fail(message, code=400):
    return jsonify({"ok": False, "error": message}), code


def _job_or_404(job_id):
    job = store.load_job(job_id)
    if not job:
        return None, _fail(
            "That scan has expired. Run the page again — it only takes a moment.", 404
        )
    return job, None


def _public_item(item):
    """What the browser is allowed to see. Never the bytes."""
    return {
        "id": item["id"],
        "source_url": item["source_url"],
        "filename": item["filename"],
        "alt": item["alt"],
        "ai": item.get("ai", False),
        "naming_error": item.get("naming_error", ""),
        "error": item.get("error", ""),
        "status": item.get("status", "ready"),
        "info": item.get("info", {}),
    }


def _clean_filename(value, fallback="client-image"):
    return naming.slugify(value) or fallback


def _dedupe(name, taken):
    """Two images in one batch can end up with the same edited name."""
    candidate, n = name, 2
    while candidate in taken:
        candidate = f"{name}-{n}"
        n += 1
    taken.add(candidate)
    return candidate


# --------------------------------------------------------------------------- #
# pages
# --------------------------------------------------------------------------- #

@bp.get("/")
def index():
    return render_template(
        "index.html",
        batch_size=settings.PAGE_IMAGES_BATCH_SIZE,
        max_edge=settings.SEO_IMAGES_MAX_EDGE,
        alt_max=settings.ALT_MAX_CHARS,
        ai_on=bool(settings.OPENAI_API_KEY),
        cloudinary_on=bool(settings.CLOUDINARY_URL),
        archive_backend=archive.archive_backend(),
    )


@bp.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "ai": bool(settings.OPENAI_API_KEY),
        "cloudinary": bool(settings.CLOUDINARY_URL),
        "archive": archive.archive_backend(),
        "batch_size": settings.PAGE_IMAGES_BATCH_SIZE,
        "max_edge": settings.SEO_IMAGES_MAX_EDGE,
    })


# --------------------------------------------------------------------------- #
# 1. scan
# --------------------------------------------------------------------------- #

@bp.post("/api/scan")
def api_scan():
    data = _payload()
    company = (data.get("company") or "").strip()
    project = (data.get("project") or "").strip()
    page_name = (data.get("page_name") or "").strip()
    page_url = (data.get("url") or "").strip()

    if not company:
        return _fail("Add the client or company name.")
    if not project:
        return _fail("Add a project name.")

    try:
        result = scan_page(page_url)
    except ScanError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("page scan failed")
        return _fail("That page couldn't be read. Check the address and try again.")

    if not result["images"]:
        return _fail("No images found on that page.")

    job = store.create_job({
        "company": company,
        "project": project,
        "page_name": page_name or result["title"] or "",
        "page_url": result["url"],
        "page_title": result["title"],
        "page_h1": result["h1"],
        "page_description": result["description"],
        "candidates": {img["url"]: img for img in result["images"]},
    })

    optimizable = [i for i in result["images"] if i["optimizable"]]
    return jsonify({
        "ok": True,
        "job_id": job["id"],
        "page": {
            "url": result["url"],
            "title": result["title"],
            "h1": result["h1"],
            "company": company,
            "project": project,
            "page_name": job["page_name"],
        },
        "images": result["images"],
        "totals": {
            "found": len(result["images"]),
            "optimizable": len(optimizable),
            "bytes": sum(i["bytes"] for i in optimizable),
            "saving": sum(i["saving"] for i in optimizable),
        },
        "batch_size": settings.PAGE_IMAGES_BATCH_SIZE,
    })


@bp.get("/api/job/<job_id>")
def api_job(job_id):
    job, error = _job_or_404(job_id)
    if error:
        return error
    saved_urls = {row["source_url"] for row in job["saved"]}
    return jsonify({
        "ok": True,
        "job_id": job["id"],
        "page": {
            "url": job["page_url"], "title": job["page_title"],
            "company": job["company"], "project": job["project"],
            "page_name": job["page_name"],
        },
        "images": list(job["candidates"].values()),
        "saved": job["saved"],
        "saved_urls": sorted(saved_urls),
        "batch_size": settings.PAGE_IMAGES_BATCH_SIZE,
    })


# --------------------------------------------------------------------------- #
# 2. optimize a batch
# --------------------------------------------------------------------------- #

def _process_one(job, index, url):
    """Download, optimize, and name one image. Never raises."""
    item_id = f"i{index}"
    candidate = job["candidates"].get(url, {})
    base = {
        "id": item_id,
        "source_url": url,
        "filename": "",
        "alt": "",
        "ai": False,
        "status": "ready",
        "error": "",
        "info": {},
        "history": [],
    }

    try:
        raw = optimizer.download(url)
        webp, info = optimizer.optimize(raw)
        thumb = optimizer.preview(raw)
    except ScanError as exc:
        base.update(status="error", error=str(exc))
        return base
    except Exception as exc:  # noqa: BLE001
        log.warning("optimize failed for %s: %s", url, exc)
        base.update(status="error", error="This image couldn't be processed.")
        return base

    store.put_bytes(job["id"], f"{item_id}.webp", webp)
    store.put_bytes(job["id"], f"{item_id}.preview", thumb)

    context = {
        "company": job["company"],
        "project": job["project"],
        "page_name": job["page_name"],
        "page_url": job["page_url"],
        "page_title": job["page_title"],
        "page_h1": job["page_h1"],
        "nearest_heading": candidate.get("nearest_heading", ""),
        "existing_alt": candidate.get("alt", ""),
        "caption": candidate.get("caption", ""),
        "current_filename": url.rsplit("/", 1)[-1],
        "source_url": url,
    }
    suggestion = naming.suggest(thumb, context)

    base.update(
        filename=suggestion["filename"],
        alt=suggestion["alt"],
        ai=suggestion.get("ai", False),
        naming_error=suggestion.get("error", ""),
        info=info,
        context=context,
    )
    return base


@bp.post("/api/job/<job_id>/batch")
def api_batch(job_id):
    job, error = _job_or_404(job_id)
    if error:
        return error

    urls = _payload().get("urls") or []
    if not isinstance(urls, list) or not urls:
        return _fail("Choose at least one image.")
    if len(urls) > settings.PAGE_IMAGES_BATCH_SIZE:
        return _fail(
            f"{settings.PAGE_IMAGES_BATCH_SIZE} images at a time — "
            "the rest stay selected for the next round."
        )

    chosen = []
    for url in urls:
        candidate = job["candidates"].get(url)
        if not candidate:
            return _fail("One of those images isn't on the page that was scanned.")
        if not candidate.get("optimizable"):
            return _fail(f"{url.rsplit('/', 1)[-1]} can't be optimised.")
        chosen.append(url)

    batch_id = store.new_id()
    offset = len(job.get("batches", {})) * settings.PAGE_IMAGES_BATCH_SIZE
    with ThreadPoolExecutor(max_workers=min(5, len(chosen))) as pool:
        items = list(pool.map(
            lambda pair: _process_one(job, offset + pair[0], pair[1]),
            enumerate(chosen),
        ))

    job.setdefault("batches", {})[batch_id] = {"id": batch_id, "items": items}
    store.save_job(job)

    return jsonify({
        "ok": True,
        "batch_id": batch_id,
        "items": [_public_item(i) for i in items],
    })


@bp.get("/api/job/<job_id>/preview/<item_id>")
def api_preview(job_id, item_id):
    if not re.fullmatch(r"i\d+", item_id or ""):
        return _fail("Unknown image.", 404)
    data = store.get_bytes(job_id, f"{item_id}.preview")
    if not data:
        return _fail("That preview has expired.", 404)
    return Response(data, mimetype="image/webp",
                    headers={"Cache-Control": "private, max-age=600"})


@bp.post("/api/job/<job_id>/batch/<batch_id>/rename")
def api_rename(job_id, batch_id):
    job, error = _job_or_404(job_id)
    if error:
        return error
    batch = job.get("batches", {}).get(batch_id)
    if not batch:
        return _fail("That batch has expired.", 404)

    item_id = _payload().get("item_id")
    item = next((i for i in batch["items"] if i["id"] == item_id), None)
    if not item:
        return _fail("Unknown image.", 404)
    if item["status"] == "error":
        return _fail("That image couldn't be processed, so it can't be renamed.")

    thumb = store.get_bytes(job_id, f"{item_id}.preview")
    if not thumb:
        return _fail("That image has expired. Run the batch again.", 404)

    history = item.get("history", [])
    if item["filename"]:
        history.append(item["filename"])
    suggestion = naming.suggest(thumb, item.get("context", {}), avoid=history)

    item.update(
        filename=suggestion["filename"],
        alt=suggestion["alt"],
        ai=suggestion.get("ai", False),
        naming_error=suggestion.get("error", ""),
        history=history[-5:],
    )
    store.save_job(job)
    return jsonify({"ok": True, "item": _public_item(item)})


# --------------------------------------------------------------------------- #
# 3. save the batch, then continue
# --------------------------------------------------------------------------- #

@bp.post("/api/job/<job_id>/batch/<batch_id>/save")
def api_save(job_id, batch_id):
    job, error = _job_or_404(job_id)
    if error:
        return error
    batch = job.get("batches", {}).get(batch_id)
    if not batch:
        return _fail("That batch has expired. Run those images again.", 404)

    edits = {e.get("id"): e for e in (_payload().get("items") or [])}
    taken = {row["filename"] for row in job["saved"]}
    saved, skipped, failed = [], [], []

    for item in batch["items"]:
        edit = edits.get(item["id"], {})
        if item["status"] == "error":
            failed.append({"source_url": item["source_url"], "error": item["error"]})
            continue
        if edit.get("skip"):
            skipped.append(item["source_url"])
            continue

        filename = _dedupe(
            _clean_filename(edit.get("filename") or item["filename"]), taken
        )
        alt = re.sub(r"\s+", " ", str(edit.get("alt", item["alt"]))).strip()
        alt = alt[: settings.ALT_MAX_CHARS]

        data = store.get_bytes(job_id, f"{item['id']}.webp")
        if not data:
            failed.append({"source_url": item["source_url"],
                           "error": "The optimised file expired before saving."})
            continue

        try:
            upload = archive.upload(
                data,
                filename=filename,
                company=job["company"],
                project=job["project"],
                page_name=job["page_name"],
                page_url=job["page_url"],
                alt=alt,
                source_url=item["source_url"],
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("cloudinary upload failed")
            failed.append({"source_url": item["source_url"],
                           "error": "Upload failed. Try saving this one again."})
            continue

        row = {
            "company": job["company"],
            "project": job["project"],
            "page": job["page_name"],
            "page_url": job["page_url"],
            "source_url": item["source_url"],
            "filename": filename,
            "alt": alt,
            "url": upload.get("url", ""),
            "public_id": upload.get("public_id", ""),
            "bytes": item["info"].get("bytes", 0),
            "source_bytes": item["info"].get("source_bytes", 0),
            "saved_bytes": item["info"].get("saved_bytes", 0),
            "saved_pct": item["info"].get("saved_pct", 0),
            "width": item["info"].get("width", 0),
            "height": item["info"].get("height", 0),
            "stored": upload.get("stored", False),
            "note": upload.get("note", ""),
            "img_tag": (
                f'<img src="{upload.get("url", "")}" alt="{alt}" '
                f'width="{item["info"].get("width", 0)}" '
                f'height="{item["info"].get("height", 0)}" loading="lazy">'
            ),
        }
        archive.record(**{k: v for k, v in row.items() if k != "img_tag"})
        job["saved"].append(row)
        saved.append(row)

    job["batches"].pop(batch_id, None)
    store.save_job(job)

    saved_urls = {row["source_url"] for row in job["saved"]}
    remaining = [
        url for url, c in job["candidates"].items()
        if c.get("optimizable") and url not in saved_urls
    ]

    return jsonify({
        "ok": True,
        "saved": saved,
        "skipped": skipped,
        "failed": failed,
        "totals": {
            "saved_all": len(job["saved"]),
            "saved_bytes": sum(r["saved_bytes"] for r in job["saved"]),
            "remaining": len(remaining),
        },
        "saved_urls": sorted(saved_urls),
    })


# --------------------------------------------------------------------------- #
# 4. archive
# --------------------------------------------------------------------------- #

@bp.get("/api/archive")
def api_archive():
    return jsonify({
        "ok": True,
        "backend": archive.archive_backend(),
        "rows": archive.recent(
            limit=request.args.get("limit", 200),
            company=request.args.get("company"),
        ),
    })


def register(app, url_prefix="/tools/page-images"):
    """Mount the tool. Called from wsgi.py / the Hub app factory."""
    app.register_blueprint(bp, url_prefix=url_prefix)
    return app
