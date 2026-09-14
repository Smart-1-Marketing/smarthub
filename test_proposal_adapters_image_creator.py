"""Proposal Execution: the image_creator adapter (WO-2, Adapter F).

    python3 test_proposal_adapters_image_creator.py

Same shape as the other adapter test files: no pytest, no new dependencies,
a temporary data directory and a throwaway SQLite database.

## What it asserts

meta_carousel used to come back as `brief`: AI-written text describing what
a Meta carousel ad should look like, with nothing anywhere that had drawn
one. This adapter calls hub.ai.image() -- the same billed OpenAI call
modules/image_creator/app.py's own /api/ai/image route makes -- and saves
the result as a real Image Creator project through
modules.image_creator.projects.save_project(), so the task's artifact is a
project a person can open at /tools/image-creator/, not a paragraph.

hub.ai.image() is stubbed here: it makes a real OpenAI call and CI neither
can nor should. The stub is scoped to this file, the way
test_proposal_adapters_search_ads.py stubs hub.ai.chat_json.
"""
import base64
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1peximgcreator_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "peximgcreator-test-secret"
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
from modules.image_creator import projects as ic_projects               # noqa: E402

assert pe_routes.bp is not None
assert "image_creator" in {a["key"] for a in pe.adapters()}, \
    "the composed app's boot should have registered the image_creator adapter"

with hub_app.app_context():
    db.create_all()

CLIENT = "Image Creator Test Co"
TEXT = "PLAN\nMeta In-Market Home Buyers $2,000\n"
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


_ONE_PX_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_asked = []


def fake_image(prompt, **kw):
    _asked.append({"prompt": prompt, **kw})
    return _ONE_PX_PNG


hub_ai.image = fake_image


# ---------------------------------------------------------------------------
section("hub.proposal_adapters registered image_creator on meta_carousel")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run()
    tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
    check("meta_carousel exists (the proposal mentions Meta)",
          "meta_carousel" in tasks, True)
    check("...and uses the image_creator adapter", tasks["meta_carousel"].adapter, "image_creator")
    check("...in approval mode, like every other adapter that produces creative",
          tasks["meta_carousel"].execution_mode, "approval")


# ---------------------------------------------------------------------------
section("A missing primary CTA holds the task at needs_input, never a raw traceback")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run(client="No CTA Meta Co")
    run = pe.update_inputs(run.id, {"target_geography": "Columbus, OH",
                                    "landing_url": "noctameta.example.com",
                                    "conversion_goal": "form fill"}, actor="rep@example.com")
    # A task's own missing() is checked before its dependency's state, so
    # meta_carousel reads needs_input for its own unanswered primary_cta
    # whatever campaign_foundation (which also needs primary_cta) is doing.
    tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
    check("meta_carousel holds at needs_input on its own missing primary_cta",
          tasks["meta_carousel"].state, pe.NEEDS_INPUT)


# ---------------------------------------------------------------------------
section("A real run: a real image, saved as a real Image Creator project")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run()
    run = pe.update_inputs(run.id, {"target_geography": "Columbus, OH",
                                    "landing_url": "imagecreatortest.example.com",
                                    "conversion_goal": "form fill",
                                    "product_destinations": "3BR/2BA new build\n4BR/3BA new build",
                                    "primary_cta": "Schedule a private tour"}, actor="rep@example.com")
    run = pe.start_run(run.id, actor="rep@example.com")
    tasks = _drain(run)
    task = tasks["meta_carousel"]
    check("meta_carousel reaches needs_approval (mode=approval), not completed",
          task.state, pe.NEEDS_APPROVAL)

    result = task.result()
    check("exactly one image was generated -- never one per destination",
          len(_asked), 1)
    check("the primary CTA reached the prompt",
          "Schedule a private tour" in _asked[0]["prompt"], True)
    check("the first destination seeded the prompt",
          "3BR/2BA new build" in _asked[0]["prompt"], True)
    check("the call was billed under image_creator, the real tool's own module name",
          _asked[0].get("module"), "image_creator")
    check("the client rode along so cost tracking can attribute it",
          _asked[0].get("client"), CLIENT)

    project_id = result.get("project_id")
    check("a project_id came back", bool(project_id), True)
    with hub_app.app_context():
        record = next((r for r in ic_projects.load_index() if r.get("id") == project_id), None)
    check("...and it is a real saved project", record is not None, True)
    check("...filed under this client", record.get("client") if record else None, CLIENT)
    check("...carrying one canvas object (the generated image)",
          (record.get("objects") if record else None), 1)
    check("the artifact_url points at the real project",
          result.get("artifact_url"), f"/tools/image-creator/?project={project_id}")

    # An activity-log entry attributes the work to this client without going
    # through modules.image_creator.app._log(), which reads flask.g/
    # request.environ and would silently drop the entry outside a request --
    # the same flask.g trap Google Finder already paid for once.
    from hub import audit as hub_audit
    entries = [e for e in hub_audit.read(limit=50, module="image_creator", type_="project_saved")
               if e.get("client") == CLIENT]
    check("the save was logged under image_creator, attributed to this client",
          len(entries) >= 1, True)


# ---------------------------------------------------------------------------
section("An unavailable image provider fails the task by name, not silently")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run(client="No Key Meta Co")
    run = pe.update_inputs(run.id, {"target_geography": "Columbus, OH",
                                    "landing_url": "nokeymeta.example.com",
                                    "conversion_goal": "form fill",
                                    "product_destinations": "3BR/2BA new build",
                                    "primary_cta": "Schedule a private tour"}, actor="rep@example.com")
    run = pe.start_run(run.id, actor="rep@example.com")

    def raising_image(prompt, **kw):
        raise hub_ai.AIUnavailable("OPENAI_API_KEY is not set.")

    hub_ai.image = raising_image
    try:
        tasks = _drain(run)
    finally:
        hub_ai.image = fake_image
    task = tasks["meta_carousel"]
    check("the task fails rather than saving a project with no image",
          task.state, pe.FAILED)


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
