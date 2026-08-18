"""What the Landing Ads tool knows before it reads anything.

**Pages.** The Smart 1 suite landing-page apps, the ones we build and run
ourselves. Live URLs are deliberately *not* hardcoded — each one is set once
in the tool's Pages panel and stored on disk, because the Render URLs move and
a wrong URL here would silently produce ads for the wrong page. Anything not
on this list can still be run by pasting a URL.

**Formats and sizes** are the platform specs the copy is validated against.
Every character limit below is the platform's own; copy that busts one is
flagged rather than silently truncated, because a truncated headline is how a
CTA disappears mid-word in the live ad.
"""
from __future__ import annotations

SUITE_PAGES = [
    {"slug": "smart1boat", "name": "Smart 1 Boat", "industry": "boat dealers",
     "audience": "boat dealers and marine dealerships buying local media"},
    {"slug": "smart1rv", "name": "Smart 1 RV", "industry": "RV dealers",
     "audience": "RV dealerships with a defined sales radius"},
    {"slug": "smart1ski", "name": "Smart 1 Ski", "industry": "ski resorts",
     "audience": "ski resorts and mountain destinations"},
    {"slug": "smarthvac", "name": "Smart 1 HVAC", "industry": "HVAC contractors",
     "audience": "residential HVAC contractors and home-services owners"},
    {"slug": "smart1legal", "name": "Smart 1 Legal", "industry": "law firms",
     "audience": "personal injury and general-practice attorneys"},
    {"slug": "smart1restaurant", "name": "Smart 1 Restaurant", "industry": "restaurants",
     "audience": "independent restaurants and small groups"},
    {"slug": "smart1recruit", "name": "Smart 1 Recruit", "industry": "hiring / recruitment",
     "audience": "employers with open roles to fill"},
    {"slug": "smart-tourism", "name": "Smart 1 Tourism", "industry": "tourism & attractions",
     "audience": "attractions, festivals and destination marketers"},
    {"slug": "stadium-to-screen", "name": "Stadium to Screen", "industry": "sports audience media",
     "audience": "businesses that want to reach a team's fan base"},
    {"slug": "smart1-marketing-audit", "name": "Smart 1 Marketing Audit", "industry": "marketing audit",
     "audience": "CPA and bookkeeping partners referring their clients"},
    {"slug": "smart1-proposal-builder", "name": "Smart 1 Proposal Builder", "industry": "proposals",
     "audience": "prospects being sent a tailored media proposal"},
]


def page_by_slug(slug: str) -> dict | None:
    for page in SUITE_PAGES:
        if page["slug"] == slug:
            return page
    return None


FORMATS = [
    {"id": "google_rsa", "label": "Google search (RSA)",
     "blurb": "15 headlines, 4 descriptions, callouts and sitelinks."},
    {"id": "paid_social", "label": "Paid social (Meta / TikTok)",
     "blurb": "Primary text, headline and hook per platform, three angles each."},
    {"id": "display", "label": "Display banners",
     "blurb": "Headline, subhead and CTA per IAB size, with optional background art."},
    {"id": "streaming_audio", "label": "Streaming audio",
     "blurb": ":15 and :30 scripts written to the clock, ready for Radio Promo."},
]

# Google's own limits.
RSA_LIMITS = {"headline": 30, "description": 90, "callout": 25, "sitelink": 25,
              "sitelink_description": 35, "path": 15}

# Meta and TikTok truncate rather than reject, which is worse.
SOCIAL_LIMITS = {"meta": {"primary": 125, "headline": 40, "description": 30},
                 "tiktok": {"primary": 100, "headline": 40, "description": 30}}

# The sizes Image Creator already offers, so a concept can be built there
# without resizing anything.
BANNER_SIZES = [
    {"w": 300, "h": 250, "name": "Medium rectangle"},
    {"w": 728, "h": 90, "name": "Leaderboard"},
    {"w": 320, "h": 50, "name": "Mobile banner"},
    {"w": 300, "h": 600, "name": "Half page"},
    {"w": 970, "h": 250, "name": "Billboard"},
    {"w": 1200, "h": 630, "name": "Social / link preview"},
    {"w": 1080, "h": 1080, "name": "Square"},
    {"w": 1080, "h": 1920, "name": "Story / vertical"},
]

OBJECTIVES = ["Lead generation", "Free instant report", "Book a call",
              "Phone calls", "Demo request", "Brand awareness"]
