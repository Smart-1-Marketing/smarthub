"""Reframing a stored background asset for a new aspect ratio -- WO-CS7.

Creating a variation reuses the same footage the parent scene already has,
never a fresh Find Stock / Generate AI search: `modules/video_tools`'s own
docstring names the reason a vertical reframe of an existing clip is the
right first move, and it applies identically here. What this module adds
over that one is nothing new about the transformation itself -- the same
rule, proved against the live account: `c_fill,g_auto` for the crop, and on
**video** `g_auto` must be its own leading transformation component
(`g_auto/w_W,h_H,c_fill`) or Cloudinary rejects the URL outright. Images take
it inline. This file exists because that rule needed applying against a
*stored delivery URL* rather than a bare public_id -- Creative Studio's
scenes carry the former (`hub/storage.py::preview_url()`'s own situation,
not `modules/video_tools`'s), so the insertion point is
``url.replace("/upload/", f"/upload/{t}/", 1)``, the same pattern
`preview_url()` already uses.
"""
from __future__ import annotations


def reframe_background(url: str, width: int, height: int, *,
                       resource_type: str = "image", face: bool = False) -> str:
    """Rewrite a Cloudinary delivery URL to fill ``width`` x ``height``,
    framed on Cloudinary's own subject detection rather than a plain centre
    crop -- the whole reason a variation is not simply the parent's video
    stretched or letterboxed into a new canvas.

    Returns the URL **unchanged** when it is not a Cloudinary delivery URL,
    or when either dimension is not a positive integer: a caller handed
    this something it cannot reframe, and passing it through is the safe
    direction to be wrong in. Creatomate's own `fit: "cover"` on an
    untouched URL still fills the frame -- centre-cropped rather than
    subject-aware, which is a worse crop, never a broken one.
    """
    u = str(url or "")
    if "res.cloudinary.com" not in u or "/upload/" not in u:
        return u
    try:
        w, h = int(width), int(height)
    except (TypeError, ValueError):
        return u
    if w <= 0 or h <= 0:
        return u
    gravity = "g_auto:faces" if face else "g_auto"
    if resource_type == "video":
        transform = f"{gravity}/w_{w},h_{h},c_fill,q_auto"
    else:
        transform = f"w_{w},h_{h},c_fill,{gravity},q_auto"
    return u.replace("/upload/", f"/upload/{transform}/", 1)


def looks_like_video(url: str, asset_meta: dict | None = None) -> bool:
    """A caller-side guess at whether a scene's background is video or
    image -- `creatomate_service._element_type`'s own rule, read here
    rather than imported: that function is a private helper of a module
    this one must not reach into, and the two questions are close enough
    in shape that a shared rule would tie two files together for a handful
    of lines. `asset_meta["media"]` is trusted first, the same reason that
    function trusts it first -- it is written at the moment an asset is
    attached and cannot go stale the way a URL's own suffix can."""
    meta = asset_meta or {}
    media = meta.get("media")
    if media in ("image", "video"):
        return media == "video"
    u = str(url or "").lower().split("?")[0]
    return u.endswith((".mp4", ".mov", ".webm", ".m4v"))
