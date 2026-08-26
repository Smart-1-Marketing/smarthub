"""Smart 1 Hub — Stock Photo Search.

One search box over four sources: **Pexels**, **Pixabay**, **Unsplash** and our
own **library** — the Cloudinary folders this agency has already filled
("General Stock Photos", "Smart 1 Ads"), including everything beneath them.

The point of putting our own folders beside the three free libraries is that a
photo we already own costs nothing, carries no third-party licence and is often
already the client's own brand — and it was reachable only by opening the
Cloudinary console and scrolling, which is done by nobody. So library results
sort first and are badged *ours*, and the licence line appears only on the three
that need one.

The search itself is `hub/stock_search.py`, shared with the Image Picker rather
than copied — see the note at the top of that file, and the opportunistic
migration rule in CLAUDE.md. This module is the screen.

Three things it is careful about:

* **A folder that does not exist is not a folder with nothing in it.**
  Cloudinary's search returns zero for both, so the page asks the folders API
  as well and says *not in Cloudinary yet* rather than drawing a confident
  empty grid. At the time of writing neither configured folder existed in the
  account, which is exactly the case that would otherwise read as "no photos
  match" for ever.
* **A source that refused is named.** "Nothing matched" and "we could not look"
  are different answers and only the first means change your search.
* **Using a photo is recorded, and Unsplash is pinged.** The Unsplash API terms
  require a download ping when a photo is actually used rather than browsed;
  `/api/use` is where that happens, and it is why the copy and download buttons
  go through a route instead of linking straight out.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, render_template, request

from hub import stock_search

try:
    from hub import audit as hub_audit
except Exception:                                       # noqa: BLE001
    hub_audit = None

try:
    from hub import storage as hub_storage
except Exception:                                       # noqa: BLE001
    hub_storage = None

BASE_DIR = Path(__file__).parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))

ORIENTATIONS = ("", "landscape", "portrait", "square")
PER_PAGE = 24
MAX_LIMIT = 48


def _actor() -> str:
    """Whoever is signed in, for the activity log. Never fails a request."""
    for key in ("X-Hub-User", "X-Hub-Actor"):
        val = (request.headers.get(key) or "").strip()
        if val:
            return val[:60]
    try:
        from flask import session
        return str(session.get("user") or session.get("email") or "")[:60]
    except Exception:                                   # noqa: BLE001
        return ""


def _boot() -> dict:
    """What the page needs before anybody types anything.

    The folder state is included here rather than fetched after the first
    search, so a library that has not been created yet says so on arrival
    instead of looking like a search that found nothing.
    """
    configured = stock_search.configured_sources()
    return {
        "sources": [
            {"key": "library", "label": "Our library", "on": configured["library"],
             "ours": True,
             "note": "Photography we already own — no licence, no attribution."},
            {"key": "pexels", "label": "Pexels", "on": configured["pexels"],
             "ours": False, "note": "Free for commercial use."},
            {"key": "pixabay", "label": "Pixabay", "on": configured["pixabay"],
             "ours": False, "note": "Free for commercial use."},
            {"key": "unsplash", "label": "Unsplash", "on": configured["unsplash"],
             "ours": False, "note": "Free for commercial use."},
        ],
        "folders": _folders(),
        "orientations": list(ORIENTATIONS),
        "per_page": PER_PAGE,
    }


def _folders() -> list[dict]:
    """Per configured folder: does it exist, and can we say so.

    `folder_state()` answers "ok" / "missing" / "error" and never a bare
    boolean, because those are three different situations and only one of them
    means the library is genuinely empty.
    """
    names = stock_search.library_folders()
    if not stock_search.configured_sources()["library"]:
        return [{"name": n, "state": "error",
                 "detail": "Cloudinary is not configured on this deployment."}
                for n in names]
    state = stock_search.folder_state(names)
    detail = {
        "ok": "",
        "missing": "Not in Cloudinary yet — nothing to search until it exists.",
        "error": "We could not check this folder.",
    }
    return [{"name": n, "state": state.get(n, "error"),
             "detail": detail.get(state.get(n, "error"), "")} for n in names]


@app.route("/")
def index():
    return render_template("stock_photos.html", boot=_boot())


@app.route("/api/folders")
def api_folders():
    return jsonify({"ok": True, "folders": _folders()})


@app.route("/api/search")
def api_search():
    query = (request.args.get("q") or "").strip()
    orientation = (request.args.get("orientation") or "").strip().lower()
    if orientation not in ORIENTATIONS:
        orientation = ""
    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1

    wanted = [s for s in (request.args.get("sources") or "").split(",") if s.strip()]
    sources = [s.strip() for s in wanted if s.strip() in stock_search.SOURCES] \
        or list(stock_search.SOURCES)

    # An empty query is a real request for our own shelf and a request for the
    # whole internet from the other three. The shared search enforces that;
    # this only avoids sending a pointless job.
    if not query and "library" not in sources:
        return jsonify({"ok": True, "results": [], "sources": {}, "folders": [],
                        "cached": False,
                        "message": "Type something to search Pexels, Pixabay "
                                   "and Unsplash — or include our library to "
                                   "browse what we already have."})

    found = stock_search.search(
        [query] if query else [],
        sources=sources,
        per_page=PER_PAGE,
        page=page,
        limit=MAX_LIMIT,
        orientation=orientation or None,
    )
    return jsonify({
        "ok": True,
        "results": found.get("results", []),
        "sources": found.get("sources", {}),
        "folders": _folders() if "library" in sources else [],
        "cached": bool(found.get("cached")),
        "query": query,
        "page": page,
    })


@app.route("/api/use", methods=["POST"])
def api_use():
    """A photo was actually taken, rather than looked at.

    Two things happen here and both are the reason this is a route rather than
    a plain link. Unsplash's API terms require a download ping at the moment of
    use — not on browse — and it is the condition of the key. And the pick is
    written to the activity log, so a client's 360 record shows the creative
    work done for them.
    """
    body = request.get_json(silent=True) or {}
    provider = str(body.get("provider") or "").strip()
    image_id = str(body.get("id") or "")[:120]
    url = str(body.get("url") or "")[:600]
    client = str(body.get("client") or "").strip()[:120]

    pinged = False
    if provider == "unsplash":
        pinged = stock_search.trigger_unsplash_download(
            str(body.get("download_location") or ""))

    # A library asset downloads under its own name; a provider URL is served by
    # somebody else's CDN and cannot be rewritten, so it is returned unchanged
    # rather than into something that 404s.
    download = url
    if provider == "library" and hub_storage is not None:
        try:
            download = hub_storage.attachment_url(url, image_id.split(":", 1)[-1])
        except Exception:                               # noqa: BLE001
            download = url

    if hub_audit is not None:
        try:
            # First positional is the module. Passing module= in the extras
            # raises TypeError and silently zeroes cost tracking — CLAUDE.md.
            hub_audit.log("stock_photos", "photo_used", actor=_actor() or None,
                          client=client or None, tool="stock_photos",
                          provider=provider, image=image_id)
        except Exception:                               # noqa: BLE001
            pass

    return jsonify({"ok": True, "download": download, "unsplash_pinged": pinged})
