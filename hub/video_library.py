"""Searchable video backgrounds, indexed forward from the day this went live.

The Cloudinary account holds ~81 videos and **not one of them carries a tag**.
The only search handles are the folder and the filename, and for the footage
library that is actually useful as backgrounds — the `video files` folder — the
filenames are supplier catalogue numbers (`hd0961.mov`, `4k0367.mp4`). Twenty
five of the thirty tell you nothing about what is on screen, so "find me a
drone shot over a neighbourhood" has no answer that does not involve opening
clips one at a time. Cloudinary's own semantic search is not enabled on this
account either: a visual-search call for "abstract motion background loop"
returns zero results, as does one for a subject that certainly exists.

So this module does three things:

1. **Indexes a video when it arrives** — three keyframes to the vision model,
   which returns a description and a set of tags drawn from a fixed
   vocabulary, written back onto the asset as real Cloudinary tags and context.
2. **Searches what has been indexed**, over those tags rather than filenames.
3. **Builds a delivery URL that is actually usable as a background** — the
   masters are 50-235 MB ProRes-ish `.mov` files, so linking one into a page is
   not an option. Every result carries a transformed URL instead.

Three decisions worth keeping
-----------------------------

**Indexing is forward-only, and the cutoff is recorded rather than assumed.**
The existing library is deliberately out of scope: `cutoff()` is stamped the
first time an index runs and nothing created at or before it is ever touched.
That is a product decision, not a technical limit — which is why it is one
stored timestamp and one comparison, so "actually, do the back catalogue too"
is a change to `INDEX_BACKLOG`, not a rewrite. Assets skipped for this reason
are reported as `skipped_predates_cutoff`, never as failures, because a silent
skip and a silent error look identical in a counter.

**The tag vocabulary is closed.** A vision model asked for free-form keywords
returns `car`, `cars`, `automobile` and `vehicle` across four clips, and then
no single search finds all four. Terms outside `VOCAB` are dropped and counted
in `dropped_terms` so the drift is visible in the response rather than
absorbed. When the same unknown term keeps showing up, that is the signal to
add it to the vocabulary on purpose.

**A search result never points at the master.** `background_url()` strips the
audio, trims to a loop length, caps the edge and lets Cloudinary pick the
format, which is the difference between a 155 MB `.mov` and something a page
can actually load. The master stays where it is and is never modified.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote

from hub import jsonstore
from hub.config import settings

try:                                        # pragma: no cover - optional dep
    import cloudinary
    import cloudinary.api
    from cloudinary.search import Search
except Exception:                           # noqa: BLE001
    cloudinary = None
    Search = None


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
# Grouped so the UI can offer them as three filter rows rather than one flat
# list of sixty checkboxes, and so a term can be validated against the group
# the model claimed to be answering.
VOCAB: dict[str, tuple[str, ...]] = {
    "subject": (
        "aerial", "city", "suburb", "office", "people", "crowd", "home",
        "interior", "nature", "water", "sky", "road", "traffic", "industrial",
        "construction", "retail", "food", "medical", "automotive", "sports",
        "technology", "screens", "abstract", "texture", "animals", "weather",
        "farm", "energy", "money", "travel",
    ),
    "motion": (
        "static", "slow-pan", "push-in", "pull-out", "handheld", "drone",
        "tracking", "timelapse", "orbit", "rack-focus",
    ),
    "look": (
        "bright", "dark", "warm", "cool", "moody", "sunny", "overcast",
        "night", "golden-hour", "high-contrast", "desaturated", "colourful",
    ),
}

# Facts about the clip rather than descriptions of it. Kept apart from VOCAB
# because these gate whether a clip can sit behind headline text, and a filter
# on "no burned-in text" is a different question from "show me city footage".
FLAGS: tuple[str, ...] = (
    "has-people",      # anyone recognisable on screen
    "has-faces",       # a face large enough to read, which limits reuse
    "has-text",        # burned-in words: unusable behind a headline
    "loopable",        # starts and ends close enough to cut back on itself
    "bg-ready",        # holds a steady look with room for an overlay
)

# Marker tags. INDEX_TAG is what search filters on, so an asset that was
# indexed before the schema changed is still findable; SCHEMA_TAG says which
# pass wrote it, so a re-index can target only the stale ones.
INDEX_TAG = "s1-indexed"
SCHEMA_TAG = "s1-index-v1"

# Context keys. Cloudinary exposes these to the Search API as
# `context.<key>`, which is what makes the free-text half of search work.
CTX_DESC = "s1_desc"
CTX_INDEXED_AT = "s1_indexed_at"

# Flip to True to let the back catalogue be indexed too. Deliberately a
# constant and not an env var: turning it on re-reads and re-tags a hundred-odd
# assets and costs a vision call each, which is a decision someone should make
# in a diff rather than in a dashboard field at 2am.
INDEX_BACKLOG = False

_STATE_FILE = "state.json"

# Clamps. An unbounded max_results here is an unbounded Cloudinary bill and an
# unbounded page, and /api/integrity has a check that looks for exactly that.
MAX_RESULTS = 100
DEFAULT_RESULTS = 24
MAX_INDEX_BATCH = 25
KEYFRAMES = 3


class VideoLibraryError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

def ready() -> bool:
    """Cloudinary is usable. Search and delivery both need it."""
    return bool(cloudinary) and settings.cloudinary_ready


def can_index() -> bool:
    """Indexing additionally needs the vision model."""
    return ready() and settings.openai_ready


def _configure() -> None:
    if ready():
        cloudinary.config(secure=True)      # reads CLOUDINARY_URL


# ---------------------------------------------------------------------------
# The cutoff
# ---------------------------------------------------------------------------

def _state_path() -> str:
    return os.path.join(jsonstore.data_dir("video_library"), _STATE_FILE)


def _read_state() -> dict:
    got = jsonstore.read_json(_state_path(), default={})
    return got if isinstance(got, dict) else {}


def cutoff() -> str:
    """When indexing started, or "" if it has not.

    Reading never establishes it. A page view is not a decision, and a cutoff
    quietly stamped by whoever happened to open the tool first is a cutoff
    nobody chose.
    """
    return str(_read_state().get("cutoff") or "")


def begin(actor: str = "") -> str:
    """Stamp the cutoff if it is not already set, and return it."""
    state = _read_state()
    existing = str(state.get("cutoff") or "")
    if existing:
        return existing
    stamp = _iso_z(datetime.now(timezone.utc))
    state["cutoff"] = stamp
    state["began_by"] = (actor or "")[:60]
    jsonstore.write_json(_state_path(), state)
    return stamp


def _iso_z(when: datetime) -> str:
    """UTC as `...Z`, which is the only offset form the search API accepts.

    `datetime.isoformat()` renders UTC as `+00:00`, and Cloudinary's expression
    parser rejects that: `created_at>"2026-08-24T00:00:00+00:00"` is a query
    error, while the same instant written `...Z` parses. The failure surfaces
    as an exception from the search call rather than as bad results, so it is
    survivable — but it would have made every pending() lookup return nothing
    and the tool would have reported "nothing to index" forever.
    """
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _after_cutoff(created_at: str, mark: str) -> bool:
    """Was this asset created after indexing started?

    Compared as datetimes, not as strings. The two sides genuinely differ in
    format — Cloudinary returns `2026-06-03T21:05:26+00:00` while the cutoff is
    stored as `2026-08-24T09:00:00Z` for the reason in `_iso_z` — and a string
    compare of those only happens to work while the dates differ before the
    offset. An unreadable or missing date sorts as "not after", which keeps an
    asset out of the index rather than guessing it in.
    """
    if INDEX_BACKLOG:
        return True
    made, since = _parse_iso(created_at), _parse_iso(mark)
    if made is None or since is None:
        return False
    return made > since


# ---------------------------------------------------------------------------
# Delivery URLs
# ---------------------------------------------------------------------------

def _base(kind: str = "video") -> str:
    cloud = settings.cloudinary_cloud_name
    if not cloud:
        return ""
    return f"https://res.cloudinary.com/{cloud}/{kind}/upload"


def _pid(public_id: str) -> str:
    # public_ids in this account contain spaces and emoji; the stored
    # secure_url shows them percent-encoded. Slashes are folder separators and
    # must survive.
    return quote(str(public_id or ""), safe="/")


def background_url(public_id: str, *, width: int = 1920, height: int = 1080,
                   duration: float | int | None = 8, start: float | int = 0,
                   fmt: str = "auto") -> str:
    """A URL that can actually be a page background.

    Audio is dropped (`ac_none`) because a background that makes noise is a
    bug, and because it is most of the file on a talking-head clip. The edge is
    capped and the format left to Cloudinary (`f_auto:video` serves webm to
    browsers that take it, mp4 to the rest). Duration trims to a loop length —
    the masters run 7-29 seconds and a hero background wants the first few.

    Nothing here modifies the stored asset; it is a derived delivery URL.
    """
    base = _base("video")
    if not base or not public_id:
        return ""
    chain = []
    if duration:
        chain.append(f"so_{_num(start)},du_{_num(duration)}")
    size = []
    if width:
        size.append(f"w_{int(width)}")
    if height:
        size.append(f"h_{int(height)}")
    if size:
        size.append("c_fill")
        size.append("g_auto")
    fetch = "f_auto:video" if fmt == "auto" else f"f_{fmt}"
    chain.append(",".join([fetch, "q_auto", *size, "ac_none"]))
    ext = "mp4" if fmt == "auto" else fmt
    return f"{base}/{'/'.join(chain)}/{_pid(public_id)}.{ext}"


def poster_url(public_id: str, *, second: float | int = 1, width: int = 640) -> str:
    """A still from the clip — the gallery thumbnail, and the page's poster
    frame so something is on screen before the video has buffered."""
    base = _base("video")
    if not base or not public_id:
        return ""
    return (f"{base}/so_{_num(second)}/"
            f"w_{int(width)},c_fill,q_auto,f_jpg/{_pid(public_id)}.jpg")


def keyframe_urls(public_id: str, duration: float | int | None,
                  count: int = KEYFRAMES) -> list[str]:
    """Evenly spaced stills, for the vision model to read.

    Sampled inside the clip rather than at 0 and at the end: the first frame of
    a stock clip is often a fade from black, which describes nothing.
    """
    dur = float(duration or 0)
    count = max(1, min(int(count), 6))
    if dur <= 0:
        return [poster_url(public_id, second=1, width=640)]
    step = dur / (count + 1)
    return [poster_url(public_id, second=round(step * (i + 1), 2), width=640)
            for i in range(count)]


def _num(value) -> str:
    """Cloudinary accepts 8 and 8.5 but not 8.0 in a duration; normalise."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "0"
    return str(int(f)) if f == int(f) else str(round(f, 2))


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

_PROMPT = """You are cataloguing a stock video clip so it can be found later by
someone looking for a background to put behind headline text.

You are shown {n} frames sampled evenly through one clip. Describe the clip,
not the individual frames.

Reply with JSON only, no prose, in exactly this shape:

{{"description": "<one plain sentence, max 20 words, what is on screen>",
  "subject": ["<1-3 terms>"],
  "motion": ["<exactly 1 term>"],
  "look": ["<1-2 terms>"],
  "flags": ["<0-5 terms>"]}}

Choose every term from these lists and use no others:

subject: {subject}
motion: {motion}
look: {look}
flags: {flags}

Flag meanings — these decide whether the clip is usable, so be strict:
  has-people  someone is on screen
  has-faces   a face is large enough to recognise
  has-text    words are burned into the footage
  loopable    the last frame could cut back to the first without a jump
  bg-ready    the look holds steady and there is room for an overlay; do NOT
              set this if has-text is set, or if the subject sits dead centre
              where a headline would go
"""


def _describe(public_id: str, duration) -> dict:
    """Ask the vision model what is in the clip. Raises on an unusable reply."""
    from hub import ai

    frames = keyframe_urls(public_id, duration)
    prompt = _PROMPT.format(
        n=len(frames),
        subject=", ".join(VOCAB["subject"]),
        motion=", ".join(VOCAB["motion"]),
        look=", ".join(VOCAB["look"]),
        flags=", ".join(FLAGS),
    )
    raw = ai.vision(prompt, frames, module="video_library",
                    purpose="index_clip")
    return _parse(raw)


def _parse(raw: str) -> dict:
    """Pull the JSON object out of a model reply.

    Models fence JSON in ``` about a third of the time even when told not to,
    and a bare json.loads on that raises for a reason that has nothing to do
    with the content being wrong.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.I)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise VideoLibraryError("The vision model did not return JSON.")
    try:
        got = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise VideoLibraryError(f"Unreadable JSON from the vision model: {exc}") from exc
    if not isinstance(got, dict):
        raise VideoLibraryError("The vision model returned JSON that is not an object.")
    return got


def validate(payload: dict) -> tuple[list[str], str, list[str]]:
    """Reduce a model reply to (tags, description, dropped_terms).

    Everything outside the vocabulary is dropped and returned, not silently
    discarded — an unknown term appearing repeatedly is the argument for adding
    it, and that argument only exists if someone can see it happening.
    """
    tags: list[str] = []
    dropped: list[str] = []

    def take(group: str, allowed: tuple[str, ...], limit: int) -> None:
        raw = payload.get(group) or []
        if isinstance(raw, str):
            raw = [raw]
        kept = 0
        for term in raw:
            term = str(term).strip().lower()
            if not term:
                continue
            if term in allowed:
                if term not in tags and kept < limit:
                    tags.append(term)
                    kept += 1
            else:
                dropped.append(f"{group}:{term}")

    take("subject", VOCAB["subject"], 3)
    take("motion", VOCAB["motion"], 1)
    take("look", VOCAB["look"], 2)
    take("flags", FLAGS, len(FLAGS))

    desc = str(payload.get("description") or "").strip()[:300]

    # A clip carrying burned-in text cannot sit behind a headline, whatever the
    # model said. Two of the three prompts that produced bg-ready alongside
    # has-text also described the text in the sentence, so the model is seeing
    # it and weighing it wrong rather than missing it. Cheaper to enforce here
    # than to keep rewording the prompt.
    if "has-text" in tags and "bg-ready" in tags:
        tags.remove("bg-ready")
        dropped.append("flags:bg-ready(overridden by has-text)")

    return tags, desc, dropped


def index_asset(public_id: str, *, force: bool = False) -> dict:
    """Describe and tag one video. Returns a result dict, never raises.

    A failure here must not take down the batch above it, so every outcome is
    a status string with a reason attached. `skipped_predates_cutoff` is not a
    failure and is counted separately.
    """
    result = {"public_id": public_id, "status": "error", "reason": "",
              "tags": [], "description": "", "dropped_terms": []}
    if not can_index():
        result["reason"] = ("Indexing needs both CLOUDINARY_URL and "
                            "OPENAI_API_KEY; one of them is not set.")
        return result

    _configure()
    try:
        info = cloudinary.api.resource(public_id, resource_type="video",
                                       context=True, tags=True)
    except Exception as exc:                # noqa: BLE001
        result["reason"] = f"Could not read the asset: {exc}"
        return result

    mark = cutoff()
    if not _after_cutoff(info.get("created_at", ""), mark):
        result["status"] = "skipped_predates_cutoff"
        result["reason"] = (f"Created {info.get('created_at') or 'unknown'}, at "
                            f"or before the {mark or 'unset'} cutoff. Indexing "
                            f"covers new footage only.")
        return result

    if INDEX_TAG in (info.get("tags") or []) and not force:
        result["status"] = "skipped_already_indexed"
        result["reason"] = "Already indexed. Pass force to re-describe it."
        return result

    try:
        payload = _describe(public_id, info.get("duration"))
    except Exception as exc:                # noqa: BLE001
        result["reason"] = f"Description failed: {exc}"
        return result

    tags, desc, dropped = validate(payload)
    if not tags and not desc:
        result["reason"] = "The vision model returned nothing usable."
        return result

    all_tags = sorted(set(tags) | {INDEX_TAG, SCHEMA_TAG})
    context = {
        CTX_DESC: desc,
        CTX_INDEXED_AT: _iso_z(datetime.now(timezone.utc)),
    }
    try:
        cloudinary.api.update(public_id, resource_type="video",
                              tags=",".join(all_tags), context=context)
    except Exception as exc:                # noqa: BLE001
        result["reason"] = f"Could not write tags back: {exc}"
        return result

    result.update(status="indexed", tags=tags, description=desc,
                  dropped_terms=dropped, reason="")
    return result


def pending_expression(mark: str) -> str:
    """The expression pending() searches on. Separate so it can be asserted.

    Clause order here is load-bearing, and not for the reason you would guess.
    A leading bare NOT is a parse error, so the resource_type clause comes
    first -- that much is documented. What is not documented: a *comparison*
    clause placed after a negated one is also a parse error. Both of these were
    run against this account --

        resource_type:video AND created_at>"..." AND -tags:s1-indexed  -> 23
        resource_type:video AND -tags:s1-indexed AND created_at>"..."  -> error

    Same terms, same meaning, and only one of them parses. So the date goes in
    the middle and the negation goes last. Getting it wrong throws inside
    pending(), which returns [] on any exception, so the tool would report
    "nothing to index" forever with nothing looking broken.
    """
    expr = "resource_type:video"
    if mark and not INDEX_BACKLOG:
        expr += f' AND created_at>"{mark}"'
    expr += f" AND -tags:{INDEX_TAG}"
    return expr


def pending(limit: int = MAX_INDEX_BATCH) -> list[dict]:
    """Videos created after the cutoff that carry no index tag."""
    mark = cutoff()
    if not ready() or (not mark and not INDEX_BACKLOG):
        return []
    _configure()
    limit = max(1, min(int(limit), MAX_INDEX_BATCH))
    try:
        res = (Search().expression(pending_expression(mark))
               .sort_by("created_at", "asc")
               .max_results(limit)
               .execute())
    except Exception:                       # noqa: BLE001
        return []
    return [_shape(r) for r in (res.get("resources") or [])]


def index_new(limit: int = MAX_INDEX_BATCH, actor: str = "") -> dict:
    """Index everything waiting. Stamps the cutoff on the first ever run."""
    started = begin(actor)
    out = {"cutoff": started, "indexed": 0, "skipped": 0, "failed": 0,
           "results": []}
    if not can_index():
        out["error"] = ("Indexing needs both CLOUDINARY_URL and "
                        "OPENAI_API_KEY; one of them is not set.")
        return out
    for item in pending(limit):
        res = index_asset(item["public_id"])
        out["results"].append(res)
        if res["status"] == "indexed":
            out["indexed"] += 1
        elif res["status"].startswith("skipped"):
            out["skipped"] += 1
        else:
            out["failed"] += 1
    return out


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

_ORIENTATION = {
    # (min aspect, max aspect) — a 1920x1080 clip is 1.78, a 1080x1920 is 0.56
    "landscape": (1.2, 99.0),
    "portrait": (0.0, 0.85),
    "square": (0.85, 1.2),
}

_SAFE_TERM = re.compile(r"[^a-z0-9\- ]+")


def build_expression(query: str = "", *, tags: list[str] | None = None,
                     indexed_only: bool = True) -> str:
    """The Cloudinary search expression for a query.

    Free text is matched against the description context field, the tags and
    the filename together, so "drone" finds a clip tagged `drone` and one whose
    description says "drone shot over a suburb". Terms are stripped to
    `[a-z0-9- ]` first: the expression language treats `:`, `[`, `{`, `^` and
    friends as syntax, and an unquoted one turns a search into a parse error
    rather than an empty result, which reads as the tool being broken.
    """
    clauses = ["resource_type:video"]
    if indexed_only:
        clauses.append(f"tags:{INDEX_TAG}")

    for tag in (tags or []):
        clean = _SAFE_TERM.sub("", str(tag).strip().lower())
        if clean:
            clauses.append(f"tags:{clean}")

    words = [w for w in _SAFE_TERM.sub(" ", (query or "").lower()).split() if w]
    for word in words[:6]:
        clauses.append(f'(tags:{word} OR context.{CTX_DESC}:{word} '
                       f'OR filename:{word})')
    return " AND ".join(clauses)


def search(query: str = "", *, tags: list[str] | None = None,
           orientation: str = "", max_duration: float | int | None = None,
           limit: int = DEFAULT_RESULTS, indexed_only: bool = True) -> dict:
    """Find indexed background footage.

    Orientation and duration are applied here rather than in the expression on
    purpose: aspect ratio in the expression language is an exact match on a
    string like "16:9", so a 1998x1080 clip is not 16:9 and silently vanishes.
    Filtering on the returned width and height gets the clip a person would
    have picked.
    """
    limit = max(1, min(int(limit or DEFAULT_RESULTS), MAX_RESULTS))
    out = {"ok": False, "query": query, "results": [], "total": 0,
           "cutoff": cutoff(), "indexed_only": indexed_only, "note": ""}

    if not ready():
        out["note"] = ("Cloudinary is not configured, so the owned library "
                       "cannot be searched. This is not an empty library.")
        return out
    if indexed_only and not cutoff():
        out["ok"] = True
        out["note"] = ("Indexing has not started yet, so nothing is "
                       "searchable. Existing footage is deliberately out of "
                       "scope — only clips uploaded after indexing begins are "
                       "indexed.")
        return out

    _configure()
    # Over-fetch, because orientation and duration are filtered below rather
    # than by Cloudinary. Bounded so a wide query cannot pull the whole library.
    fetch = min(MAX_RESULTS, limit * 3)
    try:
        res = (Search().expression(build_expression(query, tags=tags,
                                                    indexed_only=indexed_only))
               .with_field("context").with_field("tags")
               .sort_by("created_at", "desc")
               .max_results(fetch)
               .execute())
    except Exception as exc:                # noqa: BLE001
        out["note"] = f"The search failed: {exc}"
        return out

    items = [_shape(r) for r in (res.get("resources") or [])]
    items = [i for i in items if _matches(i, orientation, max_duration)]
    out.update(ok=True, results=items[:limit], total=len(items))
    if not items:
        out["note"] = ("Nothing indexed matches that yet. Indexing covers "
                       f"clips uploaded after {out['cutoff'] or 'it starts'}.")
    return out


def _matches(item: dict, orientation: str, max_duration) -> bool:
    if orientation:
        lo, hi = _ORIENTATION.get(orientation, (0.0, 99.0))
        ratio = item.get("aspect") or 0
        if not (lo <= ratio <= hi):
            return False
    if max_duration:
        try:
            if float(item.get("duration") or 0) > float(max_duration):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _shape(resource: dict) -> dict:
    """One Cloudinary resource as the shape every caller here expects.

    Deliberately close to the Commercial Builder's universal asset shape (see
    modules/commercial_builder/routes/stock.py) so owned footage can be merged
    into the same result list as Pexels and Pixabay without a translation step
    at that call site.
    """
    pid = resource.get("public_id") or ""
    width = int(resource.get("width") or 0)
    height = int(resource.get("height") or 0)
    duration = resource.get("duration")
    ctx = resource.get("context") or {}
    # The Search API returns context either flat or nested under "custom",
    # depending on how it was written. Both shapes are in this account.
    if isinstance(ctx, dict) and isinstance(ctx.get("custom"), dict):
        ctx = ctx["custom"]
    tags = [t for t in (resource.get("tags") or [])
            if t not in (INDEX_TAG, SCHEMA_TAG)]
    return {
        "id": f"cloudinary_{resource.get('asset_id') or pid}",
        "provider": "cloudinary",
        "tier": "OWNED",
        "public_id": pid,
        "folder": resource.get("asset_folder") or "",
        "thumbnail": poster_url(pid, second=1, width=480),
        "preview_url": background_url(pid, width=1280, height=720, duration=6),
        "full_url": background_url(pid, width=1920, height=1080, duration=None),
        "background_url": background_url(pid),
        "poster_url": poster_url(pid, width=1920),
        "width": width, "height": height,
        "aspect": round(width / height, 3) if width and height else 0,
        "duration": duration,
        "bytes": resource.get("bytes"),
        "description": str(ctx.get(CTX_DESC) or ""),
        "indexed_at": str(ctx.get(CTX_INDEXED_AT) or ""),
        "tags": tags,
        "bg_ready": "bg-ready" in (resource.get("tags") or []),
        "author": "Smart 1 library",
        "source_url": "",
    }


# ---------------------------------------------------------------------------
# Status, for the tool page and /api/integrity
# ---------------------------------------------------------------------------

def status() -> dict:
    """What is and is not working, in words rather than a silent empty list."""
    mark = cutoff()
    out = {
        "cloudinary": ready(),
        "openai": settings.openai_ready,
        "cutoff": mark,
        "indexing_started": bool(mark),
        "indexed_count": None,
        "library_count": None,
        "note": "",
    }
    if not ready():
        out["note"] = "CLOUDINARY_URL is not set; the library cannot be read."
        return out
    if not settings.openai_ready:
        out["note"] = ("OPENAI_API_KEY is not set. Existing index entries are "
                       "searchable; new clips cannot be described.")
    _configure()
    # Counted rather than left as zero: "0 indexed" and "could not count" mean
    # very different things and must not look the same on the page.
    for key, expr in (("indexed_count", f"resource_type:video AND tags:{INDEX_TAG}"),
                      ("library_count", "resource_type:video")):
        try:
            out[key] = int((Search().expression(expr).max_results(0)
                            .execute()).get("total_count") or 0)
        except Exception:                   # noqa: BLE001
            out[key] = None
    return out
