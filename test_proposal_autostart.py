"""A proposal that is won starts its own execution plan.

    python3 test_proposal_autostart.py

Same shape as the other module tests here: no pytest, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

The Proposal Builder knows the moment a quote is won -- the client accepts
it at their link (Approved) or an insertion order is written from it
(Converted) -- and Proposal Execution was told nothing. A plan existed only
when somebody opened the tool and pressed Analyze, which on the day the
proposal is signed is the press most likely to be forgotten.
`proposal_execution.start_won()` reads the sales book's status and starts
a run for every won quote that has none, hourly from `hub/scheduler.py` and
on demand from the plan page. Every rule in it is a way to be wrong
quietly:

* **A quote is started once.** A run for `quote:<id>` in any state -- one
  started from the PDF the quote was filed as resolves to the same key --
  reads as `already`, so the sweep starts nothing on its second pass.
* **Only won statuses.** Draft and Sent are not won; Approved and
  Converted are.
* **A client with a different run open is named, never superseded.**
  Superseding carries approved work and shared inputs forward, and that is
  a person's press; the sweep records the conflict and leaves both runs
  exactly as they were.
* **Won too long ago is skipped and counted**, because a plan built for a
  campaign somebody set up a month ago is a list about work that happened.
* **The run says it was automatic** -- on the plan's notes, on its first
  event, and on the quote's own activity strip with the link, so the rep
  who sold it finds the plan from the screen they already read.
* **A table that would not answer is not measured**, never a clean sweep
  of nothing, and nothing in the sweep raises.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-autostart-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "autostart-test")
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
import hub.scheduler as sched                                        # noqa: E402
import hub.auth as auth                                              # noqa: E402
from werkzeug.test import Client as WSGIClient                       # noqa: E402

hub_app = wsgi.hub_app
B = sys.modules.get("salesb_app")
if B is None:                                   # pragma: no cover - mount failed
    from modules.sales_builder import app as B

staff = WSGIClient(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"), domain="localhost")
stranger = WSGIClient(wsgi.application)


def quote(name, budget=4000):
    state = {"client": name, "months": 6, "budget": budget,
             "startDate": "2026-11-02",
             "creativePlan": {"display": {"answer": "has"}},
             "objectives": ["Lead Generation"],
             "items": [{"category": "DISPLAY", "product": "Category",
                        "rate": "CPM", "rateValue": 4.25, "dollars": budget}]}
    r = staff.post("/sales/builder/api/quotes", json={"data": state})
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    return r.get_json()["quote"]


def set_status(qid, status, *, days_ago=None):
    db = B.SessionLocal()
    try:
        q = db.get(B.Quote, int(qid))
        q.status = status
        if status == "Converted":
            q.converted_at = datetime.now(timezone.utc)
        if days_ago is not None:
            when = datetime.now(timezone.utc) - timedelta(days=days_ago)
            q.updated_at = when
            if q.converted_at:
                q.converted_at = when
        db.commit()
    finally:
        db.close()


def activity(qid):
    db = B.SessionLocal()
    try:
        return [a.text for a in db.query(B.Activity).filter_by(quote_id=int(qid)).all()]
    finally:
        db.close()


def runs_for(qid):
    return (pe.ProposalExecutionRun.query
            .filter_by(proposal_id=f"quote:{qid}").order_by(pe.ProposalExecutionRun.id).all())


# ---------------------------------------------------------------------------
section("A won quote gets a run; a draft does not")
won = quote("Alpha Dental Autostart")
drafted = quote("Beta Roofing Autostart")
sent = quote("Gamma HVAC Autostart")
set_status(won["id"], "Approved")
set_status(sent["id"], "Sent")
with hub_app.app_context():
    before = {r.id for r in pe.ProposalExecutionRun.query.all()}
    out = pe.start_won()
    check("the sweep measured", out["measured"], True)
    check("it checked the won quote", out["checked"] >= 1)
    started = {s["quote"]: s for s in out["started"]}
    check("the Approved quote was started", won["quote_number"] in started)
    check("the Draft was not", drafted["quote_number"] in started, False)
    check("nor the Sent one", sent["quote_number"] in started, False)
    check("no conflicts, no errors", (out["conflicts"], out["errors"]), ([], []))
    rows = runs_for(won["id"])
    check("one run, keyed on the quote", [r.proposal_id for r in rows], [f"quote:{won['id']}"])
    run = rows[0]
    check("filed against the quote's client", run.client, "Alpha Dental Autostart")
    check("the run is new rather than one that existed", run.id not in before)
    plan = pe.plan_for(run)
    check("the plan's notes say it was automatic",
          any("Started automatically" in n and won["quote_number"] in n for n in plan.get("notes") or []))
    check("and that the quote was Approved", any("marked Approved" in n for n in plan.get("notes") or []))
    check("nothing on it is kept -- it arrives as proposals for the owner to review",
          plan["summary"]["to_review"] == sum(v["total"] for v in plan["summary"]["lists"].values()))
    check("the launch date came off the quote", pe.plan_for(run)["questions"][0]["answer"], "2026-11-02")
    ev = pe.events_for_run(run.id)[-1]
    check("the first event says so", "Started automatically" in ev["message"])
    check("by the scheduler", ev.get("actor"), "scheduler")
    acts = activity(won["id"])
    check("the quote's activity strip carries the link",
          any(f"/proposal-execution?run={run.id}" in a for a in acts))
    check("saying why", any("started automatically" in a and "Approved" in a for a in acts))
    RUN_ID = run.id

# ---------------------------------------------------------------------------
section("A second sweep starts nothing")
with hub_app.app_context():
    out = pe.start_won()
    check("nothing started", out["started"], [])
    check("the won quote reads as already having a plan", out["already"] >= 1)
    check("still one run", len(runs_for(won["id"])), 1)
    check("and one activity row", sum(1 for a in activity(won["id"]) if "Execution plan" in a), 1)
    # A run superseded or completed still counts as "already": the quote
    # had its plan, whatever became of it.
    pe.get_run(RUN_ID).state = pe.RUN_COMPLETED
    pe.db.session.commit()
    out = pe.start_won()
    check("a completed run for the quote still means already", won["quote_number"] in
          {s["quote"] for s in out["started"]}, False)
    pe.get_run(RUN_ID).state = pe.RUN_DRAFT
    pe.db.session.commit()

# ---------------------------------------------------------------------------
section("Converted counts as won; a client with another run open is a named conflict")
converted = quote("Delta Law Autostart")
set_status(converted["id"], "Converted")
second = quote("Alpha Dental Autostart", 9000)          # same client as the open run
set_status(second["id"], "Approved")
with hub_app.app_context():
    out = pe.start_won()
    started = {s["quote"] for s in out["started"]}
    check("the Converted quote was started", converted["quote_number"] in started)
    check("the second quote for the client with a run open was not", second["quote_number"] in started, False)
    conflicts = {c["quote"]: c for c in out["conflicts"]}
    check("it is named as a conflict", second["quote_number"] in conflicts)
    check("naming the run in the way", conflicts[second["quote_number"]]["run_id"], RUN_ID)
    check("the open run was not superseded", pe.get_run(RUN_ID).state, pe.RUN_DRAFT)
    check("and no run exists for the second quote", runs_for(second["id"]), [])
    check("nothing was written to the second quote's strip",
          any("Execution plan" in a for a in activity(second["id"])), False)
    out = pe.start_won()
    check("the conflict is named again on the next sweep rather than forgotten",
          second["quote_number"] in {c["quote"] for c in out["conflicts"]})

# ---------------------------------------------------------------------------
section("Won too long ago is skipped and counted; the cap defers rather than drops")
old = quote("Epsilon Auto Autostart")
set_status(old["id"], "Approved", days_ago=pe.AUTOSTART_MAX_AGE_DAYS + 5)
fresh1 = quote("Zeta Marine Autostart")
fresh2 = quote("Eta Solar Autostart")
set_status(fresh1["id"], "Approved")
set_status(fresh2["id"], "Approved")
with hub_app.app_context():
    out = pe.start_won(limit=1)
    check("the old one is counted as too old", out["too_old"] >= 1)
    check("and not started", old["quote_number"] in {s["quote"] for s in out["started"]}, False)
    check("one started under the cap", len(out["started"]), 1)
    check("and one deferred, counted", out["deferred"] >= 1)
    out = pe.start_won()
    check("the next sweep starts the deferred one", len(out["started"]), 1)
    check("both fresh quotes have a run", all(len(runs_for(q["id"])) == 1 for q in (fresh1, fresh2)))
    check("the old one still has none", runs_for(old["id"]), [])

# ---------------------------------------------------------------------------
section("A won quote naming no client is skipped and counted, never filed under nobody")
db = B.SessionLocal()
try:
    q = db.get(B.Quote, int(fresh1["id"]))
    orphan = B.Quote(quote_number=q.quote_number + "X", status="Approved", client="", data="{}")
    db.add(orphan)
    db.commit()
    ORPHAN_ID = orphan.id
finally:
    db.close()
with hub_app.app_context():
    out = pe.start_won()
    check("counted as having no client", out["skipped_no_client"] >= 1)
    check("and started nowhere", runs_for(ORPHAN_ID), [])
    check("and not reported as an error", out["errors"], [])

# ---------------------------------------------------------------------------
section("A table that would not answer is not measured, and nothing raises")
_real = pe._quote_module


class _Broken:
    Quote = B.Quote

    @staticmethod
    def SessionLocal():
        raise RuntimeError("no book")


pe._quote_module = lambda: _Broken
try:
    with hub_app.app_context():
        out = pe.start_won()
    check("measured is False", out["measured"], False)
    check("with the reason named", "could not be read" in out.get("error", ""))
    check("and nothing started", out["started"], [])
finally:
    pe._quote_module = _real

# ---------------------------------------------------------------------------
section("The scheduler job and the on-demand route")
every, fn, desc = sched.JOBS["proposal_autostart"]
check("the job ticks hourly", every, 60)
check("and says what it does", "Approved or Converted" in desc)
out = fn(hub_app)
check("run through the scheduler it answers a dict a person can read",
      isinstance(out, dict) and ("skipped" in out or out.get("measured") is True))
check("with nothing to start it reads as skipped rather than an empty run", "skipped" in out)
check("and the standing conflict rides in the sentence rather than making the hour a result",
      "with another run open" in out.get("skipped", ""))
order = list(sched.JOBS)
check("it sits ahead of the slow provider sweeps",
      order.index("proposal_autostart") < order.index("google_index"))
r = stranger.post("/api/proposal-execution/start-won")
check("a stranger cannot fire the sweep", r.status_code in (401, 302))
r = staff.post("/api/proposal-execution/start-won")
check("a member of staff can", r.status_code, 200)
d = r.get_json()
check("and reads the sweep's own counts", d["ok"] is True and d["result"]["measured"] is True)
with open(os.path.join(ROOT, "hub", "templates", "proposal_execution.html"), encoding="utf-8") as fh:
    tmpl = fh.read()
check("the plan page offers the press", "startWon()" in tmpl and "/api/proposal-execution/start-won" in tmpl)

# ---------------------------------------------------------------------------
section("A sweep that could not read the book is red on the scheduler, never green")
# `_run_job` reads an exception as a failure and a returned dict as a
# success, so a job handing back `measured: False` drew a green pill over a
# sweep that had read nobody -- the `seo_intelligence` rule.
pe._quote_module = lambda: _Broken
try:
    raised = ""
    try:
        sched.job_proposal_autostart(hub_app)
    except RuntimeError as exc:
        raised = str(exc)
    check("the job raises when the proposals could not be read", "could not be read" in raised)
    sched._run_job(hub_app, "proposal_autostart")
    state = sched._state.get("proposal_autostart") or {}
    check("so the scheduler records the run as failed", state.get("ok"), False)
    check("with the reason", "could not be read" in (state.get("error") or ""))
finally:
    pe._quote_module = _real
# Every start failing is the same verdict: attempted, and landed none.
failing = quote("Theta Plumbing Autostart")
set_status(failing["id"], "Approved")
_create = pe.create_run


def _refuse(*_a, **_k):
    raise RuntimeError("the model refused")


pe.create_run = _refuse
try:
    raised = ""
    try:
        sched.job_proposal_autostart(hub_app)
    except RuntimeError as exc:
        raised = str(exc)
    check("every start failing raises", "could not be started" in raised)
    check("naming the first quote and why", failing["quote_number"] in raised and "the model refused" in raised)
finally:
    pe.create_run = _create
with hub_app.app_context():
    out = pe.start_won()
    check("and the quote is started once creating works again",
          failing["quote_number"] in {s["quote"] for s in out["started"]})
# One failing beside one that started is a row in the result, not a red job.
half_a = quote("Iota Bakery Autostart")
half_b = quote("Kappa Vet Autostart")
set_status(half_a["id"], "Approved")
set_status(half_b["id"], "Approved")


def _half(client, key, **kw):
    if client == "Kappa Vet Autostart":
        raise RuntimeError("odd quote")
    return _create(client, key, **kw)


pe.create_run = _half
try:
    out = sched.job_proposal_autostart(hub_app)
    check("one failing beside one that started is a result rather than a failure",
          isinstance(out, dict) and len(out.get("errors") or []) == 1 and len(out.get("started") or []) == 1)
finally:
    pe.create_run = _create

# ---------------------------------------------------------------------------
section("One sweep at a time across both workers; a lock that cannot be taken never refuses the work")
import fcntl                                                         # noqa: E402
from hub import jsonstore                                            # noqa: E402
lock_path = os.path.join(jsonstore.data_dir("proposal_execution"), "start_won.lock")
waiting = quote("Lambda Gym Autostart")
set_status(waiting["id"], "Approved")
holder = open(lock_path, "a+")
fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)         # the other worker, mid-sweep
try:
    with hub_app.app_context():
        out = pe.start_won()
        check("a second sweep answers busy", out.get("busy"), True)
        check("started nothing", out["started"], [])
        check("and says so", "already running" in out.get("note", ""))
        check("the waiting quote still has no run", runs_for(waiting["id"]), [])
    out = sched.job_proposal_autostart(hub_app)
    check("the job reads it as skipped rather than as a result or a failure",
          "already running" in out.get("skipped", ""))
finally:
    holder.close()
with hub_app.app_context():
    out = pe.start_won()
    check("once the other worker is done, the sweep runs and starts it",
          waiting["quote_number"] in {s["quote"] for s in out["started"]})
    _dd = jsonstore.data_dir

    def _no_disk(*_a):
        raise OSError("no disk")

    jsonstore.data_dir = _no_disk
    try:
        check("a lock that cannot be taken claims rather than refuses", pe._claim_sweep(), (True, None))
        check("and the sweep still runs", pe.start_won()["measured"], True)
    finally:
        jsonstore.data_dir = _dd

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
