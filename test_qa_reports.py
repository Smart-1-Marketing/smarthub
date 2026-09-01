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
import re
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
os.environ["CLIENTS_DATA_DIR"] = os.path.join(ROOT, "tests", "fixtures", "clients")
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

PAGE = pathlib.Path(ROOT, "hub", "templates", "qa_report.html").read_text(encoding="utf-8")

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
        src = pathlib.Path(ROOT, path).read_text(encoding="utf-8")
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
section("The Scorecards measure 'running' the way the rest of the page does")
# ---------------------------------------------------------------------------
# The two Scorecards tested `status in ("live", "complete")` — the narrow test
# `knack_data.is_running()`'s own docstring says "missed about a third of the
# work actually running" — while every other report on /qa counted the union.
# Nothing on either said they were measured differently, which is what made it
# the /api/db/structure versus /api/integrity trap rather than a disagreement
# somebody could see.
import datetime as _dt                                          # noqa: E402

from hub import knack_data                                      # noqa: E402

_MS, _ME = _dt.date(2026, 8, 1), _dt.date(2026, 8, 31)


def _row(status, start="07/01/2026", end="09/30/2026"):
    return {"status": status, "start": start, "end": end}


for label, rec, want in [
    ("a Live row in term counts", _row("Live"), True),
    ("a Complete row in term counts — it ran, even though it is finished",
     _row("Complete"), True),
    ("so do the in-flight statuses the old allowlist dropped",
     _row("Pending Assets"), True),
    ("...and Assigned", _row("Assigned"), True),
    ("...and Scheduled", _row("Scheduled"), True),
    ("...and Paused", _row("Paused"), True),
    ("a Cancelled row in term counts, as it does everywhere else",
     _row("Cancelled"), True),
    ("a Revised row never counts — its replacement carries the numbers",
     _row("Revised"), False),
    ("a row whose term ended before the month does not count",
     _row("Live", "01/01/2026", "03/31/2026"), False),
    ("nor one that starts after it", _row("Live", "11/01/2026", "12/31/2026"), False),
    ("a row with no dates is in no month at all",
     {"status": "Live"}, False),
]:
    got = knack_data.ran_in_month(rec, _MS, _ME)
    check(label, got is want, f"got {got!r}, wanted {want!r}")

# The report must not keep a second copy of the rule -- read by AST, because
# `_active_in_month`'s own docstring quotes the old allowlist to explain the
# fix, and a check that matches text reports the explanation as the defect.
_qa_tree = ast.parse(pathlib.Path(ROOT, "hub", "qa.py").read_text(encoding="utf-8"))
_fn = next((n for n in ast.walk(_qa_tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_active_in_month"),
           None)
check("_active_in_month is still there", _fn is not None)
if _fn is not None:
    _body = [n for n in _fn.body if not (isinstance(n, ast.Expr)
                                         and isinstance(n.value, ast.Constant))]
    check("it does nothing but hand the question to the shared rule",
          len(_body) == 1 and isinstance(_body[0], ast.Return), ast.dump(_fn)[:120])
    _called = {n.func.id for n in ast.walk(_fn)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    check("and the rule it hands it to is knack_data's",
          "_knack_ran_in_month" in _called, sorted(_called))
    _strings = {n.value for n in ast.walk(_fn)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n is not _fn.body[0].value}
    check("with no status allowlist left behind",
          not ({"live", "complete"} & {str(x).lower() for x in _strings}),
          sorted(_strings))

# The invariant that makes the two agree: anything delivering today ran in the
# month containing today. The one documented exception is a row with no dates
# -- is_running() accepts it on the strength of a Live status, and a month test
# cannot place it.
_today = _dt.date.today()
_tms, _tme = qa._month_bounds(_today.strftime("%Y%m"))
_export_rows = knack_data.products()
_disagree = [
    r for r in _export_rows
    if knack_data.is_running(r)
    and not knack_data.ran_in_month(r, _tms, _tme)
    and (r.get("start") or r.get("end"))
]
check("everything delivering today counts for this month",
      len(_disagree) == 0,
      f"{len(_disagree)} running row(s) fall outside this month")

# The export's thisM/lastM flags describe the month when the export was made,
# not forever "today". Use the export's declared period rather than inferring
# it from date overlap: an insertion order can span both August and September,
# so multiple candidate months can legitimately describe the same flag set.
# A broken month rule still fails because both flag sets must match the
# declared month and its predecessor exactly.
_export_flag_ym = knack_data.export_state()["period"]
check("the products export declares its scorecard month",
      len(_export_flag_ym) == 6 and _export_flag_ym.isdigit(),
      _export_flag_ym)


def _export_flag_delta(ym):
    this_start, this_end = qa._month_bounds(ym)
    last_start, last_end = qa._month_bounds(qa._prev_ym(ym))
    flagged_this = {id(r) for r in _export_rows if r.get("thisM")}
    flagged_last = {id(r) for r in _export_rows if r.get("lastM")}
    computed_this = {id(r) for r in _export_rows
                     if qa._active_in_month(r, this_start, this_end)}
    computed_last = {id(r) for r in _export_rows
                     if qa._active_in_month(r, last_start, last_end)}
    return len(flagged_this ^ computed_this) + len(flagged_last ^ computed_last)


_export_flag_disagreement = _export_flag_delta(_export_flag_ym)
check("the export flags identify one coherent month pair",
      _export_flag_disagreement == 0,
      f"{_export_flag_disagreement} flag rows disagree")

# And every salesperson with work in the configured export appears on its
# scorecard. Deriving the names keeps the regression meaningful with the
# sanitized fixture and after that fixture is refreshed.
_sc = qa.run("sales-scorecard", _export_flag_ym)
_names = " ".join(str(c) for row in _sc["rows"] for c in row)
_export_start, _export_end = qa._month_bounds(_export_flag_ym)
_expected_sales = sorted({str(r.get("sales") or "").strip()
                          for r in _export_rows
                          if str(r.get("sales") or "").strip()
                          and qa._active_in_month(r, _export_start, _export_end)})
check("the scorecard fixture includes an active salesperson",
      bool(_expected_sales), _expected_sales)
for _who in _expected_sales:
    check(f"{_who} has live work and appears on the Scorecard",
          _who in _names, True)


# ---------------------------------------------------------------------------
section("A products export that could not be read is not a book with nobody in it")
# ---------------------------------------------------------------------------
# `knack_data._load()` swallows OSError and returns None, so a missing,
# unreadable or malformed products.json yields [] -- and to a caller that is
# indistinguishable from a client base with nobody on it. Six client reports
# and both Scorecards then rendered a clean empty table: "every client has a
# dashboard, nobody has lapsed, nobody is missing Analytics, nobody churned",
# and `report_cache.is_answer()` stored it as the day's answer, frozen until
# tomorrow, on a source that was never read.
#
# Which reports are built on that source is read from the AST rather than
# listed, so one added next month is swept without anybody remembering. The
# closure is over qa.py's own calls, because `salesperson_scorecard` reaches
# `_month_rollup` through `_scorecard`.
_QA_TREE = ast.parse(pathlib.Path(ROOT, "hub", "qa.py").read_text(encoding="utf-8"))
_QA_FNS = {n.name: n for n in ast.walk(_QA_TREE)
           if isinstance(n, ast.FunctionDef)}
_PRODUCT_READERS = {"_client_groups", "_month_rollup"}


def _reads_products(name, seen=None):
    """Does this qa.py function reach the products export, at any depth?"""
    seen = seen or set()
    if name in seen or name not in _QA_FNS:
        return False
    seen.add(name)
    for node in ast.walk(_QA_FNS[name]):
        if not isinstance(node, ast.Call):
            continue
        called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if called in _PRODUCT_READERS:
            return True
        if called and _reads_products(called, seen):
            return True
    return False


_product_backed = sorted(k for k, meta in qa.REPORTS.items()
                         if _reads_products(meta["fn"].__name__))
check("the sweep found the reports built on the products export",
      len(_product_backed) >= 8, _product_backed)

_real_base = knack_data.BASE
knack_data.BASE = os.path.join(_TMP, "no-such-export")
knack_data._cache.clear()
try:
    # Asked without assuming the helper exists: on the code this was written
    # against it did not, and an AttributeError here would crash the file
    # before the sweep below could name a single report -- a failure that
    # reports nothing is barely better than a green run.
    _src_state = getattr(knack_data, "products_error", lambda: "")()
    check("the source says it could not be read",
          "could not be read" in _src_state,
          f"products_error() -> {_src_state!r}")
    _all_clear = []
    for _key in _product_backed:
        _out = qa.run(_key)
        # What the page would draw: anything but a green tick is acceptable
        # here -- `cannotLook()` reads `measured`, and a report with its own
        # error or call to action is already saying it could not look.
        _drawn_as_all_clear = (
            not _out.get("error") and not _out.get("needs_qb")
            and not _out.get("unavailable") and _out.get("measured") is not False
            and not _out.get("rows"))
        if _drawn_as_all_clear:
            _all_clear.append(_key)
        # The invariant is "never a green tick", not "always `measured:
        # False`". Two of these reach a provider before they reach the export
        # and say so first -- with GHL and QuickBooks unconfigured here, that
        # is what they answer, and it is a true statement about why they could
        # not look. Asserting the flag would pass or fail on which providers
        # this environment happens to have.
        check(f"{_key} never draws a green tick on an unreadable export",
              not _drawn_as_all_clear,
              f"rows={len(_out.get('rows') or [])} measured={_out.get('measured')!r}")
        check(f"{_key} is not stored as the day's answer",
              report_cache.is_answer(_out) is False)
    check("and not one of them renders as a green tick", _all_clear == [],
          _all_clear)
finally:
    knack_data.BASE = _real_base
    knack_data._cache.clear()

# ...and with the export readable they answer exactly as before. Over-correcting
# is its own failure: a page that cries wolf on every clean run is one people
# stop reading.
check("a readable export is not reported as unmeasured",
      knack_data.products_error() == "", knack_data.products_error())
check("and the reports draw their tables again",
      all(qa.run(k).get("measured") is not False for k in _product_backed))


# ---------------------------------------------------------------------------
section("Invoice Off matches a customer to a client exactly, or not at all")
# ---------------------------------------------------------------------------
# It used to fall through to `next(... if norm in n or n in norm)` -- an
# unbounded substring, both directions, first out of a dict ordered by the
# export. That is the rule `hub/client_key.py` exists to refuse, and it is
# live: 32 of this deployment's 547 client names contain or are contained by
# another, and "cirilla s" alone matches 18.
#
# It failed in both directions. Forward, a QuickBooks customer was compared
# against whichever candidate came first and the variance printed with no sign
# a guess had been made. Backward -- and this is the worse one -- an active
# client with live billing and *no invoice at all* dropped off the report the
# moment any customer name merely contained theirs.
_QB_KEY = qa._month_keys(1)[0]

_BOOK = {
    "Acme Plumbing": {"live": [1], "live_total": 1000.0, "rows": [],
                      "thisM": True, "lastM": True, "this_total": 1000.0,
                      "last_total": 1000.0, "has_dash": True, "last_end": None,
                      "partners": set(), "sales": set()},
    "Acme": {"live": [1], "live_total": 400.0, "rows": [],
             "thisM": True, "lastM": True, "this_total": 400.0,
             "last_total": 400.0, "has_dash": True, "last_end": None,
             "partners": set(), "sales": set()},
}


class _FakeQB:
    def __init__(self, customers):
        self._c = customers

    def monthly_totals_by_customer(self, _n):
        return {k: {"id": "", "months": {_QB_KEY: v}} for k, v in self._c.items()}

    def customer_link(self, _i):
        return ""


def _invoice_off(customers, book=None):
    _real = (qa._qb_state, qa.invoice_assignments, qa._client_groups)
    qa._qb_state = lambda: (_FakeQB(customers), "")
    qa.invoice_assignments = lambda: {}
    qa._client_groups = lambda: dict(book if book is not None else _BOOK)
    try:
        return qa.invoice_off()
    finally:
        qa._qb_state, qa.invoice_assignments, qa._client_groups = _real


def _cell(row, i):
    c = row[i]
    return c.get("text") if isinstance(c, dict) else c


# An exact name is matched and its variance computed, exactly as before.
_out = _invoice_off({"Acme Plumbing": 250.0})
_acme = [r for r in _out["rows"] if _cell(r, 0) == "Acme Plumbing"]
check("an exact name is still matched", len(_acme), 1)
check("and its variance is computed against that client",
      _cell(_acme[0], 2) if _acme else None, "$1,000")

# A customer resembling clients is printed and counted as unmatched -- the
# rule sites_billing and domain_renewals both work to.
_out = _invoice_off({"Acme Plumbing Supply": 900.0})
_res = [r for r in _out["rows"] if _cell(r, 0) == "Acme Plumbing Supply"]
check("a customer that only resembles a client is still listed", len(_res), 1)
check("with no difference, because there is no client to compute one against",
      _cell(_res[0], 2) if _res else None, "—")
check("and the row says which clients it resembles",
      "resembles" in _cell(_res[0], 3) if _res else False, True)
check("it is counted as a resemblance, not a match", _out.get("resembled"), 1)
check("and it never claims a variance against a guessed client",
      all("▼" not in str(_cell(r, 3)) and "▲" not in str(_cell(r, 3))
          for r in _out["rows"] if _cell(r, 0) == "Acme Plumbing Supply"), True)

# The direction that hid findings: a client invoiced under no name of its own
# must still appear, with the resembling customer named on the row.
_out = _invoice_off({"Acme": 400.0})
_hidden = [r for r in _out["rows"] if _cell(r, 0) == "Acme Plumbing"]
check("a client whose name is merely contained by a customer is not hidden",
      len(_hidden), 1)
check("and the row names the customer rather than silencing the finding",
      "Acme" in _cell(_hidden[0], 3) if _hidden else False, True)
check("a genuinely uninvoiced client still reads as no invoice found",
      any("no invoice found" in str(_cell(r, 3))
          for r in _invoice_off({})["rows"]), True)

# row_styles is indexed positionally by the page, so it must stay in step.
_out = _invoice_off({"Acme Plumbing Supply": 900.0, "Acme": 400.0})
check("row_styles stays in step with rows",
      len(_out.get("row_styles") or []), len(_out.get("rows") or []))


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
section("A section heading is not a client, and the CSV was exporting it as one")

# Two spellings of "this row is a section heading" on one page: three reports
# mark the *cell* `{"group": true, "tone": …}` and the renderer draws a
# coloured band; `no_gtm` wrote a bare string and marked the *row*
# `row_styles="sub"`, which drew grey text. Same concept, two treatments, two
# reports apart -- and neither of them legible to the CSV export, which wrote
# all eight of this page's headings out as data. Active Clients exported 154
# rows for a book of 151, three of them named "Ending this month (15)" with
# every other column blank.

_headings = {}
for _key in qa.REPORTS:
    try:
        _r = qa.run(_key)
    except Exception:                                   # noqa: BLE001
        continue
    _rows, _st = _r.get("rows") or [], _r.get("row_styles") or []
    for _i, _row in enumerate(_rows):
        if any(str(c).strip() for c in _row[1:]):
            continue                                    # not a heading shape
        _c0 = _row[0]
        _headings.setdefault(_key, []).append(
            (_i, bool(isinstance(_c0, dict) and _c0.get("group")),
             _st[_i] if _i < len(_st) else None))

_unmarked = [f"{k} row {i}" for k, v in _headings.items() for i, g, _s in v if not g]
check("every heading row is marked the one way, on its cell",
      not _unmarked, "; ".join(_unmarked))
_sub = [f"{k} row {i}" for k, v in _headings.items() for i, _g, st in v if st == "sub"]
check("and none of them also carries the row style that meant it",
      not _sub, "; ".join(_sub) + " -- `sub` is a lesser row (invoice_off's "
                                 "resemblances), never a heading")
check("...and the sweep found headings to check at all",
      sum(len(v) for v in _headings.values()) >= 4,
      f"{sum(len(v) for v in _headings.values())} heading row(s)")

# The export is lifted out of the page and driven in node against the real
# payloads, the arrangement `test_menu_layout.py` uses over hub-crumbs.js: a
# copy of the rule restated here would be a third thing to keep in step.
_a = PAGE.find("/* ---- csv export")
_b = PAGE.find("/* ---- end csv export ---- */")
check("the csv export is marked for lifting", _a >= 0 and _b > _a,
      "markers moved or gone -- the rest of this section proves nothing")

if _a >= 0 and _b > _a:
    _payloads = {}
    for _key in qa.REPORTS:
        try:
            _r = qa.run(_key)
        except Exception:                               # noqa: BLE001
            continue
        _payloads[_key] = {"columns": _r.get("columns") or [],
                           "rows": _r.get("rows") or []}
    _js = PAGE[_a:_b] + "\nconst P=" + json.dumps(_payloads) + ";\n" + """
    const out = {};
    for (const [k, d] of Object.entries(P)) {
      const lines = toCsv(d);
      out[k] = {
        data: lines.length - 1,
        rows: (d.rows || []).length,
        headings: (d.rows || []).filter(isGroupRow).length,
        // a heading that survived: a label with every other field blank
        leaked: lines.slice(1).filter(l => /^("[^"]*",)*"[^"]*\\(\\d+\\)"(,"")+$/.test(l)).length,
        total: lines.some(l => l.includes('"TOTAL"')),
        header: lines[0],
      };
    }
    console.log(JSON.stringify(out));
    """
    _f = os.path.join(_TMP, "csv_export.js")
    with open(_f, "w", encoding="utf-8") as _fh:
        _fh.write(_js)
    _proc = subprocess.run(["node", _f], capture_output=True, text=True)
    check("the lifted export runs in node", _proc.returncode == 0,
          (_proc.stderr or "")[-300:])
    if _proc.returncode == 0:
        _out = json.loads(_proc.stdout.strip().splitlines()[-1])
        _leak = [k for k, v in _out.items() if v["leaked"]]
        check("no heading reaches the csv as a data row", not _leak, f"{_leak}")
        _short = [f'{k}: {v["data"]} lines for {v["rows"]}-{v["headings"]} rows'
                  for k, v in _out.items()
                  if v["data"] != v["rows"] - v["headings"]]
        check("and the csv carries exactly the rows that are not headings",
              not _short, "; ".join(_short))
        _grouped = [k for k, v in _out.items() if v["headings"]]
        check("a report with headings gains a Group column, so nothing is lost",
              all(_out[k]["header"].startswith('"Group"') for k in _grouped),
              f'{[(k, _out[k]["header"][:40]) for k in _grouped]}')
        check("...and one without headings gains no column",
              all(not v["header"].startswith('"Group"')
                  for k, v in _out.items() if not v["headings"])),
        # The scorecards mark TOTAL `group` as well -- it wants the same band
        # -- so reading the marker alone would drop the one row somebody
        # downloads the CSV for.
        check("a TOTAL row is kept, because it is data drawn like a heading",
              _out.get("sales-scorecard", {}).get("total") is True
              and _out.get("sales-scorecard", {}).get("headings") == 0,
              f'{_out.get("sales-scorecard")}')


# ---------------------------------------------------------------------------
section("The dashboard tile that read a refusal as four noughts")

# `/qa/stale-creative` says "Not measured" when the client list or every
# creative store refuses -- `build_audit()` computes the flag and the report
# page draws it. `scorecard()` copies eleven keys out of that audit for the
# dashboard tile and dropped it, so the same morning drew **0 in every band**
# on the dashboard: every client up to date on creative, in four confident
# noughts, with the report one click away saying the opposite.
#
# The tile's own note says it fails quietly so the dashboard never goes down
# when the card cannot load. That is right about a fetch that fails, and it is
# what made this invisible -- this fetch succeeds.

from hub import stale_creative as _sc                            # noqa: E402

_sc_real = _sc._registry_clients
_sc_real_sources = _sc.SOURCES
_sc_real_load_source = _sc._load_source
_sc_real_load_knack = _sc._load_knack_creative
try:
    # The private creative archives are deliberately absent from the sanitized
    # repository. Supply one measured source so this section isolates the two
    # halves of the join instead of testing whether a developer has live data.
    _sc.SOURCES = ({"label": "QA fixture"},)
    _sc._load_source = lambda _source: [{
        "client_raw": "Acme Bakery",
        "uploaded_at": _dt.datetime.now(_dt.timezone.utc),
        "source_label": "QA fixture",
        "title": "Fixture creative",
        "note": "",
        "alt": "",
        "url": "",
        "thumb": "",
    }]
    _sc._load_knack_creative = lambda: []
    _sc._registry_clients = lambda *a, **k: []
    _sc._CACHE.update({"data": None, "at": 0.0})
    _card = _sc.scorecard()
    check("the tile's payload says so when the client list refuses",
          _card.get("measured") is False, repr(_card.get("measured")))
    check("and which half refused, because they are different outages",
          _card.get("clients_measured") is False
          and _card.get("sources_measured") is True,
          f'clients_measured={_card.get("clients_measured")!r} '
          f'sources_measured={_card.get("sources_measured")!r}')
    check("...while every band is a nought, which is why the flag is the answer",
          _card.get("clients") == 0 and _card.get("needs_attention") == 0)

    _sc._registry_clients = _sc_real
    _sc._CACHE.update({"data": None, "at": 0.0})
    _card_ok = _sc.scorecard()
    check("a real run still reads as measured",
          _card_ok.get("measured") is True, repr(_card_ok.get("measured")))
    check("and still carries the counts the tile draws",
          _card_ok.get("clients") and _card_ok.get("edges"),
          repr({k: _card_ok.get(k) for k in ("clients", "edges")}))
finally:
    _sc._registry_clients = _sc_real
    _sc.SOURCES = _sc_real_sources
    _sc._load_source = _sc_real_load_source
    _sc._load_knack_creative = _sc_real_load_knack
    _sc._CACHE.update({"data": None, "at": 0.0})

# The tile has to read it. A payload carrying the flag and a card ignoring it
# is the same nought on the same dashboard.
_TILE = pathlib.Path(ROOT, "hub", "templates",
                     "_scorecard_stale_creative.html").read_text(encoding="utf-8")
check("the tile branches on measured before it draws a number",
      "d.measured === false" in _TILE,
      "the card draws d[data-k] straight from the payload")
check("and says it is not measured rather than hiding, which reads as clean",
      "Not measured" in _TILE and "card.hidden = false" in _TILE)

# The sweep, so the next tile cannot drop it either: any dashboard partial
# fetching a Hub API must either branch on `measured` or be named here with
# the reason its source cannot refuse.
# Empty, which is the only way this was worth adding: every dashboard card
# on `dashboard.html` already branches on `measured` -- the prospect queue,
# the proposal pipeline, the social scoreboard, My Clients and the presence
# line all do -- and this partial was the one outlier. An entry here would
# name a tile whose source genuinely cannot refuse, with the reason.
_TILE_EXEMPT: dict = {}
_missing = []
for _t in sorted(pathlib.Path(ROOT, "hub", "templates").glob("_scorecard_*.html")):
    _src = _t.read_text(encoding="utf-8", errors="ignore")
    if "fetch(" not in _src:
        continue
    if "measured" in _src or _t.name in _TILE_EXEMPT:
        continue
    _missing.append(_t.name)
check("every dashboard tile that fetches says when it could not measure",
      not _missing, ", ".join(_missing))
_stale_exempt = [k for k in _TILE_EXEMPT
                 if not pathlib.Path(ROOT, "hub", "templates", k).exists()]
check("and no exemption names a tile that is gone",
      not _stale_exempt, f"{_stale_exempt}")


# ---------------------------------------------------------------------------
section("A row the page can draw: one cell per heading, one handler per button")

# `renderTable()` writes one <th> per entry in `columns` and one <td> per cell
# in a row, so the two have to be the same length — a row with a cell no
# column names puts that cell under the heading belonging to the value on its
# left, and the CSV export writes `columns` as its header row and the cells
# beneath it, so every row gains an unlabelled trailing field. `no_dashboards`
# was six cells against five headings: its Add-dashboard button. The two
# functions that also emit an action cell head it `""`, which is the fix.
_shape = []
for _key in qa.REPORTS:
    try:
        _r = qa.run(_key)
    except Exception:                                   # noqa: BLE001
        continue                                        # covered above
    _cols = _r.get("columns") or []
    for _i, _row in enumerate(_r.get("rows") or []):
        if len(_row) != len(_cols):
            _shape.append(f"{_key} row {_i}: {len(_row)} cells, {len(_cols)} columns")
            break
    _styles = _r.get("row_styles")
    if _styles is not None and len(_styles) != len(_r.get("rows") or []):
        _shape.append(f"{_key}: {len(_styles)} row_styles, "
                      f"{len(_r.get('rows') or [])} rows")
check("every report's rows carry one cell per column",
      not _shape, "; ".join(_shape))

# And a button the page has no branch for is a button that does nothing, which
# on a report is indistinguishable from one that failed silently. Every form
# the handler is written in is matched, or the check reports a live action as
# dead and gets switched off for it.
_handled = set(re.findall(r"""action\s*===\s*['"]([\w-]+)['"]""", PAGE))
_handled |= set(re.findall(r"""case\s*['"]([\w-]+)['"]""", PAGE))
_emitted = set()
for _key in qa.REPORTS:
    try:
        _r = qa.run(_key)
    except Exception:                                   # noqa: BLE001
        continue
    for _row in (_r.get("rows") or []):
        for _c in _row:
            if isinstance(_c, dict):
                for _a in (_c.get("actions") or []):
                    _emitted.add((_key, str(_a.get("action") or "")))
_dead = sorted(f"{k}:{a}" for k, a in _emitted if a not in _handled)
check("every action a report puts on a row has a handler on the page",
      not _dead, "; ".join(_dead))
check("...and the sweep found buttons to check at all",
      bool(_emitted), f"{len(_emitted)} action(s) emitted")


# ---------------------------------------------------------------------------
section("The product book: which source answered, and the two flags only one carries")

# `/qa`'s client reports read `knack_data.products()` — the hand-committed
# export nothing refreshes — while Client 360 read the same object live. The
# swap looks like one line and is not: `thisM` and `lastM` exist **only** on
# the export, so pointing this page at Knack would set both False on every row
# and four reports would go quiet rather than wrong. Both halves are asserted,
# because fixing the source and leaving the flags is the worse of the two
# states — it reads as a page that has nothing to say.

import datetime as _d, calendar as _cal                          # noqa: E402
from hub import knack_data as _kd                                # noqa: E402

_today = _d.date.today()
_first = _today.replace(day=1)
_prev_end = _first - _d.timedelta(days=1)
_prev_first = _prev_end.replace(day=1)
_old_end = (_prev_first - _d.timedelta(days=1)).replace(day=1) - _d.timedelta(days=1)


def _iso(d):
    return d.strftime("%m/%d/%Y")


def _with_products(rows, source="knack", age=3):
    """Run a callable against a synthetic book, whatever the real export says."""
    real = _kd._product_source

    def fake():
        return rows, source, age
    _kd._product_source = fake
    try:
        out = qa._client_groups()
    finally:
        _kd._product_source = real
    return out


# A book a *live* pull would produce: real dates and statuses, and neither
# flag on any row, because knack_products._row() emits neither.
LIVE_BOOK = [
    {"client": "Nowbilling Co", "product": "Display", "monthly": "1000",
     "status": "Live", "start": _iso(_first), "end": _iso(_today + _d.timedelta(days=40))},
    {"client": "Lastmonth Co", "product": "Display", "monthly": "500",
     "status": "Complete", "start": _iso(_prev_first), "end": _iso(_prev_end)},
]
g = _with_products(LIVE_BOOK)
check("a live row with no thisM still counts as billing this month",
      g.get("Nowbilling Co", {}).get("thisM") is True,
      "thisM came back "
      + repr(g.get("Nowbilling Co", {}).get("thisM"))
      + " — the flag is export-only, so it has to be computed")
check("and its billing is totalled rather than left at zero",
      round(g.get("Nowbilling Co", {}).get("this_total") or 0) == 1000,
      f'this_total={g.get("Nowbilling Co", {}).get("this_total")!r}')
check("a live row that finished last month counts as lastM, not thisM",
      g.get("Lastmonth Co", {}).get("lastM") is True
      and g.get("Lastmonth Co", {}).get("thisM") is False,
      f'lastM={g.get("Lastmonth Co", {}).get("lastM")!r} '
      f'thisM={g.get("Lastmonth Co", {}).get("thisM")!r}')
check("and last month's billing is totalled",
      round(g.get("Lastmonth Co", {}).get("last_total") or 0) == 500,
      f'last_total={g.get("Lastmonth Co", {}).get("last_total")!r}')

# The other half, and the one that is live on any deployment whose committed
# export has slipped a month: the flags describe the month the export was
# generated *for*, and nothing recomputes them. A row flagged thisM whose term
# ended two months ago is not billing this month, whatever the file says.
STALE_EXPORT = [
    {"client": "Stalefile Co", "product": "Display", "monthly": "900",
     "status": "Complete", "thisM": True, "lastM": True,
     "start": _iso(_old_end.replace(day=1)), "end": _iso(_old_end)},
    {"client": "Started Since Co", "product": "Display", "monthly": "700",
     "status": "Live", "thisM": False, "lastM": False,
     "start": _iso(_first), "end": _iso(_today + _d.timedelta(days=60))},
]
g2 = _with_products(STALE_EXPORT, source="export", age=None)
check("a stale export's thisM does not make a finished campaign current",
      g2.get("Stalefile Co", {}).get("thisM") is False,
      "the flag says this month; its term ended "
      + _iso(_old_end))
check("and a campaign the stale export never saw is counted",
      g2.get("Started Since Co", {}).get("thisM") is True,
      "started " + _iso(_first) + " and the export's flag says no")

# On the real committed book, the computed month pair reproduces the flags the
# exporter wrote. The month is derived above rather than assumed to be today:
# the file and the calendar do not roll over in the same transaction.
_real_rows = _kd.products()
_ts, _te = qa._month_bounds(_export_flag_ym)
_ls, _le = qa._month_bounds(qa._prev_ym(_export_flag_ym))
for _flag, _s, _e in (("thisM", _ts, _te), ("lastM", _ls, _le)):
    _flagged = {id(r) for r in _real_rows if r.get(_flag)}
    _computed = {id(r) for r in _real_rows if qa._active_in_month(r, _s, _e)}
    check(f"computed {_flag} reproduces the export's own flag exactly",
          _flagged == _computed,
          f"{len(_flagged)} flagged, {len(_computed)} computed, "
          f"{len(_flagged ^ _computed)} disagree")

# The source is named, so a stale export cannot go on looking like live data.
_note_seen = qa.run("active-clients").get("note") or ""
check("a product-backed report says which source answered",
      "private fallback export" in _note_seen or "Live from Knack" in _note_seen,
      repr(_note_seen[-90:]))
# `getattr` rather than a direct call, so a build without the shared wording
# fails on the assertion that names it rather than dying here and taking every
# check after it out of the run.
_kd_note = getattr(_kd, "products_note", None)
_seo_note = getattr(__import__("hub.seo", fromlist=["x"]), "products_note", None)
check("and it is the one sentence knack_data gives every other screen",
      bool(_kd_note) and (_kd_note("export", None) in _note_seen
                          or _kd_note("knack", 0).split(",")[0] in _note_seen),
      repr(_note_seen[-90:]) if _kd_note else "knack_data has no products_note()")
check("hub/seo.py words it identically rather than a second time",
      bool(_kd_note) and bool(_seo_note)
      and _seo_note("export", None) == _kd_note("export", None),
      "one of the two has no products_note()" if not (_kd_note and _seo_note) else "")

# A report that could not measure keeps its own reason and is not handed a
# staleness note about rows nobody drew.
_unmeasured = dict(qa._unmeasured(["A"], "we could not look"))
check("_unmeasured() carries measured False and its reason",
      _unmeasured.get("measured") is False
      and _unmeasured.get("note") == "we could not look")

# `products_error()` asks whichever source answered. Asked of the export alone
# it would report a healthy live pull as unmeasurable wherever the committed
# file happens to be absent.
_real_src, _real_load = _kd._product_source, _kd._load
try:
    # Knack answering, and no committed export on disk at all — the state a
    # deployment that has never carried one is in, and the one where asking
    # the export refuses to measure on the strength of a file nothing read.
    _kd._product_source = lambda: ([{"client": "X"}], "knack", 1)
    _kd._load = lambda name: None
    check("products_error() is silent when Knack answered",
          _kd.products_error() == "",
          repr(_kd.products_error()))
    _kd._product_source = lambda: ([], "export", None)
    check("and says so when the export answered with nothing",
          _kd.products_error() != "")
finally:
    _kd._product_source, _kd._load = _real_src, _real_load

# The sweep, so this cannot be reintroduced one call site at a time. A product
# row's month flags may be read in exactly one place, named with its reason.
_FLAG_READERS_ALLOWED = {
    # The dashboard scorecard is deliberately measured against the export's
    # own period and its own flags — CLAUDE.md says so at length, and a term
    # rebuild there was written, reproduced the flags exactly, and was still
    # removed for not being measured the same way as the number above it.
    "hub/knack_data.py": "month_over_month(), the dashboard scorecard",
}
_flag_hits = {}
for _p in sorted(pathlib.Path(ROOT, "hub").glob("*.py")):
    _rel = "hub/" + _p.name
    _src = _p.read_text(encoding="utf-8", errors="ignore")
    for _node in ast.walk(ast.parse(_src)):
        # r.get("thisM") / r["lastM"] on anything that is not the group dict
        if isinstance(_node, ast.Call) and isinstance(_node.func, ast.Attribute) \
                and _node.func.attr == "get" and _node.args \
                and isinstance(_node.args[0], ast.Constant) \
                and _node.args[0].value in ("thisM", "lastM"):
            _tgt = _node.func.value
            if isinstance(_tgt, ast.Name) and _tgt.id in ("g", "grp", "group"):
                continue                    # the group dict, which computes it
            _flag_hits.setdefault(_rel, []).append(_node.lineno)
_unexpected = {k: v for k, v in _flag_hits.items() if k not in _FLAG_READERS_ALLOWED}
check("no module reads a product row's month flags without a reason on file",
      not _unexpected,
      "; ".join(f"{k}:{v}" for k, v in _unexpected.items()))
_gone = [k for k in _FLAG_READERS_ALLOWED if k not in _flag_hits]
check("and every allowed reader still reads them",
      not _gone, f"stale entries: {_gone}")


# ---------------------------------------------------------------------------
print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
