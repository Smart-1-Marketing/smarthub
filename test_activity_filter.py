#!/usr/bin/env python3
"""Narrowing the activity log, and linking to the rows a figure counted.

`/activity` offered a hand-typed three-entry dropdown — All, `hub`, `suite` —
on a Hub where dozens of modules log, and it read nothing from the URL. So
most of the log could not be narrowed to at all, and a link carrying
`?module=ads_builder` opened the whole log unfiltered: a reader who had
followed a figure concluded the figure was wrong rather than the filter
absent. That is the same failure the leads panel had to undo about its own
`page` and `days`.

What is asserted here is the half that goes wrong quietly:

* the filters actually filter, on the module AND on the action, because a
  count that opens a wider list than it counted is the "Showing 1 of 7"
  answer one page over;
* the dropdown is served rather than typed, and a module that has not logged
  yet is still offered — "nothing filed under this" and "not a module" are
  different answers;
* a module named in the URL that the list does not carry is kept and selected,
  never silently dropped to All, because the log genuinely holds its rows and
  a narrowed table under a control reading "All modules" is the page
  contradicting itself;
* the log being unreadable is said, not answered as a Hub with two modules;
* every route of it is refused to a stranger and to a General account — the
  activity log names every member of staff and what they did.

Run: python3 test_activity_filter.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Own directory AND own database, per test_jsonstore.py: a fresh data root in
# front of an inherited DATABASE_URL is refilled from the last run's mirror.
_TMP = tempfile.mkdtemp(prefix="activity-filter-")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
# Named explicitly rather than inherited: hub/audit.py defers to data_root()
# but AUDIT_LOG_PATH is the more specific answer, and pinning it is what keeps
# this file off the shared /var/data log every other run has been writing to.
_LOG = str(pathlib.Path(_TMP, "hub-audit.log.jsonl"))
os.environ["AUDIT_LOG_PATH"] = _LOG
os.environ.setdefault("SECRET_KEY", "test-only")
os.environ.setdefault("PANEL_PASSWORD", "test-only-password")

FAILED = []
_SECTION = ""


def section(name: str) -> None:
    global _SECTION
    _SECTION = name
    print(f"\n== {name} ==")


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        if detail:
            print(f"        {detail}")
        FAILED.append(f"{_SECTION}: {label}")


from hub import ads_status, audit                         # noqa: E402

check("the log is this test's own, not the shared one",
      audit._path() == _LOG, f"{audit._path()} != {_LOG}")

audit.log("ads_builder", "optimization_auto_applied", actor="scheduler",
          client="Acme Plumbing", customer_id="1111111111")
audit.log("ads_builder", "optimization_auto_applied", actor="scheduler",
          client="Riverside HVAC", customer_id="2222222222")
audit.log("ads_builder", "proposal_deployed", actor="Todd", client="Icon Solar")
audit.log("hub", "login", actor="Todd")
audit.log("seo", "blog_written", actor="Todd", client="Buckeye Marina")


# ---------------------------------------------------------------------------
section("The filters filter")

check("no filter reads everything",
      len(audit.read(limit=50)) == 5, str(len(audit.read(limit=50))))
check("the module narrows to that module",
      {e["module"] for e in audit.read(limit=50, module="ads_builder")} == {"ads_builder"}
      and len(audit.read(limit=50, module="ads_builder")) == 3)
check("the action narrows further, which is what a counted figure needs",
      len(audit.read(limit=50, module="ads_builder",
                     type_="optimization_auto_applied")) == 2)
check("and the action alone works across modules",
      len(audit.read(limit=50, type_="login")) == 1)
check("a module nothing logged under is empty rather than everything",
      audit.read(limit=50, module="nope") == [])
check("and so is an action nothing logged",
      audit.read(limit=50, type_="nope") == [])

# tail() already had type_; read() is what /api/activity calls, and the two
# must not disagree about what a filter means.
check("read() and tail() agree on the same narrowing",
      [e["type"] for e in audit.read(limit=50, module="ads_builder")]
      == [e["type"] for e in audit.tail(limit=50, module="ads_builder")])


# ---------------------------------------------------------------------------
section("The dropdown is served, and says what it does not know")

known = audit.known_modules()
names = {m["name"] for m in known["modules"]}
check("it measured the window", known["window_measured"] is True, repr(known))
check("every module that actually logged is offered",
      {"ads_builder", "hub", "seo"} <= names, repr(sorted(names)))
check("and each of those is marked as seen",
      all(m["seen"] for m in known["modules"]
          if m["name"] in {"ads_builder", "hub", "seo"}))

# The declared half: a module that bound a logger and has written nothing must
# still be offerable, or a quiet module reads as one that does not exist.
audit.for_module("a_quiet_module_that_has_not_logged")
known2 = audit.known_modules()
quiet = [m for m in known2["modules"]
         if m["name"] == "a_quiet_module_that_has_not_logged"]
check("a module that has registered a logger and never used it is offered",
      len(quiet) == 1, repr(quiet))
check("and is marked as having nothing recorded, rather than as absent",
      bool(quiet) and quiet[0]["seen"] is False and quiet[0]["declared"] is True,
      repr(quiet))

# The aliases. `display_ads` and `utm` are the names rows are actually written
# under, and neither is a directory name — declared in LOG_NAMES for exactly
# that reason, so a dropdown built from directories would miss both.
check("the LOG_NAMES aliases are offerable",
      set(audit.LOG_NAMES.values()) <= {m["name"] for m in known2["modules"]},
      repr(sorted(audit.LOG_NAMES.values())))

# The half that matters most, and the one _REGISTERED cannot supply. Several
# busy modules write through a direct `log("name", ...)` rather than a bound
# logger, so they register nothing and were offerable only once they had
# already logged -- ads_builder among them, which is the module the dashboard
# card links here for. client_brand's two tables are keyed on the name each
# module actually logs under and are already held true against the call sites
# by /api/integrity, which is why they are read rather than restated.
from hub import client_brand                              # noqa: E402
_declared = {m["name"] for m in known2["modules"] if m["declared"]}
check("every name client_brand knows a module logs under is offerable",
      (set(client_brand.WORK_KINDS) | set(client_brand.NOT_WORK)) <= _declared,
      repr(sorted((set(client_brand.WORK_KINDS) | set(client_brand.NOT_WORK))
                  - _declared)))
check("including ads_builder, before it has logged anything on this machine",
      "ads_builder" in _declared)

# An unreadable log is a state, not two modules.
_real_tail = audit.tail
audit.tail = lambda *a, **k: (_ for _ in ()).throw(OSError("disk gone"))
try:
    broken = audit.known_modules()
finally:
    audit.tail = _real_tail
check("a log that cannot be scanned says so rather than answering cleanly",
      broken["window_measured"] is False, repr(broken))
check("and still offers the declared modules rather than nothing at all",
      len(broken["modules"]) > 0)


# ---------------------------------------------------------------------------
section("The page reads the URL, and keeps it in step")

_PAGE = pathlib.Path(ROOT, "hub", "templates",
                     "activity.html").read_text(encoding="utf-8")

check("it reads ?module= from the address bar",
      "searchParams.get('module')" in _PAGE
      or "get('module')" in _PAGE)
check("and ?type=", "get('type')" in _PAGE)
# Scoped to the markup's own <select>, not the whole file: the renderer builds
# <option> strings in JavaScript, and counting those would be asserting about
# the fix rather than about the defect. What was live is a hand-typed list of
# module names inside the element itself.
import re                                                  # noqa: E402
_SELECT = re.search(r"<select\b[^>]*id=\"mod\".*?</select>", _PAGE, re.S)
check("the markup carries a <select> to fill in", bool(_SELECT))
check("the dropdown is built from the server, not typed into the template",
      "/api/activity/modules" in _PAGE
      and bool(_SELECT) and _SELECT.group(0).count("<option") == 1,
      f"{_SELECT.group(0).count('<option') if _SELECT else '?'} options in the "
      "element — only the All row may be hand-written")
check("and no module name is hardcoded in it",
      bool(_SELECT) and not re.search(r"value=\"[A-Za-z]", _SELECT.group(0)),
      _SELECT.group(0) if _SELECT else "")
check("changing the control writes the URL back",
      "replaceState" in _PAGE and "searchParams.set('module'" in _PAGE)
check("a module in the URL the list does not carry is still selected",
      "not in the recent log" in _PAGE)
check("the type filter says what it is showing and offers a way out",
      "clearType" in _PAGE and "every action" in _PAGE)

# With no list at all, `names` is empty and every module looks absent from it,
# so the "nothing under X in the last N entries" note has to be gated on the
# list having been read -- otherwise the page reports a window it never
# managed to read. A review bot flagged the redundant `d &&` *inside* that
# sentence, which was right; this pins the guard that is not redundant, so
# the next simplification does not take it too.
# Anchored to the push it guards, not to the first `if(` of that shape: the
# branch that ADDS the option to the dropdown is written the same way and
# comes first, so a looser search matches that one and reports on the wrong
# line -- which is what the first version of this check did.
_NOTE_BRANCH = re.search(
    r"if\((.*?)\)\s*\n\s*bits\.push\('Nothing under", _PAGE)
check("the 'not in the recent log' note is gated on the list having been read",
      bool(_NOTE_BRANCH) and "&& d" in _NOTE_BRANCH.group(1),
      _NOTE_BRANCH.group(1) if _NOTE_BRANCH else "branch not found")
check("an empty result names the filter that produced it",
      "No activity recorded" in _PAGE and "' under “'" in _PAGE
      and "' for “'" in _PAGE,
      "a narrowed log reading 'nothing recorded yet' is a Hub where nothing happened")
check("and a filtered export is not named as the whole log",
      "hub-activity-" in _PAGE)
check("the value is encoded on the way into the query string",
      _PAGE.count("encodeURIComponent") >= 2,
      "a module name with an & in it would truncate the request")

# The list is awaited before the rows: load() reads the select's value, so a
# filtered URL would otherwise fetch every module and then draw it under a
# control that had already been set.
check("the module list is loaded before the entries",
      "loadModules().then(load)" in _PAGE)


# ---------------------------------------------------------------------------
section("A count opens the rows it counted")

check("the card's applied figure carries an address",
      "APPLIED_URL" in pathlib.Path(ROOT, "hub", "ads_status.py")
      .read_text(encoding="utf-8"))
check("which names the module the rows are actually written under",
      "module=ads_builder" in ads_status.APPLIED_URL, ads_status.APPLIED_URL)
check("and the action, derived from the event rather than typed twice",
      ads_status.AUTO_APPLIED_EVENT.lower() in ads_status.APPLIED_URL,
      ads_status.APPLIED_URL)

# The whole point: that URL's own filters must select exactly the two rows the
# count counted, and not the third ads_builder row beside them.
from urllib.parse import parse_qs, urlparse                # noqa: E402
_q = parse_qs(urlparse(ads_status.APPLIED_URL).query)
_rows = audit.read(limit=50, module=_q["module"][0], type_=_q["type"][0])
check("driving the link's own query returns the counted rows and nothing else",
      len(_rows) == 2 and {r["client"] for r in _rows}
      == {"Acme Plumbing", "Riverside HVAC"},
      repr([(r["module"], r["type"]) for r in _rows]))

_DASH = pathlib.Path(ROOT, "hub", "templates",
                     "dashboard.html").read_text(encoding="utf-8")
check("the card links the phrase rather than the server sending markup",
      "applied automatically" in _DASH and "ap.url" in _DASH)
check("and only when there is something to open",
      "ap&&ap.count&&ap.url" in _DASH.replace(" ", ""))


# ---------------------------------------------------------------------------
section("It is Utilities, on every route")
# The log names every member of staff and what they did. /activity is admin
# only, and gating the page while its data stays readable is a gate in name
# only — the failure four of the Diagnostics APIs already had.

from hub import access                                     # noqa: E402
for _p in ("/activity", "/api/activity", "/api/activity/modules"):
    check(f"{_p} is a Utilities path", access.is_utility(_p))

from hub import create_hub_app                             # noqa: E402
app = create_hub_app()
with app.test_client() as c:
    for _p in ("/activity", "/api/activity",
               "/api/activity/modules",
               "/api/activity/modules?module=hub"):
        r = c.get(_p)
        check(f"{_p} refuses a request with no session",
              r.status_code in (301, 302, 401, 403),
              f"answered {r.status_code}")

# And the route actually answers the shape the page reads, signed in.
with app.test_client() as c:
    c.post("/login", data={"password": os.environ["PANEL_PASSWORD"]})
    r = c.get("/api/activity/modules")
    check("signed in, the modules route answers 200", r.status_code == 200,
          f"answered {r.status_code}")
    if r.status_code == 200:
        body = json.loads(r.data)
        check("with the shape the dropdown is built from",
              isinstance(body.get("modules"), list)
              and all({"name", "seen", "declared"} <= set(m) for m in body["modules"]),
              repr(body)[:200])
    r = c.get("/api/activity?module=ads_builder&type=optimization_auto_applied")
    check("and the entries route applies both filters together",
          r.status_code == 200 and len(json.loads(r.data)["entries"]) == 2,
          r.data[:200].decode("utf-8", "ignore"))


print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED"))
for f in FAILED:
    print("  - " + f)
sys.exit(1 if FAILED else 0)
