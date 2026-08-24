"""Pexels Video service — free stock footage, normalized to the Commercial
Builder's universal asset shape (see routes/stock.py)."""

import requests

from hub.config import settings

BASE_URL = "https://api.pexels.com/videos/search"


def _key():
    """The Pexels key, under whichever name it is actually set.

    This module read os.environ["PEXELS_API_KEY"] at import time. Render sets
    it as PEXELS_API, so the read returned None, is_live() returned False, and
    every search fell through to _mock_results() — placehold.co images labelled
    like real footage. Nothing errored and nothing said "no key"; the tool just
    quietly stopped returning real video. hub.config accepts every spelling in
    use, which is the whole reason it exists, and reading it per call rather
    than at import also means a key added in the Render dashboard takes effect
    on restart instead of needing the module reloaded.
    """
    return settings.pexels_key


def is_live():
    return bool(_key())


def search(query, per_page=8, orientation=None):
    if not is_live():
        return _mock_results(query, per_page)

    params = {"query": query, "per_page": per_page}
    if orientation:
        # Pexels expects landscape|portrait|square
        params["orientation"] = orientation
    try:
        r = requests.get(BASE_URL, headers={"Authorization": _key()}, params=params, timeout=8)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []

    results = []
    for v in data.get("videos", []):
        files = sorted(v.get("video_files", []), key=lambda f: f.get("width", 0), reverse=True)
        best = files[0] if files else {}
        hd = next((f for f in files if f.get("quality") == "hd"), best)
        results.append({
            "id": f"pexels_{v['id']}", "provider": "pexels", "tier": "FREE",
            "thumbnail": v.get("image"),
            "preview_url": hd.get("link") or best.get("link"),
            "full_url": best.get("link"),
            "width": v.get("width"), "height": v.get("height"),
            "duration": v.get("duration"),
            "author": v.get("user", {}).get("name"),
            "source_url": v.get("url"),
        })
    return results


def _mock_results(query, per_page):
    return [{
        "id": f"pexels_mock_{i}", "provider": "pexels", "tier": "FREE",
        "thumbnail": f"https://placehold.co/480x270/1a2b4c/ffffff?text=Pexels+%23{i+1}%0A{query[:24]}",
        "preview_url": None, "full_url": None,
        "width": 1920, "height": 1080, "duration": 12,
        "author": "Mock Contributor", "source_url": "https://pexels.com",
        "_mock": True,
    } for i in range(min(per_page, 4))]
