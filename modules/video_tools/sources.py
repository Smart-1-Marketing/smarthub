"""Finding the clip to work on, and reading its shape.

Both tools start the same way: somebody has a video on the Cloudinary account
and needs to point at it. Three ways they will do that, all supported here
because in practice all three happen:

  * pick it from the list of recent videos on the account,
  * paste a Cloudinary delivery URL (what a rep copies out of the media
    library, or out of a finished Commercial Builder render),
  * type the public id, which is what an integration hands over.

All three end at a public id, because that is the asset's identity. A URL is
not: it carries a version and possibly a transformation, and re-editing from a
stored URL is how an edit comes to be applied to an edit -- a 9:16 crop of a
9:16 crop, at a quarter of the resolution, with nobody able to say why.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from . import config

try:
    import cloudinary
    import cloudinary.api
    import cloudinary.search
    _SDK = True
except ImportError:                                   # noqa: BLE001
    _SDK = False


class SourceError(RuntimeError):
    """Carries a sentence fit to put on the page."""


def _settings():
    from hub.config import settings
    return settings


def ready() -> bool:
    try:
        return _SDK and _settings().cloudinary_ready
    except Exception:                                 # noqa: BLE001
        return False


def base_url() -> str:
    cloud = _settings().cloudinary_cloud_name
    return f"https://res.cloudinary.com/{cloud}/video/upload" if cloud else ""


def _configure() -> None:
    from hub import storage
    storage.configure()


# A Cloudinary delivery URL is `/video/upload/[transformations/][vNNN/]<id>.<ext>`
# and the only fixed landmarks are the `upload` segment and the extension. The
# version segment is optional, and so is every transformation component --
# which is why this walks the segments rather than matching one big pattern.
_VERSION = re.compile(r"^v\d+$")
# Transformation components are comma-separated `xx_yyy` pairs. A folder name
# could in principle look like one, and the guard against that is that a
# transformation component is never the LAST segment: the last is the asset.
_TRANSFORM = re.compile(r"^[a-z]{1,3}_[^/]+$")


def public_id_from_url(url: str) -> str:
    """The asset id inside a Cloudinary delivery URL, or "".

    Deliberately forgiving about what is in front of the id and strict about
    what it returns: a caller that gets "" asks the person for the id instead
    of guessing, which is better than editing the wrong asset.
    """
    try:
        parsed = urlparse(str(url or ""))
    except Exception:                                 # noqa: BLE001
        return ""
    if "res.cloudinary.com" not in (parsed.netloc or ""):
        return ""
    parts = [p for p in (parsed.path or "").split("/") if p]
    if "upload" not in parts:
        return ""
    rest = parts[parts.index("upload") + 1:]
    if not rest:
        return ""
    keep = []
    for i, seg in enumerate(rest):
        last = i == len(rest) - 1
        if not last and (_VERSION.match(seg) or _TRANSFORM.match(seg)):
            continue
        keep.append(seg)
    if not keep:
        return ""
    keep[-1] = keep[-1].rsplit(".", 1)[0]
    return unquote("/".join(keep))


def resolve(raw: str) -> str:
    """Whatever the person typed or pasted, as a public id."""
    text = str(raw or "").strip()
    if not text:
        raise SourceError("Pick a video, or paste its Cloudinary link.")
    if text.startswith(("http://", "https://")):
        pid = public_id_from_url(text)
        if not pid:
            raise SourceError("That link is not a Cloudinary video on this "
                              "account. Paste the link from the media "
                              "library, or pick from the list.")
        return pid
    return text.lstrip("/")


def describe(public_id: str) -> dict:
    """The asset's real shape, read from Cloudinary rather than assumed.

    Every downstream number depends on this — the waveform's timeline, the
    crop's arithmetic, the segment offsets — so it is read once, here, and
    carried. The refusals below are cheaper than the alternative: a source
    Cloudinary will reject takes minutes to find out about by way of a failed
    derived asset.
    """
    if not ready():
        raise SourceError("Cloudinary is not configured on this deployment, "
                          "so there is nothing to edit.")
    _configure()
    try:
        res = cloudinary.api.resource(public_id, resource_type="video",
                                      type="upload")
    except Exception as exc:                          # noqa: BLE001
        raise SourceError(f"No video called “{public_id}” on this Cloudinary "
                          f"account.") from exc

    duration = float(res.get("duration") or 0)
    megabytes = float(res.get("bytes") or 0) / 1_000_000
    if duration > config.MAX_SOURCE_SECONDS:
        raise SourceError(f"That clip runs {int(duration // 60)} minutes. "
                          f"These tools stop at "
                          f"{config.MAX_SOURCE_SECONDS // 60} — longer than "
                          f"that, the edit is a job for an editor.")
    if megabytes > config.MAX_SOURCE_MB:
        raise SourceError(f"That file is {megabytes:.0f} MB, over the "
                          f"{config.MAX_SOURCE_MB} MB these tools handle.")

    width, height = int(res.get("width") or 0), int(res.get("height") or 0)
    return {
        "public_id": res.get("public_id") or public_id,
        "duration": duration,
        "width": width,
        "height": height,
        "aspect": round(width / height, 3) if width and height else 0,
        "bytes": int(res.get("bytes") or 0),
        "format": res.get("format") or "",
        "folder": res.get("asset_folder") or res.get("folder") or "",
        "created_at": res.get("created_at") or "",
        "url": res.get("secure_url") or "",
        "poster": _poster(res.get("public_id") or public_id),
        "has_audio": bool(res.get("audio")),
    }


def _poster(public_id: str) -> str:
    from urllib.parse import quote
    base = base_url()
    if not base:
        return ""
    return f"{base}/so_1/w_480,c_fill,q_auto,f_jpg/{quote(public_id, safe='/')}.jpg"


def recent(query: str = "", limit: int = 24) -> list[dict]:
    """Videos on the account, newest first, for the picker.

    The Search API rather than `api.resources`, because the picker has to be
    searchable to be usable on an account with thousands of assets, and
    because `api.resources` pages by folder rather than by recency. Failures
    return an empty list rather than raising: an empty picker beside a working
    "paste a link" box is a usable page; a 500 is not.
    """
    if not ready():
        return []
    _configure()
    expression = "resource_type:video"
    term = (query or "").strip()
    if term:
        safe = term.replace('"', " ")
        expression = f'{expression} AND (public_id:*{safe}* OR filename:*{safe}*)'
    try:
        result = (cloudinary.search.Search()
                  .expression(expression)
                  .sort_by("created_at", "desc")
                  .max_results(max(1, min(int(limit), 60)))
                  .execute())
    except Exception:                                 # noqa: BLE001
        return []
    out = []
    for res in result.get("resources", []) or []:
        pid = res.get("public_id") or ""
        width, height = int(res.get("width") or 0), int(res.get("height") or 0)
        out.append({
            "public_id": pid,
            "duration": res.get("duration"),
            "width": width,
            "height": height,
            "aspect": round(width / height, 3) if width and height else 0,
            "folder": res.get("asset_folder") or res.get("folder") or "",
            "created_at": res.get("created_at") or "",
            "poster": _poster(pid),
        })
    return out
