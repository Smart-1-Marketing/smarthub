"""What a Performance Max asset group has to carry, as data.

Google refuses an asset group that does not meet its minimums, and it refuses
the **whole** bulk mutate — so an asset short by one headline costs the
campaign rather than the headline, and the error arrives at the moment
somebody is waiting on a deploy. These are the numbers that decide it, in one
place, read by the generator, the validator and the deploy builder alike, the
way `hub/creative_specs.py` is read by the proposal, the IO's upload manager
and the client galleries.

**Transcribed, not fetched**, for the reason `hub/creative_specs.py` gives: a
spec table pulled live changes what a check says with no diff to point at.
Every entry carries its `source`, and `SOURCES` names the page each came from
so the next person to re-confirm one knows where to look.

**And the transcription's own limit is written down rather than implied.**
`developers.google.com` is not reachable from the environment this was
written in — the egress proxy refuses it — so these were taken from Google's
own documentation at one remove and must be re-confirmed against the live
pages before anybody treats a refusal as a bug in this file.
`kit_note()` says so on any screen that prints them, the way
`hub/creative_specs.kit_coverage()` reports what it could not read rather
than answering that there is nothing to report.

Three things the shape has to get right.

**A minimum is a refusal and a recommendation is not.** Three headlines is
what Google will accept; eleven is what lifts ad strength. Enforcing the
second would refuse a perfectly deployable asset group, which is the check
somebody switches off — the `QR_CODE_RULES` lesson. `validate()` fails on the
first and advises on the second.

**One description must be short.** Google takes descriptions up to 90
characters and requires that at least one is 60 or under, which is the rule
most easily missed because every description is individually valid. It is its
own check with its own message.

**An image ratio nobody supplied is named.** A landscape image and a square
image are both required and a portrait one is not, so "you are missing the
square" and "you have no images at all" are different sentences and only one
of them is one field to fix.
"""
from __future__ import annotations

SOURCES = {
    "api": ("Google Ads API — Performance Max asset requirements, "
            "https://developers.google.com/google-ads/api/performance-max/"
            "asset-requirements"),
    "assets": ("Google Ads API — Assets in a Performance Max campaign, "
               "https://developers.google.com/google-ads/api/performance-max/"
               "assets"),
    "help": ("Google Ads Help — Performance Max campaign specs and format "
             "requirements, https://support.google.com/google-ads/answer/17091269"),
}

TRANSCRIBED_NOTE = (
    "These minimums are transcribed from Google's own documentation rather "
    "than fetched, so a change at Google's end will not move them until "
    "somebody edits this file. They were taken at one remove — "
    "developers.google.com is not reachable from the environment they were "
    "written in — so re-confirm them against the live pages before treating a "
    "refusal from Google as a fault here."
)

# --- text ------------------------------------------------------------------
# `minimum` is what Google will accept. `recommended` is what lifts ad
# strength, and is advice: enforcing it would refuse a deployable asset group.
TEXT_ASSETS = {
    "headlines": {
        "label": "Headlines", "field_type": "HEADLINE",
        "minimum": 3, "maximum": 15, "recommended": 11, "max_chars": 30,
        "source": "api",
    },
    "longHeadlines": {
        "label": "Long headlines", "field_type": "LONG_HEADLINE",
        "minimum": 1, "maximum": 5, "recommended": 2, "max_chars": 90,
        "source": "api",
    },
    "descriptions": {
        "label": "Descriptions", "field_type": "DESCRIPTION",
        "minimum": 2, "maximum": 5, "recommended": 4, "max_chars": 90,
        # At least one description must be this short. Every description can be
        # individually valid and the asset group still refused on this alone.
        "short_max_chars": 60, "short_minimum": 1,
        "source": "api",
    },
    "businessName": {
        "label": "Business name", "field_type": "BUSINESS_NAME",
        "minimum": 1, "maximum": 1, "recommended": 1, "max_chars": 25,
        "source": "help",
    },
}

# --- images ----------------------------------------------------------------
# Ratio, the floor Google refuses under, and the size worth building to.
IMAGE_ASSETS = {
    "marketing": {
        "label": "Landscape image", "field_type": "MARKETING_IMAGE",
        "ratio": "1.91:1", "min_size": (600, 314), "recommended_size": (1200, 628),
        "minimum": 1, "source": "help",
    },
    "square": {
        "label": "Square image", "field_type": "SQUARE_MARKETING_IMAGE",
        "ratio": "1:1", "min_size": (300, 300), "recommended_size": (1200, 1200),
        "minimum": 1, "source": "help",
    },
    "portrait": {
        "label": "Portrait image", "field_type": "PORTRAIT_MARKETING_IMAGE",
        "ratio": "4:5", "min_size": (480, 600), "recommended_size": (960, 1200),
        # Not required, and said so rather than left out: an absent entry reads
        # as an oversight, and this one is a decision of Google's.
        "minimum": 0, "source": "help",
    },
}

LOGO_ASSETS = {
    "logo": {
        "label": "Logo", "field_type": "LOGO",
        "ratio": "1:1", "min_size": (128, 128), "recommended_size": (1200, 1200),
        "minimum": 1, "source": "help",
    },
    "landscapeLogo": {
        "label": "Landscape logo", "field_type": "LANDSCAPE_LOGO",
        "ratio": "4:1", "min_size": (512, 128), "recommended_size": (1200, 300),
        "minimum": 0, "source": "help",
    },
}

# Shared ceilings across the ratios above, rather than per entry: Google counts
# images as one pool and logos as another.
MAX_IMAGES_TOTAL = 20
MAX_LOGOS_TOTAL = 5
MAX_VIDEOS = 5
IMAGE_MAX_BYTES = 5 * 1024 * 1024
IMAGE_FORMATS = ("PNG", "JPEG")

# Not a required asset through the API: Google generates one from the other
# assets where none is supplied. Carried here so a screen can say that rather
# than leaving a blank a reader takes for an omission.
VIDEO_NOTE = ("A YouTube video is not required through the API — Google "
              "generates one from the other assets where none is supplied. A "
              "video you supply almost always outperforms a generated one.")


def kit_note() -> str:
    return TRANSCRIBED_NOTE


def source_of(entry: dict) -> str:
    return SOURCES.get(entry.get("source") or "", "")


def requirements() -> list[dict]:
    """Every requirement in one list, for a screen that has to print them."""
    rows = []
    for key, entry in TEXT_ASSETS.items():
        rows.append({"key": key, "kind": "text", **entry,
                     "source_url": source_of(entry)})
    for key, entry in {**IMAGE_ASSETS, **LOGO_ASSETS}.items():
        rows.append({"key": key, "kind": "image", **entry,
                     "source_url": source_of(entry)})
    return rows


def _clean(values, limit: int) -> list[str]:
    out, seen = [], set()
    for raw in values or []:
        text = " ".join(str(raw or "").split())[:limit]
        low = text.lower()
        if text and low not in seen:
            seen.add(low)
            out.append(text)
    return out


def normalise_asset_group(group: dict, *, business_name: str = "") -> dict:
    """One asset group, clamped to Google's own limits.

    The model is good and is never trusted with a character limit — the rule
    `campaign_ai.normalise()` already applies to a responsive search ad, one
    campaign type over.
    """
    group = group or {}
    text = TEXT_ASSETS
    name = " ".join(str(group.get("businessName") or business_name or "").split())
    return {
        "name": " ".join(str(group.get("name") or "Asset group").split())[:120],
        "theme": " ".join(str(group.get("theme") or "").split())[:400],
        "businessName": name[:text["businessName"]["max_chars"]],
        "headlines": _clean(group.get("headlines"),
                            text["headlines"]["max_chars"])[:text["headlines"]["maximum"]],
        "longHeadlines": _clean(group.get("longHeadlines"),
                                text["longHeadlines"]["max_chars"])[:text["longHeadlines"]["maximum"]],
        "descriptions": _clean(group.get("descriptions"),
                               text["descriptions"]["max_chars"])[:text["descriptions"]["maximum"]],
        # Search themes are the audience signal, not an asset. Kept because the
        # generator is asked for them and dropping them would lose the one
        # thing a rep can steer a PMax campaign with.
        "searchThemes": _clean(group.get("searchThemes"), 80)[:25],
        "images": _normalise_images(group.get("images")),
    }


def _normalise_images(images) -> list[dict]:
    out = []
    for item in (images or [])[:MAX_IMAGES_TOTAL + MAX_LOGOS_TOTAL]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        if role not in IMAGE_ASSETS and role not in LOGO_ASSETS:
            continue
        out.append({
            "role": role,
            "url": str(item.get("url") or "").strip()[:2000],
            "name": " ".join(str(item.get("name") or "").split())[:128],
            "prompt": " ".join(str(item.get("prompt") or "").split())[:600],
            "width": item.get("width"), "height": item.get("height"),
        })
    return out


def validate(group: dict) -> dict:
    """What Google will refuse, and separately what it merely wishes for.

    `errors` are refusals: the deploy is not attempted with one outstanding,
    because Google rolls back the whole mutate and the message a rep gets back
    names a resource rather than the field they need to fill in. `warnings` are
    ad strength, and never block — a check that refuses the correct thing is a
    check somebody switches off.
    """
    group = group or {}
    errors, warnings = [], []

    for key, entry in TEXT_ASSETS.items():
        values = group.get(key)
        values = [values] if isinstance(values, str) else (values or [])
        values = [v for v in values if str(v).strip()]
        if len(values) < entry["minimum"]:
            errors.append(
                f'{entry["label"]}: Google needs at least {entry["minimum"]}, '
                f'and this asset group has {len(values)}.')
        elif len(values) < entry.get("recommended", 0):
            warnings.append(
                f'{entry["label"]}: {len(values)} supplied. {entry["recommended"]} '
                f'or more lifts ad strength.')
        over = [v for v in values if len(str(v)) > entry["max_chars"]]
        if over:
            errors.append(
                f'{entry["label"]}: {len(over)} over {entry["max_chars"]} characters.')

    desc_rule = TEXT_ASSETS["descriptions"]
    descriptions = [str(d) for d in (group.get("descriptions") or []) if str(d).strip()]
    short = [d for d in descriptions if len(d) <= desc_rule["short_max_chars"]]
    if descriptions and len(short) < desc_rule["short_minimum"]:
        # Individually valid and collectively refused, which is the one every
        # generator gets wrong.
        errors.append(
            f'Descriptions: at least {desc_rule["short_minimum"]} must be '
            f'{desc_rule["short_max_chars"]} characters or fewer, and none is.')

    by_role: dict[str, int] = {}
    for image in group.get("images") or []:
        if str((image or {}).get("url") or "").strip():
            by_role[image.get("role")] = by_role.get(image.get("role"), 0) + 1
    for key, entry in {**IMAGE_ASSETS, **LOGO_ASSETS}.items():
        have = by_role.get(key, 0)
        if have < entry["minimum"]:
            # Named per ratio: "you are missing the square" and "you have no
            # images at all" are different sentences, and only one of them is
            # one field to fix.
            errors.append(
                f'{entry["label"]} ({entry["ratio"]}): at least '
                f'{entry["minimum"]} required, {have} supplied.')
        elif entry["minimum"] == 0 and have == 0:
            warnings.append(
                f'{entry["label"]} ({entry["ratio"]}): none supplied. Optional, '
                f'and it opens placements the required ratios do not.')

    images = sum(n for role, n in by_role.items() if role in IMAGE_ASSETS)
    logos = sum(n for role, n in by_role.items() if role in LOGO_ASSETS)
    if images > MAX_IMAGES_TOTAL:
        errors.append(f"Images: {images} supplied, {MAX_IMAGES_TOTAL} is the maximum.")
    if logos > MAX_LOGOS_TOTAL:
        errors.append(f"Logos: {logos} supplied, {MAX_LOGOS_TOTAL} is the maximum.")

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "note": TRANSCRIBED_NOTE}


def validate_campaign(campaign: dict) -> dict:
    """Every asset group on a Performance Max campaign, reported per group."""
    groups = (campaign or {}).get("assetGroups") or []
    if not groups:
        return {"ok": False, "groups": [],
                "errors": ["A Performance Max campaign needs at least one asset group."],
                "warnings": [], "note": TRANSCRIBED_NOTE}
    rows, errors, warnings = [], [], []
    for index, group in enumerate(groups):
        result = validate(group)
        label = group.get("name") or f"Asset group {index + 1}"
        rows.append({"name": label, **result})
        errors += [f"{label} — {e}" for e in result["errors"]]
        warnings += [f"{label} — {w}" for w in result["warnings"]]
    return {"ok": not errors, "groups": rows, "errors": errors,
            "warnings": warnings, "note": TRANSCRIBED_NOTE}
