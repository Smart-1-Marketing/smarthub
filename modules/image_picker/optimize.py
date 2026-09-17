"""An SEO-ready copy of every image a client or a rep uploads, made later.

## Why this exists

The upload link hands a client's phone photographs straight into their
gallery, and a rep's "Add more images" does the same from Drive or a desk.
Both are the right file for the archive and the wrong file for a web page:
a 6 MB, 6000-pixel JPEG named `IMG_4471.jpg`. The SEO Image Pipeline knows
how to fix that -- cap the edge, convert to WebP, name it for search, write
alt text -- and nobody ran it on uploads, because it is a tool somebody opens
on purpose with a batch in hand, and an upload arrives while nobody is
looking.

So this does it in the background. One row per saved image says how far
along it is; a scheduled job works the backlog a few at a time; and the
gallery draws the copy beside the original once there is one.

## The rules

**The original is never touched.** Not re-encoded, not renamed, not moved.
The file the client sent is the file they sent -- the archive copy, the one
with the EXIF data, the one a designer wants at full size. The copy is a
second Cloudinary asset under `<gallery folder>/optimized`, and deleting the
original takes the copy with it, never the other way round.

**Under load, it waits.** Two gunicorn workers share one instance with the
pages people are actually using, and a re-encode of a 40-megapixel photo is
a second of CPU each. `busy()` reads the one-minute load average against the
core count and the sweep steps aside when the box is busy, saying so in its
result rather than silently doing nothing -- the backlog is still there next
tick and nobody waited on it.

**Bounded by a count and a wall clock**, the contract every scheduler job
here keeps (`vision.describe_backlog` states it). A provider that hangs must
not hold the one scheduler thread.

**Failure is written down and gives up in writing.** Three attempts, then
`given_up`, the rule `ImageDescription` keeps for a vision call: the fourth
attempt on a file that has failed three times spends a fetch and a model
call to learn the same thing. A file this does not apply to -- a PDF, a
video, an SVG mark that scales without help -- is `skipped` at once.

**"Not measured" is not zero.** `progress()` carries `measured`, so a store
that could not be read draws as unread on the Client 360 counter rather than
as "everything is done".

**The name is the model's, and nothing else it returns is trusted.**
`modules/page_image_optimizer/naming.suggest` writes the filename and the
alt text, never raises, and says whether the model or the fallback wrote
them; `named_by` keeps that answer on the row. The alt text is offered on
the copy, never written over the original's own `alt_text` -- a client's
words, or a rep's, are the better source, the overlay rule the vision module
works to.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone

from sqlalchemy import func, select

from hub.config import settings

from .models import ImageOptimization, PickerClient, SavedImage, session

log = logging.getLogger(__name__)

try:
    from hub import audit as _hub_audit
    _audit = _hub_audit.for_module("image_picker")
except Exception:                                       # noqa: BLE001
    def _audit(*a, **k):                                # no-op outside the Hub
        return None

# One batch of the scheduler's and the ceiling on how long it may hold the
# thread. Eight, because a re-encode plus a vision call is a few seconds
# each and the job runs every five minutes: forty phone photographs clear in
# half an hour without one run ever holding the scheduler for long.
BATCH = 8
BUDGET_SECONDS = 120
MAX_ATTEMPTS = 3
# Refuse to pull an original bigger than this into memory to optimize it.
MAX_SOURCE_BYTES = 60 * 1024 * 1024
FETCH_TIMEOUT = 30
# The one-minute load average, as a multiple of the core count, above which
# the sweep steps aside. 1.5 on a two-core box is three runnable processes
# on average: the two workers answering pages plus something else, which is
# a box with no spare second for a re-encode.
LOAD_FACTOR = 1.5

_NEVER = re.compile(r"\.(svg|gif)$", re.I)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def busy() -> tuple[bool, str]:
    """Whether the instance has no spare second right now, and why.

    Never raises: a platform without `os.getloadavg` (Windows) is simply
    never busy by this measure, and says nothing.
    """
    try:
        one_minute = os.getloadavg()[0]
    except (AttributeError, OSError):
        return False, ""
    cores = max(1, os.cpu_count() or 1)
    limit = cores * LOAD_FACTOR
    if one_minute > limit:
        return True, (f"load average {one_minute:.2f} is above {limit:.2f} "
                      f"on {cores} core{'s' if cores != 1 else ''}")
    return False, ""


def applies(image: SavedImage) -> bool:
    """Whether this file is one an SEO copy makes sense for."""
    if (image.resource_type or "image") != "image":
        return False
    if image.external or not (image.cloudinary_url or "").startswith("https://"):
        return False
    url = (image.cloudinary_url or "").split("?")[0]
    return not (_NEVER.search(image.filename or "") or _NEVER.search(url))


def enqueue(db, image: SavedImage) -> ImageOptimization | None:
    """Queue one saved image for a copy. Idempotent, and never raises.

    Called from the request that recorded the upload, which is why it only
    writes a row: the work happens in `run_backlog` on the scheduler, so a
    client sending forty photographs waits on none of them.
    """
    try:
        row = db.execute(select(ImageOptimization)
                         .where(ImageOptimization.image_id == image.id)).scalar_one_or_none()
        if row is not None:
            return row
        row = ImageOptimization(image_id=image.id, client_id=image.client_id,
                                state="pending" if applies(image) else "skipped",
                                bytes_before=image.bytes)
        db.add(row)
        db.commit()
        return row
    except Exception as exc:                              # noqa: BLE001
        try:
            db.rollback()
        except Exception:                                 # noqa: BLE001
            pass
        log.warning("image_picker: could not queue optimization for %s: %s",
                    getattr(image, "id", "?"), exc)
        return None


def enqueue_missing(db, client_id: int) -> int:
    """Queue every image in one gallery that has no row yet. Returns how many."""
    have = {r[0] for r in db.execute(
        select(ImageOptimization.image_id)
        .where(ImageOptimization.client_id == client_id)).all()}
    n = 0
    for image in db.scalars(select(SavedImage).where(SavedImage.client_id == client_id)).all():
        if image.id in have:
            continue
        if enqueue(db, image) is not None:
            n += 1
    return n


def copies_for(db, image_ids) -> dict:
    """`{image_id: copy}` for the ids that have a finished copy."""
    ids = [i for i in image_ids if isinstance(i, int)]
    if not ids:
        return {}
    out = {}
    for row in db.scalars(select(ImageOptimization)
                          .where(ImageOptimization.image_id.in_(ids))).all():
        if row.state == "done" and row.optimized_url:
            out[row.image_id] = row.to_dict()
    return out


def progress(db, client_id: int) -> dict:
    """How far one gallery's backlog has got. Tri-state, like every count here."""
    try:
        rows = db.execute(
            select(ImageOptimization.state, func.count(ImageOptimization.id))
            .where(ImageOptimization.client_id == client_id)
            .group_by(ImageOptimization.state)).all()
    except Exception as exc:                              # noqa: BLE001
        return {"measured": False, "error": f"{type(exc).__name__}: {exc}"}
    by = {state: int(n) for state, n in rows}
    pending = by.get("pending", 0)
    done = by.get("done", 0)
    failed = by.get("failed", 0) + by.get("given_up", 0)
    total = pending + done + failed
    is_busy, why = busy() if pending else (False, "")
    return {"measured": True, "total": total, "done": done, "pending": pending,
            "failed": failed, "skipped": by.get("skipped", 0),
            "deferred": is_busy, "deferred_why": why,
            "configured": _configured()}


def _configured() -> bool:
    try:
        from . import cloudinary_sink
        return bool(cloudinary_sink.configured())
    except Exception:                                     # noqa: BLE001
        return False


def _fetch(url: str) -> tuple[bytes, str]:
    """``(bytes, error)``. Never raises."""
    try:
        import requests
        r = requests.get(url, timeout=FETCH_TIMEOUT, stream=True)
    except Exception as exc:                              # noqa: BLE001
        return b"", f"could not be fetched ({type(exc).__name__})"
    if not r.ok:
        return b"", f"answered HTTP {r.status_code}"
    length = r.headers.get("Content-Length")
    if length and length.isdigit() and int(length) > MAX_SOURCE_BYTES:
        return b"", f"is {int(length) // 1048576} MB, more than this will re-encode"
    data = r.content or b""
    if not data:
        return b"", "the link returned nothing"
    if len(data) > MAX_SOURCE_BYTES:
        return b"", f"is {len(data) // 1048576} MB, more than this will re-encode"
    return data, ""


def _folder_label(image: SavedImage) -> str:
    return (image.project_name or image.collection_label or "").strip()


def optimize_one(db, row: ImageOptimization, image: SavedImage,
                 client: PickerClient) -> dict:
    """Make the copy for one image and write the result onto its row.

    Returns `{ok, error}`. The row is updated either way; committing is the
    caller's, so a batch commits once per file rather than once per field.
    """
    from hub import images as _images
    from hub import storage
    from modules.page_image_optimizer import naming

    data, err = _fetch(image.cloudinary_url or "")
    if err:
        return {"ok": False, "error": f"The original {err}."}
    try:
        processed = _images.optimise(data, max_edge=settings.max_edge, fmt="WEBP")
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"Could not re-encode it ({type(exc).__name__})."}

    named = naming.suggest(processed.data, {
        "company": client.name,
        "project": _folder_label(image),
        "current_filename": image.filename or "",
        "existing_alt": image.alt_text or "",
        "source_url": image.cloudinary_url or "",
    }, mime="image/webp")
    stem = naming.slugify(named.get("filename") or "") or "client-image"
    # The image id on the end keeps two photographs the model named alike
    # from overwriting each other, without inventing a second name.
    public_id = f"{client.folder()}/optimized/{stem}-{image.id}"
    try:
        stored = storage.put("seo_images", f"{stem}.webp", processed.data,
                             public_id=public_id, overwrite=True,
                             context={"client": client.name, "alt": named.get("alt") or "",
                                      "original": image.cloudinary_public_id or ""},
                             tags=["s1-optimized", f"client_upload,{client.slug}"])
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"Could not store the copy ({exc})."}
    if not stored.url:
        # storage.put keeps the bytes on the disk and says so; a copy with
        # no delivery URL is not a copy the gallery can offer.
        return {"ok": False, "error": stored.note or "The copy has no delivery URL."}

    row.optimized_public_id = stored.public_id
    row.optimized_url = stored.url
    row.seo_filename = f"{stem}.webp"
    row.alt_text = (named.get("alt") or "")[:500]
    row.bytes_before = len(data)
    row.bytes_after = len(processed.data)
    row.width = processed.width
    row.height = processed.height
    row.named_by = "ai" if named.get("ai") else "fallback"
    row.state = "done"
    row.last_error = ""
    return {"ok": True}


def run_backlog(limit: int = BATCH, *, max_seconds: int = BUDGET_SECONDS,
                actor: str = "scheduler", force: bool = False) -> dict:
    """One bounded pass over the images nothing has copied yet.

    Oldest first, so a client who uploaded a month ago is served before one
    who uploaded this morning. Steps aside under load unless `force` -- the
    Diagnostics "run now" button -- and says so in its result.
    """
    if not _configured():
        return {"skipped": "CLOUDINARY_URL is not set", "optimized": 0}
    if not force:
        is_busy, why = busy()
        if is_busy:
            return {"deferred": True, "why": why, "optimized": 0}

    started = time.time()
    optimized = failed = gave_up = 0
    finished_clients: dict[int, str] = {}
    errors: list[str] = []
    try:
        with session() as db:
            todo = db.scalars(
                select(ImageOptimization)
                .where(ImageOptimization.state == "pending")
                .order_by(ImageOptimization.created_at.asc(), ImageOptimization.id.asc())
                .limit(max(1, int(limit)))).all()
            for row in todo:
                if time.time() - started > max_seconds:
                    break
                image = db.get(SavedImage, row.image_id)
                client = db.get(PickerClient, row.client_id) if image else None
                row.updated_at = _now()
                if image is None or client is None:
                    row.state = "skipped"
                    row.last_error = "The image is no longer in the gallery."
                    db.commit()
                    continue
                if not applies(image):
                    row.state = "skipped"
                    db.commit()
                    continue
                out = optimize_one(db, row, image, client)
                if out.get("ok"):
                    optimized += 1
                    _audit("image_optimized", client=client.name, image=image.id,
                           saved_pct=(0 if not row.bytes_before else
                                      round(100 * (1 - (row.bytes_after or 0) / row.bytes_before))),
                           named_by=row.named_by)
                else:
                    row.attempts = int(row.attempts or 0) + 1
                    row.last_error = str(out.get("error") or "")[:400]
                    if row.attempts >= MAX_ATTEMPTS:
                        row.state = "given_up"
                        gave_up += 1
                    else:
                        row.state = "failed"
                        failed += 1
                    errors.append(row.last_error)
                db.commit()
                # A client whose last pending image just finished is told once
                # -- checked here, after the commit, so the count it reads is
                # the count that is true.
                left = db.execute(
                    select(func.count(ImageOptimization.id))
                    .where(ImageOptimization.client_id == client.id,
                           ImageOptimization.state.in_(("pending", "failed")))).scalar() or 0
                if not left:
                    finished_clients[client.id] = client.name
            # A file that failed once is retried next run, not left as
            # "failed" for ever: back to pending, attempts kept.
            for row in db.scalars(select(ImageOptimization)
                                  .where(ImageOptimization.state == "failed")).all():
                row.state = "pending"
            db.commit()
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "optimized": optimized}

    for client_id, name in finished_clients.items():
        try:
            from . import notices
            with session() as db:
                done = progress(db, client_id).get("done", 0)
            notices.optimized_all(name, done)
        except Exception:                                 # noqa: BLE001
            log.warning("image_picker: could not announce optimization for %s", name)

    out = {"ok": True, "optimized": optimized, "failed": failed, "gave_up": gave_up,
           "seconds": round(time.time() - started, 1), "actor": actor}
    if errors:
        out["last_error"] = errors[-1]
    return out


def pending_count() -> dict:
    """The whole backlog, for Diagnostics. Tri-state."""
    try:
        with session() as db:
            rows = db.execute(select(ImageOptimization.state,
                                     func.count(ImageOptimization.id))
                              .group_by(ImageOptimization.state)).all()
        by = {state: int(n) for state, n in rows}
        is_busy, why = busy()
        return {"measured": True, "pending": by.get("pending", 0) + by.get("failed", 0),
                "done": by.get("done", 0), "given_up": by.get("given_up", 0),
                "busy": is_busy, "why": why, "configured": _configured(), "error": ""}
    except Exception as exc:                              # noqa: BLE001
        return {"measured": False, "pending": 0, "done": 0, "given_up": 0,
                "busy": False, "why": "", "configured": False,
                "error": f"{type(exc).__name__}: {exc}"}


def forget(db, image: SavedImage) -> None:
    """Remove the copy when its original goes. Never raises.

    The row cascades with the image on Postgres; the Cloudinary object does
    not, and a copy of a deleted photograph left in the account is exactly
    the orphan `cloudinary_sink.destroy` exists to avoid.
    """
    try:
        row = db.execute(select(ImageOptimization)
                         .where(ImageOptimization.image_id == image.id)).scalar_one_or_none()
        if row is None:
            return
        if row.optimized_public_id:
            from . import cloudinary_sink
            cloudinary_sink.destroy(row.optimized_public_id, "image")
        db.delete(row)
    except Exception as exc:                              # noqa: BLE001
        log.warning("image_picker: could not remove the SEO copy for %s: %s",
                    getattr(image, "id", "?"), exc)
