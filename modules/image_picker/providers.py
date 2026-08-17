"""
Free stock photo providers: Pexels, Pixabay, Unsplash.

Same approach the Image Creator uses -- fan out in parallel, normalise to one
shape, interleave so no single provider dominates the grid, and let one provider
fail quietly rather than failing the whole search.

Differences from the Image Creator's photo_search:

* We search several *curated* queries per request (one topic = 3 phrasings) and
  interleave across query AND provider, so a topic tap returns variety instead
  of one query's top 24.
* Results are soft-filtered against negative terms from the taxonomy.
* Every result carries the licence/attribution metadata we are obliged to keep,
  and it rides all the way through to Cloudinary context on save.

Licence note (this is why "free" is not the same as "no rules"):
  - Pexels and Pixabay: free for commercial use, attribution appreciated but not
    required. We store it anyway.
  - Unsplash: free for commercial use, but the API terms require that we ping
    the photo's `download_location` endpoint whenever a user actually selects a
    photo for use. `trigger_unsplash_download()` does that, and it is called on
    save, not on browse. Do not remove it -- it is the condition of the API key.
"""

from __future__ import annotations

import logging
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

log = logging.getLogger(__name__)

HTTP_TIMEOUT = float(os.environ.get("IMAGE_PICKER_HTTP_TIMEOUT", "12"))
CACHE_TTL = int(os.environ.get("IMAGE_PICKER_CACHE_TTL", "1800"))  # 30 min

# Simple in-process cache. Stock results for "AC repair" do not change minute to
# minute, and every cached hit is a provider rate-limit call we did not spend.
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_MAX = 400


def _cache_get(key: str) -> list[dict[str, Any]] | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.time() - ts > CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return val


def _cache_put(key: str, val: list[dict[str, Any]]) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[: _CACHE_MAX // 4]:
            _CACHE.pop(k, None)
    _CACHE[key] = (time.time(), val)


def configured_providers() -> dict[str, bool]:
    return {
        "pexels": bool(os.environ.get("PEXELS_API_KEY")),
        "pixabay": bool(os.environ.get("PIXABAY_API_KEY")),
        "unsplash": bool(os.environ.get("UNSPLASH_ACCESS_KEY")),
    }


def any_provider_configured() -> bool:
    return any(configured_providers().values())


# --------------------------------------------------------------------------- #
# Per-provider adapters. Each returns the normalised shape or raises.
# --------------------------------------------------------------------------- #

_ORIENTATION = {
    "pexels": {"landscape": "landscape", "portrait": "portrait", "square": "square"},
    "pixabay": {"landscape": "horizontal", "portrait": "vertical", "square": "all"},
    "unsplash": {"landscape": "landscape", "portrait": "portrait", "square": "squarish"},
}


def _norm(
    *,
    provider: str,
    pid: str,
    thumb: str,
    preview: str,
    full: str,
    width: int,
    height: int,
    author: str,
    author_url: str,
    source_url: str,
    alt: str,
    tags: str,
    download_location: str = "",
) -> dict[str, Any]:
    return {
        "id": f"{provider}:{pid}",
        "provider": provider,
        "provider_image_id": str(pid),
        "thumbnail": thumb,
        "preview": preview,
        "full": full,
        "width": int(width or 0),
        "height": int(height or 0),
        "author": author or "",
        "author_url": author_url or "",
        "source_url": source_url or "",
        "alt": (alt or "").strip(),
        "tags": (tags or "").lower(),
        "download_location": download_location,
    }


def search_pexels(query: str, *, per_page: int, page: int, orientation: str | None) -> list[dict[str, Any]]:
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return []
    params: dict[str, Any] = {"query": query, "per_page": per_page, "page": page}
    o = _ORIENTATION["pexels"].get(orientation or "")
    if o:
        params["orientation"] = o
    r = requests.get(
        "https://api.pexels.com/v1/search",
        params=params,
        headers={"Authorization": key},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    out = []
    for p in (r.json() or {}).get("photos", []) or []:
        src = p.get("src") or {}
        out.append(_norm(
            provider="pexels",
            pid=p.get("id"),
            thumb=src.get("medium") or src.get("small") or "",
            preview=src.get("large") or src.get("medium") or "",
            full=src.get("original") or src.get("large2x") or "",
            width=p.get("width", 0),
            height=p.get("height", 0),
            author=p.get("photographer", ""),
            author_url=p.get("photographer_url", ""),
            source_url=p.get("url", ""),
            alt=p.get("alt", ""),
            tags=p.get("alt", ""),
        ))
    return out


def search_pixabay(query: str, *, per_page: int, page: int, orientation: str | None) -> list[dict[str, Any]]:
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return []
    params: dict[str, Any] = {
        "key": key,
        "q": query,
        "image_type": "photo",
        "safesearch": "true",
        # Pixabay rejects per_page below 3.
        "per_page": max(3, per_page),
        "page": page,
    }
    o = _ORIENTATION["pixabay"].get(orientation or "")
    if o:
        params["orientation"] = o
    r = requests.get("https://pixabay.com/api/", params=params, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    out = []
    for p in (r.json() or {}).get("hits", []) or []:
        out.append(_norm(
            provider="pixabay",
            pid=p.get("id"),
            thumb=p.get("webformatURL", ""),
            preview=p.get("webformatURL", ""),
            full=p.get("largeImageURL") or p.get("webformatURL", ""),
            width=p.get("imageWidth", 0),
            height=p.get("imageHeight", 0),
            author=p.get("user", ""),
            author_url=f"https://pixabay.com/users/{p.get('user', '')}-{p.get('user_id', '')}/",
            source_url=p.get("pageURL", ""),
            alt=p.get("tags", ""),
            tags=p.get("tags", ""),
        ))
    return out


def search_unsplash(query: str, *, per_page: int, page: int, orientation: str | None) -> list[dict[str, Any]]:
    key = os.environ.get("UNSPLASH_ACCESS_KEY")
    if not key:
        return []
    params: dict[str, Any] = {"query": query, "per_page": per_page, "page": page}
    o = _ORIENTATION["unsplash"].get(orientation or "")
    if o:
        params["orientation"] = o
    r = requests.get(
        "https://api.unsplash.com/search/photos",
        params=params,
        headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1"},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    out = []
    for p in (r.json() or {}).get("results", []) or []:
        urls = p.get("urls") or {}
        user = p.get("user") or {}
        links = p.get("links") or {}
        out.append(_norm(
            provider="unsplash",
            pid=p.get("id"),
            thumb=urls.get("small") or urls.get("thumb", ""),
            preview=urls.get("regular") or urls.get("small", ""),
            full=urls.get("full") or urls.get("raw", ""),
            width=p.get("width", 0),
            height=p.get("height", 0),
            author=user.get("name", ""),
            author_url=((user.get("links") or {}).get("html", "")),
            source_url=links.get("html", ""),
            alt=p.get("alt_description") or p.get("description") or "",
            tags=" ".join(
                (t or {}).get("title", "") for t in (p.get("tags") or [])
            ) or (p.get("alt_description") or ""),
            download_location=links.get("download_location", ""),
        ))
    return out


_ADAPTERS = {
    "pexels": search_pexels,
    "pixabay": search_pixabay,
    "unsplash": search_unsplash,
}


def trigger_unsplash_download(download_location: str) -> bool:
    """Required by the Unsplash API terms when a photo is actually used.

    Called on save, not on browse. Never raises -- a failed ping must not cost
    the client their image.
    """
    key = os.environ.get("UNSPLASH_ACCESS_KEY")
    if not key or not download_location:
        return False
    try:
        r = requests.get(
            download_location,
            headers={"Authorization": f"Client-ID {key}"},
            timeout=HTTP_TIMEOUT,
        )
        return r.ok
    except Exception as exc:  # noqa: BLE001
        log.warning("unsplash download ping failed: %s", exc)
        return False


# --------------------------------------------------------------------------- #
# Fan-out
# --------------------------------------------------------------------------- #

def _filter_negatives(items: list[dict[str, Any]], negatives: list[str]) -> list[dict[str, Any]]:
    if not negatives:
        return items
    terms = [n.lower().strip() for n in negatives if n and n.strip()]
    kept = []
    for it in items:
        hay = f"{it.get('alt', '')} {it.get('tags', '')}".lower()
        if any(t in hay for t in terms):
            continue
        kept.append(it)
    return kept


def _interleave(buckets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Round-robin across buckets so no one provider or query owns the top row."""
    out: list[dict[str, Any]] = []
    i = 0
    while True:
        added = False
        for b in buckets:
            if i < len(b):
                out.append(b[i])
                added = True
        if not added:
            break
        i += 1
    return out


def search(
    queries: list[str],
    *,
    per_page: int = 12,
    page: int = 1,
    orientation: str | None = None,
    negatives: list[str] | None = None,
    limit: int = 36,
    seed: int | None = None,
) -> dict[str, Any]:
    """Search every configured provider for every query, in parallel.

    Returns {"results": [...], "providers": {name: ok|error|off}, "cached": bool}.
    Never raises on provider failure; only an all-provider failure is surfaced,
    and even then as data rather than an exception.
    """
    queries = [q.strip() for q in (queries or []) if q and q.strip()][:4]
    if not queries:
        return {"results": [], "providers": {}, "cached": False}

    active = [name for name, on in configured_providers().items() if on]
    if not active:
        return {"results": [], "providers": {n: "off" for n in _ADAPTERS}, "cached": False}

    cache_key = "|".join([
        ",".join(sorted(queries)), str(per_page), str(page),
        orientation or "-", ",".join(sorted(active)),
    ])
    cached = _cache_get(cache_key)
    if cached is not None:
        results = _filter_negatives(cached, negatives or [])
        return {"results": results[:limit], "providers": {n: "ok" for n in active}, "cached": True}

    status: dict[str, str] = {n: "off" for n in _ADAPTERS}
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}

    jobs = [(q, name) for q in queries for name in active]
    with ThreadPoolExecutor(max_workers=min(8, len(jobs) or 1)) as pool:
        futures = {
            pool.submit(
                _ADAPTERS[name], q,
                per_page=per_page, page=page, orientation=orientation,
            ): (q, name)
            for q, name in jobs
        }
        for fut in as_completed(futures):
            q, name = futures[fut]
            try:
                buckets[(q, name)] = fut.result() or []
                if status.get(name) != "error":
                    status[name] = "ok"
            except Exception as exc:  # noqa: BLE001
                status[name] = "error"
                buckets[(q, name)] = []
                log.warning("provider %s failed for %r: %s", name, q, exc)

    # Interleave query-major so the first row spans all three phrasings.
    ordered_keys = [(q, name) for q in queries for name in active]
    merged = _interleave([buckets.get(k, []) for k in ordered_keys])

    # Dedupe on provider id and on full URL -- the same photo genuinely does
    # appear under several of our phrasings.
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    deduped = []
    for it in merged:
        url_key = (it.get("full") or it.get("preview") or "").split("?")[0]
        if it["id"] in seen_ids or (url_key and url_key in seen_urls):
            continue
        seen_ids.add(it["id"])
        if url_key:
            seen_urls.add(url_key)
        deduped.append(it)

    # A stable per-session shuffle inside the head of the list keeps repeat
    # visits from showing an identical grid without making paging incoherent.
    if seed is not None and len(deduped) > 6:
        head = deduped[:limit]
        random.Random(seed).shuffle(head)
        deduped = head + deduped[limit:]

    _cache_put(cache_key, deduped)
    filtered = _filter_negatives(deduped, negatives or [])
    return {"results": filtered[:limit], "providers": status, "cached": False}
