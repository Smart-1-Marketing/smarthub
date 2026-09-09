"""Data the Creative Studio front door reads rather than hard-codes.

Every list here is a config table, not a fact restated in a template: adding
a creative type or an asset kind should never need a template edit, the rule
CLAUDE.md states at length about every gallery filter and tile list in this
Hub. `test_creative_studio.py` asserts the dashboard cards come from
`CREATIVE_TYPES` rather than from a hand-typed block in the page.
"""
from __future__ import annotations

# key -> (label, description, route). `route` is a URL this deployment
# already serves; a type with no working tool yet points at "" and the card
# renders disabled with "Coming Soon" rather than a dead link.
CREATIVE_TYPES: list[dict] = [
    {"key": "video_commercial", "label": "Video Commercial",
     "description": "A finished spot built from a template and rendered "
                     "through the Commercial Builder pipeline.",
     "route": "/creative-studio/templates?type=video_commercial", "icon": "\U0001F3AC"},
    {"key": "social_video", "label": "Social Video",
     "description": "A vertical or square cut for feed and story placements.",
     "route": "/creative-studio/templates?type=social_video", "icon": "\U0001F4F1"},
    {"key": "ctv_commercial", "label": "CTV Commercial",
     "description": "A :15/:30/:60 built to the Connected TV creative spec.",
     "route": "/creative-studio/templates?type=ctv_commercial", "icon": "\U0001F4FA"},
    {"key": "ugc_ad", "label": "UGC Ad",
     "description": "A spokesperson- or testimonial-style vertical ad.",
     "route": "/creative-studio/templates?type=ugc_ad", "icon": "\U0001F4F8"},
    {"key": "product_ad", "label": "Product Ad",
     "description": "A product shot turned into a finished ad.",
     "route": "/creative-studio/templates?type=product_ad", "icon": "\U0001F6CD"},
    {"key": "display_ad", "label": "Display Ad",
     "description": "A banner size set. Opens the Display Ad Builder.",
     "route": "/tools/display-ads/_hub/start", "icon": "\U0001F5BC"},
    {"key": "social_image", "label": "Social Image",
     "description": "A single still for a feed post. Opens Image Creator.",
     "route": "/tools/image-creator/", "icon": "\U0001F3A8"},
    {"key": "intro_outro", "label": "Intro / Outro",
     "description": "A short branded bumper built from a template.",
     "route": "/creative-studio/templates?type=intro_outro", "icon": "\U0001F3F7"},
    {"key": "audio_commercial", "label": "Audio Commercial",
     "description": "A radio spot. Opens Radio Scripts.",
     "route": "/tools/radio-scripts/", "icon": "\U0001F3A4"},
]

_TYPE_BY_KEY = {t["key"]: t for t in CREATIVE_TYPES}


def creative_type(key: str) -> dict | None:
    return _TYPE_BY_KEY.get(key)


# Media Library tabs, in the order Todd's brief lists them.
ASSET_TYPES: list[dict] = [
    {"key": "image", "label": "Images"},
    {"key": "video", "label": "Videos"},
    {"key": "logo", "label": "Logos"},
    {"key": "audio", "label": "Audio"},
    {"key": "music", "label": "Music"},
    {"key": "voiceover", "label": "Voiceovers"},
    {"key": "document", "label": "Documents"},
    {"key": "generated", "label": "Generated"},
]

ASSET_TYPE_KEYS = {a["key"] for a in ASSET_TYPES}

# Where a media row came from -- printed as a badge, and what a producer
# module is expected to name when it writes one. "upload" is a person; every
# other value names the provider or transform that made the file, the same
# rule modules/ad_builder's PRODUCERS check applies one repo over.
ASSET_SOURCES = (
    "upload", "openai", "runway", "heygen", "elevenlabs", "template",
    "stock", "cloudinary_transform", "creatomate",
)

ACCEPTED_EXTENSIONS = {
    "image": (".jpg", ".jpeg", ".png", ".webp", ".gif"),
    "video": (".mp4", ".mov", ".webm"),
    "audio": (".mp3", ".wav", ".m4a"),
    "document": (".pdf",),
}

# Project status vocabulary -- Section 11 of the build spec, verbatim, so the
# dashboard, the projects list and the editor cannot each invent their own
# word for the same state.
PROJECT_STATUSES = (
    "Draft", "Rendering", "Internal Review", "Client Review",
    "Changes Requested", "Approved", "Archived",
)

# creative_jobs kinds this deployment knows how to run. A kind not in this
# dict is refused at enqueue time rather than sitting in the queue forever
# looking like a stuck job -- the sweep this file's own JOBS entry describes.
JOB_KINDS = ("index", "script", "storyboard", "image", "voice", "heygen",
             "render", "variant", "pdf")

# Stages shown to the user, in the order Section 11 gives. A job's `stage`
# is free text so a kind can name its own step, but these are the ones the
# progress bar recognises well enough to draw a fraction from.
STAGE_ORDER = (
    "Preparing assets", "Generating voiceover", "Creating scenes",
    "Rendering video", "Processing", "Uploading", "Complete",
)

# Minutes before a queued or processing job is swept as failed. Generation
# jobs are cheap to retry; a render is a paid Creatomate call and gets longer
# before the sweep gives up on it.
JOB_TIMEOUT_MINUTES = {
    "index": 10, "script": 5, "storyboard": 5, "image": 8, "voice": 8,
    "heygen": 20, "render": 30, "variant": 15, "pdf": 5,
}

# Renders bill; a generation retried three times over is still cheaper than
# one wasted render.
JOB_MAX_ATTEMPTS = {"render": 1}
DEFAULT_MAX_ATTEMPTS = 3

# Display labels for the industries the seed templates use. Python's
# str.title() mangles an acronym ("hvac".title() == "Hvac"), so the ones
# that are not ordinary words are spelled out here rather than derived --
# read by both the seed fixtures and the gallery's industry filter, so the
# two cannot drift into naming one industry two different ways.
INDUSTRY_LABELS = {"general": "General", "hvac": "HVAC", "restaurant": "Restaurant",
                   "home_services": "Home Services"}


def industry_label(industry: str) -> str:
    return INDUSTRY_LABELS.get(industry, (industry or "").replace("_", " ").title())


# The AI Tools screen's category row, Section 9 of the build spec verbatim.
# A fixed tuple rather than the DISTINCT-values-of-what-exists pattern every
# other filter in this module uses (industry, creative type): the build spec
# enumerates the whole menu on purpose, Social and Quick Tools included, so
# the tab is there waiting the day a tool is seeded into it rather than
# appearing out of nowhere when one finally is.
AI_TOOL_CATEGORIES = ("video", "product", "social", "images", "audio", "brand", "quick_tools")

AI_TOOL_CATEGORY_LABELS = {
    "video": "Video", "product": "Product", "social": "Social",
    "images": "Images", "audio": "Audio", "brand": "Brand",
    "quick_tools": "Quick Tools",
}


# (provider, service) -> dollars per `unit` on CsUsageLog. Every value here is
# a **placeholder** until Todd supplies real provider rates (WO-CS4's own
# words) -- `cs_usage_logs.estimated_cost` and the Usage & Costs page both
# label it as an estimate rather than a bill, the way `hub/quotas.py` already
# treats `IMAGE_PRICING`. A (provider, service) pair not in this table is not
# measured against a cost at all: `usage.record()` writes the row with
# `estimated_cost=None` rather than guessing, which is the difference between
# "we have not priced this yet" and a confident wrong number.
PROVIDER_RATES = {
    ("openai", "concepts"): 0.01,
    ("openai", "script"): 0.02,
    ("openai", "image"): 0.04,
    ("elevenlabs", "voice"): 0.30,
    ("heygen", "spokesperson"): 1.50,
    ("creatomate", "render"): 0.50,
}
