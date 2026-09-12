"""Proposal Execution — supersede, don't duplicate (WO-1).

    python3 test_proposal_execution.py

Same shape as the other module tests here: no pytest, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

`create_run()` computed `previous_run_id` on every call and never asked
whether that previous run was still open. So a second proposal for a client
who already had an execution run in flight -- an updated PDF for the same
campaign, the ordinary case a rep meets -- spawned a second, parallel run
with none of the first one's saved shared inputs and none of its approved
work, and nothing anywhere said the two were now competing accounts of one
campaign.

This asserts the fix: analyzing a proposal for a client with an open run
refuses by raising `ProposalRunConflict` rather than silently creating a
duplicate; the explicit "supersede" press (`force=True`) marks the old run
superseded, carries forward shared inputs and any task whose channel payload
is unchanged and already approved/completed, and the route answers 409
rather than a generic 500 so the page can offer "open it" or "supersede it"
instead of deciding on its own.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-propexec-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "propexec-test")
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
import hub.extensions as hub_extensions                              # noqa: E402
import hub.proposals as proposals_mod                                # noqa: E402
import hub as hub_pkg                                                # noqa: E402
import hub.auth as auth                                              # noqa: E402
from werkzeug.test import Client as WSGIClient                       # noqa: E402

hub_app = wsgi.hub_app
db = hub_extensions.db

CLIENT = "Monogram Homes"
_FIXTURES = {
    "p1": {"id": "p1", "title": "Q3 Marketing Plan", "filename": "q3.pdf", "kind": "file"},
    "p2": {"id": "p2", "title": "Updated Marketing Plan", "filename": "q3-v2.pdf", "kind": "file"},
    "p3": {"id": "p3", "title": "Q4 Marketing Plan", "filename": "q4.pdf", "kind": "file"},
}
_TEXT = {
    "p1": "Retargeting campaign for Monogram Homes. Landing page https://monogramhomes.com. Budget $2,000/month.",
    "p2": "Retargeting campaign for Monogram Homes, updated. Landing page https://monogramhomes.com/new. Budget $2,500/month. Add paid search.",
    "p3": "A brand new Q4 campaign for Monogram Homes with a completely different scope. Budget $9,000/month.",
}
_ROUTE_CLIENT = "Route Conflict Co"
_ROUTE_FIXTURES = {
    "rA": {"id": "rA", "title": "Route Plan A", "filename": "a.pdf", "kind": "file"},
    "rB": {"id": "rB", "title": "Route Plan B", "filename": "b.pdf", "kind": "file"},
}
_ROUTE_TEXT = {
    "rA": "Route Conflict Co retargeting plan. Landing page https://routeconflict.co. Budget $1,000/month.",
    "rB": "Route Conflict Co retargeting plan, revised. Landing page https://routeconflict.co/v2. Budget $1,500/month.",
}


def _fake_list_proposals(client):
    if client == CLIENT:
        return list(_FIXTURES.values())
    if client == _ROUTE_CLIENT:
        return list(_ROUTE_FIXTURES.values())
    return []


def _fake_text_for(client, ident):
    return _TEXT.get(ident) or _ROUTE_TEXT.get(ident) or ""


proposals_mod.list_proposals = _fake_list_proposals
hub_pkg._proposal_text_for = _fake_text_for


with hub_app.app_context():
    section("The first analysis of a proposal creates an open run")
    run1, created1 = pe.create_run(CLIENT, "p1", owner="rep@smart1marketing.com",
                                    actor="rep@smart1marketing.com")
    check("a run is created", created1, True)
    check("it starts open, not superseded", run1.state != pe.RUN_SUPERSEDED, True)

    section("Re-analyzing the identical document still just reopens it (unchanged by WO-1)")
    run1_again, created_again = pe.create_run(CLIENT, "p1", owner="x", actor="x")
    check("no new run for an unchanged document", created_again, False)
    check("...it is the same run", run1_again.id, run1.id)

    section("A different proposal for a client with an open run is a conflict, not a duplicate")
    conflict = None
    try:
        pe.create_run(CLIENT, "p2", owner="rep@smart1marketing.com",
                      actor="rep@smart1marketing.com")
    except pe.ProposalRunConflict as exc:
        conflict = exc
    check("create_run refuses to silently spawn a second parallel run", conflict is not None)
    check("...naming the run already open", conflict.previous_run.id if conflict else None, run1.id)
    check("no second run exists yet", len(pe.list_runs(CLIENT, limit=50)), 1)

    section("Approved work with an unchanged payload, and shared inputs, carry across a supersede")
    tasks = pe.tasks_for_run(run1.id)
    budget_task = next(t for t in tasks if t.task_key == "budget_calendar")
    budget_task.state = pe.APPROVED
    budget_task.result_json = json.dumps({"note": "approved before the update"})
    db.session.commit()
    pe.update_inputs(run1.id, {"target_geography": "Indianapolis metro, 15 mile radius"},
                     actor="rep@smart1marketing.com")

    run2, created2 = pe.create_run(CLIENT, "p2", owner="rep@smart1marketing.com",
                                    actor="rep@smart1marketing.com", force=True)
    check("superseding creates the new run", created2, True)
    check("...pointing back at the run it replaces", run2.previous_run_id, run1.id)

    refreshed1 = pe.get_run(run1.id)
    check("the run it replaced is marked superseded, not left open", refreshed1.state, pe.RUN_SUPERSEDED)
    check("...and points forward at what replaced it", refreshed1.superseded_by_run_id, run2.id)

    new_budget_task = next(t for t in pe.tasks_for_run(run2.id) if t.task_key == "budget_calendar")
    check("an approved task whose payload is unchanged carries its approval forward",
          new_budget_task.state, pe.APPROVED)
    check("...and the work already recorded against it",
          json.loads(new_budget_task.result_json or "{}").get("note"),
          "approved before the update")
    check("a shared input already answered carries onto the new run",
          run2.inputs().get("target_geography"), "Indianapolis metro, 15 mile radius")

    section("A superseded or completed run is not something a fresh analysis conflicts with")
    run2_row = pe.get_run(run2.id)
    run2_row.state = pe.RUN_COMPLETED
    db.session.commit()
    run3, created3 = pe.create_run(CLIENT, "p3", owner="x", actor="x")
    check("a completed previous run does not block a fresh analysis", created3, True)
    refreshed2 = pe.get_run(run2.id)
    check("...and completing a run does not retroactively mark it superseded",
          refreshed2.state, pe.RUN_COMPLETED)
    check("...nor does it gain a superseded_by pointer it never earned",
          refreshed2.superseded_by_run_id, None)

    section("The route answers 409 with the conflict rather than a generic 500")
    staff = WSGIClient(wsgi.application)
    staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"), domain="localhost")

    resp = staff.post("/api/proposal-execution/analyze",
                      json={"client": _ROUTE_CLIENT, "proposal_id": "rA"})
    check("first analyze via the route succeeds", resp.status_code, 200)
    route_run1_id = resp.get_json()["run"]["id"]

    resp = staff.post("/api/proposal-execution/analyze",
                      json={"client": _ROUTE_CLIENT, "proposal_id": "rB"})
    check("a conflicting analyze answers 409, not 500", resp.status_code, 409)
    body = resp.get_json()
    check("...says so explicitly", body.get("conflict"), True)
    check("...and hands back the run already open",
          (body.get("previous_run") or {}).get("id"), route_run1_id)

    resp = staff.post("/api/proposal-execution/analyze",
                      json={"client": _ROUTE_CLIENT, "proposal_id": "rB", "force": True})
    check("pressing supersede (force) succeeds", resp.status_code, 200)
    route_run2 = resp.get_json()["run"]
    check("...as a new run", route_run2["id"] != route_run1_id, True)
    check("...pointing back at the one it replaced", route_run2["previous_run_id"], route_run1_id)

    section("The late column survives being applied twice (a second worker races the same ALTER)")
    try:
        pe.add_missing_columns()
        pe.add_missing_columns()
        ok = True
    except Exception as exc:                                          # noqa: BLE001
        ok = False
        print(f"          raised: {exc!r}")
    check("add_missing_columns() never raises, called once or twice", ok, True)


print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
