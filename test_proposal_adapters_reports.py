"""Proposal Execution: the reports adapter (WO-2 follow-up).

    python3 test_proposal_adapters_reports.py

Same shape as the other adapter test files: no pytest, no new dependencies,
a temporary data directory and a throwaway SQLite database. modules.reports
binds its own engine from DATABASE_URL when REPORTS_DATABASE_URL is unset
(modules/reports/store.py's own documented fallback order) and creates its
tables at import time, so nothing extra has to be booted for this test.

## What it asserts

reporting_plan ran as `brief` -- a paragraph describing a reporting cadence,
with no client-facing dashboard anybody could actually open. This adapter
mints a real modules.reports.store.ReportLink (the same token
/reports/r/c/<token> serves) and a real BudgetLine per channel this
proposal's own analysis quotes a dollar figure for -- the same rows the
Reports module's own Budgets and Client Links screens read and write.

Idempotency is asserted directly: re-running the adapter's own `run()`
function against a run that already has a link and budget lines must reuse
the link rather than minting a second live token, and must not double a
budget line already on file for the same client, product and figure.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1pexreports_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "pexreports-test-secret"
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("REPORTS_DATABASE_URL", None)
# Other channels this test's proposal text also triggers (retargeting_creative,
# meta_carousel) must not reach out to anything real -- they are not what
# this file is testing. With no admin token the ad builder call fails fast,
# by name, with no network attempt; the same shape test_proposal_adapters_
# display_ads.py's own "No ADBUILDER_ADMIN_TOKEN" section already relies on.
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
from hub import clients_registry                                        # noqa: E402
import hub.proposals as proposals_module                                # noqa: E402
from modules.reports import store as reports_store                      # noqa: E402
from hub.proposal_adapters import reports as reports_adapter            # noqa: E402

assert pe_routes.bp is not None
assert "reports" in {a["key"] for a in pe.adapters()}, \
    "the composed app's boot should have registered the reports adapter"

with hub_app.app_context():
    db.create_all()

_RECORDS = {"prop-1": {"id": "prop-1", "filename": "Plan.pdf", "title": "Plan", "kind": "file"}}
_real_find_client = clients_registry.find_client


def _drain(run, cap=30):
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


# ---------------------------------------------------------------------------
section("reporting_plan is on every run, wired to the reports adapter, auto mode")
# ---------------------------------------------------------------------------

clients_registry.find_client = lambda name: {"name": name, "domain": ""}
try:
    with hub_app.app_context():
        run = _new_run("Reports Adapter Test Co", "PLAN\nWebsite Retargeting $500\n")
        tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
        check("reporting_plan exists on every run, not gated on a channel",
              "reporting_plan" in tasks, True)
        check("...and uses the reports adapter",
              tasks["reporting_plan"].adapter, "reports")
        check("...in auto mode -- a link and a budget figure, not client-facing copy",
              tasks["reporting_plan"].execution_mode, "auto")
finally:
    clients_registry.find_client = _real_find_client


# ---------------------------------------------------------------------------
section("reporting_plan is on every run, even with no channel at all")
# ---------------------------------------------------------------------------

clients_registry.find_client = lambda name: {"name": name, "domain": ""}
try:
    with hub_app.app_context():
        run = _new_run("No Channel Reports Co", "PLAN\nNothing here matches any known channel pattern.\n")
        run = pe.update_inputs(run.id, {"landing_url": "nochannelreports.example.com",
                                        "conversion_goal": "form fill"}, actor="rep@example.com")
        run = pe.start_run(run.id, actor="rep@example.com")
        tasks = _drain(run)
        check("reporting_plan still exists on this run", "reporting_plan" in tasks, True)

    # -----------------------------------------------------------------------
    section("No channel carries a quoted dollar figure: retryable, not a defect")
    # -----------------------------------------------------------------------

    with hub_app.app_context():
        run = _new_run("No Budget Reports Co", "PLAN\nWebsite Retargeting, no dollar figure quoted at all.\n")
        run = pe.update_inputs(run.id, {"landing_url": "https://nobudgetreports.example.com",
                                        "primary_cta": "Book now",
                                        "conversion_goal": "form fill"}, actor="rep@example.com")
        run = pe.start_run(run.id, actor="rep@example.com")
        tasks = _drain(run)
        rp = tasks["reporting_plan"]
        check("with a channel but no quoted figure, the task fails rather than inventing one",
              rp.state, pe.FAILED)
        check("...naming why, rather than a raw exception",
              "dollar figure" in (rp.error or ""), True)
finally:
    clients_registry.find_client = _real_find_client


# ---------------------------------------------------------------------------
section("A real run: a real link and real budget lines, one per priced channel")
# ---------------------------------------------------------------------------

CLIENT = "Reports Real Run Co"
PROPOSAL_TEXT = ("PLAN\nWebsite Retargeting $500\nMeta In-Market Home Buyers $1,200\n"
                 "SEO ongoing maintenance, no set monthly figure.\n")

clients_registry.find_client = lambda name: {"name": name, "domain": "reportsrealrun.example.com"}
try:
    with hub_app.app_context():
        run = _new_run(CLIENT, PROPOSAL_TEXT)
        run = pe.update_inputs(run.id, {"landing_url": "reportsrealrun.example.com",
                                        "target_geography": "Columbus, OH",
                                        "primary_cta": "Book a tour",
                                        "conversion_goal": "form fill"}, actor="rep@example.com")
        run = pe.start_run(run.id, actor="rep@example.com")
        tasks = _drain(run)
        rp = tasks["reporting_plan"]
        check("reporting_plan reaches completed (mode=auto)", rp.state, pe.COMPLETED)

        result = rp.result()
        link = reports_store.link_for_client(CLIENT)
        check("a real ReportLink was minted for this client", link is not None, True)
        check("the returned token matches the real, stored link",
              result.get("token"), link.token if link else None)
        check("the artifact_url points at the real client dashboard",
              result.get("artifact_url"), f"/reports/r/c/{link.token}" if link else None)

        lines = reports_store.budget_lines_for(CLIENT)
        products = sorted(b["product"] for b in lines)
        check("a budget line was written for each channel carrying a real figure",
              products, sorted(["Website Retargeting", "Meta In-Market Home Buyers"]))
        by_product = {b["product"]: float(b["monthly_budget"]) for b in lines}
        check("Website Retargeting priced at $500", by_product.get("Website Retargeting"), 500.0)
        check("Meta priced at $1,200", by_product.get("Meta In-Market Home Buyers"), 1200.0)
        check("SEO (no quoted figure) is reported, never invented",
              "SEO + AI" in (result.get("channels_without_a_figure") or []) or
              any("seo" in c.lower() for c in result.get("channels_without_a_figure") or []), True)
finally:
    clients_registry.find_client = _real_find_client


# ---------------------------------------------------------------------------
section("Idempotent: re-running the adapter reuses the link and skips existing lines")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
    before_link = reports_store.link_for_client(CLIENT)
    before_lines = len(reports_store.budget_lines_for(CLIENT))

    second = reports_adapter.run(run, tasks["reporting_plan"])

    after_link = reports_store.link_for_client(CLIENT)
    after_lines = reports_store.budget_lines_for(CLIENT)

    check("re-running mints no second live link for this client",
          after_link.token, before_link.token)
    check("re-running writes no duplicate budget lines",
          len(after_lines), before_lines)
    check("re-running reports every priced channel as already on file",
          sorted(second.get("budget_lines_existing") or []),
          sorted(["Website Retargeting", "Meta In-Market Home Buyers"]))
    check("...and adds none", second.get("budget_lines_added"), [])


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
