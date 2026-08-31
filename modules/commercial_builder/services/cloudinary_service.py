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


def _configured():
    """Whether Cloudinary is genuinely configured, placeholders excluded.

    `CLOUDINARY_URL` sat at `cloudinary://API_KEY:API_SECRET@CLOUD_NAME` on
    this deployment for a while, and a bare `bool(os.environ.get(...))` said
    yes to it — so this reported live and then failed to authenticate at the
    provider. The placeholder list is `hub.config`'s, because it is the one
    place that knows what env.example ships; the values are read here at call
    time, like every other key in this module. With no Hub to import, a
    placeholder is indistinguishable from a key and presence is all there is.
    """
    values = [(os.environ.get(n) or "").strip() for n in
              ("CLOUDINARY_URL", "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY",
               "CLOUDINARY_API_SECRET")]
    try:
        from hub.config import settings
        if any(settings.is_placeholder(v) for v in values):
            return False
    except Exception:  # noqa: BLE001 — standalone, or settings failed to build
        pass
    return bool(values[0] or values[1])


def is_live():
    return _SDK_AVAILABLE and _configured()


def _ensure_configured():
    """Point the SDK at the account, through the Hub where there is one.

    `hub.storage.configure()` is the one reading now. This function kept its
    own, and the two had already drifted in the way that matters: the Hub
    composes `CLOUDINARY_URL` from the three-part credential group and exports
    it (`hub/config.export_cloudinary_url()`), so a deployment given only the
    three parts is configured there and was configured here by a second,
    hand-written branch — right today, and one edit away from not being.

    The local branch survives as a **fallback**, because this module is
    written to run with no Hub to import and a service that refuses to
    configure at all is worse than one that duplicates four lines. Kept as a
    named function rather than inlined: `services/provider_check.py` calls it
    to ping the account, and that is a legitimate direct use of the SDK.
    """
    global _CONFIGURED
    if _CONFIGURED or not _SDK_AVAILABLE:
        return
    try:
        from hub import storage
        storage.configure()
        _CONFIGURED = True
        return
    except Exception:  # noqa: BLE001 — standalone, or the Hub failed to import
        pass
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


# Which categories hold a still. The gallery models an image or a raw file and
# not a video, so a commercial, a spokesperson clip and a voice track are
# deliberately NOT filed there -- a row whose thumbnail can never render is
# worse than an absent one. They stay in the client's Cloudinary tree, which is
# what `client_folder()` has always organised them into.
GALLERY_CATEGORIES = {"logo", "logos", "photo", "photos", "image", "images"}


def _is_url(src):
    return isinstance(src, str) and src.startswith(("http://", "https://"))


def _read_bytes(source):
    """Bytes from whatever a caller handed us, or None if it named a place.

    Four kinds arrive at `upload_asset`, and until now it understood two.
    Bytes and an open file object were read as a **path**: `str(x)` on a
    BytesIO is `<_io.BytesIO object at 0x7f...>`, which `open()` raises
    FileNotFoundError on, which the caller's own `except` turned into a quiet
    `{"secure_url": None}`. The QR code was the one caller passing bytes, so
    the QR image was never once stored — see the note on that call site.

    A file object is **rewound first**. A caller that has already read it
    would otherwise store nothing at all, which is the same silent-empty
    failure one layer down.
    """
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    reader = getattr(source, "read", None)
    if callable(reader):
        try:
            source.seek(0)
        except Exception:  # noqa: BLE001 — a stream that cannot rewind
            pass
        return reader()
    return None


def upload_asset(file_path_or_url, client_slug, category, public_id=None,
                 resource_type="auto", client_name="", filename=""):
    """Store an asset, from a URL, a path, raw bytes or an open file.

    `filename` is only consulted for bytes, which carry no name of their own —
    and it is asked for rather than guessed at, because the extension is what
    Cloudinary reads the format from and inventing one here would put a `.png`
    on an MP3. With none given the public id stands in.
    """
    data = _read_bytes(file_path_or_url)
    if not is_live():
        return {"secure_url": file_path_or_url if _is_url(file_path_or_url) else None,
                "public_id": public_id or f"mock_{int(time.time())}",
                "folder": client_folder(client_slug, category), "_mock": True}

    _ensure_configured()
    try:
        # Through hub.storage. Bytes go straight to it; a local path is read
        # and stored as bytes; a URL is still fetched by Cloudinary rather
        # than pulled through this process. Folder and id are unchanged, so
        # existing assets stay put.
        from hub import storage
        folder = client_folder(client_slug, category)
        src = file_path_or_url if isinstance(file_path_or_url, str) else ""
        name = (os.path.basename(src.split("?")[0]) if src else "") or filename or public_id
        if data is not None:
            asset = storage.put("commercials", name, data,
                                folder=folder,
                                public_id=f"{folder}/{public_id}",
                                overwrite=True)
        elif _is_url(src):
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
        out = {"secure_url": asset.url, "public_id": asset.public_id,
               "folder": folder}
        _file_in_gallery(client_name, category, out, name)
        return out
    except Exception as e:
        return {"secure_url": None, "public_id": None, "error": str(e)}


def _file_in_gallery(client_name, category, asset, filename):
    """A still made for a client belongs in that client's gallery too.

    The folder tree here has always been per client, which is organised and is
    not the same as findable: nothing outside this module reads a Cloudinary
    folder, so a logo or a still produced for a client was absent from the one
    page somebody opens to see what has been made for them.

    Filed by client NAME, never by the slug this module carries: the slug is
    this module's own and resolving one back to a client is a guess, and
    filing one client's creative into another client's gallery is the single
    mistake here that cannot be undone by editing a row. No name, no filing —
    and hub/image_audit.py reports what went unfiled.
    """
    if not client_name or str(category or "").lower() not in GALLERY_CATEGORIES:
        return
    if not asset.get("secure_url") or not asset.get("public_id"):
        return
    try:
        from modules.image_picker.filing import file_asset
        file_asset(client_name=client_name, public_id=asset["public_id"],
                   url=asset["secure_url"], kind="commercial",
                   filename=filename or "", provider="commercial_builder",
                   alt=f"Commercial Builder still for {client_name}",
                   saved_by="system")
    except Exception:  # noqa: BLE001 — the asset itself is stored
        pass


def list_client_assets(client_slug, category):
    """What is already in this client's tree, for the picker to offer first.

    Through `hub.storage.manifest()`, which took a folder prefix for exactly
    this — the shared reader pages properly, where the copy this replaced
    asked for one page of 100 and reported it as the whole folder. A client
    with more than a hundred photographs was quietly shown some of them.

    Falls back to the SDK directly with no Hub to import, the way
    `_ensure_configured()` does and for the same reason.
    """
    if not is_live():
        return []
    _ensure_configured()
    prefix = f"{client_folder(client_slug, category)}/"
    try:
        from hub import storage
        rows = storage.manifest("commercials", max_results=500, prefix=prefix)
        return [{"secure_url": r.get("secure_url"), "public_id": r.get("public_id"),
                 "format": r.get("format"), "created_at": r.get("created_at")}
                for r in rows]
    except Exception:  # noqa: BLE001 — standalone, or the listing failed
        pass
    try:
        result = cloudinary.api.resources(type="upload", prefix=prefix,
                                           max_results=100)
        return [{"secure_url": r.get("secure_url"), "public_id": r.get("public_id"),
                 "format": r.get("format"), "created_at": r.get("created_at")}
                for r in result.get("resources", [])]
    except Exception:
        return []
