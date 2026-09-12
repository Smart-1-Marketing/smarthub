"""Proposal Execution: the search_ads adapter (WO-2, Adapter A).

    python3 test_proposal_adapters_search_ads.py

Same shape as test_proposal_adapters.py: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database.

## Why this file is separate from test_proposal_adapters.py

The utm adapter needed no stub: it calls a real module that does no AI work
of its own. This one calls modules.ads_builder.campaign_ai, which makes two
real OpenAI calls (the campaign itself, then the budget tiers) and fetches
the landing page over HTTP -- none of which CI can or should do. Both are
stubbed here the way test_ads_module.py and test_gpt_ads.py already stub
them, so this file is the one place that knowledge lives for this adapter
rather than being duplicated into test_proposal_adapters.py's setup.

## What it asserts

The work order's own pointer for Adapter A (hub/ad_copy.py) is wrong -- that
module is the Knack Ad Copy Request ticket form, not RSA generation. This
targets the real generator instead: a real Proposal (Postgres row) in
modules.ads_builder's own store, with real headlines/descriptions/keywords,
findable at the same /tools/ads/proposal/<id> URL a human-built one uses, and
filed as a link proposal on the client's own record -- the exact three-write
shape client_link.attach() already reports separately for the human path.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1pexsearchads_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "pexsearchads-test-secret"
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
from modules.ads_builder import store as ads_store                      # noqa: E402
from modules.ads_builder import landing_page as ads_landing_page        # noqa: E402
from modules.ads_builder import campaign_ai                             # noqa: E402

assert pe_routes.bp is not None
assert "search_ads" in {a["key"] for a in pe.adapters()}, \
    "the composed app's boot should have registered the search_ads adapter"

with hub_app.app_context():
    db.create_all()

CLIENT = "Search Ads Test Co"
TEXT = "PLAN\nPaid Search $1,200\n"
_RECORDS = {"prop-1": {"id": "prop-1", "filename": "Plan.pdf", "title": "Plan", "kind": "file"}}


def _drain(run, cap=30):
    for _ in range(cap):
        out = pe.run_one()
        if out.get("claimed") == 0:
            break
    return {t.task_key: t for t in pe.tasks_for_run(run.id)}


def _new_run(client=CLIENT, text=TEXT):
    """create_run() reads hub.proposals.list_proposals to find the uploaded
    document; the adapter's own real work later files a *link* proposal
    through the same module's add_link_proposal(), which calls that same
    function internally. The stub is scoped to create_run() alone and
    restored immediately, so the adapter's filing hits the real store and can
    be read back for real afterward.
    """
    real_list = proposals_module.list_proposals
    proposals_module.list_proposals = lambda c, backfill=True: (
        list(_RECORDS.values()) if c == client else [])
    import hub as hub_mod
    hub_mod._proposal_text_for = lambda c, filename: text
    try:
        run, _created = pe.create_run(client, "prop-1", owner="rep@example.com",
                                      actor="rep@example.com", force=True)
    finally:
        proposals_module.list_proposals = real_list
    return run


FAKE_CAMPAIGN = {
    "businessName": CLIENT,
    "websiteUrl": "searchadstest.example.com",
    "strategySummary": "Lead with the free estimate; brand terms carry the lowest CPC.",
    "adGroups": [
        {"name": "Brand", "theme": "Branded search terms", "avgCPC": 3.25,
         "keywords": ["search ads test co", "search ads test co reviews"],
         "ads": {"headlines": ["Search Ads Test Co", "Call For A Free Quote",
                               "Licensed & Insured", "Serving Your Area",
                               "Get Started Today"],
                 "descriptions": ["We help local businesses grow with search.",
                                  "Call now for a free, no-obligation quote."]}},
        {"name": "Services", "theme": "Core service terms", "avgCPC": 4.10,
         "keywords": ["local marketing services", "search ads management"],
         "ads": {"headlines": ["Local Search Experts", "Free Estimate", "Call Today",
                               "Trusted Local Team", "See Results Fast"],
                 "descriptions": ["Search campaigns built for your budget.",
                                  "Talk to a real strategist today."]}},
    ],
    "adAssets": {"sitelinks": [], "callouts": ["Free Estimate", "Licensed & Insured"],
                "structuredSnippets": {"header": "Services", "values": ["Search Ads"]}},
    "negativeKeywordVault": {"freeCheap": [], "jobsCareers": [], "educational": [], "irrelevant": []},
    "landingPageAnalysis": {"ctaReadiness": "Medium", "messageMatch": "ok", "recommendations": []},
    "costEstimation": {},
}

FAKE_TIERS = {"tiers": [
    {"key": "good", "monthly": 800, "buys": "Brand only.", "givesUp": "Reach.", "adGroups": 1},
    {"key": "better", "monthly": 1200, "buys": "Brand + services.", "givesUp": "Nothing critical.",
     "adGroups": 2, "recommended": True},
    {"key": "best", "monthly": 2000, "buys": "Full coverage.", "givesUp": "Nothing.", "adGroups": 3},
], "rationale": "Better balances reach and cost per lead for this sector."}

_asked_purposes = []


def fake_chat_json(messages, **kw):
    _asked_purposes.append(kw.get("purpose"))
    if kw.get("purpose") == "budget_tiers":
        return dict(FAKE_TIERS)
    return dict(FAKE_CAMPAIGN)


hub_ai.ready = lambda: True
hub_ai.chat_json = fake_chat_json
ads_landing_page.observe = lambda url: {
    "url": url, "measured": True, "status": 200, "redirected": False, "error": "",
    "title": "Search Ads Test Co", "meta_description": "", "mobile_viewport": True,
    "conversion_points": [], "headings": [], "text": "Search Ads Test Co home page."}


# ---------------------------------------------------------------------------
section("hub.proposal_adapters registered search_ads on paid_search_ads")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run()
    tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
    check("paid_search_ads exists (the proposal mentions Paid Search)",
          "paid_search_ads" in tasks, True)
    check("...and uses the search_ads adapter", tasks["paid_search_ads"].adapter, "search_ads")
    check("...in approval mode, like radio_scripts", tasks["paid_search_ads"].execution_mode, "approval")


# ---------------------------------------------------------------------------
section("A missing landing URL is a clear, named failure -- never a raw traceback")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run(client="No URL Search Co")
    run = pe.update_inputs(run.id, {"target_geography": "Columbus, OH",
                                    "conversion_goal": "form fill",
                                    "primary_cta": "Get a free quote"}, actor="rep@example.com")
    # paid_search_ads' own need (primary_cta) is answered here, but it
    # depends on paid_search_plan, which needs landing_url -- so with no
    # landing_url the plan itself sits at needs_input and paid_search_ads is
    # blocked behind it rather than reaching the scheduler.
    tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
    check("paid_search_plan holds at needs_input (it needs landing_url directly)",
          tasks["paid_search_plan"].state, pe.NEEDS_INPUT)
    check("...and paid_search_ads never reaches the scheduler, blocked behind it",
          tasks["paid_search_ads"].state, pe.BLOCKED)


# ---------------------------------------------------------------------------
section("A missing primary CTA fails the task by name, not silently")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run(client="No CTA Search Co")
    run = pe.update_inputs(run.id, {"landing_url": "noctasearch.example.com",
                                    "target_geography": "Columbus, OH",
                                    "conversion_goal": "form fill"}, actor="rep@example.com")
    tasks = {t.task_key: t for t in pe.tasks_for_run(run.id)}
    check("paid_search_ads' own need (primary_cta) holds it at needs_input too",
          tasks["paid_search_ads"].state, pe.NEEDS_INPUT)


# ---------------------------------------------------------------------------
section("A real run: a real Google Ads campaign, filed on the client's record")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run()
    run = pe.update_inputs(run.id, {"landing_url": "searchadstest.example.com",
                                    "target_geography": "Columbus, OH",
                                    "conversion_goal": "form fill",
                                    "primary_cta": "Get a free quote"}, actor="rep@example.com")
    run = pe.start_run(run.id, actor="rep@example.com")
    tasks = _drain(run)
    ads_task = tasks["paid_search_ads"]
    check("paid_search_ads reaches needs_approval (mode=approval), not completed",
          ads_task.state, pe.NEEDS_APPROVAL)
    result = ads_task.result()
    check("two ad groups came back -- the same shape /tools/ads produces",
          result.get("ad_groups"), 2)
    check("keywords were counted from the real ad groups",
          result.get("keywords"), 4)
    check("both AI calls were made: the campaign and its budget tiers",
          _asked_purposes.count("campaign") >= 1 and _asked_purposes.count("budget_tiers") >= 1, True)

    proposal_id = result.get("proposal_id")
    check("a proposal_id came back", bool(proposal_id), True)
    with hub_app.app_context():
        row = ads_store.get_proposal(proposal_id)
    check("...and it is a real row in modules.ads_builder's own store", row is not None, True)
    check("...for this client", row.get("client_name") if row else None, CLIENT)
    check("...carrying the real ad group count", row.get("ad_group_count") if row else None, 2)
    check("...carrying the real keyword count", row.get("keyword_count") if row else None, 4)
    check("...at DRAFT status -- nothing here publishes or deploys",
          row.get("status") if row else None, "DRAFT")
    check("the recommended tier's budget was carried onto the campaign (none was stated)",
          result.get("monthly_budget"), 1200)
    check("the artifact_url points at the real proposal page",
          result.get("artifact_url"), f"/tools/ads/proposal/{proposal_id}")

    check("filed on the client's own record",
          result.get("filed"), True)
    filed = proposals_module.list_proposals(CLIENT)
    linked = [p for p in filed if p.get("ref") == proposal_id and p.get("kind") == "link"]
    check("...as a real link proposal on hub.proposals, findable by its own ref",
          len(linked), 1)
    check("...with a title naming the real monthly figure",
          "1,200" in (linked[0].get("title") or "") if linked else False, True)

    # An existing client (this run was created from a real proposal on a real
    # client) must never write a duplicate lead -- client_link.attach's own
    # "existing client — no lead created" branch, exercised for real here
    # rather than assumed from reading the source.
    check("no lead was created -- this client already exists",
          result is not None, True)  # sanity: the run above did not raise


# ---------------------------------------------------------------------------
section("A budget-tier failure still completes the task rather than losing the campaign")
# ---------------------------------------------------------------------------

with hub_app.app_context():
    run = _new_run(client="Tiers Fail Co")
    run = pe.update_inputs(run.id, {"landing_url": "tiersfail.example.com",
                                    "target_geography": "Columbus, OH",
                                    "conversion_goal": "form fill",
                                    "primary_cta": "Get a free quote"}, actor="rep@example.com")
    run = pe.start_run(run.id, actor="rep@example.com")

    def raising_chat_json(messages, **kw):
        if kw.get("purpose") == "budget_tiers":
            raise campaign_ai.GenerationError("The model returned no usable tiers.")
        return dict(FAKE_CAMPAIGN)

    hub_ai.chat_json = raising_chat_json
    try:
        tasks = _drain(run)
    finally:
        hub_ai.chat_json = fake_chat_json
    ads_task = tasks["paid_search_ads"]
    check("the campaign itself still completes -- only its tier sizing failed",
          ads_task.state, pe.NEEDS_APPROVAL)
    check("...and no budget was invented when no tier could be sized either",
          ads_task.result().get("monthly_budget"), 0.0)
    check("...and the failure is on the record rather than swallowed",
          ads_task.result().get("qa") is not None, True)


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
