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
_qa_tree = ast.parse(pathlib.Path(ROOT, "hub", "qa.py").read_text())
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

# The invariant that makes the two agree, over the real export rather than a
# fixture: anything delivering today ran in the month containing today. The one
# documented exception is a row with no dates -- is_running() accepts it on the
# strength of a Live status, and a month test cannot place it.
_today = _dt.date.today()
_tms, _tme = qa._month_bounds(_today.strftime("%Y%m"))
_disagree = [
    r for r in knack_data.products()
    if knack_data.is_running(r)
    and not knack_data.ran_in_month(r, _tms, _tme)
    and (r.get("start") or r.get("end"))
]
check("everything delivering today counts for this month",
      len(_disagree), 0)

# And the two salespeople the old rule hid are back on the scorecard.
_sc = qa.run("sales-scorecard")
_names = " ".join(str(c) for row in _sc["rows"] for c in row)
for _who in ("Debi Greenfield", "Kim Marshall"):
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
_QA_TREE = ast.parse(pathlib.Path(ROOT, "hub", "qa.py").read_text())
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

# Nothing moved on today's real book — the evidence that this is safe now and
# only bites when the export slips or Knack answers.
_real_rows = _kd.products()
_ts, _te = qa._month_bounds(_today.strftime("%Y%m"))
_ls, _le = qa._month_bounds(qa._prev_ym(_today.strftime("%Y%m")))
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
      "committed export" in _note_seen or "Live from Knack" in _note_seen,
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
