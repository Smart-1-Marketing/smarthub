"""Tones, voice characteristics and slot lengths.

Ported verbatim from Smart-1-Marketing/radio-studio (`src/catalog.js`) so a
spot written in the Hub reads the same as one written in the standalone
studio. ``direction`` steers the script model, ``banner_mood`` steers the
companion-banner artwork, ``line`` is the fallback banner headline.
"""

from __future__ import annotations

from hub import voice_casting

TONES = [
    {"id": "upbeat", "label": "Upbeat & Energetic", "blurb": "Bright, fast, feel-good.",
     "direction": "Bright, fast-paced, optimistic. Short punchy sentences. Smiling delivery.",
     "banner_mood": "sunlit, high-energy, saturated color, motion streaks", "line": "Turn it up."},
    {"id": "warm", "label": "Warm & Friendly", "blurb": "Like a neighbor, not a pitch.",
     "direction": "Warm, unhurried, personal. Second person. Feels like advice from a friend.",
     "banner_mood": "soft golden light, cozy, approachable", "line": "We saved you a seat."},
    {"id": "authority", "label": "Trusted Authority", "blurb": "Calm expertise, proof-led.",
     "direction": "Measured, credible, expert. Lead with proof and credentials. No hype words.",
     "banner_mood": "clean, architectural, confident contrast", "line": "Ask the experts."},
    {"id": "urgent", "label": "Urgent Deal", "blurb": "Deadline-driven, act now.",
     "direction": "Direct and time-boxed. Front-load the offer and the deadline. Hard close.",
     "banner_mood": "bold red-hot accents, ticking urgency, high contrast", "line": "Ends soon."},
    {"id": "playful", "label": "Fun & Playful", "blurb": "Light, cheeky, memorable.",
     "direction": "Light and cheeky. One surprising turn of phrase. Never mean-spirited.",
     "banner_mood": "pop-art color blocks, playful geometry", "line": "Yes, really."},
    {"id": "cinematic", "label": "Cinematic Epic", "blurb": "Trailer-sized, big stakes.",
     "direction": "Trailer voice. Big stakes, slow build, hard landing on the brand.",
     "banner_mood": "dramatic wide vista, deep shadow, epic scale", "line": "This changes everything."},
    {"id": "conversational", "label": "Conversational Neighbor", "blurb": "Casual, real, unscripted feel.",
     "direction": "Sounds unscripted. Contractions, natural rhythm, one aside. Not announcer-y.",
     "banner_mood": "natural daylight, candid, real-world texture", "line": "Let's talk."},
    {"id": "luxury", "label": "Luxury & Refined", "blurb": "Spare, elegant, unhurried.",
     "direction": "Spare and elegant. Few words, long pauses, nothing oversold.",
     "banner_mood": "matte black, gold leaf detail, minimal negative space", "line": "Quietly exceptional."},
    {"id": "bold", "label": "Bold & Confident", "blurb": "Declarative, no hedging.",
     "direction": "Declarative statements. No hedging, no qualifiers. Strong verbs.",
     "banner_mood": "stark type-forward design, heavy contrast", "line": "No small plans."},
    {"id": "heartfelt", "label": "Heartfelt & Emotional", "blurb": "Sincere, human, story-first.",
     "direction": "Sincere and human. Open on a small true moment, then the brand.",
     "banner_mood": "soft focus, warm human tones, gentle light", "line": "For the people who matter."},
    {"id": "quirky", "label": "Quirky Humor", "blurb": "Odd, funny, sticky.",
     "direction": "Odd and funny. One absurd premise carried straight through. Land the offer clean.",
     "banner_mood": "surreal collage, unexpected juxtaposition, bright", "line": "Weirdly good."},
    {"id": "newsread", "label": "Straight News Read", "blurb": "Clean, factual, no music bed.",
     "direction": "Clean factual read. Information density over persuasion. No adjectives that can't be proven.",
     "banner_mood": "editorial grid, restrained palette, newsroom clarity", "line": "Here's what's happening."},
    {"id": "sports", "label": "High-Energy Sports", "blurb": "Play-by-play adrenaline.",
     "direction": "Play-by-play energy. Building intensity, crowd-noise cadence, big finish.",
     "banner_mood": "stadium light flare, kinetic streaks, team-color intensity", "line": "Game on."},
    {"id": "calm", "label": "Calm & Reassuring", "blurb": "Low, steady, trustworthy.",
     "direction": "Low and steady. Reduce anxiety. Short reassuring sentences, soft close.",
     "banner_mood": "muted gradient, wide calm horizon, airy", "line": "Take a breath."},
    {"id": "nostalgic", "label": "Nostalgic Throwback", "blurb": "Retro warmth, analog feel.",
     "direction": "Retro warmth. Period-flavored phrasing, analog imagery, comfortable pacing.",
     "banner_mood": "vintage print texture, faded 70s palette, grain", "line": "Like it used to be."},
]

# The voice characteristics are hub/voice_casting.CHARACTERISTICS -- the
# Commercial Builder casts a read the same way, so the question is asked once
# and scored once. Re-exported under the old name: this module's app.py and
# its template read it from here.
VOICE_CHARACTERISTICS = voice_casting.CHARACTERISTICS

# Word budgets are the studio's, measured at the natural 2.6 words/second read
# `speech.WORDS_PER_SECOND` holds. Every other reader of these numbers reads
# them from here: the AI system prompt states them, the builder colors the
# word count against them and `hub/radio_spec.qc()` judges the script on them,
# and each of those was a hand-typed second copy of the table before now.
#
# THE :60 IS 140-170, NOT the 150-180 the build spec asked for. At this pace
# 180 words is a 69-second read, so a :60 written to the top of that range
# cannot be recorded inside its own slot -- it comes back over, gets tightened,
# and the budget that sent it there was ours. 170 words is 65 seconds, which is
# the same deliberate overshoot the :15 and :30 carry: `grade_duration()` flags
# a render more than 0.4s long, so the top of a budget is allowed to be a
# little over the clock and the measured read is what actually decides it.
DURATIONS = [
    {"seconds": 15, "key": "fifteen", "label": ":15",
     "word_target": "35-42 words", "low": 35, "high": 42},
    # A :30 is never a short tag. At the normal 2.6 words/second read this
    # floor is just over 25 seconds, leaving room for natural pauses.
    {"seconds": 30, "key": "thirty", "label": ":30",
     "word_target": "65-85 words (25+ second read)", "low": 65, "high": 85},
    {"seconds": 60, "key": "sixty", "label": ":60",
     "word_target": "140-170 words (50+ second read)", "low": 140, "high": 170},
]

# The pair every project has always produced. A :60 is opt-in rather than a
# third script on every job: writing one costs a model call and a slot nobody
# asked for, and a project saved before this existed carries no slot list at
# all -- `slots_of()` reads that as the pair rather than migrating rows nobody
# has re-opened, the rule `hub/target_areas.from_legacy()` works to.
DEFAULT_SLOTS = ("fifteen", "thirty")

SLOT_KEYS = tuple(d["key"] for d in DURATIONS)


def slots_of(row: dict | None) -> tuple[str, ...]:
    """Which lengths this project is writing, in clock order.

    Unknown keys are dropped rather than carried: a slot nothing can price or
    grade would reach the writer as a length with no budget behind it.
    """
    asked = list((row or {}).get("slots") or ())
    keys = tuple(k for k in SLOT_KEYS if k in asked)
    return keys or tuple(DEFAULT_SLOTS)


def budget_line() -> str:
    """The word budgets as one sentence, for the writer's system prompt.

    Derived rather than typed, because the prompt is what the model is
    actually held to and a stale copy of it there is a script written to a
    budget the checker no longer uses.
    """
    parts = []
    for slot in DURATIONS:
        floor = int(round(slot["low"] / 2.6))
        parts.append(f'a {slot["label"]} runs {slot["low"]}-{slot["high"]} words '
                     f'and at least {floor} seconds')
    return "; ".join(parts)


def tone_by_id(tone_id: str) -> dict | None:
    for tone in TONES:
        if tone["id"] == tone_id:
            return tone
    return None


def duration_by_key(key: str) -> dict | None:
    for slot in DURATIONS:
        if slot["key"] == key:
            return slot
    return None
