"""One design, many sizes — and what the engine refuses to guess at.

Two tiers, because two things are being asked. Between neighboring aspect
ratios the answer is arithmetic: scale the design and put each object back
against the edge it was against. Between distant ones the answer is a layout
decision, and `templates_layout.py` holds those.

The rules that matter are the ones about not being confidently wrong:

**An object re-anchors, it does not have its coordinates scaled.** Scaling raw
x/y drifts everything toward the origin as a frame shrinks, so a logo pinned
10px from the left of a 970px design lands 3px from the left of a 320px one and
a right-hand button walks into the middle. Each object is measured against the
edge it is nearest and put back the same distance from that edge, in the new
frame's own terms.

**A frame the engine is not sure about is `needs_review`, never `auto`.** The
collision guard runs after placement, and an overlap or an object past the
frame edge marks the frame and *names the objects*. A resize that produced a
broken ad and reported success is worse than one that reported it could not.

**Nothing is dropped in silence.** A role the target template has no slot for
is recorded in `unplaced` with the reason, and if it carries copy the frame is
flagged: copy that vanishes to make a layout fit is the client's offer going
missing, and it is invisible from every screen afterwards.

**Nothing is invented for an empty slot.** A template with a headline slot and
no headline object leaves the slot empty and says so. Filling it with the next
nearest thing is how a disclaimer ends up where the headline goes.
"""
from __future__ import annotations

import math

from . import qc
from . import roles as R
from . import templates_layout as L

AUTO = "auto"
NEEDS_REVIEW = "needs_review"
AI = "ai"
EDITED = "edited"

# Past this much change in aspect ratio, scaling stops being the right answer
# and the frame is laid out from a template instead. 1.5 is the build plan's
# figure and it is ours — no published rule says where re-flow should start.
RATIO_SHIFT_LIMIT = 1.5

# How much two boxes may overlap before it is a collision rather than a
# rounding artefact, as a fraction of the smaller box. A hairline touch
# between a headline and a rule beneath it is not a defect, and reporting one
# is how a guard stops being read.
OVERLAP_TOLERANCE = 0.02

_EDGE_CENTER_BAND = 0.06        # within 6% of the frame's center reads as centered


def ratio_shift(sw: int, sh: int, tw: int, th: int) -> float:
    """How far the shape changes, as a factor. 1.0 is the same shape."""
    if not (sw and sh and tw and th):
        return math.inf
    a, b = sw / sh, tw / th
    if a <= 0 or b <= 0:
        return math.inf
    return max(a / b, b / a)


def pick_tier(source: dict, target: dict) -> tuple[str, str]:
    """Which tier places this frame, and the reason — the reason is shown.

    Same family is enough on its own: a 970x250 billboard and a 728x90
    leaderboard shift by more than the ratio limit and are plainly the same
    arrangement, which is what a declared family is for. A custom size has no
    family, so the ratio decides.
    """
    sw, sh = int(source.get("width") or 0), int(source.get("height") or 0)
    tw, th = int(target.get("w") or 0), int(target.get("h") or 0)
    shift = ratio_shift(sw, sh, tw, th)

    src_family = source.get("family") or ""
    dst_family = target.get("family") or ""
    if src_family and src_family == dst_family:
        return "anchor", (f"the same {src_family.replace('_', ' ')} shape as "
                          f"the design, so it is scaled rather than re-laid out")
    if shift <= RATIO_SHIFT_LIMIT:
        return "anchor", (f"the shape changes by {shift:.2f}x, inside the "
                          f"{RATIO_SHIFT_LIMIT}x we scale within")
    return "reflow", (f"the shape changes by {shift:.2f}x, so it is laid out "
                      f"from the {L.for_ratio(tw, th)['label']} template "
                      f"rather than scaled")


# --------------------------------------------------------------------------
# Tier 1 — re-anchor
# --------------------------------------------------------------------------

def _anchor_of(obj: dict, w: int, h: int) -> tuple[str, str]:
    cx, cy = obj["x"] + obj["w"] / 2, obj["y"] + obj["h"] / 2
    if abs(cx - w / 2) / max(1, w) <= _EDGE_CENTER_BAND:
        ax = "center"
    else:
        ax = "left" if obj["x"] <= (w - (obj["x"] + obj["w"])) else "right"
    if abs(cy - h / 2) / max(1, h) <= _EDGE_CENTER_BAND:
        ay = "middle"
    else:
        ay = "top" if obj["y"] <= (h - (obj["y"] + obj["h"])) else "bottom"
    return ax, ay


def _cover(obj: dict, tw: int, th: int) -> dict:
    """A background fills the frame, keeping its own aspect, centered."""
    ow, oh = max(1.0, obj["w"]), max(1.0, obj["h"])
    scale = max(tw / ow, th / oh)
    w, h = ow * scale, oh * scale
    out = dict(obj)
    out.update({"x": (tw - w) / 2, "y": (th - h) / 2, "w": w, "h": h,
                "scale": scale})
    return out


def _reanchor(source: dict, tw: int, th: int) -> list[dict]:
    sw, sh = int(source["width"]), int(source["height"])
    sx, sy = tw / sw, th / sh
    # Uniform, and the smaller of the two: an object scaled by each axis
    # separately is a distorted logo, and the larger factor is how a headline
    # that fitted its frame stops fitting the new one.
    scale = min(sx, sy)

    placed: list[dict] = []
    for obj in source.get("objects") or []:
        if obj.get("role") == R.BACKGROUND:
            placed.append(_cover(obj, tw, th))
            continue
        ax, ay = _anchor_of(obj, sw, sh)
        w, h = obj["w"] * scale, obj["h"] * scale

        # Margins travel on the axis ratio rather than the uniform scale, so
        # the design spreads into a wider frame instead of huddling in the
        # middle of it while every object keeps its own proportions.
        if ax == "left":
            x = obj["x"] * sx
        elif ax == "right":
            x = tw - (sw - (obj["x"] + obj["w"])) * sx - w
        else:
            x = (tw - w) / 2
        if ay == "top":
            y = obj["y"] * sy
        elif ay == "bottom":
            y = th - (sh - (obj["y"] + obj["h"])) * sy - h
        else:
            y = (th - h) / 2

        out = dict(obj)
        out.update({"x": x, "y": y, "w": w, "h": h, "scale": scale,
                    "anchor": f"{ay}-{ax}"})
        if obj.get("fontSize"):
            out["fontSize"] = round(float(obj["fontSize"]) * scale, 2)
        placed.append(out)
    return placed


# --------------------------------------------------------------------------
# Tier 2 — role slots
# --------------------------------------------------------------------------

def fit_font(text: str, slot_w: float, slot_h: float) -> float:
    """The largest type that fits a slot, measured rather than assumed.

    An estimate — the real advance width needs the font — so it is deliberately
    conservative: coming back a little small costs a slightly airy layout,
    coming back large costs clipped copy, which is the thing QC then reports on
    a frame nobody looks at.
    """
    lines = [ln for ln in str(text or "").split("\n")] or [""]
    by_height = (slot_h / max(1, len(lines))) * 0.72
    longest = max((len(ln) for ln in lines), default=1) or 1
    by_width = slot_w / (longest * 0.52)
    return max(1.0, round(min(by_height, by_width), 2))


def _place_in_slot(obj: dict, slot: dict, tw: int, th: int) -> dict:
    sx, sy = slot["x"] * tw, slot["y"] * th
    sw, sh = slot["w"] * tw, slot["h"] * th
    out = dict(obj)

    if obj.get("kind") == "text":
        out.update({"x": sx, "y": sy, "w": sw, "h": sh,
                    "fontSize": fit_font(obj.get("text", ""), sw, sh),
                    "align": slot.get("align", "center")})
        return out

    # Contain, always — see `_slot`: a slot clips nothing, so covering one
    # means an object hanging off the frame.
    ow, oh = max(1.0, obj["w"]), max(1.0, obj["h"])
    scale = min(sw / ow, sh / oh)
    w, h = ow * scale, oh * scale
    out.update({"x": sx + (sw - w) / 2, "y": sy + (sh - h) / 2,
                "w": w, "h": h, "scale": scale})
    return out


def _reflow(source: dict, target: dict, tw: int, th: int
            ) -> tuple[list[dict], list[dict], list[dict]]:
    template = (L.for_family(target.get("family", ""))
                or L.for_ratio(tw, th))
    objects = list(source.get("objects") or [])

    placed: list[dict] = []
    unplaced: list[dict] = []
    used: set[str] = set()

    # Background first and outside the slot system: it is not a slot, it is
    # the frame, and giving it one would crop it to a box.
    for obj in objects:
        if obj.get("role") == R.BACKGROUND:
            placed.append(_cover(obj, tw, th))
            used.add(obj["id"])

    for slot in template["slots"]:
        match = next((o for o in objects
                      if o.get("role") == slot["role"] and o["id"] not in used),
                     None)
        if match is None:
            continue
        placed.append(_place_in_slot(match, slot, tw, th))
        used.add(match["id"])

    slot_roles = set(L.slot_roles(template))
    for obj in objects:
        if obj["id"] in used:
            continue
        role = obj.get("role") or ""
        if role not in slot_roles:
            reason = (f"the {template['label']} layout has no place for "
                      f"{R.label_for(role).lower() if role else 'an untagged object'}")
        else:
            reason = (f"a second {R.label_for(role).lower()} — the layout has "
                      f"one place for it")
        unplaced.append({"id": obj["id"], "role": role, "reason": reason,
                         "kind": obj.get("kind", ""),
                         "text": obj.get("text", "")})

    findings: list[dict] = []
    for row in unplaced:
        # Copy is the line that decides, and it decides because of what is
        # visible afterwards. A photograph the layout had no room for is
        # obvious to anybody looking at the frame; a line of rate copy that
        # did not fit is not, and it is the client's own words going missing.
        # So an unplaced object carrying words marks the frame for review and
        # one carrying none is a note beside it — a leaderboard with no room
        # for a product shot is the ordinary outcome for a 728x90, and
        # flagging every one of those is how a flag stops being read.
        carries_copy = bool((row.get("text") or "").strip())
        findings.append(qc.finding(
            "unplaced", qc.FAIL if carries_copy else qc.WARN,
            f"Left out: {reason_text(row)}.", objects=[row["id"]]))

    for role in template_missing(template, objects):
        findings.append(qc.finding(
            "empty_slot", qc.WARN,
            f"The {template['label']} layout has a place for "
            f"{R.label_for(role).lower()} and the design has none, so it is "
            f"left empty rather than filled with something else."))

    return placed, unplaced, findings


def reason_text(row: dict) -> str:
    label = R.label_for(row.get("role", "")) or "an untagged object"
    return f"{label.lower()} — {row['reason']}"


def template_missing(template: dict, objects: list[dict]) -> list[str]:
    have = {o.get("role") for o in objects}
    return [r for r in L.slot_roles(template)
            if r not in have and r in R.REQUIRED]


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------

def _boxes_overlap(a: dict, b: dict) -> float:
    ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    if ox <= 0 or oy <= 0:
        return 0.0
    smaller = max(1.0, min(a["w"] * a["h"], b["w"] * b["h"]))
    return (ox * oy) / smaller


def guard(objects: list[dict], tw: int, th: int) -> list[dict]:
    """Overlap and out-of-bounds, after placement.

    A background is exempt from both: covering the frame is its job and
    overflowing it is how cover works. Decorative objects are exempt from the
    overlap check for the same reason — a rule behind a headline is the
    design, not a collision — but not from the bounds check, since a flourish
    hanging off the edge is still clipped.
    """
    findings: list[dict] = []
    for obj in objects:
        if obj.get("role") == R.BACKGROUND:
            continue
        over = (obj["x"] < -0.5 or obj["y"] < -0.5
                or obj["x"] + obj["w"] > tw + 0.5
                or obj["y"] + obj["h"] > th + 0.5)
        if over:
            findings.append(qc.finding(
                "clipped", qc.FAIL,
                f"{R.label_for(obj.get('role','')) or 'An object'} runs past "
                f"the edge of the {tw}x{th} frame.",
                objects=[obj.get("id", "")]))

    checkable = [o for o in objects
                 if o.get("role") not in (R.BACKGROUND, R.DECORATIVE)]
    for i, a in enumerate(checkable):
        for b in checkable[i + 1:]:
            share = _boxes_overlap(a, b)
            if share > OVERLAP_TOLERANCE:
                findings.append(qc.finding(
                    "collision", qc.FAIL,
                    f"{R.label_for(a.get('role','')) or 'An object'} and "
                    f"{R.label_for(b.get('role','')) or 'another'} overlap by "
                    f"{share * 100:.0f}%.",
                    objects=[a.get("id", ""), b.get("id", "")]))
    return findings


# --------------------------------------------------------------------------

def resize(source: dict, target: dict) -> dict:
    """Produce one frame from the source design.

    `target` is a size row from `sizes.py` (or any dict carrying `w`, `h` and
    optionally `family`). The frame comes back with its status already
    decided: `auto` is a frame nothing was found wrong with, and
    `needs_review` is one a person has to look at before it goes anywhere.
    """
    tw, th = int(target.get("w") or 0), int(target.get("h") or 0)
    if tw <= 0 or th <= 0:
        return {"size_id": target.get("id", ""), "width": tw, "height": th,
                "objects": [], "status": NEEDS_REVIEW, "unplaced": [],
                "findings": [qc.finding("no_size", qc.FAIL,
                                        "This size has no dimensions.")],
                "tier": "", "tier_reason": ""}

    tier, why = pick_tier(source, target)
    if tier == "anchor":
        objects, unplaced, findings = _reanchor(source, tw, th), [], []
    else:
        objects, unplaced, findings = _reflow(source, target, tw, th)

    findings = findings + guard(objects, tw, th)
    frame = {"size_id": target.get("id", ""), "label": target.get("label", ""),
             "width": tw, "height": th, "objects": objects,
             "unplaced": unplaced, "tier": tier, "tier_reason": why}
    findings = findings + qc.run(frame)

    frame["findings"] = findings
    frame["status"] = (NEEDS_REVIEW
                       if any(f["level"] == qc.FAIL for f in findings)
                       else AUTO)
    frame["verdict"] = qc.verdict(findings)
    return frame

