"""Getting a frame out, at the weight the platform will accept.

The canvas is rendered in the browser — Fabric is the editing engine and the
only thing that can draw a Fabric frame is Fabric — so what arrives here is
finished bytes. What this module does is judge them and, where they are over,
bring them under without changing the one thing that must not change.

**The dimensions are the unit.** A 300x250 that has been shrunk to 280x233 to
save weight is not a Medium Rectangle any more; it is a file that will be
refused for a reason nobody looking at it would guess. So the ladder is
quality and format only, and `max_edge` is pinned above the frame's own longest
side precisely so `hub.images.optimise` cannot resize on the way past.

**The ladder stops before it makes the ad ugly.** `QUALITY_FLOOR` is where a
photograph starts to visibly band, and a file that cannot get under its
ceiling above that floor is **reported** rather than shipped soft — the
answer `image-budget.ts` arrived at on the other side of this Hub. A degraded
ad delivered quietly is worse than a size somebody has to redraw, because
nobody re-opens a delivery that reported success.

**A size with no published ceiling is not squeezed to fit an invented one.**
`qc.weight()` answers *not measured* for those, and the export goes as it was
rendered with the reason on it.
"""
from __future__ import annotations

import io
import zipfile
from typing import Any

from . import qc
from . import sizes as S

try:                                                   # pragma: no cover
    from hub import images as hub_images
except Exception:                                      # noqa: BLE001
    hub_images = None                                  # type: ignore[assignment]

# Tried in order. PNG first only where the frame was rendered as one — a flat
# graphic is smaller and sharper as a PNG, and a photograph is not.
QUALITY_LADDER = (92, 86, 80, 74, 68, 62)
QUALITY_FLOOR = 62

MAX_UPLOAD_BYTES = 24 * 1024 * 1024


def _ceiling(size_id: str) -> int | None:
    verdict = qc.weight(size_id, size_bytes=0, fmt="jpg")
    if not verdict.get("measured"):
        return None
    spec = S.get(size_id) or {}
    if spec.get("max_bytes"):
        return int(spec["max_bytes"])
    unit_id = spec.get("unit") or ""
    try:
        from hub import creative_specs
        unit = (getattr(creative_specs, "BY_ID", {}) or {}).get(unit_id) or {}
        return int(unit.get("max_bytes") or 0) or None
    except Exception:                                  # noqa: BLE001
        return None


def prepare(size_id: str, data: bytes, *, fmt: str = "png") -> dict:
    """One frame's bytes, brought under its ceiling or reported as over.

    Never raises. An export that fails must cost the frame and not the batch,
    and the caller has already spent the render.
    """
    spec = S.get(size_id)
    result: dict[str, Any] = {
        "size_id": size_id, "fmt": (fmt or "png").lower(),
        "bytes": len(data or b""), "original_bytes": len(data or b""),
        "data": data or b"", "recompressed": False, "quality": None,
    }
    if not spec:
        result["error"] = f"No size is declared as {size_id}."
        return result
    if not data:
        result["error"] = "The browser sent no image for this frame."
        return result
    if len(data) > MAX_UPLOAD_BYTES:
        result["error"] = "That render is too large to process."
        return result

    ceiling = _ceiling(size_id)
    result["ceiling"] = ceiling
    if ceiling is None:
        result["measured"] = False
        result["note"] = ("No published ceiling applies to this size, so the "
                          "file goes as rendered.")
        return result

    result["measured"] = True
    if len(data) <= ceiling:
        result["ok"] = True
        return result

    if hub_images is None:                             # pragma: no cover
        result["ok"] = False
        result["error"] = "Image tooling is unavailable, so nothing was compressed."
        return result

    # Pinned above the frame's own longest side so the shared optimizer cannot
    # resize: the dimensions are the unit.
    edge = max(int(spec["w"]), int(spec["h"])) + 1
    best: bytes | None = None
    best_q = None
    for quality in QUALITY_LADDER:
        try:
            out = hub_images.optimise(data, max_edge=edge, fmt="JPEG",
                                      quality=quality)
        except Exception as exc:                       # noqa: BLE001
            result["ok"] = False
            result["error"] = f"This frame could not be compressed: {exc}"
            return result
        best, best_q = out.data, quality
        if len(out.data) <= ceiling:
            result.update({"data": out.data, "bytes": len(out.data),
                           "fmt": "jpg", "recompressed": True,
                           "quality": quality, "ok": True})
            return result

    result.update({"data": best or data, "bytes": len(best or data),
                   "fmt": "jpg" if best else result["fmt"],
                   "recompressed": bool(best), "quality": best_q, "ok": False,
                   "error": (
                       f"This frame is {len(best or data) / 1024:.0f} KB at "
                       f"quality {QUALITY_FLOOR}, against a "
                       f"{ceiling / 1024:.0f} KB ceiling. Compressing further "
                       f"would show. Simplify the frame — a flatter "
                       f"background is usually what does it — rather than "
                       f"delivering it soft.")})
    return result


def filename_for(size_id: str, fmt: str) -> str:
    spec = S.get(size_id) or {}
    w, h = spec.get("w"), spec.get("h")
    stem = f"{w}x{h}" if w and h else size_id
    return f"{stem}.{(fmt or 'png').lower().lstrip('.')}"


def bundle(frames: list[dict]) -> tuple[bytes, list[dict]]:
    """A zip of prepared frames, and a row per frame saying what happened.

    A frame that could not be brought under its ceiling is **left out and
    named** rather than dropped quietly or included soft — the rule
    `deliverProject` already applies to a QA-failing size: a folder with
    seven files where there should be eight is a difference ad operations
    assumes they caused.
    """
    report: list[dict] = []
    buf = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for frame in frames:
            row = {k: v for k, v in frame.items() if k != "data"}
            if frame.get("ok") is False:
                row["included"] = False
                report.append(row)
                continue
            name = filename_for(frame["size_id"], frame.get("fmt", "png"))
            # Two frames cannot share a name — a zip keeps only the last, and
            # the missing one looks like a size nobody built.
            base, dot, ext = name.rpartition(".")
            n = 2
            while name in used:
                name = f"{base}-{n}.{ext}"
                n += 1
            used.add(name)
            zf.writestr(name, frame.get("data") or b"")
            row["included"] = True
            row["filename"] = name
            report.append(row)
        notes = [f"{r['size_id']}: {r.get('error','')}"
                 for r in report if not r.get("included")]
        if notes:
            zf.writestr("NOT-IN-THIS-ZIP.txt",
                        ("These sizes are not in this pack because they could "
                         "not be brought under their published file-size "
                         "ceiling without visibly degrading:\n\n"
                         + "\n".join(notes) + "\n"))
    return buf.getvalue(), report
