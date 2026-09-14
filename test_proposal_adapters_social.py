"""Proposal Execution: the social_plan / social_plan_posts adapters (WO-2,
Adapter C).

    python3 test_proposal_adapters_social.py

Same shape as the other adapter test files: no pytest, no new dependencies,
a temporary data directory and a throwaway SQLite database. OpenAI is
stubbed the way test_ads_module.py, test_gpt_ads.py and
test_proposal_adapters_search_ads.py already stub it -- nothing here makes a
real network or API call.

## What it asserts

social_calendar and social_posts used to both run as `brief` -- an AI-written
paragraph describing a content calendar somebody should go build, never a
post anybody could actually review. social_calendar now calls
modules.social_planner.app.create_batch() and draft_slot() -- the route's
own extracted writers -- so a real run produces a real batch, in the same
store /tools/social reads, with real drafted copy on every slot. social_posts
depends on social_calendar and re-reads that same real batch rather than
spending a second round of AI calls describing work already done.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1pexsocial_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "pexsocial-test-secret"
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
from hub import ai as hub_ai                                            # noqa: E402
from hub import social_plan                                             # noqa: E402
import hub.proposals as proposals_module                                # noqa: E402
from modules.social_planner import app as social_app                    # noqa: E402

assert pe_routes.bp is not None
assert {"social_plan", "social_plan_posts"} <= {a["key"] for a in pe.adapters()}, \
    "the composed app's boot should have registered the social adapters"

with hub_app.app_context():
    db.create_all()

CLIENT = "Social Plan Test Co"
TEXT = "PLAN\nSocial Media $600\n"
_RECORDS = {"prop-1": {"id": "prop-1", "filename": "Plan.pdf", "title": "Plan", "kind": "file"}}


def _drain(run, cap=40):
    for _ in range(cap):
        out = pe.run_one()
        if out.get("claimed") == 0:
            break
    return {t.task_key: t for t in pe.tasks_for_run(run.id)}


def _new_run(client=CLIENT, text=TEXT):
    real_list = proposals_module.list_proposals
    proposals_module.list_proposals = lambda c, backfill=True: (
        list(_RECORDS.values()) if c == client else [])
    hub_mod = sys.modules["hub"]
    hub_mod._proposal_text_for = lambda c, filename: text
    try:
        run, _created = pe.create_run(client, "prop-1", owner="rep@example.com",
                                      actor="rep@example.com", force=True)
    finally:
        proposals_module.list_proposals = real_list
    return run


def fake_chat_json(messages, **kw):
    return {"copy": "A real, drafted post about the business.", "hashtags": ["#local"]}


hub_ai.chat_json = fake_chat_json


# ---------------------------------------------------------------------------
section("hub.proposal_adapters registered the social adapters on the right tasks")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run()
    tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
    check("social_calendar exists (the proposal mentions Social Media)",
          "social_calendar" in tasks, True)
    check("...and uses the social_plan adapter", tasks["social_calendar"].adapter, "social_plan")
    check("...in approval mode -- this is copy that reaches a real account",
          tasks["social_calendar"].execution_mode, "approval")
    check("social_posts uses social_plan_posts", tasks["social_posts"].adapter, "social_plan_posts")


# ---------------------------------------------------------------------------
section("With no shared inputs yet, social_calendar is blocked behind campaign_foundation")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run(client="No Inputs Social Co")
    tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
    check("campaign_foundation holds at needs_input (it needs landing_url etc.)",
          tasks["campaign_foundation"].state, pe.NEEDS_INPUT)
    check("...social_calendar has no needs of its own, but is blocked behind it",
          tasks["social_calendar"].state, pe.BLOCKED)


# ---------------------------------------------------------------------------
section("A real run: a real month plan, drafted, in the tool's own store")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run()
    run = pe.update_inputs(run.id, {"landing_url": "socialplantest.example.com",
                                    "primary_cta": "Book a free estimate",
                                    "conversion_goal": "form fill",
                                    "offer": "Fall tune-up special"}, actor="rep@example.com")
    run = pe.start_run(run.id, actor="rep@example.com")
    tasks = _drain(run)
    cal_task = tasks["social_calendar"]
    check("social_calendar reaches needs_approval (mode=approval)",
          cal_task.state, pe.NEEDS_APPROVAL)
    result = cal_task.result()
    check("a month was chosen, in YYYY-MM form",
          bool(re.match(r"^\d{4}-\d{2}$", result.get("month", ""))), True)
    check("the module's own default channels were used",
          result.get("channels"), list(social_plan.DEFAULT_CHANNELS))
    check("at least one post was scheduled", result.get("slots", 0) > 0, True)
    check("every scheduled post was drafted (the stub always succeeds)",
          result.get("drafted"), result.get("slots"))
    check("nothing failed to draft", result.get("failed"), 0)

    batch_id = result.get("batch_id")
    check("a batch_id came back", bool(batch_id), True)
    with hub_app.app_context():
        batch = social_app.load_batch(batch_id)
    check("...and it is a real batch in the tool's own store", batch is not None, True)
    check("...for this client", batch.get("client") if batch else None, CLIENT)
    check("...at draft status -- nothing here schedules or pushes to Suite",
          batch.get("status") if batch else None, "draft")
    check("...carrying the CTA as something to promote",
          "Book a free estimate" in (batch.get("brief") or {}).get("promote", []) if batch else False, True)
    check("...carrying the stated offer",
          (batch.get("brief") or {}).get("offers") if batch else None, "Fall tune-up special")
    all_drafted = all(bool(s.get("copy")) for s in batch["slots"]) if batch else False
    check("...every slot in the real batch carries real, drafted copy",
          all_drafted, True)
    check("the artifact_url points at the real tool, for this client",
          result.get("artifact_url"), f"/tools/social?client={CLIENT.replace(' ', '%20')}")

    # Approve the calendar so social_posts (which depends on it) can run.
    pe.approve_task(cal_task.id, actor="rep@example.com")
    tasks = _drain(run)
    posts_task = tasks["social_posts"]
    check("social_posts also reaches needs_approval",
          posts_task.state, pe.NEEDS_APPROVAL)
    posts_result = posts_task.result()
    check("it re-reads the SAME batch social_calendar just built -- no second AI spend",
          posts_result.get("batch_id"), batch_id)
    check("it reports a real photo checklist rather than inventing a brief",
          "needs_image" in posts_result, True)


# ---------------------------------------------------------------------------
section("A drafting failure on every slot still completes the task rather than losing the plan")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run(client="Drafting Fails Social Co")
    run = pe.update_inputs(run.id, {"landing_url": "draftfailsocial.example.com",
                                    "primary_cta": "Call today",
                                    "conversion_goal": "phone call"}, actor="rep@example.com")
    run = pe.start_run(run.id, actor="rep@example.com")

    def raising_chat_json(messages, **kw):
        raise RuntimeError("The model is unavailable.")

    hub_ai.chat_json = raising_chat_json
    try:
        tasks = _drain(run)
    finally:
        hub_ai.chat_json = fake_chat_json
    cal_task = tasks["social_calendar"]
    check("the calendar itself still completes -- only the drafting failed",
          cal_task.state, pe.NEEDS_APPROVAL)
    result = cal_task.result()
    check("nothing was drafted", result.get("drafted"), 0)
    check("every slot is counted as failed, not silently dropped",
          result.get("failed"), result.get("slots"))
    with hub_app.app_context():
        batch = social_app.load_batch(result.get("batch_id"))
    check("the real batch still exists, with its empty slots intact",
          len(batch["slots"]) if batch else 0, result.get("slots"))


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
