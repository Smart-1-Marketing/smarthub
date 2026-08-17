"""One storage layer for every module.

Before v7 there were six separate Cloudinary blocks — hub/proposals.py,
modules/proposal_builder/store.py, modules/seo_images, modules/image_creator,
modules/bg_remover and hub/__init__.py — each with its own config call, its own
folder env var, its own readiness check and its own disk fallback.

Two real defects came out of that:

  * hub/proposals.py and modules/proposal_builder/store.py both default to the
    folder "smart1-proposals" from the same CLOUDINARY_FOLDER variable, so two
    different features write into one namespace,
  * resource_type was chosen per call site. Choosing "image" for a PDF makes
    Cloudinary refuse to deliver it, so the upload succeeds, no fallback fires,
    and the customer's download link 403s. That exact bug shipped in the suite.

Here resource_type is derived from the file, never passed in by a caller.
Every write is mirrored to the persistent disk when Cloudinary is unavailable,
so a missing CLOUDINARY_URL degrades instead of losing the file.
"""
from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
import re
import time
from dataclasses import dataclass

from hub.config import settings

try:                                        # pragma: no cover - optional dep
    import cloudinary
    import cloudinary.api
    import cloudinary.uploader
    import cloudinary.utils
except Exception:                           # noqa: BLE001
    cloudinary = None

_configured = False

# Cloudinary treats these as deliverable images. Everything else — PDF, DOC,
# JSON, SVG, ZIP — must be uploaded raw or delivery is blocked.
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"}
_VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v"}


class StorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredAsset:
    public_id: str
    url: str
    resource_type: str
    bytes: int
    backend: str          # "cloudinary" | "disk"
    folder: str
    checksum: str

    def as_dict(self) -> dict:
        return {"public_id": self.public_id, "url": self.url,
                "resource_type": self.resource_type, "bytes": self.bytes,
                "backend": self.backend, "folder": self.folder,
                "checksum": self.checksum}


def slug(text: str, fallback: str = "item") -> str:
    out = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (out or fallback)[:80]


def resource_type_for(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in _IMAGE_EXT:
        return "image"
    if ext in _VIDEO_EXT:
        return "video"
    return "raw"


def ready() -> bool:
    return bool(cloudinary) and settings.cloudinary_ready


def _configure() -> None:
    global _configured
    if _configured or not ready():
        return
    cloudinary.config(secure=True)          # reads CLOUDINARY_URL
    _configured = True


def _disk_root(kind: str) -> str:
    path = os.path.join(settings.data_dir, "assets", kind)
    os.makedirs(path, exist_ok=True)
    return path


def put(kind: str, filename: str, data: bytes, *, client: str = "",
        subpath: str = "", context: dict | None = None,
        overwrite: bool = False) -> StoredAsset:
    """Store bytes and return where they went.

    ``kind`` is a logical bucket ("seo_images", "proposals", …) resolved to a
    folder by hub.config, not a raw folder string — that is what stops two
    features colliding in one namespace.
    """
    if not data:
        raise StorageError("Refusing to store an empty file.")
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise StorageError(f"File is {len(data)//1048576} MB; the limit is {settings.max_upload_mb} MB.")

    checksum = hashlib.sha256(data).hexdigest()
    rtype = resource_type_for(filename)
    base = os.path.splitext(os.path.basename(filename))[0]
    parts = [settings.folder(kind)]
    if client:
        parts.append(slug(client, "client"))
    if subpath:
        parts.append(slug(subpath, "batch"))
    folder = "/".join(parts)
    public_id = f"{folder}/{slug(base, 'file')}"

    if ready():
        _configure()
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        uri = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        res = cloudinary.uploader.upload(
            uri,
            public_id=public_id,
            resource_type=rtype,
            overwrite=overwrite,
            unique_filename=not overwrite,
            invalidate=True,
            context=context or None,
        )
        return StoredAsset(
            public_id=res.get("public_id", public_id),
            url=res.get("secure_url") or res.get("url", ""),
            resource_type=rtype, bytes=len(data), backend="cloudinary",
            folder=folder, checksum=checksum)

    # ---- disk fallback ----
    root = _disk_root(kind)
    safe = f"{slug(base,'file')}-{int(time.time())}{os.path.splitext(filename)[1].lower()}"
    dest = os.path.join(root, safe)
    with open(dest, "wb") as fh:
        fh.write(data)
    return StoredAsset(public_id=safe, url=f"/hub/assets/{kind}/{safe}",
                       resource_type=rtype, bytes=len(data), backend="disk",
                       folder=folder, checksum=checksum)


def delete(kind: str, public_id: str, resource_type: str = "raw") -> bool:
    """Remove an asset. Never raises — a failed delete must not break a page."""
    try:
        if ready() and "/" in public_id:
            _configure()
            cloudinary.uploader.destroy(public_id, resource_type=resource_type,
                                        invalidate=True)
            return True
        path = os.path.join(_disk_root(kind), os.path.basename(public_id))
        if os.path.isfile(path):
            os.remove(path)
            return True
    except Exception:                       # noqa: BLE001
        return False
    return False


def signed_url(public_id: str, resource_type: str = "raw", ttl: int = 3600) -> str:
    """Time-limited URL. Use for anything client-confidential (proposals)."""
    if not ready():
        return ""
    _configure()
    url, _ = cloudinary.utils.cloudinary_url(
        public_id, resource_type=resource_type, type="upload",
        sign_url=True, expires_at=int(time.time()) + ttl)
    return url


def thumb_url(public_id: str, edge: int = 400) -> str:
    """Derived thumbnail. Galleries must never request the full asset."""
    if not ready():
        return ""
    _configure()
    url, _ = cloudinary.utils.cloudinary_url(
        public_id, resource_type="image", secure=True,
        transformation=[{"width": edge, "height": edge, "crop": "limit",
                         "quality": "auto", "fetch_format": "auto"}])
    return url


def manifest(kind: str, max_results: int = 500) -> list[dict]:
    """Inventory of what is actually stored — feeds the orphaned-asset audit."""
    if not ready():
        root = _disk_root(kind)
        return [{"public_id": f, "bytes": os.path.getsize(os.path.join(root, f))}
                for f in sorted(os.listdir(root))]
    _configure()
    out, cursor = [], None
    prefix = settings.folder(kind)
    while True:
        res = cloudinary.api.resources(type="upload", prefix=prefix,
                                       max_results=min(500, max_results),
                                       next_cursor=cursor)
        for r in res.get("resources", []):
            out.append({"public_id": r.get("public_id"), "bytes": r.get("bytes"),
                        "created_at": r.get("created_at"),
                        "resource_type": r.get("resource_type")})
        cursor = res.get("next_cursor")
        if not cursor or len(out) >= max_results:
            return out[:max_results]
