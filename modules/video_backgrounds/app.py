"""Video Backgrounds — search the owned footage library.

A blueprint on the hub app rather than a dispatcher-mounted app, for the
reason in CLAUDE.md: a hub route under a mounted prefix is unreachable. The
mounted prefixes under /tools are the specific ones listed in wsgi.py
(/tools/image-picker, /tools/seo-images, /tools/io …); /tools/video-backgrounds
is not one of them, so it belongs to the hub app, exactly as
/tools/commercial-builder does.

All the real work is in hub/video_library.py, which the Commercial Builder's
stock search also calls. This file is the page and the JSON endpoints behind
it, and nothing else — a second copy of the tag vocabulary or the URL builder
here is precisely how the Pexels key came to be fixed twice.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

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


@bp.get("/api/search")
def api_search():
    limit = clamp_int(request.args.get("limit"), vl.DEFAULT_RESULTS,
                      1, vl.MAX_RESULTS)
    max_duration = request.args.get("max_duration") or None
    tags = [t for t in (request.args.get("tags") or "").split(",") if t.strip()]
    result = vl.search(
        request.args.get("q") or "",
        tags=tags,
        orientation=request.args.get("orientation") or "",
        max_duration=max_duration,
        limit=limit,
    )
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
