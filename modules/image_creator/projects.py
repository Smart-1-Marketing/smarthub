"""Saved editor projects.

A project stores Fabric's serialised JSON, not a flattened image, so reopening
one gives back every editable object exactly where it was. Alongside it we
store a small WebP preview so a project library can show thumbnails without
parsing canvases.

Projects are attached to a client where one applies, which is what makes them
findable later: "everything we built for Cool Air Co" is a search, not a
folder hunt. Projects with no client are just as valid — a one-off graphic
doesn't need to be filed under anyone.

The canvas JSON goes to Cloudinary as a raw asset with a local mirror, the
same pattern the Proposal Builder uses, so work survives a redeploy.
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import os
import re
import secrets
import threading

FOLDER = os.environ.get("IMAGE_CREATOR_FOLDER", "smart1-image-projects")
_CLOUD_URL = (os.environ.get("CLOUDINARY_URL") or "").strip()
_CLOUD_NAME = (os.environ.get("CLOUDINARY_CLOUD_NAME") or "").strip()
_CLOUD_KEY = (os.environ.get("CLOUDINARY_API_KEY") or "").strip()
_CLOUD_SECRET = (os.environ.get("CLOUDINARY_API_SECRET") or "").strip()

try:
    import cloudinary
    import cloudinary.uploader
    if _CLOUD_URL.startswith("cloudinary://"):
        cloudinary.config(secure=True)
        CLOUD_READY = True
    elif _CLOUD_NAME and _CLOUD_KEY and _CLOUD_SECRET:
        cloudinary.config(cloud_name=_CLOUD_NAME, api_key=_CLOUD_KEY,
                          api_secret=_CLOUD_SECRET, secure=True)
        CLOUD_READY = True
    else:
        CLOUD_READY = False
except ImportError:                                   # pragma: no cover
    cloudinary = None
    CLOUD_READY = False

_lock = threading.Lock()


def cloud_ready() -> bool:
    return CLOUD_READY


def _data_dir() -> str:
    base = "/var/data" if os.path.isdir("/var/data") else os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data")
    path = os.path.join(base, "image-projects")
    os.makedirs(path, exist_ok=True)
    return path


def _index_path() -> str:
    return os.path.join(_data_dir(), "_index.json")


def slugify(v: str, fallback: str = "project") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(v or "").lower()).strip("-")
    return s[:80] or fallback


def load_index() -> list[dict]:
    try:
        with open(_index_path(), encoding="utf-8") as fh:
            rows = json.load(fh)
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def _save_index(rows: list[dict]):
    with _lock:
        tmp = _index_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1)
        os.replace(tmp, _index_path())


def _canvas_path(pid: str) -> str:
    return os.path.join(_data_dir(), f"{pid}.json")


def _upload_raw(pid: str, canvas: dict) -> str:
    if not CLOUD_READY:
        return ""
    # Through hub.storage, same public_id. The .json gives the shared
    # derivation "raw" without the call site stating it, and hub.storage
    # builds the data URI, so the base64 step here is gone.
    from hub import storage
    _pid = f"{FOLDER}/canvas/{pid}.json"
    return storage.put("image_projects", _pid,
                       json.dumps(canvas).encode("utf-8"),
                       public_id=_pid, overwrite=True).url


def _upload_preview(pid: str, data_url: str) -> tuple[str, int, int]:
    """The preview is whatever the canvas rendered, re-encoded small."""
    if not data_url.startswith("data:image"):
        return "", 0, 0
    try:
        header, b64 = data_url.split(",", 1)
        raw = base64.b64decode(b64)
    except (ValueError, TypeError):
        return "", 0, 0

    try:                                              # shrink before storing
        import io
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert("RGB")
            im.thumbnail((640, 640))
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=82)
            raw = buf.getvalue()
            w, h = im.size
    except Exception:                                 # noqa: BLE001
        w = h = 0

    if CLOUD_READY:
        res = cloudinary.uploader.upload(
            raw, folder=f"{FOLDER}/previews", public_id=pid,
            overwrite=True, invalidate=True, format="webp", quality="auto")
        return res.get("secure_url", ""), w, h

    path = os.path.join(_data_dir(), f"{pid}-preview.webp")
    with open(path, "wb") as fh:
        fh.write(raw)
    return f"/tools/image-creator/api/projects/{pid}/preview", w, h


# ------------------------------------------------------------------- public
def save_project(name: str, canvas: dict, preview: str = "", client: str = "",
                 client_slug: str = "", tags: str = "", pid: str = "",
                 width: int = 0, height: int = 0, actor: str = "") -> dict:
    name = str(name or "").strip()[:200] or "Untitled project"
    if not isinstance(canvas, dict) or not canvas:
        raise ValueError("Nothing to save — the canvas is empty.")

    rows = load_index()
    existing = next((r for r in rows if r.get("id") == pid), None) if pid else None
    pid = existing["id"] if existing else ("proj_" + secrets.token_hex(6))
    now = _dt.datetime.now()

    with open(_canvas_path(pid), "w", encoding="utf-8") as fh:
        json.dump(canvas, fh)
    canvas_url = ""
    try:
        canvas_url = _upload_raw(pid, canvas)
    except Exception as exc:                          # noqa: BLE001 — local copy stands
        print("project canvas upload failed:", exc)

    preview_url = existing.get("preview_url", "") if existing else ""
    if preview:
        try:
            url, _w, _h = _upload_preview(pid, preview)
            if url:
                preview_url = url
        except Exception as exc:                      # noqa: BLE001
            print("project preview upload failed:", exc)

    record = {
        "id": pid,
        "name": name,
        "slug": slugify(name),
        "client": str(client or "").strip()[:200],
        "client_slug": str(client_slug or "").strip()[:120]
                       or (slugify(client) if client else ""),
        "tags": [t.strip()[:40] for t in str(tags or "").split(",") if t.strip()][:12],
        "width": int(width or (canvas.get("width") or 0)),
        "height": int(height or (canvas.get("height") or 0)),
        "objects": len(canvas.get("objects") or []),
        "preview_url": preview_url,
        "canvas_url": canvas_url,
        "created": existing.get("created") if existing else now.strftime("%Y-%m-%d %H:%M"),
        "created_date": existing.get("created_date") if existing else now.date().isoformat(),
        "updated": now.strftime("%Y-%m-%d %H:%M"),
        "created_by": existing.get("created_by") if existing else str(actor or "")[:120],
        "updated_by": str(actor or "")[:120],
    }
    rows = [r for r in rows if r.get("id") != pid]
    rows.insert(0, record)
    _save_index(rows[:3000])
    return record


def get_canvas(pid: str) -> dict | None:
    if not re.match(r"^proj_[a-f0-9]{6,}$", str(pid or "")):
        return None
    try:
        with open(_canvas_path(pid), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        pass
    row = next((r for r in load_index() if r.get("id") == pid), None)
    if row and row.get("canvas_url"):                 # fall back to Cloudinary
        try:
            import requests
            r = requests.get(row["canvas_url"], timeout=12)
            if r.ok:
                return r.json()
        except Exception:                             # noqa: BLE001
            pass
    return None


def search_projects(q: str = "", client: str = "", limit: int = 100) -> list[dict]:
    rows = load_index()
    if client:
        cl = client.lower()
        rows = [r for r in rows
                if str(r.get("client", "")).lower() == cl
                or str(r.get("client_slug", "")).lower() == slugify(client)]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in str(r.get("name", "")).lower()
                or ql in str(r.get("client", "")).lower()
                or any(ql in t.lower() for t in r.get("tags", []))]
    return rows[:max(1, min(int(limit), 500))]


def delete_project(pid: str) -> bool:
    rows = load_index()
    hit = next((r for r in rows if r.get("id") == pid), None)
    if hit is None:
        return False
    if CLOUD_READY:
        for public_id, kind in ((f"{FOLDER}/canvas/{pid}.json", "raw"),
                                (f"{FOLDER}/previews/{pid}", "image")):
            try:
                cloudinary.uploader.destroy(public_id, resource_type=kind,
                                            invalidate=True)
            except Exception as exc:                  # noqa: BLE001
                print("cloudinary destroy failed:", exc)
    for path in (_canvas_path(pid), os.path.join(_data_dir(), f"{pid}-preview.webp")):
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
    _save_index([r for r in rows if r.get("id") != pid])
    return True


def local_preview_path(pid: str) -> str | None:
    if not re.match(r"^proj_[a-f0-9]{6,}$", str(pid or "")):
        return None
    path = os.path.join(_data_dir(), f"{pid}-preview.webp")
    return path if os.path.isfile(path) else None


def export_to_cloudinary(data_url: str, name: str, client: str = "",
                         project: str = "") -> dict:
    """Push a finished export to Cloudinary so it can be linked or handed to
    a client without leaving the browser."""
    if not CLOUD_READY:
        raise RuntimeError("Cloudinary isn't configured.")
    if not str(data_url or "").startswith("data:image"):
        raise ValueError("Nothing to export.")
    _, b64 = data_url.split(",", 1)
    raw = base64.b64decode(b64)
    folder = f"{FOLDER}/exports/{slugify(client, 'unfiled')}"
    res = cloudinary.uploader.upload(
        raw, folder=folder, public_id=slugify(name, "export"),
        overwrite=False, unique_filename=True, use_filename=False,
        quality="auto", context={"client": client, "project": project})
    return {"url": res.get("secure_url", ""), "public_id": res.get("public_id", ""),
            "width": res.get("width"), "height": res.get("height"),
            "bytes": res.get("bytes")}
