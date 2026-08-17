"""Fan Radio — what a project can be made of.

Word budgets are the same clock Radio Promo and the Commercial Builder use,
so a script written here drops straight into either without re-timing:

    :15  →  30–38 words
    :30  →  65–75 words

They are budgets, not limits. A long read is flagged with the number of
words to cut and re-tightened — never truncated, because trimming clips a
word off the end of the phone number.
"""
from __future__ import annotations

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

LENGTHS = {
    15: {"min": 30, "max": 38, "label": ":15"},
    30: {"min": 65, "max": 75, "label": ":30"},
}
LENGTH_IDS = [15, 30]

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
    {"id": "warm", "label": "Warm & neighbourly",
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
    {"id": "colour", "label": "Colour commentary",
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
    """Word count against the budget for that length."""
    words = len([w for w in str(script or "").split() if w.strip()])
    b = budget(seconds)
    if words > b["max"]:
        return {"words": words, "state": "long", "delta": words - b["max"],
                "note": f"{words - b['max']} word(s) over a {b['label']} read."}
    if words < b["min"]:
        return {"words": words, "state": "short", "delta": b["min"] - words,
                "note": f"{b['min'] - words} word(s) short — there's room."}
    return {"words": words, "state": "ok", "delta": 0,
            "note": f"On the clock for a {b['label']}."}


def default_slots() -> list[dict]:
    """Six spots: three dayparts, two lengths each."""
    return [{"daypart": d, "seconds": s, "outcome": "neutral"}
            for d in DAYPART_IDS for s in LENGTH_IDS]
