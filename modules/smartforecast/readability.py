"""Deterministic WCAG readability checks for SmartForecast heroes.

The Display Builder measures contrast instead of asking a model whether text
"looks readable".  SmartForecast follows the same rule.  Its hero keeps copy
over a fixed dark scrim, so even remote images that cannot be sampled have a
known worst-case background.  Every route that can publish content uses this
module; AI-authored and human-authored drafts therefore pass the same gate.
"""
from __future__ import annotations

import re
from typing import Any


COPY_SCRIM = "#051521"
COPY_SCRIM_ALPHA = 0.82
WCAG_AA_RATIO = 4.5
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def normalize_hex(value: Any, fallback: str) -> str:
    candidate = str(value or "").strip()
    return candidate.lower() if _HEX.fullmatch(candidate) else fallback.lower()


def _rgb(value: str) -> tuple[int, int, int]:
    value = normalize_hex(value, "#000000")
    return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))


def _channel(value: int) -> float:
    component = value / 255
    return component / 12.92 if component <= 0.04045 else ((component + 0.055) / 1.055) ** 2.4


def relative_luminance(value: str) -> float:
    red, green, blue = (_channel(item) for item in _rgb(value))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    light, dark = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def composite(foreground: str, background: str, alpha: float) -> str:
    """Return foreground painted over background at ``alpha`` as a hex color."""
    alpha = min(1.0, max(0.0, float(alpha)))
    front = _rgb(foreground)
    back = _rgb(background)
    channels = [round(alpha * front[index] + (1 - alpha) * back[index]) for index in range(3)]
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def protected_background() -> str:
    """Worst case under the copy scrim: the source image is pure white."""
    return composite(COPY_SCRIM, "#ffffff", COPY_SCRIM_ALPHA)


def assess_readability(branding: dict | None, content: dict | None) -> dict:
    branding = branding or {}
    content = content or {}
    backdrop = protected_background()
    colors = {
        "headline": normalize_hex(branding.get("headline_color"), "#ffffff"),
        "body": normalize_hex(branding.get("body_color"), "#dce7f2"),
        "button": normalize_hex(branding.get("button_color"), "#f6b544"),
        "button_text": normalize_hex(branding.get("button_text"), "#071726"),
    }
    checks = [
        _check("headline", "Headline", colors["headline"], backdrop),
        _check("body", "Body text", colors["body"], backdrop),
        _check("button", "Button label", colors["button_text"], colors["button"]),
    ]
    return {
        "ok": all(item["passed"] for item in checks),
        "standard": "WCAG 2.1 AA",
        "required_ratio": WCAG_AA_RATIO,
        "protected_background": backdrop,
        "copy_scrim": COPY_SCRIM,
        "copy_scrim_alpha": COPY_SCRIM_ALPHA,
        "checks": checks,
        "overlay_opacity": min(0.9, max(0.0, _number(content.get("overlay_opacity"), 0.0))),
    }


def _check(key: str, label: str, foreground: str, background: str) -> dict:
    ratio = contrast_ratio(foreground, background)
    return {
        "key": key,
        "label": label,
        "foreground": foreground,
        "background": background,
        "ratio": round(ratio, 2),
        "passed": ratio >= WCAG_AA_RATIO,
    }


def failure_message(result: dict) -> str:
    failed = [f"{item['label']} {item['ratio']:.1f}:1" for item in result.get("checks", [])
              if not item.get("passed")]
    return ("Readability check failed (4.5:1 required): " + ", ".join(failed) +
            ". Adjust the text/button colors before publishing.")


def _number(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
