"""The published thresholds a video ad is machine-scored against.

Every number in this file is somebody else's, and each one carries the name of
whose it is. That is the whole point of the module: a QC panel that says "your
average shot is 10 seconds and Google's own detector wants 2" is an argument a
client cannot talk us out of, where "our tool thinks this is slow" is an
opinion. `SOURCES` is printed on the panel for the same reason
`hub/creative_specs.SPEC_KIT_URL` is printed on every spec verdict.

## Where these come from

Google open-sources the evaluator it machine-scores YouTube creative with
(`google-marketing-solutions/abcds-detector`), and its `configuration.py`
carries the literal thresholds. Amazon publishes a tighter brand window for
Streaming TV and a set of measured lifts per creative element. Roku publishes
its attention and recall windows. Those are the four sources below.

## What is deliberately NOT here

Nothing about type size. No platform publishes a minimum for 10-foot viewing —
the guidance stops at "minimise text, prioritise voiceover" — so a number here
would be ours wearing somebody else's name, which is the one thing this module
exists to prevent. The house legibility standard belongs in `config.py` under
Smart 1's own name, and is labelled as ours wherever it is shown.

And nothing here is measured off pixels. This scores the **plan** — shot
durations, cut positions, what the beats say — because that is what exists
before a render, and because the whole value of a threshold is knowing you
have missed it while it is still free to fix. A frame-accurate pass over a
finished MP4 is a different tool and would need the video.
"""

from __future__ import annotations

SOURCES = {
    "abcd": ("Google ABCDs Detector (open source), configuration.py",
             "https://github.com/google-marketing-solutions/abcds-detector"),
    "amazon": ("Amazon Ads — Streaming TV creative guidance", ""),
    "roku": ("Roku — creative guidelines", ""),
    "house": ("Smart 1 house standard — ours, not a platform rule", ""),
}

# --------------------------------------------------------------------------
# The thresholds. `source` on every row, because a number with no provenance
# is an opinion and gets argued with.
# --------------------------------------------------------------------------
THRESHOLDS = {
    # Average shot length. The one that matters most here: a :30 built one
    # scene per beat is three shots averaging ten seconds.
    "avg_shot_seconds": {"value": 2.0, "source": "abcd",
                         "label": "Quick pacing — average shot"},
    # A cut inside the first three seconds. "Dynamic start" in the detector.
    "first_cut_ms": {"value": 3000, "source": "abcd",
                     "label": "Dynamic start — first cut by"},
    "shots_in_first_5s": {"value": 2, "source": "abcd",
                          "label": "Shots inside the first 5 seconds"},
    "brand_by_seconds": {"value": 5.0, "source": "abcd",
                         "label": "Brand or product on screen by"},
    # Amazon is tighter than Google on the same rule.
    "brand_by_seconds_ctv": {"value": 3.0, "source": "amazon",
                             "label": "Brand or product on screen by (Streaming TV)"},
    "face_frame_pct": {"value": 15, "source": "abcd",
                       "label": "Face close-up fills"},
    "logo_frame_pct": {"value": 3.5, "source": "abcd",
                       "label": "Logo counts as visible at"},
    "vertical_subject_pct": {"value": 60, "source": "abcd",
                             "label": "Vertical tight framing — subject fills"},
    "vo_words_per_second": {"value": 2.5, "source": "house",
                            "label": "Voiceover pace — target ceiling"},
    "vo_words_per_second_max": {"value": 3.0, "source": "house",
                                "label": "Voiceover pace — hard ceiling"},
    "roku_attention_seconds": {"value": 7.0, "source": "roku",
                               "label": "Roku attention window"},
    "roku_recall_seconds": {"value": 5.0, "source": "roku",
                            "label": "Roku brand-recall window (from the end)"},
}

# The house legibility standard, kept apart from everything above and labelled
# as ours. No platform publishes one; inventing a number and attributing it to
# a platform would be exactly the failure the rest of this file avoids.
HOUSE_LEGIBILITY = {
    "cap_height_pct": 3.0,      # ~32px on a 1080 frame
    "min_super_seconds": 3.0,
    "note": ("Smart 1's own standard, not a platform rule — no platform publishes "
             "a minimum type size for 10-foot viewing."),
}

# Amazon's measured lift per creative element. These are sales numbers rather
# than thresholds, so they are guidance a rep reads while building rather than
# something to score against — nothing here fails a spot.
MEASURED_LIFT = [
    {"element": "Realistic, relatable settings", "lift": "+16%",
     "means": "Prefer vignette and testimonial over abstract anthem footage."},
    {"element": "Voiceover or dialogue", "lift": "+10%",
     "means": "Never ship a music-only cut."},
    {"element": "Product introduced in the first 5 seconds", "lift": "+8%",
     "means": "Already the beat structure — now it has a number attached."},
    {"element": "Easy-to-read on-screen text", "lift": "+5%",
     "means": "Backs the house legibility standard."},
    {"element": ":30 versus :15", "lift": "+12%",
     "means": "On CTV the :30 is the default and the :15 is the cutdown."},
]

# A bumper is one or two shots by design and is scored on none of the pacing
# rules: cutting a six-second spot to Google's average would be three cuts a
# second, which is not a bumper, it is a strobe.
BUMPER_LENGTHS = (5, 6)


def _source_label(key):
    return SOURCES.get(key, ("unknown", ""))[0]


def shot_targets(length_seconds):
    """How many shots a spot of this length wants, from the 2-second average.

    A range rather than a number: a spot is not better for hitting exactly 15
    shots, it is better for not being three. The bumpers are excluded by
    design and get their own answer.
    """
    length = int(length_seconds or 0)
    if length in BUMPER_LENGTHS:
        return {"low": 1, "high": 2,
                "note": "A bumper is one or two shots by design — pacing rules do not apply."}
    target = THRESHOLDS["avg_shot_seconds"]["value"]
    ideal = max(2, round(length / target))
    return {"low": max(2, ideal - 1), "high": ideal + 1,
            "note": (f"About {ideal} shots — {target:g}s average, which is "
                     f"{_source_label('abcd')}'s own quick-pacing threshold.")}


def score(shots, length_seconds, platform="both"):
    """Score a planned spot against the thresholds above.

    `shots` is a list of dicts carrying at least `start` and `end`. Returns one
    row per rule with `passed`, the measured value and the threshold, plus a
    headline. Never raises: this runs inside QC and a scoring bug must not
    take the panel down.

    A rule that cannot be measured from a plan is reported as **not measured**
    rather than passed — face size and logo size need pixels, and a green tick
    over a rule nothing checked is the confident wrong answer this codebase
    keeps undoing.
    """
    length = float(length_seconds or 0)
    rows = []
    shots = [s for s in (shots or []) if s is not None]

    if not shots:
        return {"rows": [], "score": 0, "of": 0, "measured": False,
                "headline": "No shots to score yet."}

    bumper = int(length) in BUMPER_LENGTHS

    # --- average shot length ------------------------------------------------
    durations = [max(0.0, float(s.get("end") or 0) - float(s.get("start") or 0))
                 for s in shots]
    avg = (sum(durations) / len(durations)) if durations else 0.0
    if bumper:
        rows.append(_row("avg_shot_seconds", None, avg,
                         "A bumper is one or two shots by design — not scored on pacing.",
                         measured=False))
    else:
        want = THRESHOLDS["avg_shot_seconds"]["value"]
        rows.append(_row("avg_shot_seconds", avg <= want + 0.001, avg,
                         f"{len(shots)} shots, averaging {avg:.1f}s "
                         f"(threshold {want:g}s or less)."))

    # --- dynamic start ------------------------------------------------------
    first_cut = float(shots[0].get("end") or 0) if len(shots) > 1 else None
    if bumper:
        rows.append(_row("first_cut_ms", None, None,
                         "Not scored on a bumper.", measured=False))
    elif first_cut is None:
        rows.append(_row("first_cut_ms", False, None,
                         "There is only one shot, so the spot never cuts."))
    else:
        want_ms = THRESHOLDS["first_cut_ms"]["value"]
        rows.append(_row("first_cut_ms", first_cut * 1000 <= want_ms, first_cut,
                         f"First cut at {first_cut:.1f}s "
                         f"(threshold {want_ms / 1000:g}s or sooner)."))

    # --- shots inside the first five seconds --------------------------------
    if bumper:
        rows.append(_row("shots_in_first_5s", None, None,
                         "Not scored on a bumper.", measured=False))
    else:
        early = sum(1 for s in shots if float(s.get("start") or 0) < 5.0)
        want = THRESHOLDS["shots_in_first_5s"]["value"]
        rows.append(_row("shots_in_first_5s", early >= want, early,
                         f"{early} shot(s) begin inside the first 5 seconds "
                         f"(threshold {want} or more)."))

    # --- the brand window ---------------------------------------------------
    # Amazon is tighter than Google on the same rule, and a CTV spot is judged
    # against the tighter one rather than the one it would pass.
    key = "brand_by_seconds_ctv" if platform in ("ctv", "both") else "brand_by_seconds"
    want = THRESHOLDS[key]["value"]
    # Measured from the plan: the earliest shot whose visual or grammar says
    # the brand or product is on screen. Nothing is inferred from a shot that
    # says neither -- it is simply not evidence.
    branded = [float(s.get("start") or 0) for s in shots if _mentions_brand(s)]
    if branded:
        at = min(branded)
        rows.append(_row(key, at <= want, at,
                         f"Brand or product first described at {at:.1f}s "
                         f"(threshold {want:g}s)."))
    else:
        rows.append(_row(key, None, None,
                         "Not measured — no shot describes the brand or product on "
                         "screen, so there is nothing to time.", measured=False))

    # --- the rules a plan genuinely cannot answer ---------------------------
    for k, why in (("face_frame_pct", "needs the rendered frame to measure"),
                   ("logo_frame_pct", "needs the rendered frame to measure")):
        rows.append(_row(k, None, None, f"Not measured — {why}.", measured=False))

    measured_rows = [r for r in rows if r["measured"]]
    passed = [r for r in measured_rows if r["passed"]]
    return {
        "rows": rows,
        "score": len(passed),
        "of": len(measured_rows),
        "measured": bool(measured_rows),
        "headline": _headline(len(passed), len(measured_rows), bumper),
    }


def _headline(passed, total, bumper):
    if bumper and not total:
        return "A bumper is not scored on pacing — it is one idea held still."
    if not total:
        return "Nothing could be measured from this plan yet."
    if passed == total:
        return f"Meets all {total} of the thresholds that can be scored from a plan."
    return (f"Meets {passed} of {total} scorable thresholds — "
            f"{total - passed} to look at below.")


def _row(key, passed, value, message, measured=True):
    spec = THRESHOLDS[key]
    return {"key": key, "label": spec["label"], "threshold": spec["value"],
            "source": _source_label(spec["source"]), "passed": bool(passed),
            "value": value, "message": message, "measured": measured}


_BRAND_WORDS = ("logo", "brand", "product", "storefront", "sign", "packshot",
                "pack shot", "van", "truck", "uniform", "shopfront", "facade",
                "bottle", "box", "label", "end card", "endcard")


def _mentions_brand(shot):
    text = " ".join(str(shot.get(f) or "") for f in
                    ("visual", "visual_description", "grammar_note")).lower()
    return any(word in text for word in _BRAND_WORDS) or bool(shot.get("is_cta"))
