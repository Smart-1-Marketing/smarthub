"""The fallback when a template cannot place a frame — never the first pass.

`templates_layout.py` is deliberately house-authored and fixed, so a size set
is the same ad eight times. This is what happens on the one frame that lands
in `needs_review` anyway: a **Recompose with AI** button on that frame, and on
no other. There is no bulk fix-everything action, because a model asked to
lay out eight frames produces eight frames a person then has to check, which
is the work the templates exist to have already done.

Four rules, and each is one of this Hub's standing ones wearing a layout.

**It proposes; the code decides; a person presses.** The model returns
*positions* for objects that already exist — never an image, never new copy,
never an object nobody added. Anything it names that is not on the frame is
dropped and counted; anything it leaves out keeps the position the template
gave it. A model that returns nothing usable costs the suggestion, not the
frame.

**It is handed roles and geometry, not the design.** The prompt carries the
target size, each object's role, its current box and, for text, how long the
copy is. It is not handed the client's name, the brand or the imagery: none
of that changes where a button goes, and every field in a prompt is a field a
model can echo back into an answer.

**A proposal is `ai`, not `auto`.** The status is what tells somebody scanning
a set which frames a template produced and which a model adjusted, and one
status for both makes it unanswerable.

**Nothing is applied by arriving.** `propose()` returns a proposal; writing it
onto the frame is `store.mark_ai()`, which the route only reaches on an
explicit press.
"""
from __future__ import annotations

import json
from typing import Any

from . import qc
from . import roles as R

MODULE = "magic_resize"
PURPOSE = "frame_recompose"

_INSTRUCTIONS = """You are laying out one display ad frame.

You are given the frame's pixel size and a list of objects already on it. Each
object has an id, a role, and its current box. Reposition them so the frame
reads as a finished ad: nothing overlapping, nothing past an edge, the logo
and the call to action both clearly visible, and the reading order sensible
for this shape.

Answer with JSON and nothing else, in this exact shape:

{"objects": [{"id": "<id>", "x": <number>, "y": <number>,
              "w": <number>, "h": <number>}]}

Rules you must keep:
- Use only the ids you were given. Do not invent an object.
- x, y, w and h are pixels inside the frame. Every box must sit fully inside it.
- Do not change any copy. You are not being asked for words.
- Keep each image object's width-to-height ratio within 2% of what you were
  given; a stretched logo is worse than a badly placed one.
"""


def manifest(frame: dict) -> dict:
    """What the model is shown. Roles and boxes, and nothing about the client."""
    return {
        "width": frame.get("width"),
        "height": frame.get("height"),
        "objects": [
            {
                "id": o.get("id"),
                "role": o.get("role") or "untagged",
                "kind": o.get("kind"),
                "box": [round(float(o.get("x", 0)), 1),
                        round(float(o.get("y", 0)), 1),
                        round(float(o.get("w", 0)), 1),
                        round(float(o.get("h", 0)), 1)],
                "characters": len(str(o.get("text") or "")) or None,
            }
            for o in frame.get("objects") or []
            if o.get("role") != R.BACKGROUND
        ],
    }


def _parse(answer: str) -> list[dict]:
    text = (answer or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The model did not answer with JSON.")
    data = json.loads(text[start:end + 1])
    rows = data.get("objects")
    if not isinstance(rows, list) or not rows:
        raise ValueError("The model returned no positions.")
    return rows


def apply_positions(frame: dict, rows: list[dict]) -> tuple[list[dict], dict]:
    """Move the frame's own objects. Nothing is created and nothing is dropped.

    An object the model did not mention keeps the position it had — a model
    that answered about four of six objects has still helped with four, and
    dropping the other two would lose them off the ad.
    """
    by_id = {str(r.get("id")): r for r in rows if isinstance(r, dict)}
    known = {o.get("id") for o in frame.get("objects") or []}
    unknown = [i for i in by_id if i not in known]

    moved: list[str] = []
    out: list[dict] = []
    for obj in frame.get("objects") or []:
        row = by_id.get(obj.get("id", ""))
        if not row or obj.get("role") == R.BACKGROUND:
            out.append(dict(obj))
            continue
        try:
            x, y = float(row["x"]), float(row["y"])
            w, h = float(row["w"]), float(row["h"])
        except (KeyError, TypeError, ValueError):
            out.append(dict(obj))
            continue
        if w <= 0 or h <= 0:
            out.append(dict(obj))
            continue
        new = dict(obj)
        new.update({"x": x, "y": y, "w": w, "h": h})
        if obj.get("kind") == "text":
            from .engine import fit_font
            new["fontSize"] = fit_font(obj.get("text", ""), w, h)
        out.append(new)
        moved.append(obj.get("id", ""))
    return out, {"moved": moved, "ignored_unknown": unknown,
                 "untouched": sorted(known - set(moved))}


def propose(frame: dict, *, ask=None) -> dict:
    """Ask for a layout. Returns a proposal — it writes nothing.

    `ask` is the transport, defaulting to the Hub's one OpenAI reader, so a
    test stands in front of this without a key and without a call.
    """
    if ask is None:                                    # pragma: no cover
        from hub.openai_responses import ask as _ask
        ask = _ask

    prompt = (_INSTRUCTIONS + "\nFrame:\n"
              + json.dumps(manifest(frame), separators=(",", ":")))
    try:
        answer = ask(prompt, module=MODULE, purpose=PURPOSE,
                     max_output_tokens=2000)
    except Exception as exc:                           # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    try:
        rows = _parse(answer)
    except Exception as exc:                           # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    objects, report = apply_positions(frame, rows)
    findings = _check(objects, frame)
    return {"ok": True, "objects": objects, "report": report,
            "findings": findings,
            "clean": not any(f["level"] == qc.FAIL for f in findings)}


def _check(objects: list[dict], frame: dict) -> list[dict]:
    """The proposal goes through the same guard a template's output does.

    A model told not to overlap anything still does, and a proposal that
    arrives with a collision must not read as the fix for the collision it was
    asked about.
    """
    from .engine import guard
    return guard(objects, int(frame.get("width") or 0),
                 int(frame.get("height") or 0)) + qc.run(
                     {"objects": objects})
