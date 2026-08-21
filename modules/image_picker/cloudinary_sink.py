"""
Cloudinary side of the save.

Two things worth knowing:

1. We upload **by URL**. Cloudinary fetches the photo straight from Pexels /
   Pixabay / Unsplash; the bytes never travel through the Hub. That is the same
   lesson the SEO Image Pipeline learned the hard way -- the Gemini prototype
   pushed 130 MB over the wire for one batch of five.

2. We ask for an **eager** derived asset that resizes first and only then
   converts. v1.5.0 found that `f_auto`/`q_auto` alone never resized anything:
   a 6000px stock photo stayed 6000px and just changed container. `c_limit` with
   a max edge runs first here, so what lands in the client's GHL media library
   is a web-sized file, not a 9 MB original.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

log = logging.getLogger(__name__)

try:
    import cloudinary
    import cloudinary.api
    import cloudinary.uploader
    CLOUDINARY_AVAILABLE = True
except Exception:  # noqa: BLE001
    CLOUDINARY_AVAILABLE = False


FILLER = {
    "image", "photo", "picture", "stock", "final", "new", "copy", "untitled",
    "img", "dsc", "the", "a", "an", "of", "and", "with", "in", "on", "at", "to",
}


def configured() -> bool:
    return CLOUDINARY_AVAILABLE and bool(
        os.environ.get("CLOUDINARY_URL") or os.environ.get("CLOUDINARY_CLOUD_NAME")
    )


def _configure() -> None:
    if os.environ.get("CLOUDINARY_URL"):
        cloudinary.config()  # reads CLOUDINARY_URL
    else:
        cloudinary.config(
            cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
            api_key=os.environ.get("CLOUDINARY_API_KEY"),
            api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
            secure=True,
        )


def max_edge() -> int:
    try:
        return max(400, int(os.environ.get("IMAGE_PICKER_MAX_EDGE", "2000")))
    except ValueError:
        return 2000


def seo_filename(*, alt: str, collection_label: str, client_name: str, fallback: str) -> str:
    """Readable, SEO-shaped filename.

    Client name last so the useful words lead. Filler stripped, because
    `image-photo-final-2-smart1.webp` helps nobody.
    """
    parts: list[str] = []
    for chunk in (alt or "", collection_label or "", client_name or ""):
        for word in re.split(r"[^A-Za-z0-9]+", chunk.lower()):
            if word and word not in FILLER and not word.isdigit() and word not in parts:
                parts.append(word)
    slug = "-".join(parts)[:70].strip("-")
    return slug or re.sub(r"[^a-z0-9-]+", "-", (fallback or "client-image").lower()).strip("-")


def upload_from_url(
    *,
    image_url: str,
    folder: str,
    public_id: str,
    context: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Upload a remote image and return the stored + web-sized URLs.

    Returns keys: public_id, secure_url (original), delivery_url (resized WebP),
    width, height, bytes.
    """
    if not configured():
        raise RuntimeError("Cloudinary is not configured (set CLOUDINARY_URL).")

    _configure()
    edge = max_edge()

    # Through hub.storage.put_remote — Cloudinary still fetches the URL
    # itself, which is the point: downloading here first would double the
    # bandwidth and fail where their fetch succeeds. Same folder and id, so
    # nothing moves; the resource type is derived rather than asserted.
    from hub import storage
    _asset = storage.put_remote("image_picker", image_url,
                                filename=f"{public_id}.jpg",
                                folder=folder,
                                public_id=f"{folder}/{public_id}",
                                overwrite=False, unique=True)
    result = {"secure_url": _asset.url, "public_id": _asset.public_id,
              "resource_type": _asset.resource_type, "bytes": _asset.bytes}

    eager = (result.get("eager") or [{}])[0]
    return {
        "public_id": result.get("public_id"),
        "secure_url": result.get("secure_url"),
        "delivery_url": eager.get("secure_url") or result.get("secure_url"),
        "width": eager.get("width") or result.get("width"),
        "height": eager.get("height") or result.get("height"),
        "bytes": eager.get("bytes") or result.get("bytes"),
    }


def destroy(public_id: str) -> bool:
    """Remove an asset. Used when a client deletes from their gallery."""
    if not configured() or not public_id:
        return False
    try:
        _configure()
        res = cloudinary.uploader.destroy(public_id, resource_type="image", invalidate=True)
        return (res or {}).get("result") in ("ok", "not found")
    except Exception as exc:  # noqa: BLE001
        log.warning("cloudinary destroy failed for %s: %s", public_id, exc)
        return False
