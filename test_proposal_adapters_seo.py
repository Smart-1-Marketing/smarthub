"""Proposal Execution: the seo_scan / seo_workplan adapters (WO-2, Adapter D).

    python3 test_proposal_adapters_seo.py

Same shape as the other adapter test files: no pytest, no new dependencies,
a temporary data directory and a throwaway SQLite database. No network call
and no OpenAI call: this adapter is deliberately read-only, so the only
thing to stand up is a real completed scan row -- through
modules.scans.app.Scan, the way test_website_audit.py already does, never
hand-written SQL.

## What it asserts

seo_audit and seo_workplan both ran as `brief` -- an AI-written paragraph
imagining what an SEO audit might find, for a site nobody had actually
looked at. seo_audit now calls hub.website_audit.audit(), the same reader
Client 360 uses, so a real completed scan produces real findings each
carrying their own evidence; and where no scan exists yet it says so and
names where to run one, rather than inventing an audit. seo_workplan
depends on seo_audit and re-reads its real findings rather than writing a
second, AI-imagined workplan on top of them.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1pexseo_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "pexseo-test-secret"
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
import hub.proposals as proposals_module                                # noqa: E402
from modules.scans import app as scans_app                              # noqa: E402

assert pe_routes.bp is not None
assert {"seo_scan", "seo_workplan"} <= {a["key"] for a in pe.adapters()}, \
    "the composed app's boot should have registered the seo adapters"

with hub_app.app_context():
    db.create_all()

_RECORDS = {"prop-1": {"id": "prop-1", "filename": "Plan.pdf", "title": "Plan", "kind": "file"}}


def _drain(run, cap=20):
    for _ in range(cap):
        out = pe.run_one()
        if out.get("claimed") == 0:
            break
    return {t.task_key: t for t in pe.tasks_for_run(run.id)}


def _new_run(client, text):
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


def _seed_scan(domain, report, *, public_id, days_old=10):
    with hub_app.app_context():
        s = scans_app.SessionLocal()
        try:
            s.add(scans_app.Scan(
                public_id=public_id, domain_key=domain, input_url=domain,
                overall_score=71, tier="Silver", status="complete",
                raw_report=json.dumps(report),
                created_at=datetime.now(timezone.utc) - timedelta(days=days_old),
                completed_at=datetime.now(timezone.utc) - timedelta(days=days_old)))
            s.commit()
        finally:
            s.close()


TEXT = "PLAN\nEnhanced SEO + AI Optimization $1,400\n"


# ---------------------------------------------------------------------------
section("hub.proposal_adapters registered the seo adapters on the right tasks")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run("SEO Adapter Test Co", TEXT)
    tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
    check("seo_audit exists (the proposal mentions Enhanced SEO + AI)",
          "seo_audit" in tasks, True)
    check("...and uses the seo_scan adapter", tasks["seo_audit"].adapter, "seo_scan")
    check("...in auto mode -- it invents nothing, so it needs no approval gate",
          tasks["seo_audit"].execution_mode, "auto")
    check("seo_workplan uses the seo_workplan adapter", tasks["seo_workplan"].adapter, "seo_workplan")


# ---------------------------------------------------------------------------
section("A missing landing URL is a clear, named failure -- never a raw traceback")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run("No URL SEO Co", TEXT)
    tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
    check("seo_audit's own need (landing_url) holds it at needs_input",
          tasks["seo_audit"].state, pe.NEEDS_INPUT)


# ---------------------------------------------------------------------------
section("No scan exists yet: the audit says so rather than inventing one")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run("Never Scanned SEO Co", TEXT)
    run = pe.update_inputs(run.id, {"landing_url": "neverscannedseo.example.com",
                                    "primary_cta": "Call now", "conversion_goal": "form fill",
                                    "target_geography": "Columbus, OH"}, actor="rep@example.com")
    run = pe.start_run(run.id, actor="rep@example.com")
    tasks = _drain(run)
    audit_task = tasks["seo_audit"]
    check("seo_audit completes (auto mode) rather than blocking forever",
          audit_task.state, pe.COMPLETED)
    result = audit_task.result()
    check("it says no audit was found", result.get("found"), False)
    check("...and names that a scan is needed", result.get("needs_scan"), True)
    check("...pointing at Site Scans", result.get("artifact_url"), "/scans")

    # seo_workplan cannot build a real plan from a reading that does not
    # exist -- it must fail by name rather than claim "no gaps found", which
    # would read as a clean site when nobody has looked at it at all.
    workplan_task = tasks["seo_workplan"]
    check("seo_workplan refuses rather than reporting a false all-clear",
          workplan_task.state, pe.FAILED)
    check("...naming the real reason", "has not found a site reading" in workplan_task.error, True)


# ---------------------------------------------------------------------------
section("A real scan: real findings, each carrying its own evidence")
# ---------------------------------------------------------------------------

REPORT_WITH_GAPS = {
    "retargeting": {"has_facebook_pixel": False, "has_google_pixel": False},
    "analytics": {"has_analytics": False},
    "google_business_profile": {"is_listing_found": True, "is_listing_claimed": False,
                                "review_count": 8},
    "paid_search": {"has_adwords_spend": True, "average_adspend": 1800, "average_adtraffic": 500},
}

with hub_app.app_context():
    run = _new_run("Scanned SEO Co", TEXT)
    run = pe.update_inputs(run.id, {"landing_url": "scannedseo.example.com",
                                    "primary_cta": "Call now", "conversion_goal": "form fill",
                                    "target_geography": "Columbus, OH"}, actor="rep@example.com")
    _seed_scan("scannedseo.example.com", REPORT_WITH_GAPS, public_id="seo-adapter-1")
    run = pe.start_run(run.id, actor="rep@example.com")
    tasks = _drain(run)
    audit_task = tasks["seo_audit"]
    check("seo_audit completes with a real reading", audit_task.state, pe.COMPLETED)
    result = audit_task.result()
    check("the real scan was found", result.get("found"), True)
    check("the real domain came back", result.get("domain"), "scannedseo.example.com")
    keys = {o["key"] for o in result.get("opportunities") or []}
    check("no-retargeting is a real, evidenced finding", "no_retargeting" in keys, True)
    check("no-analytics too", "no_analytics" in keys, True)
    check("an unclaimed listing too", "gbp_unclaimed" in keys, True)
    check("running paid search is NOT a finding -- it is running",
          "no_paid_search" in keys, False)
    check("the artifact_url points at the client's own 360 record",
          result.get("artifact_url"), "/client360?q=Scanned%20SEO%20Co")

    workplan_task = tasks["seo_workplan"]
    check("seo_workplan completes once the audit found something real",
          workplan_task.state, pe.COMPLETED)
    plan = workplan_task.result().get("workplan") or []
    check("every audit finding reached the workplan", len(plan), len(keys))
    check("each carries a priority, a finding, and what it recommends",
          all({"priority", "finding", "means", "recommend"} <= set(p) for p in plan), True)
    check("priorities are 1..N, in the order the audit surfaced them",
          [p["priority"] for p in plan], list(range(1, len(plan) + 1)))


# ---------------------------------------------------------------------------
section("A clean scan (no gaps) is a real answer, not a failure")
# ---------------------------------------------------------------------------

CLEAN_REPORT = {
    "retargeting": {"has_facebook_pixel": True, "has_google_pixel": True},
    "analytics": {"has_analytics": True, "uses_universal_ga": False},
    "google_business_profile": {"is_listing_found": True, "is_listing_claimed": True,
                                "review_count": 120},
    "ai_readiness": {"is_ai_optimised": True},
    "paid_search": {"has_adwords_spend": True, "average_adspend": 1800, "average_adtraffic": 500},
    "booking_widget": {"has_booking_widget": True},
    "click_to_contact": {"tel_links_found_count": 3},
    "live_chat": {"has_live_chat": True},
    "mobile": {"is_mobile": True},
    "alternative_text": {"images_no_alt_count": 0},
    "structured_data": {"count_missing_schema_items": 0},
    "facebook_page": {"days_since_last_post": 2},
}

with hub_app.app_context():
    run = _new_run("Clean Scan SEO Co", TEXT)
    run = pe.update_inputs(run.id, {"landing_url": "cleanscanseo.example.com",
                                    "primary_cta": "Call now", "conversion_goal": "form fill",
                                    "target_geography": "Columbus, OH"}, actor="rep@example.com")
    _seed_scan("cleanscanseo.example.com", CLEAN_REPORT, public_id="seo-adapter-2")
    run = pe.start_run(run.id, actor="rep@example.com")
    tasks = _drain(run)
    check("seo_audit still completes and found the scan",
          (tasks["seo_audit"].state, tasks["seo_audit"].result().get("found")),
          (pe.COMPLETED, True))
    check("...with no opportunities on a genuinely clean site",
          tasks["seo_audit"].result().get("opportunities"), [])
    check("seo_workplan completes rather than failing on an empty plan",
          tasks["seo_workplan"].state, pe.COMPLETED)
    wp = tasks["seo_workplan"].result()
    check("...reporting no gaps rather than an empty, unexplained list",
          "No gaps were found" in wp.get("summary", ""), True)
    check("...with an empty workplan", wp.get("workplan"), [])


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
