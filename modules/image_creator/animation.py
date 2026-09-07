"""Animated GIF export — the timeline math, and Pillow to assemble it.

An "entrance" is a per-object property (``s1anim``: ``type``, ``delayMs``,
``durationMs``), set from the Properties panel and carried through the same
custom-property list Fabric already serialises (``name``, ``s1kind``,
``s1meta``). Nothing here re-renders a Fabric canvas: the browser is where
that canvas actually exists — fonts, groups, filters, cropped images — and a
second renderer in Python could disagree with what a rep is looking at on
screen. What the browser sends is a sequence of already-rendered frame
images (one per timestamp, produced by interpolating each object's opacity
and position and calling Fabric's own ``toDataURL()``); this module works out
*how many* frames and *what interval*, and turns the finished sequence into
one GIF file.

That split is also why this is not a screen recording: nothing here captures
what a monitor shows, and the frame count is bounded by the timeline rather
than by a fixed clip length.
"""
from __future__ import annotations

import base64
import io

FRAME_INTERVAL_MS = 100  # 10fps — see qc.py: kept modest so an ordinary
                         # display-ad frame count stays inside the 150KB/
                         # animation-weight budget without every ad needing
                         # a custom frame rate.

ENTRANCES = ("none", "fade-in", "slide-in-left", "slide-in-right", "slide-up")


def timeline_duration_ms(objects: list[dict]) -> int | None:
    """Total animated runtime in ms, or ``None`` if nothing on the canvas
    carries an entrance animation.

    ``delayMs + durationMs`` per object, taking the longest — the moment the
    slowest-arriving object has finished settling is when the loop is over.
    """
    total = 0
    found = False
    for obj in objects or []:
        anim = obj.get("s1anim") or {}
        kind = str(anim.get("type") or "none")
        if kind not in ENTRANCES or kind == "none":
            continue
        found = True
        try:
            delay = max(0, int(anim.get("delayMs") or 0))
            dur = max(0, int(anim.get("durationMs") or 0))
        except (TypeError, ValueError):
            delay, dur = 0, 0
        total = max(total, delay + dur)
    return total if found else None


def frame_count(total_ms: int, interval_ms: int = FRAME_INTERVAL_MS) -> int:
    """How many frames a timeline of this length needs at this interval.

    Always at least 1 — a design with no motion still exports as a single
    frame if somebody asks for one, rather than raising over an empty range.
    """
    interval_ms = max(1, int(interval_ms or FRAME_INTERVAL_MS))
    if total_ms <= 0:
        return 1
    return max(1, int(total_ms // interval_ms) + 1)


def frame_times(total_ms: int, interval_ms: int = FRAME_INTERVAL_MS) -> list[int]:
    """The timestamps (ms from the start) each frame should be captured at."""
    n = frame_count(total_ms, interval_ms)
    interval_ms = max(1, int(interval_ms or FRAME_INTERVAL_MS))
    return [min(i * interval_ms, max(total_ms, 0)) for i in range(n)]


MAX_FRAMES = 400  # ~40s at the default interval — well past the 30s Google
                  # ceiling; a request over this is refused rather than
                  # spending minutes assembling a GIF nobody could ship.


def _decode_frame(data_url_or_b64: str) -> bytes:
    s = str(data_url_or_b64 or "")
    if s.startswith("data:"):
        _, _, s = s.partition(",")
    return base64.b64decode(s)


def assemble_gif(frames: list[str], *, frame_ms: int = FRAME_INTERVAL_MS,
                 loop: int = 0) -> bytes:
    """Turn a list of already-rendered frame images (data URLs or bare
    base64) into one animated GIF.

    GIF carries no alpha blending, so a transparent frame is flattened onto
    white first — the same fallback ``/api/export/optimize`` already uses for
    a JPEG export of a transparent canvas.
    """
    from PIL import Image

    if not frames:
        raise ValueError("No frames to assemble.")
    if len(frames) > MAX_FRAMES:
        raise ValueError(f"That animation would need {len(frames)} frames — "
                         f"more than the {MAX_FRAMES} this export allows.")

    flat = []
    for raw in frames:
        data = _decode_frame(raw)
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            flat.append(bg)

    buf = io.BytesIO()
    flat[0].save(buf, format="GIF", save_all=True, append_images=flat[1:],
                duration=max(20, int(frame_ms or FRAME_INTERVAL_MS)),
                loop=max(0, int(loop or 0)), optimize=True)
    return buf.getvalue()
