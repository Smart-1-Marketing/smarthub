"""Universal Stock Video Search (spec section 6) — fans out to the owned
library, Pexels and Pixabay simultaneously, merges results, and labels
OWNED / FREE / PREMIUM instead of naming the provider."""

from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, request

from hub.webargs import clamp_int

from ..config import ASSET_SOURCE_PRIORITY
from ..services import openai_service, pexels_service, pixabay_service

# Footage we already own, searched through the same shared service the
# /tools/video-backgrounds page uses. Imported defensively because this module
# has to keep working when the Hub is not around it — routes/stock.py runs in
# the standalone mode db.py supports.
try:
    from hub import video_library
except Exception:                            # noqa: BLE001
    video_library = None

bp = Blueprint("cb_stock", __name__, url_prefix="/api/stock")

_ORIENTATION_MAP = {"16:9": "landscape", "9:16": "portrait", "1:1": "square"}


@bp.get("/search")
def search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"ok": False, "error": "q is required"}), 400

    # Straight to two billed providers, once per expanded query, so an
    # unbounded caller number here is a fan-out somebody else invoices us for
    # -- and `int()` outside a try made ?per_provider=abc a 500. The three
    # faults hub/webargs.py was written to end, all present on one line.
    per_provider = clamp_int(request.args.get("per_provider"), 8, 1, 50)
    output_format = request.args.get("format")
    orientation = _ORIENTATION_MAP.get(output_format)
    use_ai_queries = request.args.get("expand", "false").lower() == "true"

    queries = openai_service.expand_stock_queries(query) if use_ai_queries else [query]

    with ThreadPoolExecutor(max_workers=4) as pool:
        # The scene description is expanded because full shot prose is a poor
        # search query. The free providers already received those short terms;
        # the owned library must receive them too or a search can claim it
        # checked our footage while asking it a much stricter question.
        owned_future = pool.submit(_owned_queries, queries, orientation,
                                   per_provider)
        pexels_futures = [pool.submit(pexels_service.search, q, per_provider, orientation) for q in queries]
        pixabay_futures = [pool.submit(pixabay_service.search, q, per_provider, orientation) for q in queries]
        pexels_results = [r for f in pexels_futures for r in f.result()]
        pixabay_results = [r for f in pixabay_futures for r in f.result()]
        owned_results = owned_future.result()

    # interleave so results don't read as "all Pexels then all Pixabay"
    merged, seen = [], set()
    for a, b in zip(pexels_results, pixabay_results):
        for item in (a, b):
            if item["id"] not in seen:
                merged.append(item)
                seen.add(item["id"])
    # append any leftovers from the longer list
    longer = pexels_results if len(pexels_results) > len(pixabay_results) else pixabay_results
    for item in longer[len(merged) // 2:]:
        if item["id"] not in seen:
            merged.append(item)
            seen.add(item["id"])

    # Owned footage goes in front of everything, not interleaved with it.
    # ASSET_SOURCE_PRIORITY already says client_asset before free_stock before
    # premium_stock — a clip we hold costs nothing, needs no licence check and
    # is the one a producer should reach for first. Ranking it below a Pexels
    # result would contradict the waterfall the rest of the module follows.
    merged = owned_results + [m for m in merged if m["id"] not in
                              {o["id"] for o in owned_results}]

    return jsonify({
        "ok": True, "query": query, "queries_used": queries, "results": merged,
        "priority": ASSET_SOURCE_PRIORITY,
        "providers": {"owned": _owned_live(),
                      "pexels": pexels_service.is_live(),
                      "pixabay": pixabay_service.is_live()},
        "owned_note": _owned_note(),
    })


def _owned_live():
    return bool(video_library and video_library.ready())


def _owned_note():
    """Why the owned library returned nothing, when it returns nothing.

    Three different causes -- no Hub, Cloudinary unset, indexing not started --
    and an empty list looks identical for all three. A producer who reads
    "0 owned results" as "we own nothing relevant" goes and licenses a clip we
    may already have.
    """
    if not video_library:
        return "The owned library is unavailable outside the Hub."
    if not video_library.ready():
        return "CLOUDINARY_URL is not set, so the owned library was not searched."
    if not video_library.cutoff():
        return ("Indexing has not started, so no owned footage is searchable "
                "yet. Existing clips are deliberately out of scope.")
    return ""


def _owned(query, orientation, per_provider):
    """Owned footage, in the same shape as the stock providers.

    hub.video_library._shape already emits this module's universal asset shape
    (id/provider/tier/thumbnail/preview_url/full_url/width/height/duration/
    author/source_url), which is why there is no translation here. Never
    raises: the owned library going down must not take the stock search with
    it -- a producer with two thirds of the results can still work.
    """
    return _owned_queries([query], orientation, per_provider)


def _owned_queries(queries, orientation, limit):
    """Search our library with each short query until the result cap is full.

    OpenAI turns a scene description into up to three concrete searches for
    Pexels and Pixabay. Searching Cloudinary only with the original prose made
    every word an AND clause, so the owned shelf frequently returned nothing
    even when one of the short queries matched it. Keep one overall cap and
    de-duplicate clips that answer more than one query.
    """
    if not _owned_live():
        return []
    cap = max(1, int(limit or 1))
    merged, seen = [], set()
    for query in queries or []:
        query = str(query or "").strip()
        if not query:
            continue
        try:
            found = video_library.search(
                # Ask each query for the full cap: an earlier clip may match
                # again, and asking only for the remaining slot can return
                # that duplicate while hiding the next new clip.
                query, orientation=orientation or "", limit=cap)
        except Exception:                    # noqa: BLE001
            continue
        for item in found.get("results") or []:
            key = (item.get("id") or item.get("preview_url") or
                   item.get("full_url") or item.get("thumbnail"))
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(item)
            if len(merged) >= cap:
                return merged
    return merged
