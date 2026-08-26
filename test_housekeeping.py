"""What needs filling in goes to the person who can fill it in.

    python3 test_housekeeping.py

Same shape as the other test files: no pytest, no new dependencies, and it
runs against a temporary data directory and a throwaway SQLite database, so it
never touches /var/data or the real one.

## Why this file exists

The dashboard's birthday block ended with a sentence naming seven placeholder
start dates and telling the reader to fix them under Users — on a page eleven
of the fourteen accounts read and none of those eleven can act on, since
`/diagnostics/users` answers them 403. And that sentence was the only record
of the gap anywhere, so the three people who could fix it learned about it by
looking at somebody else's screen. Every failure below leaves that arrangement
in place while looking like it was fixed:

  1.  the warning is moved and
      nothing lists it            — a to-do deleted from the one screen it was
                                    on is not a to-do that got done
  2.  the row does not say
      which page                  — the point of moving it is that the person
                                    who can act never saw the page, so a
                                    finding with no page has lost the half
                                    that makes it actionable
  3.  a source that cannot be
      read reports nothing        — "nobody needs anything" and "we could not
                                    look" rendering identically is the failure
                                    this whole codebase is written against
  4.  one broken source empties
      the panel                   — the other checks must still answer
  5.  a General account still
      gets the to-do              — or the counts, or the names, or a link to
                                    a page that 403s them
  6.  a General account is told
      nothing at all              — the list really is short, and a block that
                                    silently omits people reads as the whole
                                    roster
  7.  the panel is readable
      without being an admin      — it names staff and what is missing about
                                    them
  8.  two screens classify the
      gaps differently            — the block and the row read one classifier
                                    or they will drift
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1housekeeping_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "housekeeping-test-secret"
os.environ["PANEL_PASSWORD"] = "housekeeping-test-shared"

from werkzeug.test import Client                                   # noqa: E402

from hub import access, celebrations, housekeeping, user_directory  # noqa: E402
from wsgi import application                                        # noqa: E402

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def ok(label, cond, why=""):
    check(label + (f" ({why})" if why and not cond else ""), bool(cond), True)


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def by_key(rows):
    return {r["key"]: r for r in rows}


# ------------------------------------------------ the roster is the first one
section("The roster's missing dates are a Diagnostics row, not a dashboard nag")

REPORT = housekeeping.findings()
ROWS = by_key(REPORT["findings"])

ok("the seven census placeholders are listed",
   "roster_start_placeholder" in ROWS,
   f"only {sorted(ROWS)} came back")
check("all seven of them", ROWS["roster_start_placeholder"]["count"], 7)
ok("and they are named, because a list of names is what makes it a task",
   len(ROWS["roster_start_placeholder"]["names"]) == 7)
ok("the row says the date is on file and means nothing",
   "2019-08-01" in ROWS["roster_start_placeholder"]["issue"])

section("Every finding says where a reader meets it, and where it is fixed")

for _key, _row in ROWS.items():
    if not _row["measured"]:
        continue
    ok(f"{_key} names the page it shows on", bool(_row["page"]))
    ok(f"{_key} says what to do about it", bool(_row["fix"]))
ok("and the roster row links to the panel that fixes it",
   ROWS["roster_start_placeholder"]["fix_path"] == "/diagnostics/users")
ok("which is the page the dashboard used to send everybody to",
   ROWS["roster_start_placeholder"]["page_path"] == "/")

section("What was checked is named, so one row is not mistaken for one check")

ok("every source is listed", set(REPORT["sources"]) ==
   {key for key, _ in housekeeping.SOURCES})
ok("and the ones with nothing to say are named as clean",
   set(REPORT["clean"]) <= set(REPORT["sources"]))

# ------------------------------------------- none, and could not look, differ
section("A source that could not be read is a finding, not an absence")

_saved = celebrations.roster_gaps
celebrations.roster_gaps = lambda: {"gaps": {}, "people": 0,
                                    "error": "the profile table is unreadable"}
try:
    _blind = housekeeping.findings()
    _rows = by_key(_blind["findings"])
    check("the roster reports not measured", "roster_dates" in _blind["not_measured"], True)
    ok("in words, with the reason",
       "Not measured" in _rows["roster_dates"]["issue"]
       and "unreadable" in _rows["roster_dates"]["issue"])
    ok("and it is not counted as an open to-do",
       _rows["roster_dates"]["measured"] is False and _blind["open"] == 0)
finally:
    celebrations.roster_gaps = _saved


def _explode():
    raise RuntimeError("this source is broken")


section("A source that fails costs only itself")

_sources = housekeeping.SOURCES
housekeeping.SOURCES = (("broken", _explode),) + _sources
try:
    _mixed = housekeeping.findings()
    _rows = by_key(_mixed["findings"])
    check("the broken source is named", "broken" in _mixed["not_measured"], True)
    ok("by the exception it raised, not by silence",
       "RuntimeError" in _rows["broken"]["issue"])
    ok("and the roster is still reported beside it",
       "roster_start_placeholder" in _rows)
finally:
    housekeeping.SOURCES = _sources

# ------------------------------------------------------- who is told what
section("Two screens, one classifier")

_month = celebrations.this_month()["not_recorded"]
_direct = celebrations.roster_gaps()["gaps"]
check("the block and the Diagnostics row classify the roster identically",
      _month, _direct)

section("A General account is told the list is short, and nothing else")

_full = {"birthday": ["Ann Example"], "hired_at": [],
         "hired_placeholder": ["Bob Example"]}
_thin = housekeeping.withheld(_full)
check("the counts and the names are gone", sorted(_thin), ["any", "withheld"])
check("but the fact that somebody is missing is not", _thin["any"], True)
check("and a complete roster says nothing at all",
      housekeeping.withheld({"birthday": [], "hired_at": [],
                             "hired_placeholder": []})["any"], False)

# ------------------------------------------------ the pages, as served
section("The page, as the browser receives it")

DEFAULT = user_directory.DEFAULT_PASSWORD


def settled(email, new_password):
    """Signed in and past the forced password change.

    The starting password is valid for exactly one sign-in, so a client that
    stops at /login is redirected to /account on every request after it and
    every check below would be reading the password form.
    """
    c = Client(application)
    c.post("/login", data={"email": email, "password": DEFAULT, "next": "/"})
    c.post("/account", data={"current": DEFAULT, "password": new_password})
    return c


_ADMIN = next(r["email"] for r in user_directory.roster_rows()
              if r["role"] in ("admin", "super_admin"))
_GENERAL = next(r["email"] for r in user_directory.roster_rows()
                if r["role"] not in ("admin", "super_admin"))

admin = settled(_ADMIN, "a-long-enough-admin-phrase")
general = settled(_GENERAL, "a-long-enough-general-phrase")

check("the housekeeping list is a Utilities path",
      access.is_utility("/api/housekeeping"), True)
check("so a General account is refused it",
      general.get("/api/housekeeping").status_code, 403)
check("and it is not readable without a login at all",
      Client(application).get("/api/housekeeping").status_code, 401)

_api = admin.get("/api/housekeeping").get_json()
ok("an admin gets the roster row", "roster_start_placeholder" in by_key(_api["findings"]))

_page = admin.get("/diagnostics").get_data(as_text=True)
ok("the panel is on Diagnostics", 'id="diag-housekeeping"' in _page)
ok("above API health, because its rows are somebody's to-do",
   _page.index("diag-housekeeping") < _page.index("diag-checks"))

section("The dashboard says only what its reader can act on")

_gen = general.get("/api/celebrations").get_json()
check("a General account is told the roster is incomplete",
      _gen["not_recorded"], {"withheld": True, "any": True})
_blob = str(_gen)
ok("without a count of what is missing",
   "hired_placeholder" not in _blob and "Fill them in" not in _blob)

_adm = admin.get("/api/celebrations").get_json()
check("an admin still gets the counts",
      len(_adm["not_recorded"]["hired_placeholder"]), 7)

# The dashboard is one template for everybody — the API is what withholds —
# so what the page has to prove is that it asks who is reading before it
# builds the sentence, rather than rendering counts that were never sent.
_dash = general.get("/").get_data(as_text=True)
ok("the block checks whether the gaps were withheld", "nr.withheld" in _dash)
ok("before it builds the to-do sentence",
   _dash.index("nr.withheld") < _dash.index("Fill them in under"))

print()
if _failed:
    print(f"{_failed} FAILED, {_passed} passed")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1)
print(f"{_passed} checks passed — the to-do is on the page that can act on it, "
      "and the block still says it is not the whole roster")
shutil.rmtree(TMP, ignore_errors=True)
