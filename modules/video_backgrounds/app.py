"""Video Backgrounds — search the owned footage library, and Coverr live.

A blueprint on the hub app rather than a dispatcher-mounted app, for the
reason in CLAUDE.md: a hub route under a mounted prefix is unreachable. The
mounted prefixes under /tools are the specific ones listed in wsgi.py
(/tools/image-picker, /tools/seo-images, /tools/io …); /tools/video-backgrounds
is not one of them, so it belongs to the hub app, exactly as
/tools/commercial-builder does.

The owned-footage half is entirely hub/video_library.py, which the Commercial
Builder's stock search also calls. This file is the page and the JSON
endpoints behind it, and nothing else — a second copy of the tag vocabulary
or the URL builder here is precisely how the Pexels key came to be fixed
twice.

Coverr is the one exception to "owned footage only," and it is not folded
into that module. hub/video_library.py's whole design is the two-folder
Cloudinary allowlist — indexed, described, re-hosted through a transformed
delivery URL — and Coverr's clips are none of that: nothing here vision-tags
them, transforms them, or persists a description, because they are searched
live on the request rather than indexed ahead of it, the same way the
Commercial Builder already searches Coverr. Merging that into
hub/video_library.py would blur the one thing that module's docstring is
emphatic about — "the library is two folder trees, not the account" — into
"the library is two folder trees, the account, and also somebody else's
account." Kept as a second shelf on this page instead, clearly labelled, the
way the Commercial Builder's picker keeps "Suggested from Video Search"
apart from "More stock options."
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from hub import coverr
from hub import video_library as vl
from hub.webargs import clamp_int

try:
    from hub import audit as _hub_audit
    _log = _hub_audit.for_module("video_backgrounds")
except Exception:                           # noqa: BLE001 - runs outside the Hub
    def _log(*_a, **_k):
        return None


bp = Blueprint("video_backgrounds", __name__,
               url_prefix="/tools/video-backgrounds",
               template_folder="templates")

# Staff only. This is a blueprint on the hub app, so wsgi.py's AuthGuard --
# which wraps dispatcher-mounted modules -- never sees it, and the hub app has
# no blanket gate of its own. Without this every page and API route here
# answered 200 to anyone with the URL. One gate on the blueprint, so the next
# route added does not have to remember; hub/blueprint_guard.py says why it is
# shared rather than written out here for the third time.
try:
    from hub.blueprint_guard import install as _install_guard
    _install_guard(bp, mount="/tools/video-backgrounds")
except Exception:                                       # noqa: BLE001
    pass                                                # standalone, no Hub


def _actor() -> str:
    from flask import session
    return str(session.get("user") or session.get("email") or "")[:60]


@bp.get("/")
def page():
    return render_template(
        "video_backgrounds.html",
        status=vl.status(),
        vocab=vl.VOCAB,
        flags=vl.FLAGS,
    )


def _coverr_note(query: str, live: bool, has_results: bool) -> str:
    """Why the Coverr shelf looks the way it does.

    Not-live is checked before has_results, and deliberately so even though
    the route below never calls coverr.search() while unconfigured (so
    has_results is already False whenever live is): the Commercial Builder's
    coverr_service degrades to labeled mock results rather than an empty list
    when there is no key, precisely so a producer can keep building a spot
    without one, and that is exactly the wrong thing to show on a tool whose
    entire job is "copy a URL to real footage" — a placehold.co card with no
    working preview_url would look like a broken clip rather than a missing
    key. So this page never asks Coverr at all while it is unconfigured
    (see api_search() below), and the ordering here is what keeps that true
    even if that changes later.
    """
    if not query:
        return ("Type a search above — Coverr has no tag chips to search by, "
                "only free text.")
    if not live:
        return "COVERR_API is not set, so this shelf shows placeholders."
    if not has_results:
        return "No Coverr matches for this search."
    return ""


@bp.get("/api/search")
def api_search():
    limit = clamp_int(request.args.get("limit"), vl.DEFAULT_RESULTS,
                      1, vl.MAX_RESULTS)
    max_duration = request.args.get("max_duration") or None
    tags = [t for t in (request.args.get("tags") or "").split(",") if t.strip()]
    query = request.args.get("q") or ""
    result = vl.search(
        query,
        tags=tags,
        orientation=request.args.get("orientation") or "",
        max_duration=max_duration,
        limit=limit,
    )
    # A second, unindexed shelf: Coverr is searched live rather than folded
    # into the owned-library result set above, so a Coverr clip can never be
    # mistaken for something we hold in Cloudinary. Only spent on a real
    # free-text query -- Coverr has no tag vocabulary of its own to search a
    # chip-only request against, and the owned library's chip search must not
    # cost a Coverr request that can only come back empty. And never spent at
    # all with no key set: coverr.search() degrades to labeled mock
    # placeholders, which is right on the Commercial Builder (a producer can
    # keep building without a key) and wrong here, where the entire point of
    # a result is a working URL to copy -- a placehold.co card is not that,
    # and _coverr_note() already says the key is missing without one.
    q = query.strip()
    live = coverr.is_live()
    coverr_results = coverr.search(q, per_page=limit,
                                   orientation=request.args.get("orientation") or None) if (q and live) else []
    result["coverr"] = {
        "results": coverr_results,
        "live": live,
        "note": _coverr_note(q, live, bool(coverr_results)),
    }
    return jsonify(result)


@bp.get("/api/status")
def api_status():
    return jsonify(vl.status())


@bp.post("/api/index")
def api_index():
    """Index whatever is waiting, and stamp the cutoff on the first run.

    POST because it writes: it stamps the cutoff, spends vision calls and
    modifies tags on live assets. A GET here would be indexed by a crawler and
    fired by a browser prefetch.
    """
    body = request.get_json(silent=True) or {}
    limit = clamp_int(body.get("limit"), vl.MAX_INDEX_BATCH,
                      1, vl.MAX_INDEX_BATCH)
    result = vl.index_new(limit=limit, actor=_actor())
    _log("video_indexed", tool="video_backgrounds",
         indexed=result.get("indexed"), skipped=result.get("skipped"),
         failed=result.get("failed"))
    return jsonify(result)


@bp.get("/api/pending")
def api_pending():
    """What would be indexed, without indexing it.

    Worth having separately: the index call costs a vision request per clip,
    and being able to see the list first is the difference between running it
    and wondering what it is about to do.
    """
    return jsonify({"cutoff": vl.cutoff(), "pending": vl.pending()})


def register_video_backgrounds(app):
    app.register_blueprint(bp)
    return app
