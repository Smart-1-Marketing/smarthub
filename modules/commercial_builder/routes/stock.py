"""Universal Stock Video Search (spec section 6) — fans out to the owned
library, Pexels, Pixabay and Coverr simultaneously, merges results, and
labels OWNED / FREE / PREMIUM instead of naming the provider."""

import re
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest

from flask import Blueprint, jsonify, request

from hub.webargs import clamp_int

from ..config import ASSET_SOURCE_PRIORITY
from ..services import coverr_service, openai_service, pexels_service, pixabay_service

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
_WORD = re.compile(r"[a-z0-9]+")
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "in", "into", "is", "it", "of", "on", "or", "our", "that", "the",
    "their", "this", "to", "with",
}


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

    with ThreadPoolExecutor(max_workers=6) as pool:
        # The scene description is expanded because full shot prose is a poor
        # search query. The free providers already received those short terms;
        # the owned library must receive them too or a search can claim it
        # checked our footage while asking it a much stricter question.
        owned_future = pool.submit(_owned_queries, queries, orientation,
                                   per_provider, query)
        pexels_futures = [pool.submit(pexels_service.search, q, per_provider, orientation) for q in queries]
        pixabay_futures = [pool.submit(pixabay_service.search, q, per_provider, orientation) for q in queries]
        coverr_futures = [pool.submit(coverr_service.search, q, per_provider, orientation) for q in queries]
        pexels_results = [r for f in pexels_futures for r in f.result()]
        pixabay_results = [r for f in pixabay_futures for r in f.result()]
        coverr_results = [r for f in coverr_futures for r in f.result()]
        owned_results = owned_future.result()

    # Interleave so results don't read as "all Pexels then all Pixabay then
    # all Coverr". zip_longest rather than the two-list zip() this used to be
    # written as: with three providers a fixed pairwise interleave plus a
    # "leftover from the longer list" tail is two things to get right for
    # every provider added after the first two, and it is the shape that
    # would need rewriting again the next time a provider joins. zip_longest
    # already interleaves any number of lists of any lengths in one pass.
    merged, seen = [], set()
    for row in zip_longest(pexels_results, pixabay_results, coverr_results):
        for item in row:
            if item and item["id"] not in seen:
                merged.append(item)
                seen.add(item["id"])

    # Owned footage goes in front of everything, not interleaved with it.
    # ASSET_SOURCE_PRIORITY already says client_asset before free_stock before
    # premium_stock — a clip we hold costs nothing, needs no licence check and
    # is the one a producer should reach for first. Ranking it below a Pexels
    # result would contradict the waterfall the rest of the module follows.
    owned_ids = {o["id"] for o in owned_results}
    stock_results = [m for m in merged if m["id"] not in owned_ids]
    merged = owned_results + stock_results

    return jsonify({
        "ok": True, "query": query, "queries_used": queries, "results": merged,
        # Keep `results` for existing callers, but name the two shelves so the
        # Commercial Builder cannot accidentally mix or demote footage from
        # the Video Search tool when its picker changes again.
        "video_search_results": owned_results,
        "stock_results": stock_results,
        "source_order": ["video_search", "stock"],
        "priority": ASSET_SOURCE_PRIORITY,
        "providers": {"owned": _owned_live(),
                      "video_search": _owned_live(),
                      "pexels": pexels_service.is_live(),
                      "pixabay": pixabay_service.is_live(),
                      "coverr": coverr_service.is_live()},
        "owned_note": _owned_note(),
        "video_search_note": _owned_note(),
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
    return _owned_queries([query], orientation, per_provider, query)


def _owned_queries(queries, orientation, limit, scene_query=""):
    """Search and relevance-rank the same library as the Video Search tool.

    OpenAI turns a scene description into up to three concrete searches for
    Pexels and Pixabay. Searching Cloudinary only with the original prose made
    every word an AND clause, so the owned shelf frequently returned nothing
    even when one of the short queries matched it. Search every short query,
    de-duplicate the union, then rank it against the scene and those searches.
    Stopping when the first query fills the shelf can hide a much more relevant
    clip found by the second query merely because the first results are newer.
    """
    if not _owned_live():
        return []
    cap = max(1, int(limit or 1))
    query_list = [str(q or "").strip() for q in (queries or [])
                  if str(q or "").strip()]
    merged, seen = [], set()
    for query in query_list:
        try:
            found = video_library.search(
                # Ask every query for the full cap. The final ranking chooses
                # the best union, and duplicates are removed below.
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
    return _rank_owned(merged, scene_query, query_list)[:cap]


def _rank_owned(items, scene_query, queries):
    """Put the most relevant Video Search suggestions first.

    Cloudinary returns newest-first. That is useful as a tie-breaker, but it
    should not let a newer generic clip outrank footage whose indexed tags or
    description closely match this scene.
    """
    indexed = list(enumerate(items or []))
    return [item for _, item in sorted(
        indexed,
        key=lambda row: (-_owned_relevance(row[1], scene_query, queries),
                         row[0]),
    )]


def _owned_relevance(item, scene_query, queries):
    description = _normalise(item.get("description"))
    raw_tags = item.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    tags = _normalise(" ".join(str(tag) for tag in raw_tags))
    identity = _normalise(" ".join(str(item.get(k) or "") for k in
                                   ("public_id", "folder", "filename")))
    all_text = " ".join((description, tags, identity))
    description_terms = set(description.split())
    tag_terms = set(tags.split())
    identity_terms = set(identity.split())
    score = 1 if item.get("bg_ready") else 0

    # The producer's scene description is the strongest signal. Indexed tags
    # are deliberately concise, so a matching tag is worth more than prose.
    scene_terms = _terms(scene_query)
    score += 8 * len(scene_terms & tag_terms)
    score += 5 * len(scene_terms & description_terms)
    score += 2 * len(scene_terms & identity_terms)

    # Expanded Video Search phrases are concrete alternatives. Reward phrase
    # matches and individual words, with a small preference for earlier AI
    # suggestions while still allowing a clearly better later match to win.
    for position, query in enumerate(queries or []):
        phrase = _normalise(query)
        terms = _terms(query)
        preference = max(1, 3 - position)
        if phrase and phrase in all_text:
            score += 10 + preference
        score += preference * len(terms & tag_terms)
        score += len(terms & description_terms)
    return score


def _normalise(value):
    return " ".join(_WORD.findall(str(value or "").lower()))


def _terms(value):
    return {word for word in _WORD.findall(str(value or "").lower())
            if len(word) > 2 and word not in _STOP_WORDS}
