"""Between Fabric's canvas JSON and the boxes the engine reasons about.

The engine is arithmetic over boxes and knows nothing about Fabric; the editor
is Fabric and knows nothing about roles. This is the seam, and keeping it a
seam is what makes the engine testable without a browser in the room.

**A resize comes back as Fabric objects, not as a picture.** Every object
keeps its own `type`, its `text`, its fill and its intrinsic `width`/`height`,
and only `left`, `top`, `scaleX` and `scaleY` move — so a frame the engine
produced opens in the editor with every object still selectable. A flattened
image would be a resize you cannot fix, which on the frames that need fixing
is the whole of the value.

**Origin is read, never assumed.** Fabric positions from whichever corner or
center an object was given, and reading `left` as a left edge on an object
whose `originX` is `center` puts it half its own width out — which on a logo
in a corner is the difference between the corner and off the frame.
"""
from __future__ import annotations

from typing import Any

from . import roles as R

TEXT_TYPES = ("text", "textbox", "i-text", "itext")
IMAGE_TYPES = ("image",)
GROUP_TYPES = ("group", "activeselection")


def kind_of(fabric_type: str) -> str:
    t = (fabric_type or "").lower()
    if t in TEXT_TYPES:
        return "text"
    if t in IMAGE_TYPES:
        return "image"
    if t in GROUP_TYPES:
        return "group"
    return "shape"


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _box(obj: dict) -> tuple[float, float, float, float]:
    w = _num(obj.get("width")) * _num(obj.get("scaleX"), 1.0)
    h = _num(obj.get("height")) * _num(obj.get("scaleY"), 1.0)
    left, top = _num(obj.get("left")), _num(obj.get("top"))
    ox = (obj.get("originX") or "left").lower()
    oy = (obj.get("originY") or "top").lower()
    if ox == "center":
        left -= w / 2
    elif ox == "right":
        left -= w
    if oy == "center":
        top -= h / 2
    elif oy == "bottom":
        top -= h
    return left, top, w, h


def to_frame(canvas: dict, *, width: int = 0, height: int = 0,
             role_map: dict[str, str] | None = None) -> dict:
    """Fabric's canvas JSON as the engine's source design."""
    canvas = canvas or {}
    roles = role_map or {}
    objects: list[dict] = []
    for i, obj in enumerate(canvas.get("objects") or []):
        if not isinstance(obj, dict):
            continue
        oid = str(obj.get("id") or obj.get("s1Id") or f"o{i}")
        x, y, w, h = _box(obj)
        role = roles.get(oid) or obj.get("s1Role") or ""
        if not R.is_role(role):
            role = ""
        objects.append({
            "id": oid,
            "role": role,
            "kind": kind_of(obj.get("type", "")),
            "x": x, "y": y, "w": w, "h": h,
            "text": obj.get("text", "") or "",
            "fontSize": _num(obj.get("fontSize")) or None,
            "fill": obj.get("fill", ""),
            # The object as it was. The engine never reads it and the editor
            # needs every field of it back, so it travels whole rather than
            # being rebuilt from the fields this module happens to know.
            "fabric": obj,
        })
    return {
        "width": int(width or _num(canvas.get("width"))),
        "height": int(height or _num(canvas.get("height"))),
        "family": "",
        "objects": objects,
    }


def to_fabric(frame: dict) -> dict:
    """A resized frame back as a Fabric canvas — objects, not a picture."""
    objects: list[dict] = []
    for obj in frame.get("objects") or []:
        base = dict(obj.get("fabric") or {})
        base.setdefault("type", {"text": "textbox", "image": "image",
                                 "group": "group"}.get(obj.get("kind", ""),
                                                       "rect"))
        intrinsic_w = _num(base.get("width")) or max(1.0, _num(obj.get("w"), 1.0))
        intrinsic_h = _num(base.get("height")) or max(1.0, _num(obj.get("h"), 1.0))
        base.update({
            "left": round(_num(obj.get("x")), 2),
            "top": round(_num(obj.get("y")), 2),
            "originX": "left", "originY": "top",
            "width": intrinsic_w, "height": intrinsic_h,
            "scaleX": round(_num(obj.get("w")) / intrinsic_w, 5),
            "scaleY": round(_num(obj.get("h")) / intrinsic_h, 5),
            "id": obj.get("id", ""),
            "s1Role": obj.get("role", ""),
        })
        if obj.get("kind") == "text":
            base["text"] = obj.get("text", "")
            if obj.get("fontSize"):
                base["fontSize"] = obj["fontSize"]
                # Type is sized in points here, not stretched: a textbox whose
                # scale carries its size renders at the wrong weight and stops
                # wrapping where it says it wraps.
                base["scaleX"] = 1
                base["scaleY"] = 1
                base["width"] = round(_num(obj.get("w")), 2)
                base["height"] = round(_num(obj.get("h")), 2)
        objects.append(base)
    return {"version": "5.3.0", "width": frame.get("width"),
            "height": frame.get("height"), "objects": objects}
