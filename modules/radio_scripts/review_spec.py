"""What a client is asked about a set of radio scripts, and how answers resolve.

Data and arithmetic only, no Flask in it, for the reason `hub/proposal_spec.py`
sits beside `hub/rate_card.py`: the public page, the staff panel and the test
all read one description of what a decision means.

Named `review_spec` rather than `review` for the exact reason
`modules/commercial_builder/review_spec.py`'s own docstring gives: a sibling
`review.py` holding the routes would shadow it the moment anything did
`from . import review` and expected this module instead — `__init__.py`
importing a routes module under that name is what does it, silently, with no
error until the first call to a function that is not there.

**A small copy, not an import of `modules/commercial_builder`'s.** That
module is a heavy package (HeyGen, render providers, several dependencies
that can fail) which must be free to fail to import without taking a radio
tool down with it — the same reasoning `modules/image_creator/review_spec.py`
gives for carrying its own copy rather than reaching across. Three modules
now share this vocabulary and none of them shares code for it.

## Why the client sees anything at all

A rep generates three concepts and, until this, the only way a client heard
them was a phone read-through or a pasted script in an email — so nothing
recorded which concept the client picked, who approved it, or what they
asked changed. That is fine until somebody asks "did the client actually
sign off on this wording", and there is nothing to show them.

## Four answers, not two

Approve/reject forces "yes, but drop the discount line" into whichever end
is nearest — the rule `modules/ads_builder/spec.py` arrived at for the
paid-search estimate, and the vocabulary here is the one Commercial Builder
and Image Creator already use, so a client who has reviewed either kind of
creative before already knows what it means:

* **Approved** — write the spot from this.
* **Approved with changes** — write it, and apply these notes.
* **Changes required** — do not use this yet. They want to see it again.

The fourth state is **no answer yet**, and it is deliberately not a
decision — "not sent", "sent and ignored" and "they said no" are three
different situations, and only the third is a refusal.

## The most restrictive answer wins

A review link gets forwarded, and more than one person answers it.
**Changes required** beats **approved with changes** beats **approved**:
taking the latest reply instead would let a colleague's "sounds good"
overwrite the first reviewer's "we can't say that", and the wrong script
would go to production. Every answer is kept and shown with the name
against it; only the *verdict* is resolved.

## No round cap

Commercial Builder and Image Creator both cap at four rounds and flag rather
than refuse a fifth, because a re-render or a re-composited graphic costs
real time and money and a spot re-cut eleven times against a fixed fee is a
conversation somebody should have. A script is text: `create_set()` costs one
model call and `api_regenerate()` costs another, and a client saying "try
again" a sixth time is not a symptom of anything gone wrong. There is no cap
here on purpose — round numbers are still counted, because "the third time
they've asked for changes" is worth being able to say, but nothing here flags
it or serves the client differently for it.
"""
from __future__ import annotations

# (key, what the client sees, colour in the Hub, what it means for the rep,
#  whether another round is expected)
OUTCOMES = (
    ("approved", "Approved — write the spot from this", "green",
     "Approved as sent.", False),
    ("approved_with_changes", "Approved with changes — write it, applying these notes",
     "yellow", "Approved, with notes to apply.", False),
    ("changes_required", "Changes required — I need to hear it again", "red",
     "Rejected for this round; they expect another pass.", True),
)

OUTCOME_KEYS = tuple(k for k, _, _, _, _ in OUTCOMES)
OUTCOME_LABELS = {k: label for k, label, _, _, _ in OUTCOMES}
OUTCOME_COLOURS = {k: color for k, _, color, _, _ in OUTCOMES}
OUTCOME_NOTES = {k: note for k, _, _, note, _ in OUTCOMES}
WANTS_ANOTHER_ROUND = {k for k, _, _, _, again in OUTCOMES if again}

# Not a fourth decision. A client who has not answered has not rejected
# anything, and drawing that red is how a panel of red stops being read.
NO_ANSWER = ""
NO_ANSWER_COLOUR = "gray"
NO_ANSWER_NOTE = "No answer yet."

# Most restrictive first. `verdict()` walks this in order and takes the first
# one anybody gave, which is what makes a second reviewer unable to soften
# the first one's refusal.
PRECEDENCE = ("changes_required", "approved_with_changes", "approved")


def is_outcome(value) -> bool:
    return str(value or "") in OUTCOME_KEYS


def outcome_color(outcome) -> str:
    return OUTCOME_COLOURS.get(str(outcome or ""), NO_ANSWER_COLOUR)


def outcome_note(outcome) -> str:
    return OUTCOME_NOTES.get(str(outcome or ""), NO_ANSWER_NOTE)


def verdict(decisions) -> dict:
    """One answer from however many reviewers replied.

    `decisions` is a list of dicts each carrying at least `outcome`; anything
    that is not one of the three keys is ignored rather than treated as a
    refusal, because a row written before a key existed must not silently
    read as a rejection.
    """
    answered = [d for d in (decisions or []) if is_outcome((d or {}).get("outcome"))]
    if not answered:
        return {"outcome": NO_ANSWER, "color": NO_ANSWER_COLOUR,
                "note": NO_ANSWER_NOTE, "answered": 0, "by": "",
                "conflicting": False, "wants_another_round": False}

    given = {d["outcome"] for d in answered}
    resolved = next(k for k in PRECEDENCE if k in given)
    # The first person who gave the resolved answer, in the order recorded.
    # Naming the earliest rather than the latest matters on a refusal: it is
    # who raised it, and they are who somebody rings.
    by = next((str(d.get("reviewer_name") or "").strip()
               for d in answered if d["outcome"] == resolved), "")
    return {
        "outcome": resolved,
        "color": outcome_color(resolved),
        "note": outcome_note(resolved),
        "answered": len(answered),
        "by": by,
        # Not "several people answered" — several people answered
        # DIFFERENTLY, which is the only case a rep has to read the
        # individual rows for.
        "conflicting": len(given) > 1,
        "wants_another_round": resolved in WANTS_ANOTHER_ROUND,
    }


def round_label(round_no) -> str:
    try:
        current = max(1, int(round_no or 1))
    except (TypeError, ValueError):
        current = 1
    return f"Round {current}"


CONCEPT_LABELS = ("Concept 1", "Concept 2", "Concept 3")
LENGTH_LABELS = {"60": ":60", "30": ":30", "15": ":15"}


def concept_label(index) -> str:
    """`Concept 2` from a zero-based index, blank if it names nothing.

    Used the way `timecode()` is used in the video review pages — a comment
    that names no concept is about the set as a whole, and rendering that as
    "Concept 1" would put every general note against the first one, where the
    reader looks for something that is not there.
    """
    try:
        i = int(index)
    except (TypeError, ValueError):
        return ""
    if 0 <= i < len(CONCEPT_LABELS):
        return CONCEPT_LABELS[i]
    return ""


def length_label(key) -> str:
    return LENGTH_LABELS.get(str(key or "").strip(), "")


def clean_comment(body) -> dict:
    """One comment, bounded. Returns `text` empty when there is nothing in it.

    Every field is truncated here rather than at the column, because this is
    written by somebody with no Hub login: the caller decides what to do with
    an empty comment, and nothing downstream has to guess at a length.

    `concept_index` and `length_key` place a note against one script rather
    than the whole set — a set carries three concepts at three lengths each,
    and "the :15 is too pushy" is only true of one of the nine.
    """
    body = body or {}
    idx = body.get("concept_index")
    try:
        idx = None if idx in (None, "") else max(0, min(2, int(idx)))
    except (TypeError, ValueError):
        idx = None
    length = str(body.get("length_key") or "").strip()
    if length not in LENGTH_LABELS:
        length = ""
    return {
        "text": str(body.get("text") or "").strip()[:2000],
        "reviewer_name": str(body.get("name") or "").strip()[:200],
        "reviewer_email": str(body.get("email") or "").strip()[:200],
        "concept_index": idx,
        "length_key": length,
    }


def decision_requires_name(outcome) -> bool:
    """Whether this answer is worth nothing without a person attached to it.

    All three, for the reason `modules/ads_builder/spec.py` gives about
    change requests: "the client wants a shorter offer line" is not
    actionable on its own, and three people at one company will disagree
    with each other. An anonymous approval is exactly the argument this
    whole module exists to be able to settle later.
    """
    return is_outcome(outcome)
