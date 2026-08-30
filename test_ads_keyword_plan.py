"""Smart 1 Ads — the measured CPC, the access tier, and the deploy preflight.

    python3 test_ads_keyword_plan.py

Google is stubbed throughout. Nothing here makes an outbound call, and none of
it needs a developer token — which is the point: the behaviour worth asserting
is what the module does when Google says no, and that is unreachable from a
deployment where Google says yes.

What it is guarding against, in order of how expensive each would be to find
in front of a client:

* **A top-of-page bid printed as a cost per click.** Google returns both, they
  differ by a lot in a competitive sector, and only one of them is what you
  pay. Relabelling the first as the second inflates every estimate this tool
  produces and looks like a better number than the benchmark it replaced.
* **A refusal read as an error.** A new developer token is granted Explorer
  access, which excludes the keyword planning services entirely. The call comes
  back DEVELOPER_TOKEN_NOT_APPROVED — a perfectly healthy token, refused for
  the tier. Reported as a bad key it sends somebody to rotate a credential that
  was fine.
* **A CPC measured somewhere the campaign does not run.** An area Google cannot
  place must be named, never widened to the state it sits in and never dropped.
* **A page contradicting itself.** A measured headline CPC over tiers still
  costed at the sector rate shows a client two different campaigns.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1ads_kp_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "test.db")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.makedirs(os.environ["HUB_DATA_DIR"], exist_ok=True)
os.environ.pop("GOOGLE_ADS_ACCESS_LEVEL", None)

from modules.ads_builder import (api_readiness, campaign_ai,  # noqa: E402
                                 google_ads, keyword_plan, spec)
from modules.ads_builder.google_ads import GoogleAdsError  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f" — {extra}" if extra and not ok else ""))


def truthy(name, value, extra=""):
    check(name, bool(value), extra or repr(value))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


CAMPAIGN = {
    "businessName": "Northside Roofing Co",
    "sectorKey": "homeservices",
    "monthlyBudget": 6500,
    "targetAreas": [
        {"name": "Carmel showroom", "type": "City/ZIP + Radius",
         "origin": "Carmel, IN", "radius": 15},
        {"name": "Statewide", "type": "Statewide", "state": "Indiana"},
    ],
    "adGroups": [
        {"name": "Emergency", "keywords": ["[emergency roof repair]",
                                           '"roof leak repair"', "storm damage roof"]},
        {"name": "Replacement", "keywords": ["[roof replacement cost]", '"new roof quote"']},
    ],
    "budgetTiers": {"tiers": [
        {"key": "good", "label": "Good", "monthly": 3000, "estimatedClicks": 146},
        {"key": "better", "label": "Better", "monthly": 6500,
         "estimatedClicks": 317, "recommended": True, "buys": "The full keyword set."},
        {"key": "best", "label": "Best", "monthly": 12000, "estimatedClicks": 585},
    ]},
}


# --------------------------------------------------------------- stubbing
class Recorder:
    """Stands in for google_ads.request, answering by path."""

    def __init__(self, handlers):
        self.handlers, self.calls = handlers, []

    def __call__(self, method, path, body=None, **kw):
        self.calls.append((method, path, body))
        for needle, answer in self.handlers.items():
            if needle in path:
                if isinstance(answer, Exception):
                    raise answer
                return answer(body) if callable(answer) else answer
        return {}


def geo_answer(name="Carmel", cid="1015202"):
    return {"geoTargetConstantSuggestions": [
        {"geoTargetConstant": {"resourceName": f"geoTargetConstants/{cid}",
                               "name": name, "targetType": "City",
                               "status": "ENABLED"}}]}


def ideas_answer(rows):
    return {"results": [
        {"text": text, "keywordIdeaMetrics": {
            "avgMonthlySearches": searches,
            "competition": "HIGH",
            "lowTopOfPageBidMicros": str(int(low * 1_000_000)) if low else None,
            "highTopOfPageBidMicros": str(int(high * 1_000_000)) if high else None,
        }} for text, searches, low, high in rows]}


def forecast_answer(avg_cpc, clicks=210, cost=2600):
    return {"campaignForecastMetrics": {
        "averageCpcMicros": str(int(avg_cpc * 1_000_000)),
        "clicks": clicks, "impressions": 8400,
        "costMicros": str(int(cost * 1_000_000)), "clickThroughRate": 0.025}}


def with_google(handlers, fn):
    original = google_ads.request
    google_ads.request = Recorder(handlers)
    try:
        return fn(google_ads.request)
    finally:
        google_ads.request = original


def refusal(code="DEVELOPER_TOKEN_NOT_APPROVED"):
    return GoogleAdsError("The developer token is not approved.", status=403, code=code)


# =============================================================== the vocabulary
section("A top-of-page bid and a cost per click are different claims")

check("all three sources are named in one place",
      set(spec.CPC_SOURCES) == {"benchmark", "top_of_page_bid", "forecast"})
check("keyword_plan reads that vocabulary rather than keeping a copy",
      keyword_plan.CPC_SOURCES is spec.CPC_SOURCES)

for key, row in spec.CPC_SOURCES.items():
    truthy(f"'{key}' says what it measures", row["long"] and len(row["long"]) > 40)

bid_text = spec.CPC_SOURCES["top_of_page_bid"]["long"].lower()
check("the bid caveat says it is a bid, not a cost per click",
      "bid, not a cost per click" in bid_text, bid_text)
check("...and says what you pay is usually lower", "usually lower" in bid_text)
check("the forecast caveat says it is a forecast",
      "forecast" in spec.CPC_SOURCES["forecast"]["long"].lower())
check("the benchmark caveat is the one that was always there",
      spec.CPC_SOURCES["benchmark"]["long"] == spec.CPC_NOTE_LONG)


section("An unmeasured campaign describes itself honestly")

prov = spec.cpc_provenance({})
check("provenance always answers", prov["source"] == "benchmark")
check("...and does not claim to be measured", prov["measured"] is False)
check("...and carries no number of its own", prov["value"] is None)
check("...with the industry-estimate caveat", prov["short"] == spec.CPC_NOTE)

prov = spec.cpc_provenance({"cpcMeasured": {"measured": False, "reason": "tier"}})
check("a measurement that failed is still the benchmark", prov["source"] == "benchmark")

prov = spec.cpc_provenance({"cpcMeasured": {"measured": True, "source": "nonsense",
                                            "cpc": 9.99}})
check("an unrecognised source falls back rather than printing itself",
      prov["source"] == "benchmark" and prov["value"] is None)


# ================================================================== geography
section("An area Google cannot place is named, never widened")

def _geo_partial(_req):
    return keyword_plan.geo_targets([
        {"name": "Carmel", "type": "City/ZIP + Radius", "origin": "Carmel, IN"},
        {"name": "Nowheresville", "type": "City/ZIP + Radius", "origin": "Nowheresville"},
    ])


calls = {"geoTargetConstants:suggest": lambda body:
         geo_answer() if "Carmel" in str(body) else {"geoTargetConstantSuggestions": []}}
geo = with_google(calls, _geo_partial)

check("the area that resolved is kept", len(geo["targets"]) == 1)
check("the area that did not is named, identifiably",
      len(geo["unresolved"]) == 1 and "Nowheresville" in geo["unresolved"][0], geo)
check("...and the result says it is incomplete", geo["complete"] is False)
check("a failed area is NOT replaced by the whole United States",
      keyword_plan.US_GEO_TARGET not in [t["id"] for t in geo["targets"]], geo["targets"])

national = with_google(calls, lambda _r: keyword_plan.geo_targets(
    [{"name": "USA", "type": "National"}]))
check("a genuinely national campaign does get the US target",
      [t["id"] for t in national["targets"]] == [keyword_plan.US_GEO_TARGET])
check("...and reports nothing unresolved", national["complete"] is True)

empty = with_google(calls, lambda _r: keyword_plan.geo_targets([]))
check("a campaign with no areas at all falls back to the US",
      [t["id"] for t in empty["targets"]] == [keyword_plan.US_GEO_TARGET])


# ================================================================== keywords
section("Keywords are sent as terms, not as match-type syntax")

seeds = keyword_plan.campaign_keywords(CAMPAIGN)
check("brackets and quotes are stripped from the seeds",
      all("[" not in k and '"' not in k for k in seeds), seeds)
check("every keyword in the campaign is offered", len(seeds) == 5, seeds)
check("...de-duplicated and lower-cased", seeds == [k.lower() for k in seeds])

many = {"adGroups": [{"keywords": [f"term {i}" for i in range(60)]}]}
capped = keyword_plan._seeds(keyword_plan.campaign_keywords(many))
check("the seed list is capped at Google's limit",
      len(capped["seeds"]) == keyword_plan.MAX_SEEDS)
check("...and what was left out is counted, not dropped in silence",
      capped["left_out"] == 60 - keyword_plan.MAX_SEEDS and capped["total"] == 60, capped)


section("A keyword Google has no data for is not a keyword worth nothing")

result = with_google(
    {"generateKeywordIdeas": ideas_answer([
        ("emergency roof repair", 1900, 8.40, 21.60),
        ("roof leak repair", 880, 6.10, 15.20),
        ("storm damage roof", None, None, None),
    ])},
    lambda _r: keyword_plan.keyword_ideas("1234567890", [
        "emergency roof repair", "roof leak repair", "storm damage roof"]))

check("keywords with data are returned", len(result["keywords"]) == 2, result)
check("a keyword with none is named, not carried at zero",
      result["no_data"] == ["storm damage roof"], result["no_data"])
check("bids come back in dollars, not micros",
      result["keywords"][0]["bid_high"] == 21.60, result["keywords"][0])
check("volume is an integer", result["keywords"][0]["monthly_searches"] == 1900)
check("results sort by volume, campaign keywords first",
      result["keywords"][0]["keyword"] == "emergency roof repair")


# ============================================================== the access tier
section("A refusal for the access tier is not a bad key")

def _refused(_req):
    try:
        keyword_plan.keyword_ideas("1234567890", ["roofer"])
    except keyword_plan.PlanningUnavailable as exc:
        return exc
    return None


exc = with_google({"generateKeywordIdeas": refusal()}, _refused)
truthy("the refusal raises PlanningUnavailable, not a generic error", exc)
check("...naming the tier that would fix it", exc.tier_needed == "basic")
check("...and saying so in words a rep can act on",
      "basic access" in exc.message.lower(), exc.message)
check("...and explaining that Explorer is what a new token gets",
      "explorer" in exc.message.lower(), exc.message)
check("...without telling anyone to rotate the key",
      "rotate" not in exc.message.lower() and "invalid" not in exc.message.lower())

check("the four tiers are named, in the order Google grants them",
      [k for k, _, _ in keyword_plan.ACCESS_TIERS]
      == ["test", "explorer", "basic", "standard"])
check("Explorer's note says planning is excluded",
      "not" in keyword_plan.TIER_NOTES["explorer"].lower()
      and "planning" in keyword_plan.TIER_NOTES["explorer"].lower())
check("Basic's note says it is the first tier that can measure",
      "measured cpc is possible" in keyword_plan.TIER_NOTES["basic"].lower())


section("A tier nobody recorded is unknown, which is not broken")

t = api_readiness.declared_tier(None)
check("with nothing recorded the tier is empty", t["tier"] == "")
check("...labelled 'Not recorded' rather than guessed", t["label"] == "Not recorded")
check("...and says Google does not publish it",
      "does not publish" in t["note"].lower(), t["note"])
check("...naming its source as none", t["source"] == "none")


class FakeStore:
    def __init__(self, settings=None):
        self.settings = dict(settings or {})

    def get_setting(self, key):
        return self.settings.get(key, "")

    def set_setting(self, key, value):
        self.settings[key] = value


st = FakeStore({api_readiness.TIER_SETTING: "basic"})
check("a tier typed on Settings is read", api_readiness.declared_tier(st)["tier"] == "basic")
check("...but is labelled a claim, not an observation",
      api_readiness.declared_tier(st)["measured"] is False)

api_readiness.record_probe(st, {"available": True, "state": "ok", "detail": "answered"})
observed = api_readiness.tier(st)
check("a successful probe outranks the typed value",
      observed["source"] == "observed" and observed["measured"] is True, observed)
check("...and concludes Basic access or better", observed["tier"] == "basic")

st2 = FakeStore({api_readiness.TIER_SETTING: "standard"})
api_readiness.record_probe(st2, {"available": False, "state": "tier_too_low",
                                 "tier_needed": "basic", "detail": "refused"})
observed = api_readiness.tier(st2)
check("a refusal outranks a typed 'standard' too",
      observed["tier"] == "explorer" and observed["measured"] is True, observed)

st3 = FakeStore()
api_readiness.record_probe(st3, {"available": False, "state": "unreachable",
                                 "detail": "timeout"})
observed = api_readiness.tier(st3)
check("an unreachable Google concludes nothing about the tier",
      observed["measured"] is False and observed["tier"] == "", observed)
check("...but keeps why, so the page is not silent",
      observed["probe_state"] == "unreachable", observed)


# ==================================================================== measure
section("measure() never returns a number without saying what it is")

handlers = {
    "geoTargetConstants:suggest": geo_answer(),
    "generateKeywordIdeas": ideas_answer([("emergency roof repair", 1900, 8.40, 21.60)]),
    "generateKeywordForecastMetrics": forecast_answer(12.35),
}
m = with_google(handlers, lambda _r: keyword_plan.measure("1234567890", CAMPAIGN))

check("a forecast is preferred over a bid range", m["source"] == "forecast", m.get("source"))
check("...and it is the forecast's average CPC", m["cpc"] == 12.35)
check("the bid range is still carried beside it",
      m["top_of_page_bid"]["high"] == 21.60, m.get("top_of_page_bid"))
check("the forecast names the bid it assumed", m["forecast"]["bid_assumed"] > 0)
check("...and says where that bid came from", "budget" in m["forecast"]["bid_source"]
      or "sector" in m["forecast"]["bid_source"] or "max CPC" in m["forecast"]["bid_source"],
      m["forecast"]["bid_source"])
check("...and the window it covers", m["forecast"]["period"]["days"] == keyword_plan.FORECAST_DAYS)

no_forecast = with_google(
    {**handlers, "generateKeywordForecastMetrics": refusal("INTERNAL_ERROR")},
    lambda _r: keyword_plan.measure("1234567890", CAMPAIGN))
check("with no forecast it falls back to the bid range",
      no_forecast["source"] == "top_of_page_bid", no_forecast.get("source"))
check("...and the number is the bid, labelled as the bid",
      no_forecast["cpc"] == 21.60
      and "bid, not a cost per click" in keyword_plan.summary_line(no_forecast))

tier_blocked = with_google(
    {"geoTargetConstants:suggest": geo_answer(), "generateKeywordIdeas": refusal()},
    lambda _r: keyword_plan.measure("1234567890", CAMPAIGN))
check("a tier refusal leaves the estimate on the benchmark",
      tier_blocked["measured"] is False and tier_blocked["source"] == "benchmark")
check("...and says which tier would fix it", tier_blocked["tier_needed"] == "basic")
check("...rather than reporting zero", "cpc" not in tier_blocked, tier_blocked.keys())

nothing = with_google(
    {"geoTargetConstants:suggest": geo_answer(),
     "generateKeywordIdeas": {"results": []},
     "generateKeywordForecastMetrics": forecast_answer(0, clicks=0)},
    lambda _r: keyword_plan.measure("1234567890", CAMPAIGN))
check("no data at all reads as not measured, never zero",
      nothing["measured"] is False and "not measured, not zero" in nothing["reason"],
      nothing.get("reason"))


# ============================================================ the arithmetic
section("A measured CPC changes the numbers, and the words with them")

bench = campaign_ai.analyse_budget(6500, "homeservices")
check("with no measurement the sector mid-point is used", bench["cpc_used"] == 20.5)
check("...and the caveat is the industry estimate", bench["cpc_source"] == "benchmark")
check("...with the exact shared string", bench["cpc_note"] == spec.CPC_NOTE)

measured = campaign_ai.analyse_budget(6500, "homeservices", cpc=12.35,
                                      cpc_source="forecast")
check("a measured CPC is the one the clicks are computed from",
      measured["estimated_clicks"] == round(6500 / 12.35), measured["estimated_clicks"])
check("...and it buys more clicks than the benchmark said",
      measured["estimated_clicks"] > bench["estimated_clicks"])
check("...and the caveat changes with it", measured["cpc_source"] == "forecast")
check("...to the forecast's own words",
      measured["cpc_note_long"] == spec.CPC_SOURCES["forecast"]["long"])
check("the pessimistic case still runs on the sector ceiling",
      measured["worst_case_clicks"] == bench["worst_case_clicks"])

zeroed = campaign_ai.analyse_budget(6500, "homeservices", cpc=0, cpc_source="forecast")
check("a zero CPC cannot smuggle in a 'measured' label",
      zeroed["cpc_source"] == "benchmark", zeroed["cpc_source"])
junk = campaign_ai.analyse_budget(6500, "homeservices", cpc="nonsense",
                                  cpc_source="forecast")
check("...nor can an unparseable one", junk["cpc_source"] == "benchmark")

check("measured_cpc() is empty when nothing was measured",
      campaign_ai.measured_cpc(CAMPAIGN) == {})
check("...and carries both value and source when it was",
      campaign_ai.measured_cpc({"cpcMeasured": {"measured": True, "cpc": 12.35,
                                                "source": "forecast"}})
      == {"cpc": 12.35, "cpc_source": "forecast"})


section("The tiers cannot disagree with the headline")

priced = {**CAMPAIGN, "cpcMeasured": {"measured": True, "cpc": 12.35,
                                      "source": "forecast", "at": "now"}}
tiers = campaign_ai.retier(priced)
check("every tier is recomputed on the measured CPC",
      [t["estimatedClicks"] for t in tiers["tiers"]]
      == [round(t["monthly"] / 12.35) for t in CAMPAIGN["budgetTiers"]["tiers"]],
      [t["estimatedClicks"] for t in tiers["tiers"]])
check("the tiers say which CPC they were costed at", tiers["cpcSource"] == "forecast")
check("...and carry that source's caveat",
      tiers["cpcNote"] == spec.CPC_SOURCES["forecast"]["long"])
check("the wording a rep edited survives the recompute",
      tiers["tiers"][1]["buys"] == "The full keyword set.")
check("...and so does which tier is recommended",
      tiers["tiers"][1]["recommended"] is True)
check("re-tiering an unmeasured campaign leaves the benchmark in place",
      campaign_ai.retier(CAMPAIGN)["cpcSource"] == "benchmark")
check("re-tiering a campaign with no tiers does not invent any",
      campaign_ai.retier({"sectorKey": "general"}).get("tiers") in (None, []))

low = campaign_ai.retier({**priced, "cpcMeasured": {"measured": True, "cpc": 140.0,
                                                    "source": "forecast"}})
check("a measured CPC high enough to break a tier says so",
      low["tiers"][0]["belowFloor"] is True, low["tiers"][0])


# =================================================================== preflight
section("The preflight names every blocker at once")

pre = api_readiness.preflight(store=None, customer_id=None, proposal=None)
keys = [c["key"] for c in pre["checks"]]
check("it checks credentials, auth, tier and account", keys[:4]
      == ["credentials", "oauth", "tier", "account"], keys)
check("with nothing configured it is not ready", pre["ready"] is False)
check("an unrecorded tier is 'not measured', never a failure",
      next(c for c in pre["checks"] if c["key"] == "tier")["state"] == "not_measured")
check("an account that could not be checked is likewise",
      next(c for c in pre["checks"] if c["key"] == "account")["state"] == "not_measured")
check("the summary counts blocked and unknown separately",
      "blocking" in pre["note"] and "not measured" in pre["note"], pre["note"])

proposal = {
    "status": "DRAFT",
    "campaign": {"estimate": {}, "editLog": [
        {"what": "Budget cut to $1,500", "material": True, "rechecked": False}]},
    "review": {},
}
pre = api_readiness.preflight(store=None, customer_id=None, proposal=proposal)
by_key = {c["key"]: c for c in pre["checks"]}
check("an unapproved estimate blocks", by_key["estimate"]["state"] == "blocked")
check("an unreviewed material edit blocks", by_key["recheck"]["state"] == "blocked")
check("...naming the edit, not just its count",
      "Budget cut" in by_key["recheck"]["detail"], by_key["recheck"]["detail"])
check("a DRAFT proposal blocks", by_key["status"]["state"] == "blocked")
check("every blocker carries a fix", all(c["fix"] for c in pre["checks"]
                                         if c["state"] == "blocked"))

ready = {
    "status": "APPROVED",
    "campaign": {"estimate": {"approved_at": "now", "approved_by": "todd", "superseded": False},
                 "editLog": [{"what": "typo", "material": False, "rechecked": True}]},
    "review": {"outcome": "approved", "reviewer": "Dana"},
}
pre = api_readiness.preflight(store=None, customer_id=None, proposal=ready)
by_key = {c["key"]: c for c in pre["checks"]}
check("an approved, re-checked, approved-status proposal passes its three rungs",
      all(by_key[k]["state"] == "ok" for k in ("estimate", "recheck", "status")),
      {k: by_key[k]["state"] for k in ("estimate", "recheck", "status")})
check("the client's own answer is shown", by_key["client"]["state"] == "ok")

superseded = {**ready, "campaign": {**ready["campaign"],
                                    "estimate": {"approved_at": "then", "superseded": True}}}
pre = api_readiness.preflight(store=None, customer_id=None, proposal=superseded)
check("an approval an edit superseded does not count",
      next(c for c in pre["checks"] if c["key"] == "estimate")["state"] == "blocked")

deployed = {**ready, "status": "DEPLOYED"}
pre = api_readiness.preflight(store=None, customer_id=None, proposal=deployed)
check("a proposal already deployed is blocked from deploying again",
      next(c for c in pre["checks"] if c["key"] == "status")["state"] == "blocked")

no_answer = {**ready, "review": {}}
pre = api_readiness.preflight(store=None, customer_id=None, proposal=no_answer)
check("no client answer is 'not measured', not a blocker",
      next(c for c in pre["checks"] if c["key"] == "client")["state"] == "not_measured")
check("...so it does not stop a deploy a rep agreed by phone",
      all(c["state"] != "blocked" for c in pre["checks"] if c["key"] == "client"))


section("A client account that has not accepted the link reads as that")

original = google_ads.list_accessible_customers
status_original = google_ads.connection_status
google_ads.connection_status = lambda store=None: {
    "configured": True, "connected": True, "missing": [],
    "refresh_token_source": "environment"}
try:
    google_ads.list_accessible_customers = lambda store=None: ["9999999999"]
    pre = api_readiness.preflight(store=None, customer_id="1234567890")
    account = next(c for c in pre["checks"] if c["key"] == "account")
    check("an account we cannot reach is blocked", account["state"] == "blocked")
    check("...named with its formatted id", "123-456-7890" in account["detail"], account)
    check("...and the fix is the link invitation, not the key",
          "invitation" in account["fix"].lower() or "accepted" in account["fix"].lower(),
          account["fix"])

    google_ads.list_accessible_customers = lambda store=None: []
    pre = api_readiness.preflight(store=None, customer_id="1234567890")
    account = next(c for c in pre["checks"] if c["key"] == "account")
    check("no reachable accounts at all is its own message",
          "no Google Ads accounts" in account["detail"], account["detail"])

    def _boom(store=None):
        raise GoogleAdsError("connection reset", status=503)

    google_ads.list_accessible_customers = _boom
    pre = api_readiness.preflight(store=None, customer_id="1234567890")
    account = next(c for c in pre["checks"] if c["key"] == "account")
    check("an unreachable Google is not evidence of a bad key",
          account["state"] == "not_measured", account)
    check("...and says so", "not evidence" in account["fix"], account["fix"])

    google_ads.list_accessible_customers = lambda store=None: ["1234567890", "9999999999"]
    pre = api_readiness.preflight(store=None, customer_id="1234567890")
    account = next(c for c in pre["checks"] if c["key"] == "account")
    check("a reachable account passes", account["state"] == "ok", account)
finally:
    google_ads.list_accessible_customers = original
    google_ads.connection_status = status_original


# ================================================================== templates
section("Both renderings of the estimate carry the right caveat")

doc = (ROOT / "modules/ads_builder/templates/_estimate_doc.html").read_text()
check("the document reads provenance rather than hard-coding the caveat",
      "cpc.short" in doc and "cpc.long" in doc)
check("...and never prints CPC_NOTE beside the headline number",
      "CPC_NOTE }})</span></div>\n      </div>" not in doc)
check("an unresolved area is named on the document itself",
      "areas_unresolved" in doc, "the client cannot see a gap nothing mentions")
check("the tier row is labelled by whichever CPC costed it",
      "cpcSource" in doc)

for name in ("ads_estimate.html", "ads_client_proposal.html"):
    body = (ROOT / "modules/ads_builder/templates" / name).read_text()
    check(f"{name} still includes the one shared document",
          '{% include "_estimate_doc.html" %}' in body)


print("\n" + "-" * 60)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print(f"  FAILED: {name}")
sys.exit(1 if FAIL else 0)
