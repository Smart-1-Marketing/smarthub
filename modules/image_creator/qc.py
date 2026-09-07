"""QC for graphics built in Image Creator — the same block/advisory pattern
Commercial Builder and Magic Resize already use, so a rep sees one QC
experience across every Hub creative tool rather than three.

Two kinds of check, kept apart on every finding, for the reason
``modules/magic_resize/qc.py`` already gives at length: a **kit** check is the
S1M CREATIVE SPEC KIT or a platform's own published ceiling, judged by
``hub.creative_specs.check()`` rather than by a limit restated here — a fourth
copy of a number that already drifted once (Half Page was enforced at 150 KB
against a published 250 KB) is the one that goes stale next. A **house**
check is ours and says so: "our tool thinks this is too small" is an opinion,
not a rule, and a client can talk us out of an opinion.

Every finding is a block or an advisory, never a bare pass/fail — a check
that refuses the correct thing is a check somebody switches off, which is
why only dimension mismatches, the kit's own weight ceiling and an
over-length animation are hard blocks here. Everything else — logo presence,
the safe-margin, contrast, the white-background border — is advisory,
because none of those is a rule any ad network publishes.
"""
from __future__ import annotations

try:                                                    # pragma: no cover
    from hub import creative_specs
except Exception:                                       # noqa: BLE001
    creative_specs = None

try:                                                    # pragma: no cover
    from hub.contrast import contrast_ratio
except Exception:                                       # noqa: BLE001
    contrast_ratio = None

try:                                                    # pragma: no cover
    from modules.commercial_builder.config import SOCIAL_RULES
except Exception:                                       # noqa: BLE001
    SOCIAL_RULES = {"safe_area_pct": 14}

from . import animation as _animation

FAIL, WARN = "fail", "warn"

# Canvas presets whose size AND weight ceiling are the S1M spec kit's own,
# read from hub.creative_specs at check time — never restated as a number
# here, so a kit update (or a correction to one, the way Half Page's 150 KB
# was corrected to the published 250 KB) reaches this check with no redeploy
# conversation. Keyed on the CANVAS_PRESETS "key" in app.py.
GDN_UNITS: dict[str, str] = {
    "mrec": "medium_rectangle",
    "leaderboard": "leaderboard",
    "mobile-banner": "mobile_banner_320",
    "halfpage": "half_page",
    "billboard": "rising_star",
}

# A size the kit weighs nothing for — social placements and anything custom.
# Advisory only, and named as ours: refusing a size the kit says nothing
# about is a check somebody switches off, and it would cost the findings
# that ARE worth keeping along with it.
ADVISORY_MAX_BYTES = 150 * 1024
ADVISORY_SOURCE = "house — the S1M spec kit publishes no weight ceiling for this size"

# Google Ads / GDN: an animated display creative must stop within 30 seconds,
# loops included. https://support.google.com/adspolicy — transcribed rather
# than fetched live, the same reason hub/creative_specs.py transcribes the
# rest of the kit: a live-pulled number changes what a check says with no
# diff to point at.
ANIMATION_MAX_SECONDS = 30.0
ANIMATION_SOURCE = "Google Ads published animated-display ceiling (30s, loops included)"

# WCAG AA, normal text. Large text (roughly 18px bold+/24px regular+ on a
# typical ad) is allowed a looser 3:1, so the check reads the object's own
# font size rather than a single number.
_AA_NORMAL = 4.5
_AA_LARGE = 3.0
_LARGE_TEXT_PX = 24  # ~18pt at a typical 96dpi canvas — WCAG's "large text"

# "all channels at or above this" counts as near-white for the border advice.
WHITE_THRESHOLD = 245


def finding(code: str, level: str, message: str, *, source: str = "house") -> dict:
    return {"code": code, "level": level, "message": message, "source": source}


# ---------------------------------------------------------------------------
# 1 & 2 — dimension match and file size
# ---------------------------------------------------------------------------
def check_dimensions_and_weight(preset: dict, *, width: int, height: int,
                                size_bytes: int, fmt: str) -> list[dict]:
    """Exact pixel match to the selected preset, and the weight ceiling.

    A GDN/IAB size (``preset["unit"]`` set) is judged by the spec kit, which
    carries both rules for that unit already — one call answers both
    questions with the kit's own numbers rather than two restated ones.
    Anything else — Social or Custom — is judged directly: dimensions must
    still match exactly (no off-by-one tolerance), and weight is advisory
    against the house ceiling, since the kit weighs nothing for these.
    """
    out: list[dict] = []
    unit_id = preset.get("unit") or ""

    if unit_id and creative_specs is not None:
        verdict = creative_specs.check(width=width, height=height,
                                       size_bytes=size_bytes, fmt=fmt,
                                       unit_id=unit_id)
        for c in verdict.get("checks") or []:
            if c.get("label") not in ("Dimensions", "File size"):
                continue
            if c.get("status") == "fail":
                code = "dimension_mismatch" if c["label"] == "Dimensions" else "file_too_large"
                out.append(finding(code, FAIL, c.get("detail", ""), source="kit"))
        return out

    # No kit unit: dimensions are still exact (skip entirely for Custom,
    # which carries no fixed size to check against).
    preset_w, preset_h = int(preset.get("w") or 0), int(preset.get("h") or 0)
    if preset_w and preset_h and (width, height) != (preset_w, preset_h):
        out.append(finding(
            "dimension_mismatch", FAIL,
            f"{width}x{height} does not match the selected {preset_w}x{preset_h} "
            f"size — exports must be exact.", source="house"))

    if size_bytes and size_bytes > ADVISORY_MAX_BYTES:
        out.append(finding(
            "file_too_large", WARN,
            f"{size_bytes / 1024:.0f} KB is over our {ADVISORY_MAX_BYTES // 1024} KB "
            "guideline for this placement. Most ad networks are more lenient here "
            "than on IAB/GDN standard sizes, but a smaller file loads faster.",
            source=ADVISORY_SOURCE))
    return out


# ---------------------------------------------------------------------------
# 3 — animation duration
# ---------------------------------------------------------------------------
def check_animation_duration(objects: list[dict]) -> list[dict]:
    """Total animated runtime against Google's published 30s ceiling.

    Reads the same ``s1anim`` timeline metadata the animated GIF export
    walks (``modules.image_creator.animation``), so this can run — and
    block — before an export is ever attempted. A design with no entrance
    animation set on anything produces no finding at all: this is a check on
    an animated export, not a reason to warn about a still image.
    """
    total_ms = _animation.timeline_duration_ms(objects)
    if total_ms is None:
        return []
    seconds = total_ms / 1000.0
    if seconds > ANIMATION_MAX_SECONDS:
        return [finding(
            "animation_too_long", FAIL,
            f"The animation runs {seconds:.1f}s — over the "
            f"{ANIMATION_MAX_SECONDS:.0f}s Google Ads allows for an animated "
            "display creative, loops included.", source=ANIMATION_SOURCE)]
    return []


# ---------------------------------------------------------------------------
# 4 — brand logo present
# ---------------------------------------------------------------------------
def _role(obj: dict) -> str:
    meta = obj.get("s1meta") or {}
    return str(meta.get("role") or "").strip().lower()


def check_logo_present(objects: list[dict]) -> list[dict]:
    """Advisory: is anything on the canvas tagged as the client's logo.

    Tagged rather than guessed at — the same rule ``modules/magic_resize/
    roles.py`` states for its own required-roles check: a role is set when an
    object is added through the Logos panel (or by hand, from the Properties
    panel's "This is the logo" toggle), never inferred from what an object
    looks like. Guessing "the small image in the corner is probably the
    logo" is right most of the time and silently wrong on the one ad where it
    matters.
    """
    if any(_role(o) == "logo" for o in objects or []):
        return []
    return [finding(
        "missing_logo", WARN,
        "No object on this canvas is tagged as the client's logo.")]


# ---------------------------------------------------------------------------
# 5 — text-safe margin
# ---------------------------------------------------------------------------
def _bbox(obj: dict) -> tuple[float, float, float, float] | None:
    """(x, y, w, h) of an object's axis-aligned bounding box.

    Ignores rotation — fine for an advisory margin check, where the cost of
    a false negative on a rotated object is a missed warning rather than a
    wrong block.
    """
    try:
        left = float(obj.get("left") or 0)
        top = float(obj.get("top") or 0)
        w = float(obj.get("width") or 0) * float(obj.get("scaleX") or 1)
        h = float(obj.get("height") or 0) * float(obj.get("scaleY") or 1)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return left, top, w, h


def check_text_safe_margin(objects: list[dict], *, width: int, height: int) -> list[dict]:
    """Advisory: text kept clear of the top/bottom safe zone.

    The margin is the S1M kit's own — 14% of the frame, the figure it
    publishes for Stories/Reels and the one ``modules/commercial_builder``
    already carries as ``SOCIAL_RULES["safe_area_pct"]`` — read from there
    rather than restated, applied once to the whole frame instead of per
    scene the way a static graphic needs it.
    """
    if not width or not height:
        return []
    pct = float(SOCIAL_RULES.get("safe_area_pct", 14)) / 100.0
    band = height * pct
    out: list[dict] = []
    for obj in objects or []:
        if "text" not in str(obj.get("type") or "").lower():
            continue
        box = _bbox(obj)
        if not box:
            continue
        _x, y, _w, h = box
        if y < band or (y + h) > (height - band):
            label = obj.get("name") or "Text"
            out.append(finding(
                "text_in_safe_margin", WARN,
                f'"{label}" sits within {SOCIAL_RULES.get("safe_area_pct", 14):.0f}% '
                "of the top or bottom edge — some placements crop or overlay that area.",
                source="kit"))
    return out


# ---------------------------------------------------------------------------
# 6 — contrast / legibility
# ---------------------------------------------------------------------------
def _overlaps(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


def _canvas_background_hex(canvas: dict) -> str:
    bg = canvas.get("background") or canvas.get("backgroundColor") or ""
    return bg if isinstance(bg, str) and bg.startswith("#") else ""


def check_contrast(canvas: dict) -> list[dict]:
    """Advisory: a text object's fill against what's plainly behind it.

    Only judged where the background is knowable without decoding a bitmap:
    the canvas's own solid background color, or a solid-fill background
    shape (``s1kind == "background"`` with a plain ``fill``). Text sitting
    over a photograph is **not measured** rather than judged against
    whatever the photo's average color happens to be — a guess there is
    worse than silence, the rule this codebase applies everywhere a source
    cannot actually answer the question.
    """
    if contrast_ratio is None:
        return []
    objects = canvas.get("objects") or []
    bg_objects = [o for o in objects
                  if str(o.get("s1kind") or "") == "background"
                  and str(o.get("type") or "").lower() != "image"
                  and isinstance(o.get("fill"), str) and o.get("fill", "").startswith("#")]
    canvas_bg = _canvas_background_hex(canvas)
    has_bg_image = any(str(o.get("s1kind") or "") == "background"
                       and str(o.get("type") or "").lower() == "image"
                       for o in objects)

    out: list[dict] = []
    for obj in objects:
        if "text" not in str(obj.get("type") or "").lower():
            continue
        fill = obj.get("fill")
        if not isinstance(fill, str) or not fill.startswith("#"):
            continue
        box = _bbox(obj)
        behind_hex = ""
        if box:
            covering = next((bo for bo in bg_objects if _bbox(bo) and _overlaps(box, _bbox(bo))), None)
            if covering:
                behind_hex = covering.get("fill", "")
        if not behind_hex:
            if has_bg_image:
                continue  # not measured — behind a photo, no pixel to sample
            behind_hex = canvas_bg
        if not behind_hex:
            continue
        ratio = contrast_ratio(fill, behind_hex)
        if ratio is None:
            continue
        floor = _AA_LARGE if float(obj.get("fontSize") or 0) >= _LARGE_TEXT_PX else _AA_NORMAL
        if ratio < floor:
            label = obj.get("name") or "Text"
            out.append(finding(
                "low_contrast", WARN,
                f'"{label}" is {ratio:.1f}:1 against its background — WCAG AA asks '
                f"for {floor:.1f}:1. Some readers will struggle with it.",
                source="house"))
    return out


# ---------------------------------------------------------------------------
# 7 — white-background border
# ---------------------------------------------------------------------------
def _is_near_white(hexs: str) -> bool:
    h = (hexs or "").lstrip("#")
    if len(h) != 6:
        return False
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return False
    return r >= WHITE_THRESHOLD and g >= WHITE_THRESHOLD and b >= WHITE_THRESHOLD


def check_white_background_border(canvas: dict) -> list[dict]:
    """Advisory: recommend a 1px border when the background is white.

    A common ad-network legibility convention — creative has to be
    distinguishable from the page it sits on — rather than a rule any
    network refuses a file over, so it is a recommendation and never a
    block, the same way ``hub/creative_specs.py`` carries the identical
    guidance in its notes rather than as an enforced check.
    """
    objects = canvas.get("objects") or []
    bg_rect = next((o for o in objects if str(o.get("s1kind") or "") == "background"
                    and str(o.get("type") or "").lower() != "image"), None)
    hexs = (bg_rect.get("fill") if bg_rect else None) or _canvas_background_hex(canvas)
    if isinstance(hexs, str) and _is_near_white(hexs):
        return [finding(
            "white_background_border", WARN,
            "The background is white or near-white — consider a 1px contrasting "
            "border so the ad reads as a distinct unit against the page it runs on.")]
    return []


# ---------------------------------------------------------------------------
# The whole run
# ---------------------------------------------------------------------------
def run(preset: dict, canvas: dict, *, size_bytes: int = 0, fmt: str = "png") -> dict:
    """Every check, against one exported (or about-to-be-exported) canvas."""
    objects = canvas.get("objects") or []
    width = int(canvas.get("width") or preset.get("w") or 0)
    height = int(canvas.get("height") or preset.get("h") or 0)

    findings: list[dict] = []
    findings += check_dimensions_and_weight(preset, width=width, height=height,
                                            size_bytes=size_bytes, fmt=fmt)
    findings += check_animation_duration(objects)
    findings += check_logo_present(objects)
    findings += check_text_safe_margin(objects, width=width, height=height)
    findings += check_contrast(canvas)
    findings += check_white_background_border(canvas)

    return {"result": verdict(findings), "findings": findings}


def verdict(findings: list[dict]) -> str:
    if any(f.get("level") == FAIL for f in findings):
        return "fail"
    if any(f.get("level") == WARN for f in findings):
        return "warn"
    return "pass"
