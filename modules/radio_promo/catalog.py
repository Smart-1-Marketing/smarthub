"""Tones, voice characteristics and slot lengths.

Ported verbatim from Smart-1-Marketing/radio-studio (`src/catalog.js`) so a
spot written in the Hub reads the same as one written in the standalone
studio. ``direction`` steers the script model, ``banner_mood`` steers the
companion-banner artwork, ``line`` is the fallback banner headline.
"""
from __future__ import annotations

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

VOICE_CHARACTERISTICS = [
    {"id": "gender", "label": "Voice", "help": "Perceived voice type.", "options": [
        {"id": "female", "label": "Female"}, {"id": "male", "label": "Male"},
        {"id": "neutral", "label": "Neutral"}, {"id": "any", "label": "No preference"}]},
    {"id": "age", "label": "Age", "help": "How old the read should sound.", "options": [
        {"id": "young", "label": "Young adult"}, {"id": "middle_aged", "label": "Middle aged"},
        {"id": "old", "label": "Mature"}, {"id": "any", "label": "No preference"}]},
    {"id": "accent", "label": "Accent", "help": "", "options": [
        {"id": "american", "label": "American"}, {"id": "british", "label": "British"},
        {"id": "australian", "label": "Australian"}, {"id": "transatlantic", "label": "Transatlantic"},
        {"id": "any", "label": "No preference"}]},
    {"id": "energy", "label": "Energy", "help": "", "options": [
        {"id": "laid_back", "label": "Laid back"}, {"id": "conversational", "label": "Conversational"},
        {"id": "energetic", "label": "Energetic"}, {"id": "explosive", "label": "Explosive"}]},
    {"id": "delivery", "label": "Delivery", "help": "", "options": [
        {"id": "announcer", "label": "Announcer"}, {"id": "narrator", "label": "Narrator"},
        {"id": "best_friend", "label": "Best friend"}, {"id": "spokesperson", "label": "Spokesperson"},
        {"id": "character", "label": "Character"}]},
]

# Word budgets are the studio's, measured at a natural read pace.
DURATIONS = [
    {"seconds": 15, "key": "fifteen", "word_target": "35-42 words", "low": 35, "high": 42},
    {"seconds": 30, "key": "thirty", "word_target": "70-85 words", "low": 70, "high": 85},
]


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
