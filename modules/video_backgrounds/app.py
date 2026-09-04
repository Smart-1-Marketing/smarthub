"""Video Backgrounds — search the owned footage library, Coverr, Pexels and
Pixabay live, and save any of the four into a client's gallery.

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

Coverr, Pexels and Pixabay are the exception to "owned footage only," and none
of the three is folded into hub/video_library.py. That module's whole design
is the two-folder Cloudinary allowlist — indexed, described, re-hosted through
a transformed delivery URL — and a live web result is none of that: nothing
here vision-tags it, transforms it, or persists a description ahead of time,
because it is searched live on the request rather than indexed ahead of it,
the same way the Commercial Builder already searches all three. Merging that
into hub/video_library.py would blur the one thing that module's docstring is
emphatic about — "the library is two folder trees, not the account" — into
"the library is two folder trees, the account, and also somebody else's
account." Each is kept as its own shelf on this page instead, clearly
labelled, the way the Commercial Builder's picker keeps "Suggested from Video
Search" apart from "More stock options."

Pexels and Pixabay both search **video**, and the two adapters are Commercial
Builder's own (`modules/commercial_builder/services/pexels_service.py` and
`pixabay_service.py`) — imported directly rather than copied, the same rule
`hub/coverr.py`'s own docstring states: the Pexels key had to be fixed twice
before that rule existed. Unsplash is deliberately not a fourth shelf here:
its API is photographs only and has never published a video endpoint, so a
shelf for it could only ever be empty or mocked, and this page's whole
argument (see the Coverr note below) is that a result here has to be a
working URL to real footage.

**Saving to a client's gallery** goes through `modules/image_picker/filing.py`
— the one filing path every other tool in this Hub uses — under
`kind="video_search"`, so it lands on the client's record under its own
"Video Searches" heading rather than mixed into `stock`. Owned footage is
filed where it already sits in Cloudinary; a Coverr, Pexels or Pixabay clip is
on somebody else's CDN and is stored through `hub/storage.put_remote()` first,
the same reason `modules/stock_photos/app.py` copies a chosen photo rather
than linking it — a provider that reorganises its CDN must not empty a
client's gallery with nothing saying why. The folder a rep types or picks is
the `collection_label`/`collection_key` on the row, so "name a folder or add
to an existing one" is answered by `/api/gallery/folders`, which lists the
folder names already used for that client under this kind.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from hub import coverr
from hub import storage as hub_storage
from hub import video_library as vl
from hub.webargs import clamp_int
from modules.commercial_builder.services import pexels_service, pixabay_service

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


def _live_shelf_note(name: str, env_hint: str, query: str, live: bool,
                     has_results: bool) -> str:
    """Why a live, unindexed shelf (Coverr, Pexels, Pixabay) looks the way it
    does. Shared by the three note functions below rather than copied three
    times, which is exactly the drift that had the Pexels key spelling fixed
    twice before hub/coverr.py's docstring named the rule.

    Not-live is checked before has_results, and deliberately so even though
    the route below never calls a provider's search() while unconfigured (so
    has_results is already False whenever live is False): the Commercial
    Builder's provider services degrade to labeled mock results rather than an
    empty list when there is no key, precisely so a producer can keep building
    a spot without one, and that is exactly the wrong thing to show on a tool
    whose entire job is "copy a URL to real footage" — a placehold.co card
    with no working preview_url would look like a broken clip rather than a
    missing key. So this page never asks a provider at all while it is
    unconfigured (see api_search() below), and the ordering here is what
    keeps that true even if that changes later.
    """
    if not query:
        return (f"Type a search above — {name} has no tag chips to search by, "
                "only free text.")
    if not live:
        return f"{env_hint} is not set, so this shelf shows placeholders."
    if not has_results:
        return f"No {name} matches for this search."
    return ""


def _coverr_note(query: str, live: bool, has_results: bool) -> str:
    """Why the Coverr shelf looks the way it does. See _live_shelf_note()."""
    return _live_shelf_note("Coverr", "COVERR_API", query, live, has_results)


def _pexels_note(query: str, live: bool, has_results: bool) -> str:
    """Why the Pexels shelf looks the way it does. See _live_shelf_note()."""
    return _live_shelf_note("Pexels", "PEXELS_API", query, live, has_results)


def _pixabay_note(query: str, live: bool, has_results: bool) -> str:
    """Why the Pixabay shelf looks the way it does. See _live_shelf_note()."""
    return _live_shelf_note("Pixabay", "PIXABAY_API", query, live, has_results)


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
    orientation = request.args.get("orientation") or None

    live = coverr.is_live()
    coverr_results = coverr.search(q, per_page=limit,
                                   orientation=orientation) if (q and live) else []
    result["coverr"] = {
        "results": coverr_results,
        "live": live,
        "note": _coverr_note(q, live, bool(coverr_results)),
    }

    # Two more live, unindexed shelves -- same reasoning as Coverr above, same
    # guard against ever calling a provider's mock fallback. Pixabay's search
    # ignores orientation (its docstring says so); passed anyway so a caller
    # never has to know which of the three providers honours it.
    pexels_live = pexels_service.is_live()
    pexels_results = pexels_service.search(
        q, per_page=limit, orientation=orientation) if (q and pexels_live) else []
    result["pexels"] = {
        "results": pexels_results,
        "live": pexels_live,
        "note": _pexels_note(q, pexels_live, bool(pexels_results)),
    }

    pixabay_live = pixabay_service.is_live()
    pixabay_results = pixabay_service.search(
        q, per_page=limit, orientation=orientation) if (q and pixabay_live) else []
    result["pixabay"] = {
        "results": pixabay_results,
        "live": pixabay_live,
        "note": _pixabay_note(q, pixabay_live, bool(pixabay_results)),
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


# --------------------------------------------------------------------------- #
# Saving a result to a client's gallery
#
# A searchable list of real clients, never a typed name resolved on faith --
# the client_key rule this codebase states at length: a typo'd name files the
# clip under a client nothing joins to and still reads as success. This is the
# same shape modules/bg_remover/app.py already uses.
# --------------------------------------------------------------------------- #

@bp.get("/api/clients")
def api_clients():
    try:
        from hub import clients_registry
    except Exception as exc:                           # noqa: BLE001
        return jsonify({"clients": [], "error": str(exc)})
    rows = clients_registry.search_clients(request.args.get("q", ""), limit=10)
    return jsonify({"clients": [{"name": r["name"], "domain": r.get("domain", "")}
                                for r in rows]})


@bp.get("/api/gallery/folders")
def api_gallery_folders():
    """Folder names already used under "Video Searches" for one client.

    So the save panel can offer "add to an existing one" as well as "name a
    new folder" -- without this every save would be a guess at what the last
    one was called. An unknown or blank client answers with an empty list
    rather than an error: there is nothing to offer yet, which is the ordinary
    case for a client's first saved clip.
    """
    client_name = (request.args.get("client") or "").strip()
    if not client_name:
        return jsonify({"ok": True, "folders": []})
    try:
        from sqlalchemy import select

        from modules.image_picker.filing import gallery_for_name
        from modules.image_picker.models import SavedImage, session
    except Exception as exc:                           # noqa: BLE001
        return jsonify({"ok": False, "folders": [], "error": str(exc)})

    db = session()
    client = gallery_for_name(db, client_name, create=False)
    if client is None:
        return jsonify({"ok": True, "folders": []})
    rows = db.execute(
        select(SavedImage.collection_label)
        .where(SavedImage.client_id == client.id,
              SavedImage.collection_kind == "video_search",
              SavedImage.collection_label.isnot(None))
        .distinct()
        .order_by(SavedImage.collection_label)
    ).scalars().all()
    return jsonify({"ok": True, "folders": [r for r in rows if r]})


def _save_clip_to_gallery(*, client_name: str, folder: str, provider: str,
                          item: dict, actor: str) -> dict:
    """File one Video Search result into a client's gallery.

    Owned Cloudinary footage is filed where it already sits -- nothing is
    re-uploaded. A Coverr, Pexels or Pixabay clip lives on somebody else's CDN,
    so it is stored through hub/storage.put_remote() first: a gallery row
    pointing at a URL we do not control empties itself the day that provider
    reorganises its CDN, with nothing on screen saying why -- the same
    argument modules/stock_photos.py's _file_for_client() makes for a chosen
    photo. Never raises: every caller here is finishing a search that already
    succeeded, and the answer is a dict with `ok` either way.
    """
    client_name = (client_name or "").strip()
    if not client_name:
        return {"ok": False, "error": "No client chosen, so this clip was "
                                      "not saved."}
    if not isinstance(item, dict):
        return {"ok": False, "error": "No clip was sent to save."}

    provider = str(provider or item.get("provider") or "").strip().lower()
    folder = (folder or "").strip()[:200] or "Unsorted"
    folder_key = hub_storage.slug(folder, "unsorted")

    owned = provider in ("cloudinary", "video_library")
    if owned:
        public_id = str(item.get("public_id") or "")
        url = (item.get("full_url") or item.get("background_url")
              or item.get("preview_url") or "")
        if not public_id or not url:
            return {"ok": False, "error": "That clip has no stored file to save."}
        filename = public_id.rsplit("/", 1)[-1] or "clip"
        file_provider = "video_library"
    else:
        source = item.get("full_url") or item.get("preview_url") or ""
        if not source:
            return {"ok": False, "error": "That clip has no address to save."}
        clip_id = str(item.get("id") or item.get("provider_image_id") or "clip")
        try:
            asset = hub_storage.put_remote(
                "video_search", source, client=client_name,
                filename=f"{provider or 'clip'}-{clip_id}.mp4")
        except Exception as exc:                       # noqa: BLE001
            return {"ok": False, "error": f"The clip could not be stored: {exc}"}
        public_id, url = asset.public_id, asset.url
        filename = f"{provider or 'clip'}-{clip_id}.mp4"
        file_provider = provider or "video_library"

    try:
        from modules.image_picker.filing import file_asset
    except Exception as exc:                           # noqa: BLE001
        return {"ok": False, "error": f"The client galleries are unavailable: {exc}"}

    author = str(item.get("author") or "").strip()
    alt = (f"Video found in Video Search for {client_name}"
          + (f" ({author})" if author and not owned else ""))
    out = file_asset(
        client_name=client_name, public_id=public_id, url=url,
        kind="video_search", label=folder, key=folder_key,
        filename=filename, alt=alt, resource_type="video",
        width=item.get("width"), height=item.get("height"),
        provider=file_provider, saved_by=actor or "system",
        tool="video_backgrounds",
    )
    return out if isinstance(out, dict) else {"ok": False}


@bp.post("/api/gallery/save")
def api_gallery_save():
    """Save one search result -- owned or from a live provider -- to a
    client's gallery, in a named folder under Video Searches.

    POST because it writes: it can upload a copy of a provider clip into this
    Hub's own Cloudinary account and always writes a gallery row.
    """
    body = request.get_json(silent=True) or {}
    out = _save_clip_to_gallery(
        client_name=str(body.get("client") or ""),
        folder=str(body.get("folder") or ""),
        provider=str(body.get("provider") or ""),
        item=body.get("item") if isinstance(body.get("item"), dict) else {},
        actor=_actor(),
    )
    if out.get("ok"):
        image = out.get("image") or {}
        _log("video_saved_to_gallery", tool="video_backgrounds",
             client=str(body.get("client") or "")[:200] or None,
             provider=image.get("provider") or body.get("provider"),
             folder=str(body.get("folder") or "")[:200] or None)
    return jsonify(out)


def register_video_backgrounds(app):
    app.register_blueprint(bp)
    return app
