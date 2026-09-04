"""Slot layouts for the sizes a proportional resize cannot reach.

Between neighboring aspect ratios, scaling and re-anchoring is enough. Between
distant ones it is not: a square design scaled into a 728x90 gives eight
illegibly thin elements in a row, and into a 160x600 gives a postage stamp
floating in white. What a person does instead is *re-lay it out* — logo left,
copy beside it, button right — and that is a layout decision rather than an
arithmetic one.

**These are house-authored and fixed, not asked of a model.** The same choice
`SOCIAL_STRUCTURE_TEMPLATES` makes in the Commercial Builder, for the same
reason: a layout a model picks is a layout that differs between two renders of
one campaign, and the whole promise of a size set is that it is the same ad.
AI is the fallback on a frame this cannot place (`§5`), never the first pass.

Slots are fractions of the target frame, so one template serves every size in
its family — a 728x90 and a 970x250 are the same arrangement at two widths.
Each names the role that fills it; the text, color and font come from the
source object and only the geometry comes from here.

Two things a template deliberately does not do. It never invents an object for
an empty slot: a missing logo is a finding, not a gap to fill. And it has no
slot for a **disclaimer** on a leaderboard, because there is nowhere on a
728x90 to put rate copy at a legible size — so a design carrying one is
flagged rather than having it dropped to make the layout work.
"""
from __future__ import annotations

from . import roles as R

# x, y, w, h as fractions of the frame.
#
# An image always *contains* — it is scaled until it fits its slot and never
# past it. The tempting alternative is to let a photograph cover its slot the
# way a background covers the frame, and it is wrong here for a mechanical
# reason: covering means overflowing, a background may overflow because the
# canvas clips it, and a slot clips nothing unless the object carries a clip
# path. This module emits plain Fabric objects, so a covered slot is an object
# hanging outside the frame — which the guard correctly reports as clipped, on
# every frame, for ever. Letterboxing inside a slot is the cost, and it is a
# smaller one than a photograph half off the ad.
def _slot(role, x, y, w, h, align="center"):
    return {"role": role, "x": x, "y": y, "w": w, "h": h, "align": align}


LEADERBOARD = {
    "id": "leaderboard",
    "label": "Leaderboard",
    "note": "Logo left, copy beside it, button right — the arrangement a "
            "wide, short unit is read in.",
    "slots": [
        _slot(R.LOGO, 0.020, 0.150, 0.130, 0.700),
        _slot(R.HEADLINE, 0.170, 0.160, 0.480, 0.420, align="left"),
        _slot(R.SUBHEADLINE, 0.170, 0.580, 0.480, 0.260, align="left"),
        _slot(R.CTA, 0.680, 0.220, 0.300, 0.560),
    ],
}

SKYSCRAPER = {
    "id": "skyscraper",
    "label": "Skyscraper / Half Page",
    "note": "A vertical stack. The product image is the largest thing on it, "
            "because on a tall narrow unit the picture is what stops a scroll.",
    "slots": [
        _slot(R.LOGO, 0.100, 0.030, 0.800, 0.100),
        _slot(R.PRODUCT, 0.060, 0.160, 0.880, 0.340),
        _slot(R.HEADLINE, 0.080, 0.530, 0.840, 0.160),
        _slot(R.SUBHEADLINE, 0.080, 0.700, 0.840, 0.090),
        _slot(R.CTA, 0.120, 0.810, 0.760, 0.100),
        _slot(R.DISCLAIMER, 0.080, 0.930, 0.840, 0.050),
    ],
}

SQUARE_MEDIUM = {
    "id": "square_medium",
    "label": "Rectangle / Square",
    "note": "Full-bleed background, logo in a corner, copy over it, button "
            "low. The shape most display inventory is sold in.",
    "slots": [
        _slot(R.LOGO, 0.050, 0.060, 0.300, 0.130),
        _slot(R.HEADLINE, 0.050, 0.240, 0.900, 0.240),
        _slot(R.SUBHEADLINE, 0.050, 0.500, 0.900, 0.130),
        _slot(R.PRODUCT, 0.550, 0.400, 0.420, 0.400),
        _slot(R.CTA, 0.050, 0.700, 0.450, 0.160),
        _slot(R.DISCLAIMER, 0.050, 0.900, 0.900, 0.070),
    ],
}

# The two boundaries are the platform's chrome rather than taste: a story's
# top band carries the account row and its bottom band the reply control, and
# anything under either is covered on the phone whatever it looks like here.
STORY_TOP_SAFE = 0.14
STORY_BOTTOM_SAFE = 0.80

STORY_PORTRAIT = {
    "id": "story_portrait",
    "label": "Story / Portrait",
    "note": "Laid out between the platform's own top and bottom chrome. The "
            "button sits low enough to reach with a thumb and high enough "
            "not to be under the reply bar.",
    "slots": [
        _slot(R.LOGO, 0.100, 0.160, 0.340, 0.050),
        _slot(R.PRODUCT, 0.080, 0.240, 0.840, 0.260),
        _slot(R.HEADLINE, 0.080, 0.530, 0.840, 0.130),
        _slot(R.SUBHEADLINE, 0.080, 0.670, 0.840, 0.060),
        _slot(R.CTA, 0.200, 0.740, 0.600, 0.060),
        _slot(R.DISCLAIMER, 0.080, 0.820, 0.840, 0.040),
    ],
}

TEMPLATES = {t["id"]: t for t in
             (LEADERBOARD, SKYSCRAPER, SQUARE_MEDIUM, STORY_PORTRAIT)}


def for_family(family: str) -> dict | None:
    return TEMPLATES.get(family or "")


def for_ratio(width: int, height: int) -> dict:
    """The template a frame of this shape is laid out with.

    Used where the target is a custom size with no declared family. The
    boundaries are wide on purpose — the point is which of four arrangements
    the shape is closest to, and a size sitting near a boundary reads fine
    either way.
    """
    if not height:
        return SQUARE_MEDIUM
    ratio = width / height
    if ratio >= 2.2:
        return LEADERBOARD
    # Narrower than about 1:2 is a skyscraper — a column beside an article.
    # Between that and about 4:5 is a story, which is a whole screen and is
    # laid out around the platform's own chrome rather than as a column.
    if ratio <= 0.52:
        return SKYSCRAPER
    if ratio <= 0.85:
        return STORY_PORTRAIT
    return SQUARE_MEDIUM


def slot_roles(template: dict) -> list[str]:
    return [s["role"] for s in template.get("slots", [])]
