"""Universal stock photo search — Pexels, Pixabay and Unsplash as one result set.

The editor should never care where a photo came from. This module fans a single
query out to every configured provider in parallel, normalises the three very
different response shapes into one, removes duplicates, and interleaves the
results so the grid doesn't come back grouped by provider.

Three rules from the spec drive the design:

* **One provider failing must not break the search.** Each provider is called
  independently and its errors are recorded, not raised. The caller only sees
  an error when every configured provider fails.
* **Don't burn rate limit.** Identical queries are served from a short-lived
  cache, so paging back and forth or retyping the same search costs nothing.
* **Filters are translated, not assumed.** The providers support different
  orientation and colour vocabularies; each adapter maps the common request
  onto whatever its provider actually accepts, and ignores the rest.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

TIMEOUT = 12
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
CACHE_SECONDS = int(os.environ.get("PHOTO_CACHE_SECONDS", "900"))

ORIENTATIONS = ("any", "landscape", "portrait", "square")
SORTS = ("relevant", "popular", "new")


# ------------------------------------------------------------------ helpers
# Env var names for the stock providers drifted: this deployment sets
# PEXELS_API and PIXABAY_API, while the code was written against
# PEXELS_API_KEY / PIXABAY_API_KEY. The keys were present and the tool
# reported "no API key set" — worse than a missing key, because everything
# looked configured. hub/config.py already knows every spelling; route
# through it rather than maintaining a second list here.
_ALIASES = {
    "PEXELS_API_KEY": ("PEXELS_API", "PEXELS_API_KEY", "PEXELS_KEY"),
    "PIXABAY_API_KEY": ("PIXABAY_API", "PIXABAY_API_KEY", "PIXABAY_KEY"),
    "UNSPLASH_ACCESS_KEY": ("UNSPLASH_ACCESS_KEY", "UNSPLASH_API",
                            "UNSPLASH_API_KEY", "UNSPLASH_KEY"),
    "GOOGLE_FONTS_API_KEY": ("GOOGLE_FONTS_API", "GOOGLE_FONTS_API_KEY"),
    "REMOVE_BG_API_KEY": ("REMOVE_BG_API", "REMOVE_BG_API_KEY"),
}


def _key(name: str) -> str:
    for candidate in _ALIASES.get(name, (name,)):
        val = (os.environ.get(candidate) or "").strip()
        if val:
            return val
    return ""


def configured() -> dict[str, bool]:
    return {
        "pexels": bool(_key("PEXELS_API_KEY")),
        "pixabay": bool(_key("PIXABAY_API_KEY")),
        "unsplash": bool(_key("UNSPLASH_ACCESS_KEY")),
    }


def any_configured() -> bool:
    return any(configured().values())


def _norm(provider: str, pid, thumbnail: str, preview: str, full: str,
          width, height, author: str, source_url: str,
          download_location: str = "") -> dict:
    """The single shape the front end consumes, whatever the source."""
    return {
        "id": f"{provider}:{pid}",
        "provider": provider,
        "provider_image_id": str(pid),
        "thumbnail": thumbnail,
        "preview": preview,
        "full": full,
        "width": int(width or 0),
        "height": int(height or 0),
        "author": author or "",
        "source_url": source_url or "",
        # Unsplash requires a download ping before use; harmless elsewhere.
        "download_location": download_location,
    }


# ---------------------------------------------------------------- providers
def search_pexels(q: str, page: int, per_page: int, orientation: str,
                  color: str, sort: str = "relevant") -> list[dict]:
    key = _key("PEXELS_API_KEY")
    if not key:
        return []
    params = {"query": q, "page": page, "per_page": per_page}
    if orientation and orientation != "any":
        params["orientation"] = orientation          # landscape|portrait|square
    if color:
        params["color"] = color
    # Pexels' search endpoint has no sort/order parameter — relevance only.
    # Nothing to translate `sort` to here; it's silently ignored per §41's
    # "translate to the closest supported parameter, ignore the rest."
    r = requests.get("https://api.pexels.com/v1/search",
                     headers={"Authorization": key}, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for p in r.json().get("photos", []):
        src = p.get("src") or {}
        out.append(_norm(
            "pexels", p.get("id"),
            src.get("medium") or src.get("small") or "",
            src.get("large") or src.get("medium") or "",
            src.get("original") or src.get("large2x") or "",
            p.get("width"), p.get("height"),
            p.get("photographer", ""), p.get("url", "")))
    return out


def search_pixabay(q: str, page: int, per_page: int, orientation: str,
                   color: str, sort: str = "relevant") -> list[dict]:
    key = _key("PIXABAY_API_KEY")
    if not key:
        return []
    params = {"key": key, "q": q, "page": page, "image_type": "photo",
              "safesearch": "true",
              # Pixabay rejects per_page below 3
              "per_page": max(3, min(int(per_page), 200))}
    if orientation == "landscape":
        params["orientation"] = "horizontal"
    elif orientation == "portrait":
        params["orientation"] = "vertical"
    # Pixabay has no square filter — handled after the fact in _post_filter
    if color:
        params["colors"] = color
    if sort == "new":
        params["order"] = "latest"
    elif sort == "popular":
        params["order"] = "popular"               # Pixabay's own default
    r = requests.get("https://pixabay.com/api/", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for p in r.json().get("hits", []):
        out.append(_norm(
            "pixabay", p.get("id"),
            p.get("previewURL") or "",
            p.get("webformatURL") or "",
            p.get("largeImageURL") or p.get("fullHDURL") or p.get("webformatURL") or "",
            p.get("imageWidth"), p.get("imageHeight"),
            p.get("user", ""), p.get("pageURL", "")))
    return out


def search_unsplash(q: str, page: int, per_page: int, orientation: str,
                    color: str, sort: str = "relevant") -> list[dict]:
    key = _key("UNSPLASH_ACCESS_KEY")
    if not key:
        return []
    params = {"query": q, "page": page, "per_page": per_page}
    if orientation == "landscape":
        params["orientation"] = "landscape"
    elif orientation == "portrait":
        params["orientation"] = "portrait"
    elif orientation == "square":
        params["orientation"] = "squarish"
    if color:
        params["color"] = color
    # Unsplash's search endpoint only distinguishes relevant vs latest —
    # there's no true "popular" order, so that request maps to the default.
    if sort == "new":
        params["order_by"] = "latest"
    r = requests.get("https://api.unsplash.com/search/photos",
                     headers={"Authorization": f"Client-ID {key}"},
                     params=params, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for p in r.json().get("results", []):
        urls = p.get("urls") or {}
        out.append(_norm(
            "unsplash", p.get("id"),
            urls.get("thumb") or urls.get("small") or "",
            urls.get("regular") or urls.get("small") or "",
            urls.get("full") or urls.get("raw") or "",
            p.get("width"), p.get("height"),
            (p.get("user") or {}).get("name", ""),
            (p.get("links") or {}).get("html", ""),
            (p.get("links") or {}).get("download_location", "")))
    return out


PROVIDERS = {
    "pexels": search_pexels,
    "pixabay": search_pixabay,
    "unsplash": search_unsplash,
}


# ------------------------------------------------------------------ merging
def deduplicate(items: list[dict]) -> list[dict]:
    """Exact URL and per-provider id duplicates. Perceptual hashing is
    deliberately out of scope until duplicates actually become a problem."""
    seen_ids, seen_urls, out = set(), set(), []
    for it in items:
        ident = it["id"]
        url = (it.get("full") or it.get("preview") or "").split("?")[0]
        if ident in seen_ids or (url and url in seen_urls):
            continue
        seen_ids.add(ident)
        if url:
            seen_urls.add(url)
        out.append(it)
    return out


def interleave(by_provider: dict[str, list[dict]]) -> list[dict]:
    """Round-robin so the grid mixes sources instead of showing 20 Pexels
    results followed by 20 Pixabay ones."""
    out, i = [], 0
    order = [p for p in ("pexels", "unsplash", "pixabay") if by_provider.get(p)]
    while order:
        for provider in list(order):
            bucket = by_provider[provider]
            if i < len(bucket):
                out.append(bucket[i])
            else:
                order.remove(provider)
        i += 1
    return out


def _post_filter(items: list[dict], orientation: str) -> list[dict]:
    """Apply orientation locally for providers that couldn't do it server-side
    (Pixabay has no square), so the grid is consistent."""
    if not orientation or orientation == "any":
        return items
    out = []
    for it in items:
        w, h = it.get("width") or 0, it.get("height") or 0
        if not w or not h:
            out.append(it)
            continue
        ratio = w / h
        if orientation == "square" and 0.85 <= ratio <= 1.18:
            out.append(it)
        elif orientation == "landscape" and ratio > 1.18:
            out.append(it)
        elif orientation == "portrait" and ratio < 0.85:
            out.append(it)
    return out


def search(q: str, page: int = 1, per_page: int = 24, orientation: str = "any",
           color: str = "", sort: str = "relevant",
           providers: list[str] | None = None) -> dict:
    """One query, every provider, one merged result set."""
    q = (q or "").strip()
    if not q:
        return {"results": [], "errors": [], "providers": configured(), "cached": False}

    orientation = orientation if orientation in ORIENTATIONS else "any"
    sort = sort if sort in SORTS else "relevant"
    wanted = [p for p in (providers or list(PROVIDERS)) if configured().get(p)]
    if not wanted:
        return {"results": [], "errors": ["No stock photo providers are configured."],
                "providers": configured(), "cached": False}

    ck = hashlib.md5(
        f"{q}|{page}|{per_page}|{orientation}|{color}|{sort}|{','.join(sorted(wanted))}"
        .encode()).hexdigest()
    with _cache_lock:
        hit = _cache.get(ck)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return {**hit[1], "cached": True}

    per_provider = max(3, per_page // max(1, len(wanted)) + 4)
    buckets: dict[str, list[dict]] = {}
    errors: list[str] = []

    def run(name: str):
        try:
            return name, PROVIDERS[name](q, page, per_provider, orientation, color, sort), None
        except Exception as exc:                     # noqa: BLE001 — never fatal
            return name, [], f"{name}: {exc}"

    with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
        for name, items, err in pool.map(run, wanted):
            if err:
                errors.append(err)
            else:
                buckets[name] = items

    if errors and not buckets:                       # every provider failed
        return {"results": [], "errors": errors, "providers": configured(),
                "cached": False, "all_failed": True}

    merged = deduplicate(interleave(buckets))
    merged = _post_filter(merged, orientation)[:per_page]
    payload = {"results": merged, "errors": errors, "providers": configured(),
               "counts": {k: len(v) for k, v in buckets.items()}}

    with _cache_lock:
        _cache[ck] = (time.time(), payload)
        if len(_cache) > 400:                        # keep the cache bounded
            for k in sorted(_cache, key=lambda x: _cache[x][0])[:120]:
                _cache.pop(k, None)
    return {**payload, "cached": False}


def track_download(item: dict):
    """Unsplash's API terms require pinging download_location when an image is
    actually used. No-op for the other providers."""
    loc = (item or {}).get("download_location") or ""
    key = _key("UNSPLASH_ACCESS_KEY")
    if not loc or not key:
        return
    try:
        requests.get(loc, headers={"Authorization": f"Client-ID {key}"}, timeout=6)
    except Exception:                                # noqa: BLE001
        pass
