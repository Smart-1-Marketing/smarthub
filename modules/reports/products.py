"""The Smart 1 product catalog: the one dimension a client is shown.

A client never sees which platform ran their campaign. What they read on
the live page, in ``data.json`` and on the PDF is the **Smart 1 product** --
*Streaming TV*, *Streaming Audio*, *Paid Search* -- and two platforms that
carry one product (a Trade Desk buy and a StackAdapt buy both sold as
Streaming TV) are one bar, one row, one figure. ``CampaignMap.product`` is
that dimension; this module is its vocabulary.

``PRODUCTS`` is the ordered list a picker offers, one entry per media
product Smart 1 sells, in the words a client is sold it under. It is held
against the Hub's own rate card rather than restating it: every media
category ``hub/rate_card.py`` publishes maps to one of these names in
``RATE_CARD_CATEGORIES``, and ``unmapped_categories()`` names any category
the card gains that this map does not know -- so a product added to the
card cannot silently go un-offered here. The names are deliberately NOT the
card's own category headings ("DATA TARGETED DISPLAY", "OTT"), which are
the wholesale card's vocabulary rather than a client's; the client-facing
name is what the IO and the proposal sell the product as.

``DEFAULT_PRODUCT_FOR_PLATFORM`` is what the auto-mapper files a campaign
under when its name carries no product segment, and what the client page
falls back to when a mapping's product is blank -- with the blank flagged
on the staff page, because a generic label is a mapping nobody finished.

``VENDOR_WORDS`` is the list of platform and vendor names a campaign's
display name is stripped of by default, and ``FORBIDDEN`` is the hard rule
``test_reports_crossover.py`` sweeps the public HTML, data.json and PDF
bytes for. ``ALLOWED`` documents the phrases that pass on purpose:
"Google search" (the organic section names the search engine, which is not
a vendor Smart 1 buys from) and "Facebook & Instagram" (a product label --
the networks the client's ads run on, not the ad platform behind them).
"""
from __future__ import annotations

import re

# The client-facing products, in picker order. Nothing here names a
# vendor: a client reads the product they were sold, never the exchange.
PRODUCTS: tuple[str, ...] = (
    "Streaming TV",
    "Streaming Audio",
    "Online Video",
    "Programmatic Display",
    "Native",
    "Geofencing",
    "IP Targeting",
    "Retargeting",
    "Paid Search",
    "Paid Social",
    "Facebook & Instagram",
    "LinkedIn",
    "Short-Form Video",
    "Amazon Ads",
    "Email Marketing",
    "Digital Signage",
    "Local SEO",
    "Social Media Management",
    "Website",
    "Creative",
    "Smart 1 Suite",
)

# Rate-card category -> product. Every media category on hub/rate_card.py
# is here; unmapped_categories() reports one that is not.
RATE_CARD_CATEGORIES: dict[str, str] = {
    "OTT": "Streaming TV",
    "DIGITAL RADIO": "Streaming Audio",
    "YOUTUBE": "Online Video",
    "DISPLAY": "Programmatic Display",
    "DATA TARGETED DISPLAY": "Programmatic Display",
    "MOBILE ONLY": "Geofencing",
    "LOCATION LOOKBACK": "Geofencing",
    "IP TARGETS": "IP Targeting",
    "RETARGETING": "Retargeting",
    "SEARCH ENGINE MARKETING / PAY PER CLICK": "Paid Search",
    "META": "Facebook & Instagram",
    "SOCIAL ADS - VIDEO": "Paid Social",
    "SOCIAL ADS": "Paid Social",
    "EMAIL MARKETING": "Email Marketing",
    "SMART 1 SIGNAGE": "Digital Signage",
    "SEARCH ENGINE OPTIMIZATION": "Local SEO",
    "SOCIAL MEDIA MANAGEMENT": "Social Media Management",
    "WEB DEVELOPMENT": "Website",
    "CREATIVE / DESIGN SERVICES": "Creative",
    # Add-ons are lines on a media product's order, not a product a client
    # reads a report for; they map to nothing on purpose.
    "ADD-ON PRODUCT": "",
}

# What a campaign is filed under when nobody said: the product the
# platform is sold as on this book. Every platform in store.PLATFORMS.
DEFAULT_PRODUCT_FOR_PLATFORM: dict[str, str] = {
    "ttd": "Streaming TV",
    "stackadapt": "Programmatic Display",
    "audiogo": "Streaming Audio",
    "groundtruth": "Geofencing",
    "google": "Paid Search",
    "bing": "Paid Search",
    "meta": "Facebook & Instagram",
    "linkedin": "LinkedIn",
    "tiktok": "Short-Form Video",
    "x": "Paid Social",
    "amazon_sa": "Amazon Ads",
    "amazon_dsp": "Streaming TV",
    "suite": "Smart 1 Suite",
}

# What a Google Ads campaign is filed under from the channel type Google
# itself reports on it. The platform default above is Paid Search, which
# is right for most of the account and wrong for every YouTube buy: the
# rate card sells YouTube (TrueView, bumpers) as Online Video, those are
# Google Ads VIDEO campaigns, and filed under the platform default they
# read as search on the client's own page. Only the channel types that
# map to one product cleanly are named; the rest (Performance Max, Demand
# Gen, Shopping, Smart, Local) take the platform default and the mapping
# row says a person has not chosen, because a guess filed as a product is
# a bar on the client's page that no budget line can pace.
GOOGLE_CHANNEL_PRODUCTS: dict[str, str] = {
    "VIDEO": "Online Video",
    "SEARCH": "Paid Search",
    "DISPLAY": "Programmatic Display",
}

# The same rule for Microsoft Advertising's ``CampaignType``, which the
# native pull carries on the row as ``channel_type``: Search is the
# platform's own product and Audience (the Microsoft Audience Network --
# native and display placements) is a display buy. Shopping,
# DynamicSearchAds, Hotel and PerformanceMax take the platform default and
# the mapping row says nobody chose, for the reason above.
BING_CHANNEL_PRODUCTS: dict[str, str] = {
    "SEARCH": "Paid Search",
    "AUDIENCE": "Programmatic Display",
}

# One reading of which platforms report a channel type this table maps.
CHANNEL_PRODUCTS: dict[str, dict[str, str]] = {
    "google": GOOGLE_CHANNEL_PRODUCTS,
    "bing": BING_CHANNEL_PRODUCTS,
}

# Vendor and platform names a campaign name is stripped of for its default
# display name. Longer phrases first so "The Trade Desk" goes before
# "Trade Desk". Matched case-insensitively on word boundaries.
VENDOR_WORDS: tuple[str, ...] = (
    "The Trade Desk", "Trade Desk", "TheTradeDesk", "TTD",
    "StackAdapt", "Stack Adapt",
    "AudioGo", "Audacy",
    "Google Ads", "GoogleAds", "AdWords",
    "Microsoft Ads", "Bing Ads", "Bing",
    "Meta Ads", "Facebook Ads",
    "TikTok", "Tik Tok",
    "LinkedIn Ads",
    "Amazon DSP",
    "GroundTruth", "Ground Truth",
    "Windsor",
    "DSP",
)

# The hard rule: none of these may appear in the public HTML, data.json or
# PDF bytes, case-insensitively. "ttd" and "bing" are matched on word
# boundaries (they are substrings of ordinary words); the rest as phrases.
FORBIDDEN: tuple[str, ...] = (
    "trade desk", "thetradedesk", "ttd", "stackadapt", "stack adapt", "audiogo",
    "audacy", "google ads", "microsoft ads", "bing", "meta ads", "facebook ads",
    "tiktok", "linkedin ads", "amazon dsp", "groundtruth", "windsor", "platform",
)
_WORD_BOUNDED = {"ttd", "bing"}

# Phrases that pass on purpose, with the reason. They are not exemptions
# from FORBIDDEN -- none of them contains a forbidden phrase -- they are
# written down so nobody widens FORBIDDEN to catch them by mistake.
ALLOWED: dict[str, str] = {
    "Google search": "the organic section names the search engine, not an ad vendor",
    "Google Analytics": "named only when the link opts in to naming products",
    "Facebook & Instagram": "a product label: the networks the ads run on",
    "LinkedIn": "a product label, not 'LinkedIn Ads'",
    "Amazon Ads": "a product label; 'Amazon DSP' is the vendor spelling",
    "YouTube": "the channel section names the client's own channel, not an ad vendor",
}


def forbidden_pattern() -> re.Pattern:
    parts = []
    for w in FORBIDDEN:
        esc = re.escape(w)
        parts.append(rf"\b{esc}\b" if w in _WORD_BOUNDED else esc)
    return re.compile("|".join(parts), re.IGNORECASE)


def forbidden_hits(text: str | bytes) -> list[str]:
    """Every forbidden phrase found in ``text``, deduplicated, lowercased."""
    if isinstance(text, bytes):
        text = text.decode("latin-1")
    return sorted({m.lower() for m in forbidden_pattern().findall(text or "")})


_VENDOR_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(w) for w in VENDOR_WORDS) + r")(?![A-Za-z0-9])",
    re.IGNORECASE)


def strip_vendor_words(name: str) -> str:
    """A campaign name with the vendor words removed and the punctuation
    they leave behind tidied: ``"Fall Wine - TTD CTV"`` -> ``"Fall Wine -
    CTV"``; ``"StackAdapt Display"`` -> ``"Display"``. A name that was
    nothing but vendor words comes back empty, and the caller falls back
    to the product."""
    s = _VENDOR_RE.sub(" ", str(name or ""))
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*([|/\-:–—])\s+(?=[|/\-:–—])", " ", s)      # "- -" left by a removed word
    s = re.sub(r"^\s*[|/\-:–—]+\s*|\s*[|/\-:–—]+\s*$", "", s)  # leading/trailing
    s = re.sub(r"\(\s*\)|\[\s*\]", "", s)
    return re.sub(r"\s+", " ", s).strip()


_S1M_RE = re.compile(r"^\s*s1m\s*\|", re.IGNORECASE)


def default_display_name(campaign_name: str, product: str | None = None) -> str:
    """What a campaign row is called on the client's page unless staff
    override it: the platform's name with the vendor words stripped, or the
    product when nothing is left. A name in the auto-mapper's own shape
    (``S1M | client | product | anything``) shows its trailing part -- the
    client key and the S1M mark are ours, not something a client reads."""
    name = str(campaign_name or "")
    if _S1M_RE.match(name):
        parts = [p.strip() for p in name.split("|")]
        name = " | ".join(p for p in parts[3:] if p) if len(parts) > 3 else ""
    cleaned = strip_vendor_words(name)
    return cleaned or (product or "") or "Campaign"


def normalize(product: str | None) -> str:
    """A typed product, matched case-insensitively against the catalog so
    "streaming tv" files as "Streaming TV"; free text is kept as typed."""
    p = str(product or "").strip()
    if not p:
        return ""
    low = p.lower()
    for known in PRODUCTS:
        if known.lower() == low:
            return known
    return p[:120]


def default_for(platform: str, channel: str = "") -> str:
    """The product a campaign is filed under when its name carries none:
    the channel type's, where the platform reports one this table maps,
    else the platform's. ``channel_decided()`` says which answered."""
    plat = str(platform or "").lower()
    hit = CHANNEL_PRODUCTS.get(plat, {}).get(str(channel or "").upper())
    if hit:
        return hit
    return DEFAULT_PRODUCT_FOR_PLATFORM.get(plat, "")


def channel_decided(platform: str, channel: str = "") -> bool:
    """Whether ``default_for(platform, channel)`` answered from the channel
    type rather than the platform default -- what the mapping row records,
    so a filed-from-the-channel product can be told from a guess."""
    return str(channel or "").upper() in CHANNEL_PRODUCTS.get(str(platform or "").lower(), {})


def rate_card_products() -> dict:
    """``{"available": bool, "categories": [...], "mapped": {category:
    product}, "unmapped": [...]}`` -- the rate card read live where it is
    importable, so a category the card gains is named rather than missed."""
    try:
        from hub import rate_card
        cats = list(rate_card.categories())
    except Exception as exc:                            # noqa: BLE001
        return {"available": False, "categories": [], "mapped": {}, "unmapped": [],
                "error": f"{type(exc).__name__}"}
    mapped = {c: RATE_CARD_CATEGORIES[c] for c in cats if c in RATE_CARD_CATEGORIES}
    return {"available": True, "categories": cats, "mapped": mapped,
            "unmapped": [c for c in cats if c not in RATE_CARD_CATEGORIES], "error": ""}


def unmapped_categories() -> list[str]:
    return rate_card_products()["unmapped"]


def catalog() -> list[str]:
    """What the picker offers: PRODUCTS, plus any product the rate card maps
    to that PRODUCTS does not list (which should be none -- the test holds
    it -- but a name is never dropped on the way to a picker)."""
    out = list(PRODUCTS)
    for name in rate_card_products()["mapped"].values():
        if name and name not in out:
            out.append(name)
    return out
