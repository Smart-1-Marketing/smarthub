"""Brand details observed by an Insites website audit.

This module only describes what the completed scan saw on the live website.
It deliberately does not merge the result with Brandfetch or the client's
approved asset library: a mark scraped from a page is useful evidence, but it
is not automatically safe to put on client-facing work.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from .audit_fields import get_field


COLOUR_FIELDS = (
    ("colour_scheme.primary_accent_colour", "Primary accent"),
    ("colour_scheme.secondary_accent_colour", "Secondary accent"),
    ("colour_scheme.primary_background_colour", "Primary background"),
    ("colour_scheme.secondary_background_colour", "Secondary background"),
    ("colour_scheme.primary_text_colour", "Primary text"),
    ("colour_scheme.secondary_text_colour", "Secondary text"),
)

SCREENSHOT_FIELDS = (
    ("website_screenshot.desktop_screenshot_url", "Desktop"),
    ("website_screenshot.mobile_screenshot_url", "Mobile"),
    ("mobile.mobile_screenshot_url", "Mobile"),
    ("mobile.tablet_screenshot_url", "Tablet"),
)

_HEX = re.compile(r"^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RGB = re.compile(
    r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})"
    r"(?:\s*,\s*(?:0(?:\.\d+)?|1(?:\.0+)?)\s*)?\)$",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    """A displayable scalar, never a Python container repr."""
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _hex(value: Any) -> str:
    """Normalize the CSS colours Insites emits to a safe hex value."""
    raw = _text(value)
    match = _HEX.fullmatch(raw)
    if match:
        digits = match.group(1).upper()
        if len(digits) in (3, 4):
            digits = "".join(ch * 2 for ch in digits)
        return "#" + digits

    match = _RGB.fullmatch(raw)
    if not match:
        return ""
    channels = [int(match.group(i)) for i in range(1, 4)]
    if any(channel > 255 for channel in channels):
        return ""
    return "#" + "".join(f"{channel:02X}" for channel in channels)


def _on_colour(value: str) -> str:
    """Readable text for a swatch; alpha, when present, does not affect it."""
    digits = value.lstrip("#")[:6]
    if len(digits) != 6:
        return "#172033"
    red, green, blue = (int(digits[i:i + 2], 16) for i in (0, 2, 4))
    luminance = (0.2126 * red) + (0.7152 * green) + (0.0722 * blue)
    return "#172033" if luminance > 150 else "#FFFFFF"


def _base_url(report: dict, base_url: str = "") -> str:
    candidate = _text(base_url) or _text(report.get("domain"))
    if not candidate:
        return ""
    if not candidate.startswith(("http://", "https://")):
        candidate = "https://" + candidate.lstrip("/")
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return candidate.rstrip("/") + "/"


def _asset_url(value: Any, base_url: str) -> str:
    raw = _text(value)
    if not raw:
        return ""
    joined = urljoin(base_url, raw) if base_url else raw
    parsed = urlparse(joined)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return joined


def _walk_urls(value: Any, base_url: str) -> list[str]:
    """Find every URL in the logo payload, including future variant arrays."""
    if isinstance(value, str):
        found = _asset_url(value, base_url)
        return [found] if found else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            out.extend(_walk_urls(item, base_url))
        return out
    if not isinstance(value, dict):
        return []

    out = []
    for key, item in value.items():
        name = str(key).lower()
        if isinstance(item, str) and any(
                token in name for token in ("url", "src", "image", "logo")):
            found = _asset_url(item, base_url)
            if found:
                out.append(found)
        elif isinstance(item, (dict, list, tuple)):
            out.extend(_walk_urls(item, base_url))
    return out


def _unique(items: list[str]) -> list[str]:
    out = []
    seen = set()
    for item in items:
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def identity(report: dict, *, base_url: str = "") -> dict:
    """Return everything useful for the scan detail's observed-brand card."""
    report = report if isinstance(report, dict) else {}
    base = _base_url(report, base_url)

    palette_by_hex: dict[str, dict] = {}
    for path, role in COLOUR_FIELDS:
        value = _hex(get_field(report, path))
        if not value:
            continue
        item = palette_by_hex.setdefault(
            value, {"hex": value, "roles": [], "on_color": _on_colour(value)})
        item["roles"].append(role)
    palette = list(palette_by_hex.values())
    for item in palette:
        item["label"] = " / ".join(item["roles"])

    logo_urls = _walk_urls(report.get("logo"), base)
    canonical_logo = _asset_url(get_field(report, "logo.logo_url"), base)
    if canonical_logo:
        logo_urls.insert(0, canonical_logo)
    logo_urls = _unique(logo_urls)
    marks = [
        {"url": url, "kind": "Logo", "label": (
            "Primary logo" if index == 0 else f"Logo variant {index + 1}")}
        for index, url in enumerate(logo_urls)
    ]

    favicon = report.get("favicon") if isinstance(report.get("favicon"), dict) else {}
    favicon_url = _asset_url(favicon.get("favicon_location"), base)
    if favicon_url and favicon_url.casefold() not in {
            item["url"].casefold() for item in marks}:
        marks.append({
            "url": favicon_url,
            "kind": "Site icon",
            "label": "Favicon",
            "format": _text(favicon.get("favicon_type")).upper(),
        })

    previews = []
    seen_previews = set()
    for path, label in SCREENSHOT_FIELDS:
        url = _asset_url(get_field(report, path), base)
        if not url or url.casefold() in seen_previews:
            continue
        seen_previews.add(url.casefold())
        previews.append({"url": url, "label": label})

    favicon_notes = []
    if favicon.get("favicon_is_too_small") is True:
        favicon_notes.append("The detected favicon may be too small.")
    if favicon.get("favicon_is_recommended_type") is False:
        favicon_notes.append("The favicon is not in a recommended format.")

    title = _text(get_field(
        report, "page_titles_and_descriptions.homepage_title_tag"))
    description = _text(get_field(
        report, "page_titles_and_descriptions.homepage_meta_description"))
    google_fonts = get_field(report, "gdpr.has_google_font_api")
    if not isinstance(google_fonts, bool):
        google_fonts = None

    found = bool(palette or marks or previews or title or description)
    return {
        "found": found,
        "palette": palette,
        "marks": marks,
        "previews": previews,
        "homepage_title": title,
        "homepage_description": description,
        "uses_google_fonts": google_fonts,
        "favicon_notes": favicon_notes,
        "source": "Observed in this InSites scan",
        "note": (
            "These elements were detected on the live website. Treat them as "
            "brand candidates until the client approves them."
            if found else
            "This scan did not return a logo, color scheme, site icon, or preview."
        ),
    }
