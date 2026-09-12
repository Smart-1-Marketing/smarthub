"""Proposal Execution: real-tool adapters (WO-2).

    python3 test_proposal_adapters.py

Same shape as the other test files here: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never
touches /var/data or the real one.

## Why this file exists

Every task on the Proposal Execution graph ran through `_brief_runner` --
an AI-written paragraph describing what somebody should go and build in a
tool this Hub already has, never the tool itself. `hub/proposal_adapters/`
is where that changes: one adapter per file, registered via
`register_adapter`, importing this package from
`proposal_execution_routes.register_proposal_execution` is what switches a
task from a text brief to the real module.

`tracking_plan` is the first one built, calling
`modules.utm_builder.app.save_batch()` -- the exact function the tool's own
"Save" button calls. This asserts the whole path end to end: a real run,
started, drained through `run_one()`, produces a real saved row in UTM
Builder's own book, findable by its own search, with no test-only seam
between the two modules.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1pexadapters_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "pexadapters-test-secret"
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


from wsgi import application, hub_app                                   # noqa: E402
from hub.extensions import db                                           # noqa: E402
from hub import proposal_execution as pe                                # noqa: E402
from hub import proposal_execution_routes as pe_routes                  # noqa: E402
from modules.utm_builder import app as utm_app                          # noqa: E402
import hub.proposals as proposals_module                                # noqa: E402

hub = sys.modules["hub"]
# The `wsgi` import above already booted the composed app, which calls
# register_proposal_execution(app) -- the thing that imports
# hub.proposal_adapters and registers the utm adapter. Checked here rather
# than importing the routes module purely for a side effect nothing
# verifies.
assert pe_routes.bp is not None
assert "utm" in {a["key"] for a in pe.adapters()}, \
    "the composed app's boot should have registered the utm adapter"

with hub_app.app_context():
    db.create_all()

CLIENT = "Adapter Test Co"
TEXT = ("PLAN\nMeta In-Market Home Buyers $750\nPaid Search $550\n"
        "Enhanced SEO + AI Optimization $1,400\n")
_RECORDS = {"prop-1": {"id": "prop-1", "filename": "Plan.pdf", "title": "Plan", "kind": "file"}}


def _drain(run, cap=30):
    for _ in range(cap):
        out = pe.run_one()
        if out.get("claimed") == 0:
            break
    return {t.task_key: t for t in pe.tasks_for_run(run.id)}


def _new_run(client=CLIENT, text=TEXT):
    proposals_module.list_proposals = lambda c, backfill=True: (
        list(_RECORDS.values()) if c == client else [])
    hub._proposal_text_for = lambda c, filename: text
    run, _created = pe.create_run(client, "prop-1", owner="rep@example.com",
                                  actor="rep@example.com", force=True)
    return run


# ---------------------------------------------------------------------------
section("hub.proposal_adapters registered the utm adapter on tracking_plan")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run()
    tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
    check("tracking_plan uses the utm adapter", tasks["tracking_plan"].adapter, "utm")
    check("its own adapter registry lists it", "utm" in {a["key"] for a in pe.adapters()}, True)


# ---------------------------------------------------------------------------
section("A missing landing URL is a clear, named failure -- never a raw traceback")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run(client="No URL Co")
    run = pe.update_inputs(run.id, {"conversion_goal": "appointment",
                                    "primary_cta": "Call now"}, actor="rep@example.com")
    # tracking_plan's own declared need (landing_url) keeps it at needs_input
    # before it is ever queued -- the graph's own pre-flight gate, checked
    # here so the adapter's defensive raise is known to be a second line of
    # defense rather than the only one.
    tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
    check("with no landing_url the task never reaches the scheduler at all",
          tasks["tracking_plan"].state, pe.NEEDS_INPUT)


# ---------------------------------------------------------------------------
section("An unusable landing URL fails the task by name, not silently")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run(client="Bad URL Co")
    run = pe.update_inputs(run.id, {"landing_url": "not a url at all",
                                    "conversion_goal": "appointment",
                                    "primary_cta": "Call now"}, actor="rep@example.com")
    run = pe.start_run(run.id, actor="rep@example.com")
    tasks = _drain(run)
    check("tracking_plan fails rather than silently completing",
          tasks["tracking_plan"].state, pe.FAILED)
    check("...naming the URL that could not be used",
          "not a url at all" in tasks["tracking_plan"].error, True)


# ---------------------------------------------------------------------------
section("A real run: real links, saved in UTM Builder's own book")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    before = len(utm_app.load_links())
    run = _new_run()
    run = pe.update_inputs(run.id, {"landing_url": "adaptertest.example.com",
                                    "conversion_goal": "appointment",
                                    "primary_cta": "Schedule an appointment"},
                           actor="rep@example.com")
    run = pe.start_run(run.id, actor="rep@example.com")
    tasks = _drain(run)
    tp = tasks["tracking_plan"]
    check("tracking_plan completes (mode=auto)", tp.state, pe.COMPLETED)
    result = tp.result()
    check("two links built -- one per real channel on the proposal (meta, paid_search)",
          result.get("saved"), 2)
    check("SEO is not one of the five UTM-source channels, so it built nothing for it",
          any("seo" in link for link in result.get("links") or []), False)
    check("the artifact_url points at the real tool, filtered to this client",
          result.get("artifact_url"), f"/tools/utm?q={CLIENT.replace(' ', '%20')}")

    rows = [r for r in utm_app.load_links() if r.get("client") == CLIENT]
    check("exactly two rows landed in the real book", len(rows), 2)
    check("no test-only seam: this is the same store /tools/utm reads",
          len(utm_app.load_links()) - before, 2)
    sources = sorted((r["utm_source"], r["utm_medium"]) for r in rows)
    check("built with the vocabulary UTM Builder already publishes",
          sources, [("facebook", "paid-social"), ("google", "cpc")])
    check("every link points at the client's real landing URL",
          all("adaptertest.example.com" in r["url"] for r in rows), True)
    check("one campaign slug ties every channel's link together",
          len({r["utm_campaign"] for r in rows}), 1)

    # A second run of the adapter against the identical proposal (a rep
    # re-running the task, or a scheduler retry that reaches the adapter a
    # second time after the write already landed) must not duplicate the
    # links -- the adapter's own de-dupe against what UTM Builder already
    # has, called directly here rather than through rerun_task's own state
    # gate, which is a separate concern this file is not testing.
    from hub.proposal_adapters import utm as utm_adapter
    second = utm_adapter.run(run, tp)
    check("a second run of the adapter creates no duplicate links",
          len([r for r in utm_app.load_links() if r.get("client") == CLIENT]), 2)
    check("...and says so rather than reporting a second save",
          second.get("already_saved"), 2)


# ---------------------------------------------------------------------------
section("A proposal with none of the five UTM-source channels completes cleanly")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run(client="SEO Only Co", text="PLAN\nEnhanced SEO + AI Optimization $1,400\n")
    run = pe.update_inputs(run.id, {"landing_url": "seoonly.example.com",
                                    "conversion_goal": "lead",
                                    "primary_cta": "Get a quote"}, actor="rep@example.com")
    run = pe.start_run(run.id, actor="rep@example.com")
    tasks = _drain(run)
    tp = tasks["tracking_plan"]
    check("completes -- this is not a failure, the proposal just has nothing to tag",
          tp.state, pe.COMPLETED)
    check("says so rather than reporting a link that does not exist",
          tp.result().get("saved"), 0)


# ---------------------------------------------------------------------------
section("modules/utm_builder/app.py: save_batch() is the one writer, route included")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    from werkzeug.test import Client as HttpClient
    from hub import auth
    http = HttpClient(application)
    http.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Route Test"), domain="localhost")
    before = len(utm_app.load_links())
    resp = http.post("/tools/utm/api/links", json={
        "url": "https://route-check.example.com/", "client": "Route Check Co",
        "links": [{"url": "https://route-check.example.com/?utm_campaign=x&utm_source=google",
                  "utm_campaign": "x", "utm_source": "google", "utm_medium": "cpc"}]})
    body = resp.get_json()
    check("the route still saves through save_batch() with the same JSON shape",
          body.get("saved"), 1)
    check("...and the row actually landed", len(utm_app.load_links()) - before, 1)

    dup = http.post("/tools/utm/api/links", json={
        "url": "https://route-check.example.com/", "client": "Route Check Co",
        "links": [{"url": "https://route-check.example.com/?utm_campaign=x&utm_source=google"}]})
    check("the route's own de-dupe against an identical link still works",
          (dup.get_json().get("saved"), dup.get_json().get("skipped")), (0, 1))


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
