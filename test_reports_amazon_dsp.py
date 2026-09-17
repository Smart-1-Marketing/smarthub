"""The native Amazon DSP pull: five claims, one report per advertiser, and
where no credential ever goes.

    python3 test_reports_amazon_dsp.py

No pytest, no new dependencies, a throwaway reports database and a stand-in
for Amazon: ``amazon_ads._http`` is replaced whole, the Login with Amazon
token endpoint and the pre-signed download are stubbed at ``requests``, so
nothing here reaches the network and every request the module makes is
recorded.

What it holds:

  * unconfigured names the variables and not connected is a sentence
    pointing at Settings -- each a clean pull that returns rather than
    raises and stamps no watermark over the provider's; a quoted value and
    a placeholder are refused the way hub/config.py refuses them;
  * the consent URL asks Login with Amazon for the campaign-management
    scope and comes back to <PUBLIC_BASE_URL>/tools/ads/oauth/amazon/callback,
    which is the string hub/oauth_redirects.py prints for the LWA console;
  * the five claims are told apart: ``invalid_grant`` is a consent revoked
    or from another application and never a bad secret; a 403 on the
    advertiser list is the API application not being approved for this
    entity; an entity that is not in the profile list is a consent given by
    somebody who does not administer it. Rung 5, write access, is never
    probed and never claimed;
  * ONE report per advertiser: submitted, polled inside a wall-clock budget
    (pending is not failed, touches no watermark, and the next tick asks for
    the same reportId rather than paying for a second report), downloaded
    from a pre-signed URL with NO Authorization header on it, and parsed
    defensively -- a row with no day or no order id is counted, not
    invented;
  * the order is the campaign, and the report's finer grain is folded onto
    the order-day rather than upserted over itself — line items ride in
    extras by name; spend is
    totalCost with no divisor; purchases and detail-page views land in
    extras under their own names and never into conversions; completes
    are carried only where video served;
  * one advertiser refused is that advertiser's problem and the rest of the
    entity still lands; every call is recorded under amazon_ads by endpoint
    family, and no credential reaches a row, a result, a status line, a
    watermark or a recorded call;
  * a report left pending is carried with the moment it was first seen and
    given up on past a ceiling -- a fresh one is asked for and both the run
    and the index say so, rather than re-polling a dead id every night;
  * the check page answers with an error on it rather than raising, whatever
    fails under it;
  * the reconcile reads the month back as the same report over the whole
    window, labeled a re-read; the scheduler pulls it in the same loop;
  * Smart 1 Ads: the settings card offers Connect only when configured and
    says who has to press it, the callback keeps the token and shows it once
    against the pin variable, disconnect clears it, /api/status carries the
    connection, and campaign management still answers 501.
"""
import gzip
import json
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_amazon_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-amazon-test"
os.environ["PUBLIC_BASE_URL"] = "https://smart1.agency"
AMAZON_VARS = ("AMAZON_ADS_CLIENT_ID", "AMAZON_ADS_CLIENT_SECRET", "AMAZON_ADS_REFRESH_TOKEN",
               "AMAZON_DSP_ENTITY_ID", "AMAZON_DSP_ENTITY_PROFILE_ID", "AMAZON_ADS_REGION",
               "AMAZON_ADS_REDIRECT_URI")
for k in AMAZON_VARS + ("STACKADAPT_API_KEY", "AUDIOGO_API_KEY", "TTD_API_TOKEN",
                        "BING_AD_CLIENT_ID", "GOOGLE_ADS_REFRESH_TOKEN"):
    os.environ.pop(k, None)

_passed = _failed = 0


def check(label, got, want=True, note=None):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}"
              + (f"\n          note: {note!r}" if note is not None else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


import requests                                                      # noqa: E402
from modules.ads_builder import amazon_ads as amz, store as ads_store  # noqa: E402
from modules.reports import amazon_dsp, store                        # noqa: E402
_reports_testdb.reset(store)

SECRET = "amzn1.oa2-cs.v1-sekret-9f8e7d6c"
REFRESH = "Atzr|IwEBIrefresh-token-abcdef0123456789"
ACCESS = "Atza|IwEBIaccess-token-0123456789abcdef"


def _secrets_in(text) -> list:
    s = json.dumps(text, default=str) if not isinstance(text, str) else text
    return [n for n, v in (("secret", SECRET), ("refresh", REFRESH), ("access", ACCESS))
            if v in s]


class _Resp:
    def __init__(self, status, payload=None, content=None):
        self.status_code = status
        self._payload = payload
        self.content = content if content is not None else json.dumps(payload or {}).encode()
        self.text = self.content.decode("utf-8", errors="replace") if isinstance(self.content, bytes) else ""
        self.ok = 200 <= status < 300

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


# ------------------------------------------------------------ unconfigured
section("Unconfigured is a sentence naming the variables, not an exception")

st = amazon_dsp.status()
check("not configured", st["configured"], False)
check("...the line names all three", st["line"],
      "Amazon DSP: not configured: AMAZON_ADS_CLIENT_ID, AMAZON_ADS_CLIENT_SECRET, "
      "AMAZON_DSP_ENTITY_ID unset")
check("...and missing lists them", st["missing"], list(amz.REQUIRED))
res = amazon_dsp.pull()
check("a pull returns rather than raising", res["ok"], False)
check("...saying so", res["error"].startswith("not configured"))
check("...and wrote no watermark", store.sync_status().get("amazon_dsp"), None)
check("...and claimed no last pull, because nothing was read",
      "last pull" not in amazon_dsp.status()["line"])

cs = amz.connection_status(ads_store)
check("connection_status names what each missing variable is for",
      [b["name"] for b in cs["blocks"]], list(amz.REQUIRED))
check("...with a reason on each", all(b["why"] for b in cs["blocks"]))

os.environ.update({"AMAZON_ADS_CLIENT_ID": '"amzn1.application-oa2-client.quoted"',
                   "AMAZON_ADS_CLIENT_SECRET": SECRET,
                   "AMAZON_DSP_ENTITY_ID": "ENTITY1"})
check("a value Render stored with its quotes is read without them",
      amz.load_config().client_id, "amzn1.application-oa2-client.quoted")
os.environ["AMAZON_ADS_CLIENT_ID"] = "API_KEY"
check("a placeholder copied out of env.example is refused: set, and not a credential",
      "AMAZON_ADS_CLIENT_ID" in amz.connection_status(ads_store)["missing"])
os.environ["AMAZON_ADS_CLIENT_ID"] = "amzn1.application-oa2-client.s1hub"

os.environ["AMAZON_ADS_REGION"] = "APAC"
check("a region this API does not have is refused by name, before anything is sent",
      ("APAC" in amz.connection_status(ads_store)["region_problem"],
       amz.connection_status(ads_store)["configured"]), (True, False))
check("...and the pull says so rather than picking a host",
      "AMAZON_ADS_REGION" in amazon_dsp.pull()["error"])
os.environ["AMAZON_ADS_REGION"] = "NA"

cs = amz.connection_status(ads_store)
check("credentials set: configured, and nobody has consented yet",
      (cs["configured"], cs["connected"], cs["deploy_ready"]), (True, False, False))
res = amazon_dsp.pull()
check("a pull not connected is clean", (res["ok"], res["error"]), (False, amazon_dsp.NOT_CONNECTED))
check("...pointing at the press, and at who has to make it",
      "Connect Amazon Ads" in res["error"] and "entity admin" in res["error"])
check("...and wrote no watermark", store.sync_status().get("amazon_dsp"), None)
from hub import diagnostics                                          # noqa: E402
check("/diagnostics reads it as a warning naming the press",
      diagnostics.check_amazon_dsp().state, "warn")


# --------------------------------------------------------------- the consent
section("The consent URL, and the callback the panel prints")

from urllib.parse import parse_qs, urlsplit                          # noqa: E402
url = amz.build_auth_url("state-abc")
parts = urlsplit(url)
q = {k: v[0] for k, v in parse_qs(parts.query).items()}
check("Login with Amazon's authorize endpoint",
      f"{parts.scheme}://{parts.netloc}{parts.path}", amz.LWA_AUTHORIZE_URL)
check("...asking for the campaign-management scope, as a code",
      (q["scope"], q["response_type"]), (amz.LWA_SCOPE, "code"))
check("...coming back to the mount's own callback under PUBLIC_BASE_URL",
      q["redirect_uri"], "https://smart1.agency/tools/ads/oauth/amazon/callback")
check("...carrying the state, and the client id but never the secret",
      (q["state"], q["client_id"] == os.environ["AMAZON_ADS_CLIENT_ID"], SECRET in url),
      ("state-abc", True, False))

from hub import oauth_redirects as orx                               # noqa: E402
row = next(r for r in orx.rows("https://smart1.agency/") if r["key"] == "amazon_ads")
check("hub/oauth_redirects.py lists the flow as a ninth, built from PUBLIC_BASE_URL",
      (row["source"], row["client_var"], row["state"]),
      ("PUBLIC_BASE_URL", "AMAZON_ADS_CLIENT_ID", "ok"))
check("...and prints exactly the string the code sends", row["uris"], [amz.redirect_uri()])
check("...naming the Login with Amazon console", "Login with Amazon" in row["console"])
os.environ["PUBLIC_BASE_URL"] = "https://smart1.agency/tools/ads/oauth/callback"
check("a PUBLIC_BASE_URL carrying a path does not put it in the middle of the callback",
      amz.redirect_uri(), "https://smart1.agency/tools/ads/oauth/amazon/callback")
os.environ["PUBLIC_BASE_URL"] = ""
check("no PUBLIC_BASE_URL is a callback with nowhere to come back to, named",
      (amz.redirect_uri(), "PUBLIC_BASE_URL" in amz.connection_status(ads_store)["missing"]),
      ("", True))
os.environ["PUBLIC_BASE_URL"] = "https://smart1.agency"


# ----------------------------------------------------------------- the token
section("The refresh token: environment first, settings table second, never out")

TOKEN_CALLS = []


def fake_token_post(url, data=None, timeout=None, **kw):
    TOKEN_CALLS.append({"url": url, "data": dict(data or {})})
    if (data or {}).get("refresh_token") == "dead":
        return _Resp(400, {"error": "invalid_grant",
                           "error_description": "The request has an invalid grant parameter"})
    return _Resp(200, {"access_token": ACCESS, "refresh_token": REFRESH,
                       "expires_in": 3600, "token_type": "bearer"})


_real_post = requests.post
requests.post = fake_token_post
amz.forget_tokens()

try:
    amz.access_token()
    check("no token anywhere raises", False)
except amz.AmazonAuthError as exc:
    check("no token anywhere is a refusal naming the variable and the press",
          "AMAZON_ADS_REFRESH_TOKEN" in str(exc) and "Connect" in str(exc))

ads_store.set_setting("amazon_refresh_token", REFRESH)
check("the settings-table token is exchanged at the LWA token endpoint",
      amz.access_token(), ACCESS)
check("...with the refresh grant and the client secret",
      (TOKEN_CALLS[-1]["url"], TOKEN_CALLS[-1]["data"]["grant_type"],
       TOKEN_CALLS[-1]["data"]["refresh_token"]),
      (amz.LWA_TOKEN_URL, "refresh_token", REFRESH))
n = len(TOKEN_CALLS)
amz.access_token()
check("...and held until it expires, not minted per call", len(TOKEN_CALLS), n)
cs = amz.connection_status(ads_store)
check("connection_status: connected from the hub database, and says to pin it",
      (cs["connected"], cs["deploy_ready"], cs["refresh_token_source"].startswith("hub database")),
      (True, True, True))
os.environ["AMAZON_ADS_REFRESH_TOKEN"] = "Atzr|env-token-wins-000000"
check("the environment's token wins over the table's", amz.refresh_token_value(ads_store),
      "Atzr|env-token-wins-000000")
check("...and the source says so", amz.connection_status(ads_store)["refresh_token_source"],
      "environment")
os.environ.pop("AMAZON_ADS_REFRESH_TOKEN")

amz.forget_tokens()
ads_store.set_setting("amazon_refresh_token", "dead")
try:
    amz.access_token()
    check("a refusal from Amazon raises", False)
except amz.AmazonAuthError as exc:
    check("invalid_grant is a consent to give again, not a key to rotate",
          "revoked" in str(exc) and "another LWA application" in str(exc)
          and "rotate" in str(exc))
ads_store.set_setting("amazon_refresh_token", REFRESH)
amz.forget_tokens()
amz.access_token()


# ------------------------------------------------------------ the five claims
section("Five separate claims, told apart")

CALLS = []
ANSWERS = []


def fake_http(method, url, *, headers, json=None, timeout=60):
    CALLS.append({"method": method, "url": url, "headers": dict(headers), "body": json})
    if not ANSWERS:
        raise AssertionError(f"no answer queued for {url}")
    answer = ANSWERS.pop(0)
    return answer(url) if callable(answer) else answer


amz._http = fake_http

from hub import quotas                                               # noqa: E402
RECORDED = []
quotas.record = lambda provider, **kw: RECORDED.append({"provider": provider, **kw})

# Rung 3: the consent can see profiles, and the entity is not among them.
ANSWERS.append(_Resp(200, [{"profileId": 90, "accountInfo": {"id": "ENTITY_SOMEBODY_ELSE",
                                                             "type": "agency"}}]))
pre = amz.preflight(ads_store)
check("an entity that is not in the profile list stops at rung 3",
      (pre["ok"], pre["rung"]), (False, 3))
check("...reading as a consent given by the wrong person, or a wrong entity id",
      "does not administer the entity" in pre["problems"][0]
      and "AMAZON_DSP_ENTITY_ID" in pre["problems"][0])

# Rung 4: the profile resolves and the advertiser list is refused.
ANSWERS.extend([
    _Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1", "type": "agency"}}]),
    _Resp(403, {"code": "UNAUTHORIZED", "details": "not authorized for this entity"}),
])
pre = amz.preflight(ads_store)
check("a 403 on the advertiser list stops at rung 3 as not_permitted",
      (pre["ok"], pre["rung"]), (False, 3))
check("...read as the API application not being approved, which is not a wrong key",
      "not approved for this entity" in pre["problems"][0]
      and "not a wrong key" in pre["problems"][0])

# Rung 4: reachable entity with no advertisers on it yet.
ANSWERS.extend([_Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
                _Resp(200, {"response": []})])
pre = amz.preflight(ads_store)
check("an entity with no advertisers stops at rung 4, saying where they are created",
      (pre["rung"], "DSP console" in pre["problems"][0]), (4, True))

# All the way up.
ANSWERS.extend([_Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
                _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme Plumbing",
                                          "currency": "USD", "timezone": "America/New_York"}]})])
pre = amz.preflight(ads_store)
check("every claim met reaches rung 5", (pre["ok"], pre["rung"], pre["entity_profile_id"]),
      (True, 5, "77"))
check("...and rung 5, write access, is never probed and never claimed",
      pre["write_access"], "unprobed")
check("nothing the ladder produced carries a credential", _secrets_in(pre), [])
scope_headers = {c["headers"].get("Amazon-Advertising-API-Scope") for c in CALLS
                 if "advertisers" in c["url"]}
check("the advertiser list is scoped to the entity's own profile", scope_headers, {"77"})
check("every call carried the bearer token and the client id header",
      {(c["headers"]["Authorization"], c["headers"]["Amazon-Advertising-API-ClientId"])
       for c in CALLS},
      {("Bearer " + ACCESS, os.environ["AMAZON_ADS_CLIENT_ID"])})

os.environ["AMAZON_DSP_ENTITY_PROFILE_ID"] = "77"
check("a pinned profile id skips discovery altogether", amz.entity_profile_id(), "77")
os.environ.pop("AMAZON_DSP_ENTITY_PROFILE_ID")


# ----------------------------------------------------- one report, one tick
section("One report per advertiser: submit, poll, download, parse")

# A pre-signed S3 URL is a bearer capability: whoever holds the query string
# can fetch the report until it expires. It must reach the network and
# nothing that is written down.
SIGNED_URL = ("https://amazon-dsp-reports.s3.amazonaws.com/report.json.gz"
              "?X-Amz-Credential=AKIAEXAMPLE%2F20260916&X-Amz-Signature=" + "d4c3b2a1" * 8)

RAW = [
    # The request asks for ORDER *and* LINE_ITEM, so one order-day comes back
    # as one row per line item. These two are the same order on the same day.
    {"date": "20260914", "orderId": "o1", "orderName": "S1M | Acme Plumbing | Targeted Display",
     "lineItemId": "l1", "lineItemName": "Acme CTV 30s", "totalCost": "12.50",
     "impressions": 1000, "clickThroughs": 3, "videoComplete": 400,
     "totalPurchases": 2, "totalDetailPageViews": 9},
    {"date": "20260914", "orderId": "o1", "orderName": "S1M | Acme Plumbing | Targeted Display",
     "lineItemId": "l7", "lineItemName": "Acme CTV 15s", "totalCost": "7.50",
     "impressions": 600, "clickThroughs": 2, "videoComplete": 100,
     "totalPurchases": 1, "totalDetailPageViews": 3},
    # A display-only order: no completes on the row at all.
    {"date": "2026-09-13", "orderId": "o2", "orderName": "Acme display",
     "lineItemId": "l2", "lineItemName": "Acme banners", "totalCost": 3.25,
     "impressions": 800, "clickThroughs": 4, "videoComplete": 0,
     "totalPurchases": 0, "totalDetailPageViews": 0},
    {"date": "", "orderId": "o3", "orderName": "no day", "totalCost": 5},
    {"date": "20260914", "orderId": "", "orderName": "no order id", "totalCost": 5},
]
DOWNLOADS = []


def fake_download_get(url, timeout=None, **kw):
    DOWNLOADS.append({"url": url, "headers": dict(kw.get("headers") or {})})
    return _Resp(200, content=gzip.compress(json.dumps(RAW).encode()))


_real_get = requests.get
requests.get = fake_download_get

ANSWERS.extend([
    _Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
    _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme Plumbing", "currency": "USD"}]}),
    _Resp(200, {"reportId": "rep-1"}),
    _Resp(200, {"status": "IN_PROGRESS"}),
    _Resp(200, {"status": "SUCCESS", "location": SIGNED_URL}),
])
slept = []
res = amazon_dsp.pull(today=date(2026, 9, 16), sleep=slept.append)
check("the pull is ok", (res["ok"], res["error"]), (True, ""), note=res)
check("...landing two order-days from three readable rows, and counting the two it could not read",
      (res["rows"], res["skipped"]), (2, 2))
check("...across one advertiser, with nothing left pending",
      (res["advertisers"], res["pending"]), (1, {}))
check("...having waited once between polls", slept, [amz.POLL_EVERY])
check("...and said the field map is a claim nobody has confirmed",
      any("confirm" in n for n in res["notes"]))

submit = next(c for c in CALLS if c["method"] == "POST" and "/dsp/reports" in c["url"])
check("the report is asked for over the trailing fortnight, complete days only",
      (submit["body"]["startDate"], submit["body"]["endDate"]), ("20260902", "20260915"))
check("...daily, by order and line item, as JSON",
      (submit["body"]["timeUnit"], submit["body"]["dimensions"], submit["body"]["format"]),
      ("DAILY", ["ORDER", "LINE_ITEM"], "JSON"))
check("...asking for the metrics the field map reads",
      set(amazon_dsp.FIELD_MAP[k] for k in ("spend", "impressions", "clicks", "completes",
                                            "purchases", "detail_page_views"))
      <= set(submit["body"]["metrics"]))
check("the body carried no credential", _secrets_in([c["body"] for c in CALLS]), [])
check("the report body is fetched from the pre-signed URL, signature and all",
      [d["url"] for d in DOWNLOADS], [SIGNED_URL])
check("...with NO Authorization header on it: that host is not Amazon's API",
      [d["headers"] for d in DOWNLOADS], [{}])

from modules.reports.store import SessionLocal, AdPerfDaily          # noqa: E402
db = SessionLocal()
try:
    facts = {(r.account_id, r.campaign_id): r
             for r in db.query(AdPerfDaily).filter_by(platform="amazon_dsp").all()}
finally:
    db.close()
check("the rows are platform amazon_dsp / source native",
      {(r.platform, r.source) for r in facts.values()}, {("amazon_dsp", "native")})
r1 = facts[("A1", "o1")]
check("the campaign is the DSP order, filed under the advertiser",
      (r1.campaign_name, r1.account_id), ("S1M | Acme Plumbing | Targeted Display", "A1"))
# The bug this pins: the fact table's key is the order-day, so two line items
# on one order-day are two upserts into one row. Written unfolded, the last
# one wins and an order that spent 20.00 is filed as 7.50 — a wrong number on
# a client's report with nothing on any screen to question it.
check("...the order-day's line items are summed rather than the last one winning",
      (float(r1.spend), r1.impressions, r1.clicks), (20.0, 1600, 5))
check("...a YYYYMMDD day and an ISO one both read",
      (r1.date, facts[("A1", "o2")].date), (date(2026, 9, 14), date(2026, 9, 13)))
check("...no Amazon purchase is written into conversions, which stays at nothing",
      float(r1.conversions), 0.0)
check("...completes summed across them where video served", r1.completes, 500)
check("...and not at all where it did not", facts[("A1", "o2")].completes, None)
ex1 = r1.extras if isinstance(r1.extras, dict) else json.loads(r1.extras or "{}")
check("every line item on the order-day rides in extras by name, not one survivor",
      (ex1.get("line_items"), ex1.get("advertiser_name"), ex1.get("currency")),
      (["Acme CTV 30s", "Acme CTV 15s"], "Acme Plumbing", "USD"))
check("purchases and detail-page views ride there too, summed, under their own names",
      (ex1.get("purchases"), ex1.get("detail_page_views")), (3.0, 12.0))
# Folding is per order AND per day: neither may be summed into the other.
one_line = amazon_dsp.to_facts(
    [{"date": "20260914", "orderId": "oA", "orderName": "A", "lineItemName": "x", "totalCost": 1},
     {"date": "20260915", "orderId": "oA", "orderName": "A", "lineItemName": "x", "totalCost": 2},
     {"date": "20260914", "orderId": "oB", "orderName": "B", "lineItemName": "y", "totalCost": 4}],
    {"id": "A1", "name": "Acme"})["rows"]
check("a second day on one order is its own row, and another order is its own row",
      sorted((r["campaign_id"], r["date"].isoformat(), r["spend"]) for r in one_line),
      [("oA", "2026-09-14", 1.0), ("oA", "2026-09-15", 2.0), ("oB", "2026-09-14", 4.0)])

check("the native watermark is stamped", store.sync_status()["amazon_dsp"]["source"], "native")
check("...so the provider normalize defers to it", store.native_is_current("amazon_dsp"), True)
check("nothing the pull produced carries a credential",
      _secrets_in(res) + _secrets_in(amazon_dsp.status())
      + _secrets_in(store.sync_status()["amazon_dsp"]), [])

by_api = {}
for r in RECORDED:
    by_api[r.get("api")] = by_api.get(r.get("api"), 0) + 1
check("every call was recorded under amazon_ads, by endpoint family",
      {r["provider"] for r in RECORDED}, {"amazon_ads"})
check("...the report's own three among them",
      (by_api.get("reporting"), by_api.get("download")), (3, 1))
check("...filed under the reports module", {r["module"] for r in RECORDED}, {"reports"})
check("...with no credential in the detail", _secrets_in(RECORDED), [])
check("...and no pre-signed signature either: the ledger is rendered onto a page",
      [r for r in RECORDED if "X-Amz-Signature" in str(r.get("detail", ""))
       or "X-Amz-Credential" in str(r.get("detail", ""))], [])
check("...the download recorded by host and path alone",
      [r["detail"] for r in RECORDED if r.get("api") == "download"],
      ["https://amazon-dsp-reports.s3.amazonaws.com/report.json.gz"])

check("the unmapped queue opens on the order, not the line item",
      {u["campaign_id"] for u in store.unmapped_campaigns(days=365)
       if u["platform"] == "amazon_dsp"}, {"o1", "o2"})
from modules.reports import products as _products                    # noqa: E402
check("an order nobody has mapped is filed under the product the platform is sold as",
      _products.default_for("amazon_dsp"), "Streaming TV")
check("and the platform draws its own label", store.platform_label("amazon_dsp"), "Amazon DSP")


# ----------------------------------------------------------------- pending
section("A report still preparing is not a failure")

before_wm = dict(store.sync_status()["amazon_dsp"])
ANSWERS.extend([
    _Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
    _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme Plumbing", "currency": "USD"}]}),
    _Resp(200, {"reportId": "rep-2"}),
    _Resp(200, {"status": "IN_PROGRESS"}),
])
res = amazon_dsp.pull(today=date(2026, 9, 16), budget=0)
check("the tick is ok and nothing landed", (res["ok"], res["rows"], res["failures"]),
      (True, 0, []))
check("...the reportId is carried for the next tick, with when it was first seen",
      (res["pending"]["A1"]["report_id"], bool(res["pending"]["A1"]["since"])), ("rep-2", True))
check("...the watermark is untouched: nothing landed and nothing failed",
      store.sync_status()["amazon_dsp"], before_wm)
check("...so the provider normalize still defers to the last good pull",
      store.native_is_current("amazon_dsp"), True)
check("...and the index line says which reports are still preparing",
      "still preparing" in amazon_dsp.status()["line"])
check("the module's own note is what carries it between ticks",
      amazon_dsp.pending_reports(), {"A1": "rep-2"})

CALLS.clear()
ANSWERS.extend([
    _Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
    _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme Plumbing", "currency": "USD"}]}),
    _Resp(200, {"status": "SUCCESS", "location": SIGNED_URL}),
])
res = amazon_dsp.pull(today=date(2026, 9, 16))
check("the next tick asks for the same report rather than paying for a second one",
      [c["url"].rsplit("/", 1)[-1] for c in CALLS if "dsp/reports" in c["url"]], ["rep-2"])
check("...and files it", (res["ok"], res["rows"]), (True, 2))
check("...clearing what it was waiting on", amazon_dsp.pending_reports(), {})


# ------------------------------------------------ a report that never lands
section("A report that never lands is named, not carried for ever")

from datetime import datetime, timedelta, timezone                   # noqa: E402

# Put the carried report's first-seen stamp past the ceiling, the way a
# report that has sat at Amazon through a whole nightly cycle would be.
ANSWERS.extend([
    _Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
    _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme Plumbing", "currency": "USD"}]}),
    _Resp(200, {"reportId": "rep-stuck"}),
    _Resp(200, {"status": "IN_PROGRESS"}),
])
res = amazon_dsp.pull(today=date(2026, 9, 16), budget=0)
check("a first pending tick records when the report was first seen",
      (list(res["pending"]), bool(amazon_dsp.pending_since().get("A1"))), (["A1"], True))
check("...and nothing is stuck yet", (amazon_dsp.stuck_reports(), res["gave_up"]), ({}, {}))
first_seen = amazon_dsp.pending_since()["A1"]

ANSWERS.extend([
    _Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
    _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme Plumbing", "currency": "USD"}]}),
    _Resp(200, {"status": "IN_PROGRESS"}),
])
res = amazon_dsp.pull(today=date(2026, 9, 16), budget=0)
check("a second tick carries the SAME stamp, not a fresh one -- or it is never stuck",
      amazon_dsp.pending_since()["A1"], first_seen)
check("...still carrying the same report rather than paying for another",
      (res["pending"]["A1"]["report_id"], res["gave_up"]), ("rep-stuck", {}))

# Age the stamp past the ceiling and run again.
old_stamp = (datetime.now(timezone.utc)
             - timedelta(hours=amazon_dsp.STUCK_AFTER_HOURS + 2)).isoformat()
state = amazon_dsp._remembered()
state["pending"] = {"A1": {"report_id": "rep-stuck", "since": old_stamp}}
amazon_dsp._remember(state)
check("a report past the ceiling reads as stuck", list(amazon_dsp.stuck_reports()), ["A1"])
st = amazon_dsp.status()
check("...the index line says how long, rather than 'still preparing' for ever",
      ("have been preparing at Amazon for" in st["line"], "fresh one" in st["line"]), (True, True))
check("...and /diagnostics says so as a warning, with what happens next",
      (diagnostics.check_amazon_dsp().state,
       "fresh report" in (diagnostics.check_amazon_dsp().fix or "")), ("warn", True))

CALLS.clear()
ANSWERS.extend([
    _Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
    _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme Plumbing", "currency": "USD"}]}),
    _Resp(200, {"reportId": "rep-fresh"}),
    _Resp(200, {"status": "SUCCESS", "location": SIGNED_URL}),
])
res = amazon_dsp.pull(today=date(2026, 9, 16))
check("the next run stops carrying that id and asks Amazon for a new report",
      [c["url"].rsplit("/", 1)[-1] for c in CALLS if "dsp/reports" in c["url"]][:1], ["reports"])
check("...saying so rather than starting over quietly",
      (list(res["gave_up"]), any("fresh report" in n for n in res["notes"])), (["A1"], True))
check("...and the fresh report lands", (res["ok"], res["rows"]), (True, 2))
check("...leaving nothing pending or stuck",
      (amazon_dsp.pending_reports(), amazon_dsp.stuck_reports()), ({}, {}))

# The older note shape, from a deploy before the stamp existed.
amazon_dsp._remember({**amazon_dsp._remembered(), "pending": {"A9": "rep-old"}})
check("a note written before the stamp existed is still collected, not paid for twice",
      amazon_dsp.pending_reports(), {"A9": "rep-old"})
check("...and is not called stuck on the strength of a stamp nobody took",
      amazon_dsp.stuck_reports(), {})
amazon_dsp._remember({**amazon_dsp._remembered(), "pending": {}})


# ------------------------------------------- what a review found afterwards
section("The error paths, which is where the quiet losses were")

# A day Amazon writes in a shape this cannot read loses that ROW. store.parse_date
# raises rather than answering None, so unguarded it threw out of the fold and
# lost the whole advertiser's report -- the opposite of skipped-and-counted.
mixed = amazon_dsp.to_facts(
    [{"date": "20260914", "orderId": "o1", "orderName": "good", "totalCost": 1,
      "impressions": 10, "clickThroughs": 1},
     {"date": "the fourteenth", "orderId": "o2", "orderName": "unreadable day",
      "totalCost": 2, "impressions": 20}],
    {"id": "A1", "name": "Acme"})
check("an unreadable day costs that row and not the advertiser's whole report",
      ([r["campaign_id"] for r in mixed["rows"]], mixed["skipped"]), (["o1"], 1))

# Rounding once at the end rather than on every line item.
many = amazon_dsp.to_facts(
    [{"date": "20260914", "orderId": "o1", "orderName": "o", "lineItemName": f"l{i}",
      "totalCost": "0.005", "impressions": 1} for i in range(40)],
    {"id": "A1", "name": "Acme"})["rows"][0]
check("spend is rounded once, so forty line items do not compound a drift",
      many["spend"], 0.2)

# A night that never reached the reports must not forget what is pending.
# Two hours old: inside the ceiling, so this is about the error paths rather
# than about giving up.
HELD_SINCE = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
amazon_dsp._remember({**amazon_dsp._remembered(),
                      "pending": {"A1": {"report_id": "rep-held", "since": HELD_SINCE}}})
_saved_status = amazon_dsp.amazon_status
amazon_dsp.amazon_status = lambda: {"configured": False, "connected": False, "missing": ["X"],
                                    "region": "NA", "entity_id": "", "region_problem": ""}
try:
    amazon_dsp.pull(today=date(2026, 9, 16))
finally:
    amazon_dsp.amazon_status = _saved_status
check("a tick that stopped before it looked keeps the carried report",
      amazon_dsp.pending_reports(), {"A1": "rep-held"})
check("...and keeps when it was first seen, so the ceiling still counts from then",
      amazon_dsp.pending_since()["A1"], HELD_SINCE)

# A refusal partway through polling must not drop the carried report either:
# dropping it re-submits, which restarts the stuck clock for ever.
ANSWERS.extend([
    _Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
    _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme Plumbing", "currency": "USD"}]}),
    _Resp(429, {"code": "TOO_MANY_REQUESTS", "details": "slow down"}),
])
res = amazon_dsp.pull(today=date(2026, 9, 16))
check("a throttle mid-poll keeps the report rather than abandoning it",
      (res["pending"]["A1"]["report_id"], res["pending"]["A1"]["since"]),
      ("rep-held", HELD_SINCE))
check("...and names the refusal", any("throttled" in f for f in res["failures"]))

# pull() answers {adv: {report_id, since}}; handing that straight back must work.
ANSWERS.extend([
    _Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
    _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme Plumbing", "currency": "USD"}]}),
    _Resp(200, {"status": "SUCCESS", "location": SIGNED_URL}),
])
CALLS.clear()
res2 = amazon_dsp.pull(today=date(2026, 9, 16), pending=res["pending"])
check("a caller can hand back the pending shape pull() itself returned",
      [c["url"].rsplit("/", 1)[-1] for c in CALLS if "dsp/reports/" in c["url"]], ["rep-held"])
check("...and it collects rather than paying for another", (res2["ok"], res2["rows"]), (True, 2))

# Two stale reports: each name against its own duration.
old_a = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
old_b = (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat()
amazon_dsp._remember({**amazon_dsp._remembered(), "pending": {
    "A1": {"report_id": "r1", "since": old_a}, "A2": {"report_id": "r2", "since": old_b}}})
ANSWERS.extend([
    _Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
    _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme", "currency": "USD"},
                             {"advertiserId": "A2", "name": "Riverside", "currency": "USD"}]}),
    _Resp(200, {"reportId": "fresh-1"}),
    _Resp(200, {"status": "IN_PROGRESS"}),
    _Resp(200, {"reportId": "fresh-2"}),
    _Resp(200, {"status": "IN_PROGRESS"}),
])
res3 = amazon_dsp.pull(today=date(2026, 9, 16), budget=0)
note = next(n for n in res3["notes"] if "fresh report" in n)
check("each stuck advertiser is printed against its OWN duration, not the other's",
      ("A1 (preparing 30h)" in note, "A2 (preparing 50h)" in note), (True, True))
amazon_dsp._remember({**amazon_dsp._remembered(), "pending": {}})


# ------------------------------------------------- the check page never 500s
section("The check page answers even when something under it raises")

# The page is a GET a person refreshes. A reportId it asks for and does not
# write down is a report nobody ever collects -- one orphaned per refresh.
amazon_dsp._remember({**amazon_dsp._remembered(), "pending": {}})
ANSWERS.extend([
    _Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
    _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme", "currency": "USD"}]}),
    _Resp(200, {"reportId": "page-1"}),
    _Resp(200, {"status": "IN_PROGRESS"}),
])
slept.clear()
chk = amazon_dsp.check(today=date(2026, 9, 16))
check("a report the check page asks for is written down, so the pull collects it",
      amazon_dsp.pending_reports(), {"A1": "page-1"})
check("...and it waits inside the page's own budget, not the nightly job's",
      sum(slept) <= amazon_dsp.CHECK_BUDGET_SECONDS, True, note=slept)
CALLS.clear()
ANSWERS.extend([
    _Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
    _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme", "currency": "USD"}]}),
    _Resp(200, {"status": "IN_PROGRESS"}),
])
amazon_dsp.check(today=date(2026, 9, 16))
check("...so a refresh polls that same report rather than ordering another",
      ({c["url"].rsplit("/", 1)[-1] for c in CALLS if "dsp/reports/" in c["url"]},
       [c for c in CALLS if c["method"] == "POST" and c["url"].endswith("/dsp/reports")]),
      ({"page-1"}, []))
amazon_dsp._remember({**amazon_dsp._remembered(), "pending": {}})

# A row that is not a dict must never reach the template, which calls .items()
# on it -- and check()'s guard wraps _check(), not the render.
ANSWERS.extend([
    _Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
    _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme", "currency": "USD"}]}),
    _Resp(200, {"reportId": "odd-1"}),
    _Resp(200, {"status": "SUCCESS", "location": SIGNED_URL}),
])
_saved_dl = requests.get
requests.get = lambda url, timeout=None, **kw: _Resp(
    200, content=gzip.compress(json.dumps([["not", "a", "dict"], RAW[0]]).encode()))
try:
    chk = amazon_dsp.check(today=date(2026, 9, 16))
finally:
    requests.get = _saved_dl
check("a row that is not an object never reaches the page as the sample",
      isinstance(chk["sample"], dict) and chk["sample"]["orderId"] == "o1")
check("...and what the page prints is always something .items() works on",
      chk["sample"] is None or hasattr(chk["sample"], "items"))
amazon_dsp._remember({**amazon_dsp._remembered(), "pending": {}})

_boom = amz.connection_status
amz.connection_status = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("amazon fell over"))
try:
    chk = amazon_dsp.check(today=date(2026, 9, 16))
    check("a raise under the page is a page with an error on it, not a 500",
          ("could not be built" in chk["error"], "amazon fell over" in chk["error"]), (True, True))
    check("...and the template still has every key it reads",
          sorted(chk) >= sorted(["confirmed", "endpoints", "error", "map", "preflight",
                                 "request", "resolves", "sample", "status", "window"]))
    check("...carrying no credential", _secrets_in(chk), [])
finally:
    amz.connection_status = _boom

_ladder = amz.preflight
amz.preflight = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("the ladder fell over"))
try:
    chk = amazon_dsp.check(today=date(2026, 9, 16))
    check("a raise inside the ladder is named where it happened, not as the page failing",
          ("the ladder fell over" in chk["error"], "could not be built" in chk["error"]),
          (True, False))
finally:
    amz.preflight = _ladder


# --------------------------------------------- one advertiser, not the entity
section("One advertiser refused is that advertiser's problem")

ANSWERS.extend([
    _Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
    _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme Plumbing", "currency": "USD"},
                             {"advertiserId": "A2", "name": "Riverside HVAC", "currency": "USD"}]}),
    _Resp(403, {"code": "UNAUTHORIZED", "details": "not authorized for advertiser A1"}),
    _Resp(200, {"reportId": "rep-3"}),
    _Resp(200, {"status": "SUCCESS", "location": SIGNED_URL}),
])
res = amazon_dsp.pull(today=date(2026, 9, 16))
check("the refused advertiser is named, with the kind of refusal",
      (len(res["failures"]), "Acme Plumbing" in res["failures"][0],
       "not_permitted" in res["failures"][0]), (1, True, True))
check("...and the rest of the entity still landed", res["rows"], 2)
check("...the failure reaching the watermark, carrying no credential",
      ("not_permitted" in store.sync_status()["amazon_dsp"]["error"],
       _secrets_in(store.sync_status()["amazon_dsp"])), (True, []))

ANSWERS.extend([_Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
                _Resp(403, {"code": "UNAUTHORIZED", "details": "nope"})])
res = amazon_dsp.pull(today=date(2026, 9, 16))
check("the whole entity refused reads as the approval and not as the key",
      (res["ok"], "the approval, not the key" in res["error"]), (False, True), note=res)


# -------------------------------------------------------------- check page
section("/reports/amazon-check: the ladder, the request, and one raw row")

ANSWERS.extend([
    _Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
    _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme Plumbing", "currency": "USD"}]}),
    _Resp(200, {"reportId": "rep-4"}),
    _Resp(200, {"status": "SUCCESS", "location": SIGNED_URL}),
])
chk = amazon_dsp.check(today=date(2026, 9, 16))
check("the ladder is climbed to the top", chk["preflight"]["rung"], 5)
check("...a raw row is printed for the map to be read against",
      chk["sample"]["orderName"], "S1M | Acme Plumbing | Targeted Display")
check("...and the map resolves against it",
      (chk["resolves"]["resolved"], chk["resolves"]["missing"]), (True, []))
check("the map is a claim until a person flips it", (chk["confirmed"], amazon_dsp.CONFIRMED),
      (False, False))
check("...and every path says whether anybody has confirmed it",
      {ep["confirmed"] for ep in chk["endpoints"].values()}, {False})
check("nothing on the page carries a credential", _secrets_in(chk), [])
check("a map that does not resolve names the fields the report did not carry",
      amazon_dsp.check_map([{"orderId": "o1"}])["missing"],
      ["clickThroughs", "date", "impressions", "orderName", "totalCost"])


_saved_get = requests.get
requests.get = lambda url, timeout=None, **kw: _Resp(200, content=b"\x1f\x8btruncated")
try:
    amz.download_report(SIGNED_URL)
    check("a body that says gzip and will not inflate is a refusal in words", False)
except amz.AmazonApiError as exc:
    check("a body that says gzip and will not inflate is a refusal in words",
          ("inflate" in str(exc), exc.kind), (True, "shape"))
except Exception as exc:                                             # noqa: BLE001
    check("a body that says gzip and will not inflate is a refusal in words",
          f"raised {type(exc).__name__}")
requests.get = _saved_get


# --------------------------------------------------------------- reconcile
section("The reconcile figure is the same feed asked again, and says so")

from modules.reports import reconcile                                # noqa: E402
ANSWERS.extend([
    _Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
    _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme Plumbing", "currency": "USD"}]}),
    _Resp(200, {"reportId": "rep-5"}),
    _Resp(200, {"status": "SUCCESS", "location": SIGNED_URL}),
])
theirs = reconcile.theirs("amazon_dsp", date(2026, 9, 1), date(2026, 9, 15))
check("the month is measured", theirs["measured"], True, note=theirs)
check("...summing the same report over the window, line items and all",
      float(theirs["spend"]), 23.25)
check("...labeled a re-read and never an independent source",
      (theirs["independent"], "fetched again" in theirs["label"]), (False, True))

ANSWERS.extend([_Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
                _Resp(200, {"response": [{"advertiserId": "A1", "name": "Acme"}]}),
                _Resp(200, {"reportId": "rep-6"}),
                _Resp(200, {"status": "IN_PROGRESS"})])
try:
    amazon_dsp.month_total(date(2026, 9, 1), date(2026, 9, 15), budget=0)
    check("a report still preparing refuses rather than holding the nightly thread", False)
except amz.AmazonApiError as exc:
    check("a report still preparing refuses rather than holding the nightly thread",
          "not measured tonight" in str(exc))
ANSWERS.extend([_Resp(200, [{"profileId": 77, "accountInfo": {"id": "ENTITY1"}}]),
                _Resp(403, {"code": "UNAUTHORIZED", "details": "nope"})])
theirs = reconcile.theirs("amazon_dsp", date(2026, 9, 1), date(2026, 9, 15))
check("...and a refusal is not measured, with Amazon's own sentence and no credential",
      (theirs["measured"], "UNAUTHORIZED" in theirs["reason"], _secrets_in(theirs)),
      (False, True, []))


# --------------------------------------------------------------- the wiring
section("Wired: the scheduler, the index, the usage page")

import inspect                                                       # noqa: E402
from hub import scheduler                                            # noqa: E402
src = inspect.getsource(scheduler.job_reports_native_pull)
check("the nightly native pull runs it in the same loop as the others",
      '("amazon_dsp", amazon_dsp.pull)' in src)
from modules.reports import app as reports_app                       # noqa: E402
check("the Reports index prints its status line",
      ("amazon_dsp", "amazon_dsp") in reports_app.NATIVE_PULLS)
check("the store knows the platform and the provider map covers it",
      ("amazon_dsp" in store.PLATFORMS,
       "amazon_dsp" in __import__("modules.reports.provider_map", fromlist=["x"]).PLATFORM_SOURCES),
      (True, True))
check("the usage page has a row to file the calls under", "amazon_ads" in quotas.QUOTAS)
check("...and the unrecorded-call sweep watches the Amazon hosts",
      quotas._PROVIDER_MARKERS["amazon_ads"]["calls"](
          'requests.get("https://advertising-api.amazon.com/v2/profiles")'), True)


# ------------------------------------------------------------- Smart 1 Ads
section("Smart 1 Ads: the card, the callback, the disconnect, and 501")

os.environ["PANEL_PASSWORD"] = "x"
import importlib                                                     # noqa: E402
ads_app_mod = importlib.import_module("modules.ads_builder.app")
ads_app_mod.app.config["TESTING"] = True
client = ads_app_mod.app.test_client()
ENV = {"REMOTE_ADDR": "127.0.0.1"}

html = client.get("/settings", environ_base=ENV).get_data(as_text=True)
check("the settings card offers Disconnect while a consent is held",
      "disconnectAmazon()" in html and "Connect Amazon Ads" not in html)
check("...saying who has to press Connect", "admin on the DSP entity" in html)
check("...and printing the callback to register", "/tools/ads/oauth/amazon/callback" in html)
check("...and no credential", _secrets_in(html), [])

r = client.get("/connect/amazon", environ_base=ENV)
check("Connect redirects to Login with Amazon, keeping a state cookie",
      (r.status_code, r.headers["Location"].startswith(amz.LWA_AUTHORIZE_URL),
       "s1ads_amazon_oauth_state" in r.headers.get("Set-Cookie", "")), (302, True, True))
state = parse_qs(urlsplit(r.headers["Location"]).query)["state"][0]

EXCHANGED = []
_real_exchange = amz.exchange_code


def fake_exchange(code):
    EXCHANGED.append(code)
    return {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600}


amz.exchange_code = fake_exchange
try:
    client.set_cookie("s1ads_amazon_oauth_state", state)
    r = client.get(f"/oauth/amazon/callback?code=auth-code-1&state={state}", environ_base=ENV)
    html = r.get_data(as_text=True)
    check("the callback exchanges the code once", (r.status_code, EXCHANGED), (200, ["auth-code-1"]))
    check("...keeps the refresh token in the settings table",
          ads_store.get_setting("amazon_refresh_token"), REFRESH)
    check("...shows it once against the Amazon pin variable",
          REFRESH in html and "AMAZON_ADS_REFRESH_TOKEN" in html
          and "BING_AD_REFRESH_TOKEN" not in html)
    check("...naming what the consent reaches, and what it does not",
          "admin on the DSP entity" in html)
    check("...and the state cookie is cleared",
          "s1ads_amazon_oauth_state=;" in r.headers.get("Set-Cookie", ""))
    client.set_cookie("s1ads_amazon_oauth_state", state)
    r = client.get("/oauth/amazon/callback?code=x&state=wrong", environ_base=ENV)
    check("a state mismatch is refused",
          (r.status_code, "state mismatch" in r.get_data(as_text=True).lower()), (400, True))
    r = client.get("/oauth/amazon/callback?error=access_denied", environ_base=ENV)
    check("a cancelled consent is refused in words",
          (r.status_code, "cancelled" in r.get_data(as_text=True)), (400, True))
finally:
    amz.exchange_code = _real_exchange

r = client.get("/api/status", environ_base=ENV).get_json()
check("/api/status carries the connection in the Google card's shape",
      (r["amazon"]["configured"], r["amazon"]["connected"], r["amazon"]["deploy_ready"],
       "note" in r["amazon"]), (True, True, True, True))
check("...and no credential", _secrets_in(r), [])
r = client.get("/api/amazon/orders", environ_base=ENV)
check("campaign management answers 501, saying what is built",
      (r.status_code, "not built" in r.get_json()["error"]), (501, True))
r = client.post("/api/amazon/disconnect", environ_base=ENV)
check("disconnect clears the stored consent and names the pinned copy too",
      (r.status_code, ads_store.get_setting("amazon_refresh_token"),
       "AMAZON_ADS_REFRESH_TOKEN" in r.get_json()["note"]), (200, "", True))

os.environ.pop("AMAZON_ADS_CLIENT_ID")
r = client.get("/connect/amazon", environ_base=ENV)
check("Connect refuses by name until the variables are set",
      (r.status_code, "AMAZON_ADS_CLIENT_ID" in r.get_data(as_text=True)), (400, True))
html = client.get("/settings", environ_base=ENV).get_data(as_text=True)
check("...and the card names what is missing rather than offering the button",
      "AMAZON_ADS_CLIENT_ID" in html and "Connect Amazon Ads" not in html)

requests.post = _real_post
requests.get = _real_get
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
