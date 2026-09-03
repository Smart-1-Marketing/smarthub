"""What a frame is checked for, and whose rule each check is.

Two kinds of check live here and they are kept apart on every verdict, because
they are argued with differently. A **kit** check is the S1M CREATIVE SPEC KIT
or a platform's own published ceiling, and it is judged by
`hub.creative_specs.check()` rather than by a limit restated in this module —
the kit is transcribed once, `kit_drift()` holds that transcription against the
published page, and a fourth copy here is the one that would go stale. A
**house** check is ours, and says so wherever it is drawn: "our tool thinks
this is too small" is an opinion, and a client can talk us out of an opinion.

The legibility floor is the one number this file owns, and it is deliberately
**advisory**. No platform publishes a minimum type size for display, so a hard
failure on it would be house guidance wearing a platform's name — the reason
`HOUSE_LEGIBILITY` is kept out of `THRESHOLDS` in `services/abcd_service.py`.
It matches the `minFontPx` the display-ad renderer already carries per size,
so the two halves of this Hub at least agree; §7 of the build plan asks for a
figure signed off rather than assumed, and until there is one this warns and
never blocks.

Nothing here refuses a resize. A check that stops the correct thing is a check
somebody switches off, and switching this off would cost the missing-logo
finding along with it.
"""
from __future__ import annotations


from . import roles as R
from . import sizes as S

try:                                                   # pragma: no cover
    from hub import creative_specs
except Exception:                                      # noqa: BLE001
    creative_specs = None                              # type: ignore[assignment]

# House. See the module docstring: advisory until a number is signed off.
MIN_FONT_PX = 11
MIN_FONT_SOURCE = ("house — no platform publishes a minimum type size for "
                   "display. Matches the per-size minFontPx the display-ad "
                   "renderer already uses. Advisory, never a block.")

FAIL, WARN, NOTE = "fail", "warn", "note"


def finding(code: str, level: str, message: str, *,
            objects: list[str] | None = None, source: str = "house") -> dict:
    return {"code": code, "level": level, "message": message,
            "objects": list(objects or []), "source": source}


def required_roles(objects: list[dict]) -> list[dict]:
    """Every frame carries a logo and a call to action. House rules, both."""
    out: list[dict] = []
    present = [o.get("role", "") for o in objects]
    for role in R.missing_required(present):
        out.append(finding(
            f"missing_{role}", FAIL,
            f"No {R.label_for(role).lower()} on this frame.",
        ))
    return out


def _overlap(a: dict, b: dict) -> float:
    ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    if ox <= 0 or oy <= 0:
        return 0.0
    return ox * oy


def cta_clear(objects: list[dict]) -> list[dict]:
    """The call to action is not covered by anything drawn after it.

    Only by something *above* it: a button sitting on the background is the
    normal case, and reporting that is how a check stops being read. Order is
    the paint order, so anything later in the list is on top.
    """
    out: list[dict] = []
    for i, obj in enumerate(objects):
        if obj.get("role") != R.CTA:
            continue
        area = max(1.0, obj["w"] * obj["h"])
        for other in objects[i + 1:]:
            if other.get("role") in (R.BACKGROUND, R.CTA):
                continue
            covered = _overlap(obj, other) / area
            if covered > 0.10:
                out.append(finding(
                    "cta_obscured", FAIL,
                    f"{R.label_for(other.get('role','')) or 'An object'} covers "
                    f"{covered * 100:.0f}% of the call to action.",
                    objects=[obj.get("id", ""), other.get("id", "")]))
    return out


def legibility(objects: list[dict]) -> list[dict]:
    out: list[dict] = []
    for obj in objects:
        if obj.get("kind") != "text":
            continue
        size = float(obj.get("fontSize") or 0)
        if 0 < size < MIN_FONT_PX:
            out.append(finding(
                "type_too_small", WARN,
                f"{R.label_for(obj.get('role','')) or 'Text'} lands at "
                f"{size:.0f}px, under our {MIN_FONT_PX}px floor.",
                objects=[obj.get("id", "")]))
    return out


def weight(size_id: str, *, size_bytes: int, fmt: str = "jpg") -> dict:
    """Judge an exported file against the kit, or say why it was not judged.

    Never a restated ceiling. A size the kit maps a unit for goes through
    `creative_specs.check()`; a house size with a published platform ceiling
    of its own (the two Responsive Display assets) is judged on that and says
    whose it is; and a house size with neither is **not measured** rather than
    judged against the nearest-looking unit.
    """
    spec = S.get(size_id)
    if not spec:
        return {"measured": False, "note": f"No size is declared as {size_id}."}

    unit_id = spec.get("unit") or ""
    if unit_id and creative_specs is not None:
        try:
            verdict = creative_specs.check(
                width=spec["w"], height=spec["h"],
                size_bytes=int(size_bytes or 0), fmt=fmt, unit_id=unit_id)
        except Exception as exc:                       # noqa: BLE001
            return {"measured": False,
                    "note": f"The spec kit could not judge this file: {exc}"}
        return {"measured": True, "source": "kit", "unit": unit_id,
                "result": verdict.get("result"),
                "summary": verdict.get("summary"),
                "checks": verdict.get("checks") or []}

    ceiling = spec.get("max_bytes")
    if ceiling:
        over = int(size_bytes or 0) > int(ceiling)
        return {
            "measured": True, "source": "platform",
            "result": "fail" if over else "pass",
            "summary": (f"{size_bytes / 1024:.0f} KB against a "
                        f"{int(ceiling) / (1024 * 1024):.0f} MB ceiling."),
            "note": spec.get("weight_source", ""),
        }

    return {
        "measured": False, "source": "house",
        "note": ("The spec kit weighs no unit at this size, so the file was "
                 "not judged against a published ceiling."),
    }


def run(frame: dict) -> list[dict]:
    """Every frame-level check. Geometry findings ride in from the engine."""
    objects = frame.get("objects") or []
    return required_roles(objects) + cta_clear(objects) + legibility(objects)


def verdict(findings: list[dict]) -> str:
    if any(f.get("level") == FAIL for f in findings):
        return FAIL
    if any(f.get("level") == WARN for f in findings):
        return WARN
    return "pass"
