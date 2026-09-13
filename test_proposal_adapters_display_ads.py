"""Proposal Execution: the display_ads adapter (WO-2, Adapter E).

    python3 test_proposal_adapters_display_ads.py

Same shape as the other adapter test files: no pytest, no new dependencies,
a temporary data directory and a throwaway SQLite database. The Display Ad
Builder is a separate Node process this test does not start, so the one
HTTP-calling function (hub.ad_builder_link._api) is stubbed the way
hub.ai.chat_json already is elsewhere -- nothing here reaches a real
network address.

## What it asserts

retargeting_creative ran as `brief` -- an AI paragraph describing creative
somebody should go build, never a project anybody could actually open.
hub/ad_builder_link.start_project() is the real, already-existing,
non-Flask entry point the Hub-side "Start a build" form itself calls, so
this adapter is one more caller of it rather than a new integration: a real
run creates a real project in the renderer, validated against the client
registry the same way start_project() always has, and the renderer's own
refusals (an unknown client, the renderer not answering) surface by name
rather than as a raw exception.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1pexdisplayads_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "pexdisplayads-test-secret"
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ADBUILDER_ADMIN_TOKEN", None)
os.environ.pop("ADMIN_TOKEN", None)

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
from hub import ad_builder_link                                         # noqa: E402
from hub import clients_registry                                       # noqa: E402
import hub.proposals as proposals_module                                # noqa: E402

assert pe_routes.bp is not None
assert "display_ads" in {a["key"] for a in pe.adapters()}, \
    "the composed app's boot should have registered the display_ads adapter"

with hub_app.app_context():
    db.create_all()

_RECORDS = {"prop-1": {"id": "prop-1", "filename": "Plan.pdf", "title": "Plan", "kind": "file"}}
_real_find_client = clients_registry.find_client
_real_api = ad_builder_link._api


def _drain(run, cap=20):
    for _ in range(cap):
        out = pe.run_one()
        if out.get("claimed") == 0:
            break
    return {t.task_key: t for t in pe.tasks_for_run(run.id)}


def _new_run(client, text="PLAN\nWebsite Retargeting $500\n"):
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


# ---------------------------------------------------------------------------
section("hub.proposal_adapters registered display_ads on retargeting_creative")
# ---------------------------------------------------------------------------

clients_registry.find_client = lambda name: {"name": name, "domain": ""}
try:
    with hub_app.app_context():
        run = _new_run("Display Ads Adapter Test Co")
        tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
        check("retargeting_creative exists (the proposal mentions Website Retargeting)",
              "retargeting_creative" in tasks, True)
        check("...and uses the display_ads adapter",
              tasks["retargeting_creative"].adapter, "display_ads")
        check("...in approval mode -- a person finishes the build",
              tasks["retargeting_creative"].execution_mode, "approval")
finally:
    clients_registry.find_client = _real_find_client


# ---------------------------------------------------------------------------
section("With no landing URL, the creative task is blocked behind its plan")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run("No URL Display Ads Co")
    tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
    check("retargeting_plan holds at needs_input (it needs landing_url directly)",
          tasks["retargeting_plan"].state, pe.NEEDS_INPUT)
    check("...and retargeting_creative never reaches the scheduler, blocked behind it",
          tasks["retargeting_creative"].state, pe.BLOCKED)


# ---------------------------------------------------------------------------
section("No ADBUILDER_ADMIN_TOKEN: the renderer's own real refusal, by name")
# ---------------------------------------------------------------------------

clients_registry.find_client = lambda name: {"name": name, "domain": ""}
try:
    with hub_app.app_context():
        run = _new_run("No Token Display Ads Co")
        run = pe.update_inputs(run.id, {"landing_url": "notokendisplay.example.com",
                                        "target_geography": "Columbus, OH",
                                        "primary_cta": "Book now",
                                        "conversion_goal": "form fill"}, actor="rep@example.com")
        run = pe.start_run(run.id, actor="rep@example.com")
        tasks = _drain(run)
        creative_task = tasks["retargeting_creative"]
        check("the task fails rather than reporting a build that does not exist",
              creative_task.state, pe.FAILED)
        check("...naming the real, unconfigured reason",
              "ADBUILDER_ADMIN_TOKEN is not set" in creative_task.error, True)
finally:
    clients_registry.find_client = _real_find_client


# ---------------------------------------------------------------------------
section("An unknown client is refused by name, never guessed at as a prospect")
# ---------------------------------------------------------------------------

clients_registry.find_client = lambda name: None
try:
    with hub_app.app_context():
        run = _new_run("Unknown To Registry Co")
        run = pe.update_inputs(run.id, {"landing_url": "unknownregistry.example.com",
                                        "target_geography": "Columbus, OH",
                                        "primary_cta": "Book now",
                                        "conversion_goal": "form fill"}, actor="rep@example.com")
        run = pe.start_run(run.id, actor="rep@example.com")
        tasks = _drain(run)
        check("the task fails rather than silently filing a prospect",
              tasks["retargeting_creative"].state, pe.FAILED)
        check("...naming the client the registry could not find",
              "Unknown To Registry Co" in tasks["retargeting_creative"].error, True)
finally:
    clients_registry.find_client = _real_find_client


# ---------------------------------------------------------------------------
section("A real run: a real project in the renderer, kind=client, never a prospect")
# ---------------------------------------------------------------------------

_seen_payloads = []


def fake_api(method, path, payload=None, timeout=(10, 60)):
    _seen_payloads.append((method, path, payload))
    if method == "POST" and path == "/api/requests":
        return True, {"requestId": "req-adapter-1"}
    return False, {"error": "unexpected call"}


clients_registry.find_client = lambda name: {"name": name, "domain": "displayadstest.example.com"}
ad_builder_link._api = fake_api
try:
    with hub_app.app_context():
        run = _new_run("Display Ads Real Run Co")
        run = pe.update_inputs(run.id, {"landing_url": "displayadstest.example.com",
                                        "target_geography": "Columbus, OH",
                                        "primary_cta": "Book a free estimate",
                                        "conversion_goal": "form fill"}, actor="rep@example.com")
        run = pe.start_run(run.id, actor="rep@example.com")
        tasks = _drain(run)
        creative_task = tasks["retargeting_creative"]
        check("retargeting_creative reaches needs_approval (mode=approval)",
              creative_task.state, pe.NEEDS_APPROVAL)
        result = creative_task.result()
        check("a real request_id came back", result.get("request_id"), "req-adapter-1")
        check("the artifact_url points at the real build",
              result.get("artifact_url"), "/tools/display-ads/build?request=req-adapter-1")

        method, path, payload = _seen_payloads[-1]
        check("the renderer's own real endpoint was called", (method, path),
              ("POST", "/api/requests"))
        check("the client's real registry spelling was used",
              payload.get("business"), "Display Ads Real Run Co")
        check("the landing URL was sent as both website and landingPage",
              (payload.get("website"), payload.get("landingPage")),
              ("https://displayadstest.example.com", "https://displayadstest.example.com"))
        check("the primary CTA reached the renderer as what to promote",
              payload.get("promoting"), "Book a free estimate")

        # kind="client" always -- never a guessed prospect for a business we
        # already have. start_project() only calls _capture_prospect() for
        # kind=="prospect", so this must never have been called.
        check("no lead_id was minted -- this is an existing, known client",
              result.get("lead_id"), "")
finally:
    clients_registry.find_client = _real_find_client
    ad_builder_link._api = _real_api


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
