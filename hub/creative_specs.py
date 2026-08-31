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
import html as _html
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

    # ---- Tablet display: retired -------------------------------------------
    # These four were house guidance -- desktop weights against tablet
    # dimensions -- carried because the kit published no tablet section. The
    # 2026 kit does now, in one sentence under Native Display: "Tablet Display
    # retired as a category -- IAB removed device-class ad units. 300x250 and
    # 728x90 serve on tablet as the same units." They are in RETIRED_UNITS.

    # ---- Native display ----------------------------------------------------
    # Transcribed against the 2026 kit, which is per platform: The Trade Desk
    # and Google Demand Gen declare different sizes and different character
    # limits for the same asset, because OpenRTB Native 1.2 sets none and each
    # seller declares its own. The kit's instruction is to "build to the
    # strictest platform in the plan", so that is what each field carries and
    # the looser platform is named in the notes.
    #
    # The old `headline: (15, 55)` / `description: (25, 120)` is quoted by the
    # kit's own update note as the thing that is wrong -- "character limits
    # are per-platform, not a single 15-55 / 25-120 range" -- so a client was
    # being told a headline of 55 was fine where The Trade Desk takes 25.
    {"id": "native_image", "channel": "native_display", "name": "Main image",
     "kind": "image", "size": (1200, 627), "max_bytes": 750 * KB,
     "formats": ["gif", "jpg", "jpeg", "png"],
     # Strictest wins: TTD's 25-character short title against Demand Gen's 40.
     "text": {"short_title": 25, "long_title": 90,
              "short_description": 90, "long_description": 140},
     "notes": ["The Trade Desk publishes 1200x627 (1.91:1).",
               "Google Demand Gen requires 1.91:1, 1:1 and 4:5 — all three, "
               "not a choice between them.",
               "Character limits are declared per seller per placement. "
               "These are the strictest in the plan: Demand Gen allows a "
               "40-character short title, and publishes no long description.",
               "1:1 and 4:5 are first-class native ratios now rather than "
               "optional extras."]},
    {"id": "native_logo", "channel": "native_display", "name": "Brand logo",
     "kind": "image", "size": (200, 200), "max_bytes": 150 * KB,
     "formats": ["gif", "jpg", "jpeg", "png"],
     "notes": ["The Trade Desk publishes 200x200 (1:1).",
               "Google Demand Gen takes 1:1 and 4:1, capped at 150 KB — the "
               "stricter of the two weights, so it is the one carried."]},
    # Two asset fields the kit publishes that nothing here ever asked for. A
    # native ad renders them; a client who is not asked simply does not supply
    # them, and whoever traffics the campaign types something in.
    {"id": "native_business_name", "channel": "native_display",
     "name": "Business name", "kind": "text", "text": {"total": 25},
     "notes": ["Google Demand Gen only; The Trade Desk publishes no business "
               "name field."]},
    {"id": "native_cta", "channel": "native_display",
     "name": "Call to action", "kind": "text", "text": {"total": 15},
     "notes": ["The Trade Desk only; Google Demand Gen publishes no CTA "
               "character limit.",
               "New in the 2026 kit."]},
    {"id": "native_html5", "channel": "native_display",
     "name": "HTML5 package", "kind": "package", "formats": ["zip"],
     "max_bytes": 300 * KB,
     "notes": ["Raw HTML5 files zipped, or a third-party ad tag.",
               "Initial-load weight is platform-specific: The Trade Desk "
               "allows 300 KB initial load, 200 KB recommended. Display & "
               "Video 360 allows 5 MB total download across at most 100 "
               "files, measured gzipped across font, image, audio, video, "
               "CSS and HTML combined.",
               "The IAB caps file requests at 10 during initial load; DV360 "
               "permits up to 100 HTTP calls per ad. Unlimited after user "
               "interaction.",
               "Clicks must open in a new window or tab.",
               "Must not use any element designed to misleadingly generate a "
               "click, and must not lead to malware, spyware or viruses."]},

    # ---- Video -----------------------------------------------------------
    {"id": "standard_video", "channel": "standard_video", "name": "Standard Video",
     "kind": "video", "formats": ["mp4"], "ratios": [(16, 9), (4, 3)],
     "max_bytes": 10 * GB, "duration": (15, 60), "bitrate_kbps": (15000, 30000),
     "notes": ["VAST 2.0 / 3.0 compliant.",
               "Third-party VAST must contain MP4 and FLV format videos."]},
    # Transcribed against the 2026 kit. "TrueView" is no longer a format name
    # at all -- Google repurposed it in October 2025 as a *metric*, TrueView
    # views, spanning skippable in-stream, in-feed, Shorts and Masthead -- so
    # the requirement line asked a client to supply a thing that does not
    # exist. The id survives because the format survives in substance
    # (skippable in-stream is what TrueView was) and `tags_for()` has written
    # `unit_youtube_trueview` onto delivered creative: the rule `billboard`
    # already follows from when the IAB retired the Rising Stars name.
    #
    # The weight was the half that refused real work: 10 MB against a
    # published 256 GB, which is the kit's own "wrong by four orders of
    # magnitude". A checker refusing files the client was told to send is the
    # Half Page failure this module already carries a note about, and the
    # third transcription of four to run that way.
    {"id": "youtube_trueview", "channel": "youtube",
     "name": "Skippable in-stream", "kind": "video",
     "formats": ["mpg", "mpeg", "mp4", "mov", "webm"],
     "ratios": [(16, 9), (9, 16), (1, 1)], "max_bytes": 256 * GB,
     # No duration: the kit publishes "no maximum, under 3:00 recommended",
     # and a ceiling invented from the recommendation would refuse a cut the
     # kit permits -- the `target_bytes` lesson, wearing a stopwatch.
     "text": {"headline": 40, "description": 35},
     "notes": ["Skip becomes available at 5 seconds.",
               "No maximum length; under 3:00 recommended. Google Ads "
               "reservations run :12 minimum to 6:00 maximum.",
               "Headline is 40 characters per line over two lines, and the "
               "description 35 per line over two.",
               "Video Action campaigns take their own copy: headline 30, "
               "long headline 90, description 90, call to action 10.",
               "16:9 is 1920x1080, 9:16 is 1080x1920 and 1:1 is 1080x1080.",
               "Video asset must be loaded to YouTube. Google's ad policy "
               "requires a public video; the Shorts asset page permits "
               "unlisted. Use public unless a Google rep confirms otherwise "
               "for the campaign type."]},
    {"id": "youtube_nonskippable", "channel": "youtube",
     "name": "Non-skippable in-stream", "kind": "video",
     "formats": ["mpg", "mpeg", "mp4", "mov", "webm"],
     "ratios": [(16, 9), (9, 16), (1, 1)], "max_bytes": 256 * GB,
     "duration": (7, 30),
     "notes": [":07 to :15 is standard; :16 to :30 runs on CTV.",
               "The policy cap is :30 on auction and :60 on reservation, so a "
               "cut over :30 is a reservation buy rather than a free choice.",
               "Video asset must be loaded to YouTube as a public video."]},
    {"id": "youtube_bumper", "channel": "youtube", "name": "Bumper",
     "kind": "video", "formats": ["mpg", "mpeg", "mp4", "mov", "webm"],
     "ratios": [(16, 9), (9, 16), (1, 1)], "max_bytes": 256 * GB,
     "duration": (0, 6),
     "notes": ["Non-skippable.",
               "Video asset must be loaded to YouTube as a public video."]},
    {"id": "youtube_in_feed", "channel": "youtube", "name": "In-feed video",
     "kind": "video", "formats": ["mpg", "mpeg", "mp4", "mov", "webm"],
     "ratios": [(16, 9), (9, 16), (1, 1)], "max_bytes": 256 * GB,
     "text": {"headline": 40, "description": 35},
     "notes": ["Formerly TrueView Discovery.",
               "No maximum length specified.",
               "Thumbnail 1280x720 (1280x640 minimum), 16:9, under 2 MB, "
               "JPG, GIF or PNG.",
               "Video asset must be loaded to YouTube as a public video."]},
    {"id": "youtube_shorts", "channel": "youtube", "name": "YouTube Shorts",
     "kind": "video", "formats": ["mpg", "mpeg", "mp4", "mov", "webm"],
     "ratios": [(9, 16)], "max_bytes": 256 * GB, "duration": (0, 180),
     "notes": ["The feed shows the first :60 only; under :60 recommended.",
               ":06 to :60 in Video Reach campaigns, :10 to :30 for action.",
               "CTA overlay copy: headline 40, description 90, channel "
               "description 90."]},
    {"id": "youtube_masthead", "channel": "youtube", "name": "Masthead",
     "kind": "video", "formats": ["mpg", "mpeg", "mp4", "mov", "webm"],
     "size": (1920, 1080), "max_bytes": 256 * GB,
     "notes": ["Any length; over :10 recommended.",
               "Companion banner 300x60, 5:1, under 150 KB — desktop only.",
               "Video asset must be loaded to YouTube as a public video."]},
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
    # Transcribed against the 2026 kit. The 2025 model was eight units and not
    # one of its names is a format X still sells: "Website Card" and "Direct
    # Message Card" are retired, and the mobile/desktop pairs modelled a split
    # the page says in as many words is gone -- "the mobile-versus-desktop
    # creative split is gone. one asset set serves both." So a client was
    # being asked to supply four things that do not exist and two of them
    # twice, on the requirement line the client document prints.
    #
    # Ids are kept wherever the format survives in substance, because
    # `tags_for()` has written `unit_<id>` onto delivered creative in
    # Cloudinary and a gallery filters on it -- the rule this file already
    # works to for `billboard`, which kept its id when the IAB retired the
    # Rising Stars name. The four with no 2026 equivalent are in
    # RETIRED_UNITS below rather than deleted, for the same reason.
    {"id": "x_image_website_card", "channel": "x", "name": "Image Ads",
     "kind": "image", "formats": ["jpg", "jpeg", "png"],
     "sizes": [(1080, 1080), (2064, 1080), (1920, 1080),
               (1440, 1800), (1080, 1620), (1080, 1920)],
     "max_bytes": 5 * MB,
     # 280 with no deduction: the page says media no longer consumes
     # characters, which retires the old `final: 256`.
     "text": {"total": 280, "headline": 70}},
    {"id": "x_video_website_card", "channel": "x", "name": "Video Ads",
     "kind": "video", "formats": ["mp4", "mov"],
     "max_bytes": 1 * GB, "duration": (0, 140),
     "text": {"total": 280, "headline": 70},
     "notes": ["Up to 2:20; :15 recommended.",
               "29.97 or 30 fps; 60 fps accepted. H.264 with AAC LC.",
               "Under 30 MB recommended."]},
    {"id": "x_vertical_video", "channel": "x", "name": "Vertical Video Ads",
     "kind": "video", "formats": ["mp4", "mov"], "ratios": [(9, 16)],
     "min_width": 720, "max_bytes": 1 * GB, "duration": (0, 140),
     "text": {"total": 280},
     "notes": ["720x1280 minimum to 1080x1920 maximum.",
               "Under :15 recommended. 60 fps maximum.",
               "5-10 Mbps target, 25 Mbps maximum."]},
    # The multi-image pair is one carousel now. The mobile id carries it: a
    # carousel is the multi-image format, and keeping one of the two ids is
    # what stops the tag orphaning.
    {"id": "x_multi_image_mobile", "channel": "x", "name": "Carousel Ads",
     "kind": "image", "formats": ["jpg", "jpeg", "png"],
     "sizes": [(800, 418), (800, 800)], "max_bytes": 5 * MB,
     "text": {"total": 280},
     "notes": ["2 to 6 slides. The file size is per slide."]},
    {"id": "x_conversational", "channel": "x", "name": "Conversation Button",
     "kind": "image", "formats": ["jpg", "jpeg", "png"],
     "sizes": [(1080, 1080), (1920, 1080)], "max_bytes": 5 * MB,
     "text": {"total": 280, "hashtag": 21, "pre_populated_tweet": 256,
              "headline": 23, "thank_you": 23},
     "notes": ["Up to 4 buttons; emoji supported.",
               "Media follows the standard image specs."]},
    {"id": "x_amplify_preroll", "channel": "x", "name": "Amplify Pre-roll",
     "kind": "video", "formats": ["mp4", "mov"], "ratios": [(1, 1)],
     "min_width": 600, "max_bytes": 1 * GB, "duration": (0, 140),
     "text": {"total": 280},
     "notes": [":15 recommended, 2:20 maximum.",
               "1:1 recommended; 600x600 minimum, 1200x1200 recommended."]},
    {"id": "x_spotlight_takeover", "channel": "x", "name": "Spotlight Takeover",
     "kind": "image", "formats": ["jpg", "jpeg", "png", "gif"],
     "size": (1280, 720), "max_bytes": 5 * MB,
     "text": {"trend_name": 20, "description": 30},
     "notes": ["3 to 6 companion ads at 16:9.",
               "5 MB for an image, 15 MB for a GIF."]},
    # No media and no file of any kind -- it is copy and a duration. Modelled
    # so the requirement can name it; there is nothing here to judge a file
    # against, and `check()` is never handed one.
    {"id": "x_polls", "channel": "x", "name": "Polls",
     "kind": "other", "formats": [],
     "text": {"total": 280, "option": 25},
     "notes": ["2 to 4 options, 25 characters each.",
               "Runs 5 minutes to 7 days."]},
    # ---- LinkedIn --------------------------------------------------------
    # Transcribed against the 2026 kit. The 2025 model held five formats to
    # the kit's eleven, and three of its numbers refused files the kit itself
    # allows: Message Ads were capped at 40 KB against a published 2 MB,
    # Sponsored Content video at 200 MB against 500 MB, and that video carried
    # a `max_width` of 1080 while the kit publishes 1920. Each is the Half Page
    # failure this file already records -- creative refused that the client was
    # told to send.
    #
    # "Sponsored InMail" is what LinkedIn used to call Message Ads, and the id
    # is kept through the rename for the reason `billboard` already gives:
    # tags_for() has written `unit_li_inmail` onto delivered creative and a
    # gallery filters on it.
    {"id": "li_single_image", "channel": "linkedin",
     "name": "Sponsored Content \u2014 Single Image",
     "kind": "image", "formats": ["jpg", "jpeg", "png", "gif"],
     "sizes": [(1200, 628), (1200, 1200), (600, 900), (720, 900)],
     "max_bytes": 5 * MB,
     "text": {"intro": 150, "headline": 70, "description": 70},
     "notes": ["600x900 and 720x900 are mobile-only.",
               "The 70-character description shows on the LinkedIn Audience "
               "Network only."]},
    {"id": "li_video", "channel": "linkedin", "name": "Sponsored Content \u2014 Video",
     "kind": "video", "formats": ["mp4"],
     "ratios": [(9, 16), (1, 1), (16, 9), (4, 5)],
     "min_width": 360, "min_bytes": 75 * KB, "max_bytes": 500 * MB,
     "duration": (3, 1800),
     "text": {"intro": 600, "headline": 200},
     "notes": ["9:16 to 1080x1920, 1:1 to 1920x1920, 16:9 to 1920x1080, "
               "4:5 to 1080x1350.",
               "150 characters of intro and a 70-character headline are "
               "recommended; 200 is the headline maximum.",
               "Length across Sponsored Content is :03 to 30 minutes."]},
    {"id": "li_carousel", "channel": "linkedin",
     "name": "Sponsored Content \u2014 Carousel",
     "kind": "image", "formats": ["jpg", "jpeg", "png"],
     "size": (1080, 1080), "max_bytes": 10 * MB,
     "text": {"intro": 255, "headline": 45},
     "notes": ["2 to 10 cards, 1:1, 1080x1080 minimum.",
               "Video is not supported in a carousel."]},
    {"id": "li_text_ad", "channel": "linkedin", "name": "Text Ad",
     "kind": "image", "formats": ["jpg", "jpeg", "png"],
     "size": (100, 100), "max_bytes": 2 * MB,
     "text": {"headline": 25, "description": 75}},
    {"id": "li_inmail", "channel": "linkedin", "name": "Message Ads",
     "kind": "image", "formats": ["jpg", "jpeg", "png"],
     "size": (300, 250), "max_bytes": 2 * MB,
     "text": {"subject": 60, "message": 8000, "footer": 20000,
              "call_to_action": 25},
     "notes": ["The 300x250 banner is desktop only.",
               "Sponsored messaging delivers in the EU only to members who "
               "have opted in \u2014 plan for reduced reach."]},
    {"id": "li_conversation", "channel": "linkedin", "name": "Conversation Ads",
     "kind": "image", "formats": ["jpg", "jpeg", "png"],
     "size": (300, 250), "max_bytes": 2 * MB,
     "text": {"subject": 60, "message": 8000, "footer": 20000,
              "call_to_action": 25}},
    {"id": "li_document", "channel": "linkedin", "name": "Document Ads",
     "kind": "raw", "formats": ["pdf", "doc", "docx", "ppt", "pptx"],
     "max_bytes": 100 * MB,
     "text": {"intro": 150, "headline": 70},
     "notes": ["Under 10 pages recommended; 300 maximum."]},
    # The client supplies no file for these two: one runs an author's own post
    # and the other pulls its image off the LinkedIn Event page. They are
    # modeled so a requirement can name them; there is nothing here to judge a
    # file against, which is the honest answer rather than an invented ceiling.
    {"id": "li_thought_leader", "channel": "linkedin",
     "name": "Thought Leader Ads", "kind": "other", "formats": [],
     "notes": ["Runs an author's post verbatim; no separate creative.",
               "Single image or single video posts only.",
               "The file is whatever the source post carries."]},
    {"id": "li_event", "channel": "linkedin", "name": "Event Ads",
     "kind": "other", "formats": [],
     "text": {"intro": 600, "event_name": 255},
     "notes": ["The 4:1 image is pulled from the LinkedIn Event page "
               "automatically."]},
    {"id": "li_ctv", "channel": "linkedin", "name": "Connected TV Ads",
     "kind": "video", "formats": ["mp4"], "ratios": [(16, 9)],
     "min_width": 1280, "max_bytes": 500 * MB, "duration": (6, 60),
     "notes": ["1920x1080 recommended, 1280x720 minimum.",
               "H.264, 23.98-30 fps CFR, 15-40 Mbps.",
               "Audio -23 LUFS, 192 kbps minimum, 48 kHz."]},
    {"id": "li_click_to_message", "channel": "linkedin",
     "name": "Click to Message Ads",
     "kind": "image", "formats": ["jpg", "jpeg", "png", "gif"],
     "min_width": 401, "max_bytes": 5 * MB,
     "text": {"intro": 600, "message": 8000, "response": 8000,
              "footer": 20000},
     "notes": ["A single image under 401 px wide renders as a thumbnail."]},

    # ---- Snapchat --------------------------------------------------------
    # ---- Snapchat --------------------------------------------------------
    # Transcribed against the 2026 kit. The 2025 model held two formats to the
    # kit's seven, and both of the two refused creative the kit allows -- the
    # third transcription running that way, and the reason this list is worked
    # down rather than waited on:
    #
    #   * video capped at :30 against a published :03 to 3:00. The kit's own
    #     update note says "the 30-second cap is gone", so a :45 spot was
    #     refused outright.
    #   * both pinned to a fixed 1080x1920, when the kit publishes that as
    #     what to *build at* and names 720x1280 as the minimum -- so a legal
    #     720x1280 file failed on dimensions.
    #
    # That second one is the gpt_ads rule: a required 9:16 is a `ratios` entry
    # and a fail, a recommended 1080x1920 is `min_size` and a warn (it runs, it
    # just runs soft), and 720x1280 is the floor and fails. Collapsing the
    # three into one number is what refused the file.
    #
    # Both ids are kept through their renames -- the kit pluralises the two
    # names -- the rule `billboard` gives: tags_for() has written `unit_<id>`
    # onto delivered creative.
    {"id": "snap_image", "channel": "snapchat", "name": "Single Image Ads",
     "kind": "image", "formats": ["jpg", "jpeg", "png"],
     "ratios": [(9, 16)], "min_size": (1080, 1920), "min_width": 720,
     "max_bytes": 5 * MB, "text": {"brand_name": 25, "headline": 34},
     "notes": ["Build at 1080x1920; 720x1280 is the stated minimum, not the "
               "target.",
               "The help center specifies a 25-character brand name and the "
               "Marketing API allows 32 — 25 is what a creative brief "
               "should carry."]},
    {"id": "snap_video", "channel": "snapchat", "name": "Video Ads",
     "kind": "video", "formats": ["mp4", "mov"],
     "ratios": [(9, 16)], "min_size": (1080, 1920), "min_width": 720,
     "max_bytes": 1 * GB, "duration": (3, 180),
     "text": {"brand_name": 25, "headline": 34},
     "notes": ["Build at 1080x1920; 720x1280 is the stated minimum.",
               "The :30 cap is gone — :03 to 3:00 across every video "
               "format."]},
    {"id": "snap_sponsored", "channel": "snapchat", "name": "Sponsored Snaps",
     "kind": "video", "formats": ["mp4", "mov", "jpg", "jpeg", "png"],
     "ratios": [(9, 16)], "min_size": (1080, 1920), "min_width": 720,
     "max_bytes": 1 * GB, "duration": (3, 180),
     "text": {"brand_name": 25, "headline": 34, "chat_message": 500,
              "auto_response": 500},
     "notes": ["Under :10 is recommended.",
               "25 to 28 characters of headline is what the kit recommends, "
               "against a 34 maximum.",
               "A still is accepted here as well as a video.",
               "The branded chat background is optional and is its own "
               "1080x1920 file."]},
    {"id": "snap_story", "channel": "snapchat", "name": "Story Ads",
     "kind": "video", "formats": ["mp4", "mov"],
     "ratios": [(9, 16)], "min_size": (1080, 1920), "min_width": 720,
     "max_bytes": 1 * GB, "duration": (3, 180),
     "text": {"brand_name": 25, "headline": 34}},
    {"id": "snap_collection", "channel": "snapchat", "name": "Collection Ads",
     "kind": "video", "formats": ["mp4", "mov"],
     "ratios": [(9, 16)], "min_size": (1080, 1920), "min_width": 720,
     "max_bytes": 1 * GB, "duration": (3, 180),
     "text": {"brand_name": 25, "headline": 34},
     "notes": ["The product tiles are their own files, beside the "
               "1080x1920 hero."]},
    {"id": "snap_commercial", "channel": "snapchat", "name": "Commercials",
     "kind": "video", "formats": ["mp4", "mov"],
     "ratios": [(9, 16)], "min_size": (1080, 1920), "min_width": 720,
     "max_bytes": 1 * GB, "duration": (3, 180),
     "text": {"brand_name": 25, "headline": 34},
     "notes": ["H.264.",
               "The first :06 is non-skippable — the argument has to be "
               "made in it."]},
    # One row on the kit, two shapes: a static PNG and a moving GIF, at
    # different sizes. It stays one unit because "AR Filters" is the format
    # Snapchat sells; splitting it would invent two names the kit does not
    # publish, which is the drift kit_name_drift() exists to catch. No file
    # weight is published for it, so none is invented -- the gpt_ads rule.
    {"id": "snap_ar_filter", "channel": "snapchat", "name": "AR Filters",
     "kind": "image", "formats": ["png", "gif"],
     "sizes": [(945, 2048), (720, 1560)], "duration": (1, 3),
     "notes": ["Static is a 945x2048 PNG; moving is a 720x1560 GIF.",
               "A moving filter runs :01 to :03.",
               "The kit publishes no file-weight ceiling for these."]},

    # ---- TikTok ----------------------------------------------------------
    # Transcribed against the 2026 kit. The 2025 model held three formats to
    # the kit's six and not one of the three names is a format TikTok sells --
    # which is how it was found, and is the smaller half of it. Two of the
    # three refused creative the kit allows:
    #
    #   * the in-feed video capped at :60 against a published **10 minutes**,
    #     and taking two file types where the kit takes five;
    #   * the image ad pinned to 1200x628 at 500 KB, when the kit specs
    #     images by ratio now and says in as many words that 1200x628
    #     "survives only as the horizontal carousel option" -- so a 720x1280
    #     vertical, the shape TikTok recommends, was refused outright.
    #
    # `tiktok_video` keeps its id through the rename, the rule `billboard`
    # gives. The other two are in RETIRED_UNITS rather than edited into
    # something else: an in-feed still and a profile image are not formats
    # this kit sells, and quietly re-pointing their ids at the carousel would
    # make a delivered 1200x628 read as one card of a 2-to-35 image set.
    {"id": "tiktok_video", "channel": "tiktok", "name": "Auction In-Feed",
     "kind": "video", "formats": ["mp4", "mov", "mpeg", "3gp", "avi"],
     "ratios": [(9, 16), (16, 9), (1, 1)],
     "min_width": 540, "max_bytes": 500 * MB, "duration": (1, 600),
     "text": {"caption": 100},
     "notes": ["9:16 is recommended, 540x960 minimum; 16:9 at 960x540 and "
               "1:1 at 640x640.",
               "Bitrate 516 kbps or better.",
               "The caption is ~100 characters, 50 for CJK.",
               "Brand Name and Profile Image are obsolete for new auction "
               "campaigns — from January 2026 the display name and avatar "
               "are inherited from the linked TikTok account.",
               "Emojis cannot appear in the account name.",
               "Emojis, {} and # cannot appear in the description.",
               "Punctuation and spaces occupy characters."]},
    {"id": "tiktok_spark", "channel": "tiktok", "name": "Spark Ads",
     "kind": "video", "formats": ["mp4", "mov"], "duration": (1, 600),
     "text": {"caption": 150},
     "notes": ["No aspect ratio, resolution, bitrate or file-size "
               "restriction — the organic post is the creative.",
               "Copy is inherited from that post and is not editable; a "
               "pulled caption runs to 150 characters."]},
    {"id": "tiktok_reservation", "channel": "tiktok",
     "name": "Reservation In-Feed",
     "kind": "video", "formats": ["mp4", "mov"], "ratios": [(9, 16)],
     "max_bytes": 500 * MB, "duration": (5, 60),
     "text": {"caption": 100},
     "notes": ["9:16 recommended, bitrate 2,500 kbps or better.",
               ":09 to :15 is the recommended length."]},
    {"id": "tiktok_carousel", "channel": "tiktok", "name": "Carousel Ads",
     "kind": "image", "formats": ["jpg", "jpeg", "png"],
     "sizes": [(1200, 628), (640, 640), (720, 1280)],
     "target_bytes": 100 * KB,
     "notes": ["2 to 35 images: 1200x628 horizontal, 640x640 square or "
               "720x1280 vertical.",
               "100 KB per image is what the kit suggests, not a ceiling it "
               "publishes.",
               "A music track is required and is a separate file: MP3, at "
               "least :02, up to 10 MB.",
               "One caption and one call to action cover the whole "
               "carousel."]},
    {"id": "tiktok_gab_video", "channel": "tiktok",
     "name": "Global App Bundle — Video",
     "kind": "video", "formats": ["mp4", "mov", "mpeg", "avi"],
     "ratios": [(9, 16), (16, 9), (1, 1)],
     "min_width": 640, "max_bytes": 500 * MB, "duration": (5, 60),
     "text": {"brand_name": (2, 20), "description": (1, 100)},
     "notes": ["9:16 at 720x1280 minimum, 16:9 at 1280x720, 1:1 at 640x640.",
               ":21 to :30 is the recommended length.",
               "Brand name is 2-20 Latin characters, 1-10 Asian.",
               "Global App Bundle is what Pangle is called now, and it "
               "covers the CapCut and Fizzo placements."]},
    {"id": "tiktok_gab_image", "channel": "tiktok",
     "name": "Global App Bundle — Image",
     "kind": "image", "formats": ["jpg", "jpeg", "png"],
     "ratios": [(9, 16), (16, 9), (1, 1)],
     "min_width": 640, "max_bytes": 100 * MB,
     "text": {"brand_name": (2, 20), "description": (1, 100)},
     "notes": ["9:16 at 720x1280 recommended, 16:9 at 1280x720, "
               "1:1 at 640x640."]},

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

# Units a platform has retired. Kept out of UNITS -- so nothing asks a client
# to supply one, and no requirement line names one -- and kept resolvable by
# id, because `tags_for()` has written `unit_<id>` onto creative already
# delivered and a gallery filtering on that tag must still find a unit rather
# than nothing. `hub/audit.LOG_NAMES`' rule: a name already written down is
# not renamed to make a check happy, it is declared.
#
# Each says what replaced it, so a row carrying the tag can be read.
RETIRED_UNITS = [
    {"id": "tiktok_image", "channel": "tiktok", "name": "In-Feed Image Ad",
     "kind": "image", "retired": "The kit specs TikTok images by ratio now, "
                                 "and 1200x628 survives only as the "
                                 "horizontal Carousel Ads option."},
    # The IAB removed device-class ad units, so there is no tablet unit to
    # ask for -- 300x250 and 728x90 serve on tablet as the desktop units the
    # client has already supplied. Out of UNITS so nothing asks twice, still
    # in BY_ID so a row carrying `unit_tablet_rectangle` resolves.
    {"id": "tablet_rectangle", "channel": "tablet_display",
     "name": "Tablet Rectangle", "kind": "image",
     "retired": "Tablet Display is retired as a category — the IAB removed "
                "device-class ad units. Superseded by Medium Rectangle "
                "(300x250), which serves on tablet as the same unit."},
    {"id": "tablet_leaderboard", "channel": "tablet_display",
     "name": "Tablet Leaderboard", "kind": "image",
     "retired": "Tablet Display is retired as a category — the IAB removed "
                "device-class ad units. Superseded by Leaderboard (728x90), "
                "which serves on tablet as the same unit."},
    {"id": "tablet_interstitial", "channel": "tablet_display",
     "name": "Tablet Interstitial", "kind": "image",
     "retired": "Tablet Display is retired as a category — the IAB removed "
                "device-class ad units, and 1024x768 has no replacement. It "
                "was the one tablet size that did not dedupe against a "
                "desktop unit, so it was the extra file every display "
                "requirement asked for."},
    {"id": "tablet_html5", "channel": "tablet_display",
     "name": "HTML5 package", "kind": "package",
     "retired": "Tablet Display is retired as a category. The desktop and "
                "mobile HTML5 packages are the same ask."},
    {"id": "tiktok_profile", "channel": "tiktok", "name": "Profile Image",
     "kind": "image", "retired": "Custom Identity is being retired — from "
                                 "January 2026 the avatar is inherited from "
                                 "the linked TikTok account, so there is no "
                                 "profile image to supply."},
    {"id": "x_direct_message", "channel": "x", "name": "Direct Message Card",
     "kind": "image", "retired": "X retired the format; there is no 2026 "
                                 "equivalent on the kit."},
    {"id": "x_multi_image_desktop", "channel": "x",
     "name": "Multi Image Tweet — Desktop", "kind": "image",
     "retired": "The mobile/desktop creative split is gone — one asset set "
                "serves both. Superseded by Carousel Ads."},
    {"id": "x_single_image_mobile", "channel": "x",
     "name": "Single Image Tweet — Mobile", "kind": "image",
     "retired": "The mobile/desktop creative split is gone. Superseded by "
                "Image Ads."},
    {"id": "x_single_image_desktop", "channel": "x",
     "name": "Single Image Tweet — Desktop", "kind": "image",
     "retired": "The mobile/desktop creative split is gone. Superseded by "
                "Image Ads."},
]

BY_ID = {u["id"]: u for u in UNITS}
BY_ID.update({u["id"]: u for u in RETIRED_UNITS if u["id"] not in BY_ID})

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
# The Meta placements, named once. Two rules below answer with this list --
# a product naming both platforms, and the general Facebook/Meta rule -- and
# two hand-written copies of it is how one of them comes to be missing the
# carousel.
_META_CHANNELS = ["facebook", "instagram", "facebook_video",
                  "facebook_carousel", "stories"]

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
    # A product naming *both* platforms is one buy running across both, and
    # the wider answer is the right one. The `instagram` rule below returns a
    # deliberately narrower list, written for a product named only Instagram
    # -- and every Meta product on this card is called "Facebook | Instagram
    # ...", so five of the seven took the narrow one and were asked for an
    # Instagram image and a Story and never for the Facebook feed, the
    # Facebook video or the carousel. On the video buy that is worse than it
    # sounds: `facebook_video` was dropped from a product whose own name says
    # Video. Nothing errors -- the units returned are real Meta units, just
    # not all of the ones being bought -- and the two products named
    # "Facebook - ..." got the full set the whole time, which is why it read
    # as working.
    (r"(?=.*facebook)(?=.*instagram)", _META_CHANNELS),
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
    (r"facebook|\bmeta\b|social ads?", _META_CHANNELS),
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
    # No tablet: the IAB removed device-class ad units and the kit retired the
    # category. Left here it would name a channel with no unit behind it, and
    # `required_units()` reports that as "the spec kit maps no unit for this"
    # -- a warning about our own dangling entry, printed at the client.
    (r"display|programmatic|retarget|geo-?fenc|location lookback|"
     r"ip target|data targeted", ["desktop_display", "mobile_display"]),
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
# table is Unit / Dimensions / weight are listed: the rest publish prose per
# format, or a table of entirely different columns, and a parser that guessed
# at those would report drift that is not there.
_KIT_SECTIONS = {"desktop-display": "desktop_display",
                 "mobile-display": "mobile_display",
                 "dooh": "dooh"}

# The same three sections, read as a fact about what the buy IS rather than
# about what this parser can read. Where the kit's table is Unit / Dimensions
# / weight, the size *is* the unit and the name adds nothing a client can act
# on: "Leaderboard" is 728x90, and eleven labels beside eleven sizes is a wall
# nobody reads to the bottom of. Where the first column is a Format, the name
# is the ask and the sizes belong to it -- so folding those into one anonymous
# run loses exactly the thing the client is being asked to choose.
#
# It had, on every channel published that way. An X buy asked for nine bare
# sizes with Image Ads, Carousel Ads, Conversation Button and Spotlight
# Takeover all dissolved into them; LinkedIn the same across six; and native
# display printed "1200x628, 200x200" with nothing saying the second one is
# the brand logo. That is `creative_needs._shape_of()`'s note about a unit
# reaching the line as a bare name, running the other way: here the shape is
# all that arrives and the *name* is what went missing.
#
# It was `| {"tablet_display"}` for one release, described here as "ours
# rather than the kit's, because the kit publishes no tablet section". The
# 2026 kit publishes one sentence about it and that sentence retires the whole
# category, so there is no tablet channel left to fold.
SIZE_SET_CHANNELS = frozenset(_KIT_SECTIONS.values())

# ...and that sentence covered three sections of twenty-three while the check
# answered "no drift", which is a clean bill of health about seven per cent of
# the thing it is auditing. The page in the repo is now the **2026** kit and
# says on itself "20 formats updated, 3 added", against a transcription taken
# from 2025 -- so a section outside the parser is not a hypothetical gap. What
# makes it dangerous is that the gap is invisible: a section the next rebuild
# adds is silently outside the check for ever, exactly as the twenty already
# are, with the panel green.
#
# So every published section is declared, and `kit_coverage()` reports one
# that is not. Same shape as `compliance_spec.NOT_ENFORCED` and
# `ghl_scopes.NOT_REQUESTED`: a thing left out on purpose is named with its
# reason, so its absence is never ambiguous between an oversight and a
# decision. It starts empty, which is the only way it was worth adding.
_SHAPE = ("published as prose per format rather than a Unit / Dimensions / "
          "weight table")
_COLUMNS = ("published as a table of Format / Copy / Media / File Size, not "
            "the Unit / Dimensions / weight shape this parser reads")

_KIT_UNREAD = {
    "native-display": _COLUMNS,
    "standard-video": _SHAPE,
    "youtube-video": _COLUMNS,
    "ctv-ott": _SHAPE,
    "native-video": _SHAPE,
    "digital-radio": _SHAPE,
    "standard-email": _SHAPE,
    "facebook-display": _SHAPE,
    "instagram-display": _SHAPE,
    "facebook-video": _SHAPE,
    "facebook-carousel": _SHAPE,
    "stories-display": _SHAPE,
    "stories-video": _SHAPE,
    "x-twitter": _COLUMNS,
    "linkedin": _COLUMNS,
    "snapchat": _COLUMNS,
    "tiktok": _COLUMNS,
}

# And three of the twenty are a different kind of gap. These are formats the
# kit sells and this module has no unit for at all -- not "we cannot parse the
# table", but "there is nothing here to judge one against". `when` names the
# channels whose presence in a requirement means the format is in play, so
# `required_units()` can say the kit publishes something it cannot measure
# rather than answering confidently with the units it does have. That is the
# rule this module works to everywhere else, and the reason it matters here is
# the page's own sentence: "Facebook Reels and Instagram Reels are not
# interchangeable -- different file types, text limits and duration rules."
# A Meta requirement that lists Stories and never Reels is the Pinterest
# failure again, one placement along.
_KIT_NOT_MODELLED = (
    {"id": "instagram-reels", "name": "Instagram Reels",
     "when": ("instagram", "stories", "facebook", "facebook_video",
              "facebook_carousel")},
    {"id": "facebook-reels", "name": "Facebook Reels",
     "when": ("instagram", "stories", "facebook", "facebook_video",
              "facebook_carousel")},
    {"id": "ctv-new-formats", "name": "CTV interactive formats "
                                      "(pause, menu, screensaver, in-scene, "
                                      "squeezeback, overlay)",
     "when": ("ctv",)},
)


# Sections whose table's first column is a format name, for channels this
# module has been transcribed against the **2026** kit. `kit_drift()` cannot
# read these tables -- their columns are Format / Copy / Media / File Size,
# not Unit / Dimensions / weight -- but the first column is a name, and a name
# is what a client is asked to supply. X is here because its 2025 model named
# eight formats and not one of them was a format X still sells.
#
# A platform joins this list when its units are transcribed, not before: a
# check listing every platform on the day it is written is red on the day it
# is written, and gets switched off. What is still on 2025 is named below
# rather than left as an absence.
def _plain(fragment: str) -> str:
    """One table cell as a person reads it: no markup, no entities."""
    return _html.unescape(
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


_KIT_NAME_CHECKED = {"x-twitter": "x", "linkedin": "linkedin",
                     "tiktok": "tiktok", "snapchat": "snapchat",
                     "youtube-video": "youtube"}

# Transcribed against the 2025 kit and not yet re-checked against 2026, with
# what is known to have moved. Not findings -- a backlog somebody works down,
# the way `help_audit.demo_targets()` lists its 55 steps rather than failing
# the build on them. Each is a client being asked for a format under a name
# its platform has changed or dropped.
_KIT_NAMES_PENDING: dict[str, str] = {}

# Transcribed against 2026, and still outside the name pass, with the reason.
# That is a different state from either of the two above and needs saying:
# left in _KIT_NAMES_PENDING it would claim a 2025 transcription that is no
# longer there, and added to _KIT_NAME_CHECKED it would report a finding that
# is not one.
#
# Native display's first column is an *asset* rather than a format -- half its
# rows are character limits, which this module carries on the main image
# rather than as units of their own -- and the section publishes HTML5
# packaging under its own heading, outside the table the parser reads. So the
# name pass would report our HTML5 package unit as a format the kit does not
# sell, which is the crying wolf `UNCHECKED` exists to avoid.
_KIT_NAMES_UNCHECKABLE = {
    "native_display": "the section's table is an asset list, not a format "
                      "list — four of its eight rows are character limits "
                      "carried on the main image, and the HTML5 package the "
                      "section also publishes sits outside that table, so "
                      "the name pass would report it as drift",
}


def _kit_section_names(section_id: str) -> tuple[list[str], str]:
    """The format names in a section's first column, as published."""
    try:
        html = _KIT_PAGE.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], f"the published kit could not be read ({exc})"
    m = re.search(rf'<section class="section" id="{section_id}"(.*?)</section>',
                  html, re.S)
    if not m:
        return [], f"the published kit has no {section_id} section"
    table = re.search(r"<table>(.*?)</table>", m.group(1), re.S)
    if not table:
        return [], f"the {section_id} section publishes no table"
    out = []
    for row in re.findall(r"<tr>(.*?)</tr>", table.group(1), re.S)[1:]:
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)
        if cells:
            out.append(_plain(cells[0]))
    return [n for n in out if n], ""


def _name_key(name: str) -> str:
    """Loose enough for a plural and a dash, strict enough to mean something."""
    n = (name or "").lower().replace("\u2014", "-").replace("\u2013", "-")
    n = re.sub(r"[^a-z0-9]+", " ", n).strip()
    return n[:-1] if n.endswith("s") else n


def kit_name_drift() -> list[dict]:
    """Unit names we ask a client for that the published kit no longer sells.

    Only for the channels declared in `_KIT_NAME_CHECKED`. A name is what the
    requirement line prints, so a unit named after a format the platform has
    retired asks a client to supply something that does not exist -- which is
    silent from both ends: the name is a real format's name, it was right once,
    and nothing errors.
    """
    out = []
    for section_id, channel in _KIT_NAME_CHECKED.items():
        published, error = _kit_section_names(section_id)
        if error:
            # A section that cannot be read is not a section with nothing
            # wrong in it.
            out.append({"unit": "", "detail": f"Not measured — {error}."})
            continue
        known = {_name_key(n) for n in published}
        for unit in UNITS:
            if unit.get("channel") != channel:
                continue
            if _name_key(unit.get("name", "")) not in known:
                out.append({
                    "unit": unit["id"],
                    "detail": f"\"{unit['name']}\" is not a format the "
                              f"published kit sells. A client is asked for it "
                              f"by name on the requirement line."})
    return out


def _kit_section_ids() -> tuple[list[str], str]:
    """Every section id the published page carries, in page order."""
    try:
        html = _KIT_PAGE.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], f"the published kit could not be read ({exc})"
    ids = re.findall(r'<section class="section" id="([^"]+)"', html)
    if not ids:
        return [], "no sections could be read out of the published kit"
    return ids, ""


def kit_coverage() -> dict:
    """Which sections of the published kit this transcription accounts for.

    `kit_drift()` compares numbers; this asks the question one step earlier --
    is every section of the page one somebody has looked at. A section nobody
    declared is the finding, because that is what a rebuild adds and what no
    other check here can see.

    A page that cannot be read is **not measured**, never a clean answer: that
    is the one state where "nothing undeclared" would be a lie.
    """
    ids, error = _kit_section_ids()
    if error:
        return {"measured": False, "error": error, "sections": 0,
                "checked": [], "unread": [], "not_modelled": [],
                "undeclared": [], "stale": [],
                "names_checked": [], "names_pending": {},
                "names_unchecked": {}}
    modelled = {e["id"] for e in _KIT_NOT_MODELLED}
    declared = set(_KIT_SECTIONS) | set(_KIT_UNREAD) | modelled
    return {
        "measured": True,
        "error": "",
        "sections": len(ids),
        "checked": [i for i in ids if i in _KIT_SECTIONS],
        "unread": [i for i in ids if i in _KIT_UNREAD],
        "not_modelled": [i for i in ids if i in modelled],
        # A section on the page that nobody has declared. This is the finding.
        "undeclared": [i for i in ids if i not in declared],
        # ...and the other direction, the rule check_stale_json_exemptions()
        # works to: a declaration that outlives the section it described goes
        # on excusing whatever is published under that id next.
        "stale": sorted(d for d in declared if d not in set(ids)),
        # Which channels' *names* are held to the 2026 page, and which are
        # still on the 2025 transcription. A backlog, named rather than left
        # as an absence.
        "names_checked": sorted(_KIT_NAME_CHECKED.values()),
        "names_pending": dict(sorted(_KIT_NAMES_PENDING.items())),
        # Transcribed, and outside the name pass for a stated reason. Neither
        # a backlog nor a clean bill: a third state, because it is the one a
        # reader would otherwise infer wrongly from the absence of the other
        # two.
        "names_unchecked": dict(sorted(_KIT_NAMES_UNCHECKABLE.items())),
    }


def unmodelled_for(channels) -> list[str]:
    """Published formats in play for these channels that we cannot measure."""
    have = set(channels or ())
    return [e["name"] for e in _KIT_NOT_MODELLED
            if have & set(e["when"])]


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
