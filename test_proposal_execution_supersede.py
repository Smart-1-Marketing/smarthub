"""Proposal Execution: supersede, don't duplicate.

    python3 test_proposal_execution_supersede.py

Same shape as the other test files here: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never
touches /var/data or the real one.

## Why this file exists

`hub/proposal_execution.py::create_run` only ever caught the identical file:
same client, same stored proposal id, same content hash. The IO sheet a
proposal is actually revised into is built from an *updated* PDF -- a new
upload, usually a new stored id -- so a re-analysis of that revision hashed
differently and `create_run` happily built a second run beside one still in
progress, with its own thirty tasks and its own notifications, while the
work already approved on the first run sat there unreferenced.

`create_run` now looks for an OPEN run for the same client whose proposal id
matches or whose title normalises to the same string once the noise a
re-upload picks up -- "(1)", "updated", "v2", a date, the extension -- is
stripped. A different file against that run raises `ProposalRunConflict`
unless the caller passes `supersede=True`, in which case the old run is
paused and marked superseded, its shared inputs travel onto the new run
wholesale, and any task the old run had already carried to approval, a
handoff or completion is carried across untouched *only* where the channel
it was built for is byte-identical in the new graph -- a channel the new
proposal actually changed is left to start fresh, with an event naming what
moved rather than silently discarding a decision nobody re-made.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1pexsupersede_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "pexsupersede-test-secret"
os.environ.pop("OPENAI_API_KEY", None)

_passed, _failed = 0, 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from werkzeug.test import Client as HttpClient                          # noqa: E402
from wsgi import application, hub_app                                   # noqa: E402
from hub import auth, proposal_execution as pe                          # noqa: E402
from hub.extensions import db                                           # noqa: E402
import hub.proposals as proposals_module                                # noqa: E402

hub = sys.modules["hub"]  # already imported above; avoid a second `import hub`

with hub_app.app_context():
    db.create_all()

http = HttpClient(application)
http.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Test User"), domain="localhost")

CLIENT = "Fixture Homes"

_RECORDS = {
    "prop-v1": {"id": "prop-v1", "filename": "Fixture_Homes_2026_Plan.pdf",
                "title": "Fixture Homes 2026 Plan", "kind": "file"},
    "prop-v2": {"id": "prop-v2", "filename": "Fixture_Homes_2026_Plan(1).pdf",
                "title": "Fixture Homes 2026 Plan (Updated)", "kind": "file"},
}

TEXT_V1 = """
FIXTURE HOMES 2026 PLAN
Meta In-Market Home Buyers $750
Paid Search $550
"""

TEXT_V2 = """
FIXTURE HOMES 2026 PLAN (UPDATED)
Meta In-Market Home Buyers $500
Paid Search $550
"""

_TEXTS = {"prop-v1": TEXT_V1, "prop-v2": TEXT_V2}


def _fake_list_proposals(client, backfill=True):
    return [rec for rec in _RECORDS.values()] if client == CLIENT else []


def _fake_text_for(client, filename):
    for rec in _RECORDS.values():
        if rec["id"] == filename or rec["filename"] == filename:
            return _TEXTS[rec["id"]]
    return ""


proposals_module.list_proposals = _fake_list_proposals
hub._proposal_text_for = _fake_text_for


# ---------------------------------------------------------------------------
section("An identical re-analysis returns the existing run, unchanged")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run1, created1 = pe.create_run(CLIENT, "prop-v1", owner="rep@example.com", actor="rep@example.com")
    check("first analysis creates a run", created1, True)
    check("run has the paid_search and meta channels",
          {c["key"] for c in run1.analysis()["channels"]} >= {"meta", "paid_search"}, True)

    run1_again, created_again = pe.create_run(CLIENT, "prop-v1", owner="rep@example.com", actor="rep@example.com")
    check("re-analyzing the identical file returns the same run", run1_again.id, run1.id)
    check("...and reports it was not newly created", created_again, False)


# ---------------------------------------------------------------------------
section("A changed file for an open run raises a conflict, not a parallel run")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    raised = None
    try:
        pe.create_run(CLIENT, "prop-v2", owner="rep@example.com", actor="rep@example.com")
    except pe.ProposalRunConflict as exc:
        raised = exc
    check("a different file (same title, new proposal id) raises ProposalRunConflict", raised is not None, True)
    if raised:
        check("...naming the run already in progress", raised.existing_run_id, run1.id)
        payload = raised.payload()
        check("...and the payload carries what the UI needs to offer both doors",
              set(payload) >= {"existing_run_id", "existing_state", "existing_progress", "changed"}, True)
    check("no second run was created for this client",
          len([r for r in pe.list_runs(CLIENT, limit=50) if r["proposal_id"] == "prop-v2"]), 0)


# ---------------------------------------------------------------------------
section("Supersede: reviewed work carries forward, changed work starts fresh")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    # Simulate a task on the unchanged channel (Paid Search) already reviewed
    # and completed on run #1 -- this is the work that must not be re-done.
    tasks1 = pe.tasks_for_run(run1.id)
    paid_search_ads = next(t for t in tasks1 if t.task_key == "paid_search_ads")
    paid_search_ads.state = pe.COMPLETED
    paid_search_ads.result_json = pe._dumps({"summary": "Reviewed and finished on run #1.", "generated_by": "human"})
    db.session.commit()

    meta_plan_old = next(t for t in tasks1 if t.task_key == "meta_plan")
    old_meta_channel = meta_plan_old.payload().get("channel") or {}
    check("run #1's meta channel budget is $750", old_meta_channel.get("budgets"), ["$750"])

    run2, created2 = pe.create_run(CLIENT, "prop-v2", owner="rep@example.com",
                                    actor="rep@example.com", supersede=True)
    check("superseding creates a new run", created2, True)
    check("...distinct from the run it replaces", run2.id != run1.id, True)

    run1_reloaded = pe.get_run(run1.id)
    check("the superseded run is marked superseded", run1_reloaded.state, pe.RUN_SUPERSEDED)
    check("...and paused", bool(run1_reloaded.paused), True)

    check("the new run's shared inputs carry the old run's answers wholesale",
          run2.inputs(), run1_reloaded.inputs())

    tasks2 = pe.tasks_for_run(run2.id)
    by_key2 = {t.task_key: t for t in tasks2}

    check("the unchanged Paid Search task is carried over, not re-done",
          by_key2["paid_search_ads"].state, pe.COMPLETED)
    check("...with its old result intact",
          by_key2["paid_search_ads"].result().get("summary"), "Reviewed and finished on run #1.")

    check("the changed Meta task is NOT carried over -- it starts fresh",
          by_key2["meta_plan"].state in {pe.COMPLETED, pe.APPROVED, pe.LIVE, pe.NEEDS_APPROVAL}, False)
    new_meta_channel = by_key2["meta_plan"].payload().get("channel") or {}
    check("...against the new proposal's own $500 budget", new_meta_channel.get("budgets"), ["$500"])

    events2 = {e["task_id"]: e["message"] for e in pe.events_for_run(run2.id, 200)
               if e["task_id"] in (by_key2["paid_search_ads"].id, by_key2["meta_plan"].id)}
    check("a 'carried over' event is recorded against the carried task",
          f"run #{run1.id}" in events2.get(by_key2["paid_search_ads"].id, ""), True)
    check("a 're-planned' event names the budget that changed",
          "$750" in events2.get(by_key2["meta_plan"].id, "") and "$500" in events2.get(by_key2["meta_plan"].id, ""),
          True)

    check("run #2's previous_run_id points at the run it superseded",
          run2.previous_run_id, run1.id)


# ---------------------------------------------------------------------------
section("A superseded run's tasks are inert to the scheduler")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run1_final = pe.get_run(run1.id)
    check("no task on the superseded run is left QUEUED",
          any(t.state == pe.QUEUED for t in pe.tasks_for_run(run1_final.id)), False)
    # run_one() only ever claims a QUEUED task belonging to a RUN_RUNNING,
    # unpaused run -- a superseded run can satisfy neither condition.
    check("run_one() would never select a task from it",
          run1_final.state == "running" and not run1_final.paused, False)


# ---------------------------------------------------------------------------
section("The route: analyze() answers 409 with the conflict, and honours supersede")
# ---------------------------------------------------------------------------

CLIENT2 = "Route Fixture Co"
_RECORDS2 = {
    "route-v1": {"id": "route-v1", "filename": "Route_Plan.pdf", "title": "Route Plan", "kind": "file"},
    "route-v2": {"id": "route-v2", "filename": "Route_Plan (1).pdf", "title": "Route Plan (1)", "kind": "file"},
}
_ROUTE_TEXTS = {
    "route-v1": "ROUTE PLAN\nPaid Search $400\n",
    "route-v2": "ROUTE PLAN\nPaid Search $600\n",
}


def _fake_list_proposals_2(client, backfill=True):
    if client == CLIENT2:
        return list(_RECORDS2.values())
    return _fake_list_proposals(client, backfill)


def _fake_text_for_2(client, filename):
    for rec in _RECORDS2.values():
        if rec["id"] == filename or rec["filename"] == filename:
            return _ROUTE_TEXTS[rec["id"]]
    return _fake_text_for(client, filename)


proposals_module.list_proposals = _fake_list_proposals_2
hub._proposal_text_for = _fake_text_for_2

resp1 = http.post("/api/proposal-execution/analyze",
                  json={"client": CLIENT2, "proposal_id": "route-v1"})
check("first analyze via the route succeeds", resp1.status_code, 200)
route_run1_id = resp1.get_json()["run"]["id"]

resp2 = http.post("/api/proposal-execution/analyze",
                  json={"client": CLIENT2, "proposal_id": "route-v2"})
check("a conflicting analyze via the route answers 409", resp2.status_code, 409)
conflict_body = resp2.get_json()
check("...ok is false", conflict_body.get("ok"), False)
check("...naming the run already open", conflict_body.get("existing_run_id"), route_run1_id)

resp3 = http.post("/api/proposal-execution/analyze",
                  json={"client": CLIENT2, "proposal_id": "route-v2", "supersede": True})
check("posting supersede:true succeeds", resp3.status_code, 200)
route_run2 = resp3.get_json()["run"]
check("...and creates a distinct run", route_run2["id"] != route_run1_id, True)
check("...whose previous_run_id names the one it replaced",
      route_run2["previous_run_id"], route_run1_id)

with hub_app.app_context():
    check("the route-superseded run is marked superseded on reload",
          pe.get_run(route_run1_id).state, pe.RUN_SUPERSEDED)


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
