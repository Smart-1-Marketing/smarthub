"""Universal Stock Video Search (spec section 6) — fans out to Pexels +
Pixabay simultaneously, merges results, labels FREE vs PREMIUM instead of
naming the provider."""

from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, request

from ..services import openai_service, pexels_service, pixabay_service

bp = Blueprint("cb_stock", __name__, url_prefix="/api/stock")

_ORIENTATION_MAP = {"16:9": "landscape", "9:16": "portrait", "1:1": "square"}


@bp.get("/search")
def search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"ok": False, "error": "q is required"}), 400

    per_provider = int(request.args.get("per_provider", 8))
    output_format = request.args.get("format")
    orientation = _ORIENTATION_MAP.get(output_format)
    use_ai_queries = request.args.get("expand", "false").lower() == "true"

    queries = openai_service.expand_stock_queries(query) if use_ai_queries else [query]

    with ThreadPoolExecutor(max_workers=4) as pool:
        pexels_futures = [pool.submit(pexels_service.search, q, per_provider, orientation) for q in queries]
        pixabay_futures = [pool.submit(pixabay_service.search, q, per_provider, orientation) for q in queries]
        pexels_results = [r for f in pexels_futures for r in f.result()]
        pixabay_results = [r for f in pixabay_futures for r in f.result()]

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

    return jsonify({
        "ok": True, "query": query, "queries_used": queries, "results": merged,
        "providers": {"pexels": pexels_service.is_live(), "pixabay": pixabay_service.is_live()},
    })
