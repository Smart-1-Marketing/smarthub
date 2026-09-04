"""Vox explainer — the beat list, and the contract it has to satisfy.

A Vox-style explainer is a 60–90 second editorial collage: a topic, a
document or a link becomes a sequence of **beats**, each a claim with a
headline, a supporting line and a visual treatment, and the HyperFrames
template turns those into typography and cut-out collage. That makes this
module the one place in the feature where real content intelligence is
needed. Everything after it is deterministic — `hub/hyperframes.py` fills a
pre-authored template and headless Chrome renders the same output every time
for the same input, which is the whole reason this framework was worth
standing up beside Creatomate.

So the risk is not the rendering. It is the join: a model writes JSON, a
template consumes JSON, and nothing between them checks that the two agree.
That gap is the failure this file exists to close, and it is the shape the
Commercial Builder has already paid for once — `submit_render` built its
audit line from `project.name` and `project.length`, attributes the model
does not have, and every render this tool had ever been asked for returned a
500 that reached the browser as "Bad response from server".

Six rules, each of which is a way this goes quietly wrong.

**A beat list is validated, never trusted.** `validate()` is run over
whatever comes back *and* over anything typed by hand in the preview, or the
rule holds only until somebody edits a beat. A model asked for eight beats
returns seven and a paragraph about why; a model asked for `seconds` returns
`"about 8"`. Both render, badly, and neither errors.

**A beat that cannot be read is dropped and counted, never repaired.**
Inventing the missing half of somebody's explainer is this module writing
copy nobody asked for, and a silently shorter list is a video missing exactly
the point somebody wanted made. `validate()` returns what it dropped and the
screen prints it — the rule `hub/site_names_ai.py` applies to an ungrounded
reading.

**The window is the format's, and it is arithmetic rather than a request.**
`VOX_MIN_SECONDS`–`VOX_MAX_SECONDS` is what the skill is scoped to, so the
per-beat seconds are **rebalanced** to fit rather than the prompt being asked
nicely for a total. A model told "make it 75 seconds" writes beats summing to
52 and says 75 in a field beside them.

**"We could not ask the model" is not "there is nothing to explain."** With
no OpenAI key the outline still comes back, built from the source text, marked
`source: "house"`, and the screen says which it got — the answer
`modules/image_picker/profile.py` arrived at. A tool that returns nothing
when a provider is down reads as broken.

**Nothing is rendered by arriving.** `generate()` produces a draft; a person
reads it and presses. Rendering is minutes of headless Chrome and the beat
list is the only thing anybody can correct before it — the "approve before
spend" shape every expensive step in this Hub uses.

**A Vox explainer is not a broadcast slot.** It is deliberately not offered
at :05/:15/:30/:60 — those are the durations inventory is sold in, and this
format is an editorial piece for YouTube and social. `PLATFORMS` says where
it belongs and the Start page reads it rather than restating it.
"""

from __future__ import annotations

import re

from hub.hyperframes import VOX_MAX_SECONDS, VOX_MIN_SECONDS

__all__ = [
    "COMMERCIAL_TYPE", "PLATFORMS", "FORMATS", "SOURCE_KINDS", "TREATMENTS",
    "MIN_BEATS", "MAX_BEATS", "TARGET_SECONDS",
    "validate", "rebalance", "outline_from_text", "beats_seconds",
    "clean_source_kind",
    "platform_note", "check_spec",
]

# The ninth commercial type. Its id is written into `cb_projects.commercial_type`
# and read by `library_spec.archetype_for()`, so it is a value on disk from the
# first project built with it and is never renamed — the `display_ads` rule.
COMMERCIAL_TYPE = "vox_explainer"

# Where a 60–90 second collage explainer is actually a placement. A CTV buy
# sells :15 and :30 slots and refuses this outright, so offering it there is a
# length somebody picks and then cannot traffic.
#
# `both` is deliberately absent, and it is the one worth naming: config spells
# it "CTV and YouTube", so a Vox explainer allowed on `both` is a Vox explainer
# on CTV — refused by the buy, with the platform field reading as though it had
# been checked. The half that passes is not permission for the half that does
# not.
PLATFORMS = ("youtube", "social")

# 16:9 leads because that is where an explainer of this length is watched.
# 1:1 is deliberately absent: a square crop of an editorial collage loses the
# typography the format is built on, and offering it is a crop nobody wants.
FORMATS = ("16:9", "9:16")

SOURCE_KINDS = [
    {"id": "topic", "label": "A topic",
     "hint": "One line saying what this should explain."},
    {"id": "document", "label": "A document",
     "hint": "Paste the text. A brief, a one-pager, a set of notes."},
    {"id": "link", "label": "A link",
     "hint": "A page we read and explain. The page is fetched, not imagined."},
]
_SOURCE_IDS = {s["id"] for s in SOURCE_KINDS}
DEFAULT_SOURCE_KIND = "topic"

# The visual treatments the template draws. Closed, because a treatment
# outside this set reaches a template with no branch for it and renders the
# default while the beat list says otherwise — the same closed-vocabulary rule
# `hub/hyperframes.PAINT_STYLES` follows.
TREATMENTS = [
    {"id": "statement", "label": "Statement",
     "hint": "Type on color. The claim carries it."},
    {"id": "collage", "label": "Collage",
     "hint": "Cut-out imagery behind the line. The format's default."},
    {"id": "data", "label": "Number",
     "hint": "One figure, large. For a beat whose point is a quantity."},
    {"id": "quote", "label": "Quote",
     "hint": "Attributed words. Needs a source on the beat."},
]
_TREATMENT_IDS = {t["id"] for t in TREATMENTS}
DEFAULT_TREATMENT = "collage"

# Under six beats and 75 seconds is one idea held far too long; over ten and
# no beat gets the four-plus seconds a headline needs to be read.
MIN_BEATS = 4
MAX_BEATS = 10
TARGET_SECONDS = 75          # the middle of the window, so either edit fits

_MIN_BEAT_SECONDS = 4.0
_MAX_BEAT_SECONDS = 20.0

# Spreading the remainder can itself push a beat into a clamp, so the pass
# repeats. Bounded rather than `while`: this runs on a list a model wrote and
# a loop that cannot terminate is worse than a total that is a second out.
_REBALANCE_PASSES = 8

# What a beat may carry, and nothing else. A key the template does not read is
# a field somebody fills in that changes nothing — the failure
# `current_marketing.unanswered_keys()` exists to name.
_BEAT_KEYS = ("headline", "support", "treatment", "seconds", "source",
              "image_query")


# --------------------------------------------------------------------------- #
# validation — the join between the model and the template
# --------------------------------------------------------------------------- #

def validate(beats, *, total_seconds: float = TARGET_SECONDS) -> dict:
    """Read a beat list the way the template will.

    Returns `{"beats", "dropped", "ok", "seconds"}`. `dropped` names what was
    thrown away and why, because a list that quietly gets shorter is a video
    missing exactly the beat somebody wanted, and a count with no reason on it
    is not something anybody can act on.

    Never raises: it is handed whatever a model returned, which on a bad day
    is a string, a dict, or a list of strings.
    """
    dropped: list[dict] = []
    clean: list[dict] = []

    if isinstance(beats, dict):
        # A model asked for a list routinely wraps it. Read the wrapper rather
        # than reporting the whole answer as unusable.
        beats = beats.get("beats") or beats.get("items") or []
    if not isinstance(beats, list):
        return {"beats": [], "dropped": [{"index": None, "reason":
                "The beat list came back as something that is not a list."}],
                "ok": False, "seconds": 0.0}

    for i, raw in enumerate(beats):
        if not isinstance(raw, dict):
            dropped.append({"index": i, "reason": "not a beat — it came back as "
                                                  "plain text rather than a set "
                                                  "of fields."})
            continue
        headline = _clean(raw.get("headline"), 90)
        if not headline:
            # The headline IS the beat. One with none renders as an empty
            # card holding its own seconds, which reads as a rendering fault
            # rather than as a beat nobody wrote.
            dropped.append({"index": i, "reason": "no headline, so there is "
                                                  "nothing for the beat to say."})
            continue
        treatment = str(raw.get("treatment") or "").strip().lower()
        if treatment not in _TREATMENT_IDS:
            treatment = DEFAULT_TREATMENT
        beat = {
            "headline": headline,
            "support": _clean(raw.get("support"), 180),
            "treatment": treatment,
            "seconds": _seconds(raw.get("seconds")),
            "source": _clean(raw.get("source"), 120),
            "image_query": _clean(raw.get("image_query"), 80),
        }
        if beat["treatment"] == "quote" and not beat["source"]:
            # An unattributed quote on a document about somebody's business is
            # a claim we cannot stand behind. Demoted rather than dropped —
            # the words are still a perfectly good statement.
            beat["treatment"] = "statement"
        clean.append(beat)

    if len(clean) > MAX_BEATS:
        for i, extra in enumerate(clean[MAX_BEATS:], start=MAX_BEATS):
            dropped.append({"index": i, "reason": f"beat {i + 1} of "
                            f"{len(clean)} — a Vox explainer holds at most "
                            f"{MAX_BEATS} and the rest get no time on screen."})
        clean = clean[:MAX_BEATS]

    clean = rebalance(clean, total_seconds=total_seconds)
    return {"beats": clean, "dropped": dropped,
            "ok": len(clean) >= MIN_BEATS,
            "seconds": beats_seconds(clean)}


def rebalance(beats, *, total_seconds: float = TARGET_SECONDS) -> list[dict]:
    """Make the beats sum to the target, in code rather than in the prompt.

    A model told to write 75 seconds of beats writes beats summing to 52 and
    puts 75 in a field beside them. The window is arithmetic, so it is done
    here — and the *shape* the model chose is kept: a beat it gave twice the
    time keeps twice the time, scaled, rather than every beat being flattened
    to the average.
    """
    beats = [b for b in (beats or []) if isinstance(b, dict)]
    if not beats:
        return []
    target = max(VOX_MIN_SECONDS, min(VOX_MAX_SECONDS, float(total_seconds or TARGET_SECONDS)))

    have = sum(float(b.get("seconds") or 0) for b in beats)
    if have <= 0:
        even = target / len(beats)
        for b in beats:
            b["seconds"] = round(even, 2)
    else:
        scale = target / have
        for b in beats:
            b["seconds"] = round(max(_MIN_BEAT_SECONDS,
                                     min(_MAX_BEAT_SECONDS,
                                         float(b.get("seconds") or 0) * scale)), 2)

    # The clamps above can leave the total short of the target on a badly
    # lopsided list, and the remainder is **spread across the beats that have
    # room** rather than dropped on the longest one. That version was written
    # first and is wrong in the way this whole module is about: it put 52
    # seconds on one beat of a collage explainer, past a per-beat ceiling that
    # exists because nobody watches one card for the better part of a minute.
    # A cap honoured everywhere except in the correction is not a cap.
    for _ in range(_REBALANCE_PASSES):
        drift = round(target - beats_seconds(beats), 2)
        if abs(drift) < 0.01:
            break
        room = [b for b in beats
                if (drift > 0 and float(b["seconds"]) < _MAX_BEAT_SECONDS)
                or (drift < 0 and float(b["seconds"]) > _MIN_BEAT_SECONDS)]
        if not room:
            # Genuinely unreachable — too few beats to fill the window, or too
            # many to fit in it. Left short rather than forced, because a list
            # this shape is already failing `validate()`'s beat count and the
            # honest total is what the duration check should read.
            break
        share = drift / len(room)
        for b in room:
            b["seconds"] = round(max(_MIN_BEAT_SECONDS,
                                     min(_MAX_BEAT_SECONDS,
                                         float(b["seconds"]) + share)), 2)
    return beats


def beats_seconds(beats) -> float:
    return round(sum(float((b or {}).get("seconds") or 0)
                     for b in (beats or []) if isinstance(b, dict)), 2)


def clean_source_kind(value) -> str:
    """One of the kinds this module offers, or the default.

    The closed-vocabulary rule its two neighbours in this file already follow:
    a value outside the set reaches code that has no branch for it and behaves
    as some default while the screen reports what was asked for. `_SOURCE_IDS`
    was written for exactly this and had no caller, which a review bot was
    right to name — a constant declared and never read is the shape this repo
    counts six of.
    """
    kind = str(value or "").strip().lower()
    return kind if kind in _SOURCE_IDS else DEFAULT_SOURCE_KIND


def _clean(value, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _seconds(value) -> float:
    try:
        return round(max(0.0, float(value)), 2)
    except (TypeError, ValueError):
        # "about 8" is what a model returns when the field is not typed. Read
        # as unstated rather than as zero — `rebalance()` gives it a share.
        return 0.0


# --------------------------------------------------------------------------- #
# the fallback outline — what comes back when nobody can ask a model
# --------------------------------------------------------------------------- #

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def outline_from_text(text: str, *, title: str = "",
                      total_seconds: float = TARGET_SECONDS) -> list[dict]:
    """A beat list built from the source text alone.

    Not a good explainer — a good one needs a model to decide what the point
    is. What it is instead is an honest one: every line in it came out of what
    somebody actually supplied, so nothing here can invent a claim about their
    business. `generate()` marks it `house` and the screen says so.
    """
    body = " ".join(str(text or "").split())
    sentences = [s.strip() for s in _SENTENCE.split(body) if len(s.strip()) > 20]
    beats: list[dict] = []
    if title:
        beats.append({"headline": _clean(title, 90), "support": "",
                      "treatment": "statement", "seconds": 0.0,
                      "source": "", "image_query": ""})
    for sentence in sentences[: MAX_BEATS - len(beats)]:
        # The first clause is the claim and the rest is the support, which is
        # how most written sentences are shaped. Where there is no comma the
        # whole sentence leads and nothing is invented to sit under it.
        head, _, tail = sentence.partition(",")
        beats.append({"headline": _clean(head, 90), "support": _clean(tail, 180),
                      "treatment": DEFAULT_TREATMENT, "seconds": 0.0,
                      "source": "", "image_query": _clean(head, 80)})
    return rebalance(beats, total_seconds=total_seconds)


# --------------------------------------------------------------------------- #
# what the model is asked, and what a screen is told
# --------------------------------------------------------------------------- #

def prompt_system() -> str:
    """The instruction. Every constraint in it is also checked on the way back.

    A prompt is a request, and "the model was told not to" is not evidence
    that it did not — the rule `hub/proposal_spec.py` states about Smart 1
    Labs and `hub/blog_spec.py` about a client's never-mention list.
    """
    return (
        "You write beat lists for Vox-style explainer videos: editorial "
        "collage pieces that make one argument in a numbered sequence of "
        "short, concrete claims. "
        f"Return a JSON object with a 'beats' array of {MIN_BEATS} to "
        f"{MAX_BEATS} objects. Each beat has: headline (a claim, under 90 "
        "characters, no trailing full stop), support (one sentence of "
        "evidence or consequence, under 180 characters), treatment (one of "
        + ", ".join(sorted(_TREATMENT_IDS)) + "), seconds (a number, roughly "
        "how long this beat needs on screen), source (who said it — required "
        "if treatment is 'quote', otherwise empty), and image_query (three or "
        "four words describing a picture for this beat). "
        "Use only what is in the material you are given. Do not invent "
        "statistics, prices, dates or quotations. If the material does not "
        "support a claim, leave that beat out rather than filling it in. "
        "Write American English."
    )


def platform_note(platform: str) -> str:
    """Why this format is not offered on a broadcast buy.

    Said in words rather than by the option simply not being there — an
    absent option reads as one nobody thought of.
    """
    if str(platform or "").strip().lower() in PLATFORMS:
        return ""
    return (f"A Vox explainer runs {VOX_MIN_SECONDS}–{VOX_MAX_SECONDS} seconds, "
            "which is a YouTube or social piece rather than a slot anybody "
            "sells on CTV. Build this one for YouTube or social, or pick a "
            "different commercial type for the broadcast buy.")


def check_spec() -> list[str]:
    """Fields this module declares that nothing downstream reads.

    The question `current_marketing.unanswered_keys()` asks, one module over:
    a beat field a rep can fill in that changes nothing is a form somebody
    fills in for no reason. Returns an empty list today, which is the only way
    it was worth adding.
    """
    findings: list[str] = []
    try:
        from hub import hyperframes
        params = hyperframes.vox_params(
            title="x",
            beats=[{k: "x" for k in _BEAT_KEYS}])
    except Exception as exc:                             # noqa: BLE001
        return [f"the beat list could not be composed into render parameters: {exc}"]

    sent = params.get("beats") or [{}]
    for key in _BEAT_KEYS:
        if key not in (sent[0] or {}):
            findings.append(f"beat field '{key}' is validated here and is not "
                            f"sent to the render service.")
    if not set(_TREATMENT_IDS) or DEFAULT_TREATMENT not in _TREATMENT_IDS:
        findings.append("the default treatment is not one of the treatments "
                        "the template draws.")
    return findings
