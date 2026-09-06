"""Submitting an edit to Cloudinary, watching it, and filing the result.

One path for both tools, because from here down they are the same operation:
a transformation string against a source public id, derived asynchronously,
polled, and then -- only on the person's say-so -- stored as an asset of its
own.

Why *derived* and then separately *stored*, rather than one upload:

  * A derived asset is free to abandon. Most of what these tools produce is a
    trial: three sensitivities on the dead-air cutter, two focus modes on the
    reframe. Deriving costs a transformation; storing costs storage and puts a
    row in a client's gallery, and the second should follow a decision.
  * A derived URL is tied to its source. If the source is replaced, the
    derived URL follows it — which is right while somebody is iterating and
    wrong the moment the edit is the deliverable. Saving breaks that tie on
    purpose.

Why asynchronous. A Cloudinary transformation is normally built on first
request, and the first request for a thirty-cut concatenation of a four-minute
recording does not return inside an HTTP timeout. `eager_async` moves that
work off the request that asked for it, which is the same reason the Hub has
no ffmpeg on its image: a video encode inside a web worker takes the whole Hub
down, not just the page.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from . import config
from .sources import base_url, ready, SourceError

try:
    import cloudinary
    import cloudinary.api
    import cloudinary.uploader
    _SDK = True
except ImportError:                                   # noqa: BLE001
    _SDK = False


def derived_url(public_id: str, transformation: str, ext: str = "mp4") -> str:
    """The delivery URL for an edit — what the preview player is pointed at."""
    base = base_url()
    if not base or not public_id or not transformation:
        return ""
    return (f"{base}/{transformation}/"
            f"{quote(str(public_id), safe='/')}.{ext}")


def submit(public_id: str, transformation: str) -> dict:
    """Ask Cloudinary to build this edit, without waiting for it.

    `explicit` with an eager transformation is the request; `eager_async`
    makes it a background job on their side. The call is idempotent — asking
    twice for the same transformation on the same asset does not derive it
    twice — which is what makes `poll()` below able to be the same call.
    """
    if not _SDK or not ready():
        raise SourceError("Cloudinary is not configured on this deployment.")
    from hub import storage
    storage.configure()
    try:
        res = cloudinary.uploader.explicit(
            public_id, type="upload", resource_type="video",
            eager=[{"raw_transformation": transformation}],
            eager_async=True,
        )
    except Exception as exc:                          # noqa: BLE001
        # Cloudinary's message names the offending component, and this is one
        # of the few provider errors worth surfacing: it is about the
        # transformation we built, not about their account.
        raise SourceError(f"Cloudinary refused this edit: {exc}") from exc
    return _eager_state(res, transformation)


def poll(public_id: str, transformation: str) -> dict:
    """Where that edit has got to.

    The same `explicit` call as `submit`, deliberately. Cloudinary answers
    with the state of the eager transformation whether it is building it now
    or built it an hour ago, so there is one code path and no separate
    "did I already ask" bookkeeping to get wrong.
    """
    return submit(public_id, transformation)


def _eager_state(response: dict, transformation: str) -> dict:
    """Cloudinary's answer, reduced to done / building / failed.

    The eager entry carries `status: "pending"` while it builds and drops the
    field once it is finished. A response with no eager entry at all is the
    one case that must not be read as success: it means the transformation was
    ignored, and treating that as done would file the *source* as the edit.
    """
    entries = response.get("eager") or []
    entry = next((e for e in entries
                  if (e.get("transformation") or "") == transformation), None)
    entry = entry or (entries[0] if entries else None)
    if not entry:
        return {"status": "failed",
                "error": "Cloudinary accepted the request but built nothing. "
                         "The transformation was ignored.",
                "url": ""}
    state = (entry.get("status") or "").lower()
    if state in ("pending", "processing"):
        return {"status": "building", "url": "", "error": ""}
    if state in ("failed", "error"):
        return {"status": "failed", "url": "",
                "error": entry.get("error") or "Cloudinary could not build "
                                               "this edit."}
    url = entry.get("secure_url") or entry.get("url") or ""
    if not url:
        return {"status": "building", "url": "", "error": ""}
    return {"status": "done", "url": url, "error": "",
            "bytes": entry.get("bytes"), "width": entry.get("width"),
            "height": entry.get("height")}


def save(job, *, url: str, filename: str) -> dict:
    """Store a finished edit as an asset of its own, in the client's tree.

    Through `hub.storage.put_remote` rather than the SDK directly, so this
    edit is filed in the same folder layout, counted in the same asset ledger
    and visible to the same audits as everything else the Hub produces.
    Cloudinary fetches the derived URL itself — the file never passes through
    this process, which on a 200 MB recording is the difference between a
    save and an out-of-memory.
    """
    from hub import storage
    asset = storage.put_remote(
        "commercials", url,
        filename=filename,
        client=job.client_name or "",
        subpath="edits",
        overwrite=False, unique=True,
        context={"source": job.source_public_id, "tool": job.tool},
        tags=["video-tools", job.tool.replace("_", "-")],
    )
    job.saved_public_id = asset.public_id
    job.saved_url = asset.url
    job.finished_at = datetime.utcnow()

    # Into the client's own gallery. Every other producer in this Hub files
    # what it makes (hub/image_audit.py); without this an edit somebody just
    # decided is the deliverable sits in the "commercials" Cloudinary folder
    # with a client tag and nowhere a client's own record can show it.
    # Best-effort -- the file is already stored above, and a gallery write
    # that fails must not cost the save that already succeeded.
    gallery_url = ""
    if job.client_name:
        try:
            from modules.image_picker import filing
            filed = filing.file_asset(
                client_name=job.client_name, public_id=asset.public_id,
                url=asset.url, kind="video_edit",
                label=f"Video Tools — {job.tool.replace('_', ' ').title()}",
                filename=filename, resource_type="video",
                provider="video_tools", saved_by="video_tools")
            gallery_url = filed.get("gallery_url", "") if filed.get("ok") else ""
        except Exception:                                # noqa: BLE001
            pass

    return {"public_id": asset.public_id, "url": asset.url,
            "gallery_url": gallery_url}


def output_name(job) -> str:
    """A filename that says what it is, from the source's own name.

    Named rather than left to Cloudinary's random suffix because these land in
    a client's gallery beside their source, and "Q1_Rebates_vertical-9x16.mp4"
    beside "Q1_Rebates.mp4" is the whole difference between a library somebody
    can use and one they re-derive from every time.
    """
    stem = str(job.source_public_id or "video").rsplit("/", 1)[-1]
    if job.tool == "reframe":
        ratio = str((job.options or {}).get("ratio") or config.DEFAULT_RATIO)
        return f"{stem}-{ratio.replace(':', 'x')}.mp4"
    return f"{stem}-tightened.mp4"
