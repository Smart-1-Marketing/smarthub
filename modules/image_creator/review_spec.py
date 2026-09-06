"""What a client is asked about a finished graphic, and how several answers
resolve to one.

Data and arithmetic only, no Flask and no database in it — the shape
``hub/proposal_spec.py`` and ``modules/commercial_builder/review_spec.py``
already use, so the public page, the staff panel and the test all read one
description of what an answer means.

## Why a client sees anything at all

Before this, a finished graphic went out as an emailed PNG or a Slack
message, and whatever the client said back lived in that thread — so nothing
recorded which version they approved, who at the client approved it, or what
they asked changed on the round before. That is fine until somebody asks
"did the client actually sign off on this", and there is nothing to show
them.

## Four answers, not two — ported from ``modules/commercial_builder/
## review_spec.py``, which is the more complete of the two prior
## implementations of this pattern (the other is
## ``modules/ads_builder/spec.py``'s three-answer estimate). Not imported
## directly: ``modules/commercial_builder`` is a heavy package (HeyGen,
## render providers) that must be free to fail to import without taking
## Image Creator down with it, so this is its own small copy of the same
## vocabulary rather than a cross-module dependency.

Approve/reject forces "yes, but move the logo" into whichever end is
nearest. The vocabulary here is the one every proofing tool uses (Ziflow,
Frame.io, Wipster), so a client who has reviewed creative before already
knows what it means:

* **Approved** — ship it.
* **Approved with changes** — ship it, and apply these notes. Not asking to
  see it again.
* **Changes required** — do not ship it. Expecting another round.

The fourth state is **no answer yet**, and it is deliberately not a
decision: "not sent", "sent and ignored" and "they said no" are three
different situations, and only the third is a rejection. It draws grey
rather than as a fourth kind of bad.

## The most restrictive answer wins

A review link gets forwarded, and more than one person can answer it.
**Changes required** beats **approved with changes** beats **approved** —
taking the latest answer instead would let a colleague's casual "looks
good" overwrite the first reviewer's "you can't say that", and the graphic
would ship. Every answer is kept and shown with the name against it; only
the *verdict* is resolved.

## Rounds are counted, and the cap is not a wall

Four rounds. A fifth is where a project has stopped being a revision and
started being a different graphic, so it is **flagged for the Hub** rather
than refused — the client is served exactly as before, because stopping
them pushes the whole conversation into email, where none of this is
recorded.
"""
from __future__ import annotations

OUTCOMES = (
    ("approved", "Approved — this is good to go", "green",
     "Approved as sent.", False),
    ("approved_with_changes", "Approved with changes — go ahead once these are fixed",
     "yellow", "Approved, with notes to apply before it ships.", False),
    ("changes_required", "Changes required — I need to see it again", "red",
     "Rejected for this round; another version is expected.", True),
)

OUTCOME_KEYS = tuple(k for k, _, _, _, _ in OUTCOMES)
OUTCOME_LABELS = {k: label for k, label, _, _, _ in OUTCOMES}
OUTCOME_COLOURS = {k: color for k, _, color, _, _ in OUTCOMES}
OUTCOME_NOTES = {k: note for k, _, _, note, _ in OUTCOMES}
WANTS_ANOTHER_ROUND = {k for k, _, _, _, again in OUTCOMES if again}

NO_ANSWER = ""
NO_ANSWER_COLOUR = "gray"
NO_ANSWER_NOTE = "No answer yet."

# Most restrictive first — verdict() walks this in order and takes the first
# one anybody gave, which is what stops a second reviewer softening the
# first one's refusal.
PRECEDENCE = ("changes_required", "approved_with_changes", "approved")

# Filing puts the export in the client's gallery; a graphic the client has
# explicitly rejected must not get there. "Approved with changes" still
# files — blocking it would teach people to answer "approved" to get past
# the gate.
BLOCKS_FILING = {"changes_required"}

MAX_ROUNDS = 4


def is_outcome(value) -> bool:
    return str(value or "") in OUTCOME_KEYS


def outcome_color(outcome) -> str:
    return OUTCOME_COLOURS.get(str(outcome or ""), NO_ANSWER_COLOUR)


def outcome_note(outcome) -> str:
    return OUTCOME_NOTES.get(str(outcome or ""), NO_ANSWER_NOTE)


def decision_requires_name(outcome) -> bool:
    """All three real answers need a name and email attached — an approval
    nobody can be named for is one nobody is held to, and a change request
    with nobody attached to it is not actionable."""
    return is_outcome(outcome)


def verdict(decisions) -> dict:
    """One resolved answer from however many reviewers replied.

    ``decisions`` is a list of dicts each carrying at least ``outcome``.
    Anything that is not one of the three keys is ignored rather than
    treated as a refusal, so a row from before a key existed cannot silently
    block a delivery.
    """
    answered = [d for d in (decisions or []) if is_outcome((d or {}).get("outcome"))]
    if not answered:
        return {"outcome": NO_ANSWER, "color": NO_ANSWER_COLOUR,
                "note": NO_ANSWER_NOTE, "answered": 0, "by": "",
                "conflicting": False, "blocks_filing": False,
                "wants_another_round": False}

    given = {d["outcome"] for d in answered}
    resolved = next(k for k in PRECEDENCE if k in given)
    by = next((str(d.get("reviewer_name") or "").strip()
              for d in answered if d["outcome"] == resolved), "")
    return {
        "outcome": resolved,
        "color": outcome_color(resolved),
        "note": outcome_note(resolved),
        "answered": len(answered),
        "by": by,
        "conflicting": len(given) > 1,
        "blocks_filing": resolved in BLOCKS_FILING,
        "wants_another_round": resolved in WANTS_ANOTHER_ROUND,
    }


def round_state(round_no) -> dict:
    """Where this review sits against the cap, in words a client can read."""
    try:
        current = max(1, int(round_no or 1))
    except (TypeError, ValueError):
        current = 1
    over = current > MAX_ROUNDS
    if over:
        return {"round": current, "of": MAX_ROUNDS, "over": True,
                "label": f"Round {current}",
                "note": ("This graphic has been round more times than a "
                         "revision cycle usually runs. Nothing is blocked "
                         "— but it is worth a conversation rather than "
                         "another version."),
                "client_note": ""}
    return {
        "round": current, "of": MAX_ROUNDS, "over": False,
        "label": f"Round {current} of {MAX_ROUNDS}",
        "note": "",
        "client_note": ("This is the last scheduled round of changes, so "
                        "please put everything you need in this one reply.")
        if current == MAX_ROUNDS else "",
    }


def clean_comment(body) -> dict:
    """One comment, bounded. ``text`` is empty when there is nothing in it —
    the caller decides what to do with that."""
    body = body or {}
    return {
        "text": str(body.get("text") or "").strip()[:2000],
        "reviewer_name": str(body.get("name") or "").strip()[:200],
        "reviewer_email": str(body.get("email") or "").strip()[:200],
    }


# ---------------------------------------------------------------------------
# Who is waiting on whom — there is no mailer in this Hub, so the honest
# route (hub/social_content.py's own note) is a figure where people already
# look rather than an email nobody would receive.
# ---------------------------------------------------------------------------
WAITING_ON_US = "answered"
WAITING_ON_THEM = "sent"


def inbox(rows) -> dict:
    """What has come back from clients, and what is still out with them.

    An answer is not only a decision — a client who left a comment and
    pressed no button has answered. A filed graphic is not waiting on
    anybody. And "nothing waiting" and "nothing ever sent" are different
    empties; only the first is a state to act on.
    """
    rows = list(rows or [])
    ours, theirs = [], []
    for row in rows:
        if row.get("filed"):
            continue
        answered = bool(row.get("answered")) or bool(row.get("comments"))
        (ours if answered else theirs).append(row)

    if ours:
        state = "waiting"
    elif theirs:
        state = "out_with_clients"
    elif rows:
        state = "all_handled"
    else:
        state = "never_sent"
    return {
        "waiting": ours, "out_with_clients": theirs,
        "waiting_count": len(ours), "out_count": len(theirs),
        "state": state, "line": INBOX_LINES[state], "measured": True,
    }


INBOX_LINES = {
    "waiting": "Clients have answered and these have not been filed.",
    "out_with_clients": "Nothing has come back yet — these are with the client.",
    "all_handled": "Every answer that has come back has been acted on.",
    "never_sent": "Nothing has been sent to a client for review yet.",
}


def inbox_unmeasured(reason) -> dict:
    """"Nobody has answered" and "we could not look" are different
    answers — a card drawing a clean zero over a failed read is exactly the
    confident wrong answer this codebase refuses to produce."""
    return {"waiting": [], "out_with_clients": [], "waiting_count": 0,
            "out_count": 0, "state": "not_measured", "measured": False,
            "line": f"The review rounds could not be read: {reason}"}
