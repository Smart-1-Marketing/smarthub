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

# ---------------------------------------------------------------------------
# The slots a radio spot is sold in.
#
# The studio shipped the pair — a :15 and a :30 written together so they share
# a hook — and that pair is still the default, because it is what most streaming
# buys are sold as. It was also the whole menu, which left the two units either
# side of it unbuildable in this tool: a :10 is the sponsorship tag every
# station sells against a live read, and the :60 is the classic radio unit, the
# one length with room for a story rather than an offer.
#
# Word budgets are measured at speech.WORDS_PER_SECOND, the natural commercial
# read pace, and the two that were already here are unchanged.
#
# `min_seconds` is a floor on the read, and it is deliberately set on the long
# slots only. A :30 or a :60 is bought and billed by the second, so a read that
# lands well under it is dead air somebody paid for — that is what the existing
# 25-second rule on the :30 was for, and it is kept exactly. A :10 or a :15 is a
# tag: it is naturally tight, and a floor there would refuse correct copy, which
# is the crying wolf that gets a check switched off.
# ---------------------------------------------------------------------------
DURATIONS = [
    {"seconds": 10, "key": "ten", "word_target": "22-28 words",
     "low": 22, "high": 28, "min_seconds": None,
     "label": "Sponsorship tag",
     "note": "One idea and the brand. No offer, no phone number — ten seconds "
             "cannot carry a response somebody acts on.",
     "cost": "Cheapest read on the menu — roughly a third of a :30 in "
             "voiceover characters."},
    {"seconds": 15, "key": "fifteen", "word_target": "35-42 words",
     "low": 35, "high": 42, "min_seconds": None,
     "label": "Standard short",
     "note": "One message, one call to action. The brand said at least once, "
             "the address last.",
     "cost": "Low — about half a :30 in voiceover characters."},
    # A :30 is never a short tag. At the normal 2.6 words/second read this
    # floor is just over 25 seconds, leaving room for natural pauses.
    {"seconds": 30, "key": "thirty",
     "word_target": "65-85 words",
     "low": 65, "high": 85, "min_seconds": 25,
     "label": "The workhorse",
     "note": "The unit most streaming audio is sold in. Hook, value, close, "
             "with the brand said at least twice.",
     "cost": "Moderate — the length every other read here is priced against."},
    {"seconds": 60, "key": "sixty",
     "word_target": "140-165 words",
     "low": 140, "high": 165, "min_seconds": 52,
     "label": "Long form",
     "note": "Room for a story, a testimonial or a real explanation rather "
             "than an offer. Worth it where the listener is already yours.",
     "cost": "Roughly twice a :30 in voiceover characters.",
     "warning": "A :60 is about twice a :30 in voiceover characters, and "
                "ElevenLabs bills the character — so every re-record of it "
                "costs twice as much too. Build one where the air is bought "
                "for it, and cut a :30 or :15 alongside for everywhere else."},
]

# What a project writes when nobody chose. The pair the studio shipped, and
# what nearly every buy is sold as.
DEFAULT_SLOTS = ("fifteen", "thirty")

SLOT_KEYS = tuple(slot["key"] for slot in DURATIONS)


def budget_line(slot_key: str) -> str:
    """The word budget and any read floor, in one sentence.

    Composed rather than written into `word_target` twice: the screen and the
    script prompt both print this, and a budget stated in two places is the
    one that drifts.
    """
    slot = duration_by_key(slot_key) or {}
    words = slot.get("word_target") or ""
    floor = slot.get("min_seconds")
    if not floor:
        return words
    return f"{words}, and at least {floor:g} seconds at a natural read pace"


def length_warning(slot_key: str) -> str:
    """The warning for a slot, or empty where there is none.

    Empty rather than a cheerful reassurance: a note on every length is a note
    nobody reads, and then the one that mattered goes past unread too.
    """
    return (duration_by_key(slot_key) or {}).get("warning", "")


def normalize_slots(keys) -> list:
    """The slots a project writes, in slot order, deduped, never empty.

    Ordered by length rather than by the order somebody ticked them, so a
    :60 and a :15 come back the same way round however they were picked, and
    an unknown key is dropped rather than reaching the prompt: a slot this
    catalog cannot describe is one nothing downstream can budget or grade.
    """
    wanted = {str(k or "").strip() for k in (keys or [])}
    out = [k for k in SLOT_KEYS if k in wanted]
    return out or list(DEFAULT_SLOTS)


# ---------------------------------------------------------------------------
# The beats a read is built on.
#
# The Commercial Builder has shown its structure above the storyboard since it
# was written (config.STRUCTURE_TEMPLATES) and radio had none — so the shape of
# a read lived in the prompt, where a rep could not see it and could not tell a
# script that had wandered from one that was written to a plan.
#
# Same shape as the Commercial Builder's, deliberately, so somebody moving
# between the two tools is reading one idea. The guidance is radio's own: there
# is no picture, so every beat has to earn its seconds in words.
# ---------------------------------------------------------------------------
STRUCTURE_TEMPLATES = {
    "ten": [
        {"label": "Brand + one line", "start_pct": 0, "end_pct": 100,
         "guidance": "Name the business and say one thing about it. No offer, "
                     "no phone number, no second idea — this is recall, and "
                     "ten seconds is gone before anybody can act on it."},
    ],
    "fifteen": [
        {"label": "Hook", "start_pct": 0, "end_pct": 27,
         "guidance": "One line that makes somebody stop scrolling past the "
                     "audio. A question, a pain point, or the offer itself."},
        {"label": "Offer", "start_pct": 27, "end_pct": 73,
         "guidance": "What they get and why it is worth hearing out. One "
                     "benefit, not three — a :15 has room for exactly one."},
        {"label": "Call", "start_pct": 73, "end_pct": 100,
         "guidance": "The brand and the address, last, said clean and "
                     "unhurried. The last thing heard is the thing recalled."},
    ],
    "thirty": [
        {"label": "Hook", "start_pct": 0, "end_pct": 20,
         "guidance": "Open on the listener's problem or the moment the offer "
                     "solves. Do not open on the company name."},
        {"label": "Value", "start_pct": 20, "end_pct": 70,
         "guidance": "The offer and the proof behind it. This is where the "
                     "brand name is said the first of its two times."},
        {"label": "Call", "start_pct": 70, "end_pct": 100,
         "guidance": "Brand, address, and any disclaimer word for word before "
                     "it. Leave the last beat unhurried — a rushed URL is a "
                     "URL nobody caught."},
    ],
    "sixty": [
        {"label": "Open", "start_pct": 0, "end_pct": 15,
         "guidance": "Set a scene or a moment. A :60 is the one radio length "
                     "with room to earn attention rather than grab it."},
        {"label": "Story", "start_pct": 15, "end_pct": 55,
         "guidance": "The narrative, the testimonial or the real explanation. "
                     "This is the beat that does not exist in any shorter cut "
                     "and the only reason to buy this length."},
        {"label": "Offer", "start_pct": 55, "end_pct": 82,
         "guidance": "Land the offer and the proof. By here the listener has "
                     "given you forty seconds — say something specific."},
        {"label": "Call", "start_pct": 82, "end_pct": 100,
         "guidance": "Brand, address, disclaimer word for word. Say the "
                     "address twice if it is hard to spell."},
    ],
}


def structure_for(slot_key: str) -> list:
    """The beats for a slot, or the :30's where a slot has none of its own."""
    return STRUCTURE_TEMPLATES.get(slot_key, STRUCTURE_TEMPLATES["thirty"])


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
