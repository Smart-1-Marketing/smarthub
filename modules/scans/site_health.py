"""Turn a raw Insites audit into the two things a scan reader acts on.

Two readers, two shapes:

* ``fixes()``  — broken links and image problems, as a work list. Every entry
  is something someone can go and put right, with the count and a plain
  sentence saying what to do. Nothing here is a score.
* ``speed()``  — one summary line plus a full metric table. The scan page
  shows the summary and hides the table behind a disclosure, because the
  detail is only wanted once the summary says something is wrong.

Both are defensive in the same way ``audit_fields`` is: an account whose plan
doesn't include a section, or a site where a check couldn't run, yields
``None``/empty rather than an error. A missing section is reported as
"not measured", never as zero — a zero here reads as "you're clean", which
would be a lie.

**A limit worth knowing:** the Insites payload carries *counts* of broken
links and image problems, not the URLs behind them (the field dictionary
defines ``broken_links.links_broken_count`` and no list). So this module can
say "7 broken links" but not which 7. ``linkcheck.py`` exists to answer that
second question, on demand, by checking the site itself.
"""
from __future__ import annotations

from typing import Any, Optional

from .audit_fields import get_field

# --------------------------------------------------------------------------
# Small coercion helpers. Insites occasionally returns a numeric string, or a
# float where an int is documented; none of that should reach a template.
# --------------------------------------------------------------------------


def _int(report: dict, dotted: str) -> Optional[int]:
    v = get_field(report, dotted)
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _float(report: dict, dotted: str) -> Optional[float]:
    v = get_field(report, dotted)
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bool(report: dict, dotted: str) -> Optional[bool]:
    v = get_field(report, dotted)
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    return None


def _band(value: Optional[str]) -> str:
    """Normalise Insites' lighthouse band strings to ok / warn / bad."""
    s = (value or "").strip().lower()
    if s in ("good", "fast", "pass", "green"):
        return "ok"
    if s in ("average", "moderate", "medium", "orange", "amber"):
        return "warn"
    if s in ("bad", "slow", "poor", "fail", "red"):
        return "bad"
    return ""


def _secs(value: Optional[float]) -> Optional[float]:
    """Insites reports some timings in seconds and some in milliseconds.

    The dictionary types don't disambiguate, so decide by magnitude: no real
    First Contentful Paint is 800 seconds, and none is 0.8 milliseconds.
    """
    if value is None:
        return None
    return value / 1000.0 if value > 100 else value


def _fmt_secs(value: Optional[float]) -> str:
    v = _secs(value)
    return "—" if v is None else f"{v:.1f}s"


# ==========================================================================
# Fix list — broken links and images
# ==========================================================================

def fixes(report: dict) -> dict:
    """Broken links + image problems as an actionable work list.

    Returns::

        {"items": [...], "broken_links": 7, "image_issues": 23,
         "total": 30, "measured": True, "worst": "bad"}

    Each item::

        {"key", "label", "count", "unit", "severity", "action", "measured"}

    ``severity`` is one of ok / warn / bad / unknown. Items that couldn't be
    measured are kept (so the reader can see the check didn't run) but are
    sorted last and excluded from the totals.
    """
    if not isinstance(report, dict):
        report = {}

    items: list[dict] = []

    def add(key: str, label: str, count: Optional[int], action: str,
            bad_at: int = 1, warn_at: Optional[int] = None,
            unit: str = "") -> None:
        if count is None:
            sev = "unknown"
        elif count <= 0:
            sev = "ok"
        elif warn_at is not None and count < warn_at:
            sev = "warn"
        elif count >= bad_at:
            sev = "bad"
        else:
            sev = "warn"
        items.append({"key": key, "label": label, "count": count, "unit": unit,
                      "severity": sev, "action": action,
                      "measured": count is not None})

    # ---- Links -----------------------------------------------------------
    broken = _int(report, "broken_links.links_broken_count")
    add("broken_links", "Broken links", broken,
        "Each one is a dead end for a visitor and a wasted crawl for Google. "
        "Point them at the right page or remove them.")

    # ---- Images: alt text ------------------------------------------------
    no_alt = _int(report, "alternative_text.images_no_alt_count")
    add("images_no_alt", "Images with no alt text", no_alt,
        "Invisible to screen readers and to image search. Describe what the "
        "image shows, in a sentence a person would say.",
        warn_at=5)

    empty_alt = _int(report, "alternative_text.number_empty_alt")
    add("images_empty_alt", "Images with empty alt text", empty_alt,
        "An empty alt attribute is only correct for purely decorative "
        "images. Anything meaningful needs real text.", warn_at=5)

    weak_alt = _int(report, "alternative_text.count_weak_alt_tags")
    add("images_weak_alt", "Weak alt text", weak_alt,
        'Alt text like "image1" or "logo" carries no meaning. Rewrite it to '
        "describe the subject.", warn_at=5)

    alt_in_link = _int(report, "alternative_text.count_missing_alt_link")
    add("images_alt_in_link", "Linked images with no alt text", alt_in_link,
        "An image used as a link with no alt text gives a screen-reader user "
        "nothing to click. Highest priority of the alt-text fixes.")

    # ---- Images: weight and sizing --------------------------------------
    to_optimise = _int(report, "image_optimisation.images_to_optimise_count")
    add("images_to_optimise", "Oversized images", to_optimise,
        "These are heavier than they need to be and are usually the single "
        "biggest cause of a slow page. Run them through Tools → SEO Images "
        "to resize and convert to WebP.", warn_at=5)

    non_web = _int(report, "images.non_web_friendly_count")
    add("images_non_web", "Images in a non-web format", non_web,
        "Convert to WebP (or JPG/PNG) so browsers don't have to work around "
        "the format.", warn_at=3)

    stretched = _int(report, "images.stretched_image_count")
    add("images_stretched", "Stretched images", stretched,
        "Displayed at a different shape to the file, so they look distorted. "
        "Re-crop, or fix the width/height in the page.", warn_at=3)

    # Sizing is a percentage, not a count — convert to "how many are missing
    # dimensions" so it sits in the same list without a second unit.
    pct_sized = _float(report, "images.percent_images_sized")
    img_count = _int(report, "images.image_count")
    unsized = None
    if pct_sized is not None and img_count:
        unsized = int(round(img_count * (100.0 - pct_sized) / 100.0))
    add("images_unsized", "Images with no width/height set", unsized,
        "Without dimensions the page jumps around as images load, which "
        "hurts the layout-shift score. Set width and height.", warn_at=5)

    # ---- Totals ----------------------------------------------------------
    measured = [i for i in items if i["measured"]]
    image_issues = sum(i["count"] for i in measured
                       if i["key"] != "broken_links")
    total = sum(i["count"] for i in measured)

    order = {"bad": 0, "warn": 1, "ok": 2, "unknown": 3}
    items.sort(key=lambda i: (order[i["severity"]], -(i["count"] or 0)))

    worst = "unknown"
    for level in ("bad", "warn", "ok"):
        if any(i["severity"] == level for i in items):
            worst = level
            break

    return {
        "items": items,
        "broken_links": broken,
        "image_issues": image_issues if measured else None,
        "total": total if measured else None,
        "measured": bool(measured),
        "worst": worst,
        "pages_discovered": get_field(report, "page_count.pages_discovered") or [],
    }


# ==========================================================================
# Speed
# ==========================================================================

# label, dotted value path, dotted band path, formatter, plain-English note
_SPEED_METRICS: list[tuple[str, str, str, str, str]] = [
    ("Performance score", "website_speed.performance_score",
     "website_speed.performance_score_band", "score",
     "Google's overall 0–100 rating for this page."),
    ("First Contentful Paint", "website_speed.first_contentful_paint",
     "website_speed.first_contentful_paint_band", "secs",
     "How long before the visitor sees anything at all."),
    ("Largest Contentful Paint", "website_speed.largest_contentful_paint",
     "website_speed.largest_contentful_paint_band", "secs",
     "How long before the main image or headline is on screen. "
     "Under 2.5s is the target."),
    ("Speed Index", "website_speed.speed_index",
     "website_speed.speed_index_band", "secs",
     "How quickly the page fills in visually."),
    ("First Meaningful Paint", "website_speed.first_meaningful_paint",
     "website_speed.first_meaningful_paint_band", "secs",
     "When the page's main content becomes visible."),
    ("Server response time", "website_speed.server_response_time", "", "secs",
     "How long the host takes to answer before anything can load. "
     "Over 0.6s points at hosting, not at the page."),
    ("Cumulative Layout Shift", "web_vitals.cumulative_layout_shift",
     "web_vitals.cumulative_layout_shift_band", "raw",
     "How much the page jumps about while loading. Usually images with no "
     "width and height."),
    ("First Input Delay", "web_vitals.first_input_delay",
     "web_vitals.first_input_delay_band", "ms",
     "The lag between a visitor tapping something and the page reacting."),
]


def speed(report: dict) -> dict:
    """Speed summary + the full metric table behind it.

    Returns ``{"available", "blocked", "score", "score_band", "lcp", "lcp_s",
    "headline", "severity", "vitals_pass", "metrics": [...], "notes": [...]}``.

    ``headline`` is one sentence naming the biggest single drag, so the
    summary card can say something useful without the table being open.
    """
    if not isinstance(report, dict):
        report = {}

    success = _bool(report, "website_speed.lighthouse_success")
    if success is None:
        success = _bool(report, "web_vitals.lighthouse_success")
    blocked = bool(_bool(report, "website_speed.lighthouse_blocked")
                   or _bool(report, "web_vitals.lighthouse_blocked"))

    score = _int(report, "website_speed.performance_score")

    metrics: list[dict] = []
    for label, vpath, bpath, kind, note in _SPEED_METRICS:
        raw = _float(report, vpath)
        band = _band(get_field(report, bpath) if bpath else None)
        if raw is None:
            display = "—"
        elif kind == "score":
            display = str(int(round(raw)))
        elif kind == "secs":
            display = _fmt_secs(raw)
        elif kind == "ms":
            display = f"{int(round(raw))} ms"
        else:
            display = f"{raw:.3f}".rstrip("0").rstrip(".")
        # Server response has no band of its own; Google's own threshold is
        # 0.6s, so derive one rather than showing a blank cell.
        if not band and vpath.endswith("server_response_time") and raw is not None:
            s = _secs(raw) or 0
            band = "ok" if s <= 0.6 else ("warn" if s <= 1.2 else "bad")
        metrics.append({"label": label, "value": display, "band": band,
                        "note": note, "measured": raw is not None})

    # Contributing factors that aren't Lighthouse metrics but explain them.
    notes: list[dict] = []
    gzip = _bool(report, "server_behaviour.uses_gzip_compression")
    brotli = _bool(report, "server_behaviour.uses_brotli_compression")
    if gzip is not None or brotli is not None:
        on = bool(gzip) or bool(brotli)
        notes.append({
            "label": "Compression",
            "value": "Brotli" if brotli else ("GZIP" if gzip else "None"),
            "band": "ok" if on else "bad",
            "note": "Text, CSS and JavaScript are sent compressed."
                    if on else
                    "The server is sending files uncompressed — normally a "
                    "one-line hosting change for a large win."})

    heavy = _int(report, "image_optimisation.images_to_optimise_count")
    if heavy is not None:
        notes.append({
            "label": "Oversized images",
            "value": str(heavy),
            "band": "ok" if heavy == 0 else ("warn" if heavy < 5 else "bad"),
            "note": "Image weight is the usual reason a page paints slowly. "
                    "See the fix list above."})

    viewport = _bool(report, "mobile.has_viewport_optimised_for_mobile")
    if viewport is not None:
        notes.append({
            "label": "Mobile viewport",
            "value": "Set" if viewport else "Missing",
            "band": "ok" if viewport else "bad",
            "note": "Without a viewport tag a phone renders the desktop "
                    "layout scaled down."})

    lcp_s = _secs(_float(report, "website_speed.largest_contentful_paint"))
    vitals_pass = _bool(report, "web_vitals.core_web_vitals_pass")

    # ---- Severity + headline --------------------------------------------
    available = bool(metrics and any(m["measured"] for m in metrics))
    if blocked or not available:
        severity = "unknown"
    elif score is not None:
        severity = "ok" if score >= 90 else ("warn" if score >= 50 else "bad")
    else:
        bands = [m["band"] for m in metrics if m["band"]]
        severity = "bad" if "bad" in bands else ("warn" if "warn" in bands else "ok")

    if blocked:
        headline = ("The speed test was blocked by the site — usually a "
                    "firewall or bot protection refusing Google's tester.")
    elif not available:
        headline = "Speed wasn't measured on this scan."
    else:
        worst = next((m for m in metrics
                      if m["band"] == "bad" and m["measured"]
                      and m["label"] != "Performance score"), None)
        worst = worst or next((m for m in metrics
                               if m["band"] == "warn" and m["measured"]
                               and m["label"] != "Performance score"), None)
        heavy_note = next((n for n in notes
                           if n["label"] == "Oversized images"
                           and n["band"] == "bad"), None)
        if severity == "ok" and not worst:
            headline = "This site is fast. Nothing needs doing here."
        elif worst and heavy_note:
            headline = (f"Slowest point is {worst['label'].lower()} at "
                        f"{worst['value']}, and {heavy_note['value']} oversized "
                        "images are the likeliest cause.")
        elif worst:
            headline = (f"Slowest point is {worst['label'].lower()} at "
                        f"{worst['value']}.")
        else:
            headline = "Room to improve, but no single metric stands out."

    return {
        "available": available,
        "blocked": blocked,
        "success": success,
        "score": score,
        "score_band": _band(get_field(report, "website_speed.performance_score_band")),
        "lcp": _fmt_secs(_float(report, "website_speed.largest_contentful_paint")),
        "lcp_s": lcp_s,
        "vitals_pass": vitals_pass,
        "severity": severity,
        "headline": headline,
        "metrics": metrics,
        "notes": notes,
        "device": get_field(report, "web_vitals.lighthouse_device") or "",
    }


def summary(report: dict) -> dict:
    """Both sections at once — what the detail page and Client 360 both want."""
    return {"fixes": fixes(report), "speed": speed(report)}
