"""Pixabay Video service — free stock footage, normalized to the Commercial
Builder's universal asset shape (see routes/stock.py)."""

import os

import requests

API_KEY = os.environ.get("PIXABAY_API_KEY")
BASE_URL = "https://pixabay.com/api/videos/"


def is_live():
    return bool(API_KEY)


def search(query, per_page=8, orientation=None):
    if not is_live():
        return _mock_results(query, per_page)

    params = {"key": API_KEY, "q": query, "per_page": max(per_page, 3)}
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
