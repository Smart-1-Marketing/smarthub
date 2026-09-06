"""Coverr Video — a free stock footage provider, searched live rather than
indexed.

Shared between the two screens that search for video footage: the standalone
Video Search tool (`modules/video_backgrounds`, our own Cloudinary library
plus this) and the Commercial Builder's stock search
(`modules/commercial_builder/routes/stock.py`, which fans this out alongside
Pexels and Pixabay). One implementation for both, the reason
`hub/video_library.py` is the single reader of the owned library rather than
each screen keeping its own copy — the Pexels key had to be fixed twice
before that rule existed here.
`modules/commercial_builder/services/coverr_service.py` re-exports these
names under their old module path so its existing callers are unchanged, the
same shape `modules/radio_promo/voices.py` uses for `hub/voice_casting.py`.

Results come back in the universal asset shape the Commercial Builder's other
stock providers use (id/provider/tier/thumbnail/preview_url/full_url/width/
height/duration/author/source_url) rather than `hub/video_library.py`'s
owned-clip shape (which carries `public_id`, `folder`, `tags`, `description`
and a transformed `background_url` — none of which Coverr's schema has, since
nothing here transforms or re-hosts a Coverr clip the way owned footage is
transformed through Cloudinary).

Two credentials, not one: COVERR_API_KEY (or COVERR_API / COVERR_KEY) is the
documented one, sent as a Bearer token. COVERR_APP_ID is not in Coverr's
published API docs at all — see _app_id()'s docstring for why it is sent
anyway, and how a wrong guess there is discovered."""

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


def _app_id():
    """The Coverr app id, if this deployment has one.

    Coverr's own published docs describe exactly one credential — the API
    key, sent as an Authorization header or an api_key query param — and
    never mention a second value on a request. What their developer console
    calls an "app" is where the key comes from (you register an application
    to get one), which reads as bookkeeping on their side rather than
    something this service has to send.

    This deployment's Coverr account issued an app id alongside its key
    anyway, so it is included on every request rather than left unused —
    on the working assumption that Coverr scopes a key to its app the way
    Algolia scopes a key to an application id, which is the closest
    documented shape to "create an app, get a key" this codebase has seen
    elsewhere. That assumption is unconfirmed: outbound access to
    api.coverr.co is blocked from this environment, so it could not be
    checked against a live response. If it is wrong, Coverr simply ignores
    an extra query parameter it does not recognise and nothing here changes;
    if the key alone is refused where key+app_id would have been accepted,
    that surfaces as "Coverr refused the key" on the Commercial Builder's
    Check keys panel (services/provider_check.py), which is the one place
    this deployment can tell the two apart without a live doc to read.
    """
    return settings.coverr_app_id


def is_live():
    return bool(_key())


def search(query, per_page=8, orientation=None):
    """Search Coverr's video library.

    ``orientation`` is accepted and ignored — the same shape as
    pixabay_service.search: Coverr's videos endpoint publishes no
    orientation filter, and callers pass one because Pexels honours it.
    Dropping the argument here would just move the mismatch to the call
    site.

    Authenticated with an ``Authorization: Bearer <key>`` header, which is
    the form Coverr's own docs give (a ``?api_key=`` query param also works,
    but a header keeps the key out of logs and referrers). ``app_id`` rides
    along as a query parameter when this deployment has one — see
    ``_app_id()`` for why that is a best-effort inclusion rather than a
    documented requirement.

    Never raises: a caller with an otherwise-working screen must not go down
    because Coverr is unreachable, the same rule pexels_service and
    pixabay_service follow.
    """
    if not is_live():
        return _mock_results(query, per_page)

    params = {"query": query, "page_size": max(1, min(int(per_page or 8), 50))}
    if _app_id():
        params["app_id"] = _app_id()
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
            # Coverr is a curated in-house library rather than a marketplace
            # of individual contributors, so its schema carries no per-clip
            # attribution field — "Coverr" names the source without
            # inventing a person, the same shape hub/video_library.py uses
            # for the Hub's own footage ("Smart 1 library").
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
