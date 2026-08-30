"""Every QA report, and the page that draws one — test harness.

    python3 test_qa_reports.py

Same shape as the other test files here: no pytest, no new dependencies, a
throwaway SQLite database and a temporary data directory, so it never touches
/var/data or the real one. Nothing here reaches a third party: the reports are
run with no Knack, no QuickBooks and no GoHighLevel configured, which is
exactly the state in which they have to answer honestly.

## What is worth asserting

`/qa` is eighteen reports and eight whole tools, and every one of them is read
by somebody deciding what to do this week. Two things can go wrong with one,
and only the first announces itself:

  * **It raises.** `/api/qa/<key>` catches that into `{"error": …}`, so the
    page says so and nobody is misled. Asserted anyway, per report, because a
    report that has started raising is a report nobody is reading.

  * **It answers confidently and wrongly.** This is the one worth a file.
    Every source in these reports degrades to an empty list rather than
    raising, so a morning where Knack, the lead store and the scans table all
    refused produces a *complete-looking* page. `hub/report_cache.py` already
    refuses to store such a run as the day's answer — but only if the report
    said `measured: False`, and only if the page then draws it as such.

### The green tick over the sentence that says otherwise

`qa_report.html` handled `error`, `needs_qb` and `unavailable`, and fell
through to **"Nothing to report — all clear ✓"** for everything else. Four
returns carried the refusal in `note` alone:

    hub/upsell.py            the client list, or the site audits, refused
    hub/prospect_queue.py    the lead store refused
    hub/qa.py (×2)           the uploads gallery database would not open

So a Knack timeout drew a green tick over the report's own sentence reading
*"which is not the same as nobody"*, and *"Couldn't read the uploads
database"* was rendered as every client's files being safely in Suite. The
page's own comment had said for three of those what the code did not do:
*"'We looked and it is fine' and 'we could not look' are different answers,
and rendering both as a green tick is how a page ends up confidently telling
you the opposite of the truth."*

Both halves are asserted, in both directions, because over-correcting is its
own failure: a report that genuinely found nothing must keep its green tick,
or the page cries wolf on every clean run and people stop reading it.

**This is a sweep, not a list of the four that were wrong.** A test naming
those four proves nothing about the fifth. `_empty_returns_without_a_signal()`
reads the **AST** of every module a report is built from and requires any
return of no rows to carry something the page actually draws — so a report
added next month cannot reintroduce the silence without failing the run. It
reads the AST rather than the text because three of these modules explain this
very trap in prose, and a check that matches text reports the explanation as
the defect.

And the branch itself is **lifted out of the page rather than restated**, the
arrangement `test_menu_layout.py` uses: a copy of the rule in this file is a
third thing to keep in step, and it would go on passing after somebody edited
the template.
"""
import ast
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-qareports-")
# Set, not setdefault: this file always gets its own throwaway mirror, so it is
# safe to re-run in a job whose DATABASE_URL is already a real Postgres.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["AUDIT_LOG_PATH"] = os.path.join(_TMP, "activity.jsonl")
# The reports are run directly here, and a day-long cache would mean the second
# assertion about a report read the first one's answer.
os.environ["REPORT_CACHE"] = "off"
os.environ.setdefault("SECRET_KEY", "qa-reports-test")
os.environ.setdefault("PANEL_PASSWORD", "test")

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f"  — {detail}" if detail else ""))


def section(title):
    print("\n" + title)
    print("-" * len(title))


from hub import qa                                              # noqa: E402

PAGE = pathlib.Path(ROOT, "hub", "templates", "qa_report.html").read_text()

# The modules a REPORTS entry is actually built from. Kept beside the sweep
# rather than derived, because a report is a thin wrapper here and the return
# that matters is in the module behind it.
REPORT_SOURCES = ["hub/qa.py", "hub/upsell.py", "hub/prospect_queue.py",
                  "hub/io_reconcile.py", "hub/sites_billing.py"]

# What the page will actually draw instead of the all-clear. `measured` is the
# general one; the other three are the specific panels that came first.
DRAWN_SIGNALS = {"error", "unavailable", "needs_qb", "measured"}


# ---------------------------------------------------------------------------
section("Every report answers, and answers something a route can return")
# ---------------------------------------------------------------------------
# No provider is configured, which is the state that produces a confident
# empty. Each of these must still come back as a table the page can draw.
for key in qa.REPORTS:
    try:
        out = qa.run(key)
        raised = ""
    except Exception as exc:                                    # noqa: BLE001
        out, raised = None, f"{type(exc).__name__}: {exc}"
    check(f"{key} does not raise", not raised, raised)
    if out is None:
        continue
    check(f"{key} returns columns and rows the page can draw",
          isinstance(out.get("columns"), list) and isinstance(out.get("rows"), list),
          f"columns={type(out.get('columns')).__name__} "
          f"rows={type(out.get('rows')).__name__}")
    # /api/qa/<key> jsonifies this. A value json cannot encode is a 500 on a
    # route whose whole contract is degrading gracefully.
    try:
        json.dumps(out)
        encodable = True
    except Exception as exc:                                    # noqa: BLE001
        encodable, raised = False, f"{type(exc).__name__}: {exc}"
    check(f"{key} is JSON-encodable", encodable, raised)


# ---------------------------------------------------------------------------
section("A report that could not look never renders as all clear")
# ---------------------------------------------------------------------------
def _empty_returns_without_a_signal():
    """Returns of no rows carrying nothing the page draws, by AST.

    Text would report the paragraph above, and the ones in `hub/upsell.py` and
    `hub/prospect_queue.py` explaining this trap, as defects themselves.
    """
    out = []
    for path in REPORT_SOURCES:
        src = pathlib.Path(ROOT, path).read_text()
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
                continue
            pairs = {k.value: v for k, v in zip(node.value.keys, node.value.values)
                     if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            rows = pairs.get("rows")
            # Only a literally empty row list. A returned name may hold rows.
            if not (isinstance(rows, ast.List) and not rows.elts):
                continue
            if DRAWN_SIGNALS & set(pairs):
                continue
            out.append(f"{path}:{node.lineno}")
    return out


unsignalled = _empty_returns_without_a_signal()
check("no report returns an empty table carrying nothing the page draws",
      unsignalled == [], ", ".join(unsignalled))

check("the page still explains why the two answers must differ",
      "could not look" in PAGE and "green tick" in PAGE)


# ---------------------------------------------------------------------------
section("The page's own branch, run in node")
# ---------------------------------------------------------------------------
# Lifted out of qa_report.html between its own markers rather than restated,
# and rather than matched as an exact line: a copy here is a third thing to
# keep in step, and a regex pinned to one line's formatting fails the day
# somebody reindents it — a check that cries wolf is a check somebody switches
# off. Same arrangement as test_menu_layout.py over hub-crumbs.js.
_start = PAGE.find("/* ---- cannot-look decision")
_end = PAGE.find("/* ---- end cannot-look decision")
check("the cannot-look block is still marked for lifting",
      _start > 0 and _end > _start,
      "qa_report.html no longer marks the block this runs")
GUARD = PAGE[_start:_end] if (_start > 0 and _end > _start) else None

if GUARD is not None:
    # The branch order render() applies, with the real block dropped in.
    JS = GUARD + """
    function verdict(d){
      if(d.error) return 'error';
      if(d.needs_qb) return 'quickbooks';
      if(cannotLook(d)) return 'not-measured';
      if(!d.rows || !d.rows.length) return 'all-clear';
      return 'table';
    }
    const cases = JSON.parse(process.argv[1]);
    console.log(JSON.stringify(cases.map(c => verdict(c[1]))));
    """

    CASES = [
        # The four that used to draw a green tick over their own refusal.
        ("the client list refused",
         {"columns": ["Client"], "rows": [], "measured": False,
          "note": "The client list could not be read (RuntimeError)."},
         "not-measured"),
        ("the lead store refused",
         {"columns": ["Prospect"], "rows": [], "measured": False,
          "note": "The lead store could not be read — not the same as nobody."},
         "not-measured"),
        ("the uploads gallery would not open",
         {"columns": ["Client"], "rows": [], "measured": False,
          "note": "Couldn't read the uploads database (OperationalError)."},
         "not-measured"),
        ("the site audits refused",
         {"columns": ["Client"], "rows": [], "measured": False,
          "note": "The site audits could not be read."},
         "not-measured"),
        # The panels that already worked, which the change must not disturb.
        ("a report carrying `unavailable` keeps its own panel",
         {"columns": [], "rows": [],
          "unavailable": {"message": "The index has not been built yet.",
                          "action_post": "/api/google/rebuild"}},
         "not-measured"),
        ("QuickBooks not connected still gets its own call to action",
         {"columns": [], "rows": [], "needs_qb": True,
          "note": "QuickBooks isn't configured."},
         "quickbooks"),
        ("a report that raised still shows the error",
         {"columns": [], "rows": [], "error": "GHL_COMPANY_ID is not configured."},
         "error"),
        # Over-correcting is its own failure: a page that cries wolf on every
        # clean run is one people stop reading.
        ("a report that genuinely found nothing keeps its green tick",
         {"columns": ["Client"], "rows": [], "measured": True,
          "note": "Every uploaded file has reached Smart 1 Suite."},
         "all-clear"),
        ("a report with no `measured` key at all keeps its green tick",
         {"columns": ["Client"], "rows": [],
          "note": "Nothing is waiting."},
         "all-clear"),
        ("rows are still drawn as a table",
         {"columns": ["Client"], "rows": [["Acme"]], "measured": True},
         "table"),
    ]

    payload = json.dumps([[c[0], c[1]] for c in CASES])
    proc = subprocess.run(["node", "-e", JS, payload],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        check("the lifted branch runs in node", False,
              (proc.stderr or "").strip()[:200])
    else:
        got = json.loads(proc.stdout)
        for (label, _, want), have in zip(CASES, got):
            check(label, have == want, f"drew {have}, wanted {want}")


# ---------------------------------------------------------------------------
section("The reports that say they could not look actually say so")
# ---------------------------------------------------------------------------
# The end-to-end half: make each source fail the way it fails in production
# and read the payload the route would hand the page.
from hub import leads, prospect_queue, upsell                   # noqa: E402


def _boom(*_a, **_k):
    raise RuntimeError("Knack timed out")


_orig = qa._client_groups                                       # noqa: SLF001
qa._client_groups = _boom                                       # noqa: SLF001
try:
    sell = upsell.build()
finally:
    qa._client_groups = _orig                                   # noqa: SLF001
check("sell-to-clients says it is not measured when the client list refuses",
      sell.get("measured") is False and not sell.get("rows"), sell.get("measured"))
check("and it says why", "could not be read" in (sell.get("note") or ""),
      sell.get("note"))

_orig2 = leads.listing
leads.listing = _boom
try:
    queue = prospect_queue.build()
finally:
    leads.listing = _orig2
check("prospect-queue says it is not measured when the lead store refuses",
      queue.get("measured") is False and not queue.get("rows"),
      queue.get("measured"))
check("and it says why", "could not be read" in (queue.get("note") or ""),
      queue.get("note"))

# `hub/report_cache.py` reads the same flag, so an unmeasured run is never
# frozen into the day's answer — connecting the source an hour later is not
# lost until tomorrow.
from hub import report_cache                                    # noqa: E402
check("report_cache does not treat an unmeasured run as an answer",
      report_cache.is_answer(sell) is False, report_cache.is_answer(sell))
check("and it does treat a measured one as an answer",
      report_cache.is_answer({"columns": [], "rows": [], "measured": True}) is True)

# The QuickBooks half of the same statement. `report_cache.py`'s own docstring
# names it -- "what stops 'QuickBooks isn't connected yet' being cached for a
# day by whoever opened the report before anyone connected it" -- and
# `is_answer()` never read the key. Somebody who opened Invoice Off at 08:50,
# before the connect at 09:10, left the call to action on all three billing
# reports for everybody until tomorrow.
check("a QuickBooks call to action is not the day's answer either",
      report_cache.is_answer(
          {"columns": [], "rows": [], "needs_qb": True,
           "note": "QuickBooks isn't configured."}) is False)

_qb_calls = {"n": 0}


def _qb_build():
    _qb_calls["n"] += 1
    if _qb_calls["n"] == 1:                     # nobody has connected it yet
        return {"columns": ["Customer"], "rows": [], "needs_qb": True,
                "note": "QuickBooks isn't configured."}
    return {"columns": ["Customer"], "rows": [["Acme"]], "measured": True}


# The cache is off for the rest of this file, so it has to be switched on for
# these two serve() calls — with it off, `serve()` never stores anything and
# the assertion would pass on the broken code too.
os.environ.pop("REPORT_CACHE")
try:
    assert report_cache.enabled()
    report_cache.serve("qa:test-billing", _qb_build)
    _after = report_cache.serve("qa:test-billing", _qb_build)

    # The control: a real answer *is* held, so the assertion below is about
    # `needs_qb` and not about the cache being inert.
    _ok_calls = {"n": 0}

    def _ok_build():
        _ok_calls["n"] += 1
        return {"columns": ["Customer"], "rows": [["Held"]], "measured": True}

    report_cache.serve("qa:test-holding", _ok_build)
    report_cache.serve("qa:test-holding", _ok_build)
finally:
    os.environ["REPORT_CACHE"] = "off"

check("the cache really is holding measured answers in this run",
      _ok_calls["n"] == 1, f"built {_ok_calls['n']} times")
check("so connecting QuickBooks is not lost until tomorrow",
      _after.get("rows") == [["Acme"]] and _qb_calls["n"] == 2,
      f"rows={_after.get('rows')} builds={_qb_calls['n']}")


# ---------------------------------------------------------------------------
section("A report does not describe a window it does not use")
# ---------------------------------------------------------------------------
# stale_90() stops at 180 days, deliberately and with the reason beside it,
# and told the reader "(up to 24 months back)" — so the 168 clients quiet for
# longer read as checked and not lapsed.
_stale_note = qa.run("stale-90").get("note") or ""
check("stale-90 does not claim a 24-month window",
      "24 months" not in _stale_note, _stale_note[:120])
check("and it says the window it actually uses",
      "90 and 180 days" in _stale_note, _stale_note[:120])


# ---------------------------------------------------------------------------
section("Every QA tile points at something that answers")
# ---------------------------------------------------------------------------
# Six of the eight EXTRAS are whole tools rather than table-returning
# functions. A tile pointing at a page that 404s is the failure this repo
# counts six of, and it is invisible from the report side.
import wsgi                                                     # noqa: E402
from hub import auth                                            # noqa: E402
from werkzeug.test import Client                                # noqa: E402

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("QA test"),
                 domain="localhost")
for group, key, meta in qa.EXTRAS:
    href = meta["href"]
    code = staff.get(href, follow_redirects=False).status_code
    check(f"{key} answers at {href}", code < 400, f"HTTP {code}")

for key in qa.REPORTS:
    code = staff.get(f"/qa/{key}", follow_redirects=False).status_code
    check(f"/qa/{key} renders", code < 400, f"HTTP {code}")


# ---------------------------------------------------------------------------
print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
