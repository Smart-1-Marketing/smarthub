"""Read helpers for a raw Insites audit payload.

The audit JSON uses dotted namespaces (``amount_of_content.total_word_count``,
``chatgpt.found_in_gpt``, ``meta.detected_name`` …). The values live nested,
e.g. ``report["amount_of_content"]["total_word_count"]``. ``get_field`` walks a
dotted path safely so callers never KeyError on a section a given account/site
didn't return.

This module also defines which field namespaces belong to which client-facing
report (SEO/AEO, Local, Social, Ads, Content&UX, …). Phase 2's report
generators read these groupings; Phase 1 only needs ``summarise``.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

_DICT_PATH = os.path.join(os.path.dirname(__file__), "field_dictionary.json")


def get_field(report: dict, dotted: str, default: Any = None) -> Any:
    """Safely read ``a.b.c`` from a nested audit dict."""
    cur: Any = report
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def summarise(report: dict) -> dict:
    """Pull the handful of fields we surface in the scans table + record.

    Everything is defensive: a missing section yields None, never an error.
    """
    score = get_field(report, "overall_score")
    try:
        score = int(round(float(score))) if score is not None else None
    except (TypeError, ValueError):
        score = None

    return {
        "domain": get_field(report, "domain"),
        "overall_score": score,
        "report_id": get_field(report, "report_id"),
        "detected_name": get_field(report, "meta.detected_name")
                         or get_field(report, "local_presence.detected_name"),
        "detected_phone": get_field(report, "meta.detected_phone")
                          or get_field(report, "local_presence.detected_phone"),
        "detected_address": get_field(report, "meta.detected_address")
                            or get_field(report, "local_presence.detected_address"),
        "primary_industry": get_field(report, "meta.primary_industry"),
        "analysis_country": get_field(report, "meta.analysis_country"),
        "completed_at": get_field(report, "meta.report_completed_at"),
        "pages_analysed": get_field(report, "analysed_page_count")
                          or get_field(report, "amount_of_content.pages_tested"),
    }


def tier_for_score(score: Optional[int]) -> str:
    """Human tier band for a 0-100 score (matches the funnel's bands)."""
    if score is None:
        return "Unknown"
    if score < 25:
        return "Critical"
    if score < 50:
        return "Competitive"
    if score < 75:
        return "Strong"
    return "Dominant"


# --- Report-category field groupings (used from Phase 2 onward) --------------
# Each entry: report key -> list of top-level audit namespaces that feed it.
# Derived from the account's data dictionary (definitions CSV / JSON).
REPORT_CATEGORIES: dict[str, list[str]] = {
    "seo_aeo": [
        "ai_readiness", "chatgpt", "gemini", "perplexity", "gemini_optimisation",
        "voice_search", "eeat", "page_titles_and_descriptions", "structured_data",
        "google_indexability", "bing_indexability", "sitemap", "backlinks",
        "organic_search", "faq",
    ],
    "content_ux": [
        "amount_of_content", "reading_ease", "images", "alternative_text",
        "colour_scheme", "structured_data", "faq", "video", "blog",
        "image_optimisation",
    ],
    "ads": [
        "facebook_ads", "google_ads_readiness", "paid_search", "display_ads",
        "retargeting",
    ],
    "social": [
        "facebook_page", "instagram_account", "x_(formerly_twitter)",
        "tiktok_account", "youtube", "linkedin", "pinterest", "snapchat",
    ],
    "local_seo": [
        "google_business_profile", "local_grid", "local_pack", "reviews",
        "review_integration", "voice_search",
    ],
    # speed/performance, accessibility, compliance are named in the menu; the
    # account's field set covers them via broken_links, page_errors, gdpr, etc.
    "performance": [
        "broken_links", "page_errors", "indexing_errors", "technology_profile",
        "technology_detection",
    ],
    "accessibility": [
        "alternative_text", "colour_scheme", "images",
    ],
    "compliance": [
        "gdpr",
    ],
}


def load_dictionary() -> dict:
    """Load the bundled field dictionary (labels + definitions for 440 fields)."""
    try:
        with open(_DICT_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"fields": [], "checkpoint_summary_definitions": []}


def field_label_map() -> dict:
    """Map every dotted field id -> its human label, from the dictionary."""
    d = load_dictionary()
    out = {}
    for f in d.get("fields", []):
        fid = f.get("id")
        if fid:
            out[fid] = f.get("label") or fid
    return out
