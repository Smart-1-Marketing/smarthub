"""Pixabay Video service — free stock footage, normalized to the Commercial
Builder's universal asset shape (see routes/stock.py)."""

import requests

from hub.config import settings

BASE_URL = "https://pixabay.com/api/videos/"


def _key():
    """The Pixabay key, under whichever name it is actually set.

    Same defect as pexels_service: read as PIXABAY_API_KEY at import, set on
    Render as PIXABAY_API, so every search silently returned placeholders.
    See the note there — hub.config accepts both spellings.
    """
    return settings.pixabay_key


def is_live():
    return bool(_key())


def search(query, per_page=8, orientation=None):
    """Search Pixabay video.

    ``orientation`` is accepted and ignored: the Pixabay video API has no
    orientation filter. The caller in routes/stock.py passes one because Pexels
    honours it, and dropping the argument here would just move the mismatch to
    the call site. Results are filtered by shape downstream, not here.
    """
    if not is_live():
        return _mock_results(query, per_page)

    params = {"key": _key(), "q": query, "per_page": max(per_page, 3)}
    try:
        r = requests.get(BASE_URL, params=params, timeout=8)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []

    results = []
    for v in data.get("hits", []):
        videos = v.get("videos", {})
        large = videos.get("large") or videos.get("medium") or {}
        results.append({
            "id": f"pixabay_{v['id']}", "provider": "pixabay", "tier": "FREE",
            "thumbnail": (videos.get("tiny", {}) or {}).get("thumbnail") or large.get("thumbnail"),
            "preview_url": large.get("url"),
            "full_url": large.get("url"),
            "width": large.get("width"), "height": large.get("height"),
            "duration": v.get("duration"),
            "author": v.get("user"),
            "source_url": v.get("pageURL"),
        })
    return results[:per_page]


def _mock_results(query, per_page):
    return [{
        "id": f"pixabay_mock_{i}", "provider": "pixabay", "tier": "FREE",
        "thumbnail": f"https://placehold.co/480x270/2b1a4c/ffffff?text=Pixabay+%23{i+1}%0A{query[:24]}",
        "preview_url": None, "full_url": None,
        "width": 1920, "height": 1080, "duration": 15,
        "author": "Mock Contributor", "source_url": "https://pixabay.com",
        "_mock": True,
    } for i in range(min(per_page, 4))]
