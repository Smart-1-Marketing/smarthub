"""From a loudness reading to a list of cuts a person can approve.

Pure functions with no network and no database, deliberately: this is the
half of the Dead Air Cutter that decides what disappears from a client's
commercial, and it should be testable by handing it a list of numbers.

The shape of the decision, in order:

  1. A column is *quiet* if it is below `threshold` of the clip's peak.
  2. A run of quiet columns longer than `gap` is a candidate cut.
  3. Candidates are ranked by length and only the longest `max_cuts` survive,
     because the transformation that carries them has a budget.
  4. Each surviving candidate keeps `breath` seconds -- half at its head, half
     at its tail -- so the edit does not butt two words together.
  5. What is left between the cuts is the segment list, with anything too
     short to be audible dropped.

Step 3 is the one worth reading twice. When a recording has more dead air than
the budget allows, this removes the *most* dead air it can rather than the
*first* -- and the plan says so, so the person is told the clip was capped
instead of finding out by watching a result that is still baggy at the end.
"""
from __future__ import annotations

from . import config


def _clamp(value, low, high):
    return max(low, min(high, value))


def quiet_runs(levels: list[float], threshold: float) -> list[tuple[int, int]]:
    """Half-open [start, end) column ranges that are below the threshold."""
    runs: list[tuple[int, int]] = []
    start = None
    for i, level in enumerate(levels):
        if level <= threshold:
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(levels)))
    return runs


def plan(levels: list[float], *, seconds_per_column: float, duration: float,
         gap: float = config.DEFAULT_GAP,
         breath: float = config.DEFAULT_BREATH,
         sensitivity: str = config.DEFAULT_SENSITIVITY,
         trim_ends: bool = True,
         max_cuts: int = config.MAX_CUTS) -> dict:
    """What to cut, what to keep, and what the person should be told.

    `trim_ends` is separate from the gap rule because the two are different
    edits. A pause in the middle is dead air; the run-up before the first word
    and the tail after the last are head and tail trim, and a person often
    wants one without the other -- a spot whose last shot holds on a logo over
    silence is correctly trimmed in the middle and must not lose its ending.
    """
    gap = _clamp(float(gap), config.MIN_GAP, config.MAX_GAP)
    breath = _clamp(float(breath), 0.0, config.MAX_BREATH)
    threshold = config.SENSITIVITY.get(sensitivity,
                                       config.SENSITIVITY[config.DEFAULT_SENSITIVITY])
    half = breath / 2.0

    def at(column: int) -> float:
        return _clamp(column * seconds_per_column, 0.0, duration)

    runs = quiet_runs(levels, threshold)
    last = len(levels)

    candidates = []
    for start_col, end_col in runs:
        start, end = at(start_col), at(end_col)
        if end - start < gap:
            continue
        leading = start_col == 0
        trailing = end_col >= last
        if (leading or trailing) and not trim_ends:
            continue
        # `cut_start`/`cut_end` bound the region that DISAPPEARS, which is
        # the quiet run pulled in by half a breath at each end. A cut at the
        # head or the tail only needs that on the side with audio next to it:
        # padding the outside edge of a head trim just puts the silence back.
        cut_start = start if leading else min(start + half, end)
        cut_end = end if trailing else max(end - half, cut_start)
        if cut_end - cut_start <= 0.01:
            continue
        candidates.append({
            "start": round(cut_start, 3),
            "end": round(cut_end, 3),
            "removed": round(cut_end - cut_start, 3),
            "silence": round(end - start, 3),
            "where": "head" if leading else ("tail" if trailing else "middle"),
        })

    considered = len(candidates)
    capped = considered > max_cuts
    if capped:
        candidates = sorted(candidates, key=lambda c: -c["removed"])[:max_cuts]
    cuts = sorted(candidates, key=lambda c: c["start"])

    # Everything not cut, in order. Built from the cut list rather than from
    # the runs, so the two can never describe different videos.
    segments = []
    cursor = 0.0
    for cut in cuts:
        if cut["start"] - cursor >= config.MIN_SEGMENT:
            segments.append({"start": round(cursor, 3),
                             "end": round(cut["start"], 3)})
        cursor = cut["end"]
    if duration - cursor >= config.MIN_SEGMENT:
        segments.append({"start": round(cursor, 3), "end": round(duration, 3)})

    kept = round(sum(s["end"] - s["start"] for s in segments), 3)
    removed = round(sum(c["removed"] for c in cuts), 3)

    return {
        "segments": segments,
        "cuts": cuts,
        "removed": removed,
        "kept": kept,
        "duration": round(float(duration), 3),
        "considered": considered,
        "capped": capped,
        "max_cuts": max_cuts,
        "threshold": threshold,
        "settings": {"gap": gap, "breath": breath,
                     "sensitivity": sensitivity, "trim_ends": bool(trim_ends)},
        "notes": _notes(cuts, segments, removed, duration, capped, max_cuts,
                        considered),
    }


def _notes(cuts, segments, removed, duration, capped, max_cuts, considered):
    """What the page should say about this plan, in plain sentences.

    Written here rather than in the template because these are conclusions
    about the analysis, and a template that recomputed them would be a second
    opinion that could disagree with the first.
    """
    out = []
    if not cuts:
        out.append("Nothing to cut at these settings — every pause in this "
                   "clip is shorter than the gap you set, or there is sound "
                   "under all of it. Try a shorter gap, or a more aggressive "
                   "sensitivity.")
        return out
    if capped:
        out.append(f"This clip has {considered} gaps worth cutting and one "
                   f"edit can carry {max_cuts}. The {max_cuts} longest were "
                   f"taken, so the result is tighter but not fully tight — "
                   f"run it through a second time to catch the rest.")
    if duration and removed / duration > 0.4:
        out.append("More than 40% of this clip is being removed. That is "
                   "usually right for a raw recording and usually wrong for "
                   "a finished spot — watch the preview before you save it.")
    if len(segments) > 1 and min(s["end"] - s["start"] for s in segments) < 0.5:
        out.append("Some kept pieces are under half a second. If the result "
                   "sounds choppy, raise the breathing room.")
    return out


def concat_transformation(public_id: str, segments: list[dict]) -> str:
    """The Cloudinary transformation that plays those segments back to back.

    The first segment is a plain trim on the asset itself; each one after it
    is the same asset spliced on as a layer. Folder separators become colons
    inside a layer reference, which is Cloudinary's own escaping and is the
    single most common way a correct-looking transformation 400s.

    Returns "" for a plan that removes nothing, so the caller can decline to
    render an edit that would be a copy of its source.
    """
    if len(segments) < 1:
        return ""
    layer_id = str(public_id or "").replace("/", ":")
    head = segments[0]
    chain = [f"so_{_t(head['start'])},eo_{_t(head['end'])}"]
    for seg in segments[1:]:
        chain.append(f"fl_splice,l_video:{layer_id}")
        chain.append(f"so_{_t(seg['start'])},eo_{_t(seg['end'])}")
        chain.append("fl_layer_apply")
    return "/".join(chain)


def _t(value) -> str:
    """Cloudinary takes 8 and 8.5 in an offset but not 8.0.

    Same normalisation as hub/video_library._num(), and written out here
    rather than imported because this module is deliberately free of Hub
    imports so it can be tested on its own.
    """
    try:
        f = round(float(value), 2)
    except (TypeError, ValueError):
        return "0"
    return str(int(f)) if f == int(f) else str(f)
