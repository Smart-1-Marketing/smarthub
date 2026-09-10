"""The AI Tools registry -- Section 9 of the build spec, verbatim.

`seed()` is idempotent -- it skips any key already in the table, the same
shape `seed_templates.seed()` uses and for the same reason: it runs on every
boot, and a seed that only ever inserts what is missing costs one query on
every boot after the first rather than fighting an admin's own edits. Routes
point at URLs this deployment already serves; a key with no live tool behind
it yet ships `route=""` and `status="coming_soon"` so its tile renders
disabled rather than linking nowhere.
"""
from __future__ import annotations

from .db import db
from .models import CsAiTool

# (key, name, category, description, icon, route, status, sort)
_ROWS: list[tuple] = [
    ("script_generator", "Script Generator", "video",
     "Brief, concepts and a timed script -- opened in Studio context on the "
     "Commercial Builder's own pipeline.",
     "\U0001F4DD", "/tools/commercial-builder/", "live", 10),
    ("ai_image", "AI Image", "images",
     "A still image generated from a text prompt. Opens Image Creator's AI panel.",
     "\U0001F5BC", "/tools/image-creator/", "live", 20),
    ("ai_spokesperson", "AI Spokesperson", "video",
     "An on-camera avatar reads your script. Opens the Commercial Builder's "
     "HeyGen picker.",
     "\U0001F464", "/tools/commercial-builder/", "live", 30),
    ("voiceover", "Voiceover", "audio",
     "A cast, professional voice reads your script. Opens Voice Studio.",
     "\U0001F3A4", "/tools/commercial-builder/", "live", 40),
    ("intro_outro", "Intro / Outro", "video",
     "A short branded bumper built from a template.",
     "\U0001F3F7", "/creative-studio/templates?type=intro_outro", "live", 50),
    ("resize_creative", "Resize Creative", "video",
     "Reframe a finished video for a second aspect ratio.",
     "\U0001F4D0", "/tools/vertical-reframe/", "live", 60),
    ("background_generator", "Background Generator", "images",
     "An AI-generated background for a scene or a still. Opens Image "
     "Creator's AI Background panel.",
     "\U0001F3A8", "/tools/image-creator/", "live", 70),
    ("image_to_video", "Image to Video", "video",
     "Animate a still into a short clip. Live in the Commercial Builder.",
     "\U0001F3AC", "/tools/commercial-builder/", "live", 80),
    ("product_lifestyle", "Product Lifestyle", "product",
     "A product shot placed into a lifestyle scene.",
     "\U0001F6D2", "", "coming_soon", 90),
    ("logo_animation", "Logo Animation", "brand",
     "The logo alone, animated in -- the logo_reveal template layout.",
     "✨", "/creative-studio/templates?type=intro_outro", "live", 100),
    ("pdf_to_video", "PDF to Video", "video",
     "Turn a PDF flyer or one-sheet into a short video.",
     "\U0001F4C4", "", "coming_soon", 110),
    ("dead_air_cutter", "Dead Air Cutter", "audio",
     "Trim silence out of a raw voice or interview recording.",
     "✂️", "/tools/dead-air/", "live", 120),
    ("vertical_reframe", "Vertical Reframe", "video",
     "Reframe a horizontal video for 9:16 placements.",
     "\U0001F4F1", "/tools/vertical-reframe/", "live", 130),
    ("video_backgrounds", "Video Backgrounds", "video",
     "Search the Hub's own footage library by what is on screen.",
     "\U0001F3A5", "/tools/video-backgrounds/", "live", 140),
]


def seed() -> int:
    """Insert any of the rows above not already present. Returns how many
    were created. Never raises past its own transaction -- the rule
    `seed_templates.seed()` states: a seed failure must leave the screen a
    little emptier than it should be, not take the Hub down."""
    created = 0
    for key, name, category, description, icon, route, status, sort in _ROWS:
        if CsAiTool.query.filter_by(key=key).first() is not None:
            continue
        db.session.add(CsAiTool(key=key, name=name, category=category,
                                description=description, icon=icon, route=route,
                                status=status, sort=sort))
        created += 1
    if created:
        db.session.commit()
    return created
