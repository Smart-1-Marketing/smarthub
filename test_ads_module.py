"""Smart 1 Ads module — test harness.

Mounts the module exactly the way the Hub's wsgi.py does (DispatcherMiddleware
+ an AuthGuard that injects ``s1hub.user``), then exercises every route.

    python3 test_ads_module.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from wsgiref.simple_server import WSGIServer, make_server

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Isolated database and data directory per run, house style: nothing here may
# reach /var/data, the real database, or the repo's own ./data — client_link
# files a proposal onto a client record, and that is a write.
TMP = tempfile.mkdtemp(prefix="s1ads_test_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "test.db")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.makedirs(os.environ["HUB_DATA_DIR"], exist_ok=True)
os.environ.pop("CLOUDINARY_URL", None)
os.environ.setdefault("GOOGLE_ADS_REDIRECT_URI", "http://localhost:8099/tools/ads/oauth/callback")

import requests  # noqa: E402
from werkzeug.middleware.dispatcher import DispatcherMiddleware  # noqa: E402

from modules.ads_builder import app as ads  # noqa: E402
from modules.ads_builder import store  # noqa: E402

PORT = 8099
MOUNT = "/tools/ads"
BASE = f"http://127.0.0.1:{PORT}{MOUNT}"

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f" — {extra}" if extra and not ok else ""))


class StubAuthGuard:
    """Stands in for the Hub's AuthGuard: sets the signed-in user, like wsgi.py."""

    def __init__(self, app, user="todd@smart1marketing.com"):
        self.app, self.user = app, user

    def __call__(self, environ, start_response):
        environ["s1hub.user"] = self.user
        return self.app(environ, start_response)


def hub_root(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/html")])
    return [b"<html><body>Hub shell</body></html>"]


application = DispatcherMiddleware(hub_root, {MOUNT: StubAuthGuard(ads.app)})


class QuietServer(WSGIServer):
    def handle_error(self, request, client_address):
        pass


SAMPLE = {
    "businessName": "Northside Roofing Co",
    "websiteUrl": "https://northsideroofing.example.com/roof-repair",
    "monthlyBudget": 6500,
    "sector": "Home Services / Trades",
    "sectorKey": "homeservices",
    "strategySummary": "Two tightly themed groups split emergency storm repair from planned "
                       "replacement, because the two buyers behave nothing alike.",
    "costEstimation": {
        "estimatedMonthlyCost": 6500, "avgCPC": 11.4, "estimatedMonthlyClicks": 570,
        "estimatedConversionRate": 0.08, "estimatedConversions": 46, "estimatedCPA": 141,
        "budgetViability": {"status": "HEALTHY",
                            "advice": "$6,500/mo supports roughly 570 clicks at an $11.40 CPC."},
    },
    "landingPageAnalysis": {
        "ctaReadiness": "Medium",
        "messageMatch": "The hero speaks to replacement, but most of this traffic wants urgent repair.",
        "recommendations": ["Add a click-to-call button above the fold on mobile.",
                            "Put the 24/7 storm response promise in the hero."],
    },
    "adGroups": [
        {"name": "Emergency Roof Repair", "theme": "Storm damage, leaks, urgent callouts", "avgCPC": 13.2,
         "keywords": ["[emergency roof repair]", '"roof leak repair"', "[24 hour roofer]",
                      "storm damage roof repair", '"roof repair near me"', "urgent roofer"],
         "ads": {"headlines": ["24/7 Emergency Roofers", "Roof Leak? We Come Today",
                               "Free Storm Damage Check", "Licensed & Insured Crew"],
                 "descriptions": ["Storm damage or an active leak? Our crew is out the same day.",
                                  "Free inspection, clear pricing and a 10 year workmanship warranty."]}},
        {"name": "Roof Replacement Quotes", "theme": "Planned full replacement research", "avgCPC": 9.8,
         "keywords": ["[roof replacement cost]", '"new roof quote"', "[roof replacement near me]",
                      "cost to replace a roof"],
         "ads": {"headlines": ["Free Replacement Quote", "Financing From $99/Month", "Local Since 1998"],
                 "descriptions": ["Compare shingle, metal and tile options with an honest breakdown.",
                                  "No pressure quotes from a family run local crew."]}},
    ],
    "adAssets": {
        "sitelinks": [
            {"title": "Free Roof Inspection", "desc1": "Booked within 24 hours",
             "desc2": "No obligation quote", "url": "https://northsideroofing.example.com/inspection"},
            {"title": "Storm Damage Help", "desc1": "Insurance claim support",
             "desc2": "We deal with adjusters", "url": "https://northsideroofing.example.com/storm"},
            {"title": "Financing Options", "desc1": "", "desc2": "",
             "url": "https://northsideroofing.example.com/financing"},
        ],
        "callouts": ["Licensed & Insured", "10 Year Warranty", "Same Day Callouts", "Free Estimates"],
        "structuredSnippets": {"header": "Services",
                               "values": ["Roof Repair", "Roof Replacement", "Storm Damage", "Gutters"]},
    },
    "negativeKeywordVault": {
        "freeCheap": ["free roof", "cheap roofer", "diy roof repair"],
        "jobsCareers": ["roofer jobs", "roofing salary"],
        "educational": ["how to repair a roof", "roofing course"],
        "irrelevant": ["roof rack", "roof box", "sunroof"],
    },
}


def run():
    server = make_server("127.0.0.1", PORT, application, server_class=QuietServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.4)
    print(f"\nSmart 1 Ads module test  (mounted at {MOUNT})\n")

    g = requests.get
    p = requests.post

    # ---------------------------------------------------------- unit level
    from modules.ads_builder import campaign_ai, google_ads

    check("[unit] $500/mo in legal reads CRITICAL",
          campaign_ai.analyse_budget(500, "legal")["status"] == "CRITICAL")
    check("[unit] $25k/mo in home services reads HEALTHY",
          campaign_ai.analyse_budget(25000, "homeservices")["status"] == "HEALTHY")
    check("[unit] $1.5k/mo in B2B SaaS warns",
          campaign_ai.analyse_budget(1500, "b2bsaas")["status"] == "WARN")
    check("[unit] an unknown sector falls back to general",
          campaign_ai.analyse_budget(5000, "nonsense")["sector_key"] == "general")

    check("[unit] [brackets] parse as EXACT",
          google_ads.parse_keyword("[roof repair]")["match_type"] == "EXACT")
    check("[unit] \"quotes\" parse as PHRASE",
          google_ads.parse_keyword('"roof repair"')["match_type"] == "PHRASE")
    check("[unit] a (broad) tag parses as BROAD",
          google_ads.parse_keyword("roof repair (broad)")["match_type"] == "BROAD")
    check("[unit] a bare term defaults to PHRASE",
          google_ads.parse_keyword("roof repair")["match_type"] == "PHRASE")
    check("[unit] normalise_url adds https",
          google_ads.normalise_url("example.com/a") == "https://example.com/a")
    check("[unit] normalise_url rejects junk",
          google_ads.normalise_url("not a url at all") == "")
    check("[unit] customer ids format with dashes",
          google_ads.format_customer_id("1234567890") == "123-456-7890")

    # ------------------------------------------------- deploy payload shape
    # Build the mutate batch without touching the network, and assert the
    # things Google rejects campaigns for.
    import modules.ads_builder.google_ads as ga
    captured = {}

    def fake_request(method, path, body=None, **kw):
        captured["path"] = path
        captured["body"] = body
        return {"mutateOperationResponses": [
            {"campaignResult": {"resourceName": "customers/1234567890/campaigns/999"}}]}

    real_request = ga.request
    ga.request = fake_request
    try:
        result = ga.deploy_proposal("123-456-7890", SAMPLE, campaign_name="Test Campaign")
    finally:
        ga.request = real_request

    ops = captured["body"]["mutateOperations"]
    kinds = [list(o.keys())[0] for o in ops]

    check("[deploy] posts to googleAds:mutate", captured["path"].endswith("/googleAds:mutate"))
    check("[deploy] partialFailure is off, so it is all-or-nothing",
          captured["body"]["partialFailure"] is False)
    check("[deploy] creates exactly one budget", kinds.count("campaignBudgetOperation") == 1)
    check("[deploy] creates exactly one campaign", kinds.count("campaignOperation") == 1)
    check("[deploy] creates one ad group per themed group",
          kinds.count("adGroupOperation") == 2)
    check("[deploy] creates a responsive search ad per ad group",
          kinds.count("adGroupAdOperation") == 2)

    campaign = next(o["campaignOperation"]["create"] for o in ops if "campaignOperation" in o)
    check("[deploy] the campaign is PAUSED", campaign["status"] == "PAUSED")
    check("[deploy] the campaign is a Search campaign",
          campaign["advertisingChannelType"] == "SEARCH")
    check("[deploy] search partners are off by default",
          campaign["networkSettings"]["targetSearchNetwork"] is False)
    check("[deploy] display network is off",
          campaign["networkSettings"]["targetContentNetwork"] is False)

    groups = [o["adGroupOperation"]["create"] for o in ops if "adGroupOperation" in o]
    check("[deploy] ad groups are PAUSED", all(x["status"] == "PAUSED" for x in groups))

    ads_ops = [o["adGroupAdOperation"]["create"] for o in ops if "adGroupAdOperation" in o]
    check("[deploy] ads are PAUSED", all(a["status"] == "PAUSED" for a in ads_ops))
    check("[deploy] every RSA has >= 3 headlines",
          all(len(a["ad"]["responsiveSearchAd"]["headlines"]) >= 3 for a in ads_ops))
    check("[deploy] every RSA has >= 2 descriptions",
          all(len(a["ad"]["responsiveSearchAd"]["descriptions"]) >= 2 for a in ads_ops))
    check("[deploy] no headline exceeds 30 characters",
          all(len(h["text"]) <= 30 for a in ads_ops for h in a["ad"]["responsiveSearchAd"]["headlines"]))
    check("[deploy] no description exceeds 90 characters",
          all(len(d["text"]) <= 90 for a in ads_ops for d in a["ad"]["responsiveSearchAd"]["descriptions"]))

    kws = [o["adGroupCriterionOperation"]["create"]["keyword"] for o in ops
           if "adGroupCriterionOperation" in o]
    check("[deploy] keyword match types survive the round trip",
          {"EXACT", "PHRASE", "BROAD"} >= {k["matchType"] for k in kws}
          and any(k["matchType"] == "EXACT" for k in kws))
    check("[deploy] bracket syntax is stripped from the keyword text",
          all("[" not in k["text"] and '"' not in k["text"] for k in kws))

    negs = [o["campaignCriterionOperation"]["create"] for o in ops if "campaignCriterionOperation" in o]
    check("[deploy] all 10 vault negatives are attached", len(negs) == 10, f"got {len(negs)}")
    check("[deploy] negatives are negative and broad",
          all(n["negative"] and n["keyword"]["matchType"] == "BROAD" for n in negs))

    sitelinks = [o["assetOperation"]["create"] for o in ops
                 if "assetOperation" in o and "sitelinkAsset" in o["assetOperation"]["create"]]
    check("[deploy] all three sitelinks are created", len(sitelinks) == 3)
    check("[deploy] sitelink descriptions are paired or absent, never half",
          all(("description1" in s["sitelinkAsset"]) == ("description2" in s["sitelinkAsset"])
              for s in sitelinks))
    check("[deploy] the sitelink with no descriptions has neither line",
          not any(k.startswith("description") for k in sitelinks[2]["sitelinkAsset"]))

    budget = next(o["campaignBudgetOperation"]["create"] for o in ops if "campaignBudgetOperation" in o)
    check("[deploy] the monthly budget becomes a sane daily amount in micros",
          budget["amountMicros"] == str(int(round(6500 / 30.4, 2) * 1_000_000)),
          budget["amountMicros"])
    check("[deploy] the budget is not shared with other campaigns",
          budget["explicitlyShared"] is False)
    check("[deploy] returns the new campaign id", result["campaign_id"] == "999")
    check("[deploy] reports the keyword count", result["keyword_count"] == 10, str(result["keyword_count"]))

    # dry run must send validateOnly and write nothing
    ga.request = fake_request
    try:
        dry = ga.deploy_proposal("1234567890", SAMPLE, validate_only=True)
    finally:
        ga.request = real_request
    check("[deploy] dry run sets validateOnly", captured["body"].get("validateOnly") is True)
    check("[deploy] dry run reports it wrote nothing", dry["validated"] is True)

    for bad, why in (
        ({**SAMPLE, "websiteUrl": "nope"}, "a broken URL"),
        ({**SAMPLE, "monthlyBudget": 0}, "a zero budget"),
        ({**SAMPLE, "adGroups": []}, "no ad groups"),
    ):
        try:
            ga.deploy_proposal("1234567890", bad)
            check(f"[deploy] refuses {why}", False, "no exception raised")
        except ga.GoogleAdsError:
            check(f"[deploy] refuses {why}", True)

    # ------------------------------------- Ads Editor export (no dev token)
    # The whole point of this path is that it needs no Google API access at
    # all, so nothing in this block may reach for a credential.
    import csv as _csv
    import io as _io
    from modules.ads_builder import export

    csv_text = export.editor_csv(SAMPLE)
    rows = list(_csv.DictReader(_io.StringIO(csv_text)))
    camp_rows = [r_ for r_ in rows if r_["Campaign Type"]]
    group_rows = [r_ for r_ in rows if r_["Max CPC"]]
    kw_rows = [r_ for r_ in rows if r_["Keyword"] and not r_["Criterion Type"].startswith("Campaign Negative")]
    neg_rows = [r_ for r_ in rows if r_["Criterion Type"].startswith("Campaign Negative")]
    ad_rows = [r_ for r_ in rows if r_["Ad Type"]]

    check("[export] one campaign row", len(camp_rows) == 1)
    check("[export] the campaign is Paused, like the API path",
          camp_rows[0]["Status"] == "Paused")
    check("[export] it is a Search campaign", camp_rows[0]["Campaign Type"] == "Search")
    check("[export] search partners are off by default",
          "partners" not in camp_rows[0]["Networks"].lower())
    check("[export] the monthly budget becomes a daily one",
          camp_rows[0]["Campaign Daily Budget"] == "213.82",
          camp_rows[0]["Campaign Daily Budget"])
    check("[export] one row per ad group", len(group_rows) == 2)
    check("[export] ad groups are Paused", all(r_["Status"] == "Paused" for r_ in group_rows))
    check("[export] every keyword survives", len(kw_rows) == 10, str(len(kw_rows)))
    check("[export] bracket syntax is stripped from the keyword text",
          all(not k["Keyword"].startswith("[") for k in kw_rows))
    check("[export] match types become Editor criterion types",
          {k["Criterion Type"] for k in kw_rows} == {"Exact", "Phrase"},
          str({k["Criterion Type"] for k in kw_rows}))
    check("[export] an exact keyword stays exact",
          any(k["Keyword"] == "emergency roof repair" and k["Criterion Type"] == "Exact"
              for k in kw_rows))
    check("[export] all 10 vault negatives are attached", len(neg_rows) == 10, str(len(neg_rows)))
    check("[export] negatives are campaign level and broad",
          all(n["Criterion Type"] == "Campaign Negative Broad" for n in neg_rows))
    check("[export] one responsive search ad per ad group", len(ad_rows) == 2)
    check("[export] ads are Paused", all(a["Status"] == "Paused" for a in ad_rows))
    check("[export] every ad carries the final URL",
          all(a["Final URL"] == SAMPLE["websiteUrl"] for a in ad_rows))
    check("[export] every ad has >= 3 headlines",
          all(len([1 for i in range(1, 16) if a[f"Headline {i}"]]) >= 3 for a in ad_rows))
    check("[export] every ad has >= 2 descriptions",
          all(len([1 for i in range(1, 5) if a[f"Description {i}"]]) >= 2 for a in ad_rows))
    check("[export] no headline exceeds 30 characters",
          all(len(a[f"Headline {i}"]) <= 30 for a in ad_rows for i in range(1, 16)))
    check("[export] no description exceeds 90 characters",
          all(len(a[f"Description {i}"]) <= 90 for a in ad_rows for i in range(1, 5)))

    # The name must not move between downloads: a timestamped one imports as a
    # second campaign on the second attempt, with nothing saying so.
    check("[export] the campaign name is stable across downloads",
          export.default_campaign_name(SAMPLE) == export.default_campaign_name(SAMPLE))
    check("[export] and carries the client name",
          "Northside Roofing Co" in export.default_campaign_name(SAMPLE))

    # Absent data reads as absent, never as zero.
    no_budget = {**SAMPLE, "monthlyBudget": 0}
    blank = list(_csv.DictReader(_io.StringIO(export.editor_csv(no_budget))))
    check("[export] a missing budget leaves the cell blank rather than inventing one",
          [r_ for r_ in blank if r_["Campaign Type"]][0]["Campaign Daily Budget"] == "")
    check("[export] ...and says so before anybody imports it",
          any("budget" in x.lower() for x in export.problems(no_budget)))
    check("[export] a broken URL is reported, not silently dropped",
          any("final url" in x.lower() for x in export.problems({**SAMPLE, "websiteUrl": "nope"})))
    check("[export] a healthy proposal reports no problems", export.problems(SAMPLE) == [])

    sheet = export.build_sheet(SAMPLE)
    check("[sheet] says what the CSV does not carry", "does NOT carry" in sheet)
    check("[sheet] lists every sitelink",
          all(sl["title"] in sheet for sl in SAMPLE["adAssets"]["sitelinks"]))
    check("[sheet] lists the callouts", "10 Year Warranty" in sheet)
    check("[sheet] names the structured snippet header",
          SAMPLE["adAssets"]["structuredSnippets"]["header"] in sheet)
    check("[sheet] tells you how to import", "Import from file" in sheet)
    check("[sheet] a missing budget is named rather than shown as $0.00",
          "fill it in" in export.build_sheet(no_budget))

    from modules.ads_builder import client_link

    # ----------------------------------------- the Hub activity log mirror
    # audit.log()'s first positional is the MODULE. This mirror used to pass
    # "ads.<action>" as it and no type_ at all, which is a TypeError the
    # surrounding except swallowed — so nothing this module logged ever reached
    # the Hub, and nothing reached Client 360, while its own Activity page
    # looked complete.
    seen = []

    class _AuditStub:
        @staticmethod
        def log(module, type_, actor=None, **extra):
            seen.append({"module": module, "type": type_, "actor": actor, **extra})

    real_audit = store.hub_audit
    store.hub_audit = _AuditStub
    try:
        store.log_event("GENERATION_SUCCESS", "todd@smart1marketing.com",
                        client="Northside Roofing Co", proposal="prop_mirror")
    finally:
        store.hub_audit = real_audit

    check("[audit] the event reaches the Hub activity log at all", len(seen) == 1)
    check("[audit] filed under the module name Client 360 knows",
          seen and seen[0]["module"] == "ads_builder", seen and seen[0]["module"])
    check("[audit] with the action as the type, not smuggled into the module",
          seen and seen[0]["type"] == "generation_success")
    check("[audit] and the client, which is what puts it on their 360 record",
          seen and seen[0]["client"] == "Northside Roofing Co")
    from hub.client_brand import WORK_KINDS
    check("[audit] that module name is one Client 360 actually reads",
          "ads_builder" in WORK_KINDS)

    # The mirror reports itself, so nothing can tell a rep their work was filed
    # without asking. That is the half that was missing when it broke.
    class _BrokenAudit:
        @staticmethod
        def log(*a, **k):
            raise RuntimeError("activity log is down")

    store.hub_audit = _BrokenAudit
    try:
        broken = store.log_event("GENERATION_SUCCESS", "todd@smart1marketing.com",
                                 client="Northside Roofing Co")
    finally:
        store.hub_audit = real_audit
    check("[audit] a failed mirror is reported, not swallowed",
          broken["mirrored"] is False and "down" in broken["error"])
    ok_report = store.log_event("PROPOSAL_COMMENT", "todd@smart1marketing.com")
    check("[audit] and a working one says so", isinstance(ok_report, dict))
    check("[audit] a proposal whose work log failed does not claim it was filed",
          client_link.attach({"id": "p1", "client_name": "X", "campaign": {}},
                             MOUNT, work=broken)["work"]["ok"] is False)

    # ------------------------------------------------- client join, no Hub
    lead = client_link.create_lead("Northside Roofing Co", "https://x.example.com",
                                   {"name": "Dana"}, SAMPLE)
    check("[client] a lead with no email and no phone is refused, not faked",
          lead["created"] is False and "email or a phone" in lead["note"])

    # Filing the proposal onto the client record, and the rule that keeps one
    # campaign to one row however many times it is re-filed.
    fake_proposal = {"id": "prop_test1", "client_name": "Northside Roofing Co",
                     "campaign": SAMPLE}
    filed = client_link.file_proposal(fake_proposal, MOUNT, actor="todd@smart1marketing.com")
    check("[client] the proposal is filed on the client record", filed["ok"] is True, filed["note"])
    from hub import proposals as hub_proposals
    on_record = hub_proposals.list_proposals("Northside Roofing Co")
    check("[client] ...and appears in the client's proposals", len(on_record) == 1)
    check("[client] ...as a link to the live page, not a stale copy",
          on_record[0]["kind"] == "link" and "prop_test1" in on_record[0]["url"])
    check("[client] ...carrying the monthly value", on_record[0]["value"] == 6500)
    check("[client] ...and naming the tool that built it",
          on_record[0]["source"] == "ads_builder")

    client_link.file_proposal(fake_proposal, MOUNT, actor="todd@smart1marketing.com")
    again = hub_proposals.list_proposals("Northside Roofing Co")
    check("[client] re-filing one campaign updates its row rather than adding another",
          len(again) == 1, f"{len(again)} rows")
    check("[client] ...keeping the same quote number",
          again[0]["quote_number"] == on_record[0]["quote_number"])

    hits = client_link.search("north")
    check("[client] an unreadable client list says so rather than returning nothing",
          hits["available"] is False or isinstance(hits["clients"], list))
    check("[client] ...and never claims a match it could not look up",
          hits["clients"] == [] if not hits["available"] else True)

    # ---------------------------------------------------------- HTTP routes
    r = g(f"{BASE}/healthz", timeout=10)
    check("GET /healthz is ok", r.status_code == 200 and r.json()["ok"])

    r = g(f"{BASE}/api/version", timeout=10)
    check("GET /api/version reports 6.1ads", r.json().get("version") == "6.1ads", r.text[:120])

    r = g(f"{BASE}/api/status", timeout=10)
    check("GET /api/status describes Google", r.json()["google"]["connected"] is False)
    check("GET /api/status marks Bing as phase 2", r.json()["bing"]["configured"] is False)

    r = g(f"{BASE}/api/budget-check?budget=400&sector=legal", timeout=10)
    check("GET /api/budget-check flags a thin budget", r.json()["status"] == "CRITICAL")

    r = g(f"{BASE}/api/accounts", timeout=10)
    check("accounts fail cleanly with no credentials",
          r.status_code >= 400 and "GOOGLE_ADS" in r.json().get("error", ""), r.text[:160])

    r = g(f"{BASE}/api/campaigns", timeout=10)
    check("campaigns without customer_id is a 400", r.status_code == 400)

    # proposals lifecycle, seeded directly (no OpenAI call needed)
    proposal = store.create_proposal("Northside Roofing Co", SAMPLE, created_by="todd@smart1marketing.com")
    pid = proposal["id"]
    check("store computes the ad group count", proposal["ad_group_count"] == 2)
    check("store computes the keyword count", proposal["keyword_count"] == 10)
    check("store computes the negative count", proposal["negative_count"] == 10)

    r = g(f"{BASE}/api/proposals", timeout=10)
    check("GET /api/proposals lists it", any(x["id"] == pid for x in r.json()["proposals"]))

    r = p(f"{BASE}/api/proposals/{pid}/status", json={"status": "NONSENSE"}, timeout=10)
    check("an invalid status is rejected", r.status_code == 400)

    r = p(f"{BASE}/api/proposals/{pid}/comments", json={"text": "Cut ad group 2."}, timeout=10)
    check("a comment is attached", len(r.json()["proposal"]["comments"]) == 1)
    check("the comment is stamped with the Hub user",
          r.json()["proposal"]["comments"][0]["author"] == "todd@smart1marketing.com")

    r = p(f"{BASE}/api/deploy", json={"proposal_id": pid, "customer_id": "1234567890"}, timeout=10)
    check("a draft proposal cannot deploy", r.status_code == 400 and r.json()["code"] == "NOT_APPROVED")

    r = p(f"{BASE}/api/deploy", json={"proposal_id": pid}, timeout=10)
    check("deploy without an account is rejected", r.status_code == 400)

    r = p(f"{BASE}/api/deploy", json={"proposal_id": "prop_missing", "customer_id": "1"}, timeout=10)
    check("deploy of a missing proposal 404s", r.status_code == 404)

    r = p(f"{BASE}/api/proposals/{pid}/status", json={"status": "APPROVED"}, timeout=10)
    check("a proposal can be approved", r.json()["proposal"]["status"] == "APPROVED")

    r = p(f"{BASE}/api/deploy", json={"proposal_id": pid, "customer_id": "1234567890"}, timeout=10)
    check("an approved proposal still needs a live connection",
          r.status_code >= 400 and "GOOGLE_ADS" in r.json().get("error", ""), r.text[:160])

    r = g(f"{BASE}/api/bing/campaigns", timeout=10)
    check("Bing endpoints answer 501", r.status_code == 501)

    # pages render
    for path, needle in (
        ("/", "Search existing clients"),
        ("/campaigns", "Live campaign data needs the Google Ads API"),
        ("/approvals", "Northside Roofing Co"),
        (f"/proposal/{pid}", "Negative keyword vault"),
        (f"/proposal/{pid}/client", "Paid Search Estimate"),
        ("/activity", "Activity"),
        ("/settings", "Environment reference"),
    ):
        r = g(BASE + path, timeout=10)
        check(f"GET {path} renders", r.status_code == 200 and needle in r.text,
              f"status {r.status_code}")

    # The front door is the generator, and it works with no Google anything.
    r = g(f"{BASE}/", timeout=10)
    check("the first screen is the campaign generator",
          "Monthly budget" in r.text and "Date range" not in r.text)
    check("the first screen does not ask for an API key",
          "customApiKey" not in r.text and "key override" not in r.text.lower())
    check("the first screen mentions no missing Google credential",
          "GOOGLE_ADS_DEVELOPER_TOKEN" not in r.text)
    nav = r.text[r.text.index("<nav"):r.text.index("</nav>")]
    check("the generator tab comes before live campaigns in the menu",
          nav.index("Campaign generator") < nav.index("Live campaigns"))
    check("...and live campaigns comes after the approval hub",
          nav.index("Approval hub") < nav.index("Live campaigns"))

    r = g(f"{BASE}/generator", timeout=10, allow_redirects=False)
    check("the old generator URL still lands", r.status_code in (301, 302)
          and r.headers.get("Location", "").rstrip("/").endswith("/tools/ads"),
          f"{r.status_code} {r.headers.get('Location')}")

    # The launch options are the last thing on the proposal, after approval.
    r = g(f"{BASE}/proposal/{pid}", timeout=10)
    check("the launch card sits below review and approval",
          r.text.index("Review and approval") < r.text.index(">Launch"))
    check("the Ads Editor route is offered whether or not Google is connected",
          "Ads Editor CSV" in r.text)
    check("...and the API route names the credential it is waiting on",
          "GOOGLE_ADS_DEVELOPER_TOKEN" in r.text)
    # The join is recorded inside the campaign JSON — never in a new column,
    # which create_all() would not add to the live table.
    store.record_client_link(pid, {
        "client": "Northside Roofing Co", "website": SAMPLE["websiteUrl"],
        "known_client": {"known": False, "client": "", "matched_on": "", "why": ""},
        "filed": {"ok": True, "quote_number": "U-10200", "note": "Filed on the client record."},
        "work": {"ok": True, "note": "Recorded on the client's work log."},
        "lead": {"ok": True, "created": True, "note": "Created in Smart 1 Suite."},
        "all_ok": True,
    })
    check("the client join survives a round trip through the campaign JSON",
          store.get_proposal(pid)["campaign"]["clientLink"]["result"]["filed"]["ok"] is True)
    check("...and the campaign it was recorded against is untouched",
          len(store.get_proposal(pid)["campaign"]["adGroups"]) == 2)
    r = g(f"{BASE}/proposal/{pid}", timeout=10)
    check("the proposal page reports what the join wrote",
          "Client record" in r.text and "U-10200" in r.text)
    check("...naming the Suite lead separately from the filing",
          "Created in Smart 1 Suite." in r.text)

    # ----------------------------------------------- the handoff over HTTP
    r = g(f"{BASE}/proposal/{pid}/export/campaign.csv", timeout=10)
    check("the Ads Editor CSV downloads", r.status_code == 200)
    check("...as an attachment named for the client",
          "attachment" in r.headers.get("Content-Disposition", "")
          and "northside-roofing-co" in r.headers.get("Content-Disposition", ""),
          r.headers.get("Content-Disposition", ""))
    check("...and is a CSV", r.headers["Content-Type"].startswith("text/csv"))
    check("...carrying the keywords", "emergency roof repair" in r.text)

    r = g(f"{BASE}/proposal/{pid}/export/build-sheet.txt", timeout=10)
    check("the build sheet downloads", r.status_code == 200 and "SITELINKS" in r.text)

    r = g(f"{BASE}/api/proposals/{pid}/export", timeout=10)
    d = r.json()
    check("the export summary says what goes by hand", d["by_hand"]["sitelinks"] == 3)
    check("...and names no problems on a healthy proposal", d["problems"] == [])

    r = g(f"{BASE}/proposal/prop_nope/export/campaign.csv", timeout=10)
    check("exporting a missing proposal 404s", r.status_code == 404)

    # ------------------------------------------- what works without Google
    caps = g(f"{BASE}/api/status", timeout=10).json()["capabilities"]
    check("the Ads Editor export needs no Google credentials at all",
          caps["export_to_ads_editor"]["ready"] is True)
    check("approval needs none either", caps["approve"]["ready"] is True)
    check("deploying does, and names them",
          caps["deploy_via_api"]["ready"] is False
          and "GOOGLE_ADS_DEVELOPER_TOKEN" in caps["deploy_via_api"]["needs"])
    check("reading live campaigns is gated the same way",
          caps["read_live_campaigns"]["ready"] is False)

    r = g(f"{BASE}/api/clients?q=north", timeout=10)
    check("the client lookup answers", r.status_code == 200 and "clients" in r.json())
    check("...and says when the Hub client list could not be read",
          r.json()["available"] is True or bool(r.json()["note"]))

    r = g(f"{BASE}/proposal/{pid}/client", timeout=10)
    check("the client proposal hides bracket match types", "[emergency roof repair]" not in r.text)
    check("the client proposal hides quoted match types", '"roof leak repair"' not in r.text)
    check("the client proposal still shows the keyword", "emergency roof repair" in r.text)
    check("the client proposal hides the internal match-type labels",
          ">exact<" not in r.text and ">phrase<" not in r.text)

    r = g(f"{BASE}/proposal/{pid}", timeout=10)
    check("the internal view does show match types", ">exact<" in r.text)
    check("the internal view links back to the Hub tools mount", '/tools/ads/approvals' in r.text)

    r = g(f"{BASE}/proposal/prop_nope", timeout=10)
    check("an unknown proposal 404s with a page", r.status_code == 404)

    r = g(f"{BASE}/connect", timeout=10, allow_redirects=False)
    check("connect refuses to start without credentials", r.status_code == 400)

    # audit
    events = store.list_events()
    check("the activity log recorded the status change",
          any(e["action"] == "PROPOSAL_STATUS_CHANGE" for e in events))
    check("the activity log recorded the comment",
          any(e["action"] == "PROPOSAL_COMMENT" for e in events))
    check("activity rows carry the Hub user",
          all(e["actor"] for e in events))

    r = requests.delete(f"{BASE}/api/proposals/{pid}", timeout=10)
    check("a proposal can be deleted", r.json().get("ok") is True)

    # the Hub shell outside the mount is untouched
    r = g(f"http://127.0.0.1:{PORT}/", timeout=10)
    check("the Hub shell outside the mount is untouched", "Hub shell" in r.text)

    gallery_background_source()
    basepath_shim()

    server.shutdown()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed\n")
    if FAIL:
        for f in FAIL:
            print("  FAILED:", f)
    return 1 if FAIL else 0


# ---------------------------------------------------------------------------
# The base-path shim must not rewrite the Hub's own chrome
# ---------------------------------------------------------------------------
#
# src/basepath.ts prefixes every root-relative href/src/action so this app
# works under a mount. But the Hub injects its sidebar, its feedback tab and
# five /hub-*.js scripts into the SAME page after this service has produced
# it, and those URLs belong to the Hub. Prefixing them sent every sidebar link
# back into this service, which answered
#
#     {"error": "No route for GET /sales/landing"}
#
# and looked, from the browser, exactly like the Hub had lost the page. The
# help layer was dead on this page for the same reason and nobody noticed,
# because a missing bubble looks like a page that simply has none.
#
# Driven from Python through node, the way test_proposal_spec.py checks the
# wizard's copy of the classifier: the shim is browser code, so the real
# parser is the only one worth asking.

BASEPATH_FIXTURE = r"""
function El(tag, attrs, parent){
  const e = {tag, attrs: {...attrs}, parent: parent||null, children: [],
    getAttribute:(a)=>e.attrs[a]===undefined?null:e.attrs[a],
    setAttribute:(a,v)=>{e.attrs[a]=v;},
    matches:(sel)=>selMatch(e, sel),
    closest:(sel)=>{let n=e; while(n){ if(selMatch(n,sel)) return n; n=n.parent;} return null;},
    querySelectorAll:(sel)=>all(e).filter(n=>n!==e&&selMatch(n,sel))};
  if(parent) parent.children.push(e);
  return e;
}
function all(root){ let out=[root]; root.children.forEach(c=>out=out.concat(all(c))); return out; }
function selMatch(el, sel){
  return sel.split(',').some(part=>{
    part = part.trim();
    let m = part.match(/^\[(\w+)\^="(.*)"\]$/);
    if(m){ const v = el.getAttribute(m[1]); return typeof v==='string' && v.startsWith(m[2]); }
    m = part.match(/^\[class\*="(.*)"\]$/);
    if(m){ const v = el.getAttribute('class'); return typeof v==='string' && v.includes(m[1]); }
    return false;
  });
}
const body = El('body', {});
const sb = El('nav', {class:'s1hub-sb'}, body);
const hub = {
  landing: El('a', {class:'s1hub-item', href:'/sales/landing'}, sb),
  c360:    El('a', {class:'s1hub-item', href:'/client360'}, sb),
  feed:    El('a', {class:'s1hub-feed', href:'/feedback'}, body),
  script:  El('script', {src:'/hub-help.js'}, body),
  css:     El('link', {href:'/hub-help.css'}, body)};
const app = {
  link: El('a', {href:'/projects'}, body),
  img:  El('img', {src:'/files/x.png'}, body),
  form: El('form', {action:'/api/render'}, body)};
const fetches=[];
global.window = { fetch:(i,o)=>{fetches.push(i); return Promise.resolve();} };
global.XMLHttpRequest = function(){};
global.XMLHttpRequest.prototype = { open:function(){} };
global.MutationObserver = function(){ return { observe(){} }; };
global.document = { body, documentElement: body, readyState:'complete',
                    addEventListener(){} };
__SHIM__
window.fetch('/api/render');
window.fetch('/hub-help.js');
console.log(JSON.stringify({
  hub: Object.fromEntries(Object.entries(hub).map(([k,e])=>
        [k, e.getAttribute('href') || e.getAttribute('src')])),
  app: Object.fromEntries(Object.entries(app).map(([k,e])=>
        [k, e.getAttribute('href') || e.getAttribute('src') || e.getAttribute('action')])),
  fetches}));
"""


def gallery_background_source():
    """What the background chooser is allowed to offer from a client gallery.

    The filtering is the whole feature. A finished display ad in that list
    puts last month's headline and the logo behind this month's ad, and the
    brief for the chooser is explicit that a logo must never be inside the
    picture -- so both are excluded here rather than being caught by whoever
    is looking at the proof.
    """
    print("\nthe client gallery as a background source")
    print("-" * 60)

    from hub import ad_builder_link
    from modules.image_picker import models as ip

    ip.init_db()
    if ip.DB_BOOT_ERROR:
        check("the image gallery tables exist", False, ip.DB_BOOT_ERROR)
        return

    name = "Northside Roofing"
    db = ip.session()
    try:
        gallery = ip.PickerClient(name=name, slug=ip.slugify(name),
                                  industry_key="homeservices",
                                  share_token=ip.new_token())
        db.add(gallery)
        db.commit()

        def add(pid, kind, **kw):
            row = ip.SavedImage(
                client_id=gallery.id, provider="test", provider_image_id=pid,
                cloudinary_public_id=pid,
                cloudinary_url=f"https://res.cloudinary.com/x/{pid}.jpg",
                collection_kind=kind, **kw)
            db.add(row)
            return row

        add("crew", "upload", width=1600, height=900)
        add("van", "upload", width=1200, height=800)
        add("thumb", "upload", width=120, height=90)         # too small
        add("brochure", "upload", width=1200, height=1600,
            resource_type="raw")                             # a PDF
        add("last-months-ad", ad_builder_link.GALLERY_KIND, width=970, height=250)
        add("their-logo", ad_builder_link.LOGO_KIND, width=600, height=200)
        add("unmeasured", "upload")                          # no dimensions
        db.commit()
    finally:
        db.close()

    res = ad_builder_link.client_gallery(name)
    offered = {i["public_id"] for i in res["images"]}

    check("a client photograph is offered", "crew" in offered, str(offered))
    check("a finished display ad is not",
          "last-months-ad" not in offered,
          "an ad behind an ad prints the headline twice")
    check("nor is the saved logo", "their-logo" not in offered)
    check("nor is a PDF filed in the same gallery", "brochure" not in offered)
    check("a picture too small to be a background is left out",
          "thumb" not in offered)
    # Absent data reads as "not measured", not as zero -- filtering on
    # (width or 0) < 300 would drop every row filed before dimensions were
    # recorded, which is the oldest and often best material.
    check("an unmeasured picture is still offered", "unmeasured" in offered)
    check("...and says so rather than showing a size",
          next((i["label"] for i in res["images"]
                if i["public_id"] == "unmeasured"), "") == "size not recorded")
    check("the label carries the real dimensions",
          next((i["label"] for i in res["images"]
                if i["public_id"] == "crew"), "") == "1600\u00d7900")

    # The loop the upload source depends on: a picture filed from the editor
    # has to come back out of the gallery source, or the next person uploads
    # the same photo again.
    saved = ad_builder_link.save_to_gallery(
        client_name=name, url="https://res.cloudinary.com/x/just-uploaded.jpg",
        public_id="just-uploaded", filename="premises.jpg",
        width=1800, height=1200, actor="todd@smart1marketing.com")
    check("an uploaded background files into the gallery", saved.get("ok"),
          str(saved))
    check("...and the gallery source then offers it",
          "just-uploaded" in {i["public_id"] for i in
                              ad_builder_link.client_gallery(name)["images"]})
    check("a picture with no https URL is refused in words",
          ad_builder_link.save_to_gallery(client_name=name, url="ftp://x/y.jpg")
          .get("error", "").startswith("A gallery picture"))
    check("and one with no client is refused rather than guessed at",
          not ad_builder_link.save_to_gallery(
              client_name="", url="https://res.cloudinary.com/x/z.jpg")["ok"])

    empty = ad_builder_link.client_gallery("Nobody We Know Ltd")
    check("an unknown client returns no images and says why",
          empty["ok"] and not empty["images"] and "gallery" in empty["note"],
          str(empty))
    check("no client at all is answered, not crashed",
          ad_builder_link.client_gallery("")["images"] == [])


def basepath_shim():
    import json as _json
    import re as _re
    import subprocess as _sp
    import tempfile as _tf

    print("\nthe base-path shim leaves the Hub's chrome alone")
    print("-" * 60)

    src = Path("modules/ad_builder/src/basepath.ts").read_text()
    m = _re.search(r"const shim = `(.*?)`;\n", src, _re.S)
    if not m:
        check("the shim can be read out of basepath.ts", False,
              "the `const shim = ` template moved")
        return
    shim = m.group(1).replace("${JSON.stringify(base)}",
                              _json.dumps("/tools/display-ads"))
    shim = shim.replace("<script>", "", 1)
    shim = shim[:shim.rindex("</script>")]

    with _tf.TemporaryDirectory() as tmp:
        script = Path(tmp, "t.js")
        script.write_text(BASEPATH_FIXTURE.replace("__SHIM__", shim))
        proc = _sp.run(["node", str(script)], capture_output=True, text=True,
                       timeout=60)
    if proc.returncode != 0:
        check("the shim runs under node", False,
              (proc.stderr or proc.stdout).strip()[:300])
        return
    out = _json.loads(proc.stdout.strip().splitlines()[-1])

    # The Hub's own URLs, untouched.
    check("a sidebar link keeps its Hub path",
          out["hub"]["landing"] == "/sales/landing", out["hub"]["landing"])
    check("...and so does Client 360",
          out["hub"]["c360"] == "/client360", out["hub"]["c360"])
    check("the feedback tab is not rewritten",
          out["hub"]["feed"] == "/feedback", out["hub"]["feed"])
    check("nor is the Hub's help script",
          out["hub"]["script"] == "/hub-help.js", out["hub"]["script"])
    check("nor its stylesheet",
          out["hub"]["css"] == "/hub-help.css", out["hub"]["css"])

    # ...and this app's URLs still get the mount prefix, which is the whole
    # reason the shim exists.
    check("a page link is still prefixed",
          out["app"]["link"] == "/tools/display-ads/projects", out["app"]["link"])
    check("so is an image",
          out["app"]["img"] == "/tools/display-ads/files/x.png", out["app"]["img"])
    check("so is a form action",
          out["app"]["form"] == "/tools/display-ads/api/render", out["app"]["form"])
    check("patched fetch prefixes this app's calls",
          out["fetches"][0] == "/tools/display-ads/api/render", out["fetches"][0])
    check("but leaves a Hub asset alone",
          out["fetches"][1] == "/hub-help.js", out["fetches"][1])


if __name__ == "__main__":
    sys.exit(run())
