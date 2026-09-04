"""Coverr Video service — free stock footage, normalized to the Commercial
Builder's universal asset shape (see routes/stock.py)."""

import requests

from hub.config import settings

BASE_URL = "https://api.coverr.co/videos"


def _key():
    """The Coverr key, under whichever name it is actually set.

    Read through hub.config rather than os.environ directly, the same shape
    as pexels_service._key() and for the same reason: a key added in the
    Render dashboard under an accepted spelling takes effect on restart
    rather than needing the module reloaded, and reading os.environ directly
    here is exactly the drift hub/integrity.py's provider_key_drift check
    exists to catch.
    """
    return settings.coverr_key


def is_live():
    return bool(_key())


def search(query, per_page=8, orientation=None):
    """Search Coverr's video library.

    ``orientation`` is accepted and ignored — the same shape as
    pixabay_service.search: Coverr's videos endpoint publishes no
    orientation filter, and the caller in routes/stock.py passes one because
    Pexels honours it. Dropping the argument here would just move the
    mismatch to the call site.

    Authenticated with an ``Authorization: Bearer <key>`` header, which is
    the form Coverr's own docs give (a ``?api_key=`` query param also works,
    but a header keeps the key out of logs and referrers).
    """
    if not is_live():
        return _mock_results(query, per_page)

    params = {"query": query, "page_size": max(1, min(int(per_page or 8), 50))}
    try:
        r = requests.get(BASE_URL, headers={"Authorization": f"Bearer {_key()}"},
                         params=params, timeout=8)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []

    results = []
    for v in data.get("hits", []):
        urls = v.get("urls") or {}
        preview = urls.get("mp4_preview") or urls.get("mp4")
        full = urls.get("mp4_download") or urls.get("mp4") or preview
        vid = v.get("id")
        results.append({
            "id": f"coverr_{vid}", "provider": "coverr", "tier": "FREE",
            "thumbnail": v.get("poster") or v.get("thumbnail"),
            "preview_url": preview,
            "full_url": full,
            "width": v.get("max_width"), "height": v.get("max_height"),
            "duration": v.get("duration"),
            # Coverr is a curated in-house library rather than a marketplace of
            # individual contributors, so its schema carries no per-clip
            # attribution field — "Coverr" names the source without inventing
            # a person, the same shape hub/video_library.py uses for the
            # Hub's own footage ("Smart 1 library").
            "author": "Coverr",
            "source_url": f"https://coverr.co/videos/{vid}" if vid else "https://coverr.co",
        })
    return results[:per_page]


def _mock_results(query, per_page):
    return [{
        "id": f"coverr_mock_{i}", "provider": "coverr", "tier": "FREE",
        "thumbnail": f"https://placehold.co/480x270/1a4c2b/ffffff?text=Coverr+%23{i+1}%0A{query[:24]}",
        "preview_url": None, "full_url": None,
        "width": 1920, "height": 1080, "duration": 10,
        "author": "Mock Contributor", "source_url": "https://coverr.co",
        "_mock": True,
    } for i in range(min(per_page, 4))]
