"""Fan Radio — what a project can be made of.

The dayparts, the tones and the result-neutral rule are this tool's own. The
**lengths** are not: they are `hub/radio_spec.DURATIONS`, the same table the
Radio Ad Creator writes to, read rather than restated.

That is a fix rather than a tidy-up. This file used to carry its own two-entry
copy, and the docstring above it claimed the budgets were "the same clock Radio
Promo and the Commercial Builder use, so a script written here drops straight
into either without re-timing". They were not the same: a :15 was 30-38 words
here and 35-42 there, a :30 was 65-75 here and 65-85 there. So a script that
read as *on the clock* in one tool read as short or long in the other, each
screen internally consistent, with the README promising they agreed. Nothing
errored, and the only way to notice was to open both.

Reading the shared table also brings the two lengths this tool could not build
at all: the **:10** sponsorship tag every station sells against a live read,
and the **:60**, the one radio length with room for a story rather than an
offer.

They are budgets, not limits. A long read is flagged with the number of words
to cut and re-tightened — never truncated, because trimming clips a word off
the end of the phone number.
"""
from __future__ import annotations

from hub import radio_spec

DAYPARTS = [
    {
        "id": "pregame",
        "label": "Pre-Game Prep",
        "when": "Thursday through Saturday, and the morning of the game",
        "job": "Build-up. The listener is planning: shopping, booking, "
               "prepping the house, getting the truck ready.",
        "angle": "Get it handled before kickoff. Beat the rush.",
        "cta_shape": "Book it / order it / stop in before the weekend.",
    },
    {
        "id": "gameday",
        "label": "Game Day",
        "when": "day of — pregame show through the fourth quarter",
        "job": "Energy and immediacy. The listener is at the tailgate, in "
               "the truck, or has the game on in the background.",
        "angle": "Right now, today, while it's happening.",
        "cta_shape": "Today only / open till kickoff / we're open now.",
    },
    {
        "id": "postgame",
        "label": "Post-Game",
        "when": "final whistle through Monday drive",
        "job": "Wind-down and recap. The listener is dissecting what "
               "happened — or avoiding it.",
        "angle": "However it ended, here's what's next.",
        "cta_shape": "Monday special / this week only / call us tomorrow.",
    },
]
DAYPART_IDS = [d["id"] for d in DAYPARTS]

# The length menu, from the one shared table. Keyed on seconds because that is
# what a Fan Radio spot carries on its own row -- `radio_spec.duration_by_seconds()`
# is the lookup written for exactly this, so one table answers both builders
# without either storing the other's key.
LENGTHS = {d["seconds"]: {"min": d["low"], "max": d["high"], "label": d["label"],
                          "key": d["key"], "name": d["name"], "note": d["note"],
                          "cost": d["cost"], "word_target": d["word_target"],
                          "min_seconds": d.get("min_seconds"),
                          "warning": d.get("warning", "")}
           for d in radio_spec.DURATIONS}
LENGTH_IDS = [d["seconds"] for d in radio_spec.DURATIONS]

# The pair every project has written since this tool existed, and still the
# default. The :10 and the :60 are opt-in for the reason the shared table gives:
# each is a model call and a slot nobody asked for, and here it is that per
# daypart -- ticking all four across three dayparts is twelve billed writes for
# a job that usually wants six.
DEFAULT_LENGTH_IDS = [15, 30]

# Post-game only. A spot booked for after the whistle is voiced days before
# it airs, so 'neutral' is the default and the alternates are opt-in extras
# the station can swap in once the result is known.
OUTCOMES = [
    {"id": "neutral", "label": "Result-neutral",
     "note": "Airs whatever the score. This is the one you can book blind."},
    {"id": "win", "label": "If it went well",
     "note": "Optional alternate. Only book this once the result is in."},
    {"id": "loss", "label": "If it didn't",
     "note": "Optional alternate. Keeps the spot from sounding tone-deaf "
             "after a bad one."},
]
OUTCOME_IDS = [o["id"] for o in OUTCOMES]

# Same fifteen tones as Radio Promo, so a client sounds like themselves
# across both tools.
TONES = [
    {"id": "warm", "label": "Warm & neighborly",
     "hint": "Family business, been here forever."},
    {"id": "urgent", "label": "Urgent",
     "hint": "Deadline, limited stock, ends Sunday."},
    {"id": "authority", "label": "Authority",
     "hint": "The experts. Certified, licensed, trusted."},
    {"id": "hype", "label": "High energy",
     "hint": "Loud, fast, gameday adrenaline."},
    {"id": "playful", "label": "Playful",
     "hint": "Light jokes, banter, ribbing the listener."},
    {"id": "underdog", "label": "Underdog grit",
     "hint": "Hard work, blue collar, earn it."},
    {"id": "celebration", "label": "Celebration",
     "hint": "Something good happened. Mark it."},
    {"id": "straight", "label": "Straight read",
     "hint": "No theatre. Information, clean."},
    {"id": "conversational", "label": "Conversational",
     "hint": "One person talking to one person."},
    {"id": "nostalgic", "label": "Nostalgic",
     "hint": "Seasons past, tradition, the way it's always been."},
    {"id": "value", "label": "Value / deal",
     "hint": "Price forward. The number is the hook."},
    {"id": "premium", "label": "Premium",
     "hint": "Considered, unhurried, quality first."},
    {"id": "coach", "label": "Coach's voice",
     "hint": "Direct instruction. Do this, then this."},
    {"id": "color", "label": "Color commentary",
     "hint": "Play-by-play cadence, calling the action."},
    {"id": "family", "label": "Family & kids",
     "hint": "Whole household, weekend plans."},
]
TONE_IDS = [t["id"] for t in TONES]


def daypart(did: str) -> dict:
    for d in DAYPARTS:
        if d["id"] == did:
            return d
    return DAYPARTS[1]


def tone(tid: str) -> dict:
    for t in TONES:
        if t["id"] == tid:
            return t
    return TONES[0]


def budget(seconds: int) -> dict:
    return LENGTHS.get(int(seconds), LENGTHS[30])


def grade(script: str, seconds: int) -> dict:
    """Word count against the budget for that length.

    One line, because the verdict is `radio_spec.grade_words()` -- the same
    reading the Radio Ad Creator colours its counts against. Two functions
    answering *does this script fit* is how the two tools came to disagree
    about a :15 in the first place.
    """
    return radio_spec.grade_words(script, seconds)


def length_warning(seconds: int) -> str:
    """The warning for a length, or empty where there is none.

    Empty rather than a reassurance: a note on every length is a note nobody
    reads, and then the one that mattered goes past unread too. Only the :60
    carries one, because only the :60 costs twice a :30 every time it is
    re-recorded.
    """
    return (LENGTHS.get(int(seconds or 0)) or {}).get("warning", "")


def default_slots() -> list[dict]:
    """Six spots: three dayparts, the :15/:30 pair each.

    Deliberately not every length the menu now offers. The :10 and the :60 are
    on the picker and are a tick away; defaulted on, they would be twelve
    billed writes on a job that asked for six.
    """
    return [{"daypart": d, "seconds": s, "outcome": "neutral"}
            for d in DAYPART_IDS for s in DEFAULT_LENGTH_IDS]
