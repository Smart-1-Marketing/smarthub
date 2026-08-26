"""Stock photo search — the three free providers, and our own Cloudinary library.

One search box over four sources: **Pexels**, **Pixabay**, **Unsplash** and
**library**, the last being the folders this agency has already filled with
photography it owns or has cleared. They are searched together, normalised to
one shape and interleaved, so a rep looking for "roofing crew" sees what we
already have beside what is free to take.

This is the *shared* implementation. `modules/image_picker/providers.py` used
to carry its own copy of the three adapters and is now a thin delegation to
this file, because that copy was already the second one -- Image Creator has a
third in `modules/image_creator/photo_search.py` -- and this codebase has paid
twice for a rule ("cap the longest edge", the Pexels key spelling) that had to
be found and fixed in several places separately.

Four things worth knowing before editing:

**Keys are read through `hub/config.py` at call time, never `os.environ` at
import.** That is the provider-key trap in `CLAUDE.md`: this deployment sets
`PEXELS_API` and `PIXABAY_API`, much of the code was written against
`..._API_KEY`, and a module reading one spelling directly degrades to an empty
grid with every screen looking healthy. `/api/integrity`'s `provider_key_drift`
check is high severity for exactly this.

**The library is a different kind of source from the other three, and the
difference is stated rather than smoothed over.** Pexels, Pixabay and Unsplash
answer about the whole internet; the library answers about two named folders in
one Cloudinary account. So a library result carries the folder it came from and
is labelled *ours*, because "we already own this" is the whole reason to look
there first -- it needs no attribution, no licence note and no download ping.

**A folder that does not exist and a folder with nothing in it are different
answers, and Cloudinary's search cannot tell them apart** -- both come back as
zero results. That is the `CLAUDE.md` rule about absent data reading as "not
measured" rather than zero, and here it is not hypothetical: at the time this
was written **neither** configured folder existed in the `smart1labs` account,
so a tool that trusted the search would have reported "no photos match" for
ever, about a library nobody had created yet. `folder_state()` asks the folders
API instead, and the page says *this folder is not in Cloudinary yet* rather
than drawing a confident empty grid.

**Unsplash's API terms require a download ping when a photo is actually used.**
`trigger_unsplash_download()` does it, and it is called on *use*, not on
browse. It is the condition of the API key -- do not remove it.

Licence, per source:
  - Pexels / Pixabay: free for commercial use, attribution appreciated but not
    required. We keep it anyway, because a photo whose origin nobody recorded
    is one nobody can defend later.
  - Unsplash: free for commercial use, download ping required (above).
  - library: ours. No third-party terms attach.
"""

from __future__ import annotations

import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from hub.config import settings

log = logging.getLogger(__name__)

HTTP_TIMEOUT = float(os.environ.get("STOCK_SEARCH_TIMEOUT", "12"))
CACHE_TTL = int(os.environ.get("STOCK_SEARCH_CACHE_TTL", "900"))  # 15 min

# The two folders this agency keeps its own photography in, including every
# subfolder beneath them. Overridable so a third folder does not need a deploy,
# and so a second deployment with different folder names is a variable rather
# than a fork.
DEFAULT_LIBRARY_FOLDERS = ("General Stock Photos", "Smart 1 Ads")

SOURCES = ("library", "pexels", "pixabay", "unsplash")

# Which sources need a licence line on screen. The library does not: it is ours.
THIRD_PARTY = ("pexels", "pixabay", "unsplash")


def library_folders() -> list[str]:
    """The Cloudinary folders the library source searches, top-level names.

    Subfolders are included by the search itself, so only the roots are listed
    here -- naming every subfolder would go stale the day somebody adds one.
    """
    raw = (os.environ.get("STOCK_LIBRARY_FOLDERS") or "").strip()
    if not raw:
        return list(DEFAULT_LIBRARY_FOLDERS)
    out = [f.strip().strip("/") for f in raw.split(",")]
    return [f for f in out if f]


# --------------------------------------------------------------------------- #
# Cache. Stock results for "AC repair" do not change minute to minute, and
# every cached hit is a provider rate-limit call we did not spend.
# --------------------------------------------------------------------------- #

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_MAX = 400


def _cache_get(key: str) -> Any | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.time() - ts > CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return val


def _cache_put(key: str, val: Any) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[: _CACHE_MAX // 4]:
            _CACHE.pop(k, None)
    _CACHE[key] = (time.time(), val)


def clear_cache() -> None:
    _CACHE.clear()


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #

def _key(source: str) -> str:
    """Read a provider key through config, which accepts every spelling in use."""
    return {
        "pexels": lambda: settings.pexels_key,
        "pixabay": lambda: settings.pixabay_key,
        "unsplash": lambda: settings.unsplash_key,
    }.get(source, lambda: "")() or ""


def configured_sources() -> dict[str, bool]:
    """Which sources can be searched at all. Not whether they will answer."""
    return {
        "library": _library_ready(),
        "pexels": bool(_key("pexels")),
        "pixabay": bool(_key("pixabay")),
        "unsplash": bool(_key("unsplash")),
    }


def any_source_configured() -> bool:
    return any(configured_sources().values())


# Kept for the Image Picker, which asks about the three third-party providers
# only and has no concept of our own library.
def configured_providers() -> dict[str, bool]:
    on = configured_sources()
    return {name: on[name] for name in THIRD_PARTY}


def any_provider_configured() -> bool:
    return any(configured_providers().values())


# --------------------------------------------------------------------------- #
# One shape, whatever answered
# --------------------------------------------------------------------------- #

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
    folder: str = "",
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
        # Only ever set on a library result. It is what makes "we already own
        # this one" visible on the card rather than inferable from the badge.
        "folder": folder,
        "ours": provider == "library",
    }


_ORIENTATION = {
    "pexels": {"landscape": "landscape", "portrait": "portrait", "square": "square"},
    "pixabay": {"landscape": "horizontal", "portrait": "vertical", "square": "all"},
    "unsplash": {"landscape": "landscape", "portrait": "portrait", "square": "squarish"},
}


def _orientation_of(width: int, height: int) -> str:
    """Used for the library, which is filtered here rather than server-side."""
    if not width or not height:
        return ""
    ratio = width / float(height)
    if ratio > 1.15:
        return "landscape"
    if ratio < 0.87:
        return "portrait"
    return "square"


# --------------------------------------------------------------------------- #
# Third-party adapters. Each returns the normalised shape or raises.
# --------------------------------------------------------------------------- #

def search_pexels(query: str, *, per_page: int, page: int,
                  orientation: str | None) -> list[dict[str, Any]]:
    key = _key("pexels")
    if not key:
        return []
    params: dict[str, Any] = {"query": query, "per_page": per_page, "page": page}
    o = _ORIENTATION["pexels"].get(orientation or "")
    if o:
        params["orientation"] = o
    r = requests.get("https://api.pexels.com/v1/search", params=params,
                     headers={"Authorization": key}, timeout=HTTP_TIMEOUT)
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


def search_pixabay(query: str, *, per_page: int, page: int,
                   orientation: str | None) -> list[dict[str, Any]]:
    key = _key("pixabay")
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


def search_unsplash(query: str, *, per_page: int, page: int,
                    orientation: str | None) -> list[dict[str, Any]]:
    key = _key("unsplash")
    if not key:
        return []
    params: dict[str, Any] = {"query": query, "per_page": per_page, "page": page}
    o = _ORIENTATION["unsplash"].get(orientation or "")
    if o:
        params["orientation"] = o
    r = requests.get(
        "https://api.unsplash.com/search/photos", params=params,
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
            tags=" ".join((t or {}).get("title", "") for t in (p.get("tags") or []))
                 or (p.get("alt_description") or ""),
            download_location=links.get("download_location", ""),
        ))
    return out


def trigger_unsplash_download(download_location: str) -> bool:
    """Required by the Unsplash API terms when a photo is actually used.

    Called on use, not on browse. Never raises -- a failed ping must not cost
    somebody their image.
    """
    key = _key("unsplash")
    if not key or not download_location:
        return False
    try:
        r = requests.get(download_location,
                         headers={"Authorization": f"Client-ID {key}"},
                         timeout=HTTP_TIMEOUT)
        return r.ok
    except Exception as exc:                                # noqa: BLE001
        log.warning("unsplash download ping failed: %s", exc)
        return False


# --------------------------------------------------------------------------- #
# The library -- our own Cloudinary folders
# --------------------------------------------------------------------------- #

try:                                                        # pragma: no cover
    import cloudinary
    import cloudinary.api
    import cloudinary.search
    import cloudinary.utils
except Exception:                                           # noqa: BLE001
    cloudinary = None                                       # type: ignore


_cl_configured = False


def _library_ready() -> bool:
    return bool(cloudinary) and settings.cloudinary_ready


def _configure() -> None:
    global _cl_configured
    if _cl_configured or not _library_ready():
        return
    cloudinary.config(secure=True)          # reads CLOUDINARY_URL
    _cl_configured = True


def _note_call(op: str, detail: str = "") -> None:
    """Count one Cloudinary Admin/Search call against the credit estimate.

    Filed under this module rather than the calling tool, because several
    screens search the same two folders and the folders are what a Cloudinary
    console listing shows. Never raises -- an uninstrumented call site is worse
    than a missing feature here, but a metering failure must not cost a search.
    """
    try:
        from hub import quotas as _q
        _q.record_asset(module="stock_search", kind=op, nbytes=0, detail=detail[:120])
    except Exception:                                       # noqa: BLE001
        pass


# Cloudinary's expression language treats these as syntax. A folder called
# "Smart 1 Ads" is fine quoted; a query term carrying one of them is not.
_RESERVED = re.compile(r'[!(){}\[\]^~?:\\=&<>|"+\-]')


def _quoted(value: str) -> str:
    return '"' + str(value or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _folder_clause(folders: list[str]) -> str:
    """Match each folder AND everything beneath it.

    Two terms per folder on purpose: `folder:"X/*"` matches the subfolders and
    NOT the folder itself, so a photo sitting directly in "General Stock Photos"
    would be invisible -- which is where most of them will sit.
    """
    parts = []
    for f in folders:
        parts.append(f"folder={_quoted(f)}")
        parts.append(f"folder:{_quoted(f + '/*')}")
    return "(" + " OR ".join(parts) + ")" if parts else ""


def _query_clause(query: str) -> str:
    terms = [_RESERVED.sub(" ", t).strip() for t in str(query or "").split()]
    terms = [t for t in terms if len(t) > 1][:6]
    if not terms:
        return ""
    return "(" + " AND ".join(_quoted(t) for t in terms) + ")"


def folder_state(folders: list[str] | None = None) -> dict[str, str]:
    """Per folder: "ok", "missing" or "error". Never a bare boolean.

    This exists because Cloudinary's *search* cannot answer it -- a folder that
    was never created and a folder with nothing in it both return zero results,
    and reporting the first as "no photos match your search" is the confident
    wrong answer this codebase keeps having to undo. The folders API does
    distinguish them: a missing path raises NotFound, an existing one with no
    children returns an empty list.
    """
    names = folders if folders is not None else library_folders()
    if not _library_ready():
        return {f: "error" for f in names}
    cache_key = "folders|" + ",".join(names)
    hit = _cache_get(cache_key)
    if hit is not None:
        return dict(hit)
    _configure()
    out: dict[str, str] = {}
    for f in names:
        try:
            cloudinary.api.subfolders(f)
            _note_call("folders", f)
            out[f] = "ok"
        except Exception as exc:                            # noqa: BLE001
            name = type(exc).__name__
            # NotFound is the answer we asked for, not a failure to look.
            if "NotFound" in name or "not found" in str(exc).lower():
                out[f] = "missing"
            else:
                out[f] = "error"
                log.warning("folder check failed for %r: %s", f, exc)
    _cache_put(cache_key, dict(out))
    return out


def _derive(url: str, transform: str) -> str:
    """A derived delivery URL. A gallery must never request the full asset."""
    u = str(url or "")
    if "/upload/" not in u:
        return u
    return u.replace("/upload/", f"/upload/{transform}/", 1)


def search_library(query: str, *, per_page: int, page: int,
                   orientation: str | None) -> list[dict[str, Any]]:
    """Search our own folders, including everything beneath them."""
    if not _library_ready():
        return []
    folders = library_folders()
    clause = _folder_clause(folders)
    if not clause:
        return []
    _configure()
    parts = [clause, "resource_type:image"]
    q = _query_clause(query)
    if q:
        parts.append(q)
    expression = " AND ".join(parts)

    search = (cloudinary.search.Search()
              .expression(expression)
              .with_field("context")
              .with_field("tags")
              .sort_by("created_at", "desc")
              .max_results(min(100, max(per_page * 3, per_page))))
    res = search.execute()
    _note_call("search", expression[:120])

    out = []
    for r in res.get("resources", []) or []:
        url = r.get("secure_url") or ""
        ctx = (r.get("context") or {}).get("custom") or (r.get("context") or {})
        tags = " ".join(r.get("tags") or [])
        pid = r.get("public_id") or r.get("asset_id") or ""
        alt = (ctx.get("alt") or ctx.get("caption") or "").strip()
        width, height = r.get("width", 0), r.get("height", 0)
        if orientation and _orientation_of(width, height) != orientation:
            continue
        out.append(_norm(
            provider="library",
            pid=pid,
            thumb=_derive(url, "c_limit,w_400,q_auto,f_auto"),
            preview=_derive(url, "c_limit,w_1200,q_auto,f_auto"),
            full=url,
            width=width,
            height=height,
            # Ours, so there is no photographer to credit and nothing that
            # would read as one. The folder is the useful provenance here.
            author="Smart 1",
            author_url="",
            source_url=url,
            alt=alt or str(pid).replace("_", " ").replace("-", " "),
            tags=f"{tags} {pid}".strip(),
            folder=r.get("asset_folder") or "",
        ))
    # Paged here rather than in the API call: the orientation filter above is
    # local, so asking Cloudinary for one page would hand back short pages.
    start = max(0, (int(page or 1) - 1) * per_page)
    return out[start:start + per_page]


_ADAPTERS = {
    "library": search_library,
    "pexels": search_pexels,
    "pixabay": search_pixabay,
    "unsplash": search_unsplash,
}


# --------------------------------------------------------------------------- #
# Fan-out
# --------------------------------------------------------------------------- #

def _filter_negatives(items: list[dict[str, Any]],
                      negatives: list[str]) -> list[dict[str, Any]]:
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
    """Round-robin so no one source or phrasing owns the top row."""
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
    sources: list[str] | None = None,
    per_page: int = 12,
    page: int = 1,
    orientation: str | None = None,
    negatives: list[str] | None = None,
    limit: int = 36,
    seed: int | None = None,
) -> dict[str, Any]:
    """Search every requested source for every query, in parallel.

    Returns `{"results": [...], "sources": {name: ok|error|off}, "folders":
    {...}, "cached": bool}`. Never raises on a source failure: one provider
    refusing must not cost the other three, and an all-source failure comes
    back as data rather than as an exception -- but it comes back **named**,
    because "nothing matched" and "we could not look" are different answers and
    only the first means change your search.
    """
    queries = [q.strip() for q in (queries or []) if q and q.strip()][:4]
    on = configured_sources()
    wanted = [s for s in (sources or list(SOURCES)) if s in _ADAPTERS]
    active = [s for s in wanted if on.get(s)]

    # The library is browsable with no query at all -- it is a finite shelf and
    # "show me what we have" is a real question. The three web providers are
    # not: an empty query there is a request for the whole internet.
    if not queries:
        active = [s for s in active if s == "library"]
        queries = [""]
        if not active:
            return {"results": [], "sources": {s: ("off" if not on.get(s) else "idle")
                                               for s in SOURCES},
                    "folders": {}, "cached": False}

    if not active:
        return {"results": [], "sources": {s: "off" for s in wanted},
                "folders": folder_state() if "library" in wanted else {},
                "cached": False}

    cache_key = "|".join([
        ",".join(sorted(queries)), str(per_page), str(page),
        orientation or "-", ",".join(sorted(active)),
    ])
    cached = _cache_get(cache_key)
    if cached is not None:
        results = _filter_negatives(cached, negatives or [])
        return {"results": results[:limit],
                "sources": {s: ("ok" if s in active else
                                ("off" if not on.get(s) else "idle")) for s in SOURCES},
                "folders": folder_state() if "library" in active else {},
                "cached": True}

    status: dict[str, str] = {s: ("off" if not on.get(s) else "idle") for s in SOURCES}
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}

    jobs = [(q, name) for q in queries for name in active]
    with ThreadPoolExecutor(max_workers=min(8, len(jobs) or 1)) as pool:
        futures = {
            pool.submit(_ADAPTERS[name], q, per_page=per_page, page=page,
                        orientation=orientation): (q, name)
            for q, name in jobs
        }
        for fut in as_completed(futures):
            q, name = futures[fut]
            try:
                buckets[(q, name)] = fut.result() or []
                if status.get(name) != "error":
                    status[name] = "ok"
            except Exception as exc:                        # noqa: BLE001
                status[name] = "error"
                buckets[(q, name)] = []
                log.warning("source %s failed for %r: %s", name, q, exc)

    ordered = [(q, name) for q in queries for name in active]
    merged = _interleave([buckets.get(k, []) for k in ordered])

    # Dedupe on id and on the delivery URL -- the same photo genuinely does come
    # back under several phrasings.
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

    # Ours first. A photo we already own costs nothing, needs no licence note
    # and is already the client's brand -- so it is worth more than a better
    # match from a stock library, and burying it under 30 free ones is how the
    # library goes unused. Stable within each half.
    deduped.sort(key=lambda it: 0 if it.get("provider") == "library" else 1)

    if seed is not None and len(deduped) > 6:
        import random
        head = [it for it in deduped[:limit] if it.get("provider") != "library"]
        lib = [it for it in deduped[:limit] if it.get("provider") == "library"]
        random.Random(seed).shuffle(head)
        deduped = lib + head + deduped[limit:]

    _cache_put(cache_key, deduped)
    filtered = _filter_negatives(deduped, negatives or [])
    return {"results": filtered[:limit], "sources": status,
            "folders": folder_state() if "library" in active else {},
            "cached": False}
