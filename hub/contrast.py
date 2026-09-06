"""WCAG relative luminance and contrast ratio — one implementation.

``modules/scans/reports.py`` had this as a closure inside ``s_contrast()``,
reachable by nothing else. The Display Ad Studio QC panel needs the identical
math to judge a text object against what is behind it, and restating it would
be the drift ``hub/storage.py`` and ``hub/images.py`` exist to stop, wearing a
color check. This is the one reading now; ``s_contrast()`` calls it too.
"""
from __future__ import annotations


def relative_luminance(hex_color: str) -> float | None:
    """WCAG relative luminance of a `#rrggbb` (or `rrggbb`) color, or None."""
    h = (hex_color or "").lstrip("#").strip()
    if len(h) != 6:
        return None
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast_ratio(hex_a: str, hex_b: str) -> float | None:
    """WCAG contrast ratio between two colors, or None if either can't be read."""
    la, lb = relative_luminance(hex_a), relative_luminance(hex_b)
    if la is None or lb is None:
        return None
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
