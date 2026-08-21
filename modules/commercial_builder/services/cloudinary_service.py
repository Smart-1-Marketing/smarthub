"""Cloudinary service — the client's Creative Library (spec section 2).

Reuses the Hub's existing `CLOUDINARY_URL` env var (already configured per
the v1.6.0 handoff) and just adds the folder convention this module needs:

    /{client-slug}/logos/
    /{client-slug}/photos/
    /{client-slug}/videos/
    /{client-slug}/commercials/
    /{client-slug}/audio/
"""

import os
import time

try:
    import cloudinary
    import cloudinary.uploader
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False

_CONFIGURED = False


def is_live():
    return _SDK_AVAILABLE and bool(os.environ.get("CLOUDINARY_URL") or os.environ.get("CLOUDINARY_CLOUD_NAME"))


def _ensure_configured():
    global _CONFIGURED
    if _CONFIGURED or not _SDK_AVAILABLE:
        return
    if os.environ.get("CLOUDINARY_URL"):
        cloudinary.config(cloudinary_url=os.environ["CLOUDINARY_URL"])
    elif os.environ.get("CLOUDINARY_CLOUD_NAME"):
        cloudinary.config(
            cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
            api_key=os.environ.get("CLOUDINARY_API_KEY"),
            api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
        )
    _CONFIGURED = True


CATEGORY_FOLDERS = {
    "logo": "logos", "photo": "photos", "video": "videos",
    "commercial": "commercials", "audio": "audio",
}


def client_folder(client_slug, category):
    sub = CATEGORY_FOLDERS.get(category, category)
    return f"{client_slug}/{sub}"


def upload_asset(file_path_or_url, client_slug, category, public_id=None, resource_type="auto"):
    if not is_live():
        return {"secure_url": file_path_or_url if str(file_path_or_url).startswith("http") else None,
                "public_id": public_id or f"mock_{int(time.time())}",
                "folder": client_folder(client_slug, category), "_mock": True}

    _ensure_configured()
    try:
        # Through hub.storage. A local path is read and stored as bytes; a URL
        # is still fetched by Cloudinary rather than pulled through this
        # process. Folder and id are unchanged, so existing assets stay put.
        from hub import storage
        folder = client_folder(client_slug, category)
        name = os.path.basename(str(file_path_or_url).split("?")[0]) or public_id
        src = str(file_path_or_url)
        if src.startswith(("http://", "https://")):
            asset = storage.put_remote("commercials", src, filename=name,
                                       folder=folder,
                                       public_id=f"{folder}/{public_id}",
                                       overwrite=True, unique=False)
        else:
            with open(src, "rb") as fh:
                asset = storage.put("commercials", name, fh.read(),
                                    folder=folder,
                                    public_id=f"{folder}/{public_id}",
                                    overwrite=True)
        return {"secure_url": asset.url, "public_id": asset.public_id,
                "folder": folder}
    except Exception as e:
        return {"secure_url": None, "public_id": None, "error": str(e)}


def list_client_assets(client_slug, category):
    if not is_live():
        return []
    _ensure_configured()
    try:
        result = cloudinary.api.resources(type="upload", prefix=f"{client_folder(client_slug, category)}/",
                                           max_results=100)
        return [{"secure_url": r.get("secure_url"), "public_id": r.get("public_id"),
                 "format": r.get("format"), "created_at": r.get("created_at")}
                for r in result.get("resources", [])]
    except Exception:
        return []
