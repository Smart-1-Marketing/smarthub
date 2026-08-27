#!/usr/bin/env python3
"""hub/video_library.py — the parts that fail silently if they drift.

No pytest, no new dependencies, a temporary data directory. Run it directly:

    python3 test_video_library.py

Three of these assert things that were found the hard way while building the
module, against the live Cloudinary account:

  * `created_at>"...+00:00"` is a **query error** in Cloudinary's expression
    language, while the same instant written `...Z` parses. Getting this wrong
    makes pending() throw on every call, which the module swallows into an
    empty list — so the tool would have reported "nothing to index" forever
    and nothing would have looked broken.
  * A comparison clause placed *after* a negated one is also a query error.
    Same terms, same meaning, and only one clause order parses.
  * Cloudinary's `created_at` comes back as `+00:00` while the cutoff is stored
    as `Z`, so the two sides genuinely differ in format and a string compare of
    them only happens to work while the dates differ before the offset.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

_TMP = tempfile.mkdtemp(prefix="vl-test-")
os.environ["HUB_DATA_DIR"] = os.path.join(_TMP, "disk")
os.environ["CLOUDINARY_URL"] = "cloudinary://123456789:secret@testcloud"
# A throwaway mirror, and not an optional detail. jsonstore keys a file by its
# path *relative to the data root*, so a fresh temporary directory alone does
# not isolate anything: the first run of this file wrote a cutoff, and the
# second run restored it from the real mirror into a brand-new empty tempdir
# and failed on "no cutoff before anything indexes". That is jsonstore doing
# exactly what it exists to do. Point it somewhere disposable instead.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "mirror.sqlite3")

from hub import video_library as vl            # noqa: E402

FAILED = []

_ROOT = os.path.dirname(os.path.abspath(__file__))


def _read(*parts):
    with open(os.path.join(_ROOT, *parts), encoding="utf-8",
              errors="ignore") as fh:
        return fh.read()


def check(label, got, want):
    if got != want:
        FAILED.append(f"{label}\n     got:  {got!r}\n     want: {want!r}")
        print(f"  FAIL {label}")
    else:
        print(f"  ok   {label}")


def check_true(label, got):
    check(label, bool(got), True)


def check_in(label, needle, haystack):
    if needle not in haystack:
        FAILED.append(f"{label}\n     {needle!r} not in {haystack!r}")
        print(f"  FAIL {label}")
    else:
        print(f"  ok   {label}")


def check_not_in(label, needle, haystack):
    if needle in haystack:
        FAILED.append(f"{label}\n     {needle!r} unexpectedly in {haystack!r}")
        print(f"  FAIL {label}")
    else:
        print(f"  ok   {label}")


# ---------------------------------------------------------------------------
print("\nThe cutoff records when indexing began, and does not move")
# ---------------------------------------------------------------------------
check("no cutoff before anything indexes", vl.cutoff(), "")

first = vl.begin("tester")
check_true("begin() stamps one", first)
check("cutoff now reads back", vl.cutoff(), first)
check("begin() is idempotent — a second caller must not move it",
      vl.begin("someone else"), first)

# With the backlog on, the cutoff gates nothing: every clip in the allowlisted
# folders is in scope whenever it was uploaded. Asserted directly, because the
# alternative -- a stale gate quietly still excluding the back catalogue -- is
# a library that reads as healthy and finds nothing.
check("a clip from long before the cutoff is still indexable",
      vl._after_cutoff("2024-01-01T00:00:00+00:00", first), True)
check("...even with no cutoff stamped at all",
      vl._after_cutoff("2024-01-01T00:00:00+00:00", ""), True)

# The comparison itself is still in the code and still correct, and it is one
# flip of INDEX_BACKLOG from being load-bearing again -- so it stays asserted
# rather than being left untested until the day somebody turns it back on.
vl.INDEX_BACKLOG = False
try:
    check("forward-only: a clip from before the cutoff is not indexable",
          vl._after_cutoff("2026-06-03T21:05:26+00:00", first), False)
    check("forward-only: a clip from after it is",
          vl._after_cutoff("2027-01-01T00:00:00+00:00", first), True)
    check("an unreadable date stays out rather than being guessed in",
          vl._after_cutoff("not-a-date", first), False)
    check("a missing date stays out", vl._after_cutoff("", first), False)
    check("nothing is indexable before indexing has started",
          vl._after_cutoff("2027-01-01T00:00:00+00:00", ""), False)

    # Cloudinary returns +00:00; the cutoff is stored as Z. Comparing them as
    # strings works only by accident, so this asserts the parse is real.
    check("the two timestamp formats compare as instants, not as strings",
          vl._after_cutoff("2026-08-24T10:00:01+00:00", "2026-08-24T10:00:00Z"),
          True)
    check("...and in the other direction",
          vl._after_cutoff("2026-08-24T09:59:59+00:00", "2026-08-24T10:00:00Z"),
          False)

    # The clause order pending() emits when there *is* a date clause, asserted
    # directly. Verified against the live account: swapping these two produces
    # "Query Error (at position 45)" while this order returns results.
    _fwd = vl.pending_expression("2026-08-24T10:09:05Z")
    check_true("the expression opens on resource_type, never on a bare NOT",
               _fwd.startswith("resource_type:video"))
    check_true("the date clause precedes the negation — the other way is a "
               "parse error", _fwd.index('created_at>"') < _fwd.index("-tags:"))
    check_in("the cutoff is quoted", 'created_at>"2026-08-24T10:09:05Z"', _fwd)
    check_not_in("...in Z form, because +00:00 is a parse error", "+00:00", _fwd)
finally:
    vl.INDEX_BACKLOG = True


# ---------------------------------------------------------------------------
print("\nTimestamps are stamped in the only offset form the search API accepts")
# ---------------------------------------------------------------------------
stamp = vl._iso_z(datetime(2026, 8, 24, 10, 9, 5, tzinfo=timezone.utc))
check("Z, not +00:00", stamp, "2026-08-24T10:09:05Z")
check_not_in("no +00:00 anywhere in a stamped cutoff", "+00:00", vl.cutoff())
check_in("a stamped cutoff ends in Z", "Z", vl.cutoff()[-1])


# ---------------------------------------------------------------------------
print("\nSearch expressions parse (clause order is load-bearing)")
# ---------------------------------------------------------------------------
expr = vl.build_expression("drone suburb", tags=["bg-ready"])
check_in("indexed-only filter is present", "tags:s1-indexed", expr)
check_in("an explicit tag becomes its own clause", "tags:bg-ready", expr)
check_in("free text searches tags, description and filename together",
         "(tags:drone OR context.s1_desc:drone OR filename:drone)", expr)
check_in("...for every word", "context.s1_desc:suburb", expr)

# Reserved characters turn a search into a parse error rather than an empty
# result, which reads to a user as the tool being broken.
nasty = vl.build_expression('drone: [a TO b] {x} ^2 "quoted" oh(no)')
for char in (":", "[", "]", "{", "}", "^", '"', "(", ")"):
    # Parentheses are emitted by build_expression itself around each OR group,
    # so only assert that none survived from the user's input.
    if char in "()":
        continue
    # The scope clause is emitted by this module from a code constant, not by
    # the person typing, so it is stripped alongside the field prefixes --
    # leaving the assertion about what it has always been about: the user's own
    # characters.
    check_not_in(f"a bare {char!r} from user input never reaches the expression",
                 char, nasty.replace(vl.folder_clause(), "")
                            .replace("context.s1_desc:", "").replace("tags:", "")
                            .replace("filename:", "").replace("resource_type:", ""))

# The sweep's own clause order. With the backlog on there is no date clause at
# all -- that ordering is asserted above, under the flag that produces it.
pend = vl.pending_expression("2026-08-24T10:09:05Z")
check_true("the expression opens on resource_type, never on a bare NOT",
           pend.startswith("resource_type:video"))
check_in("unseen clips only", "-tags:s1-seen", pend)
check_not_in("the backlog sweep asks for no date at all", "created_at", pend)
check_not_in("...with or without a cutoff",
             "created_at", vl.pending_expression(""))


# ---------------------------------------------------------------------------
print("\nThe library is two folder trees, not the whole account")
# ---------------------------------------------------------------------------
# Before this scope existed, both counts and every search ran as bare
# `resource_type:video` -- every video in the product environment, client
# commercials and Cloudinary's own demo files included, presented under a
# heading about background footage. Each assertion below is one way the scope
# fails open again without anything looking broken.
check("the allowlist is the two folders asked for",
      list(vl.FOLDERS), ["Smart 1 Ads", "Video Backgrounds"])

_clause = vl.folder_clause()
for _f in vl.FOLDERS:
    check_in(f"{_f} itself is matched exactly", f'asset_folder="{_f}"', _clause)
    check_in(f"...and everything beneath {_f}", f'asset_folder:"{_f}/*"', _clause)
    # Dynamic folder mode publishes asset_folder, fixed mode publishes folder.
    # Asking for only one returns zero in an environment set the other way,
    # with every screen healthy -- which is exactly the situation this tool is
    # being pointed at a different product environment from the one it was
    # written against.
    check_in(f"...under either folder mode ({_f})", f'folder="{_f}"', _clause)
check_true("the clause is one parenthesised OR", _clause.startswith("(")
           and _clause.endswith(")") and " OR " in _clause)

_search_expr = vl.build_expression("drone")
check_in("every search carries the scope", _clause, _search_expr)
check_true("...directly after resource_type, before any negation or comparison",
           _search_expr.index(_clause) < _search_expr.index("tags:s1-indexed"))
_pend_expr = vl.pending_expression("2026-08-24T10:09:05Z")
check_in("so does the pending sweep", _clause, _pend_expr)
check_true("...still ahead of the negation",
           _pend_expr.index(_clause) < _pend_expr.index("-tags:"))
# And ahead of the date clause too, on the forward-only path that still emits
# one -- the ordering rule is about that path, so it is asserted there.
vl.INDEX_BACKLOG = False
try:
    _fwd_expr = vl.pending_expression("2026-08-24T10:09:05Z")
    check_true("...and ahead of the date clause when there is one",
               _fwd_expr.index(_clause) < _fwd_expr.index('created_at>"')
               < _fwd_expr.index("-tags:"))
finally:
    vl.INDEX_BACKLOG = True

# Whole path segments, for the reason hub/access.py refuses to read /statuses
# as /status.
check_true("a clip in an allowlisted folder is in scope",
           vl.in_scope("Video Backgrounds"))
check_true("...and one in a subfolder of it", vl.in_scope("Smart 1 Ads/Q3/cuts"))
check_true("a client folder is not", not vl.in_scope("Icon Solar"))
check_true("no folder at all is not", not vl.in_scope(""))
check_true("a folder that merely starts with the same letters is not",
           not vl.in_scope("Smart 1 Ads Archive"))
check_true("nor is one that merely contains the name",
           not vl.in_scope("clients/Smart 1 Ads"))

# An empty allowlist must refuse, never widen to the whole account.
_real = vl.FOLDERS
vl.FOLDERS = ()
check("an empty allowlist searches nothing", vl.folder_clause(), "")
_empty = vl.search("anything")
check("...and the search refuses rather than reading the account",
      _empty["results"], [])
check_in("...saying it is a configuration problem, not an empty library",
         "configuration problem", _empty["note"])
vl.FOLDERS = _real

# A search payload has to carry what it searched, or three clips on screen
# read as the whole library.
check("a result payload names the folders",
      vl.search("x")["folders"], list(vl.FOLDERS))
check_in("...and says so in words", "Smart 1 Ads/", vl.scope_note())


# ---------------------------------------------------------------------------
print("\nA folder named here and absent there is reported, never a zero")
# ---------------------------------------------------------------------------
# The scope's own failure mode. A folder renamed in Cloudinary, or a
# CLOUDINARY_URL pointing at a different product environment, matches nothing
# -- and a count of 0 says that identically to "nobody has uploaded any
# backgrounds yet". Only one of those is something to act on.
_rows = vl.folder_report()
check("one row per allowlisted folder", len(_rows), len(vl.FOLDERS))
check_true("existence is tri-state, and unreachable is None not False",
           all(r["exists"] is None for r in _rows))
check_true("...with a reason on every unmeasured row",
           all(r["note"] for r in _rows))

_st_missing = dict(vl.status())
check_true("status carries the folder rows", "folder_rows" in _st_missing)
check_true("...and the list of missing ones separately",
           isinstance(_st_missing.get("missing_folders"), list))

# The dashboard check must treat a missing folder as its own state rather than
# printing a truthful, useless zero.
_hub_block = _read("hub", "__init__.py").split("--- Video background library ---", 1)[1]
_hub_block = _hub_block.split("--- binaries for the PDF optimizer ---", 1)[0]
check_in("the dashboard names a missing folder", "missing_folders", _hub_block)
check_in("...as an error, not a count", '"error"', _hub_block)
# Matched short, because the sentence is wrapped across two source lines and a
# check that breaks on re-wrapping is a check somebody deletes.
check_in("...and says it is not an empty library", "not an empty", _hub_block)


# ---------------------------------------------------------------------------
print("\nIndexing cannot reach round the scope")
# ---------------------------------------------------------------------------
# pending() only ever returns in-scope clips, but index_asset() takes a
# public_id from its caller -- which is the path that goes round a search
# filter. Asserted on the source because exercising it needs a live account.
_src = _read("hub", "video_library.py")
_idx = _src.split("def index_asset(", 1)[1].split("\ndef ", 1)[0]
check_in("index_asset asks whether the asset is in scope", "in_scope(", _idx)
check_in("...and skips rather than failing on one that is not",
         "skipped_out_of_scope", _idx)
check_true("...before spending a vision call on it",
           _idx.index("in_scope(") < _idx.index("_describe("))
check_true("out of scope counts as skipped, not failed",
           'startswith("skipped")' in _src)


# ---------------------------------------------------------------------------
print("\nThe back catalog is swept, and one negation is all there is")
# ---------------------------------------------------------------------------
# Forward-only was right when the in-scope library was thirty nameless supplier
# clips; once the scope became the two folders holding the real footage it
# would have left every one of them permanently unsearchable behind a healthy
# looking count.
check("the backlog is indexed", vl.INDEX_BACKLOG, True)
check_true("...so the sweep carries no date clause",
           'created_at>"' not in vl.pending_expression("2026-08-24T10:09:05Z"))

# Exactly one trailing `-tags:` is the only negation Cloudinary's expression
# language handles here, and the two forms that look like alternatives are both
# worse than a parse error. Run against the live account:
#   -tags:s1-indexed AND -tags:s1-index-failed  -> Query Error
#   -(tags:s1-indexed OR tags:s1-index-failed)  -> parses, returns 0
#   (scope) NOT tags:s1-indexed                 -> parses, returns the whole
#                                                  account, folder scope gone
# So "described" and "given up on" are folded into one marker.
_sweep = vl.pending_expression("")
check("the sweep excludes exactly one tag", _sweep.count("-tags:"), 1)
check_in("...the seen marker, not the index marker", f"-tags:{vl.SEEN_TAG}", _sweep)
check_true("the index marker is not negated in the sweep",
           f"-tags:{vl.INDEX_TAG}" not in _sweep)
check_true("the negation is last", _sweep.rstrip().endswith(f"-tags:{vl.SEEN_TAG}"))
check_true("no parenthesised negation anywhere", "-(" not in _sweep)
check_true("no bare NOT — it discards the folder scope with it",
           " NOT " not in _sweep and " NOT " not in vl.build_expression("x"))

# Search still filters on the index marker, so a clip given up on is skipped by
# the sweep and invisible to search rather than appearing undescribed.
check_in("search still asks for described clips only",
         f"tags:{vl.INDEX_TAG}", vl.build_expression("x"))
check_true("...and never for the seen marker",
           f"tags:{vl.SEEN_TAG}" not in vl.build_expression("x"))
check_not_in("the marker is not shown to a user",
             vl.SEEN_TAG, vl._shape({"public_id": "a", "tags": [vl.SEEN_TAG, "aerial"]})["tags"])

_src = _read("hub", "video_library.py")
_ia = _src.split("def index_asset(", 1)[1].split("\ndef ", 1)[0]
check_in("a described clip carries the seen marker too", "SEEN_TAG", _ia)
check_in("...and one indexed before the marker existed is backfilled",
         "_mark_seen(", _ia)


# ---------------------------------------------------------------------------
print("\nA clip that cannot be described stops costing money")
# ---------------------------------------------------------------------------
# The sweep returns whatever carries no marker, so without this one unreadable
# clip is a vision call an hour for ever -- and every individual run looks like
# a normal batch that happened to have one failure in it.
check_true("there is an attempt ceiling", 0 < vl.MAX_ATTEMPTS <= 5)
check("no attempts recorded to start with", vl.attempts(), {})
check("a failure is counted", vl._bump_attempt("clip-a", "boom"), 1)
check("...and counts up", vl._bump_attempt("clip-a", "boom again"), 2)
check_in("...naming the clip", "clip-a", vl.attempts())
check_in("...with the reason kept", "boom again", vl.attempts()["clip-a"]["reason"])
vl._forget_attempts("clip-a")
check("a clip that succeeds loses its history", vl.attempts(), {})

_in = _src.split("def index_new(", 1)[1].split("\ndef ", 1)[0]
check_in("the runner counts failures per clip", "_bump_attempt(", _in)
check_in("...gives up at the ceiling", "MAX_ATTEMPTS", _in)
check_in("...by writing the marker onto the asset", "_mark_seen(", _in)
check_in("...and forgets the attempt book once it has", "_forget_attempts(", _in)
check_in("a success clears the clip's failures", "_forget_attempts(pid)", _in)
# A give-up kept only in memory forgets itself on the next deploy, and the clip
# starts costing a call an hour again with nothing saying so.
_ms = _src.split("def _mark_seen(", 1)[1].split("\ndef ", 1)[0]
check_in("the give-up records why, on the clip", "CTX_SKIPPED", _ms)
check_true("...and a give-up we could not write is retried, not lost",
           "return False" in _ms)


# ---------------------------------------------------------------------------
print("\nThe sweep is bounded by clips and by the clock")
# ---------------------------------------------------------------------------
# Scheduler jobs run in sequence on one thread and a vision call has no useful
# ceiling, so a batch with only a count limit holds up every job behind it.
check_true("the batch is bounded", 0 < vl.BACKLOG_BATCH <= vl.MAX_INDEX_BATCH)
check_true("the wall clock is bounded", 0 < vl.BACKLOG_SECONDS <= 600)
check_in("the runner takes a time budget", "max_seconds", _in)
check_in("...and says why it stopped rather than looking finished",
         'out["stopped"]', _in)

_sched = _read("hub", "scheduler.py")
check_in("the sweep is a registered job", '"video_backlog"', _sched)
check_in("...calling the bounded entry point", "index_backlog(", _sched)
check_true("...hourly", '"video_backlog":     (60,' in _sched)
_job = _sched.split("def job_index_video_backlog(", 1)[1].split("\n\nJOBS", 1)[0]
check_in("an unconfigured Hub is skipped, not failed every hour",
         '"skipped"', _job)
check_in("...and a provider outage cannot take the scheduler down",
         "except Exception", _job)


# ---------------------------------------------------------------------------
print("\nProgress is four numbers, because two cannot say if it is moving")
# ---------------------------------------------------------------------------
_st = vl.status()
for _k in ("library_count", "indexed_count", "waiting_count", "undescribed_count"):
    check(f"{_k} is None when it could not be counted", _st[_k], None)
_page_src = _read("modules", "video_backgrounds", "templates",
                  "video_backgrounds.html")
check_in("the page shows what is left to do", "Still to describe", _page_src)
check_in("...and what was given up on", "Could not be described", _page_src)
check_true("the page no longer claims indexing is forward-only",
           "forward-only" not in _page_src)


# ---------------------------------------------------------------------------
print("\nThe tag vocabulary is closed, and drops are reported not swallowed")
# ---------------------------------------------------------------------------
tags, desc, dropped = vl.validate({
    "description": "Drone shot over a suburban neighborhood at sunset.",
    "subject": ["aerial", "suburb", "banana"],
    "motion": ["drone"],
    "look": ["golden-hour", "warm"],
    "flags": ["bg-ready", "loopable"],
})
check_in("a vocabulary term is kept", "aerial", tags)
check_in("...and so is a flag", "loopable", tags)
check_not_in("a term outside the vocabulary is dropped", "banana", tags)
check_in("...and the drop is reported so the drift is visible",
         "subject:banana", dropped)
check("the description survives", desc,
      "Drone shot over a suburban neighborhood at sunset.")

# Group limits stop one runaway reply flooding a clip with twelve subjects.
tags, _, _ = vl.validate({"subject": list(vl.VOCAB["subject"]),
                          "motion": list(vl.VOCAB["motion"]), "look": [], "flags": []})
check("at most 3 subject terms",
      len([t for t in tags if t in vl.VOCAB["subject"]]), 3)
check("exactly 1 motion term",
      len([t for t in tags if t in vl.VOCAB["motion"]]), 1)

# Burned-in text makes a clip unusable behind a headline whatever the model said.
tags, _, dropped = vl.validate({
    "description": "Promo card with a phone number burned in.",
    "subject": ["abstract"], "motion": ["static"], "look": ["bright"],
    "flags": ["has-text", "bg-ready"],
})
check_in("has-text is kept", "has-text", tags)
check_not_in("bg-ready cannot survive alongside has-text", "bg-ready", tags)
check_true("the override is recorded",
           any("bg-ready" in d for d in dropped))

# The vocabulary groups must not overlap: a term in two groups would be
# validated against whichever group happened to be checked first.
_all = [t for group in vl.VOCAB.values() for t in group] + list(vl.FLAGS)
check("no term appears in two groups", len(_all), len(set(_all)))
check_not_in("the marker tag is not also a vocabulary term", vl.INDEX_TAG, _all)


# ---------------------------------------------------------------------------
print("\nA model reply is parsed out of whatever wrapping it arrives in")
# ---------------------------------------------------------------------------
check("fenced JSON", vl._parse('```json\n{"description":"x"}\n```'),
      {"description": "x"})
check("bare JSON", vl._parse('{"description":"x"}'), {"description": "x"})
check("JSON with prose around it",
      vl._parse('Sure! {"description":"x"} Hope that helps.'), {"description": "x"})
for bad, label in ((("not json at all"), "prose only"),
                   ("", "empty reply"),
                   ('{"description": }', "malformed JSON"),
                   ('["a","b"]', "a list rather than an object")):
    try:
        vl._parse(bad)
        FAILED.append(f"_parse accepted {label}")
        print(f"  FAIL _parse rejects {label}")
    except vl.VideoLibraryError:
        print(f"  ok   _parse rejects {label}")


# ---------------------------------------------------------------------------
print("\nA delivery URL never points at the master")
# ---------------------------------------------------------------------------
bg = vl.background_url("video files/hd0021", width=1280, height=720, duration=8)
check_in("built against the configured cloud", "res.cloudinary.com/testcloud/video/upload/", bg)
check_in("audio is stripped — a background must not make noise", "ac_none", bg)
check_in("format is left to Cloudinary", "f_auto:video", bg)
check_in("quality is left to Cloudinary", "q_auto", bg)
check_in("trimmed to a loop length", "so_0,du_8", bg)
check_in("edge is capped", "w_1280", bg)
check_in("a space in the public_id is encoded", "video%20files/hd0021", bg)
check_not_in("the folder separator survives encoding", "video%20files%2Fhd0021", bg)
check_true("the master extension is not served", bg.endswith(".mp4"))

check("a whole-clip URL omits the trim",
      "so_" in vl.background_url("hd0021", duration=None), False)
check("a fractional duration is not rendered as 8.0",
      "du_8/" in vl.background_url("x", duration=8.0), True)
check_in("a real fraction survives", "du_8.5", vl.background_url("x", duration=8.5))

poster = vl.poster_url("Icon_psycho_eyes_1", second=2, width=640)
check_in("a poster is a still from the clip", "so_2", poster)
check_true("...delivered as a jpg", poster.endswith(".jpg"))

frames = vl.keyframe_urls("hd0021", 12, count=3)
check("three keyframes", len(frames), 3)
check_true("sampled inside the clip, never at frame zero",
           all("so_0/" not in f for f in frames))
check("a clip of unknown length still yields one frame",
      len(vl.keyframe_urls("hd0021", None)), 1)
check_true("the keyframe count is clamped",
           len(vl.keyframe_urls("hd0021", 30, count=99)) <= 6)


# ---------------------------------------------------------------------------
print("\nAbsent data reads as 'not measured', never as zero")
# ---------------------------------------------------------------------------
# No network here, so the Cloudinary calls fail and the counts must come back
# as None rather than 0 — "we could not count" and "there are none" are
# different answers and must not look alike on the page.
st = vl.status()
check("indexed_count is None when it could not be counted", st["indexed_count"], None)
check("library_count is None when it could not be counted", st["library_count"], None)
check_true("the cutoff is reported", st["cutoff"])

# A search with indexing not yet started must say so, not return a bare empty
# list that reads as "we own nothing".
import json as _json                                            # noqa: E402
_saved = vl._read_state()
vl.jsonstore.write_json(vl._state_path(), {})
blank = vl.search("anything")
check("a search before indexing starts is not an error", blank["ok"], True)
check("...and returns nothing", blank["results"], [])
check_in("...but says why", "Indexing has not run yet", blank["note"])
check_in("...and what it would have searched", "Smart 1 Ads/", blank["note"])
vl.jsonstore.write_json(vl._state_path(), _saved)


# ---------------------------------------------------------------------------
print("\nOwned results already match the Commercial Builder's asset shape")
# ---------------------------------------------------------------------------
# stock.py merges these into the same list as Pexels and Pixabay with no
# translation step, so a missing key here is a KeyError in that route.
shaped = vl._shape({
    "public_id": "video files/hd0021", "asset_id": "abc123",
    "width": 1920, "height": 1080, "duration": 12.5, "bytes": 9194949,
    "asset_folder": "video files",
    "tags": ["aerial", "drone", "bg-ready", vl.INDEX_TAG, vl.SCHEMA_TAG],
    "context": {"custom": {vl.CTX_DESC: "Drone over a suburb.",
                           vl.CTX_INDEXED_AT: "2026-08-24T10:00:00Z"}},
})
for key in ("id", "provider", "tier", "thumbnail", "preview_url", "full_url",
            "width", "height", "duration", "author", "source_url"):
    check_true(f"the universal asset shape carries {key}", key in shaped)
check("owned footage is labeled as such", shaped["tier"], "OWNED")
check("the description is lifted out of nested context",
      shaped["description"], "Drone over a suburb.")
check_not_in("the marker tag is not shown to a user", vl.INDEX_TAG, shaped["tags"])
check_in("a real tag is", "drone", shaped["tags"])
check("bg_ready is exposed as a flag", shaped["bg_ready"], True)
check("aspect is computed for the orientation filter", shaped["aspect"], 1.778)

# Cloudinary returns context flat as well as nested, and both shapes exist in
# this account.
flat = vl._shape({"public_id": "x", "width": 1080, "height": 1920,
                  "context": {vl.CTX_DESC: "Vertical clip."}, "tags": []})
check("flat context is read too", flat["description"], "Vertical clip.")
check("a portrait clip's aspect is below 1", flat["aspect"] < 1, True)

check("a landscape clip passes the landscape filter",
      vl._matches(shaped, "landscape", None), True)
check("...and fails the portrait filter", vl._matches(shaped, "portrait", None), False)
check("a 12.5s clip fails a 10s cap", vl._matches(shaped, "", 10), False)
check("...and passes a 20s cap", vl._matches(shaped, "", 20), True)


# ---------------------------------------------------------------------------
print("\nThe library reports itself on System Status")
# ---------------------------------------------------------------------------
# The tool's own status card is only seen by someone already on the tool. The
# question "is the video library working?" gets asked on /status like every
# other key and connection, so the check has to exist there -- and it has to
# distinguish the three states the card distinguishes, because "no results"
# means something different in each.
import re as _re                                                # noqa: E402

_hub_src = _read("hub", "__init__.py")
check_in("/api/status carries a video library check",
         'add("Video background library"', _hub_src)
_block = _hub_src.split('# --- Video background library ---', 1)[-1].split(
    '# --- binaries for the PDF optimizer ---', 1)[0]
check_true("it asks the library rather than reading the two env vars itself",
           "video_library" in _block and "os.environ" not in _block)
check("it separates unset Cloudinary, a missing folder, never-indexed and working",
      len(_re.findall(r'add\("Video background library"', _block)), 5)
check_in("never-indexed says so rather than reading as an empty library",
         "Indexing has never run", _block)
check_in("a count that could not be taken is not printed as a number",
         "counts unavailable", _block)


# ---------------------------------------------------------------------------
print("\nThe page is legible on the Hub's own light theme")
# ---------------------------------------------------------------------------
# Every control on this page was originally a near-black plate carrying grey
# text, which on hub.css's white .card reads as a disabled button. The failure
# is invisible to every other check in the repo -- the template is valid, the
# links resolve, the page renders -- so it is asserted here.
_page = _read("modules", "video_backgrounds", "templates",
              "video_backgrounds.html")
_css = _page.split("<style>", 1)[-1].split("</style>", 1)[0]
for _sel in (".vb-chip{", ".vb-input{", ".vb-acts button{"):
    _rule = _css.split(_sel, 1)[-1].split("}", 1)[0]
    check_true(_sel[:-1] + " is not a dark plate",
               "#0f172a" not in _rule and "#0b1220" not in _rule)
check_true("the video plate stays dark on purpose — footage is previewed on it",
           "#0f172a" in _css.split(".vb-card video", 1)[-1].split("}", 1)[0])
check_not_in("no button carries the class hub.css never defines",
             'class="btn"', _page)
check_in("the page says the free stock libraries are not indexed here",
         "our own Cloudinary footage only", _page)


# ---------------------------------------------------------------------------
print("\nLimits are clamped")
# ---------------------------------------------------------------------------
check_true("MAX_RESULTS is bounded", 0 < vl.MAX_RESULTS <= 500)
check_true("the index batch is bounded", 0 < vl.MAX_INDEX_BATCH <= 100)
# Asserted in the backlog section above, where the reason for it lives. Left
# here only as the clamp it belongs beside: a batch bounded by count and clock.
check_true("the sweep's batch is no larger than a manual one",
           vl.BACKLOG_BATCH <= vl.MAX_INDEX_BATCH)


# ---------------------------------------------------------------------------
shutil.rmtree(_TMP, ignore_errors=True)
print()
if FAILED:
    print(f"{len(FAILED)} FAILED\n")
    for f in FAILED:
        print("  - " + f)
    sys.exit(1)
print("All video library checks passed.")
