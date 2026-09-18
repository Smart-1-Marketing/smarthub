"""History for the fact table: thirty days further back, on request or nightly.

    python3 test_reports_backfill.py

No pytest, no new dependencies, a throwaway reports database and stand-ins
for the pull modules: ``backfill._pull_module`` is replaced whole, so every
window the runner asks for is recorded and nothing reaches a platform.

What it holds:

  * the next window ends the day before the oldest native day on file, and
    after a landed window the day before the ledger's ``reached``;
  * a ``today``-and-``days`` platform is called with the window's end as
    today and thirty days, the same call the nightly makes;
  * a landed window moves the ledger; an error or a pending file does not;
  * an empty window marks the platform complete and nightly leaves it out,
    and rows landing again clear it;
  * nightly is a flag per platform and a platform is asked once a night;
  * Amazon DSP is refused by name, not offered a button that does nothing;
  * the scheduler job waits for its window and the button forces it, on
    the background lane; the Reports index draws the card and its buttons.
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_backfill_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-backfill-test"
os.environ["HUB_SCHEDULER"] = "false"

_passed = _failed = 0


def check(label, got, want=True, note=None):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}"
              + (f"\n          note: {note!r}" if note is not None else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from modules.reports import backfill, store                          # noqa: E402
_reports_testdb.reset(store)

TODAY = date(2026, 9, 17)


class _FixedDate(date):
    """The module's clock, pinned: the scheduler job and the buttons call
    with no ``today`` and read ``date.today()``, and a test that ran at
    00:17 UTC on the 18th with the 17th in its expectations found out."""
    @classmethod
    def today(cls):
        return TODAY


backfill.date = _FixedDate


def _row(platform, day, campaign="c1", spend=1.0, source="native"):
    return {"platform": platform, "account_id": "a1", "campaign_id": campaign,
            "date": day, "campaign_name": "camp", "spend": spend, "impressions": 10,
            "clicks": 1, "conversions": 0, "source": source}


# The stand-in pulls: each records the call and lands what the test tells it.
CALLS = []
LAND = {"google": 5, "stackadapt": 3, "bing": 0}


class _Pull:
    def __init__(self, platform):
        self.platform = platform

    def pull(self, days, today):
        CALLS.append((self.platform, days, today))
        n = LAND.get(self.platform, 0)
        rows = [_row(self.platform, today - timedelta(days=i)) for i in range(n)]
        if self.platform == "groundtruth":
            return {"ok": False, "rows": 0, "error": "HTTP 500 from the platform"}
        if rows:
            store.upsert_rows(rows)
        return {"ok": True, "rows": len(rows), "error": ""}


class _TTD:
    calls = 0

    def pull_window(self, start, end):
        CALLS.append(("ttd", start, end))
        _TTD.calls += 1
        if _TTD.calls == 1:
            return {"ok": False, "rows": 0, "pending": True, "note": "history report scheduled"}
        store.upsert_rows([_row("ttd", end), _row("ttd", start)])
        return {"ok": True, "rows": 2, "pending": False, "error": ""}


def _fake_module(name):
    return _TTD() if name == "ttd" else _Pull({"google_ads_perf": "google", "stackadapt": "stackadapt",
                                              "audiogo": "audiogo", "bing": "bing",
                                              "groundtruth": "groundtruth"}[name])


backfill._pull_module = _fake_module

# ------------------------------------------------------------- the window
section("The next window ends the day before what is on file")

w = backfill.window_for("google", TODAY)
check("with nothing on file the window is the thirty days to yesterday", w, (date(2026, 8, 18), date(2026, 9, 16)))
store.upsert_rows([_row("google", date(2026, 8, 15)), _row("google", date(2026, 9, 10)),
                   _row("google", date(2026, 7, 1), source="csv")])
w = backfill.window_for("google", TODAY)
check("with native rows it ends the day before the oldest native day, not the CSV's",
      w, (date(2026, 7, 16), date(2026, 8, 14)))
check("the ledger is empty before a run", backfill.entry("google"), {})

# ---------------------------------------------------------------- a run
section("A landed window moves the ledger")

res = backfill.run_platform("google", today=TODAY, actor="Todd")
check("the pull was asked for thirty days ending at the window's end", CALLS[-1], ("google", 30, date(2026, 8, 14)))
check("...and answered", (res["ok"], res["rows"], res["start"], res["end"]), (True, 5, "2026-07-16", "2026-08-14"))
e = backfill.entry("google")
check("the ledger reached the window's start", e["reached"], "2026-07-16")
check("...not complete, with the run on it", (e["complete"], e["last_rows"], e["actor"], bool(e["finished_at"])),
      (False, 5, "Todd", True))
w = backfill.window_for("google", TODAY)
check("the next window ends the day before reached", w, (date(2026, 6, 16), date(2026, 7, 15)))

res = backfill.run_platform("groundtruth", today=TODAY)
check("a failed pull answers the error", (res["ok"], "HTTP 500" in res["error"]), (False, True))
check("...and does not move the ledger", backfill.entry("groundtruth").get("reached"), None)
check("...but records it", backfill.entry("groundtruth")["last_error"], "HTTP 500 from the platform")

res = backfill.run_platform("bing", today=TODAY)
check("an empty window marks the platform complete", (res["ok"], res["rows"], res["complete"]), (True, 0, True))
check("...with reached at the window's start", backfill.entry("bing")["reached"], "2026-08-18")

res = backfill.run_platform("amazon_dsp", today=TODAY)
check("Amazon DSP is refused by name", (res["ok"], "Not wired" in res["error"]), (False, True))
res = backfill.run_platform("nope", today=TODAY)
check("...and so is a platform that is not a native pull", res["error"], "nope is not a native pull.")

# -------------------------------------------------------------- pending
section("A Trade Desk window is pending until the file lands")

res = backfill.run_platform("ttd", today=TODAY)
check("the first call schedules the report and is pending", (res["ok"], res["pending"], res["rows"]), (True, True, 0))
check("...the ledger keeps the window, not reached",
      (backfill.entry("ttd").get("reached"), backfill.entry("ttd")["pending"]["start"]), (None, "2026-08-18"))
w = backfill.window_for("ttd", TODAY)
check("...so the next run asks for the same window", w, (date(2026, 8, 18), date(2026, 9, 16)))
res = backfill.run_platform("ttd", today=TODAY)
check("the second call lands the file", (res["ok"], res["pending"], res["rows"]), (True, False, 2))
check("...and the ledger moves", (backfill.entry("ttd")["reached"], backfill.entry("ttd")["pending"]), ("2026-08-18", {}))

# -------------------------------------------------------------- nightly
section("Nightly: a flag per platform, once a night, until complete")

check("nothing is due with no flags set", backfill.due_nightly(TODAY), [])
backfill.set_nightly("google", True)
backfill.set_nightly("bing", True)
check("turning nightly on clears complete", backfill.entry("bing")["complete"], False)
check("both are due", backfill.due_nightly(date(2026, 9, 18)), ["google", "bing"])
try:
    backfill.set_nightly("amazon_dsp", True)
    refused = ""
except ValueError as exc:
    refused = str(exc)
check("nightly for Amazon DSP is refused by name", "Not wired" in refused, note=refused)
CALLS.clear()
out = backfill.run_nightly(today=date(2026, 9, 18))
check("the nightly run pulls each due platform's next window", sorted(out["platforms"]), ["bing", "google"])
check("...google thirty days further back", [c for c in CALLS if c[0] == "google"], [("google", 30, date(2026, 7, 15))])
check("...and bing, empty again, is complete", out["complete"], ["bing"])
check("a platform that ran today is not asked again", backfill.due_nightly(date(2026, 9, 18)), [])
check("...and google is due again tomorrow, bing is not (complete)", backfill.due_nightly(date(2026, 9, 19)), ["google"])
backfill.set_nightly("google", False)
check("nightly off leaves it out", backfill.due_nightly(date(2026, 9, 19)), [])

# ----------------------------------------------------------------- rows
section("What the index prints")

rows = {r["platform"]: r for r in backfill.rows(TODAY)}
check("one row per native platform, Amazon DSP included and marked",
      (sorted(rows), rows["amazon_dsp"]["supported"], rows["amazon_dsp"]["state"]),
      (sorted(backfill.PLATFORMS), False, "unsupported"))
check("google: oldest day on file is the CSV's, own API back to the ledger's reached",
      (rows["google"]["oldest_any"], rows["google"]["reached"], rows["google"]["state"]),
      ("2026-07-01", "2026-06-16", "partial"))
check("bing is complete", (rows["bing"]["state"], rows["bing"]["complete"]), ("complete", True))
check("groundtruth failed, saying why", (rows["groundtruth"]["state"], rows["groundtruth"]["last_error"]),
      ("error", "HTTP 500 from the platform"))
check("a platform never run says so", rows["audiogo"]["state_label"], "not started")

# ------------------------------------------------------- the scheduler
section("The scheduler job and the button")

from hub import scheduler                                            # noqa: E402
from flask import Flask                                              # noqa: E402

check("the job is registered on the background lane",
      "reports_backfill" in scheduler.JOBS and "reports_backfill" in scheduler.BACKGROUND_JOBS)
backfill.set_nightly("google", True)
_real_hour = scheduler._eastern_hour
try:
    scheduler._eastern_hour = lambda: 14
    r = scheduler.job_reports_backfill(Flask("t"))
    check("at 2 PM the nightly job waits for its window, naming what is due",
          "overnight window" in r.get("skipped", "") and r.get("due") == ["google"], True, note=r)
    scheduler._eastern_hour = lambda: 5
    CALLS.clear()
    r = scheduler.job_reports_backfill(Flask("t"))
    check("at 5 AM it runs the due platforms", sorted(r.get("platforms", {})), ["google"])
    r = scheduler.job_reports_backfill(Flask("t"))
    check("...and not again the same day", r.get("skipped"), "nothing set to nightly is still incomplete")
finally:
    scheduler._eastern_hour = _real_hour

CALLS.clear()
res = scheduler.backfill_now(["stackadapt"], actor="Ann")
check("the button starts a history pull in the background", res["started"] and "stackadapt" in res["note"], note=res)
t = scheduler._background.get("reports_backfill")
if t is not None:
    t.join(20)
check("...which pulled stackadapt's next window", [c[0] for c in CALLS], ["stackadapt"])
check("...by Ann", backfill.entry("stackadapt")["actor"], "Ann")
res = scheduler.backfill_now(["amazon_dsp"], actor="Ann")
check("the button refuses Amazon DSP by name", res["started"] is False and "Not wired" in res["note"], note=res)

# ---------------------------------------------------------- the page
section("The Reports index draws the History card")

from werkzeug.test import Client                                     # noqa: E402
from hub import auth                                                 # noqa: E402
from modules.reports import app as reports_app                       # noqa: E402

c = Client(reports_app.app)
c.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")
page = c.get("/").get_data(as_text=True)
check("the card is there", 'id="history"' in page and "Pull 30 more days" in page)
_g = {r["platform"]: r for r in backfill.rows()}["google"]
check("...with google's reach and its oldest day on file (native rows are older than the CSV's now)",
      _g["reached"] in page and _g["oldest_any"] in page and _g["oldest_any"] < "2026-07-01", note=_g)
check("...bing complete, twice over", "nothing before 2026-07-19" in page)
check("...and Amazon DSP marked not wired", "not wired" in page and "Not wired for history yet" in page)
r = c.post("/backfill/bing/nightly", data={"on": "1"}, environ_base={"s1hub.user": "Todd"})
check("the nightly toggle answers with a redirect to the card", (r.status_code, "#history" in r.headers.get("Location", "")), (302, True))
check("...and set the flag", backfill.entry("bing")["nightly"], True)
r = c.post("/backfill/amazon_dsp/nightly", data={"on": "1"})
check("...refusing Amazon DSP in the banner", "error=" in r.headers.get("Location", ""))
r = c.post("/backfill/audiogo", environ_base={"s1hub.user": "Todd"})
check("the pull button answers at once", (r.status_code, "saved=" in r.headers.get("Location", "")), (302, True), note=r.headers.get("Location"))
t = scheduler._background.get("reports_backfill")
if t is not None:
    t.join(20)
check("...and audiogo's window was pulled", backfill.entry("audiogo").get("reached"), "2026-08-18")
r = c.post("/backfill/nope")
check("an unknown platform is a 404", r.status_code, 404)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
