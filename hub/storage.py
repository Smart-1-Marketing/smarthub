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
# Audio counts as "video" to Cloudinary — it has no separate audio type, and
# an .mp3 uploaded as "raw" is stored but not transformable or streamable.
# Radio Promo and Fan Radio both knew this and said so at their own call
# sites; the shared function did not, so deriving the type here would have
# quietly downgraded every spot they upload.
_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".flac", ".weba"}
_VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v"} | _AUDIO_EXT


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


def _note_asset(bucket: str, op: str, nbytes: int, public_id: str) -> None:
    """Count one Cloudinary operation against the credit estimate.

    The logical bucket ("seo_images", "proposals") is what gets attributed,
    not the calling module: several modules write into one bucket and the
    bucket is what a Cloudinary folder listing shows, so it is the label that
    can be reconciled against their console.

    Only the Cloudinary path records. A write that fell through to the disk
    costs Cloudinary nothing, and counting it would put storage on the
    estimate that is not on the bill.
    """
    try:
        from hub import quotas as _q
        _q.record_asset(module=bucket or "hub", kind=op, nbytes=nbytes,
                        detail=public_id)
    except Exception:                       # noqa: BLE001
        pass


def put(kind: str, filename: str, data: bytes, *, client: str = "",
        subpath: str = "", context: dict | None = None,
        overwrite: bool = False, tags: list | tuple | None = None,
        public_id: str = "", folder: str = "") -> StoredAsset:
    """Store bytes and return where they went.

    ``kind`` is a logical bucket ("seo_images", "proposals", …) resolved to a
    folder by hub.config, not a raw folder string — that is what stops two
    features colliding in one namespace.

    tags, public_id and folder exist so a module that already has
    assets in Cloudinary can move onto this function without moving its files.
    A module that has been writing to its own folder for a year has URLs in
    client inboxes and listing code that walks that folder; changing the layout
    to adopt a shared uploader would be a migration disguised as a refactor.
    Pass the existing folder (or a full public_id) and only the *code path*
    changes — where the bytes land does not.
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
    folder = folder or "/".join(parts)
    public_id = public_id or f"{folder}/{slug(base, 'file')}"

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
            tags=list(tags) if tags else None,
        )
        _note_asset(kind, "upload", len(data), public_id)
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


def put_remote(kind: str, url: str, *, filename: str = "", client: str = "",
               subpath: str = "", public_id: str = "", folder: str = "",
               overwrite: bool = False, unique: bool = True,
               context: dict | None = None,
               tags: list | tuple | None = None) -> StoredAsset:
    """Store an asset Cloudinary fetches itself, from a URL.

    Image Picker and Commercial Builder hand Cloudinary a URL rather than
    bytes. Making them download first so they could call put() would be slower,
    would double the bandwidth, and would fail in cases where Cloudinary's own
    fetch succeeds — a worse module in exchange for a tidier call site. So the
    shared layer learns the remote case instead.

    Requires Cloudinary: there is no sensible disk fallback for "have someone
    else fetch this", and silently downloading it here would reintroduce the
    behaviour this exists to avoid.
    """
    if not url:
        raise StorageError("put_remote needs a URL.")
    if not ready():
        raise StorageError("Cloudinary is not configured; cannot fetch a "
                           "remote asset.")
    _configure()
    name = filename or os.path.basename(url.split("?")[0]) or "asset"
    rtype = resource_type_for(name)
    parts = [settings.folder(kind)]
    if client:
        parts.append(slug(client, "client"))
    if subpath:
        parts.append(slug(subpath, "batch"))
    folder = folder or "/".join(parts)
    res = cloudinary.uploader.upload(
        url,
        public_id=public_id or f"{folder}/{slug(os.path.splitext(name)[0], 'file')}",
        resource_type=rtype,
        overwrite=overwrite,
        unique_filename=unique,
        use_filename=False,
        invalidate=True,
        context=context or None,
        tags=list(tags) if tags else None,
    )
    _note_asset(kind, "fetch", int(res.get("bytes") or 0),
                res.get("public_id", ""))
    return StoredAsset(
        public_id=res.get("public_id", ""),
        url=res.get("secure_url") or res.get("url", ""),
        resource_type=rtype, bytes=int(res.get("bytes") or 0),
        backend="cloudinary", folder=folder, checksum="")


def delete(kind: str, public_id: str, resource_type: str = "raw") -> bool:
    """Remove an asset. Never raises — a failed delete must not break a page."""
    try:
        if ready() and "/" in public_id:
            _configure()
            cloudinary.uploader.destroy(public_id, resource_type=resource_type,
                                        invalidate=True)
            _note_asset(kind, "delete", 0, public_id)
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


# ------------------------------------------------------------- previews
# One derived size for every gallery in the Hub, and deliberately not one per
# box. Cloudinary bills a credit per thousand transformations and caches each
# derivative separately, so a 64px row thumb, a 120px tile and a 300px grid
# cell asked for at their own sizes is three derivatives of every image in the
# account to save bytes nobody would notice. 400 covers all three: a 400px
# image in a 64px box is still two orders of magnitude smaller than the
# original a phone produced.
THUMB_EDGE = 400

# The transformation we insert, and the marker that says we already have.
_THUMB_T = "c_limit,w_{edge},h_{edge},f_auto,q_auto"


def thumb_url(public_id: str, edge: int = THUMB_EDGE) -> str:
    """Derived thumbnail from a public_id. Galleries must never request the
    full asset.

    `preview_url()` is the sibling for a row that carries a delivery URL and
    no public_id, which is most of them.
    """
    if not ready():
        return ""
    _configure()
    url, _ = cloudinary.utils.cloudinary_url(
        public_id, resource_type="image", secure=True,
        transformation=[{"width": edge, "height": edge, "crop": "limit",
                         "quality": "auto", "fetch_format": "auto"}])
    return url


def preview_url(url: str, resource_type: str = "", edge: int = THUMB_EDGE) -> str:
    """The version a gallery draws, from a stored delivery URL.

    Every gallery in this Hub drew `<img src="{the original}">` into a box a
    fraction of its size: a client uploads forty photographs off a phone and
    the staff gallery delivers a hundred and sixty megabytes to fill forty
    64x48 boxes. Nothing errors, the pictures are right, and the only symptoms
    are a slow page and a Cloudinary bill -- which is charged in credits, one
    of which is a gigabyte delivered, so this is the line item rather than
    what we upload. `thumb_url()` existed for exactly this and had no caller.

    Four rules, each a way to turn a working tile into a broken one:

    * **Anything that is not ours comes back unchanged.** A stock provider's
      CDN, a Google Drive link, a `data:` URI, an empty string. The same
      answer `attachment_url()` gives, for the same reason: rewriting a URL
      we do not own produces a 404 where there was a picture.
    * **Only an image.** An image transformation on a `/raw/upload/` or
      `/video/upload/` URL 404s -- Cloudinary keeps the three in separate
      namespaces, the lesson `cloudinary_sink.destroy()` paid for when a PDF
      asked for as an image came back "not found" and read as a clean
      success. A row's own `resource_type` is believed first, and the URL's
      own segment decides when it says nothing.
    * **Idempotent.** A row rewritten here and handed to a caller that
      rewrites again must not chain two transformations, so the marker we
      insert is what we look for.
    * **`c_limit`, never `c_fill` or a bare width.** It caps and never
      upscales or crops: a 180px logo stays 180px instead of being blown up
      and re-encoded, and a tall photograph keeps its subject instead of
      being centre-cropped through it.
    """
    u = str(url or "")
    if "res.cloudinary.com" not in u or "/upload/" not in u:
        return u
    kind = str(resource_type or "").strip().lower()
    if kind and kind != "image":
        return u
    if not kind and "/image/upload/" not in u:
        return u                            # raw or video, by its own segment
    edge = max(1, int(edge or THUMB_EDGE))
    t = _THUMB_T.format(edge=edge)
    if f"/{t}/" in u:
        return u                            # already a preview
    return u.replace("/upload/", f"/upload/{t}/", 1)


# --------------------------------------------------------------- downloads
# Getting a stored file back out is a storage concern, and it was being solved
# per module: the image picker had its own zip builder, blog images built an
# fl_attachment URL inline, and the SEO pipeline had neither. One copy here,
# so the next module that needs it does not write a fourth.
def attachment_url(url: str, filename: str = "") -> str:
    """A URL that downloads instead of displaying.

    The `download` attribute on an <a> is IGNORED cross-origin, so linking a
    Cloudinary URL with `download` opens the image in a tab and the button
    looks broken. `fl_attachment` makes Cloudinary send Content-Disposition,
    which is the only thing that actually works — and the name after the colon
    is what the file is called on the way down. That matters here more than
    most places: the SEO filename *is* the deliverable.

    Anything that is not a Cloudinary delivery URL is returned unchanged
    rather than rewritten into something that 404s.
    """
    u = str(url or "")
    if "/upload/" not in u or "res.cloudinary.com" not in u:
        return u
    name = slug(os.path.splitext(str(filename or ""))[0], "")
    flag = f"fl_attachment:{name}" if name else "fl_attachment"
    return u.replace("/upload/", f"/upload/{flag}/", 1)


# A ceiling, because this streams through the Hub rather than from the CDN.
# Two gunicorn workers and a 512 MB dyno: an unbounded "select all" on a
# thousand-row archive is the one request that takes the whole service down.
ZIP_MAX_FILES = 200
ZIP_MAX_BYTES = 300 * 1024 * 1024


def bundle_zip(items, *, bucket: str = "", timeout: int = 25) -> tuple[bytes, list[str]]:
    """Several stored files as one zip: (zip_bytes, missing_names).

    `items` is an iterable of {"url", "filename"}. The alternative — opening
    each file in its own tab — is blocked by every popup blocker once there is
    more than one, so the browser silently delivers the first and nothing
    else.

    A file that cannot be fetched is skipped and named in a MISSING.txt inside
    the archive rather than failing the whole download: a partial set with an
    explanation beats an error page, and the caller still gets the list back
    so it can say so on screen too.
    """
    import io as _io
    import zipfile

    import requests as _rq

    buf = _io.BytesIO()
    missing: list[str] = []
    used: set[str] = set()
    delivered = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in list(items)[:ZIP_MAX_FILES]:
            url = str((item or {}).get("url") or "")
            name = str((item or {}).get("filename") or "").strip() or "file"
            name = name.replace("/", "-").replace("\\", "-")
            # Two files can genuinely share a name; without this the zip keeps
            # only the last one and the count silently drops.
            base, dot, ext = name.rpartition(".")
            candidate, n = name, 2
            while candidate.lower() in used:
                candidate = f"{base}-{n}.{ext}" if dot else f"{name}-{n}"
                n += 1
            used.add(candidate.lower())
            if not url:
                missing.append(candidate)
                continue
            if delivered >= ZIP_MAX_BYTES:
                missing.append(candidate + "  (the zip hit its size limit)")
                continue
            try:
                resp = _rq.get(url, timeout=timeout)
                resp.raise_for_status()
            except Exception:                           # noqa: BLE001
                missing.append(candidate)
                continue
            delivered += len(resp.content)
            zf.writestr(candidate, resp.content)
        if missing:
            zf.writestr("MISSING.txt",
                        "These files could not be fetched and are not in this "
                        "zip:\n\n" + "\n".join(missing) + "\n")

    # Cloudinary bills a credit per GB DELIVERED, not per call, and a zip pulls
    # every byte through here. Counting the files would make a 40 KB thumbnail
    # and a 4 MB hero cost the same on the usage page; the bytes are what the
    # bill is made of.
    if delivered and bucket:
        _note_asset(bucket, "download", delivered, f"zip x{len(used) - len(missing)}")
    buf.seek(0)
    return buf.getvalue(), missing


def configure() -> None:
    """Point the Cloudinary SDK at this deployment's account.

    Public because a module sometimes has to reach the SDK directly for
    something the functions here do not wrap — `services/provider_check.py`
    pings the account to tell a refused key from an unreachable one. Those
    call sites should not each carry their own `cloudinary.config()`: that is
    the drift this module exists to stop, and one of those copies read the
    three-part credential group by hand while this one reads the composed
    `CLOUDINARY_URL` that `hub/config.export_cloudinary_url()` puts into the
    environment for exactly this purpose. Idempotent, and a no-op where
    Cloudinary is not configured at all.
    """
    _configure()


def manifest(kind: str, max_results: int = 500, prefix: str = "") -> list[dict]:
    """Inventory of what is actually stored — feeds the orphaned-asset audit.

    Carries `secure_url` and `format` as well as the id. Without the URL the
    audit can list an orphan and neither show it nor file it, which makes the
    report a list of ids somebody has to go and look up by hand — and this
    function had no caller at all until that audit was built, so nothing
    depended on the narrower shape.

    `prefix` lists one folder inside the bucket rather than the whole of it —
    the Commercial Builder keeps a tree per client (`<slug>/photos/`) and was
    calling `cloudinary.api.resources` itself to read one. Extending this
    rather than leaving that copy in place is the rule `hub/storage.py` exists
    for: the next fix to paging, or to what a row carries, lands once. The
    bucket still decides where a missing prefix looks, so no caller has to
    know how a folder is composed.
    """
    if not ready():
        root = _disk_root(kind)
        return [{"public_id": f, "bytes": os.path.getsize(os.path.join(root, f))}
                for f in sorted(os.listdir(root))]
    _configure()
    out, cursor = [], None
    prefix = prefix or settings.folder(kind)
    while True:
        res = cloudinary.api.resources(type="upload", prefix=prefix,
                                       max_results=min(500, max_results),
                                       next_cursor=cursor)
        for r in res.get("resources", []):
            out.append({"public_id": r.get("public_id"), "bytes": r.get("bytes"),
                        "created_at": r.get("created_at"),
                        "resource_type": r.get("resource_type"),
                        "format": r.get("format", ""),
                        "secure_url": r.get("secure_url", "")})
        cursor = res.get("next_cursor")
        if not cursor or len(out) >= max_results:
            return out[:max_results]
