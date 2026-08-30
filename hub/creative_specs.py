"""The 2025 Creative Spec Kit, as data.

The IO Builder's upload manager now names the **2026** kit on screen, because
that is the edition a rep is working from. Every number below is still
transcribed from S1M CREATIVE SPEC KIT 2025 and has NOT been re-checked
against 2026 — `catalogue()`'s `source` string says so, and it is the one
place that makes a claim about provenance. When the 2026 kit is transcribed,
the numbers and that string change together; until then the year on a button
and the year a dimension came from are different facts, and only one of them
has been verified.

Every number here is transcribed from S1M CREATIVE SPEC KIT 2025. When the kit
is revised, this file is what changes — not a regex inside a template.

Why it lives in hub/ rather than in the IO builder: the same question ("does
this file meet spec?") is asked when creative is attached to an insertion
order, when it lands in a client's gallery, and by diagnostics looking for
creative that will be rejected downstream. Those were three different answers.
The IO builder carried a hand-written approximation of this table in
JavaScript — nine display sizes, one shared 150 KB cap, and no notion of what a
DOOH billboard, a Snapchat story or a LinkedIn InMail banner requires.

## pass, fail and warn are different things

A publisher rejects a 151 KB display banner. It does not reject a Meta image at
1000x1000 — that is under the recommended 1080x1080, and it will run, slightly
soft. Collapsing those into one "warning", as the old check did, taught people
to ignore the warning, which meant the real rejections went out too.

    fail     the platform will refuse this file
    warn     it will run, but it is outside what the kit recommends
    pass     nothing to flag
    unknown  no spec is mapped for this product — say so, do not imply pass

`unknown` is deliberately not `pass`. A clean result on an unmapped product is
a wrong answer presented confidently.
"""

from __future__ import annotations

import datetime as _dt
import pathlib
import re
from typing import Any

KB = 1024
MB = 1024 * KB
GB = 1024 * MB

# Where a person checks these numbers against the published kit.
#
# It is a URL and not a fetch. Nothing in this Hub reads that page at runtime:
# a spec table pulled live would change what a check says without anyone
# editing anything, and the first time it did, a creative that passed on
# Tuesday would fail on Wednesday with no diff to point at. The numbers above
# are transcribed, deliberately, and this is the address to re-transcribe them
# from -- printed wherever a verdict is shown so the source of a refusal is
# one click away rather than folklore.
SPEC_KIT_URL = "https://smart1.agency/partner/creative-specs"


# --------------------------------------------------------------------------
# The catalogue
#
# Each unit is one row of one table in the kit. Fields, all optional except
# id/channel/name/kind:
#
#   size / sizes   exact pixel dimensions the unit is sold at
#   min_size       (w, h) floor for units the kit gives as a recommendation
#   ratios         acceptable width:height ratios, as (w, h) pairs
#   ratio_range    (low, high) ratio pair, for units given as a span
#   width_range    (low, high) acceptable pixel widths
#   min_width      hard floor on width
#   max_width      hard ceiling on width
#   max_height     hard ceiling on height
#   max_bytes      hard ceiling — over it is a fail
#   subload_bytes  the kit's second weight: what the unit may reach once the
#                  initial load has rendered. Reported, never failed on --
#                  a delivered image file IS the initial load, and the
#                  subload ceiling only means anything to an HTML5 package
#   target_bytes   what the kit asks for rather than what it refuses. NOT a
#                  floor: DOOH publishes "40 KB target / 750 KB max", and
#                  carrying that as `min_bytes` failed a clean 30 KB
#                  billboard for being too small
#   min_bytes      floor, where the kit states one as a floor
#   formats        accepted extensions; anything else is a fail
#   duration       (min_seconds, max_seconds) for video and audio
#   max_anim       animation ceiling in seconds, with max_loops alongside
#   border         pixel border the kit requires
#   text           character limits, {field: max or (min, max)}
#   notes          what a human needs to know that no check can decide
# --------------------------------------------------------------------------

# The common case, not the rule: the kit publishes a weight per unit and
# says in as many words that "the flat 150 KB rule is gone". 150/300 is what
# most units carry; Half Page and Billboard carry 250/500 and say so below.
#
# `max_anim` and `max_loops` are gone with it. The kit's own wording is that
# there is "no single universal limit" -- Google Ads and DV360 allow 30
# seconds, The Trade Desk allows 15 seconds of looping, and the IAB now
# guides on CPU load rather than duration -- and that "15 seconds or 3 loops
# is no longer a universal rule". Carrying 15/3 as though it were one is
# quoting a retired rule at somebody with a compliant file. The platform
# figures are in `notes`, where a human can act on them, rather than in a
# field that reads as a check.
_DISPLAY_COMMON: dict[str, Any] = {
    "kind": "image",
    # SVG is accepted now; the kit lists it on every display unit.
    "formats": ["gif", "jpg", "jpeg", "png", "svg"],
    "max_bytes": 150 * KB,
    "subload_bytes": 300 * KB,
    "border": 1,
    "notes": [
        "Animation has no single universal limit. Google Ads allows 30 "
        "seconds and requires the animation to stop; DV360 caps at 30 "
        "seconds; The Trade Desk allows 15 seconds of looping. Build to the "
        "platform the buy is on.",
        "Creative must be clearly distinguishable from the page around it — "
        "a contrasting border or a non-white background. The 1-pixel "
        "contrasting border is a publisher convention rather than a formal "
        "IAB standard; build it in by default.",
    ],
}

# The two desktop units the kit weighs at 250/500 rather than 150/300.
_DISPLAY_HEAVY: dict[str, Any] = {**_DISPLAY_COMMON,
                                  "max_bytes": 250 * KB,
                                  "subload_bytes": 500 * KB}

# HTML5 display is packaged rather than sized, so it gets its own ceiling.
_HTML5: dict[str, Any] = {
    "kind": "package",
    "formats": ["zip"],
    "max_bytes": 200 * KB,
    "notes": [
        "200 KB applies to font, image, audio, video, CSS and HTML combined.",
        "Max 15 host-initiated file requests during initial and sub-load; "
        "unlimited after user interaction.",
        "Clicks must open in a new window or tab.",
        "Must not use any element designed to misleadingly generate a click, "
        "and must not lead to malware, spyware or viruses.",
    ],
}

_DOOH_COMMON: dict[str, Any] = {
    "target_bytes": 40 * KB,
    "max_bytes": 750 * KB,
    "formats": ["jpg", "jpeg", "png", "mp4", "html5"],
}

UNITS: list[dict[str, Any]] = [
    # ---- Desktop display -------------------------------------------------
    {"id": "leaderboard", "channel": "desktop_display", "name": "Leaderboard",
     "size": (728, 90), **_DISPLAY_COMMON},
    {"id": "medium_rectangle", "channel": "desktop_display", "name": "Medium Rectangle",
     "size": (300, 250), **_DISPLAY_COMMON},
    # "Skyscraper" on the kit, not "Wide Skyscraper". The id stays whatever
    # it has always been: `tags_for()` writes "unit_<id>" onto every file
    # delivered through the upload manager, so renaming one orphans the tags
    # already on a year of creative in order to correct a label.
    {"id": "wide_skyscraper", "channel": "desktop_display", "name": "Skyscraper",
     "size": (160, 600), **_DISPLAY_COMMON},
    {"id": "half_page", "channel": "desktop_display", "name": "Half Page",
     "size": (300, 600), **_DISPLAY_HEAVY},
    # 970x250 is a Billboard. The IAB retired the "Rising Stars" programme,
    # and the name went with it -- so the kit a client is sent and the
    # verdict we hand them named the same unit two different things.
    {"id": "rising_star", "channel": "desktop_display", "name": "Billboard",
     "size": (970, 250), **_DISPLAY_HEAVY},
    {"id": "desktop_html5", "channel": "desktop_display", "name": "HTML5 package",
     **_HTML5},

    # ---- Mobile display --------------------------------------------------
    # The kit weighs a smartphone banner at 50/100, not 150/300 -- this was
    # the one drift running the other way, passing a file three times the
    # published ceiling.
    {"id": "mobile_banner_320", "channel": "mobile_display", "name": "Smartphone Banner",
     "size": (320, 50), **_DISPLAY_COMMON, "max_bytes": 50 * KB,
     "subload_bytes": 100 * KB},
    {"id": "mobile_banner_300", "channel": "mobile_display", "name": "Smartphone Banner",
     "size": (300, 50), **_DISPLAY_COMMON, "max_bytes": 50 * KB,
     "subload_bytes": 100 * KB},
    # The kit sells the interstitial at three device sizes. 320x480 is on
    # none of them, so every real interstitial failed the dimension check
    # against a size nobody delivers.
    {"id": "mobile_interstitial", "channel": "mobile_display", "name": "Mobile Interstitial",
     "sizes": [(640, 1136), (750, 1334), (1080, 1920)], **_DISPLAY_COMMON,
     "max_bytes": 300 * KB, "subload_bytes": 600 * KB},
    {"id": "mobile_rectangle", "channel": "mobile_display", "name": "Mobile Rectangle",
     "size": (300, 250), **_DISPLAY_COMMON},
    {"id": "mobile_html5", "channel": "mobile_display", "name": "HTML5 package", **_HTML5},

    # ---- Tablet display --------------------------------------------------
    # The kit publishes no tablet section. These four are house guidance --
    # the desktop weights against tablet dimensions -- and are marked as such
    # rather than left to read as transcribed, which is the rule
    # `HOUSE_LEGIBILITY` in services/abcd_service.py works to.
    {"source": "house", "id": "tablet_rectangle", "channel": "tablet_display", "name": "Tablet Rectangle",
     "size": (300, 250), **_DISPLAY_COMMON},
    {"source": "house", "id": "tablet_leaderboard", "channel": "tablet_display", "name": "Tablet Leaderboard",
     "size": (728, 90), **_DISPLAY_COMMON},
    {"source": "house", "id": "tablet_interstitial", "channel": "tablet_display", "name": "Tablet Interstitial",
     "size": (1024, 768), **_DISPLAY_COMMON},
    {"id": "tablet_html5", "channel": "tablet_display", "name": "HTML5 package", **_HTML5},

    # ---- Native display --------------------------------------------------
    {"id": "native_image", "channel": "native_display", "name": "Native Image",
     "kind": "image", "size": (1200, 628), "max_bytes": 750 * KB,
     "formats": ["gif", "jpg", "jpeg", "png"],
     "text": {"headline": (15, 55), "description": (25, 120)}},
    {"id": "native_logo", "channel": "native_display", "name": "Brand Logo",
     "kind": "image", "size": (200, 200), "max_bytes": 750 * KB,
     "formats": ["gif", "jpg", "jpeg", "png"]},

    # ---- Video -----------------------------------------------------------
    {"id": "standard_video", "channel": "standard_video", "name": "Standard Video",
     "kind": "video", "formats": ["mp4"], "ratios": [(16, 9), (4, 3)],
     "max_bytes": 10 * GB, "duration": (15, 60), "bitrate_kbps": (15000, 30000),
     "notes": ["VAST 2.0 / 3.0 compliant.",
               "Third-party VAST must contain MP4 and FLV format videos."]},
    {"id": "youtube_trueview", "channel": "youtube", "name": "TrueView",
     "kind": "video", "formats": ["mp4", "mov"], "ratios": [(16, 9)],
     "max_bytes": 10 * MB, "duration": (12, 180),
     "notes": ["Video asset must be loaded to YouTube as a public video."]},
    {"id": "youtube_bumper", "channel": "youtube", "name": "Bumper",
     "kind": "video", "formats": ["mp4", "mov"], "ratios": [(16, 9)],
     "max_bytes": 10 * MB, "duration": (0, 6),
     "notes": ["Video asset must be loaded to YouTube as a public video."]},
    {"id": "ctv", "channel": "ctv", "name": "Connected TV / OTT",
     "kind": "video", "formats": ["mp4"], "size": (1920, 1080),
     "max_bytes": 10 * GB, "duration": (15, 30), "bitrate_kbps": (15000, 30000),
     "fps": [23.98, 29.97], "audio_khz": 48,
     "notes": ["VAST 2.0 / 3.0 compliant.",
               "Connected TV devices are not clickable by nature."]},
    {"id": "native_video", "channel": "native_video", "name": "Native Video",
     "kind": "video", "formats": ["mp4"], "ratios": [(16, 9)],
     "max_bytes": 150 * MB, "duration": (5, 300),
     "notes": ["VAST 2.0 / 3.0 compliant."]},

    # ---- Digital radio ---------------------------------------------------
    {"id": "radio_audio", "channel": "digital_radio", "name": "Audio Spot",
     "kind": "audio", "formats": ["mp3"], "duration": (15, 60),
     "bitrate_kbps": (160, 160)},
    {"id": "radio_companion", "channel": "digital_radio", "name": "Companion Banner",
     "kind": "image", "formats": ["jpg", "jpeg"], "size": (300, 250),
     "notes": ["HTML is not accepted for the companion banner.",
               "A companion banner is highly recommended — it lowers cost and "
               "opens more inventory."]},

    # ---- Digital out of home --------------------------------------------
    # 40 KB is what the kit *asks for*, not what it refuses: "40 KB target /
    # 750 KB max". Carried as `min_bytes` it was a fail, so a clean 30 KB
    # billboard was rejected for being too small against a number nobody
    # published as a floor.
    #
    # The kit also accepts MP4 and HTML5 on every one of these -- a screen
    # sells motion on a loop, and refusing an MP4 here sends somebody back
    # for a flattened still the site would have played.
    {"id": "dooh_1400x400", "channel": "dooh", "name": "Horizontal banner",
     "kind": "image", "size": (1400, 400), **_DOOH_COMMON},
    {"id": "dooh_840x400", "channel": "dooh", "name": "Horizontal banner",
     "kind": "image", "size": (840, 400), **_DOOH_COMMON},
    {"id": "dooh_1080x1920", "channel": "dooh", "name": "Portrait billboard (D6)",
     "kind": "image", "size": (1080, 1920), **_DOOH_COMMON},
    {"id": "dooh_1920x1080", "channel": "dooh", "name": "Landscape billboard",
     "kind": "image", "size": (1920, 1080), **_DOOH_COMMON},

    # ---- Email -----------------------------------------------------------
    {"id": "email_image", "channel": "email", "name": "Email Creative",
     "kind": "image", "formats": ["jpg", "jpeg"],
     "width_range": (600, 750), "max_height": 1728,
     "notes": ["Also required: subject line (50 characters recommended), "
               "sender, seed list, send date and send time."]},
    {"id": "email_html", "channel": "email", "name": "Email HTML Package",
     "kind": "package", "formats": ["zip", "html"],
     "notes": ["Table-based layout with inline CSS; absolute image URLs with "
               "alt text; mobile responsive.",
               "Header, body with CTA, footer with unsubscribe link and "
               "address; subject, preheader and text-only version.",
               "CAN-SPAM compliant, tested across clients and devices.",
               "If no HTML file is provided, Smart 1 can create one for an "
               "additional fee."]},

    # ---- Meta ------------------------------------------------------------
    {"id": "facebook_image", "channel": "facebook", "name": "Facebook Display",
     "kind": "image", "formats": ["jpg", "jpeg", "png"], "ratios": [(1, 1)],
     "max_bytes": 30 * MB, "min_size": (1080, 1080),
     "text": {"headline": 40, "description": 30, "primary_text": 125},
     "notes": ["Image text overlay must be less than 20%."]},
    {"id": "instagram_image", "channel": "instagram", "name": "Instagram Display",
     "kind": "image", "formats": ["jpg", "jpeg", "png"],
     "ratio_range": ((400, 500), (191, 100)), "max_bytes": 1 * GB,
     "min_size": (1080, 1080), "text": {"two_rows": 90},
     "notes": ["Image text overlay must be less than 20%."]},
    {"id": "facebook_video", "channel": "facebook_video", "name": "Facebook Video",
     "kind": "video", "formats": ["mp4", "mov"],
     "ratio_range": ((9, 16), (16, 9)), "min_bytes": 1 * MB,
     "max_bytes": 26 * GB, "duration": (1, 600),
     "text": {"headline": 25, "description": 30}},
    {"id": "facebook_carousel_image", "channel": "facebook_carousel",
     "name": "Carousel Image", "kind": "image", "formats": ["jpg", "jpeg", "png"],
     "max_bytes": 1 * GB, "min_size": (1080, 1080),
     "text": {"primary_text": 125, "headline": 25, "description": 20},
     "notes": ["2 cards minimum, 10 maximum. One landing page per card.",
               "Showcase up to 10 images or videos within a single ad."]},
    {"id": "facebook_carousel_video", "channel": "facebook_carousel",
     "name": "Carousel Video", "kind": "video", "formats": ["mp4", "mov"],
     "max_bytes": 10 * GB, "min_size": (1080, 1080),
     "notes": ["2 cards minimum, 10 maximum. One landing page per card."]},
    {"id": "stories_image", "channel": "stories", "name": "Stories Display",
     "kind": "image", "formats": ["jpg", "jpeg", "png"],
     "ratios": [(9, 16), (16, 9)], "min_width": 500,
     "notes": ["The kit lists 1920x1080 as the image resolution and 9:16 & "
               "16:9 as the ratios; ratio is treated as the rule so a "
               "1080x1920 story is not failed for being vertical.",
               "Leave 14% of the top and bottom free to avoid covering key "
               "elements."]},
    {"id": "stories_video", "channel": "stories", "name": "Stories Video",
     "kind": "video", "formats": ["mp4", "mov"],
     "ratio_range": ((90, 160), (191, 100)), "max_bytes": 4 * GB,
     "duration": (0, 120), "min_width": 500,
     "notes": ["Leave 14% of the top and bottom free.",
               "A 1080x1080 image ad can run in essentially any ad format."]},

    # ---- X (formerly Twitter) -------------------------------------------
    {"id": "x_image_website_card", "channel": "x", "name": "Image Website Card",
     "kind": "image", "formats": ["jpg", "jpeg", "png"],
     "sizes": [(800, 418), (800, 800)], "max_bytes": 3 * MB,
     "text": {"total": 280, "final": 256}},
    {"id": "x_multi_image_mobile", "channel": "x", "name": "Multi Image Tweet — Mobile",
     "kind": "image", "formats": ["jpg", "jpeg", "png"], "size": (600, 335),
     "max_bytes": 1048 * KB, "text": {"total": 280}},
    {"id": "x_multi_image_desktop", "channel": "x", "name": "Multi Image Tweet — Desktop",
     "kind": "image", "formats": ["jpg", "jpeg", "png"], "size": (600, 600),
     "max_bytes": 1048 * KB, "text": {"total": 280}},
    {"id": "x_video_website_card", "channel": "x", "name": "Video Website Card",
     "kind": "video", "formats": ["mp4", "mov"], "ratios": [(16, 9), (1, 1)],
     "max_bytes": 1 * GB, "text": {"total": 280}},
    {"id": "x_conversational", "channel": "x", "name": "Conversational Ad",
     "kind": "image", "formats": ["jpg", "jpeg", "png"], "size": (800, 320),
     "max_bytes": 3 * MB,
     "text": {"total": 256, "hashtag": 21, "pre_populated_tweet": 256,
              "headline": 23, "thank_you": 23}},
    {"id": "x_direct_message", "channel": "x", "name": "Direct Message Card",
     "kind": "image", "formats": ["jpg", "jpeg", "png"], "min_width": 800,
     "max_bytes": 3 * MB, "text": {"total": 256, "cta_button": 24},
     "notes": ["Emoji are supported in the call-to-action button text."]},
    {"id": "x_single_image_mobile", "channel": "x", "name": "Single Image Tweet — Mobile",
     "kind": "image", "formats": ["jpg", "jpeg", "png", "gif"],
     "size": (1200, 675), "max_bytes": 3 * MB, "text": {"total": 280}},
    {"id": "x_single_image_desktop", "channel": "x", "name": "Single Image Tweet — Desktop",
     "kind": "image", "formats": ["jpg", "jpeg", "png", "gif"],
     "min_width": 600, "max_bytes": 3 * MB, "text": {"total": 280}},

    # ---- LinkedIn --------------------------------------------------------
    {"id": "li_single_image", "channel": "linkedin",
     "name": "Sponsored Content — Single Image", "kind": "image",
     "formats": ["jpg", "jpeg", "png"], "size": (1200, 627),
     "max_bytes": 5 * MB,
     "text": {"intro": 150, "headline": 70, "description": 100}},
    {"id": "li_video", "channel": "linkedin", "name": "Sponsored Content — Video",
     "kind": "video", "formats": ["mp4"], "max_width": 1080,
     "min_bytes": 75 * KB, "max_bytes": 200 * MB, "duration": (3, 1800),
     "text": {"intro": 600, "headline": 70}},
    {"id": "li_carousel", "channel": "linkedin",
     "name": "Sponsored Content — Carousel", "kind": "image",
     "formats": ["jpg", "jpeg", "png"], "size": (1080, 1080),
     "max_bytes": 10 * MB,
     "text": {"intro": 150, "headline": 45, "description": 30},
     "notes": ["2 to 10 cards. Headline links to a URL, description to a form."]},
    {"id": "li_text_ad", "channel": "linkedin", "name": "Text Ad",
     "kind": "image", "formats": ["jpg", "jpeg", "png"], "size": (100, 100),
     "max_bytes": 2 * MB, "text": {"headline": 25, "description": 75}},
    {"id": "li_inmail", "channel": "linkedin", "name": "Sponsored InMail",
     "kind": "image", "formats": ["jpg", "jpeg", "png"], "size": (300, 250),
     "max_bytes": 40 * KB,
     "text": {"subject": 60, "message": 1500, "custom_terms": 2500,
              "call_to_action": 20},
     "notes": ["Up to 3 click links."]},

    # ---- Snapchat --------------------------------------------------------
    {"id": "snap_image", "channel": "snapchat", "name": "Single Image Ad",
     "kind": "image", "formats": ["jpg", "jpeg", "png"], "size": (1080, 1920),
     "max_bytes": 5 * MB, "text": {"brand_name": 25, "headline": 34}},
    {"id": "snap_video", "channel": "snapchat", "name": "Video Ad",
     "kind": "video", "formats": ["mp4", "mov"], "size": (1080, 1920),
     "max_bytes": 1 * GB, "duration": (3, 30),
     "text": {"brand_name": 25, "headline": 34}},

    # ---- TikTok ----------------------------------------------------------
    {"id": "tiktok_video", "channel": "tiktok", "name": "In-Feed Video Ad",
     "kind": "video", "formats": ["mp4", "mov"],
     "ratios": [(9, 16), (16, 9), (1, 1)], "max_bytes": 500 * MB,
     "duration": (5, 60),
     "text": {"brand_name": (2, 20), "description": (12, 100)},
     "notes": ["Emojis cannot appear in the brand name.",
               "Emojis, {} and # cannot appear in the description.",
               "Punctuation and spaces occupy characters."]},
    {"id": "tiktok_image", "channel": "tiktok", "name": "In-Feed Image Ad",
     "kind": "image", "formats": ["jpg", "jpeg", "png"], "size": (1200, 628),
     "max_bytes": 500 * KB,
     "text": {"brand_name": (2, 20), "description": (12, 100)}},
    {"id": "tiktok_profile", "channel": "tiktok", "name": "Profile Image",
     "kind": "image", "formats": ["jpg", "jpeg", "png"], "ratios": [(1, 1)],
     "max_bytes": 50 * KB},

    # ---- GPT ads ---------------------------------------------------------
    # NOT from the 2025 kit — this one is transcribed from the platform's own
    # creative requirement sheet, which is why it carries `source`. It is here
    # rather than in the GPT Ads module because the question it answers ("will
    # this file be rejected?") is asked in three places already: on upload, on
    # an insertion order, and by the gallery. A second copy of the numbers in a
    # module is how the "cap the longest edge" rule ended up being fixed twice.
    #
    # 1:1 is *required*, so it is a `ratios` entry and a fail. 256x256 is
    # *recommended*, so it is `min_size` and a warn — a 200px square runs, it
    # just runs soft, and collapsing those two into one warning is what taught
    # people to ignore warnings. No file-weight ceiling is published for this
    # placement, so none is invented: there is no max_bytes here on purpose.
    {"id": "gpt_ads_square", "channel": "gpt_ads", "name": "Static Square Image",
     "kind": "image", "formats": ["jpg", "jpeg", "png"],
     "ratios": [(1, 1)], "min_size": (256, 256),
     "source": "GPT ads creative requirements",
     "notes": ["1:1 is required. 256x256 is the recommended minimum, not the "
               "target — supply the highest-quality brand-approved version, "
               "because the platform can downsize and cannot upsize.",
               "No file-weight ceiling is published for this placement, so "
               "none is enforced here.",
               "No format list is published either. JPG and PNG are what is "
               "accepted here because every ad platform takes them — a WebP "
               "that turns out to be rejected is a launch date missed, and "
               "nothing is gained by being first to try one."]},
]

BY_ID = {u["id"]: u for u in UNITS}

CHANNEL_LABELS = {
    "desktop_display": "Desktop Display",
    "mobile_display": "Mobile Display",
    "tablet_display": "Tablet Display",
    "native_display": "Native Display",
    "standard_video": "Standard Video",
    "youtube": "YouTube Video",
    "ctv": "Connected TV / OTT",
    "native_video": "Native Video",
    "digital_radio": "Digital Radio",
    "dooh": "Digital Out of Home",
    "email": "Standard Email",
    "facebook": "Facebook Display",
    "instagram": "Instagram Display",
    "facebook_video": "Facebook Video",
    "facebook_carousel": "Facebook Carousel",
    "stories": "Facebook & Instagram Stories",
    "x": "X, formerly Twitter",
    "linkedin": "LinkedIn",
    "snapchat": "Snapchat",
    "tiktok": "TikTok",
    "gpt_ads": "GPT Ads",
}

# Product text -> channels, most specific pattern first. A product can map to
# several channels: a display buy legitimately accepts desktop, mobile and
# tablet units, and a file only has to satisfy one of them.
_PRODUCT_CHANNELS: list[tuple[str, list[str]]] = [
    # First, because "GPT display" must not be read as a display buy by the
    # generic `display` pattern near the bottom of this list.
    # "GPT" alone, because the product is written a dozen ways — "GPT Ads",
    # "ChatGPT display ads", "OpenAI ads" — and "GPT display ads" reaching the
    # generic display pattern below would judge a 1:1 square against banner
    # sizes and pass nothing.
    (r"chat\s*gpt|\bgpt\b|openai\s+ads?", ["gpt_ads"]),
    (r"native.*video", ["native_video"]),
    (r"native", ["native_display"]),
    (r"you\s*tube|trueview|bumper", ["youtube"]),
    (r"connected tv|advanced tv|\bott\b|streaming tv", ["ctv"]),
    (r"tik\s*tok", ["tiktok"]),
    (r"snap\s*chat", ["snapchat"]),
    (r"linked\s*in", ["linkedin"]),
    (r"\btwitter\b|\bx ads\b", ["x"]),
    (r"stor(y|ies)", ["stories"]),
    (r"carousel", ["facebook_carousel"]),
    (r"instagram", ["instagram", "stories"]),
    # Pinterest is on the rate card and is in no part of the kit -- S1M
    # CREATIVE SPEC KIT 2025 publishes no Pinterest section at all. Its own
    # name matches nothing above, so without this entry it fell to the
    # `social ads?` pattern below, which was matching the *category*
    # ("SOCIAL ADS - VIDEO") rather than anything about the product: a
    # Pinterest buy was asked for Facebook and Instagram units. Not a near
    # miss -- a 1:1 feed square and a 9:16 story against a platform whose
    # feed is 2:3, so a client who supplied exactly what was asked for
    # delivers creative Pinterest crops, and every screen reads as correct
    # while it happens. Snapchat, TikTok, X and LinkedIn each have a name
    # rule above and are right; this is the one platform the category was
    # answering for. It maps to nothing, so `required_units()` says the kit
    # maps no unit for it -- the rule this module already works to, that a
    # format the kit maps no unit for is *not measured* and never judged
    # against the nearest channel.
    (r"pinterest", []),
    (r"facebook|\bmeta\b|social ads?", ["facebook", "instagram",
                                        "facebook_video", "facebook_carousel",
                                        "stories"]),
    (r"radio|podcast|audio", ["digital_radio"]),
    # ...and only below it. Four programmatic *video* products are filed
    # under the card's DISPLAY heading beside banner inventory, and three of
    # their names contain no word that says so -- "Programmatic - Targeted"
    # is $17.00 CPM video while "Category" next to it is $4.25 CPM display.
    # Without this they fall to the generic `display|programmatic` pattern
    # near the bottom and a video buy is told to deliver a 728x90 and a
    # 300x250. It sits *under* the audio rule because "Programmatic -
    # Targeted" is also the $18.00 CPM buy under DIGITAL RADIO, and that one
    # needs a spot rather than a video. The same four are named in
    # `creative_needs.EXPLICIT_MEDIUM` for the same reason, and the two lists
    # are asserted to agree: a product renamed on the card must not silently
    # revert to the guess in either of them.
    (r"premium:\s*non-?skippable"
     r"|programmatic\s*-\s*(targeted|ron\b|run of network)", ["standard_video"]),
    (r"signage|out of home|\bdooh\b|billboard", ["dooh"]),
    (r"e-?mail|admail", ["email"]),
    (r"online video|pre-?roll|\bvideo\b", ["standard_video"]),
    (r"display|programmatic|retarget|geo-?fenc|location lookback|"
     r"ip target|data targeted", ["desktop_display", "mobile_display",
                                  "tablet_display"]),
    (r"mobile", ["mobile_display"]),
]


# --------------------------------------------------------------------------
# Holding the transcription to the kit we publish
#
# These numbers are transcribed on purpose -- SPEC_KIT_URL is a link and not a
# fetch, because a spec table pulled live changes what a check says with no
# diff to point at. What that argument never covered is the transcription
# going stale, which it had:
#
#   * Half Page was enforced at 150 KB against a published 250 KB, and 970x250
#     with it -- so the checker refused files the kit allows;
#   * a smartphone banner was allowed 150 KB against a published 50 KB, the
#     same fault running the other way;
#   * 970x250 was called "Rising Star", a programme the IAB retired, so the
#     kit a client is sent and the verdict we hand them named one unit two
#     things;
#   * the mobile interstitial was sized 320x480, which is on none of the three
#     rows the kit sells it at.
#
# The page ships in this repo (`hub/partner_pages/creative-specs.html`), so
# the transcription is checkable rather than remembered. This reads the unit
# tables out of it and compares. It stays a *check* rather than becoming the
# source: a table parsed at import would change a verdict the day somebody
# edits the page, which is the thing the transcription exists to prevent.
# --------------------------------------------------------------------------
_KIT_PAGE = pathlib.Path(__file__).with_name("partner_pages") / "creative-specs.html"

# Which page section holds each channel's unit table. Only the channels whose
# table is Unit / Dimensions / weight are listed: the social sections publish
# prose per format rather than a dimension column, and a parser that guessed
# at those would report drift that is not there.
_KIT_SECTIONS = {"desktop-display": "desktop_display",
                 "mobile-display": "mobile_display",
                 "dooh": "dooh"}


def _kit_rows() -> tuple[dict, str]:
    """Every (channel, w, h) the published page sells, with its weight."""
    try:
        html = _KIT_PAGE.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {}, f"the published kit could not be read ({exc})"

    def _text(x):
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x)).strip()

    out = {}
    for sid, channel in _KIT_SECTIONS.items():
        m = re.search(rf'<section class="section" id="{sid}"(.*?)</section>', html, re.S)
        if not m:
            return {}, f"the published kit has no {sid} section"
        table = re.search(r"<table>(.*?)</table>", m.group(1), re.S)
        if not table:
            return {}, f"the published kit's {sid} section has no table"
        for tr in re.findall(r"<tr>(.*?)</tr>", table.group(1), re.S):
            cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(cells) < 3:
                continue
            dim = re.match(r"^(\d+)x(\d+)$", cells[1])
            if not dim:
                continue
            # A weight column carries one figure or two, and where there are
            # two they are labelled: DOOH publishes "40 KB target / 750 KB
            # max". Taking the first would hold a billboard to the number
            # the kit asks for rather than the one it refuses -- the same
            # confusion of a target with a ceiling this change is undoing.
            weight = cells[2]
            hit = re.search(r"([\d.]+)\s*KB[^/]*\bmax\b", weight, re.I)
            if not hit:
                hit = re.search(r"([\d.]+)\s*KB", weight, re.I)
            target = re.search(r"([\d.]+)\s*KB[^/]*\btarget\b", weight, re.I)
            out[(channel, int(dim.group(1)), int(dim.group(2)))] = {
                "name": cells[0],
                "max_bytes": int(float(hit.group(1)) * KB) if hit else None,
                "target_bytes": int(float(target.group(1)) * KB) if target else None,
            }
    return out, ""


def kit_drift() -> list[dict]:
    """Where the transcription and the published kit disagree.

    Empty is the only acceptable answer. A row here is a file refused that
    the client was told to send, or accepted that they were told not to --
    and both are invisible from either end, because each document is
    internally consistent.
    """
    rows, error = _kit_rows()
    if error:
        # Not measured, never a clean bill of health: a page that will not
        # parse is the one state where "no drift" would be a lie.
        return [{"unit": "", "detail": f"Not measured — {error}."}]
    out = []
    for unit in UNITS:
        if unit["channel"] not in _KIT_SECTIONS.values():
            continue
        for (w, h) in _sizes_of(unit):
            published = rows.get((unit["channel"], w, h))
            if not published:
                out.append({"unit": unit["id"], "detail":
                            f"{unit['name']} {w}x{h} is not a size the "
                            f"published kit sells."})
                continue
            if published["name"].lower() != unit["name"].lower():
                out.append({"unit": unit["id"], "detail":
                            f"{w}x{h} is \"{unit['name']}\" here and "
                            f"\"{published['name']}\" on the kit the client "
                            f"is sent."})
            want_target = published.get("target_bytes")
            if want_target and unit.get("target_bytes") != want_target:
                out.append({"unit": unit["id"], "detail":
                            f"{unit['name']} {w}x{h} carries no "
                            f"{_fmt_bytes(want_target)} target, which the kit "
                            f"publishes."})
            want = published["max_bytes"]
            if want and unit.get("max_bytes") != want:
                out.append({"unit": unit["id"], "detail":
                            f"{unit['name']} {w}x{h} is judged at "
                            f"{_fmt_bytes(unit.get('max_bytes') or 0)} against "
                            f"the kit's {_fmt_bytes(want)}."})
    return out


def channels_for_product(product: str = "", category: str = "") -> list[str]:
    """Which spec-kit channels a product's creative is judged against."""
    blob = f"{category} {product}".lower()
    for pattern, channels in _PRODUCT_CHANNELS:
        if re.search(pattern, blob):
            return channels
    return []


def units_for_product(product: str = "", category: str = "") -> list[dict]:
    chans = set(channels_for_product(product, category))
    return [u for u in UNITS if u["channel"] in chans]


def _ratio(w: int, h: int) -> float:
    return float(w) / float(h) if h else 0.0


def _sizes_of(unit: dict) -> list[tuple[int, int]]:
    return unit.get("sizes") or ([unit["size"]] if unit.get("size") else [])


def _fmt_bytes(n: float) -> str:
    for unit, size in (("GB", GB), ("MB", MB), ("KB", KB)):
        if n >= size:
            v = n / size
            return f"{v:.0f} {unit}" if v >= 10 else f"{v:.1f} {unit}"
    return f"{int(n)} bytes"


def _kind_of(fmt: str) -> str:
    fmt = (fmt or "").lower().lstrip(".")
    if fmt in ("mp4", "mov", "webm", "flv", "m4v"):
        return "video"
    if fmt in ("mp3", "wav", "m4a", "aac"):
        return "audio"
    if fmt in ("zip", "html", "htm"):
        return "package"
    return "image"


def _score(unit: dict, width: int, height: int, fmt: str,
           duration: float | None = None) -> tuple:
    """How well a file fits a unit, for picking which unit to judge it against.

    Higher is better. Dimension agreement dominates: a 300x250 file is being
    offered as a Medium Rectangle even if it is 400 KB, and telling the user it
    is 250 KB over the cap is far more useful than telling them it is not a
    Leaderboard.

    Length matters for the same reason. On YouTube, TrueView and Bumper differ
    almost entirely by length — a six-second cut is a Bumper, and reporting it
    as a TrueView that is too short inverts the actual finding.
    """
    s = 0
    if _kind_of(fmt) == unit.get("kind"):
        s += 8
    if fmt and fmt in (unit.get("formats") or []):
        s += 2
    if duration and unit.get("duration"):
        lo, hi = unit["duration"]
        s += 25 if lo <= duration <= hi else 0
    if width and height:
        if any(w == width and h == height for w, h in _sizes_of(unit)):
            s += 100
        elif unit.get("ratios"):
            r = _ratio(width, height)
            if any(abs(r - _ratio(w, h)) < 0.03 for w, h in unit["ratios"]):
                s += 40
        elif unit.get("ratio_range"):
            lo, hi = unit["ratio_range"]
            if _ratio(*lo) - 0.03 <= _ratio(width, height) <= _ratio(*hi) + 0.03:
                s += 30
        if unit.get("width_range") and \
                unit["width_range"][0] <= width <= unit["width_range"][1]:
            s += 40
        if unit.get("min_width") and width >= unit["min_width"]:
            s += 10
    return (s,)


def _check_one(unit: dict, *, width, height, size_bytes, fmt, duration) -> list[dict]:
    """Every rule this unit imposes, evaluated. Order is presentation order."""
    out: list[dict] = []

    def add(status, label, detail):
        out.append({"status": status, "label": label, "detail": detail})

    formats = unit.get("formats") or []
    if fmt and formats:
        if fmt in formats:
            add("pass", "File type", f".{fmt} is accepted")
        else:
            add("fail", "File type",
                f".{fmt} is not accepted — {unit['name']} takes "
                + " / ".join("." + f for f in formats))

    sizes = _sizes_of(unit)
    if width and height:
        if sizes:
            if any(w == width and h == height for w, h in sizes):
                add("pass", "Dimensions", f"{width}x{height} matches {unit['name']}")
            else:
                add("fail", "Dimensions",
                    f"{width}x{height} is not a {unit['name']} size — "
                    + " or ".join(f"{w}x{h}" for w, h in sizes))
        if unit.get("ratios"):
            r = _ratio(width, height)
            if any(abs(r - _ratio(w, h)) < 0.03 for w, h in unit["ratios"]):
                add("pass", "Aspect ratio", f"{width}x{height} is an accepted ratio")
            else:
                add("fail", "Aspect ratio",
                    f"{width}x{height} is not "
                    + " or ".join(f"{w}:{h}" for w, h in unit["ratios"]))
        if unit.get("ratio_range"):
            lo, hi = unit["ratio_range"]
            if _ratio(*lo) - 0.03 <= _ratio(width, height) <= _ratio(*hi) + 0.03:
                add("pass", "Aspect ratio", f"{width}x{height} is in range")
            else:
                add("fail", "Aspect ratio",
                    f"{width}x{height} is outside {lo[0]}x{lo[1]} to {hi[0]}x{hi[1]}")
        if unit.get("min_size"):
            mw, mh = unit["min_size"]
            if width >= mw and height >= mh:
                add("pass", "Resolution",
                    f"{width}x{height} meets the recommended minimum")
            else:
                # Recommended, not required. It runs; it just runs soft.
                add("warn", "Resolution",
                    f"{width}x{height} is below the recommended {mw}x{mh} minimum")
        if unit.get("min_width"):
            if width >= unit["min_width"]:
                add("pass", "Width", f"{width}px meets the {unit['min_width']}px minimum")
            else:
                add("fail", "Width",
                    f"{width}px is under the {unit['min_width']}px minimum")
        if unit.get("max_width") and width > unit["max_width"]:
            add("fail", "Width", f"{width}px exceeds the {unit['max_width']}px maximum")
        if unit.get("width_range"):
            lo, hi = unit["width_range"]
            if lo <= width <= hi:
                add("pass", "Width", f"{width}px is within {lo}-{hi}px")
            else:
                add("fail", "Width", f"{width}px is outside the {lo}-{hi}px range")
        if unit.get("max_height") and height > unit["max_height"]:
            add("fail", "Height",
                f"{height}px exceeds the {unit['max_height']}px single-image maximum")

    if size_bytes:
        if unit.get("max_bytes"):
            if size_bytes <= unit["max_bytes"]:
                add("pass", "File size",
                    f"{_fmt_bytes(size_bytes)} is within "
                    f"{_fmt_bytes(unit['max_bytes'])}")
            else:
                add("fail", "File size",
                    f"{_fmt_bytes(size_bytes)} exceeds the "
                    f"{_fmt_bytes(unit['max_bytes'])} maximum")
        if unit.get("min_bytes") and size_bytes < unit["min_bytes"]:
            add("fail", "File size",
                f"{_fmt_bytes(size_bytes)} is under the "
                f"{_fmt_bytes(unit['min_bytes'])} minimum")

    if duration and unit.get("duration"):
        lo, hi = unit["duration"]
        if lo <= duration <= hi:
            add("pass", "Length", f"{duration:.1f}s is within {lo}-{hi}s")
        else:
            add("fail", "Length",
                f"{duration:.1f}s is outside the {lo}-{hi}s the kit allows")

    return out


def check(*, width: int = 0, height: int = 0, size_bytes: int = 0,
          fmt: str = "", duration: float | None = None,
          product: str = "", category: str = "",
          unit_id: str = "") -> dict:
    """Judge one file against the kit.

    Pass `unit_id` to test a specific unit, or `product`/`category` to let the
    product decide which channels apply. With a product, the file is judged
    against the unit it best fits — see `_score`.
    """
    fmt = (fmt or "").lower().lstrip(".")
    width, height = int(width or 0), int(height or 0)
    size_bytes = int(size_bytes or 0)

    if unit_id:
        candidates = [BY_ID[unit_id]] if unit_id in BY_ID else []
    else:
        candidates = units_for_product(product, category)

    if not candidates:
        return {
            "result": "unknown",
            "summary": ("No specification is mapped for this product. "
                        "Check the creative against the spec kit by hand."),
            "unit": None, "channel": None, "checks": [], "alternatives": [],
        }

    # Judge against the closest unit, but only among units of the right media
    # kind where one exists — a video should never be reported as failing to be
    # a 300x250 banner.
    kind = _kind_of(fmt) if fmt else ""
    same_kind = [u for u in candidates if not kind or u.get("kind") == kind]
    pool = same_kind or candidates
    best = max(pool, key=lambda u: _score(u, width, height, fmt, duration))

    checks = _check_one(best, width=width, height=height,
                        size_bytes=size_bytes, fmt=fmt, duration=duration)

    # A file that matches no unit in the buy gets judged against whichever one
    # scored highest, which is close to arbitrary. Naming that one unit —
    # "301x250 is not a Leaderboard size" — answers a question nobody asked.
    # What the designer needs is every size this buy will actually take.
    if width and height and not any(
            w == width and h == height for u in pool for w, h in _sizes_of(u)):
        every = sorted({s for u in pool for s in _sizes_of(u)},
                       key=lambda s: (-s[0] * s[1], s))
        if every:
            for c in checks:
                if c["label"] == "Dimensions" and c["status"] == "fail":
                    c["detail"] = (
                        f"{width}x{height} is not a size this buy accepts — "
                        + ", ".join(f"{w}x{h}" for w, h in every))

    failed = [c for c in checks if c["status"] == "fail"]
    warned = [c for c in checks if c["status"] == "warn"]

    result = "fail" if failed else ("warn" if warned else "pass")
    if result == "fail":
        summary = (failed[0]["detail"] if len(failed) == 1
                   else f"{len(failed)} problems: "
                        + "; ".join(c["detail"] for c in failed))
    elif result == "warn":
        summary = warned[0]["detail"]
    else:
        label = CHANNEL_LABELS.get(best["channel"], best["channel"])
        summary = f"Meets {label} spec as {best['name']}"

    # When a file fails on dimensions, the useful next question is "what would
    # it pass as?" — often it is already a valid unit on another channel in the
    # same buy.
    alternatives = []
    if failed and width and height:
        for u in candidates:
            if u is best:
                continue
            if any(w == width and h == height for w, h in _sizes_of(u)):
                alternatives.append({
                    "id": u["id"], "name": u["name"],
                    "channel": CHANNEL_LABELS.get(u["channel"], u["channel"])})

    return {
        "result": result,
        "summary": summary,
        "unit": {"id": best["id"], "name": best["name"],
                 "channel": best["channel"],
                 "channel_label": CHANNEL_LABELS.get(best["channel"], best["channel"]),
                 "notes": best.get("notes") or []},
        "channel": best["channel"],
        "checks": checks,
        "alternatives": alternatives,
    }


def _slug(v: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(v or "").lower().strip()).strip("_")
    return s[:80] or "unknown"


def tags_for(verdict: dict, *, product: str = "", client: str = "",
             width: int = 0, height: int = 0, size_bytes: int = 0,
             when: _dt.date | None = None) -> list[str]:
    """Cloudinary tags recording what was uploaded and whether it passed.

    The gallery filters on these, so creative can be found later by client, by
    product, by date or by whether it passed spec — which is the whole reason
    for tagging rather than trusting a filename. Dates are mm-dd-yy, matching
    the rest of the hub.
    """
    when = when or _dt.date.today()
    tags = [
        f"client_{_slug(client)}" if client else "",
        f"product_{_slug(product)}" if product else "",
        f"date_{when.strftime('%m-%d-%y')}",
        f"spec_{verdict.get('result', 'unknown')}",
    ]
    if width and height:
        tags.append(f"dim_{width}x{height}")
    if size_bytes:
        tags.append(f"size_{max(1, round(size_bytes / KB))}kb")
    unit = (verdict.get("unit") or {})
    if unit.get("id"):
        tags.append(f"unit_{unit['id']}")
    return [t for t in tags if t]


def catalogue() -> dict:
    """The whole kit, shaped for the browser."""
    chans: dict[str, dict] = {}
    for u in UNITS:
        c = chans.setdefault(u["channel"], {
            "id": u["channel"],
            "label": CHANNEL_LABELS.get(u["channel"], u["channel"]),
            "units": [],
        })
        c["units"].append({k: v for k, v in u.items() if k != "channel"})
    # Most units come from the kit; a few (GPT ads) come from a platform's own
    # requirement sheet and carry their own `source`. Saying "Creative Spec Kit
    # 2025" over all of them would misattribute those.
    return {"channels": list(chans.values()),
            "source": "S1M Creative Spec Kit 2025, plus the platform "
                      "requirement sheets noted on individual units",
            "source_url": SPEC_KIT_URL}
