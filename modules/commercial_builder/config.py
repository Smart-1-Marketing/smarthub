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

# What each length costs to make and how it tends to perform, said at the
# moment somebody picks it rather than after they have built it.
#
# The :60 warning is the one with money behind it. A :60 is not "the :30 with
# more room" -- it is roughly twice the AI video clips, twice the voiceover
# characters (ElevenLabs bills the character, see hub/quotas.py), twice the
# render minutes, and on skippable inventory it is the length viewers skip
# most. None of that makes it wrong; it makes it a decision, and a decision
# nobody was told they were taking is the one that gets regretted.
LENGTH_NOTES = {
    5: {"label": "Bumper",
        "note": "Pure brand recall — hero shot, logo, one value prop. No story, no "
                "phone number, no QR code: it flies past too fast to act on.",
        "cost": "Cheapest to produce — usually one or two scenes."},
    15: {"label": "Standard",
         "note": "One message, one CTA. Hook, benefit, end card. Script under 35 words.",
         "cost": "Low — around three scenes."},
    30: {"label": "The workhorse",
         "note": "The industry standard, and the length most CTV inventory is sold in. "
                 "Hook, value, close with a held end card.",
         "cost": "Moderate — around three scenes with room to breathe."},
    60: {"label": "Long form",
         "note": "Room for a narrative arc, testimonials or a real demonstration. "
                 "Worth it where attention is already earned.",
         "cost": "Roughly double a :30 in AI video credits, voiceover characters "
                 "and render time.",
         "warning": "A :60 costs about twice a :30 to generate — twice the AI video "
                    "credits, twice the voiceover characters, twice the render — and on "
                    "skippable placements it is the length viewers skip most. Build one "
                    "where the attention is already earned, and cut a :30 or :15 "
                    "alongside it for everywhere else."},
}


def length_warning(length_seconds):
    """The warning for a length, or empty string where there is none.

    Empty rather than a cheerful reassurance: a note on every length is a note
    nobody reads, and then the one that mattered goes past unread too.
    """
    return (LENGTH_NOTES.get(length_seconds) or {}).get("warning", "")


# One build can produce several lengths at once. The cap is not arbitrary --
# each length is a full project with its own script, scenes, provider calls
# and render, so ticking all four is four commercials' worth of spend from one
# press. Four is every length this tool offers; there is nothing to gain by
# allowing the same one twice.
MAX_LENGTHS_PER_BUILD = len(COMMERCIAL_LENGTHS)

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
    {"id": "ctv", "label": "CTV — Connected TV / OTT",
     "sub": "Runs in a living room. No clicks, so the QR code is the response.",
     "default_formats": ["16:9"]},
    {"id": "youtube", "label": "YouTube",
     "sub": "Skippable. The first five seconds has to survive on its own.",
     "default_formats": ["16:9"]},
    {"id": "social", "label": "Social — Meta, TikTok, Reels",
     "sub": "Vertical, watched on mute, and scrolled past in a second.",
     "default_formats": ["9:16"]},
    {"id": "both", "label": "CTV and YouTube",
     "sub": "One spot cut for both screens.",
     "default_formats": ["16:9"]},
]
DEFAULT_PLATFORM = "both"

# Social is its own platform and not a third output format, because what
# changes is not the crop. A 9:16 render of a CTV spot is still a CTV spot:
# it opens on a slow establishing shot, it carries a QR code nobody can scan
# while holding the phone that would scan it, and its whole argument is spoken
# aloud on a feed that plays muted by default. Those are script decisions and
# QC decisions, which is why this sits beside the platform switch that already
# drives both rather than beside the aspect ratios.
SOCIAL_PLATFORMS = ("social",)


def is_social(platform):
    return platform in SOCIAL_PLATFORMS

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

# Social runs to different beats and the difference is the first second, not
# the crop. A feed ad has no pre-roll slot holding the viewer in place: the
# thumb is already moving, so the hook is one beat and it is at zero. These
# override STRUCTURE_TEMPLATES for a social spot of the same length.
SOCIAL_STRUCTURE_TEMPLATES = {
    5: STRUCTURE_TEMPLATES[5],
    15: [
        {"label": "Thumb-stop", "start_pct": 0, "end_pct": 13,
         "guidance": "One arresting frame or a spoken hook in under two seconds. The viewer "
                     "is already scrolling — there is no slot holding them here."},
        {"label": "Payoff", "start_pct": 13, "end_pct": 73,
         "guidance": "Deliver the one thing being promised, on screen as text as well as in "
                     "the narration: most of this audience is watching on mute."},
        {"label": "End Card", "start_pct": 73, "end_pct": 100,
         "guidance": "Logo, offer and a tappable call to action. Keep the bottom of the "
                     "frame clear — the platform's own caption and buttons sit there."},
    ],
    30: [
        {"label": "Thumb-stop", "start_pct": 0, "end_pct": 10,
         "guidance": "Earn the next two seconds in the first two. Open on movement or a "
                     "stated problem, never on a slow establishing shot."},
        {"label": "The Value", "start_pct": 10, "end_pct": 70,
         "guidance": "Show the solution in specifics, with the key claims burned in as "
                     "on-screen text so the spot works with the sound off."},
        {"label": "The Close", "start_pct": 70, "end_pct": 100,
         "guidance": "Offer, brand and a tappable call to action, clear of the bottom of "
                     "the frame where the platform draws its own controls."},
    ],
    60: [
        {"label": "Thumb-stop", "start_pct": 0, "end_pct": 8,
         "guidance": "The hook is the whole reason a :60 survives in a feed. Lead with the "
                     "single most arresting moment in the spot."},
        {"label": "Story", "start_pct": 8, "end_pct": 60,
         "guidance": "Build the narrative, keeping a new visual beat every few seconds — a "
                     "static shot loses a feed audience faster than a weak line does."},
        {"label": "Proof", "start_pct": 60, "end_pct": 85,
         "guidance": "Testimonial, demonstration or a concrete result. This is the part a "
                     ":30 has no room for and the only reason to be running a :60."},
        {"label": "The Close", "start_pct": 85, "end_pct": 100,
         "guidance": "Offer, brand and a tappable call to action, clear of the bottom of "
                     "the frame."},
    ],
}

# What a social spot has to do that a CTV one does not. These are checked in
# services/qc_service.py rather than merely written into the prompt, for the
# reason hub/blog_spec.py gives about "never mention": a prompt is a request,
# and "the model was told to" is not evidence that it did.
SOCIAL_RULES = {
    # Meta and TikTok draw their own caption, handle and buttons over the
    # bottom of the frame, and a profile row over the top. The number is the
    # spec kit's own for Stories (hub/creative_specs.py stories_video).
    "safe_area_pct": 14,
    "preferred_format": "9:16",
    "hook_seconds": 2.0,
    "sound_off_note": ("Most of a feed audience watches with the sound off, so every "
                       "claim that matters has to be on screen as text as well as in "
                       "the narration."),
}


DEFAULT_QR_AUDIO_CUE = "Scan the code on your screen or call us today."


# ---------------------------------------------------------------------------
# The creative spec kit.
#
# This tool produced finished video for CTV, YouTube and social and never once
# judged it against the specification the same agency publishes for the people
# buying that inventory — a spot could pass every check on the Preview screen
# and be refused by the platform on the numbers, which is a rejection nobody
# here would have been able to explain. hub/creative_specs.py has held those
# numbers all along, read by the IO builder and by the client galleries.
#
# What is mapped here is only which of its channels applies: the spot's
# platform decides that, and the output format decides which unit inside the
# channel it is judged as. The numbers stay in one file, transcribed once,
# with SPEC_KIT_URL naming where to check them.
# ---------------------------------------------------------------------------
SPEC_CHANNELS_BY_PLATFORM = {
    "ctv": ["ctv"],
    "youtube": ["youtube"],
    "both": ["ctv", "youtube"],
    # A social buy is bought per network, so the file has to satisfy the one
    # it is actually running on. All four are offered rather than one being
    # picked: judging a Reel against TikTok's ceiling and reporting a pass is
    # the confident wrong answer.
    "social": ["stories", "facebook_video", "tiktok", "snapchat"],
}

# Whether the channels for a platform are all of them or any of them, which
# is a different question per platform and changes what a pass means.
#
# A "both" buy runs the same file on CTV *and* on YouTube, so it has to satisfy
# both: a :60 that YouTube accepts and CTV refuses is not a pass, it is a spot
# that will be rejected by half the buy. A social buy is bought per network and
# runs where it fits, so satisfying one network is a real pass -- but the
# networks that would refuse are named rather than dropped, because "this runs
# on Meta and TikTok, Snapchat caps at 30 seconds" is the sentence somebody
# needs before they place it.
SPEC_CHANNEL_MODE = {"ctv": "all", "youtube": "all", "both": "all", "social": "any"}


def spec_channel_mode(platform):
    return SPEC_CHANNEL_MODE.get(platform, "all")


# A vertical or square render is never a CTV unit, whatever the platform says
# — CTV is sold at 1920x1080 and nothing else. Without this, a 9:16 cut of a
# "both" spot is judged against the CTV unit, fails on dimensions, and the
# report blames the crop rather than saying that cut is for a different buy.
SPEC_CHANNELS_BY_FORMAT = {
    "9:16": ["stories", "tiktok", "snapchat"],
    "1:1": ["facebook_video", "instagram"],
}


def spec_channels(platform, format_id):
    """Which spec-kit channels one rendered format of one spot is judged against.

    Returns an empty list where the pairing has no home in the kit — a 1:1 cut
    of a CTV-only buy is not a unit anybody sells, and saying so beats picking
    the nearest channel and reporting a verdict about a placement that does
    not exist.
    """
    by_platform = SPEC_CHANNELS_BY_PLATFORM.get(platform, [])
    by_format = SPEC_CHANNELS_BY_FORMAT.get(format_id)
    if by_format is None:                      # 16:9 — every channel accepts it
        return list(by_platform)
    return [c for c in by_platform if c in by_format]

# ---------------------------------------------------------------------------
# QR code — required for CTV (no clicks on a TV), optional-but-recommended
# elsewhere. Not used at all on :05 bumpers (spec: it flies by too fast).
# ---------------------------------------------------------------------------
QR_CODE_RULES = {
    "eligible_lengths": [15, 30, 60],
    # Which platforms a QR code is *required* on, as opposed to merely
    # offered. CTV is the only one where it is the sole response mechanism.
    # Social is deliberately absent: a feed ad is already tappable, and a QR
    # code on a phone screen asks somebody to scan the device they are holding.
    "required_platforms": ["ctv", "both"],
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


def get_structure(length_seconds, platform=""):
    """The beat structure for a length, on the platform it is running on.

    Platform is optional and defaults to the CTV/YouTube shape, so every
    existing caller keeps the structure it had. Only social overrides it, and
    only because its first beat is genuinely a different beat.
    """
    if is_social(platform):
        table = SOCIAL_STRUCTURE_TEMPLATES
    else:
        table = STRUCTURE_TEMPLATES
    return table.get(length_seconds, table.get(30, STRUCTURE_TEMPLATES[30]))


def qr_eligible(length_seconds):
    return length_seconds in QR_CODE_RULES["eligible_lengths"]


def qr_required(length_seconds, platform):
    """Whether a spot of this length on this platform must carry a QR code.

    Offered and required are different, and collapsing them is how a warning
    stops being read: a social spot without one is correct, a CTV spot without
    one has no response mechanism at all.
    """
    return (qr_eligible(length_seconds)
            and platform in QR_CODE_RULES["required_platforms"])


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
