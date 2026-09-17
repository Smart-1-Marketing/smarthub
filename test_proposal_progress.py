"""Where each launch task and creative item on a plan stands: done, landed,
overdue -- and the launch date finally being a date something checks.

    python3 test_proposal_progress.py

Same shape as the other module tests here: no pytest, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

`hub/proposal_plan.resolve()` put a due date on every kept launch task and
creative item, the kickoff document printed them, and nothing read them
back: a task due last Tuesday looked identical to one due next month,
there was no way to say a task was done, and a banner set the Display Ad
Builder had already delivered sat on the plan exactly as it did the day
the plan was built. `hub/proposal_progress.py` is the reading, and each
half of it has a way of being confidently wrong:

* **A done mark is a press with a name on it**, stored on the item and
  the only thing written; a mark on an item nobody kept is a tick on
  nothing and is refused by name, and a monthly promise is sent to its
  own month strip.
* **Landed is derived on every read and never stored** -- the tool that
  makes the item, since the run was made, following the supplier answer:
  the client uploading proves what the client supplies, our tools prove
  what we produce, and a display pack cannot close the social graphic
  beside it because the evidence is keyed on the tool and not the kind.
* **Overdue is a fact about the calendar**, so it still counts when the
  log could not be read -- and the item says whether it landed is not
  known rather than reading the silence as nothing having arrived.
* **The overdue item reaches My Clients one issue per item**, keyed so a
  Done on the plan page clears it on read, with a fingerprint the passing
  days cannot move.
"""
import json
import os
import sys
import tempfile
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-progress-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "progress-test")
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
        print("  FAIL " + label + f"\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print("\n" + title)
    print("-" * 62)


import wsgi                                                          # noqa: E402
import hub.proposal_execution as pe                                  # noqa: E402
import hub.proposal_plan as pp                                       # noqa: E402
import hub.proposal_progress as prog                                 # noqa: E402
import hub.proposals as proposals_mod                                # noqa: E402
import hub.audit as audit                                            # noqa: E402
import hub.auth as auth                                              # noqa: E402
import hub.help as hub_help                                          # noqa: E402
import hub.client_brand as client_brand                              # noqa: E402
import hub.client_health as client_health                            # noqa: E402
from werkzeug.test import Client as WSGIClient                       # noqa: E402

hub_app = wsgi.hub_app
hub_pkg = sys.modules["hub"]


def _read(*parts):
    """A repo file as text -- opened and closed here, so no check leaves a handle open."""
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _log_at(when, module, action, client, **extra):
    """An activity-log row at a chosen date.

    The floor under the evidence is the run's own day, so an entry from before
    the run has to carry its date. Written through audit.log() rather than
    appended to the JSONL file, because the log's backend is the database now
    and a row in that file is one no reader looks at -- this seeding went on
    "passing" against it, which is a check that cannot fail. `time` in the
    extras is what sets the date; log() documents that it wins.
    """
    from hub import audit
    audit.log(module, action, actor="t", client=client, time=when, **extra)


CLIENT = "Acme Tyre"
TEXT = ("Retargeting display $600\nSocial Posting Outline $125\n"
        "Enhanced SEO + AI Optimization $1,400\nMonthly YouTube Sales Video $100")
_FIX = {"p1": {"id": "p1", "title": "Acme Tyre plan", "filename": "plan.pdf", "kind": "file"}}
proposals_mod.list_proposals = lambda client: list(_FIX.values()) if client == CLIENT else []
hub_pkg._proposal_text_for = lambda client, ident: TEXT

TODAY = date(2026, 9, 14)


def _served(run_id):
    return pe.get_run(run_id).as_dict(full=True)["plan"]


def _item(plan, ident):
    return next(it for name in pp.LISTS for it in plan.get(name) or [] if it["id"] == ident)


# ---------------------------------------------------------------------------
section("The rule, driven with the clock")
base = {"id": "x", "list": "launch", "title": "T", "accepted": True, "due": "2026-09-10"}
check("a dropped item is not needed, whatever its date or its evidence",
      prog.status_of({**base, "accepted": False}, today=TODAY, landed=True)[0], prog.DROPPED)
check("an unreviewed item is to review and never overdue",
      prog.status_of({**base, "accepted": None}, today=TODAY, landed=False)[0], prog.TO_REVIEW)
check("a done mark beats landed evidence",
      prog.status_of({**base, "done": {"by": "t", "at": "x"}}, today=TODAY, landed=True)[0], prog.DONE)
check("landed beats the date", prog.status_of(base, today=TODAY, landed=True)[0], prog.LANDED)
check("past its date with neither, it is overdue and says by how many days",
      prog.status_of(base, today=TODAY, landed=False), (prog.OVERDUE, 4))
check("on its date it is still open",
      prog.status_of({**base, "due": "2026-09-14"}, today=TODAY, landed=False)[0], prog.OPEN)
check("no date is never overdue", prog.status_of({**base, "due": ""}, today=TODAY, landed=False)[0], prog.OPEN)

banner = {"kind": "image", "channel": "retargeting", "list": "creative", "accepted": True}
check("a client-supplied item is proved by the client uploading and by no tool of ours",
      prog.evidence_rules({**banner, "supplier": "client"}) == prog.CLIENT_EVIDENCE)
ours = prog.evidence_rules({**banner, "supplier": "smart1"})
check("a Smart 1 item is proved by the tool that makes it and never by an upload",
      any(m == "display_ads" for m, _ in ours) and not any(m == "image_picker" for m, _ in ours))
either = prog.evidence_rules(banner)
check("an item nobody has answered for is proved by either",
      any(m == "display_ads" for m, _ in either) and any(m == "image_picker" for m, _ in either))
check("the evidence is keyed on the tool, not the kind: a social graphic is not the Display Ad Builder's",
      prog.tool_key({"kind": "image", "channel": "social"}) == "social"
      and not any(m == "display_ads" for m, _ in prog.evidence_rules({"kind": "image", "channel": "social"})))
check("every tool a creative item can point at has evidence the log can prove it by",
      sorted(pp.CREATIVE_TOOLS) == sorted(prog.TOOL_EVIDENCE))
named = {m for pairs in prog.TOOL_EVIDENCE.values() for m, _ in pairs} | {m for m, _ in prog.CLIENT_EVIDENCE}
check("...and every module named is one the work log can name, or the row never reaches the index",
      sorted(m for m in named if m not in client_brand.WORK_KINDS), [])

rows = [{"when": "2026-09-12T10:00:00", "module": "display_ads", "action": "creative_attached", "source": "Display Ad Builder", "detail": "8 sizes"},
        {"when": "2026-09-13T10:00:00", "module": "display_ads", "action": "animation_attached", "source": "Display Ad Builder", "detail": "gif"},
        {"when": "2026-01-05T10:00:00", "module": "display_ads", "action": "creative_attached", "source": "Display Ad Builder", "detail": "old"},
        {"when": "2026-09-13T11:00:00", "module": "commercial_builder", "action": "commercial_approved", "source": "Commercial Builder", "detail": "spot"}]
check("evidence is newest first, since the run and nothing older, and only this tool's",
      [e["detail"] for e in prog.evidence_for(banner, rows, since="2026-09-01")], ["gif", "8 sizes"])
check("...capped, because a tag is a sentence and not a log",
      len(prog.evidence_for(banner, rows * 3, since="")), prog.MAX_EVIDENCE)

copy_plan = {"creative": [{"id": "c1", "list": "creative", "title": "Search ads", "kind": "copy",
                           "channel": "paid_search", "accepted": True, "source": "rule"}],
             "launch": [], "monthly": [], "questions": [], "answers": {}}
quiet = {"rows": {}, "horizon": "", "scanned": 0, "error": ""}
out = prog.apply(copy_plan, client="Nobody Co", run_id=9, today=TODAY, work=quiet,
                 task_states={"paid_search_ads": "completed"})
check("copy closes from the board task that drafts it",
      out["creative"][0]["status"] == prog.LANDED and out["creative"][0]["landed"]["source"] == "the board")
out = prog.apply(copy_plan, client="Nobody Co", run_id=9, today=TODAY, work=quiet,
                 task_states={"paid_search_ads": "running"})
check("...and not before the task is done", out["creative"][0]["status"], prog.OPEN)

late_plan = {"creative": [{**banner, "id": "b1", "title": "Banners", "source": "rule", "due": "2026-08-20"}],
             "launch": [], "monthly": [], "questions": [], "answers": {}}
broken = prog.apply(late_plan, client="Nobody Co", run_id=9, today=TODAY,
                    work={"rows": {}, "horizon": "", "scanned": 0, "error": "The activity log could not be read."})
row = broken["creative"][0]
check("a log that could not be read is said, and the date still counts",
      broken["resolved"]["progress"]["measured"] is False and row["status"] == prog.OVERDUE
      and row["evidence_unknown"] is True and "could not be read" in broken["resolved"]["progress"]["log_error"])

# ---------------------------------------------------------------------------
section("The decision: a done mark is a press with a name on it")
with hub_app.app_context():
    run, _created = pe.create_run(CLIENT, "p1", owner="rep@smart1marketing.com", actor="rep")
    RUN_ID = run.id
    plan = pe.plan_for(run)
    launch_ids = [it["id"] for it in plan["launch"]]
    first = launch_ids[0]
    monthly_id = plan["monthly"][0]["id"]
    try:
        pp.apply_decisions(plan, {"done": {first: True}}, actor="t")
        refused = ""
    except ValueError as exc:
        refused = str(exc)
    check("a done mark on an item nobody kept is refused by name", "Keep" in refused and "tick on nothing" in refused)
    try:
        pp.apply_decisions(plan, {"done": {monthly_id: True}}, actor="t")
        refused = ""
    except ValueError as exc:
        refused = str(exc)
    check("a monthly promise is refused and sent to its own month strip", "strip" in refused)
    for bad, word in (({"done": {"no-such": True}}, "No plan item"), ({"done": {first: "yes"}}, "true or false"),
                      ({"done": ["x"]}, "map item ids")):
        try:
            pp.apply_decisions(plan, bad, actor="t")
            refused = ""
        except ValueError as exc:
            refused = str(exc)
        check(f"refused by name: {word}", word in refused)
    kept = pp.apply_decisions(plan, {"accept": {first: True}, "done": {first: True}}, actor="todd")
    row = _item(kept, first)
    check("keeping and finishing is one press, and the mark carries who and when",
          row["done"]["by"] == "todd" and row["done"]["at"][:4].isdigit())
    check("the stored item carries the mark and nothing derived",
          set(row) & {"status", "status_label", "landed", "landed_evidence", "overdue_days", "evidence_unknown"}, set())
    undone = pp.apply_decisions(kept, {"done": {first: False}}, actor="todd")
    check("taking it back removes the mark", "done" not in _item(undone, first))
    fresh = pp.build_plan(run.analysis(), TEXT, CLIENT, use_ai=False)
    carried = pp.carry_forward(fresh, kept)
    check("a done mark follows the item onto a superseding plan, like its verdict",
          (_item(carried, first).get("done") or {}).get("by"), "todd")

# ---------------------------------------------------------------------------
section("The run: overdue against the calendar, done from the page, landed from the log")
with hub_app.app_context():
    staff = WSGIClient(wsgi.application)
    staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"), domain="localhost")
    plan = pe.plan_for(pe.get_run(RUN_ID))
    accept = {it["id"]: True for name in ("creative", "launch") for it in plan[name]}
    past = (date.today() - timedelta(days=10)).isoformat()
    resp = staff.post(f"/api/proposal-execution/run/{RUN_ID}/plan",
                      json={"accept": accept, "answers": {"launch_date": past}})
    check("the plan route still answers with the run", resp.status_code == 200 and (resp.get_json() or {}).get("ok"))
    served = resp.get_json()["run"]["plan"]
    g = served["resolved"]["progress"]
    kept_n = g["lists"]["creative"]["kept"] + g["lists"]["launch"]["kept"]
    check("with the launch date ten days gone, every kept item is past due",
          g["measured"] is True and g["overdue"] == kept_n and kept_n > 0)
    check("...each named by run and item, the key a Done will clear",
          all(o["key"].startswith(f"{RUN_ID}|") and o["days"] > 0 for o in g["overdue_items"])
          and len(g["overdue_items"]) == g["overdue"])
    stored = pe.get_run(RUN_ID).plan()
    check("the stored plan carries no status, no evidence and no progress",
          "resolved" not in stored
          and not any(k in it for name in pp.LISTS for it in stored[name] for k in ("status", "landed", "landed_evidence")))
    early = pe._resolved_plan(pe.get_run(RUN_ID), today=date.today() - timedelta(days=40))
    check("driven back before the dates, the same plan reads open",
          all(it["status"] == prog.OPEN for it in early["launch"] + early["creative"] if it["accepted"] is True))

    resp = staff.post(f"/api/proposal-execution/run/{RUN_ID}/plan", json={"done": {first: True}})
    served = resp.get_json()["run"]["plan"]
    row = _item(served, first)
    # Who the route records is the session's own answer -- a shared-password
    # session has no account behind it and is named as shared, the
    # hub/ad_copy.py refusal -- so what is asserted is that a name is written,
    # and that the same name is what every other screen then reads.
    ACTOR = row["done"]["by"]
    check("Done from the page records who pressed it and the day",
          row["status"] == prog.DONE and bool(ACTOR) and row["done"]["at"][:10] == date.today().isoformat())
    check("...the run's history says so", any("marked done" in e["message"] for e in pe.events_for_run(RUN_ID, 3)))
    check("...and the past-due count dropped by one", served["resolved"]["progress"]["overdue"], g["overdue"] - 1)

    banner_id = next(it["id"] for it in served["creative"] if it["channel"] == "retargeting")
    social_id = next(it["id"] for it in served["creative"] if it["channel"] == "social")
    video_id = next(it["id"] for it in served["creative"] if it["kind"] == "video")
    _log_at("2025-01-05T10:00:00+00:00", "commercial_builder", "commercial_approved", CLIENT, detail="last year's spot")
    audit.log("display_ads", "creative_attached", client=CLIENT, detail="8 sizes", actor="erik")
    served = _served(RUN_ID)
    b = _item(served, banner_id)
    check("a display pack attached to the client since the run closes the banner set, named with the tool and the day",
          b["status"] == prog.LANDED and b["landed"]["source"] == "Display Ad Builder"
          and b["landed"]["detail"] == "8 sizes" and b["landed"]["when"] == date.today().isoformat())
    check("...and not the social graphic beside it, which a different tool makes",
          _item(served, social_id)["status"], prog.OVERDUE)
    check("a commercial approved last year, before the run, is not this plan's video",
          _item(served, video_id)["status"], prog.OVERDUE)
    audit.log("commercial_builder", "commercial_approved", client=CLIENT, detail="the spot", actor="erik")
    served = _served(RUN_ID)
    check("one approved since the run is", _item(served, video_id)["status"], prog.LANDED)
    check("the served plan counts it", served["resolved"]["progress"]["landed"], 2)

    video_channel = _item(served, video_id)["channel"]
    resp = staff.post(f"/api/proposal-execution/run/{RUN_ID}/plan",
                      json={"answers": {f"creative_supply:{video_channel}": "client"}})
    served = resp.get_json()["run"]["plan"]
    check("once the client is supplying it, our own approval is no longer the evidence",
          _item(served, video_id)["status"], prog.OVERDUE)
    audit.log("image_picker", "client_upload", client=CLIENT, source="dropbox", filename="spot.mp4")
    served = _served(RUN_ID)
    v = _item(served, video_id)
    check("the client uploading through their link is", v["status"] == prog.LANDED and v["landed"]["module"] == "image_picker")

    resp = staff.post(f"/api/proposal-execution/run/{RUN_ID}/plan", json={"done": {banner_id: True}})
    check("a done mark beats the landed evidence on the same item",
          _item(resp.get_json()["run"]["plan"], banner_id)["status"], prog.DONE)
    resp = staff.post(f"/api/proposal-execution/run/{RUN_ID}/plan", json={"done": {banner_id: False}})
    check("...and taking it back shows the evidence again",
          _item(resp.get_json()["run"]["plan"], banner_id)["status"], prog.LANDED)

# ---------------------------------------------------------------------------
section("The record, the report and My Clients")
with hub_app.app_context():
    summary = pe._run_plan_summary(pe.get_run(RUN_ID))
    p = summary["progress"]
    check("the run summary carries the progress counts and the past-due items by key, never the plan",
          p["measured"] is True and p["overdue"] > 0 and p["done"] == 1 and p["landed"] == 2
          and all(set(o) >= {"key", "id", "list", "title", "due", "days"} for o in p["overdue_items"])
          and "creative" not in summary)
    book = pe.open_plan_summaries()
    check("the book-wide summaries carry them too",
          book["measured"] and any(r["id"] == RUN_ID and r["progress"]["overdue"] == p["overdue"] for r in book["runs"]))
    issues = client_health._plan_issues([summary])
    late = [i for i in issues if i["kind"] == "plan_overdue"]
    check("one issue per past-due item, keyed on the item so a Done can clear it",
          len(late) == p["overdue"] and sorted(i["subject"] for i in late) == sorted(o["key"] for o in p["overdue_items"]))
    check("...naming which list it is on and how late it is",
          all(("launch task" in i["title"] or "creative" in i["title"]) and "past due" in i["title"] for i in late))
    aged = dict(summary, progress=dict(p, overdue_items=[dict(o, days=o["days"] + 1) for o in p["overdue_items"]]))
    late2 = [i for i in client_health._plan_issues([aged]) if i["kind"] == "plan_overdue"]
    check("a day passing moves the title and not the fingerprint, so a mark made on My Clients survives the morning",
          sorted(i["fingerprint"] for i in late) == sorted(i["fingerprint"] for i in late2)
          and sorted(i["title"] for i in late) != sorted(i["title"] for i in late2))
    kind = client_health.ISSUE_KINDS.get("plan_overdue") or {}
    check("the issue kind names the screen it is fixed on, and it is not the report",
          bool(kind.get("label") and kind.get("where") and kind.get("href"))
          and not kind["href"].startswith("/my-clients"))
    idx = pe.done_index()
    check("the done index carries the marks on the open plans by the same key",
          (idx.get(prog.done_key(RUN_ID, first)) or {}).get("by"), ACTOR)
    check("...and the health overlay reads them beside the promise marks",
          prog.done_key(RUN_ID, first) in client_health._promise_marks())
    check("...and never invents one for an item nobody marked",
          prog.done_key(RUN_ID, social_id) not in idx)

# ---------------------------------------------------------------------------
section("The pages")
def _c360_source():
    """The Client 360 record as one text: the template plus its script modules
    (hub/client360_assets.MODULES), because the record's JavaScript lives in
    files now and a check that asks what the record does reads all of it."""
    _os, _sys = __import__("os"), __import__("sys")
    _root = _os.path.dirname(_os.path.abspath(__file__))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    return __import__("hub.client360_assets", fromlist=["source_text"]).source_text()

with hub_app.app_context():
    resp = staff.get(f"/proposal-execution/run/{RUN_ID}/kickoff")
    body = resp.get_data(as_text=True)
    check("the kickoff document has a Status column on both tables", body.count("<th>Status</th>"), 2)
    check("...marks the past-due rows, the done row and the landed row",
          "overdue, " in body and f"done ({ACTOR}" in body and "landed (" in body)
    check("...and counts what is past due at the top", "past the date the launch date put on" in body)
    doc = pe.kickoff_document(pe.get_run(RUN_ID))
    check("the document's counts agree with the plan's",
          (doc["overdue"], doc["done"], doc["landed"]), (p["overdue"], 1, 2))

    resp = staff.post(f"/api/proposal-execution/run/{RUN_ID}/client-link")
    url = (resp.get_json() or {}).get("link", {}).get("url", "")
    anon = WSGIClient(wsgi.application)
    page = anon.get(url.replace("http://localhost", "")).get_data(as_text=True) if url else ""
    check("a file the client already sent reads as received on their page", "received" in page)
    check("...and nothing about how we know reaches them",
          bool(page) and all(w not in page for w in ("Image Picker", "image_picker", "landed", "display_ads", "overdue")))

    tpl = _read("hub", "templates", "proposal_execution.html")
    check("the plan page draws the status from the server and offers Done",
          "function statusTag" in tpl and "function planDone" in tpl and "progressLine(p)" in tpl)
    check("...and explains it", "help_dot('proposal_execution.plan.progress')" in tpl
          and any(h.key == "proposal_execution.plan.progress" for h in hub_help.REGISTRY))
    check("Client 360 prints the past-due count beside the others", "pg.overdue" in _c360_source())
    check("the client page never draws the plan page's status vocabulary",
          not any(w in _read("hub", "templates", "proposal_needs.html") for w in ("status_label", "overdue", "landed_evidence")))

# ---------------------------------------------------------------------------
section("Wiring")
check("CI runs this file", "test_proposal_progress.py" in _read(".github", "workflows", "checks.yml"))
check("the module is named in the verifying list", "test_proposal_progress.py" in _read("docs", "claude", "56-verifying-a-change.md"))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
