"""Birthdays and work anniversaries: the month block, and the popup.

    python3 test_celebrations.py

Same shape as the other test files: no pytest, no new dependencies, and it
runs against a temporary data directory and a throwaway SQLite database, so it
never touches /var/data or the real one.

## Why this file exists

The dates were already in the Hub. `hub/user_directory.py` has carried a
birthday and a date of hire for all fourteen people since the census upload,
and nothing read either — so every failure here is one that leaves the block
on screen and confidently wrong:

  1.  a missing date is dropped
      in silence                  — a shorter list is indistinguishable from a
                                    quiet month, so the count of people with
                                    no date on file is part of the answer
  2.  the year of birth is
      published                   — the panel holds it and the block has no
                                    business printing the whole company's ages
  3.  29 February disappears
      three years in four         — the one absence that nothing reports
  4.  somebody who started this
      month gets a 0th
      anniversary                 — and somebody who starts *next week* gets
                                    congratulated for a job they have not
                                    begun
  5.  the popup greets the
      wrong Todd                  — two people on this roster are called Todd,
                                    and matching a display name is how one of
                                    them is wished a happy birthday on the
                                    other's day
  6.  the popup announces
      somebody four days out      — the popup that gets closed unread, and
                                    then so is the one that mattered
  7.  the block drifts below
      System checks               — it was asked for above them, and a block
                                    under a table of green ticks is one
                                    nobody sees
  8.  the roster is readable
      without a login             — these are staff dates of birth
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1cheers_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "cheers-test-secret"
os.environ["PANEL_PASSWORD"] = "cheers-test-shared"

from werkzeug.test import Client                                    # noqa: E402

from hub import celebrations, user_directory                        # noqa: E402
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


def ok(label, condition, detail=""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}{('  — ' + detail) if detail else ''}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def names(entries):
    return [e["name"] for e in entries]


# --------------------------------------------------------- the month itself
section("The month is read from the roster the Hub already holds")

AUG = celebrations.this_month(dt.date(2026, 8, 26))

check("August's birthdays, in date order", names(AUG["birthdays"]),
      ["Michael Hawkins", "George Roberts", "Lauren Jordan", "James Hern"])
check("and the days they fall on", [e["day"] for e in AUG["birthdays"]],
      [7, 13, 18, 27])
check("the seven people hired on 1 August 2019, seven years on",
      sorted({e["years_label"] for e in AUG["anniversaries"]}),
      ["7th anniversary"])
check("all seven of them", len(AUG["anniversaries"]), 7)
check("the month says which month it is", AUG["month"], "August")

# The month is a calendar, not a queue: a birthday earlier in the month is
# still shown, marked as past, rather than vanishing on the 8th.
check("a birthday already passed is kept and labelled",
      [e["is_past"] for e in AUG["birthdays"]], [True, True, True, False])
check("and one still to come knows how far off it is",
      AUG["birthdays"][-1]["days_away"], 1)

section("A date nobody recorded is named, not skipped")

_gaps = celebrations.this_month(dt.date(2026, 8, 26))["not_recorded"]
check("every roster row has both dates today, so nothing is missing",
      (_gaps["birthday"], _gaps["hired_at"]), ([], []))

_partial = celebrations._shape([
    {"name": "No Dates", "email": "nd@example.com", "title": "Tester",
     "birthday": "", "hired_at": ""},
    {"name": "Has Both", "email": "hb@example.com", "title": "Tester",
     "birthday": "1990-08-04", "hired_at": "2020-08-04"},
])
_saved = celebrations._people
celebrations._people = lambda: (_partial, "test", "")
try:
    _small = celebrations.this_month(dt.date(2026, 8, 26))
    check("the person with no birthday is counted, by name",
          _small["not_recorded"]["birthday"], ["No Dates"])
    check("and with no start date too",
          _small["not_recorded"]["hired_at"], ["No Dates"])
    check("while the complete row still appears",
          names(_small["birthdays"]), ["Has Both"])

    # 29 February, in a year that does not have one.
    celebrations._people = lambda: (celebrations._shape([
        {"name": "Leap Day", "email": "ld@example.com", "title": "Tester",
         "birthday": "1996-02-29", "hired_at": ""}]), "test", "")
    _feb = celebrations.this_month(dt.date(2027, 2, 10))
    check("a 29 February birthday is marked on the 28th in a common year",
          [(e["name"], e["day"]) for e in _feb["birthdays"]], [("Leap Day", 28)])
    _leap = celebrations.this_month(dt.date(2028, 2, 10))
    check("and on the 29th in a leap year",
          [e["day"] for e in _leap["birthdays"]], [29])

    # Somebody who has just started, and somebody who has not started yet.
    celebrations._people = lambda: (celebrations._shape([
        {"name": "Just Started", "email": "js@example.com", "title": "New",
         "birthday": "", "hired_at": "2026-08-03"},
        {"name": "Starts Later", "email": "sl@example.com", "title": "New",
         "birthday": "", "hired_at": "2026-08-31"}]), "test", "")
    _new = celebrations.this_month(dt.date(2026, 8, 26))
    check("somebody who started this month is welcomed, not given a 0th",
          [(e["name"], e["years_label"]) for e in _new["anniversaries"]],
          [("Just Started", "joined Smart 1 this month")])
finally:
    celebrations._people = _saved

section("The year of birth is never published")

_blob = json.dumps(AUG)
_birth_years = {user_directory.iso_date(r[5])[:4]
                for r in user_directory.ROSTER if user_directory.iso_date(r[5])}
ok("no birth year reaches the payload",
   not any(y in _blob for y in _birth_years),
   f"one of {sorted(_birth_years)} is in the JSON the dashboard renders")
ok("the day and month are there, which is what the block needs",
   "Aug 27" in _blob)
# A work anniversary is different: the years of service are the point of it,
# and the start date is not personal in the way a date of birth is.
ok("a work anniversary does carry its years",
   all("years" in e for e in AUG["anniversaries"]))

section("Only today is allowed to interrupt anybody")

_today = celebrations.today(dt.date(2026, 8, 27))
check("Jim Hern's birthday, on Jim Hern's birthday",
      names(_today["birthdays"]), ["James Hern"])
check("and nobody else's", _today["anniversaries"], [])
check("a day with nothing on it says so",
      celebrations.today(dt.date(2026, 8, 26))["any"], False)
check("even though that month has four birthdays in it",
      len(AUG["birthdays"]), 4)

section("The popup greets the right person")

# Two Todds. `mine` matches the account's email, and falls back to an exact
# name only when there is no account behind the session at all.
_feb23 = celebrations.today(dt.date(2026, 2, 23))
check("two people share 23 February", sorted(names(_feb23["birthdays"])),
      ["Brandon Lipps", "Todd Swickard"])
check("the CEO's own birthday is his",
      names(celebrations.mine(_feb23, email="todd@smart1marketing.com")),
      ["Todd Swickard"])
check("and the other Todd is not greeted on it",
      celebrations.mine(_feb23, email="tjohnston@smart1marketing.com"), [])
check("a name is matched only when there is no email to match on",
      names(celebrations.mine(_feb23, name="Todd Swickard")), ["Todd Swickard"])
check("and a partial name matches nobody",
      celebrations.mine(_feb23, name="Todd"), [])

section("The page, as the browser receives it")

_anon = Client(application)
_r = _anon.get("/api/celebrations")
check("staff dates of birth are behind the login", _r.status_code, 401)

_in = Client(application)
_in.post("/login", data={"password": "cheers-test-shared", "name": "CI"})
_api = _in.get("/api/celebrations").get_json()
check("the API answers the month", _api["month"], "August")
ok("from the profile table, not the fallback", _api["source"] == "profiles",
   "sync_roster() runs on boot, so the rows are there")
ok("and carries today's slice for the popup", "today_list" in _api)

_page = _in.get("/").get_data(as_text=True)
ok("the dashboard draws the block", 'id="cheer-body"' in _page)
ok("and loads the popup", "/hub-cheers.js" in _page)
ok("the block sits above System checks, which is where it was asked for",
   _page.index("cheer-card") < _page.index("System checks"))
check("the popup script is served", _in.get("/hub-cheers.js").status_code, 200)

# The fifth partner page has been in the repo, served and reachable, since the
# other four arrived — and the dashboard offered four links and a placeholder
# reading "Page coming". The row is drawn from the files on disk now.
section("Every partner page that exists has a button")

from hub import partner                                             # noqa: E402

for _tile in partner.tiles():
    ok(f"{_tile['title']} is on the dashboard",
       (f'href="{_tile["href"]}"' in _page) == _tile["available"])
ok("including the Digital Dictionary, which had no link at all",
   'href="/partner/digital-dictionary"' in _page)

print()
if _failed:
    print(f"{_failed} FAILED, {_passed} passed")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1)
print(f"{_passed} checks passed — the month is read, the gaps are named, "
      "and only today interrupts anybody")
shutil.rmtree(TMP, ignore_errors=True)
