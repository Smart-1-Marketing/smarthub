"""
Commercial Builder — shared constants.

These values encode the "rules of the road" from the product spec: allowed
commercial lengths, output formats, commercial types, tone options, target
voiceover word counts per length, and the V1 API roster. Centralizing them
here means the storyboard/script logic, the QC checks, and the UI all read
from one source of truth instead of drifting apart.
"""

# ---------------------------------------------------------------------------
# Commercial lengths (seconds) and their target voiceover word counts.
# The script writer (services/openai_service.py) is instructed not to
# wildly exceed these ranges, and the QC service (services/qc_service.py)
# flags a project if the generated VO falls outside them.
# ---------------------------------------------------------------------------
COMMERCIAL_LENGTHS = [5, 15, 30, 60]

VO_WORD_TARGETS = {
    5: (8, 12),
    # Tightened from 30-38 to 25-35 per the CTV/YouTube best-practices brief:
    # "Keep the script under 35 words to ensure clear comprehension."
    15: (25, 35),
    30: (65, 75),
    60: (135, 150),
}

# ---------------------------------------------------------------------------
# Platform — CTV and YouTube get different treatment (living-room legibility
# and audio-first design for CTV; skippable-ad hook and clickable end
# screens for YouTube), so the builder asks up front which this spot is for.
# ---------------------------------------------------------------------------
PLATFORMS = [
    {"id": "ctv", "label": "CTV — Connected TV / OTT"},
    {"id": "youtube", "label": "YouTube"},
    {"id": "both", "label": "Both — CTV and YouTube"},
]
DEFAULT_PLATFORM = "both"

# ---------------------------------------------------------------------------
# Duration-specific structural blueprints. These drive the timed-script
# prompt (services/openai_service.py) so scenes land on the right beats
# instead of just an even split, and they're surfaced in the storyboard UI
# as a guide rail above the scene list.
# ---------------------------------------------------------------------------
STRUCTURE_TEMPLATES = {
    5: [
        {"label": "Hero + Logo + Value Prop", "start_pct": 0, "end_pct": 100,
         "guidance": "Open immediately on the product/hero shot with the logo and one core value "
                     "proposition. No story, no phone number, no QR code — it flies by too fast and "
                     "just frustrates the viewer. This is pure brand recall: a flashing logo/URL."},
    ],
    15: [
        {"label": "Hook", "start_pct": 0, "end_pct": 20,
         "guidance": "Hook the viewer instantly with a relatable pain point or a stunning visual."},
        {"label": "Product / Benefit", "start_pct": 20, "end_pct": 67,
         "guidance": "Introduce the product/solution and flash the primary benefit."},
        {"label": "End Card", "start_pct": 67, "end_pct": 100,
         "guidance": "Logo, website URL, phone number, and a steady QR code — hold it long enough "
                     "to scan."},
    ],
    30: [
        {"label": "The Hook", "start_pct": 0, "end_pct": 17,
         "guidance": "Introduce the problem, emotion, or high-impact visual immediately."},
        {"label": "The Value", "start_pct": 17, "end_pct": 67,
         "guidance": "Showcase the product solution, feature benefits, or customer experience."},
        {"label": "The Close", "start_pct": 67, "end_pct": 100,
         "guidance": "Transition to the end card: persistent logo, website URL, phone number, and "
                     "QR code, with an explicit spoken cue — \"Scan the code on your screen or call "
                     "us today.\""},
    ],
    60: [
        {"label": "Establish", "start_pct": 0, "end_pct": 17,
         "guidance": "Establish the world, the characters, or the core challenge."},
        {"label": "Narrative / Build", "start_pct": 17, "end_pct": 67,
         "guidance": "Build the narrative arc, deep-dive into product features, and build trust/"
                     "credibility (testimonials or high-end demonstrations work well here)."},
        {"label": "Close / End-Slate", "start_pct": 67, "end_pct": 100,
         "guidance": "Summarize the offer, introduce incentives or urgency, and transition into a "
                     "polished, extended end-slate holding the logo, URL, phone number, and QR code."},
    ],
}

DEFAULT_QR_AUDIO_CUE = "Scan the code on your screen or call us today."

# ---------------------------------------------------------------------------
# QR code — required for CTV (no clicks on a TV), optional-but-recommended
# elsewhere. Not used at all on :05 bumpers (spec: it flies by too fast).
# ---------------------------------------------------------------------------
QR_CODE_RULES = {
    "eligible_lengths": [15, 30, 60],
    "min_screen_pct": 15,        # QR code must occupy >=15% of the frame's shorter dimension
    "min_duration_seconds": 8,   # must hold on screen at least 8-10s to be scannable
    "recommended_min_duration_seconds": 10,
    "default_corner": "bottom-right",
    "corners": ["top-right", "bottom-right", "top-left", "bottom-left"],
}

# ---------------------------------------------------------------------------
# Persistent/recurring logo bug — keeps brand identity on screen even if a
# CTV viewer looks away mid-spot. Not needed on :05s (the logo is already
# full-treatment for the whole 5 seconds).
# ---------------------------------------------------------------------------
LOGO_PERSISTENCE_RULES = {
    "eligible_lengths": [15, 30, 60],
    "default_enabled": True,
    "default_corner": "top-left",
    "corners": ["top-right", "bottom-right", "top-left", "bottom-left"],
    "size_pct": 8,  # small corner bug, not a competing focal point
}


def get_structure(length_seconds):
    return STRUCTURE_TEMPLATES.get(length_seconds, STRUCTURE_TEMPLATES[30])


def qr_eligible(length_seconds):
    return length_seconds in QR_CODE_RULES["eligible_lengths"]


def logo_persistence_eligible(length_seconds):
    return length_seconds in LOGO_PERSISTENCE_RULES["eligible_lengths"]

# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------
OUTPUT_FORMATS = [
    {"id": "16:9", "label": "16:9 — CTV / YouTube / website", "width": 1920, "height": 1080},
    {"id": "9:16", "label": "9:16 — Reels / TikTok / Shorts", "width": 1080, "height": 1920},
    {"id": "1:1", "label": "1:1 — Social", "width": 1080, "height": 1080},
]

# ---------------------------------------------------------------------------
# Commercial types
# ---------------------------------------------------------------------------
COMMERCIAL_TYPES = [
    {"id": "stock_vo", "label": "Stock footage + voiceover"},
    {"id": "ai_spokesperson", "label": "AI spokesperson"},
    {"id": "ai_spokesperson_stock", "label": "AI spokesperson + stock footage"},
    {"id": "promo_sale", "label": "Promotional / sale"},
    {"id": "product_spotlight", "label": "Product/service spotlight"},
    {"id": "testimonial", "label": "Testimonial-style"},
    {"id": "weather_triggered", "label": "Weather-triggered commercial"},
    {"id": "seasonal", "label": "Seasonal commercial"},
]

TONE_OPTIONS = [
    "Professional", "Friendly", "Urgent", "Funny",
    "Premium", "High energy", "Emotional",
]

MUSIC_MOODS = [
    "Energetic", "Corporate", "Inspirational", "Fun", "Dramatic",
    "Luxury", "Country", "Rock", "Electronic", "Relaxed",
]

MUSIC_LEVELS = {
    # label -> (music gain dB while VO is silent, music gain dB while VO plays — ducked)
    "Low": (-14, -26),
    "Medium": (-9, -20),
    "High": (-5, -16),
}

VOICE_STYLES = [
    "Male", "Female", "Youthful", "Authoritative",
    "Conversational", "Energetic", "Luxury", "Announcer",
]

CTA_STYLES = [
    {"id": "logo_centered", "label": "Logo centered"},
    {"id": "offer_dominant", "label": "Offer dominant"},
    {"id": "website_dominant", "label": "Website dominant"},
    {"id": "phone_dominant", "label": "Phone dominant"},
]
# NOTE: an earlier version of this spec left QR codes out of the Creative
# Hub direction. The CTV/YouTube best-practices brief (2026-08-17) reversed
# that: QR is "essential for CTV where clicks aren't possible" and is now a
# toggle on the CTA Builder (see QR_CODE_RULES below), not a CTA "style" —
# it layers on top of whichever style is picked, per QR_CODE_RULES /
# LOGO_PERSISTENCE_RULES.

# Asset sourcing waterfall — cheapest/most-owned first, AI generation last
# because it costs the most per scene. Mirrors "Do not generate AI video
# automatically for every scene" from the spec.
ASSET_SOURCE_PRIORITY = ["client_asset", "free_stock", "premium_stock", "ai_generated"]

# ---------------------------------------------------------------------------
# Spokesperson scenes (section 8).
#
# A presenter that has to sit over stock footage is generated against a chroma
# matte and keyed out at composition time, so the same colour has to be known
# to the service that asks HeyGen for it AND to the service that keys it. One
# constant, read by both — heygen_service and creatomate_service.
#
# #00B140 is the broadcast chroma green, not pure #00FF00: it sits further
# from skin tones and from the greens that turn up in real footage, so a
# presenter's edges survive the key.
# ---------------------------------------------------------------------------
CHROMA_KEY_COLOR = "#00B140"

# A presenter that fills the frame has nothing behind it to reveal, so it gets
# a solid backdrop instead of a matte nobody will key.
SOLID_BACKDROP_COLOR = "#1B2A3A"

# Starter Smart 1 talent roster for spokesperson scenes. This is a placeholder
# roster — real HeyGen avatar IDs go in the SMART1_TALENT_AVATARS env var
# (see talent_avatar_overrides) once they are created in the HeyGen console,
# or a client supplies their own avatar ("Client Avatar"). Until one is set,
# the picker shows that person as not yet available rather than offering a
# tile that fails on click.
SMART1_TALENT_ROSTER = [
    {"id": "sarah", "name": "Sarah", "specialty": "Professional / friendly", "heygen_avatar_id": None},
    {"id": "mike", "name": "Mike", "specialty": "Automotive / contractor", "heygen_avatar_id": None},
    {"id": "ashley", "name": "Ashley", "specialty": "Restaurant / retail", "heygen_avatar_id": None},
    {"id": "david", "name": "David", "specialty": "Financial / legal", "heygen_avatar_id": None},
    {"id": "maria", "name": "Maria", "specialty": "Healthcare / lifestyle", "heygen_avatar_id": None},
]

def talent_avatar_overrides():
    """Real HeyGen avatar ids for the talent roster, as a JSON object mapping
    roster id -> avatar id, e.g. {"sarah": "abc123"}.

    Read at call time rather than import, so linking a new avatar takes an env
    change and a restart rather than a deploy. A malformed value is ignored
    rather than raised: a bad env var must not take the whole picker down.
    """
    import json
    import os

    raw = (os.environ.get("SMART1_TALENT_AVATARS") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items() if v}


# ---------------------------------------------------------------------------
# AI video scenes (Runway).
#
# Two constraints come from the provider and drive everything else here:
#
#   * **A reference image is required.** Gen-4 and Gen-4 Turbo animate a
#     starting frame; there is no usable text-only path. That suits this tool,
#     which already generates stills — the frame is art-directed first and
#     then moved, rather than hoping one prompt lands both.
#   * **Clips are 5 or 10 seconds.** Nothing else. A storyboard scene is
#     whatever length the script needs, so the clip is requested at the
#     shortest length that COVERS the scene and the compositor trims it. A
#     scene longer than 10s cannot be covered by one clip and is said so
#     rather than left with a gap nobody sees until the render comes back.
#
# The ratio strings are the part most likely to differ between accounts and
# model versions; they are here, in one dict, for that reason.
# ---------------------------------------------------------------------------
RUNWAY_MODEL = "gen4_turbo"
RUNWAY_DURATIONS = [5, 10]
RUNWAY_MAX_SCENE_SECONDS = 10
RUNWAY_RATIOS = {"16:9": "1280:720", "9:16": "720:1280", "1:1": "960:960"}
RUNWAY_DEFAULT_RATIO = "1280:720"


def runway_ratio(format_id):
    return RUNWAY_RATIOS.get(format_id, RUNWAY_DEFAULT_RATIO)


def runway_duration(scene_seconds):
    """The shortest offered clip that covers the scene, or None if none does.

    None is the honest answer for a scene over 10 seconds: returning 10 would
    render a clip that stops early, and a segment that goes black partway is
    the kind of defect nobody catches until a client does.
    """
    for length in sorted(RUNWAY_DURATIONS):
        if scene_seconds <= length:
            return length
    return None


# The API roster from the spec, by the release each provider arrived in.
#
# The dashboard used to read V1_PROVIDERS for its status dots and draw
# V1_5_PROVIDERS as a hard-coded grey "V1.5" chip — so Runway, which has had a
# working service and a real key check since AI video scenes shipped, could
# not be reported as connected however many keys were added. Status now comes
# from services/provider_check.py, which owns the display order and the
# per-provider checks; these lists stay the spec's own record of what belongs
# to which release. test_commercial_providers.py asserts the two agree, so a
# provider added to one and forgotten in the other cannot go unreported.
#
# V2 is the one honest exception: nothing is implemented behind those two, so
# they are drawn as a static "not built yet" label rather than a dot that
# could never light.
V1_PROVIDERS = ["openai", "pexels", "pixabay", "heygen", "elevenlabs", "creatomate", "cloudinary"]
V1_5_PROVIDERS = ["runway"]
V2_PROVIDERS = ["storyblocks", "shutterstock"]

PROJECT_STATUSES = [
    "draft", "brief", "concepts", "scripted", "storyboard",
    "qc", "rendering", "complete",
]
