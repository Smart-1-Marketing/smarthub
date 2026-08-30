"""What a client is asked about a finished cut, and how several answers resolve.

Data and arithmetic only, no Flask in it, for the reason `hub/proposal_spec.py`
sits beside `hub/rate_card.py`: the public page, the staff panel, the filing
gate and the test all read one description of what a decision means.

Named `review_spec` rather than `review` because `routes/review.py` exists and
`__init__.py` does `from .routes import (..., review)` — which binds the name
`review` **on the package**, so `from . import review` in any sibling silently
resolves to the route module instead of this one. Nothing errors at import;
the first call to a function that is not there is where it surfaces, and only
if that path is exercised. `hub/proposal_spec.py` and `hub/blog_spec.py`
carry the same suffix for the same kind of reason.

## Why the client sees anything at all

The tool renders a commercial and a **rep** presses Approve & file. The client
sees it when the account manager emails an MP4 or a Cloudinary link, replies
with three changes in the body of an email, and somebody retypes them into a
storyboard — so nothing anywhere records which cut the client approved, who at
the client approved it, or what they asked for on the round before. That is
fine until a client says "we never signed off on that", and then there is
nothing to show them.

## Four answers, not two

Approve/reject forces "yes, but fix the phone number" into whichever end is
nearest, which is the rule `modules/ads_builder/spec.py` arrived at for the
paid-search estimate. The vocabulary here is the one every proofing tool uses
(Ziflow, Frame.io, Wipster), because a client who has reviewed video before
already knows what it means:

* **Approved** — ship it.
* **Approved with changes** — ship it, and apply these notes. The client is
  not asking to see it again.
* **Changes required** — do not ship it. They want another round.

The fourth state is **no answer yet**, and it is deliberately not a decision:
"not sent", "sent and ignored" and "they said no" are three different
situations, and only the third is a rejection. It draws grey rather than as a
fourth kind of bad, the note `modules/ads_builder/spec.py` makes about the
approval hub.

## The most restrictive answer wins

A review link gets forwarded — the marketing manager sends it to the owner,
and both answer. Ziflow resolves that by precedence rather than by recency,
and so does this: **changes required** beats **approved with changes** beats
**approved**. Taking the latest answer instead would let a second reviewer's
casual "looks good" overwrite the compliance officer's "you cannot say that",
and the cut would ship. Every answer is kept and shown with the name against
it; only the *verdict* is resolved.

## Rounds are counted, and the cap is not a wall

Four rounds. A fifth is where a project has stopped being a revision and
started being a different commercial, and the cost of that conversation not
happening is a spot re-cut eleven times against a fixed fee.

But the client is the wrong person to stop. Refusing them the page means the
rep sends the file by email instead and every rule above is lost — the record,
the timecodes, the name against the decision. So round five **flags the
project for the Hub** and serves the client exactly as before. The page says
which round it is (`Round 2 of 4`) because a client who can see they are on
the last round asks for everything at once, which is the entire point.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# The decisions
# ---------------------------------------------------------------------------
# (key, what the client sees, color in the Hub, what it means for the rep,
#  whether another round is expected)
OUTCOMES = (
    ("approved", "Approved — this is good to go", "green",
     "Approved as sent.", False),
    ("approved_with_changes", "Approved with changes — go ahead once these are fixed",
     "yellow", "Approved, with notes to apply before it ships.", False),
    ("changes_required", "Changes required — I need to see it again", "red",
     "Rejected for this round; they expect another cut.", True),
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
# one anybody gave, which is what makes a second reviewer unable to soften the
# first one's refusal.
PRECEDENCE = ("changes_required", "approved_with_changes", "approved")

# What a decision permits. Filing is what puts a commercial in the client's
# library and on their 360 record, so a cut the client has explicitly refused
# must not get there — but "approved with changes" is an approval and blocking
# it would teach people to answer "approved" to get past the gate.
BLOCKS_FILING = {"changes_required"}

MAX_ROUNDS = 4


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
    block a delivery.

    Returns the resolved outcome, who gave it, and whether more than one
    person answered — the last of those because "one person approved" and
    "three people answered and one of them refused" read identically once
    they have been collapsed into a single word, and the panel has to be able
    to say which happened.
    """
    answered = [d for d in (decisions or []) if is_outcome((d or {}).get("outcome"))]
    if not answered:
        return {"outcome": NO_ANSWER, "color": NO_ANSWER_COLOUR,
                "note": NO_ANSWER_NOTE, "answered": 0, "by": "",
                "conflicting": False, "blocks_filing": False,
                "wants_another_round": False}

    given = {d["outcome"] for d in answered}
    resolved = next(k for k in PRECEDENCE if k in given)
    # The first person who gave the resolved answer, in the order they were
    # recorded. Naming the earliest rather than the latest matters on a
    # refusal: it is who raised it, and they are who somebody rings.
    by = next((str(d.get("reviewer_name") or "").strip()
               for d in answered if d["outcome"] == resolved), "")
    return {
        "outcome": resolved,
        "color": outcome_color(resolved),
        "note": outcome_note(resolved),
        "answered": len(answered),
        "by": by,
        # Not "several people answered" — several people answered DIFFERENTLY,
        # which is the only case a rep has to read the individual rows for.
        "conflicting": len(given) > 1,
        "blocks_filing": resolved in BLOCKS_FILING,
        "wants_another_round": resolved in WANTS_ANOTHER_ROUND,
    }


def round_state(round_no) -> dict:
    """Where this review sits against the cap, in words a client can read.

    `over` is the flag, and it is deliberately not a refusal: the client is
    served exactly as before and the Hub is told. See the module docstring —
    stopping the client is what pushes the whole conversation back into email.
    """
    try:
        current = max(1, int(round_no or 1))
    except (TypeError, ValueError):
        current = 1
    over = current > MAX_ROUNDS
    if over:
        return {"round": current, "of": MAX_ROUNDS, "over": over,
                "label": f"Round {current}",
                "note": ("This spot has been round more times than a revision "
                         "cycle usually runs. Nothing is blocked — but it is "
                         "worth a conversation rather than another cut."),
                "client_note": ""}
    return {
        "round": current, "of": MAX_ROUNDS, "over": False,
        "label": f"Round {current} of {MAX_ROUNDS}",
        "note": "",
        # Said to the client on the last round, and only then. A note on round
        # one reads as us managing them; on round four it is the reason to put
        # everything in one reply.
        "client_note": ("This is the last scheduled round of changes, so please "
                        "put everything you need in this one reply.")
        if current == MAX_ROUNDS else "",
    }


def timecode(seconds) -> str:
    """`0:12`, from a number of seconds. Empty for a comment with no point.

    A comment on a whole cut is a real thing to leave — "the music is too
    loud" is not at a timestamp — so no timecode is an answer rather than a
    zero. Rendering it as `0:00` would put every general note at the first
    frame, where the reader looks for something that is not there.
    """
    if seconds is None or seconds == "":
        return ""
    try:
        total = float(seconds)
    except (TypeError, ValueError):
        return ""
    if total < 0:
        return ""
    whole = int(total)
    return f"{whole // 60}:{whole % 60:02d}"


def clean_comment(body) -> dict:
    """One comment, bounded. Returns `text` empty when there is nothing in it.

    Every field is truncated here rather than at the column, because this is
    written by somebody with no Hub login: the caller decides what to do with
    an empty comment, and nothing downstream has to guess at a length.
    """
    body = body or {}
    at = body.get("at_seconds")
    try:
        at = None if at in (None, "") else max(0.0, round(float(at), 2))
    except (TypeError, ValueError):
        at = None
    return {
        "text": str(body.get("text") or "").strip()[:2000],
        "reviewer_name": str(body.get("name") or "").strip()[:200],
        "reviewer_email": str(body.get("email") or "").strip()[:200],
        "at_seconds": at,
        "format": str(body.get("format") or "").strip()[:10],
    }


def decision_requires_name(outcome) -> bool:
    """Whether this answer is worth nothing without a person attached to it.

    All three, and for the reason `modules/ads_builder/spec.py` gives about
    change requests: "the client wants the phone number bigger" is not
    actionable, and three people at one company will disagree with each other.
    It is stricter here than there, because an approval is the thing somebody
    is held to later — an anonymous sign-off on a spot that then runs is
    exactly the argument this whole module exists to be able to settle.
    """
    return is_outcome(outcome)
