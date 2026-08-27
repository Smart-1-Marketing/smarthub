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

Four decisions worth keeping
----------------------------

**The library is two folder trees, not the account.** `FOLDERS` is an
allowlist — `Smart 1 Ads` and `Video Backgrounds`, each including everything
beneath it — and it scopes the counts, the search and what may be indexed
alike. Before it, "Clips in Cloudinary" meant every video in the product
environment: client commercials, internal explainers and Cloudinary's own demo
files, counted and offered as background footage. A folder named here and
absent there is *reported* rather than returning a confident zero, because a
renamed folder and an empty one are different answers.

**The back catalogue is the library, and a sweep works through it.** Indexing
was forward-only — `cutoff()` stamped on the first run, nothing older ever
touched — which was right when the in-scope library was thirty nameless
supplier clips and became exactly wrong the moment the scope became the two
folders that already hold the real footage: every clip in them would have been
permanently unsearchable while the page reported a healthy count beside a zero.
`INDEX_BACKLOG` is on, and `index_backlog()` runs on `hub/scheduler.py` at
twenty clips an hour under a wall-clock budget, because scheduler jobs share
one thread and a vision call has no useful ceiling on how long it takes. The
cutoff still exists and still records when indexing began; it no longer gates
anything.

**A clip that fails is given up on, in writing.** The sweep returns whatever
carries no marker, so a clip that cannot be described comes back on the very
next run — a vision call an hour, for ever, with every individual run looking
like a normal batch that had one failure in it. Three attempts, counted in the
state file, and then `SEEN_TAG` goes on the asset with the reason in context.
The give-up is a write rather than a note in memory because one kept in memory
forgets itself on the next deploy. `undescribed_count` puts the total on the
page, since a number that should never grow quietly is the one worth showing.

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
import time
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
        "night", "golden-hour", "high-contrast", "desaturated", "colorful",
    ),
}

# A vocabulary term is written onto the clip as a Cloudinary tag, so renaming
# one takes every clip already carrying the old spelling out of the filter it
# used to answer -- a chip that returns nothing, on a library the page still
# reports a healthy count for. `colourful` was the spelling until Smart 1's
# copy was settled as American English. So the term offered is the new one and
# the old one is still *matched*, per term, rather than the whole library being
# re-indexed to correct a label. Re-indexing would be a vision call per clip
# and would rewrite what a person may have corrected by hand.
TAG_ALIASES: dict[str, tuple[str, ...]] = {
    "colorful": ("colorful",),
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

# "We have finished deciding about this clip", written whether the decision was
# a description or a give-up. It exists because of a hard limit in Cloudinary's
# expression language rather than for its own sake: the sweep needs to skip
# both the clips it has described *and* the ones it has repeatedly failed on,
# and there is no way to write that as two exclusions. Run against this
# account:
#
#   ... AND -tags:s1-indexed AND -tags:s1-index-failed   -> Query Error
#   ... AND -(tags:s1-indexed OR tags:s1-index-failed)   -> parses, returns 0
#   ... AND (scope) NOT tags:s1-indexed                  -> parses, returns 266
#
# The second is the dangerous one -- it looks like a working query and quietly
# excludes everything -- and the third is worse: `NOT` discarded the folder
# scope with it and answered about the whole account. Exactly one trailing
# `-tags:x` is the only negation that behaves, so the two states are folded
# into one tag and the *search* filter stays INDEX_TAG. A given-up clip
# therefore carries SEEN_TAG alone: skipped by the sweep, invisible to search,
# and countable on the page.
SEEN_TAG = "s1-seen"

# Context keys. Cloudinary exposes these to the Search API as
# `context.<key>`, which is what makes the free-text half of search work.
CTX_DESC = "s1_desc"
CTX_INDEXED_AT = "s1_indexed_at"
# Why a clip carries SEEN_TAG without a description. Absent from search either
# way; the difference is whether anyone can find out why.
CTX_SKIPPED = "s1_skipped"

# On. The back catalogue *is* the library here -- the whole point of scoping to
# Smart 1 Ads and Video Backgrounds is that those folders already hold the
# footage worth searching, and forward-only would have left every clip in them
# permanently unsearchable while the page reported a healthy count beside a
# zero. Forward-only was right when the in-scope library was thirty nameless
# supplier clips; it is the opposite of right now.
#
# Still a constant and not an env var: this costs a vision call per clip, and
# widening what a tool spends is a decision that belongs in a diff somebody
# reviewed rather than in a dashboard field at 2am.
INDEX_BACKLOG = True

# The sweep's shape. It runs on hub/scheduler.py, so it is bounded twice: by
# clip count, and by wall clock. The second bound is the one that matters --
# scheduler jobs run in sequence on one thread, so a batch that takes twenty
# minutes delays every other job behind it, and a vision call has no useful
# upper bound on how long it can take.
BACKLOG_BATCH = 20
BACKLOG_SECONDS = 240

# How many times one clip is described before the sweep gives up on it. A clip
# that fails is returned by the very next sweep, so without this a single
# unreadable file costs a vision call an hour for ever -- the cost leak is
# silent, because every individual run looks like a normal batch with one
# failure in it. Three, because a failure is usually the provider having a bad
# minute rather than the clip being bad.
MAX_ATTEMPTS = 3

# ---------------------------------------------------------------------------
# Scope — the folders this tool is allowed to see
# ---------------------------------------------------------------------------
# There was no scope at all until now, and that is not a small omission: both
# the counts on the status card and every search ran as bare
# `resource_type:video`, which is *every video in the product environment*. On
# the account this was built against that meant 33 clips of genuine stock
# footage counted alongside a client's solar spots, a chiropractor's social
# cuts, an internal rebate explainer and four of Cloudinary's own demo videos
# — presented under a heading reading "Clips in Cloudinary" on a tool whose
# entire job is backgrounds. A number that large reads as a deep library and
# is mostly footage nobody may put behind a headline.
#
# So the library is an allowlist of folder trees. Everything else in the
# account is invisible here: not ranked lower, not filtered on the way out —
# never asked for. Each entry covers the folder itself *and* every subfolder
# beneath it, so a new campaign folder inside one of them is in scope the day
# it is created and needs no edit here.
#
# Deliberately a constant rather than an environment variable, for the reason
# INDEX_BACKLOG is one: widening what a tool can reach is a decision that
# belongs in a diff somebody reviewed, not in a dashboard field. A folder that
# is renamed in Cloudinary must therefore be renamed here too — which is
# exactly why `folder_report()` exists and why the page prints it. A rename
# with no report would empty the library and read as "we own no footage".
FOLDERS: tuple[str, ...] = (
    "Smart 1 Ads",
    "Video Backgrounds",
)

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
# The folder allowlist, as a search clause
# ---------------------------------------------------------------------------

def _folder_terms(path: str) -> list[str]:
    """The clauses that match one folder tree.

    Four of them, not two, and the pair that looks redundant is the point.
    Cloudinary publishes a folder two ways depending on which folder mode the
    product environment is in: `asset_folder` in dynamic folder mode, `folder`
    (derived from the public_id path) in fixed. Both were run against this
    account and both answer identically here, so asking for either costs
    nothing — while asking for only the wrong one would return **zero** in an
    environment set the other way, with every screen looking healthy and the
    library reading as empty. That is not a hypothetical: the environment this
    tool is pointed at is not the one it was written against.

    The exact form (`=`) matches the folder itself and the trailing-wildcard
    form (`:"path/*"`) matches everything below it. Neither alone is enough:
    `asset_folder="Video Backgrounds"` misses every subfolder, and
    `asset_folder:"Video Backgrounds/*"` misses every clip sitting directly in
    it. Both were verified against the live search API.
    """
    # A double quote would close the quoted value and turn the rest of the
    # folder name into syntax. Nothing else in a folder name is special once
    # quoted, and a folder here is a code constant rather than user input --
    # this is belt and braces on a name someone will one day paste in.
    clean = str(path or "").strip().strip("/").replace('"', "")
    if not clean:
        return []
    return [f'asset_folder="{clean}"', f'asset_folder:"{clean}/*"',
            f'folder="{clean}"', f'folder:"{clean}/*"']


def folder_clause() -> str:
    """The parenthesised OR that scopes every query in this module.

    Returns "" when the allowlist is empty, which no caller treats as "search
    everything" -- see `search()`. An allowlist that widens to the whole
    account when someone deletes a line is the failure this scope exists to
    prevent.
    """
    terms: list[str] = []
    for path in FOLDERS:
        terms.extend(_folder_terms(path))
    return "(" + " OR ".join(terms) + ")" if terms else ""


def in_scope(folder: str) -> bool:
    """Is this asset's folder inside the allowlist?

    Applied to a single asset before it is indexed, so a public_id typed or
    passed in by hand cannot reach round the scope and spend a vision call
    describing a client's commercial. Matched on whole path segments: "Smart 1
    Ads Archive" is not inside "Smart 1 Ads", exactly as `hub/access.py`
    refuses to read `/statuses` as `/status`.
    """
    got = str(folder or "").strip().strip("/")
    for path in FOLDERS:
        allowed = str(path or "").strip().strip("/")
        if allowed and (got == allowed or got.startswith(allowed + "/")):
            return True
    return False


def scope_note() -> str:
    """What the tool searched, in words, for any screen that shows results."""
    if not FOLDERS:
        return ("No folders are allowlisted, so there is nothing to search. "
                "This is a configuration problem, not an empty library.")
    names = ", ".join(f"{f}/" for f in FOLDERS)
    return (f"Searching {names} and everything beneath them. Footage "
            f"elsewhere in the account is deliberately out of scope.")


def folder_report() -> list[dict]:
    """Whether each allowlisted folder actually exists, one row each.

    The whole point of the scope is that a folder named here and absent there
    returns nothing -- and "nobody has uploaded backgrounds yet", "this folder
    was renamed in Cloudinary" and "we could not ask Cloudinary" are three
    different answers that a bare 0 renders identically. So `exists` is
    tri-state: True, False, or None for *not measured*, which is the rule this
    codebase applies everywhere else and the one thing a count cannot say.
    """
    rows: list[dict] = []
    if not ready():
        return [{"path": f, "exists": None,
                 "note": "Cloudinary is not configured, so this could not be "
                         "checked."} for f in FOLDERS]
    _configure()
    for path in FOLDERS:
        row = {"path": path, "exists": None, "note": ""}
        try:
            cloudinary.api.subfolders(path)
            row["exists"] = True
        except Exception as exc:            # noqa: BLE001
            # NotFound is an answer; anything else is a failure to look, and
            # calling the second one "missing" sends somebody hunting for a
            # folder that is sitting there.
            if type(exc).__name__ == "NotFound":
                row["exists"] = False
                row["note"] = ("No folder of this name in this Cloudinary "
                               "product environment. Nothing here can match "
                               "it.")
            else:
                row["note"] = f"Could not be checked: {exc}"
        rows.append(row)
    return rows


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


def attempts() -> dict:
    """public_id -> how many times describing it has failed.

    Kept in the state file rather than on the asset, because it is *in-flight*
    bookkeeping: the moment a clip is given up on, SEEN_TAG goes on the asset
    and its entry here is dropped. So this dict holds only clips currently
    failing, which is a handful, not a copy of the library.
    """
    got = _read_state().get("attempts")
    return got if isinstance(got, dict) else {}


def _bump_attempt(public_id: str, reason: str) -> int:
    """Record a failure and return the new count. Never raises."""
    try:
        state = _read_state()
        book = state.get("attempts")
        if not isinstance(book, dict):
            book = {}
        row = book.get(public_id)
        count = int((row or {}).get("count") or 0) + 1
        book[public_id] = {"count": count, "reason": str(reason)[:200],
                           "last": _iso_z(datetime.now(timezone.utc))}
        state["attempts"] = book
        jsonstore.write_json(_state_path(), state)
        return count
    except Exception:                       # noqa: BLE001
        # Losing the count costs a retry, which is the cheap direction to fail.
        return 1


def _forget_attempts(public_id: str) -> None:
    """Drop a clip's failure history — it succeeded, or we gave up on it."""
    try:
        state = _read_state()
        book = state.get("attempts")
        if isinstance(book, dict) and public_id in book:
            book.pop(public_id, None)
            state["attempts"] = book
            jsonstore.write_json(_state_path(), state)
    except Exception:                       # noqa: BLE001
        pass


def _mark_seen(public_id: str, reason: str) -> bool:
    """Put SEEN_TAG on a clip we are not going to describe.

    This is the give-up write, and it is what stops the sweep returning the
    same broken clip every hour for ever. The reason rides along in context so
    the clip is not merely absent from search with nothing saying why.
    """
    try:
        info = cloudinary.api.resource(public_id, resource_type="video",
                                       tags=True)
        tags = sorted(set(info.get("tags") or []) | {SEEN_TAG})
        cloudinary.api.update(public_id, resource_type="video",
                              tags=",".join(tags),
                              context={CTX_SKIPPED: str(reason)[:200]})
        return True
    except Exception:                       # noqa: BLE001
        # A give-up we could not write means the clip comes back next sweep,
        # which is the safe direction: a retry costs one call, a lost clip is
        # invisible for ever.
        return False


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

_PROMPT = """You are cataloging a stock video clip so it can be found later by
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
  has-faces   a face is large enough to recognize
  has-text    words are burned into the footage
  loopable    the last frame could cut back to the first without a jump
  bg-ready    the look holds steady and there is room for an overlay; do NOT
              set this if has-text is set, or if the subject sits dead center
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

    # Before the cutoff test and before any vision call: the scope is about
    # what this tool may look at at all, and a public_id arriving from a
    # caller rather than from pending() is exactly the path that goes round a
    # search filter. Skipped, not failed -- an asset outside the library is
    # not an error, it is none of our business.
    folder = info.get("asset_folder")
    if folder is None:
        folder = str(info.get("folder") or "")
    if not in_scope(folder):
        result["status"] = "skipped_out_of_scope"
        result["reason"] = (f"Sits in {folder or 'no folder'}, which is outside "
                            f"the library. " + scope_note())
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
        # A clip indexed before SEEN_TAG existed carries INDEX_TAG alone, so
        # the sweep would hand it back every hour for ever -- costing a
        # Cloudinary read each time and never a vision call, which is cheap
        # enough to be invisible and wrong enough to fix. Backfilling here
        # settles each one permanently on its first sweep.
        if SEEN_TAG not in (info.get("tags") or []):
            _mark_seen(public_id, "Indexed before the seen marker existed.")
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

    all_tags = sorted(set(tags) | {INDEX_TAG, SCHEMA_TAG, SEEN_TAG})
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
    scope = folder_clause()
    if scope:
        expr += f" AND {scope}"
    if mark and not INDEX_BACKLOG:
        expr += f' AND created_at>"{mark}"'
    # SEEN_TAG rather than INDEX_TAG, so a clip we have already given up on is
    # not handed back every sweep. See SEEN_TAG's own note: two exclusions
    # cannot be written here, and the two forms that look like they can either
    # return nothing or silently drop the folder scope.
    expr += f" AND -tags:{SEEN_TAG}"
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


def index_new(limit: int = MAX_INDEX_BATCH, actor: str = "",
              max_seconds: float | int | None = None) -> dict:
    """Index everything waiting. Stamps the cutoff on the first ever run.

    `max_seconds` is a wall-clock budget, for the scheduler. Scheduler jobs run
    in sequence on one thread, and a vision call has no useful upper bound on
    how long it can take -- so without it a slow provider turns a twenty-clip
    batch into a job that holds up every other job behind it. Stopping early is
    free here: whatever was not reached is still un-tagged and comes back on
    the next sweep.
    """
    started = begin(actor)
    out = {"cutoff": started, "indexed": 0, "skipped": 0, "failed": 0,
           "gave_up": 0, "stopped": "", "results": []}
    if not can_index():
        out["error"] = ("Indexing needs both CLOUDINARY_URL and "
                        "OPENAI_API_KEY; one of them is not set.")
        return out

    deadline = (time.monotonic() + float(max_seconds)) if max_seconds else None
    book = attempts()
    for item in pending(limit):
        if deadline is not None and time.monotonic() >= deadline:
            out["stopped"] = (f"Ran out of its {int(max_seconds)}s budget. The "
                              f"rest comes back on the next sweep.")
            break
        pid = item["public_id"]
        res = index_asset(pid)
        out["results"].append(res)
        if res["status"] == "indexed":
            out["indexed"] += 1
            _forget_attempts(pid)
        elif res["status"].startswith("skipped"):
            out["skipped"] += 1
        else:
            out["failed"] += 1
            # Count the failure, and stop paying for this clip once it has had
            # its three goes. Giving up is a *write* -- SEEN_TAG on the asset --
            # because a give-up recorded only in memory is a give-up that
            # forgets itself on the next deploy.
            count = _bump_attempt(pid, res.get("reason") or "")
            prior = int((book.get(pid) or {}).get("count") or 0)
            if count >= MAX_ATTEMPTS or prior >= MAX_ATTEMPTS:
                if _mark_seen(pid, f"Gave up after {count} attempts: "
                                   f"{res.get('reason') or 'unknown'}"):
                    _forget_attempts(pid)
                    out["gave_up"] += 1
                    res["status"] = "gave_up"
    return out


def index_backlog(actor: str = "scheduler") -> dict:
    """One bounded pass for the scheduler, over whatever is still unseen.

    The library this points at is a back catalogue, so there is no meaningful
    difference between "new clips" and "the backlog" -- both are simply clips
    carrying no SEEN_TAG, oldest first. This is `index_new` with the sweep's
    two bounds applied, kept as its own name so the job reads as what it is.
    """
    out = index_new(limit=BACKLOG_BATCH, actor=actor,
                    max_seconds=BACKLOG_SECONDS)
    # The per-clip results are the expensive half of the payload and the
    # scheduler stores every result in its state dict, so the summary goes back
    # and the detail does not.
    out.pop("results", None)
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
    # Second, immediately after resource_type and before anything negated or
    # compared -- see pending_expression() for why clause order is not free
    # here. Every query in this module carries it: a search that could reach
    # outside FOLDERS is the scope not existing.
    scope = folder_clause()
    if scope:
        clauses.append(scope)
    if indexed_only:
        clauses.append(f"tags:{INDEX_TAG}")

    for tag in (tags or []):
        clean = _SAFE_TERM.sub("", str(tag).strip().lower())
        if not clean:
            continue
        # A term that was spelled differently when older clips were indexed
        # still has to find them -- see TAG_ALIASES. Parenthesised, and every
        # branch positive, so it stays a comparison clause the expression
        # language accepts here.
        also = [a for a in TAG_ALIASES.get(clean, ())
                if _SAFE_TERM.sub("", a) == a]
        if also:
            branches = " OR ".join(f"tags:{t}" for t in (clean, *also))
            clauses.append(f"({branches})")
        else:
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
           "cutoff": cutoff(), "indexed_only": indexed_only, "note": "",
           "folders": list(FOLDERS), "scope": scope_note()}

    if not FOLDERS:
        # Never "search everything instead". An allowlist that falls back to
        # the whole account the moment it is empty is the scope failing open,
        # which is the one way this change could make things worse than they
        # were.
        out["note"] = scope_note()
        return out
    if not ready():
        out["note"] = ("Cloudinary is not configured, so the owned library "
                       "cannot be searched. This is not an empty library.")
        return out
    if indexed_only and not cutoff():
        out["ok"] = True
        out["note"] = ("Indexing has not run yet, so nothing is searchable. "
                       + scope_note()
                       + " The scheduler describes a batch an hour once it "
                         "starts; the button on this page runs one now.")
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
        out["note"] = ("Nothing indexed matches that yet. " + scope_note()
                       + " Indexing covers clips uploaded after "
                       + f"{out['cutoff'] or 'it starts'}.")
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
            if t not in (INDEX_TAG, SCHEMA_TAG, SEEN_TAG)]
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
        "waiting_count": None,
        "undescribed_count": None,
        "failing": len(attempts()),
        "folders": list(FOLDERS),
        "folder_rows": [],
        "missing_folders": [],
        "scope": scope_note(),
        "note": "",
    }
    if not ready():
        out["note"] = "CLOUDINARY_URL is not set; the library cannot be read."
        return out
    if not settings.openai_ready:
        out["note"] = ("OPENAI_API_KEY is not set. Existing index entries are "
                       "searchable; new clips cannot be described.")
    _configure()
    out["folder_rows"] = folder_report()
    out["missing_folders"] = [r["path"] for r in out["folder_rows"]
                              if r["exists"] is False]
    if out["missing_folders"]:
        # Said here rather than left for a reader to infer from a zero. A
        # folder named in FOLDERS and absent from the account cannot match
        # anything, and the count below will be honest and useless.
        out["note"] = ((out["note"] + " ") if out["note"] else "") + (
            "Not in this Cloudinary product environment: "
            + ", ".join(out["missing_folders"])
            + ". Nothing in the library can come from them — check the folder "
              "names, or whether the Hub's CLOUDINARY_URL points at the "
              "product environment that holds them.")
    # Counted rather than left as zero: "0 indexed" and "could not count" mean
    # very different things and must not look the same on the page. Both counts
    # are scoped to FOLDERS, so this row answers "how much of the background
    # library is searchable" rather than "how many videos does the account
    # hold" -- which is what it used to answer while claiming the first.
    scope = folder_clause()
    prefix = f"resource_type:video AND {scope}" if scope else "resource_type:video"
    # Four numbers rather than two, because "3,900 clips, 40 indexed" on its
    # own cannot say whether the sweep is working through the library or has
    # stopped. waiting_count is what is left to do; undescribed_count is what
    # was tried and given up on, which is the number that should never grow
    # quietly. Each negation is a single trailing `-tags:` -- see SEEN_TAG.
    for key, expr in (("indexed_count", f"{prefix} AND tags:{INDEX_TAG}"),
                      ("library_count", prefix),
                      ("waiting_count", f"{prefix} AND -tags:{SEEN_TAG}"),
                      ("undescribed_count",
                       f"{prefix} AND tags:{SEEN_TAG} AND -tags:{INDEX_TAG}")):
        try:
            out[key] = int((Search().expression(expr).max_results(0)
                            .execute()).get("total_count") or 0)
        except Exception:                   # noqa: BLE001
            out[key] = None
    return out
