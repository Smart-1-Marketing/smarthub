"""Focused checks for the Smart 1 Ads Optimization workspace.

    python test_ads_optimization.py

No provider, model, real database, or browser is contacted.
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="s1ads_optimization_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
os.environ.pop("OPENAI_API_KEY", None)

from modules.ads_builder import google_ads, optimization  # noqa: E402

passed = failed = 0


def check(label, condition, detail=None):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}\n        {detail!r}")


def sample_rows():
    return {
        "summary": [{
            "customer": {"id": "2223334444", "descriptiveName": "Client Ads",
                         "currencyCode": "USD", "optimizationScore": 0.72},
            "metrics": {"optimizationScoreUplift": 0.11,
                        "optimizationScoreUrl": "https://ads.google.com/aw/recommendations"},
        }],
        "recommendations": [{"recommendation": {
            "resourceName": "customers/2223334444/recommendations/tag-1",
            "type": "IMPROVE_GOOGLE_TAG_COVERAGE", "dismissed": False,
        }}],
        "campaigns": [
            {"campaign": {"id": "10", "name": "Expensive Search", "status": "ENABLED",
                          "advertisingChannelType": "SEARCH", "biddingStrategyType": "MAXIMIZE_CONVERSIONS",
                          "maximizeConversions": {"targetCpaMicros": "30000000"}},
             "metrics": {"costMicros": "100000000", "clicks": 10, "impressions": 500,
                         "averageCpc": "10000000", "conversions": 0}},
            {"campaign": {"id": "20", "name": "Efficient Search", "status": "ENABLED",
                          "advertisingChannelType": "SEARCH", "biddingStrategyType": "MANUAL_CPC"},
             "metrics": {"costMicros": "20000000", "clicks": 10, "impressions": 500,
                         "averageCpc": "2000000", "conversions": 4}},
        ],
        "search_terms": [
            {"campaign": {"id": "10", "name": "Expensive Search"},
             "adGroup": {"id": "101", "name": "Roof"},
             "searchTermView": {"searchTerm": "free roof quote", "status": "NONE"},
             "metrics": {"costMicros": "40000000", "clicks": 5, "impressions": 40, "conversions": 0}},
            {"campaign": {"id": "20", "name": "Efficient Search"},
             "adGroup": {"id": "201", "name": "Repair"},
             "searchTermView": {"searchTerm": "emergency roof repair", "status": "NONE"},
             "metrics": {"costMicros": "12000000", "clicks": 4, "impressions": 30, "conversions": 2}},
        ],
        "keywords": [
            {"campaign": {"id": "10", "name": "Expensive Search"}, "adGroup": {"id": "101", "name": "Roof"},
             "adGroupCriterion": {"criterionId": "1", "keyword": {"text": "roof repair", "matchType": "PHRASE"}, "status": "ENABLED", "negative": False},
             "metrics": {"clicks": 8, "impressions": 100, "conversions": 2}},
            {"campaign": {"id": "10", "name": "Expensive Search"}, "adGroup": {"id": "101", "name": "Roof"},
             "adGroupCriterion": {"criterionId": "2", "keyword": {"text": " roof  repair ", "matchType": "PHRASE"}, "status": "ENABLED", "negative": False},
             "metrics": {"clicks": 1, "impressions": 10, "conversions": 0}},
        ],
        "schedule": [{"campaign": {"id": "10", "name": "Expensive Search"},
                      "segments": {"dayOfWeek": "MONDAY", "hour": 2},
                      "metrics": {"costMicros": "35000000", "clicks": 6, "impressions": 80, "conversions": 0}}],
    }


def run():
    global passed, failed
    print("\nSmart 1 Ads optimization test\n")
    result = optimization.analyse_rows("222-333-4444", "LAST_30_DAYS", sample_rows())
    actions = [item.get("action") for item in result["items"]]
    categories = {item["category"] for item in result["items"]}
    check("Google's account optimization score is preserved", result["score_percent"] == 72, result)
    check("high click costs become a review step", "click_costs" in categories, result["items"])
    check("underperforming search terms can become an exact negative",
          any(i.get("action") == "add_negative_keyword" and i["data"]["match_type"] == "EXACT" for i in result["items"]), result["items"])
    check("converting search terms become paused-keyword candidates", "add_keyword" in actions, result["items"])
    check("redundant keywords are individually removable", "remove_keyword" in actions, result["items"])
    check("weak day/hour slots are guidance, not automatic writes",
          any(i["category"] == "schedule" and not i.get("action") for i in result["items"]), result["items"])
    check("Google tag diagnostics remain visible", "tracking" in categories, categories)

    query_text = " ".join((optimization.SUMMARY_QUERY, optimization.CAMPAIGNS_QUERY,
                           optimization.SEARCH_TERMS_QUERY, optimization.KEYWORDS_QUERY,
                           optimization.SCHEDULE_QUERY))
    check("all scan queries use v25-compatible campaign date handling",
          "campaign.start_date" not in query_text and "campaign.end_date" not in query_text, query_text)
    check("optimization queries use official customer and campaign score fields",
          "customer.optimization_score" in query_text and "campaign.optimization_score" in query_text, query_text)

    calls = []
    real_request, real_search = google_ads.request, google_ads.search

    def fake_request(method, path, body=None, **kwargs):
        calls.append({"method": method, "path": path, "body": body})
        return {"results": [{}]}

    def fake_search(customer_id, query, **kwargs):
        return [{"campaign": {"id": "10", "biddingStrategyType": "MAXIMIZE_CONVERSIONS",
                              "biddingStrategy": ""}}]

    google_ads.request, google_ads.search = fake_request, fake_search
    try:
        refused = False
        try:
            optimization.apply_action("2223334444", "add_negative_keyword", {
                "ad_group_id": "101", "text": "free roof quote", "match_type": "EXACT",
                "confirmation": "yes",
            })
        except google_ads.GoogleAdsError:
            refused = True
        check("a mutation is refused without its exact individual approval", refused and not calls, calls)

        optimization.apply_action("2223334444", "add_negative_keyword", {
            "ad_group_id": "101", "text": "free roof quote", "match_type": "EXACT",
            "confirmation": "APPROVE",
        })
        negative = calls[-1]["body"]["operations"][0]["create"]
        check("approved negatives are one enabled exact criterion",
              negative["negative"] is True and negative["status"] == "ENABLED"
              and negative["keyword"]["matchType"] == "EXACT", negative)

        optimization.apply_action("2223334444", "add_keyword", {
            "ad_group_id": "201", "text": "emergency roof repair", "match_type": "EXACT",
            "confirmation": "APPROVE",
        })
        keyword = calls[-1]["body"]["operations"][0]["create"]
        check("approved new keywords are created paused", keyword["status"] == "PAUSED" and not keyword["negative"], keyword)

        optimization.apply_action("2223334444", "remove_keyword", {
            "ad_group_id": "101", "criterion_id": "2", "confirmation": "REMOVE",
        })
        check("redundant removal names one exact criterion",
              calls[-1]["body"]["operations"] == [{"remove": "customers/2223334444/adGroupCriteria/101~2"}], calls[-1])

        optimization.apply_action("2223334444", "apply_recommendation", {
            "resource_name": "customers/2223334444/recommendations/tag-1", "confirmation": "APPROVE",
        })
        check("Google recommendations apply one resource at a time",
              calls[-1]["path"].endswith("/recommendations:apply")
              and calls[-1]["body"] == {"operations": [{"resourceName": "customers/2223334444/recommendations/tag-1"}]}, calls[-1])

        optimization.apply_action("2223334444", "add_sitelink", {
            "campaign_id": "10", "link_text": "Emergency Repair", "final_url": "example.com/repair",
            "description1": "Fast local response", "description2": "Request service today", "confirmation": "APPROVE",
        })
        ops = calls[-1]["body"]["mutateOperations"]
        check("a sitelink creates and associates one asset atomically",
              len(ops) == 2 and "assetOperation" in ops[0] and "campaignAssetOperation" in ops[1]
              and calls[-1]["body"]["partialFailure"] is False, ops)

        png = b"\x89PNG\r\n\x1a\n" + b"test"
        optimization.apply_action("2223334444", "add_image", {
            "campaign_id": "10", "name": "Approved creative",
            "image_data": "data:image/png;base64," + base64.b64encode(png).decode(),
            "confirmation": "APPROVE",
        })
        image_ops = calls[-1]["body"]["mutateOperations"]
        check("an image approval creates and associates one asset atomically",
              image_ops[1]["campaignAssetOperation"]["create"]["fieldType"] == "AD_IMAGE", image_ops)

        optimization.apply_action("2223334444", "set_target_cpa", {
            "campaign_id": "10", "target_cpa": "32.50", "confirmation": "APPROVE",
        })
        cpa = calls[-1]["body"]["operations"][0]
        check("Target CPA updates only the standard Maximize Conversions field",
              cpa["update"]["maximizeConversions"]["targetCpaMicros"] == "32500000"
              and cpa["updateMask"] == "maximize_conversions.target_cpa_micros", cpa)
    finally:
        google_ads.request, google_ads.search = real_request, real_search

    page = (ROOT / "modules/ads_builder/templates/ads_optimization.html").read_text(encoding="utf-8")
    live = (ROOT / "modules/ads_builder/templates/ads_campaigns.html").read_text(encoding="utf-8")
    nav = (ROOT / "modules/ads_builder/templates/ads_base.html").read_text(encoding="utf-8")
    check("Optimization is a top Smart 1 Ads menu item", 'active==\'optimization\'' in nav, nav)
    check("the workspace has filters and the seven guided steps",
          all(x in page for x in ("issueSearch", "categoryFilter", "priorityFilter", "Diagnostics", "Click costs", "Search terms", "Days & hours", "Bidding & tags")), None)
    check("the screen states that AI never auto-applies changes",
          "Nothing is changed automatically" in page and "AI suggestions remain drafts" in page, None)
    check("the live account page links brand and optimization reports",
          "Brand report" in live and "loadOptimizationSummary" in live, None)
    check("stale scans and stale score reads cannot replace a newer account",
          "generation !== scanGeneration" in page and "generation !== loadGeneration" in live, None)

    import modules.ads_builder.app as ads_app
    real_status = google_ads.connection_status
    google_ads.connection_status = lambda store=None: {"deploy_ready": True, "blocks": []}
    try:
        with ads_app.app.test_client() as client:
            rendered = client.get("/optimization")
    finally:
        google_ads.connection_status = real_status
    check("the connected Optimization route renders its working screen",
          rendered.status_code == 200 and b"Accounts needing work" in rendered.data
          and b"runAction" in rendered.data, rendered.status_code)

    drafts = optimization.ai_drafts({"account_name": "Client Ads", "campaigns": [{"id": "20", "name": "Efficient"}],
                                      "winning_terms": [{"text": "emergency roof repair", "campaign_id": "20", "ad_group_id": "201"}]})
    check("AI has a measured-data fallback and never needs to auto-mutate",
          drafts["ai_used"] is False and drafts["keywords"][0]["text"] == "emergency roof repair", drafts)

    print(f"\n{passed} passed, {failed} failed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run())
