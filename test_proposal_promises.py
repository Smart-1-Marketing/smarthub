"""Monthly promises as a schedule, checked against the work log.

    python3 test_proposal_promises.py

Same shape as the other module tests here: no pytest, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

`hub/proposal_plan.py` lists what a proposal promises every month and a
person keeps each promise once. Then nothing asked about month two: a kept
"monthly sales video" was a fact about the plan and never a question about
the calendar, and the things a client notices when they stop are exactly
these. `hub/proposal_promises.py` turns each kept monthly item into a row per
month since launch and says whether it landed (the activity log has the
work), was marked done by hand, is still due, was missed, or cannot be
measured. This asserts the rules it is built on.

* Every monthly recipe row names a kind, and every module a kind reads as
  evidence is one the work log can name -- a row the record drops is one
  this cannot see either.
* The schedule is derived on read and stored nowhere; the one thing written
  is a mark, applied on every read, with who and when.
* A month is missed once nothing landed by the 25th or once it is over; a
  promise nothing here logs is recorded by hand and says so.
* Housekeeping -- reviewing bids, checking frequency -- is drawn and never
  raised. Only a deliverable reaches the QA report, the health issue and
  the Client 360 count.
* A month the log cannot answer for is not measured, never missed; a log
  that could not be read makes every tracked month not measured and leaves
  the hand-recorded ones answering.
* The report, the health issue and the record read one schedule; a mark
  made on the plan page leaves the health report on the next read rather
  than at tomorrow's rebuild.
"""
import json
import os
import sys
import tempfile
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-promises-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "promises-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["AUDIT_LOG_PATH"] = os.path.join(_TMP, "audit.jsonl")
os.environ.pop("OPENAI_API_KEY", None)

PASS = FAIL = 0


def check(label, got, want=True):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print(f"  FAIL " + label + f"\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print("\n" + title)
    print("-" * 62)


import wsgi                                                          # noqa: E402
import hub.proposal_execution as pe                                  # noqa: E402
import hub.proposal_plan as pp                                       # noqa: E402
import hub.proposal_promises as pr                                   # noqa: E402
import hub.client_brand as client_brand                              # noqa: E402
import hub.client_health as client_health                            # noqa: E402
import hub.proposals as proposals_mod                                # noqa: E402
import hub.auth as auth                                              # noqa: E402
import hub.help as hub_help                                          # noqa: E402
import hub.qa as qa                                                  # noqa: E402
from werkzeug.test import Client as WSGIClient                       # noqa: E402

hub_app = wsgi.hub_app
# The package itself, for the proposal-text hook the run reads at analysis time.
# Reached through sys.modules rather than a second import of a module this file
# already imports by its submodules -- wsgi has loaded it by now.
hub_pkg = sys.modules["hub"]


def _read(*parts):
    """A repo file as text -- opened and closed here, so no check leaves a handle open."""
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


TODAY = date(2026, 9, 13)          # before the 25th: this month is still due
LATE = date(2026, 9, 26)           # after it: this month is missed
CLIENT = "Acme Tyre"


def _log_row(when, module, action, client, **extra):
    """An activity-log row at a chosen date. audit.log() stamps the clock,
    and the schedule is about which month a row fell in."""
    row = {"time": when, "module": module, "type": action, "actor": "t", "client": client}
    row.update(extra)
    with open(os.environ["AUDIT_LOG_PATH"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


# ---------------------------------------------------------------------------
section("Every monthly promise has a kind, and every kind's evidence is work the record can name")
unkinded = [(key, row[0]) for key, rec in pp.RECIPES.items()
            for row in rec.get("monthly") or [] if len(row) < 3 or row[2] not in pp.PROMISE_KINDS]
check("every recipe's monthly rows name a kind in the table", unkinded, [])
check("...and so do the generic monthly rows",
      [r[0] for r in pp.GENERIC_MONTHLY if len(r) < 3 or r[2] not in pp.PROMISE_KINDS], [])
unnamed = [(kind, mod) for kind, rule in pp.PROMISE_KINDS.items()
           for mod, _frags in rule["evidence"] if mod not in client_brand.WORK_KINDS]
check("every evidence module is one the work log can name -- a row it drops is one this cannot see",
      unnamed, [])
check("a report to the client is a deliverable recorded by hand",
      pp.PROMISE_KINDS["report"]["deliverable"] is True and pp.PROMISE_KINDS["report"]["evidence"] == ())
check("reviewing bids is housekeeping, never raised",
      pp.PROMISE_KINDS["optimize"]["deliverable"], False)
check("a kind nobody declared reads as a deliverable recorded by hand",
      pp.promise_kind("something-the-model-wrote"), pp.PROMISE_KIND_UNKNOWN)
items, _ = pp.rule_items({"channels": [{"key": "social", "name": "Social"},
                                        {"key": "youtube_video", "name": "Video"}]})
kinds = {it["title"]: it["kind"] for it in items["monthly"]}
check("the built items carry the kind", kinds.get("Schedule the approved posts"), "social_post")
check("...and the sales video is a video",
      kinds.get("Script, produce and publish this month's YouTube sales video"), "video")
check("an item typed by a person has no kind and is still a promise",
      pp._item("monthly", "Text the sales team the numbers").get("kind"), "")
check("a creative item does not gain a promise kind", "kind" in pp._item("launch", "x"), False)

# ---------------------------------------------------------------------------
section("The months: the first one after launch, through this one, capped")
check("the first promise month is the month after launch", pr.first_month_after(date(2026, 7, 10)), "2026-08")
check("...across a year end", pr.first_month_after(date(2025, 12, 2)), "2026-01")
check("the months run from the first through this one, oldest first",
      pr.months_between("2026-06", TODAY), ["2026-06", "2026-07", "2026-08", "2026-09"])
check("a first month still ahead is no months at all", pr.months_between("2026-10", TODAY), [])
long = pr.months_between("2024-01", TODAY)
check("the walk is capped at the most recent MAX_MONTHS",
      len(long) == pr.MAX_MONTHS and long[-1] == "2026-09")
check("a month is labeled the way a person says it", pr.month_label("2026-09"), "September 2026")
check("...and short with the year only when it is not this one",
      (pr.month_short("2026-09", TODAY), pr.month_short("2025-12", TODAY)), ("Sep", "Dec 2025"))

# ---------------------------------------------------------------------------
section("The schedule: landed, marked, due, missed, not measured -- derived on read")
analysis = {"channels": [{"key": "social", "name": "Social"}, {"key": "seo_ai", "name": "SEO + AI"}]}
plan = pp.build_plan(analysis, "Social Posting Outline $125\nEnhanced SEO $1,400", CLIENT, use_ai=False)
plan = pp.apply_decisions(plan, {"accept": {it["id"]: True for it in plan["monthly"]},
                                 "answers": {"launch_date": "2026-06-10"}})
by_kind = {it["kind"]: it for it in pp.kept_items(plan, "monthly")}
posts, calendar_item, content, review = by_kind["social_post"], by_kind["social_plan"], by_kind["content"], by_kind["review"]
report_item = next(it for it in pp.kept_items(plan, "monthly") if it["kind"] == "report" and it["channel"] == "seo_ai")

# The log reaches back to June; the posts were scheduled in July and September,
# and a blog was written in September.
_log_row("2026-06-02T10:00:00+00:00", "seo", "seo_setup_saved", CLIENT)
_log_row("2026-07-14T10:00:00+00:00", "social_planner", "exported", CLIENT, format="csv")
_log_row("2026-09-03T10:00:00+00:00", "social_planner", "post_pushed", CLIENT)
_log_row("2026-09-05T10:00:00+00:00", "seo", "seo_blog_write", CLIENT)
_log_row("2026-09-06T10:00:00+00:00", "seo", "seo_blog_write", "Somebody Else")


def states(sched, item):
    row = next(i for i in sched["items"] if i["id"] == item["id"])
    return [(m["month"], m["state"]) for m in row["months"]]


s = pr.schedule(pp.resolve(plan), run_id=7, client=CLIENT, today=TODAY)
check("the schedule is measured from the launch date answered", s["measured"] and s["first_month"] == "2026-07")
check("its months run from the first after launch through this one",
      [m["month"] for m in s["months"]], ["2026-07", "2026-08", "2026-09"])
check("the posts landed in July and September and were missed in August",
      states(s, posts), [("2026-07", "landed"), ("2026-08", "missed"), ("2026-09", "landed")])
landed = next(i for i in s["items"] if i["id"] == posts["id"])["months"][2]
check("a landed month names the work -- which tool, which day",
      landed["evidence"][0]["source"] == "Social Content Planner" and landed["evidence"][0]["when"] == "2026-09-03")
check("the calendar approval, never logged, is missed and still due this month",
      states(s, calendar_item), [("2026-07", "missed"), ("2026-08", "missed"), ("2026-09", "due")])
check("the blog counts as the month's SEO work", states(s, content)[2], ("2026-09", "landed"))
check("another client's blog does not", states(s, content)[1], ("2026-08", "missed"))
check("a report is recorded by hand only, and says so",
      next(i for i in s["items"] if i["id"] == report_item["id"])["tracked"], False)
check("past the 25th this month is missed, not due",
      states(pr.schedule(pp.resolve(plan), run_id=7, client=CLIENT, today=LATE), calendar_item)[2],
      ("2026-09", "missed"))
check("housekeeping is drawn with its months...",
      len(next(i for i in s["items"] if i["id"] == review["id"])["months"]), 3)
check("...and never raised", [m["kind"] for m in s["missed_items"] if m["kind"] in ("optimize", "review")], [])
c = s["counts"]
check("the counts keep deliverables apart from housekeeping",
      c["housekeeping_open"] > 0 and c["missed"] == sum(1 for m in s["items"] if m["deliverable"] for x in m["missed"]))
check("missed this month or last is counted apart from older misses",
      c["missed_recent"] + c["missed_older"] == c["missed"] and c["missed_older"] > 0)
check("every missed deliverable-month within the window is named with its mark key",
      all(m["key"] == pr.mark_key(7, m["item"], m["month"]) for m in s["missed_items"])
      and all(m["month"] in ("2026-08", "2026-09") for m in s["missed_items"]))

stored = json.dumps(plan)
check("nothing derived is on the stored plan -- no months, no states, no marks",
      '"landed"' not in stored and "2026-07" not in stored and '"schedule"' not in stored
      and '"months"' not in stored)

# ---------------------------------------------------------------------------
section("A mark is the one thing written, with who and when, applied on read")
got = pr.mark(7, calendar_item["id"], "2026-08", actor="todd@smart1marketing.com", note="approved by phone")
check("marking a month done answers ok with the mark", got["ok"] and got["mark"]["by"] == "todd@smart1marketing.com")
s2 = pr.schedule(pp.resolve(plan), run_id=7, client=CLIENT, today=TODAY)
check("the marked month reads marked on the next read", states(s2, calendar_item)[1], ("2026-08", "marked"))
cell = next(i for i in s2["items"] if i["id"] == calendar_item["id"])["months"][1]
check("...carrying who, when and the note",
      cell["mark"]["by"] == "todd@smart1marketing.com" and cell["mark"]["note"] == "approved by phone"
      and cell["mark"]["at"].startswith("20"))
check("...and it is out of the missed count", pr.counts(s2)["missed"], pr.counts(s)["missed"] - 1)
check("a mark on another run does not read onto this one",
      states(pr.schedule(pp.resolve(plan), run_id=8, client=CLIENT, today=TODAY), calendar_item)[1],
      ("2026-08", "missed"))
check("a month written any other way is refused", pr.mark(7, calendar_item["id"], "August 2026")["ok"], False)
check("a mark with no promise named is refused", pr.mark(7, "", "2026-08")["ok"], False)
check("taking a mark back that was never made says so", pr.unmark(7, calendar_item["id"], "2026-07")["ok"], False)
check("taking it back works", pr.unmark(7, calendar_item["id"], "2026-08")["ok"], True)
check("...and the month is missed again", states(pr.schedule(pp.resolve(plan), run_id=7, client=CLIENT, today=TODAY), calendar_item)[1],
      ("2026-08", "missed"))
pr.mark(7, calendar_item["id"], "2026-08", actor="todd@smart1marketing.com")
check("the marks live in the data directory, through jsonstore",
      os.path.exists(os.path.join(_TMP, "hub", "promise_marks.json")))

# ---------------------------------------------------------------------------
section("A month the log cannot answer for is not measured, never missed")
late_start = pp.apply_decisions(plan, {"answers": {"launch_date": "2026-02-10"}})
s3 = pr.schedule(pp.resolve(late_start), run_id=7, client=CLIENT, today=TODAY)
check("the log reaches back to June", s3["horizon"], "2026-06-02")
check("a tracked promise's months before the horizon are not measured",
      states(s3, posts)[:3], [("2026-03", "not_measured"), ("2026-04", "not_measured"), ("2026-05", "not_measured")])
nm = next(i for i in s3["items"] if i["id"] == posts["id"])["months"][0]
check("...and say why", "reaches back to 2026-06-02" in nm["reason"])
check("June, which the log does reach, is missed", states(s3, posts)[3], ("2026-06", "missed"))
check("a hand-recorded promise is missed either way -- the log was never its witness",
      states(s3, report_item)[0], ("2026-03", "missed"))
broken = {"rows": {}, "horizon": "", "error": "The activity log could not be read.", "scanned": 0}
s4 = pr.schedule(pp.resolve(plan), run_id=7, client=CLIENT, today=TODAY, work=broken)
check("a log that could not be read makes every tracked month not measured",
      all(st == "not_measured" for _m, st in states(s4, posts)))
check("...named on the schedule", s4["log_error"], "The activity log could not be read.")
check("...and the hand-recorded ones still answer", states(s4, report_item)[2], ("2026-09", "due"))
check("the schedule is still measured -- the launch date is what it measures from", s4["measured"], True)

no_date = pp.apply_decisions(plan, {"answers": {"launch_date": ""}})
no_date["questions"] = [q for q in no_date["questions"] if q["key"] != "launch_date"] + \
    [{"key": "launch_date", "question": "When does the campaign launch?", "answer": ""}]
s5 = pr.schedule(pp.resolve(no_date), run_id=7, client=CLIENT, today=TODAY)
check("no launch date is not measured, with the reason", s5["measured"] is False and "launch date" in s5["why"])
check("...and the kept promises are still listed, with no months",
      len(s5["items"]) == len(pp.kept_items(plan, "monthly")) and all(i["months"] == [] for i in s5["items"]))
ahead = pp.apply_decisions(plan, {"answers": {"launch_date": "2026-09-20"}})
s6 = pr.schedule(pp.resolve(ahead), run_id=7, client=CLIENT, today=TODAY)
check("a launch this month has nothing due yet and says when it starts",
      s6["not_started"] and "October 2026" in s6["why"] and s6["measured"])

# ---------------------------------------------------------------------------
section("One reading of what a work row is, read once for the book")
idx = client_brand.work_index()
check("the index buckets rows by client and reports how far back it read",
      CLIENT.lower().replace(" ", "") in idx["rows"] and idx["horizon"] == "2026-06-02T10:00:00+00:00" and idx["error"] == "")
log_rows = client_brand.work_log(CLIENT, limit=100)["items"]
check("...and holds the same rows the client's own work log holds",
      sorted((r["when"], r["action"]) for r in idx["rows"][CLIENT.lower().replace(" ", "")]),
      sorted((r["when"], r["action"]) for r in log_rows))
src = _read("hub", "client_brand.py")
check("work_log() and work_index() read one _work_row()",
      src.count("got = _work_row(e)") == 2 and src.count("for key in CLIENT_KEYS:") >= 1)

# ---------------------------------------------------------------------------
section("Through the run: the schedule served, the mark a route, the count on the record")
TEXT = "Social Posting Outline $125\nEnhanced SEO + AI Optimization $1,400\nMonthly YouTube Sales Video $100"
_FIX = {"p1": {"id": "p1", "title": "Plan", "filename": "plan.pdf", "kind": "file"}}
proposals_mod.list_proposals = lambda client: list(_FIX.values()) if client == CLIENT else []
hub_pkg._proposal_text_for = lambda client, ident: TEXT
with hub_app.app_context():
    run, _created = pe.create_run(CLIENT, "p1", owner="rep@smart1marketing.com", actor="rep")
    RUN_ID = run.id
    monthly_ids = {it["id"]: it for it in pe.plan_for(run)["monthly"]}
    video_id = next(i for i, it in monthly_ids.items() if it["kind"] == "video")
    pe.update_plan(RUN_ID, {"accept": {i: True for i in monthly_ids},
                            "answers": {"launch_date": "2026-06-10"}}, actor="rep")
    full = pe.get_run(RUN_ID).as_dict(full=True)
    sched = (full.get("plan") or {}).get("schedule") or {}
    check("the served plan carries the schedule", sched.get("measured") is True and len(sched.get("items") or []) > 0)
    check("...built from the run's own launch date", sched.get("first_month"), "2026-07")
    check("...and the stored plan does not", "schedule" not in pe.get_run(RUN_ID).plan())
    vid = next(i for i in sched["items"] if i["id"] == video_id)
    check("the sales video, never made, is missed in July and August and due in September",
          [(m["month"], m["state"]) for m in vid["months"]],
          [("2026-07", "missed"), ("2026-08", "missed"), ("2026-09", "due")])

    summary = pe._run_plan_summary(pe.get_run(RUN_ID))
    check("the run summary carries the promise counts, never the months",
          summary["promises"]["measured"] is True and summary["promises"]["missed"] > 0
          and "months" not in summary["promises"])
    check("...naming each missed deliverable-month by mark key",
          all(m["key"].startswith(f"{RUN_ID}|") for m in summary["promises"]["missed_items"])
          and all(m["month"] in ("2026-08", "2026-09") for m in summary["promises"]["missed_items"]))
    book = pe.open_plan_summaries()
    check("the book-wide summaries carry them too",
          book["measured"] and any(r["id"] == RUN_ID and r["promises"]["missed"] for r in book["runs"]))

    staff = WSGIClient(wsgi.application)
    staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"), domain="localhost")
    resp = staff.post(f"/api/proposal-execution/run/{RUN_ID}/promise",
                      json={"item": video_id, "month": "2026-07", "note": "delivered by email"})
    check("the mark route answers with the whole run", resp.status_code == 200 and (resp.get_json() or {}).get("ok"))
    served = resp.get_json()["run"]["plan"]["schedule"]
    vid2 = next(i for i in served["items"] if i["id"] == video_id)
    check("...and the served schedule shows the month marked",
          [(m["month"], m["state"]) for m in vid2["months"]][0], ("2026-07", "marked"))
    check("...with the note kept", vid2["months"][0]["mark"]["note"], "delivered by email")
    ev = pe.events_for_run(RUN_ID, 3)
    check("the run's own history says what was marked", any("Marked done for July 2026" in e["message"] for e in ev))
    resp = staff.post(f"/api/proposal-execution/run/{RUN_ID}/promise",
                      json={"item": "monthly:all:nothing-of-the-kind", "month": "2026-07"})
    check("an item the plan does not keep is refused by name",
          resp.status_code == 400 and "not a monthly promise" in (resp.get_json() or {}).get("error", ""))
    creative_id = pe.plan_for(pe.get_run(RUN_ID))["creative"][0]["id"]
    resp = staff.post(f"/api/proposal-execution/run/{RUN_ID}/promise", json={"item": creative_id, "month": "2026-07"})
    check("...and so is a creative item", resp.status_code, 400)
    resp = staff.post(f"/api/proposal-execution/run/{RUN_ID}/promise",
                      json={"item": video_id, "month": "2026-07", "done": False})
    check("taking the mark back goes through the same route", resp.status_code == 200
          and next(i for i in resp.get_json()["run"]["plan"]["schedule"]["items"] if i["id"] == video_id)["months"][0]["state"] == "missed")
    anon = WSGIClient(wsgi.application)
    check("a stranger is refused", anon.post(f"/api/proposal-execution/run/{RUN_ID}/promise",
                                             json={"item": video_id, "month": "2026-07"}).status_code in (302, 401, 403))

    resp = staff.get(f"/api/client/execution-plan?name={CLIENT.replace(' ', '%20')}")
    row = next((r for r in (resp.get_json() or {}).get("runs") or [] if r["id"] == RUN_ID), {})
    check("the record's API carries the promise counts", (row.get("promises") or {}).get("missed", 0) > 0)

# ---------------------------------------------------------------------------
section("The QA report: missed leads, due is amber, landed and marked are listed, nothing housekeeping")
with hub_app.app_context():
    data = pr.report(today=TODAY)
    check("the book report is measured and reads the open run",
          data["measured"] and any(r["run"] == RUN_ID for r in data["rows"]))
    check("...with this month and last only", set(r["month"] for r in data["rows"]) <= {"2026-08", "2026-09"})
    check("...no housekeeping row", [r for r in data["rows"] if r["kind"] in ("optimize", "review")], [])
    check("...and the older misses counted rather than listed", data["older_missed"] > 0)
    check("the report knows how far back the log reached", data["horizon"], "2026-06-02")
    note = pr.note(data)
    check("the note says the rule, the horizon and what is left off",
          "25th" in note and "2026-06-02" in note and "older missed" in note)

    check("the report is registered on /qa under Clients",
          qa.REPORTS.get("monthly-promises", {}).get("group"), "Clients")
    out = qa.run("monthly-promises")
    check("it answers measured with columns and rows", out.get("measured") is True and bool(out["rows"]))
    check("every row has one cell per heading", all(len(r) == len(out["columns"]) for r in out["rows"]))
    groups = [r[0]["group"] for r in out["rows"] if isinstance(r[0], dict) and r[0].get("group")]
    check("missed leads and due follows", groups[0].startswith("Missed") and groups[1].startswith("Due"))
    check("a missed row is drawn red and a due row amber",
          "bad" in out["row_styles"] and "warn" in out["row_styles"])
    marks_cells = [r[6] for r in out["rows"] if isinstance(r[6], dict)]
    check("an open month offers Mark done, carrying the run, the item and the month",
          any("promise_mark" in c and set(c["promise_mark"]) == {"run", "item", "month"} for c in marks_cells))
    check("a landed month offers nothing to mark -- the log is the record",
          all(r[6] == "" for r in out["rows"] if r[3] == "landed"))
    check("a hand-recorded promise says so in its evidence cell",
          any(isinstance(r[4], dict) and "recorded by hand" in r[4].get("text", "") for r in out["rows"]))

    real_open = pe.open_runs

    def _refuse(limit=2000):
        raise RuntimeError("table gone")
    pe.open_runs = _refuse
    try:
        down = qa.run("monthly-promises")
    finally:
        pe.open_runs = real_open
    check("a runs table that will not answer is not measured, never an all-clear",
          down.get("measured") is False and not down["rows"] and "could not be read" in down["note"])

    from hub import report_cache
    qa.forget("monthly-promises")
    before = qa.run_cached("monthly-promises")
    check("the cache holds a measured run", report_cache.is_answer(before))
    held = {"dropped": []}
    real_forget = qa.forget
    qa.forget = lambda *keys: held["dropped"].extend(keys) or real_forget(*keys)
    try:
        pr.mark(RUN_ID, video_id, "2026-08", actor="todd@smart1marketing.com")
    finally:
        qa.forget = real_forget
    check("a mark drops the day's cached report, beside the write", "monthly-promises" in held["dropped"])

# ---------------------------------------------------------------------------
section("The health report: one issue per missed promise-month, and a mark on the plan clears it on read")
with hub_app.app_context():
    plans, err = client_health._plans()
    key = client_health._client_key(CLIENT)
    check("the health report reads the plans with their promise counts", err == "" and key in plans)
    issues = client_health._plan_issues(plans[key])
    promise_issues = [i for i in issues if i["kind"] == "plan_promise"]
    check("a missed deliverable-month raises its own issue", len(promise_issues) > 0)
    check("...titled with the promise and the month",
          all(" — " in i["title"] and "2026" in i["title"] for i in promise_issues))
    check("...with the plan's own link", all(i["link"] == f"/proposal-execution?run={RUN_ID}" for i in promise_issues))
    check("...and a subject that is the mark key", all(i["subject"].startswith(f"{RUN_ID}|") for i in promise_issues))
    check("the kind names the screen it is fixed on",
          client_health.ISSUE_KINDS["plan_promise"]["where"] == "Proposal Execution")
    check("housekeeping raises nothing",
          all("pacing" not in i["title"].lower() and "review what the proposal" not in i["title"].lower()
              for i in promise_issues))
    marked_key = pr.mark_key(RUN_ID, video_id, "2026-08")
    check("the August video, marked on the plan page, is no longer raised",
          all(i["subject"] != marked_key for i in promise_issues))
    # A mark made after the day's build: the overlay takes it off on read.
    fake_issue = client_health._issue("plan_promise", marked_key, "Video — August 2026", "Nothing landed.")
    rows = client_health._apply_overlay({"rows": [{"key": key, "issues": [fake_issue]}]},
                                        owner_index={}, mark_index={}, note_index={}, user_index={},
                                        promise_index=pr.marks())
    check("a mark made since the build moves the issue to handled on read",
          rows[0]["issue_count"] == 0 and rows[0]["handled"][0]["mark"]["state"] == "done"
          and rows[0]["handled"][0]["mark"]["by"] == "todd@smart1marketing.com")
    rows = client_health._apply_overlay({"rows": [{"key": key, "issues": [fake_issue]}]},
                                        owner_index={}, mark_index={}, note_index={}, user_index={},
                                        promise_index={})
    check("...and stays open with no mark", rows[0]["issue_count"], 1)

# ---------------------------------------------------------------------------
section("The screens draw the server's schedule and decide nothing themselves")
tpl = _read("hub", "templates", "proposal_execution.html")
check("the plan page draws a month strip on each kept monthly item", "function promiseStrip" in tpl and "pex-months" in tpl)
check("...with a press that posts to the plan's own route",
      "function planPromise" in tpl and "/promise`" in tpl)
check("...and says how a hand-recorded promise is recorded", "recorded by hand" in tpl)
check("the page decides no state of its own -- it reads the server's",
      "m.state==='landed'" in tpl and "DUE_DAY" not in tpl and ">25" not in tpl)
check("the monthly list carries a bubble",
      'data-help="proposal_execution.plan.promises"' in tpl
      and "proposal_execution.plan.promises" in hub_help.as_json()["help"])
c360 = _read("hub", "templates", "client360.html")
check("Client 360 prints missed and due promises as pills", "monthly promise" in c360 and "pr.due" in c360)
check("...and prints nothing where there is no launch date to measure from", "pr.measured!==false" in c360)
qat = _read("hub", "templates", "qa_report.html")
check("the QA page draws the mark control and posts to the same route",
      "promise_mark" in qat and "/promise'" in qat and "qa-promise-go" in qat)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
