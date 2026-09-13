"""Proposal Execution: the commercial_builder adapter (WO-2, Adapter G).

    python3 test_proposal_adapters_commercial.py

Same shape as the other adapter test files: no pytest, no new dependencies,
a temporary data directory and a throwaway SQLite database.

## What it asserts

youtube_video_concept used to come back as `brief`: AI-written text
describing a video concept nobody had actually drafted. This creates a
real modules.commercial_builder CommercialProject through the same
client_link.ensure_client() and generation.run_concepts() the Storyboard
Editor's own "Generate Concepts" button calls, and stops there -- picking
a concept, writing the script and casting a voice stay a person's job.

hub.ai.chat_json is stubbed here the way test_proposal_adapters_search_ads.py
stubs it -- generation.run_concepts() reaches it through
modules.commercial_builder.services.openai_service._chat_json(). With no
OpenAI key at all, that service degrades to deterministic mock concepts
rather than raising -- the whole Commercial Builder is built to stay
clickable with no live key -- so this file exercises both: a live-looking
key with the model stubbed, and no key at all.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1pexcommercial_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "pexcommercial-test-secret"
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
import hub.proposals as proposals_module                                # noqa: E402
from modules.commercial_builder.models import Client, CommercialProject # noqa: E402

assert pe_routes.bp is not None
assert "commercial_builder" in {a["key"] for a in pe.adapters()}, \
    "the composed app's boot should have registered the commercial_builder adapter"

with hub_app.app_context():
    db.create_all()

CLIENT = "Commercial Builder Test Co"
TEXT = "PLAN\nMonthly YouTube Sales Video $1,500\n"
_RECORDS = {"prop-1": {"id": "prop-1", "filename": "Plan.pdf", "title": "Plan", "kind": "file"}}


def _drain(run, cap=30):
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


FAKE_CONCEPTS = {"concepts": [
    {"title": "Problem to solution", "angle": "Problem -> service -> offer -> CTA",
     "summary": "Open on the pain point, resolve it with the service, close on the CTA."},
    {"title": "Local trust", "angle": "Neighbor testimonial",
     "summary": "A recognizable local voice vouches for the business."},
]}

_asked_purposes = []


def fake_chat_json(messages, **kw):
    _asked_purposes.append(kw.get("purpose"))
    return dict(FAKE_CONCEPTS)


hub_ai.chat_json = fake_chat_json


# ---------------------------------------------------------------------------
section("hub.proposal_adapters registered commercial_builder on youtube_video_concept")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run()
    tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
    check("youtube_video_concept exists (the proposal mentions YouTube video)",
          "youtube_video_concept" in tasks, True)
    check("...and uses the commercial_builder adapter",
          tasks["youtube_video_concept"].adapter, "commercial_builder")
    check("...in approval mode, like every other adapter that produces creative",
          tasks["youtube_video_concept"].execution_mode, "approval")


# ---------------------------------------------------------------------------
section("A missing primary CTA holds the task at needs_input, never a raw traceback")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run(client="No CTA Video Co")
    run = pe.update_inputs(run.id, {"landing_url": "noctavideo.example.com",
                                    "conversion_goal": "form fill"}, actor="rep@example.com")
    tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
    check("youtube_video_concept holds at needs_input on its own missing primary_cta",
          tasks["youtube_video_concept"].state, pe.NEEDS_INPUT)


# ---------------------------------------------------------------------------
section("A real run: a real CommercialProject with real generated concepts")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    os.environ["OPENAI_API_KEY"] = "sk-test-live-looking-key"
    try:
        run = _new_run()
        run = pe.update_inputs(run.id, {"landing_url": "commercialtest.example.com",
                                        "conversion_goal": "form fill",
                                        "primary_cta": "Call for a free quote"}, actor="rep@example.com")
        run = pe.start_run(run.id, actor="rep@example.com")
        tasks = _drain(run)
    finally:
        os.environ.pop("OPENAI_API_KEY", None)

    task = tasks["youtube_video_concept"]
    check("youtube_video_concept reaches needs_approval (mode=approval), not completed",
          task.state, pe.NEEDS_APPROVAL)

    result = task.result()
    check("two concepts came back from the real (stubbed) model call",
          result.get("concepts"), 2)
    check("the AI call was made for concepts", "concepts" in _asked_purposes, True)
    check("live is reported true -- a key was configured for this run",
          result.get("live"), True)

    project_id = result.get("project_id")
    check("a project_id came back", bool(project_id), True)
    with hub_app.app_context():
        project = CommercialProject.query.get(project_id)
        client_row = Client.query.get(project.client_id) if project else None
    check("...and it is a real saved CommercialProject", project is not None, True)
    check("...filed under a real cb_clients row for this client",
          client_row.name if client_row else None, CLIENT)
    check("...carrying the real generated concepts",
          len((project.concepts or [])) if project else 0, 2)
    check("...moved to concepts status -- nothing here writes a script or renders",
          project.status if project else None, "concepts")
    check("...the primary CTA reached the brief",
          (project.brief or {}).get("primary_cta") if project else None,
          "Call for a free quote")
    check("the artifact_url points at the real project's concepts screen",
          result.get("artifact_url"),
          f"/tools/commercial-builder/project/{project_id}/concepts")

    from hub import audit as hub_audit
    entries = [e for e in hub_audit.read(limit=50, module="commercial_builder",
                                          type_="cb_commercial_started")
               if e.get("client") == CLIENT]
    check("the start was logged under commercial_builder, attributed to this client",
          len(entries) >= 1, True)


# ---------------------------------------------------------------------------
section("No OpenAI key: a real project with honest mock concepts, not a failure")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run(client="No Key Video Co")
    run = pe.update_inputs(run.id, {"landing_url": "nokeyvideo.example.com",
                                    "conversion_goal": "form fill",
                                    "primary_cta": "Book a consultation"}, actor="rep@example.com")
    run = pe.start_run(run.id, actor="rep@example.com")
    tasks = _drain(run)
    task = tasks["youtube_video_concept"]
    check("the task still reaches needs_approval rather than failing",
          task.state, pe.NEEDS_APPROVAL)
    result = task.result()
    check("mock concepts still produced a real, non-empty concept list",
          result.get("concepts", 0) > 0, True)
    check("live is reported false -- honestly, no key was configured",
          result.get("live"), False)


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
