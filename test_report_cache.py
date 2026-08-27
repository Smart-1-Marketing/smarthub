"""One run per report per day, and the ways a cache lies.

    python3 test_report_cache.py

Same shape as the other test files here: no pytest, no new dependencies, a
throwaway SQLite database and a temporary data directory, so it never touches
/var/data or the real one. Nothing in it reaches a provider — every report is
a stub function that counts how many times it was called, which is the whole
question this file asks.

## What is worth asserting

**The count.** Every QA report and every report-shaped tool page re-ran its
whole build on each open: a year of QuickBooks invoices, a walk of the
GoHighLevel pipeline, a Knack pull, a name match per row. Two people opening
one report in the same minute paid twice; one person pressing Back paid again.
So the first assertion is arithmetic — build called once, however many times
the report is read.

**A failed run never becomes the answer.** This is the rule the rest of the
codebase keeps having to relearn: `knack_products.refresh()` will not overwrite
a good cache with nothing, `domain_purchase.refresh()` records a failed attempt
*beside* the rows it could not replace. Here it has a second edge to it — a
report that comes back saying "QuickBooks isn't connected yet" is a perfectly
successful function call and is not an answer, and storing it would leave the
page saying so all day to somebody who connected QuickBooks at ten past nine.

**The age is on the page.** A cached figure with no date on it is read as
today's. Every payload carries a `cache` block and every page that renders one
prints its `line`.

**A GET never rebuilds.** Re-running is a POST, because a GET that rebuilds is
one a reload, a prefetch or a link preview fires without anybody asking — which
is the cost this whole arrangement exists to stop paying.

**A write drops what it changed.** Skipping a client on No Dashboards, or
assigning an invoice to a partner, takes a row off the report the button is on.
Left cached, the row is still there on the next open and the button reads as
having done nothing — so it gets pressed again.

**A search is not a cache key.** `q=acme` and `q=acm` are two files on a 5 GB
disk and a search box types one per keystroke.
"""
import datetime as _dt
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-reportcache-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.setdefault("SECRET_KEY", "report-cache-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ.pop("REPORT_CACHE", None)

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


def section(title):
    print("\n" + title)
    print("-" * 60)


from hub import report_cache as rc                              # noqa: E402


def _read_early(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


class Counted:
    """A report that says how many times it has actually been run."""

    def __init__(self, payload=None, raises=None):
        self.payload, self.raises, self.runs = payload, raises, 0

    def __call__(self):
        self.runs += 1
        if self.raises:
            raise self.raises
        return dict(self.payload)


ROWS = {"columns": ["Client"], "rows": [["Acme Plumbing"]], "note": "one row"}


# ---------------------------------------------------------------------------
section("Run once, read all day")
# ---------------------------------------------------------------------------
rc.invalidate()
build = Counted(ROWS)
first = rc.serve("qa:demo", build)
check("the first open runs it", build.runs == 1, build.runs)
check("...and says so", first["cache"]["ran"] is True, first["cache"])

for _ in range(5):
    again = rc.serve("qa:demo", build)
check("five more opens run it no further times", build.runs == 1, build.runs)
check("...and the rows are the same rows", again["rows"] == ROWS["rows"])
check("...marked as the stored copy rather than a fresh run",
      again["cache"]["from_cache"] is True and again["cache"]["ran"] is False)

check("the age is on the payload, so a page can print it",
      "line" in again["cache"] and again["cache"]["line"], again["cache"])
check("...and the line says when, not merely that it is cached",
      "Run at" in again["cache"]["line"], again["cache"]["line"])
check("...and offers the way to run it again",
      "Refresh" in again["cache"]["line"], again["cache"]["line"])


# ---------------------------------------------------------------------------
section("Refresh runs it again — and that is the only thing that does")
# ---------------------------------------------------------------------------
forced = rc.serve("qa:demo", build, force=True)
check("force re-runs the build", build.runs == 2, build.runs)
check("...and the reply says it ran rather than being read",
      forced["cache"]["ran"] is True and forced["cache"]["from_cache"] is False)
rc.serve("qa:demo", build)
check("and the run after a forced one is read again", build.runs == 2, build.runs)


# ---------------------------------------------------------------------------
section("A failed run never becomes the day's answer")
# ---------------------------------------------------------------------------
boom = Counted(raises=RuntimeError("QuickBooks refused"))
kept = rc.serve("qa:demo", boom, force=True)
check("the previous rows are served rather than a blank table",
      kept["rows"] == ROWS["rows"], kept.get("rows"))
check("...and the failure is named beside them",
      "QuickBooks refused" in kept["cache"]["error"], kept["cache"])
check("...in the line a page prints, not only in a field nobody reads",
      "QuickBooks refused" in kept["cache"]["line"], kept["cache"]["line"])
check("...and the stored copy is untouched",
      rc.read("qa:demo")["payload"]["rows"] == ROWS["rows"])

# Nothing kept at all: the exception is re-raised, so the caller sees its own
# report fail in its own shape. Substituting a payload here would be a guess —
# half these reports are a columns/rows table and half are not, and a caller
# handed the wrong one raises somewhere further along that says nothing about
# what went wrong.
rc.invalidate()
try:
    rc.serve("qa:cold", Counted(raises=RuntimeError("Knack timed out")))
    check("with nothing to fall back on the failure reaches the caller", False)
except RuntimeError as exc:
    check("with nothing to fall back on the failure reaches the caller",
          "Knack timed out" in str(exc))
check("...and nothing was written", rc.read("qa:cold") == {}, rc.read("qa:cold"))
check("...which the QA route turns into a rendered error, as it always did",
      'except Exception as exc:  # noqa: BLE001 — reports must degrade'
      in _read_early("hub/__init__.py"))


# ---------------------------------------------------------------------------
section("“We could not look” is not an answer either")
# ---------------------------------------------------------------------------
# Every one of these is a successful function call returning a well-formed
# payload. None of them measured anything, and storing one would leave the
# page saying it for the rest of the day.
for label, payload in (
    ("a report that errored", {"rows": [], "error": "Knack refused."}),
    ("a report that could not look",
     {"rows": [], "unavailable": {"message": "QuickBooks isn't connected yet"}}),
    ("a report that says it did not measure", {"rows": [], "measured": False}),
):
    check(label + " is not stored as the day's answer", not rc.is_answer(payload))
check("a report with no findings IS an answer — these are exception lists",
      rc.is_answer({"rows": [], "note": "nothing outstanding"}))

rc.invalidate()
notyet = Counted({"rows": [], "unavailable": {"message": "not connected"}})
rc.serve("qa:qb", notyet)
rc.serve("qa:qb", notyet)
check("so the report is asked again on the next open, not held for a day",
      notyet.runs == 2, notyet.runs)
check("...and the reader is told nothing was kept",
      "nothing was kept" in rc.serve("qa:qb", notyet)["cache"]["line"])


# ---------------------------------------------------------------------------
section("The day is the report's own day")
# ---------------------------------------------------------------------------
check("the key is today on the same clock every report measures from",
      rc.day_key() == _dt.date.today().isoformat())

rc.invalidate()
rolling = Counted(ROWS)
yesterday = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
rc.serve("qa:roll", rolling, today=yesterday)
check("a run yesterday counts as a run", rolling.runs == 1)
rc.serve("qa:roll", rolling)
check("...and does not answer for today", rolling.runs == 2, rolling.runs)

# Yesterday's rows still stand when today's run fails: a stale answer that
# says it is stale beats a blank one that says nothing.
rc.invalidate()
rc.serve("qa:roll2", Counted(ROWS), today=yesterday)
stale = rc.serve("qa:roll2", Counted(raises=RuntimeError("down")))
check("a failed run today falls back to yesterday's rows",
      stale["rows"] == ROWS["rows"])
check("...and the line says these are not today's",
      "has not succeeded" in stale["cache"]["line"], stale["cache"]["line"])


# ---------------------------------------------------------------------------
section("A search is not a cache key")
# ---------------------------------------------------------------------------
check("a month is", rc.cacheable("2026-07"))
check("a tickbox is", rc.cacheable("found=1"))
check("nothing at all is", rc.cacheable(""))
check("something a person typed is not", not rc.cacheable("acme plumbing"))
check("...nor is something long enough to be one", not rc.cacheable("x" * 61))

typed = Counted(ROWS)
a = rc.serve("qa:search", typed, params="acme plumbing")
rc.serve("qa:search", typed, params="acme plumbing")
check("so it runs every time rather than writing a file per keystroke",
      typed.runs == 2, typed.runs)
check("...and says as much rather than claiming an age it does not have",
      a["cache"]["cached"] is False, a["cache"])


# ---------------------------------------------------------------------------
section("One entry per report per parameter set")
# ---------------------------------------------------------------------------
rc.invalidate()
scored = Counted(ROWS)
rc.serve("qa:sales-scorecard", scored, params="2026-07")
rc.serve("qa:sales-scorecard", scored, params="2026-08")
rc.serve("qa:sales-scorecard", scored, params="2026-07")
check("two months are two entries, and neither re-runs",
      scored.runs == 2, scored.runs)
check("...held as two files, not two hundred", len(rc.entries()) == 2,
      rc.entries())

# The day lives inside the entry, not in its filename, so tomorrow's run
# replaces today's rather than joining a pile of them.
check("one report is one file whatever day it was run",
      rc.slug("qa:demo") == rc.slug("qa:demo"))


# ---------------------------------------------------------------------------
section("A write drops what it changed")
# ---------------------------------------------------------------------------
rc.invalidate()
one, two = Counted(ROWS), Counted(ROWS)
rc.serve("qa:invoice-off", one)
rc.serve("qa:active-clients", two)
rc.invalidate("qa:invoice-off")
rc.serve("qa:invoice-off", one)
rc.serve("qa:active-clients", two)
check("the report that was written to runs again", one.runs == 2, one.runs)
check("...and the one that was not is untouched", two.runs == 1, two.runs)

rc.invalidate("qa:")
rc.serve("qa:active-clients", two)
check("a prefix drops every report under it", two.runs == 2, two.runs)


# ---------------------------------------------------------------------------
section("Nothing in the inspection panel carries a client name")
# ---------------------------------------------------------------------------
rc.invalidate()
rc.serve("qa:demo", Counted(ROWS))
held = rc.state()
check("it says what is held", held["count"] == 1, held)
check("...and how old each is", held["entries"][0]["age_minutes"] == 0)
check("...and how many rows, without carrying any of them",
      held["entries"][0]["rows"] == 1
      and "Acme Plumbing" not in repr(held), held)


# ---------------------------------------------------------------------------
section("Switching it off is one variable")
# ---------------------------------------------------------------------------
os.environ["REPORT_CACHE"] = "off"
live = Counted(ROWS)
off = rc.serve("qa:demo", live)
rc.serve("qa:demo", live)
check("every open runs the report", live.runs == 2, live.runs)
check("...and the page is told it is not cached rather than shown a false age",
      off["cache"]["cached"] is False
      and "runs on every open" in off["cache"]["line"], off["cache"])
os.environ.pop("REPORT_CACHE")


# ---------------------------------------------------------------------------
section("The QA reports go through it, and the month is part of the key")
# ---------------------------------------------------------------------------
from hub import qa                                              # noqa: E402

check("a plain report keys on itself alone",
      qa.cache_key("active-clients") == ("qa:active-clients", ""))
check("a scorecard keys on the month it was asked for",
      qa.cache_key("sales-scorecard", "2026-07")
      == ("qa:sales-scorecard", "2026-07"))
check("...and a month on a report that ignores it is not part of the key",
      qa.cache_key("active-clients", "2026-07") == ("qa:active-clients", ""),
      qa.cache_key("active-clients", "2026-07"))
check("every report in the registry has a cache key",
      all(qa.cache_key(k)[0].startswith("qa:") for k in qa.REPORTS))

rc.invalidate()
calls = []
_real_run = qa.run
qa.run = lambda key, month="": (calls.append(key),
                                {"columns": [], "rows": [], "key": key})[1]
try:
    qa.run_cached("active-clients")
    qa.run_cached("active-clients")
    check("opening a QA report twice runs it once", len(calls) == 1, calls)
    qa.run_cached("active-clients", force=True)
    check("...and Refresh runs it again", len(calls) == 2, calls)
finally:
    qa.run = _real_run


# ---------------------------------------------------------------------------
section("A GET reads; a POST re-runs")
# ---------------------------------------------------------------------------
try:
    from werkzeug.test import Client as WClient

    import wsgi
    composed = WClient(wsgi.application)
    composed.post("/login", data={"password": os.environ["PANEL_PASSWORD"],
                                  "name": "T"})

    rc.invalidate()
    ran = []
    qa.run = lambda key, month="": (ran.append(key),
                                    {"columns": ["A"], "rows": [["x"]]})[1]
    try:
        r1 = composed.get("/api/qa/active-clients")
        check("GET /api/qa/<key> answers", r1.status_code == 200)
        composed.get("/api/qa/active-clients")
        composed.get("/api/qa/active-clients")
        check("...and three GETs run the report once", len(ran) == 1, ran)
        check("...with the cache block on the reply so the page can print it",
              bool(r1.get_json().get("cache", {}).get("line")), r1.get_json())

        r2 = composed.post("/api/qa/active-clients/refresh")
        check("POST .../refresh answers", r2.status_code == 200)
        check("...and re-runs the report", len(ran) == 2, ran)
        check("...and says it ran rather than was read",
              r2.get_json()["cache"]["ran"] is True)

        # A GET must not be able to force it, whatever is on the query string:
        # that is the door a prefetch walks through.
        before = len(ran)
        composed.get("/api/qa/active-clients?refresh=1&force=1")
        check("a GET cannot force a re-run however it is spelled",
              len(ran) == before, ran)
        check("the refresh route refuses a GET",
              composed.get("/api/qa/active-clients/refresh").status_code == 405)
    finally:
        qa.run = _real_run

    check("an unknown report is still a 404, not an empty cached one",
          composed.get("/api/qa/no-such-report").status_code == 404)

    # The panel that says what is held, and the button that empties it.
    check("/api/report-cache answers",
          composed.get("/api/report-cache").status_code == 200)
    check("...and clearing it is a POST",
          composed.get("/api/report-cache/clear").status_code == 405)
    check("...which empties it",
          composed.post("/api/report-cache/clear").get_json()["ok"] is True
          and rc.state()["count"] == 0)
except Exception as exc:                                        # noqa: BLE001
    check("the composed app boots with these routes", False, exc)


# ---------------------------------------------------------------------------
section("Every page that shows a cached report prints its age")
# ---------------------------------------------------------------------------
# A cached figure with no date on it is read as today's. This is the check
# that stops a report being cached without the reader being told.
def _read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


qa_page = _read("hub/templates/qa_report.html")
check("the QA report page renders the cache line",
      "cache" in qa_page and "qaAge" in qa_page)
check("...and its Refresh button posts rather than getting",
      "'/refresh'" in qa_page.replace('"', "'")
      or "/refresh" in qa_page and "method:'POST'" in qa_page.replace('"', "'"),
      "no POST refresh found")

stale_page = _read("hub/templates/stale_creative.html")
check("the Stale Creative page renders the cache line",
      "data.cache" in stale_page, "no cache line")
check("...and its Refresh is a POST, not a ?refresh=1 link",
      'method="post"' in stale_page and 'href="?refresh=1"' not in stale_page,
      "still a GET refresh")
check("...and the server will not re-run on a GET either",
      'request.args.get("refresh")' not in _read("hub/stale_creative.py"),
      "?refresh=1 still re-runs the scan")


# ---------------------------------------------------------------------------
section("A write path drops the reports it changes")
# ---------------------------------------------------------------------------
# Each of these is a row leaving a report. Asserted by reading the source
# rather than by running the write, because the write reaches Knack — but the
# thing that goes wrong is somebody adding a write and forgetting the drop,
# and that is visible from here.
for path, needle, why in (
    ("hub/qa.py", "forget(\"no-dashboards\", \"active-clients\")",
     "skipping a client takes it off No Dashboards"),
    ("hub/qa.py", "forget(\"invoice-off\")",
     "assigning a partner takes the row off Invoice Off"),
    ("hub/qa.py", "forget(\"accounting-requests\")",
     "moving an accounting request takes it out of that stage"),
    ("hub/google_index.py", "_forget_reports()",
     "a sweep or an attachment changes the Google reports"),
    ("hub/knack_products.py", "report_cache.invalidate(",
     "a fresh product pull is when the client reports stopped being true"),
    ("hub/domain_links.py", "report_cache.invalidate(",
     "an attached domain leaves the orphan list"),
    ("hub/client_urls.py", "report_cache.invalidate(",
     "an accepted URL leaves the missing list"),
    ("hub/sites_match.py", "report_cache.invalidate(",
     "a matched project leaves the match list"),
):
    check(f"{path}: {why}", needle in _read(path), needle)

check("google_index drops the analytics audit too — it reads the same index",
      "qa:analytics-ids" in _read("hub/google_index.py"))


# ---------------------------------------------------------------------------
section("The two reports that filter after they build do exactly that")
# ---------------------------------------------------------------------------
# Splitting them is what keeps a search box from writing a cache file per
# keystroke while still holding the expensive read for the day.
dl = _read("hub/domain_links.py")
check("the orphan reading is cached and the search runs over it",
      "report_cache.serve(\"orphan-urls\"" in dl and "def _read_orphans" in dl)
gl = _read("hub/google_links.py")
check("the Google orphan book is cached and paged over it",
      "report_cache.serve(\"google-orphans\"" in gl and "def _orphan_book" in gl)
check("...and the book says when it was built, apart from when Google was swept",
      '"cache": book.get("cache")' in gl)


# ---------------------------------------------------------------------------
print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
