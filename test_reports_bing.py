"""The native Microsoft Advertising pull: the connection, the four headers,
the submit-poll-download report, and where no credential ever goes.

    python3 test_reports_bing.py

No pytest, no new dependencies, a throwaway SQLite reports database and a
stand-in for the platform: ``bing_ads._http`` is replaced whole, the token
endpoint and the report download are stubbed at ``requests``, so nothing
here reaches the network and every request the module makes is recorded.

What it holds:

  * unconfigured names the variables, a manager id that is not a customer
    id is refused by name before anything is sent, and not connected is a
    sentence pointing at Settings -- each a clean pull that returns rather
    than raises and stamps no watermark over the provider's;
  * the consent URL asks the identity platform's /common/ endpoint for the
    msads.manage and offline_access scopes and comes back to
    <PUBLIC_BASE_URL>/tools/ads/oauth/bing/callback, which is the string
    hub/oauth_redirects.py prints for the Azure portal, built from the
    origin even when PUBLIC_BASE_URL carries a path;
  * the refresh token is read from the environment first and the settings
    table second, and every call carries the bearer token, the developer
    token and the manager customer id -- and none of them reaches a result,
    an error, a watermark, a status line or a log line;
  * ONE report per pull, scoped to every account under the manager: a
    daily CampaignPerformanceReportRequest submitted, polled while Pending
    inside a wall-clock budget (pending is not failed, and touches no
    watermark), downloaded as a ZIP and parsed defensively -- a report
    header block skipped, the footer dropped, a row with no campaign id
    counted, spend in the account's currency as written;
  * CampaignType lands as channel_type and files a default product
    (Search → Paid Search, Audience → Programmatic Display, anything else
    the platform default with the rule saying so);
  * the rows land as platform bing / source native and stamp the native
    watermark the provider normalize defers to; a refusal names the
    environment it was refused on;
  * the reconcile reads the month back as an account report, labeled a
    re-read; every call is recorded under microsoft_ads with the service
    it reached; the scheduler job pulls it in the same loop;
  * Smart 1 Ads: the settings card offers Connect only when configured,
    the callback keeps the token and shows it once against the pin
    variable, disconnect clears it, /api/status carries the connection,
    and campaign management still answers 501.
"""
import io
import json
import re
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_bing_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-bing-test"
os.environ["PUBLIC_BASE_URL"] = "https://smart1.agency"
BING_VARS = ("BING_AD_CLIENT_ID", "BING_AD_CLIENT_SECRET", "BING_AD_DEVELOPER_TOKEN",
             "BING_MANAGER_ACCOUNT_ID", "BING_MANAGER_ACCOUNT_NUMBER", "BING_AD_REFRESH_TOKEN",
             "BING_AD_ENVIRONMENT")
for k in BING_VARS + ("STACKADAPT_API_KEY", "AUDIOGO_API_KEY", "TTD_API_TOKEN",
                      "GOOGLE_ADS_REFRESH_TOKEN", "GOOGLE_ADS_DEVELOPER_TOKEN"):
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
from modules.ads_builder import bing_ads, store as ads_store         # noqa: E402
from modules.reports import bing, store                              # noqa: E402
_reports_testdb.reset(store)

SECRET = "sekret-client-secret-9f8e7d6c"
DEVTOKEN = "DEV1234567890TOKEN"
REFRESH = "M.C5xx-refresh-token-abcdef0123456789"
ACCESS = "EwB4A8l6BAAU-access-token-0123456789abcdef"


def _secrets_in(text) -> list:
    s = json.dumps(text, default=str) if not isinstance(text, str) else text
    return [n for n, v in (("secret", SECRET), ("devtoken", DEVTOKEN),
                           ("refresh", REFRESH), ("access", ACCESS)) if v in s]


# --------------------------------------------------------- unconfigured
section("Unconfigured is a sentence naming the variables, not an exception")

st = bing.status()
check("not configured", st["configured"], False)
check("...the line names all four", st["line"],
      "Microsoft Ads: not configured: BING_AD_CLIENT_ID, BING_AD_CLIENT_SECRET, "
      "BING_AD_DEVELOPER_TOKEN, BING_MANAGER_ACCOUNT_ID unset")
check("...and missing lists them", st["missing"], list(bing_ads.REQUIRED))
res = bing.pull()
check("a pull returns rather than raising", res["ok"], False)
check("...saying so", res["error"].startswith("not configured"))
check("...and wrote no watermark", store.sync_status().get("bing"), None)

cs = bing_ads.connection_status(None)
check("connection_status names what each missing variable is for",
      [b["name"] for b in cs["blocks"]], list(bing_ads.REQUIRED))
check("...with a reason on each", all(b["why"] for b in cs["blocks"]))

# The environment is read at call time, through hub/config.py's own reader.
os.environ.update({"BING_AD_CLIENT_ID": "app-client-id", "BING_AD_CLIENT_SECRET": SECRET,
                   "BING_AD_DEVELOPER_TOKEN": DEVTOKEN, "BING_MANAGER_ACCOUNT_ID": "X0123456",
                   "BING_MANAGER_ACCOUNT_NUMBER": "12345678"})
cs = bing_ads.connection_status(None)
check("the account NUMBER pasted into the id slot is refused by name, before any call",
      bool(cs["manager_id_problem"]) and not cs["configured"] and cs["missing"] == [])
check("...saying which variable takes it", "BING_MANAGER_ACCOUNT_NUMBER" in cs["manager_id_problem"])
res = bing.pull()
check("...and the pull says so rather than sending it", "BING_MANAGER_ACCOUNT_ID" in res["error"]
      and res["error"].startswith("not configured"))
check("...on the status line too", "BING_MANAGER_ACCOUNT_ID" in bing.status()["line"])
from hub import diagnostics                                          # noqa: E402
check("/diagnostics reads it as an error", diagnostics.check_microsoft_ads().state, "error")

os.environ["BING_MANAGER_ACCOUNT_ID"] = "12345678"
os.environ["BING_MANAGER_ACCOUNT_NUMBER"] = "X0123456"
cs = bing_ads.connection_status(ads_store)
check("digits in the id slot: configured, and the number kept beside it",
      (cs["configured"], cs["manager_id"], cs["manager_number"]), (True, "12345678", "X0123456"))
check("...not connected: nobody has consented", (cs["connected"], cs["deploy_ready"]), (False, False))
st = bing.status()
check("the status line points at Settings", st["line"], "Microsoft Ads: " + bing.NOT_CONNECTED)
res = bing.pull()
check("a pull not connected is clean", (res["ok"], res["error"]), (False, bing.NOT_CONNECTED))
check("...and wrote no watermark", store.sync_status().get("bing"), None)
check("/diagnostics reads it as a warning naming the press", diagnostics.check_microsoft_ads().state, "warn")


# ----------------------------------------------------------- the consent
section("The consent URL, and the callback the panel prints")

from urllib.parse import parse_qs, urlsplit                          # noqa: E402
url = bing_ads.build_auth_url("state-abc")
parts = urlsplit(url)
q = {k: v[0] for k, v in parse_qs(parts.query).items()}
check("the identity platform's /common/ authorize endpoint, because a Microsoft Advertising "
      "login is as often a personal account as a work one",
      f"{parts.scheme}://{parts.netloc}{parts.path}", bing_ads.AUTH_URL)
check("...asking for the ads scope and offline_access, as a code",
      (q["scope"], q["response_type"]), ("https://ads.microsoft.com/msads.manage offline_access", "code"))
check("...coming back to the mount's own callback under PUBLIC_BASE_URL",
      q["redirect_uri"], "https://smart1.agency/tools/ads/oauth/bing/callback")
check("...carrying the state and asking which account", (q["state"], q["prompt"]), ("state-abc", "select_account"))
check("...and the client id, never the secret", q["client_id"] == "app-client-id" and SECRET not in url)

from hub import oauth_redirects as orx                               # noqa: E402
row = next(r for r in orx.rows("https://smart1.agency/") if r["key"] == "bing_ads")
check("hub/oauth_redirects.py lists the flow as an eighth, built from PUBLIC_BASE_URL",
      (row["source"], row["client_var"], row["state"]), ("PUBLIC_BASE_URL", "BING_AD_CLIENT_ID", "ok"))
check("...and prints exactly the string the code sends", row["uris"], [bing_ads.redirect_uri()])
check("...naming the Azure portal", "Azure" in row["console"])
os.environ["PUBLIC_BASE_URL"] = "https://smart1.agency/tools/ads/oauth/callback"
check("a PUBLIC_BASE_URL carrying a path does not put it in the middle of the callback",
      bing_ads.redirect_uri(), "https://smart1.agency/tools/ads/oauth/bing/callback")
os.environ["PUBLIC_BASE_URL"] = ""
check("no PUBLIC_BASE_URL is a callback with nowhere to come back to, named",
      (bing_ads.redirect_uri(), "PUBLIC_BASE_URL" in bing_ads.connection_status(None)["missing"]), ("", True))
os.environ["PUBLIC_BASE_URL"] = "https://smart1.agency"

# The spellings Render actually carries: the app registration under
# MICROSOFT_ADS_CLIENT_ID / MICROSOFT_ADS_CLIENT_SECRET beside the BING_AD_
# pair. The day this was found the card reported the pair missing with
# both plainly set, and Connect could not start.
os.environ.pop("BING_AD_CLIENT_ID"); os.environ.pop("BING_AD_CLIENT_SECRET")
cs = bing_ads.connection_status(None)
check("with neither spelling set the pair is missing under the name the card prints",
      cs["missing"], ["BING_AD_CLIENT_ID", "BING_AD_CLIENT_SECRET"])
check("...and each block names the other spelling as read too",
      all("MICROSOFT_ADS_" in b["why"] for b in cs["blocks"]))
os.environ["MICROSOFT_ADS_CLIENT_ID"] = "ms-client-id"
os.environ["MICROSOFT_ADS_CLIENT_SECRET"] = "ms-secret-value-000000"
cs = bing_ads.connection_status(None)
check("MICROSOFT_ADS_CLIENT_ID / _SECRET configure the connection", (cs["configured"], cs["missing"]), (True, []))
check("...and the consent carries that client id", "client_id=ms-client-id" in bing_ads.build_auth_url("s"))
check("...and the secret under that spelling never leaves", "ms-secret-value-000000" not in bing_ads._redact("ms-secret-value-000000 leaked"))
from hub import config as _cfg                                       # noqa: E402
check("hub/config.py resolves the same pair", (_cfg._alias("bing_client_id"), _cfg._alias("bing_client_secret")),
      ("ms-client-id", "ms-secret-value-000000"))
os.environ["BING_AD_CLIENT_ID"] = "app-client-id"; os.environ["BING_AD_CLIENT_SECRET"] = SECRET
check("...with the BING_AD_ spelling first when both are set", bing_ads.cfg()["client_id"], "app-client-id")
rep_rows = {r["setting"]: r for r in _cfg.settings.env_report()}
check("...and /diagnostics' env report says which spelling answered and which was ignored",
      (rep_rows["bing_client_id"]["resolved"], rep_rows["bing_client_id"].get("ignored")),
      ("BING_AD_CLIENT_ID", ["MICROSOFT_ADS_CLIENT_ID"]))
os.environ.pop("MICROSOFT_ADS_CLIENT_ID"); os.environ.pop("MICROSOFT_ADS_CLIENT_SECRET")

# The tenant: /common/ unless MICROSOFT_ADS_TENANT narrows it, and never
# a pasted URL inside the identity host's path.
check("no tenant is /common/", (bing_ads.tenant(), bing_ads.auth_url()), ("common", bing_ads.AUTH_URL))
os.environ["MICROSOFT_ADS_TENANT"] = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"
check("a tenant id goes into both identity endpoints",
      (bing_ads.auth_url(), bing_ads.token_url()),
      ("https://login.microsoftonline.com/0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b/oauth2/v2.0/authorize",
       "https://login.microsoftonline.com/0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b/oauth2/v2.0/token"))
check("...and the consent URL is built on it", bing_ads.build_auth_url("s").startswith(bing_ads.auth_url() + "?"))
check("...reported on the connection", bing_ads.connection_status(None)["tenant"], "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b")
os.environ["MICROSOFT_ADS_TENANT"] = "https://login.microsoftonline.com/common"
check("a pasted URL is not a tenant and falls back to /common/", bing_ads.tenant(), "common")
os.environ.pop("MICROSOFT_ADS_TENANT")

# The pinned redirect URI: sent exactly when it is this Hub's callback,
# refused by name when it is some other page.
PIN = "https://smart1-hub.onrender.com/tools/ads/oauth/bing/callback"
os.environ["MICROSOFT_ADS_REDIRECT_URI"] = f'"{PIN}"'
cs = bing_ads.connection_status(None)
check("MICROSOFT_ADS_REDIRECT_URI pins the callback, Render's quotes stripped",
      (bing_ads.redirect_uri(), cs["redirect_uri_source"], cs["configured"]), (PIN, "MICROSOFT_ADS_REDIRECT_URI", True))
check("...and the consent sends exactly it",
      parse_qs(urlsplit(bing_ads.build_auth_url("s")).query)["redirect_uri"], [PIN])
row = next(r for r in orx.rows("https://smart1.agency/") if r["key"] == "bing_ads")
check("...and the panel prints the same string, naming the pin as its source",
      (row["source"], row["uris"]), ("MICROSOFT_ADS_REDIRECT_URI", [PIN]))
os.environ["MICROSOFT_ADS_REDIRECT_URI"] = "https://smart1-hub.onrender.com/tools/ads/oauth/callback"
cs = bing_ads.connection_status(None)
check("a pin at another page's path is refused by name, not sent",
      (cs["configured"], "/tools/ads/oauth/bing/callback" in cs["redirect_uri_problem"],
       cs["missing"], [b["name"] for b in cs["blocks"]]),
      (False, True, [], ["MICROSOFT_ADS_REDIRECT_URI"]))
check("...the code builds nothing from it", bing_ads.redirect_uri(), "https://smart1.agency/tools/ads/oauth/bing/callback")
check("...the pull says so", "MICROSOFT_ADS_REDIRECT_URI" in bing.pull()["error"])
check("/diagnostics reads it as an error naming the variable",
      (diagnostics.check_microsoft_ads().state, "MICROSOFT_ADS_REDIRECT_URI" in diagnostics.check_microsoft_ads().detail),
      ("error", True))
os.environ.pop("MICROSOFT_ADS_REDIRECT_URI")


# --------------------------------------------------------- the token
section("The refresh token: environment first, settings table second, never out")

TOKEN_CALLS = []


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


def fake_token_post(url, data=None, timeout=None, **kw):
    TOKEN_CALLS.append({"url": url, "data": dict(data or {})})
    if data.get("refresh_token") == "dead":
        return _Resp(400, {"error": "invalid_grant", "error_description": "AADSTS70000: the refresh token has expired"})
    return _Resp(200, {"access_token": ACCESS, "expires_in": 3600, "refresh_token": REFRESH,
                       "token_type": "Bearer"})


_real_post = requests.post
requests.post = fake_token_post
bing_ads.forget_tokens()

try:
    bing_ads.access_token(ads_store)
    check("no token anywhere is NotConnected", False)
except bing_ads.NotConnected as exc:
    check("no token anywhere is NotConnected, pointing at Settings", "Connect Microsoft Ads" in exc.message)

ads_store.set_setting("bing_refresh_token", REFRESH)
tok = bing_ads.access_token(ads_store)
check("the settings-table token is exchanged at the token endpoint", tok, ACCESS)
check("...with the refresh grant, the client secret and the scope",
      (TOKEN_CALLS[-1]["url"], TOKEN_CALLS[-1]["data"]["grant_type"],
       TOKEN_CALLS[-1]["data"]["refresh_token"], TOKEN_CALLS[-1]["data"]["scope"]),
      (bing_ads.TOKEN_URL, "refresh_token", REFRESH, bing_ads.SCOPE))
n = len(TOKEN_CALLS)
bing_ads.access_token(ads_store)
check("...and cached until it expires, not refreshed per call", len(TOKEN_CALLS), n)
cs = bing_ads.connection_status(ads_store)
check("connection_status: connected from the hub database, and says to pin it",
      (cs["connected"], cs["deploy_ready"], cs["refresh_token_source"].startswith("hub database")),
      (True, True, True))
os.environ["BING_AD_REFRESH_TOKEN"] = "env-refresh-token-wins-000000"
check("the environment's token wins over the table's", bing_ads.refresh_token_value(ads_store),
      "env-refresh-token-wins-000000")
check("...and the source says so", bing_ads.connection_status(ads_store)["refresh_token_source"], "environment")
os.environ.pop("BING_AD_REFRESH_TOKEN")

bing_ads.forget_tokens()
ads_store.set_setting("bing_refresh_token", "dead")
try:
    bing_ads.access_token(ads_store)
    check("a refresh Microsoft refuses is NotConnected", False)
except bing_ads.NotConnected as exc:
    check("a refresh Microsoft refuses is NotConnected carrying Microsoft's own sentence",
          "AADSTS70000" in exc.message and exc.code == "REFRESH_FAILED")
ads_store.set_setting("bing_refresh_token", REFRESH)
bing_ads.forget_tokens()
bing_ads.access_token(ads_store)

h = bing_ads.api_headers(ads_store, account_id="777")
check("every call carries the bearer token, the developer token and the manager customer id",
      (h["Authorization"], h["DeveloperToken"], h["CustomerId"], h["CustomerAccountId"]),
      ("Bearer " + ACCESS, DEVTOKEN, "12345678", "777"))
check("/diagnostics reads a connected account as ok, without a probe",
      diagnostics.check_microsoft_ads().state, "ok")


# ----------------------------------------------------------- the wire
section("One report per pull: submit, poll, download, parse")

CALLS = []
ANSWERS = []          # queued _Resp objects, consumed in order by fake_http
DOWNLOADS = []


def fake_http(method, url, *, headers, json=None, timeout=60):
    CALLS.append({"method": method, "url": url, "headers": dict(headers), "body": json})
    if not ANSWERS:
        raise AssertionError(f"no answer queued for {url}")
    return ANSWERS.pop(0)


CSV_TEXT = (
    '"Report Name: smart1-hub-campaigns"\n'
    '"Report Time: 9/1/2026 - 9/10/2026"\n'
    '\n'
    'TimePeriod,AccountId,AccountName,AccountNumber,CampaignId,CampaignName,CampaignType,'
    'CampaignStatus,CurrencyCode,Spend,Impressions,Clicks,Conversions\n'
    '2026-09-10,111,Acme Plumbing,X111,c1,S1M | Acme Plumbing | Paid Search,Search,Active,USD,"12.50","1,000",12,2\n'
    '9/10/2026,111,Acme Plumbing,X111,c2,Acme audience,Audience,Active,USD,3.25,800,4,0\n'
    '2026-09-09,222,Riverside HVAC,X222,c3,Riverside shopping,Shopping,Active,USD,0,0,0,0\n'
    '2026-09-09,222,Riverside HVAC,X222,,no campaign id,Search,Active,USD,5,10,1,0\n'
    ',222,Riverside HVAC,X222,c9,no day,Search,Active,USD,5,10,1,0\n'
    '©2026 Microsoft Corporation. All rights reserved.\n'
)


def _zip(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.csv", text)
    return buf.getvalue()


def fake_get(url, timeout=None, **kw):
    DOWNLOADS.append(url)
    return _Resp(200, None, content=_zip(CSV_TEXT))


_real_http, _real_get = bing_ads._http, requests.get
bing_ads._http = fake_http
requests.get = fake_get

from hub import quotas                                               # noqa: E402
RECORDED = []
_real_record = quotas.record


def fake_record(provider, **kw):
    RECORDED.append({"provider": provider, **kw})


quotas.record = fake_record

ANSWERS.extend([
    _Resp(200, {"Accounts": [{"Id": 111, "Name": "Acme Plumbing", "Number": "X111",
                              "AccountLifeCycleStatus": "Active", "CurrencyCode": "USD"},
                             {"Id": 222, "Name": "Riverside HVAC", "Number": "X222",
                              "AccountLifeCycleStatus": "Active", "CurrencyCode": "USD"}]}),
    _Resp(200, {"ReportRequestId": "req-1"}),
    _Resp(200, {"ReportRequestStatus": {"Status": "Pending", "ReportDownloadUrl": None}}),
    _Resp(200, {"ReportRequestStatus": {"Status": "Success",
                                        "ReportDownloadUrl": "https://download.example/report.zip?sig=abc"}}),
])
slept = []
res = bing.pull(days=10, today=date(2026, 9, 10), sleep=slept.append)
check("the pull is ok", (res["ok"], res["error"]), (True, ""), note=res)
check("...landing the three complete rows and counting the two it could not read",
      (res["rows"], res["skipped"]), (3, 2))
check("...across two accounts and three campaigns", (res["accounts"], res["campaigns"]), (2, 3))
check("...on the production host", res["environment"], "production")

acct, submit, poll1, poll2 = CALLS[-4:]
check("the accounts were read from Customer Management under the manager's id",
      (acct["url"].endswith("/CustomerManagement/v13/Accounts/Search"),
       acct["body"]["Predicates"][0]), (True, {"Field": "CustomerId", "Operator": "Equals", "Value": "12345678"}))
check("...on the production host", acct["url"].startswith("https://clientcenter.api.bingads.microsoft.com/"))
rr = submit["body"]["ReportRequest"]
check("ONE daily campaign report was submitted for both accounts",
      (submit["url"].endswith("/Reporting/v13/GenerateReport/Submit"), rr["Type"], rr["Aggregation"],
       rr["Scope"]["AccountIds"]), (True, "CampaignPerformanceReportRequest", "Daily", [111, 222]))
check("...over the window, day by day", (rr["Time"]["CustomDateRangeStart"], rr["Time"]["CustomDateRangeEnd"]),
      ({"Day": 1, "Month": 9, "Year": 2026}, {"Day": 10, "Month": 9, "Year": 2026}))
check("...as CSV with the report's own header and footer left off",
      (rr["Format"], rr["ExcludeReportHeader"], rr["ExcludeReportFooter"], rr["ExcludeColumnHeaders"]),
      ("Csv", True, True, False))
check("...naming the columns the parser reads", all(c in rr["Columns"] for c in
      ("TimePeriod", "AccountId", "CampaignId", "CampaignName", "CampaignType", "Spend", "Impressions", "Clicks", "Conversions")))
check("...and a partial today still counts", rr["ReturnOnlyCompleteData"], False)
check("polled with the request id until Success", (poll1["body"], poll2["body"]),
      ({"ReportRequestId": "req-1"}, {"ReportRequestId": "req-1"}))
check("...waiting once between", slept, [bing.POLL_WAIT])
check("then downloaded from the URL the platform handed back", DOWNLOADS, ["https://download.example/report.zip?sig=abc"])
for c in (acct, submit, poll1, poll2):
    check(f"every call carried the four headers ({c['url'].rsplit('/', 1)[-1]})",
          (c["headers"]["Authorization"], c["headers"]["DeveloperToken"], c["headers"]["CustomerId"]),
          ("Bearer " + ACCESS, DEVTOKEN, "12345678"))
check("...and the body carried none of them", _secrets_in([c["body"] for c in CALLS[-4:]]), [])

rows = store.facts_for  # noqa: F841 - the store's own reader is exercised below through the watermark
from modules.reports.store import SessionLocal, AdPerfDaily          # noqa: E402
db = SessionLocal()
try:
    facts = {(r.account_id, r.campaign_id): r for r in db.query(AdPerfDaily).filter_by(platform="bing").all()}
finally:
    db.close()
r1 = facts[("111", "c1")]
check("the rows are platform bing / source native", (r1.platform, r1.source), ("bing", "native"))
check("...spend in the account's currency as written, a formatted number read",
      (float(r1.spend), r1.impressions, r1.clicks, float(r1.conversions)), (12.5, 1000, 12, 2.0))
check("...a US-style day read too", facts[("111", "c2")].date, date(2026, 9, 10))
ex1 = r1.extras if isinstance(r1.extras, dict) else json.loads(r1.extras or "{}")
ex2 = facts[("111", "c2")].extras if isinstance(facts[("111", "c2")].extras, dict) else json.loads(facts[("111", "c2")].extras or "{}")
check("CampaignType rides on the row as channel_type, with the account name",
      (ex1.get("channel_type"), ex2.get("channel_type"), ex1.get("account_name")), ("SEARCH", "AUDIENCE", "Acme Plumbing"))
check("the native watermark is stamped", store.sync_status()["bing"]["source"], "native")
check("...so the provider normalize defers to it", store.native_is_current("bing"), True)
check("nothing the pull produced carries a credential", _secrets_in(res) + _secrets_in(bing.status())
      + _secrets_in(store.sync_status()["bing"]), [])

by_api = {}
for r in RECORDED:
    by_api[r.get("api")] = by_api.get(r.get("api"), 0) + 1
check("every call was recorded under microsoft_ads, by service",
      ({r["provider"] for r in RECORDED}, by_api), ({"microsoft_ads"}, {"customer": 1, "reporting": 3, "download": 1}))
check("...filed under the reports module", {r["module"] for r in RECORDED}, {"reports"})
check("...with no credential in the detail", _secrets_in(RECORDED), [])

# What a campaign with no product in its name is filed under.
from modules.reports import products as _products                    # noqa: E402
check("an Audience campaign defaults to Programmatic Display, a Search one to Paid Search",
      (_products.default_for("bing", "AUDIENCE"), _products.default_for("bing", "SEARCH")),
      ("Programmatic Display", "Paid Search"))
check("...and the channel says when it decided",
      (_products.channel_decided("bing", "AUDIENCE"), _products.channel_decided("bing", "SHOPPING")), (True, False))
check("a type the table does not map takes the platform default",
      _products.default_for("bing", "PERFORMANCEMAX"), "Paid Search")
check("Google's table is untouched", (_products.default_for("google", "VIDEO"), _products.channel_decided("google", "VIDEO")),
      ("Online Video", True))
check("every Bing channel product is a catalog product",
      [p for p in _products.BING_CHANNEL_PRODUCTS.values() if p not in _products.PRODUCTS], [])
check("the unmapped queue opens on the channel's product",
      {(u["campaign_id"], u.get("channel_type")) for u in store.unmapped_campaigns(days=365) if u["platform"] == "bing"}
      >= {("c1", "SEARCH"), ("c2", "AUDIENCE")})


# ---------------------------------------------------------- parsing edges
section("The parse: header block found, footer dropped, nothing invented")

parsed = bing.parse_report(CSV_TEXT)
check("the column row is found below the report's own header lines",
      (len(parsed["rows"]), parsed["skipped"], parsed["error"]), (3, 2, ""))
check("...the copyright footer is not a row", all(r["campaign_id"] in ("c1", "c2", "c3") for r in parsed["rows"]))
check("a report with no column row is refused in words", "no column row" in bing.parse_report("a,b\n1,2\n")["error"])
check("...and an empty file too", "empty" in bing.parse_report("")["error"])
check("a bare CSV is read as it is; a ZIP's first .csv member is read",
      (bing_ads.report_text(b"TimePeriod,x\n"), bing_ads.report_text(_zip("TimePeriod,y\n"))),
      ("TimePeriod,x\n", "TimePeriod,y\n"))


# ------------------------------------------------------------ refusals
section("A refusal names the environment; nothing carries a credential")

ANSWERS.append(_Resp(401, {"OperationErrors": [{"Code": 105, "ErrorCode": "InvalidCredentials",
                                                 "Message": f"Authentication failed for token {ACCESS}"}]}))
res = bing.pull(days=2, today=date(2026, 9, 10), sleep=lambda s: None)
check("a refused credential is ok=False naming the host it was refused on",
      res["ok"] is False and "production host" in res["error"] and "InvalidCredentials" in res["error"])
check("...never carrying the token the platform quoted", _secrets_in(res), [])
check("...and the watermark carries the redacted error", "[redacted]" in store.sync_status()["bing"]["error"]
      and _secrets_in(store.sync_status()["bing"]) == [])
check("...and so does the status line", "InvalidCredentials" in bing.status()["line"] and _secrets_in(bing.status()) == [])

ANSWERS.extend([_Resp(200, {"Accounts": [{"Id": 111, "Name": "A"}]}), _Resp(200, {"ReportRequestId": "req-2"}),
                _Resp(200, {"ReportRequestStatus": {"Status": "Error", "ReportDownloadUrl": None}})])
res = bing.pull(days=2, today=date(2026, 9, 10), sleep=lambda s: None)
check("a report the platform reports as failed is a refusal in words", "reported the report request as failed" in res["error"])
ANSWERS.extend([_Resp(200, {"Accounts": [{"Id": 111, "Name": "A"}]}), _Resp(200, {"ReportRequestId": "req-3"}),
                _Resp(200, {"ReportRequestStatus": {"Status": "Success", "ReportDownloadUrl": ""}})])
res = bing.pull(days=2, today=date(2026, 9, 10), sleep=lambda s: None)
check("a Success with no download URL is a refusal, not a KeyError", "no download URL" in res["error"])
ANSWERS.extend([_Resp(200, {"Accounts": []})])
res = bing.pull(days=2, today=date(2026, 9, 10), sleep=lambda s: None)
check("a manager with no accounts under it says so", "no advertiser accounts" in res["error"])
ANSWERS.extend([_Resp(200, {"Accounts": [{"Id": 111}]}), _Resp(500, None, content=b"<html>oops</html>")])
res = bing.pull(days=2, today=date(2026, 9, 10), sleep=lambda s: None)
check("a non-JSON refusal is said with its status", "HTTP 500" in res["error"])
check("an unknown poll status reads as pending, never as finished",
      bing_ads.poll_report.__doc__ and "Pending" in bing_ads.poll_report.__doc__)
check("the queue is drained", ANSWERS, [])


# ---------------------------------------------------------- the budget
section("Past the wait budget the report is pending, not failed")

clock = [0.0]
slept = []


def _tick(seconds):
    slept.append(seconds)
    clock[0] += seconds + 1


# A good pull first, so there is a native watermark to leave untouched.
ANSWERS.extend([_Resp(200, {"Accounts": [{"Id": 111, "Name": "A"}]}), _Resp(200, {"ReportRequestId": "req-4"}),
                _Resp(200, {"ReportRequestStatus": {"Status": "Success", "ReportDownloadUrl": "https://d/x.zip"}})])
res = bing.pull(days=2, today=date(2026, 9, 10), sleep=lambda s: None)
check("a good pull lands", res["ok"], True)
before_wm = dict(store.sync_status()["bing"])
pending = _Resp(200, {"ReportRequestStatus": {"Status": "Pending"}})
ANSWERS.extend([_Resp(200, {"Accounts": [{"Id": 111, "Name": "A"}]}), _Resp(200, {"ReportRequestId": "req-5"}),
                pending, pending, pending, pending])
res = bing.pull(days=2, today=date(2026, 9, 10), sleep=_tick, clock=lambda: clock[0], budget=20)
check("past the wait budget the report is pending rather than failed",
      (res["pending"], res["ok"], res["rows"]), (True, False, 0))
check("...saying so, with the seconds spent", "still preparing after 18s" in res["error"])
check("...after exactly the waits the budget had room for", slept, [5, 5, 5])
check("...every queued answer was consumed", ANSWERS, [])
check("the watermark is untouched: nothing landed and nothing failed", store.sync_status()["bing"], before_wm)
check("...so the provider normalize still defers to the last good pull", store.native_is_current("bing"), True)
check("the module's own note carries pending", bing._remembered().get("pending"), True)
check("...and the index line says so", "still preparing" in bing.status()["line"])
check("the budget is a house number beside the polls",
      (bing.BUDGET_SECONDS, bing.POLL_WAIT * bing.POLL_TRIES), (20, 30))

ANSWERS.extend([_Resp(200, {"Accounts": [{"Id": 111, "Name": "A"}]}), _Resp(200, {"ReportRequestId": "req-6"})]
               + [pending] * 8)
res = bing.pull(days=2, today=date(2026, 9, 10), sleep=lambda s: None)
check("a report that never finishes is refused by name after the polls, whatever the clock says",
      "still in progress" in res["error"] and not res["pending"])
check("...and that one IS on the watermark", "still in progress" in store.sync_status()["bing"]["error"])
check("...after exactly POLL_TRIES polls, leaving the rest queued", len(ANSWERS), 8 - bing.POLL_TRIES)
ANSWERS.clear()


# ------------------------------------------------------------ reconcile
section("The reconcile reads the month back as an account report, a re-read")

from modules.reports import reconcile                                # noqa: E402
ACCT_CSV = ('AccountId,AccountName,CurrencyCode,Spend,Impressions,Clicks\n'
            '111,Acme Plumbing,USD,"1,234.56",50000,700\n'
            '222,Riverside HVAC,USD,100,2000,30\n'
            '©2026 Microsoft Corporation. All rights reserved.\n')
requests.get = lambda url, timeout=None, **kw: _Resp(200, None, content=_zip(ACCT_CSV))
ANSWERS.extend([_Resp(200, {"Accounts": [{"Id": 111, "Name": "A"}, {"Id": 222, "Name": "B"}]}),
                _Resp(200, {"ReportRequestId": "req-7"}),
                _Resp(200, {"ReportRequestStatus": {"Status": "Success", "ReportDownloadUrl": "https://d/a.zip"}})])
their = reconcile.theirs("bing", date(2026, 9, 1), date(2026, 9, 10))
check("measured, from an account-level Summary report", (their["measured"], their["independent"]), (True, False))
check("...summed across the accounts", (str(their["spend"]), their["impressions"], their["clicks"]), ("1334.56", 52000, 730))
check("...and labeled as the re-read it is", "account report" in their["label"] and "2 accounts" in their["label"])
rr = CALLS[-2]["body"]["ReportRequest"]
check("the request was an AccountPerformanceReportRequest, Summary aggregation, both accounts",
      (rr["Type"], rr["Aggregation"], rr["Scope"]["AccountIds"]), ("AccountPerformanceReportRequest", "Summary", [111, 222]))
check("bing is not among the platforms declared unmeasurable", "bing" not in reconcile.NOT_MEASURABLE)
ANSWERS.append(_Resp(401, {"OperationErrors": [{"Code": 105, "Message": "Authentication failed"}]}))
their = reconcile.theirs("bing", date(2026, 9, 1), date(2026, 9, 10))
check("a refusal is not measured with the reason, not an exception",
      their["measured"] is False and "refused" in their["reason"])
ANSWERS.clear()

bing_ads._http, requests.get, requests.post, quotas.record = _real_http, _real_get, _real_post, _real_record


# ---------------------------------------------------------- the scheduler
section("The scheduler job, the index, the marker and the docs")

from hub import scheduler                                            # noqa: E402
src = (ROOT / "hub" / "scheduler.py").read_text(encoding="utf-8")
body = src[src.index("def job_reports_native_pull"):src.index("\nJOBS = {")]
check("the native job pulls Microsoft Ads in the same loop", '("bing", bing.pull)' in body)
_saved = {k: os.environ.pop(k) for k in BING_VARS if k in os.environ}
from flask import Flask                                              # noqa: E402
out = scheduler.JOBS["reports_native"][1](Flask("t"))
check("unconfigured, the job skips it by name", "bing" in out["skipped"])
check("...with no error", "bing" not in out["errors"])
os.environ.update(_saved)
_real_pull = bing.pull
bing.pull = lambda **kw: {"ok": False, "rows": 0, "pending": True, "error": "the report was still preparing after 18s"}
try:
    out = scheduler.JOBS["reports_native"][1](Flask("t"))
finally:
    bing.pull = _real_pull
check("a pending report is counted apart from the failures", "bing" in out["pending"] and "bing" not in out["errors"])

app_src = (ROOT / "modules" / "reports" / "app.py").read_text(encoding="utf-8")
check("the index prints the status line", '("bing", "bing")' in app_src)
from modules.reports import app as reports_app                       # noqa: E402
lines = {n["platform"]: n for n in reports_app._native_status()}
check("...and the line is the module's own", lines["bing"]["line"].startswith("Microsoft Ads:"))

check("hub/quotas.py has a marker for the two REST hosts",
      "microsoft_ads" in quotas._PROVIDER_MARKERS and quotas._PROVIDER_MARKERS["microsoft_ads"]["calls"](
          'requests.post("https://clientcenter.api.bingads.microsoft.com/x")'))
check("...that the token endpoint does not trip",
      quotas._PROVIDER_MARKERS["microsoft_ads"]["calls"]('requests.post("https://login.microsoftonline.com/x")'), False)
blind = quotas.untracked_provider_calls(force=True)
check("no unrecorded Microsoft Ads call site", blind.get("microsoft_ads"), [])
check("...and the usage page has a row for it, unmeasured against a ceiling",
      (quotas.QUOTAS["microsoft_ads"].unit, quotas.QUOTAS["microsoft_ads"].thresholds()), ("calls", (0, 0)))

for f in ("env.example", "render.yaml"):
    text = (ROOT / f).read_text(encoding="utf-8")
    check(f"{f} documents the five names as set", all(n in text for n in bing_ads.REQUIRED + ("BING_MANAGER_ACCOUNT_NUMBER",)))
    # A variable, not a sentence: both files EXPLAIN that there is no twin.
    check(f"...and no BING_ADS_ twin", re.search(r"^\s*(- key: )?BING_ADS_", text, re.M) is None)
cfg_src = (ROOT / "hub" / "config.py").read_text(encoding="utf-8")
from hub import config as hub_config                                 # noqa: E402
check("hub/config.py reads the single-spelling names under exactly that name",
      all(f'_s("{n}")' in cfg_src for n in ("BING_AD_DEVELOPER_TOKEN", "BING_MANAGER_ACCOUNT_ID",
                                             "BING_MANAGER_ACCOUNT_NUMBER", "MICROSOFT_ADS_TENANT",
                                             "MICROSOFT_ADS_REDIRECT_URI")))
# The app registration is the one pair with two spellings in use on Render,
# and ALIASES names exactly those two -- no BING_ADS_ twin, no third guess.
check("...and the app registration under exactly the two spellings in use",
      (hub_config.ALIASES.get("bing_client_id"), hub_config.ALIASES.get("bing_client_secret")),
      (("BING_AD_CLIENT_ID", "MICROSOFT_ADS_CLIENT_ID"), ("BING_AD_CLIENT_SECRET", "MICROSOFT_ADS_CLIENT_SECRET")))
_alias_block = cfg_src[cfg_src.index("ALIASES: dict"):cfg_src.index("\n}\n", cfg_src.index("ALIASES: dict"))]
check("...and no other Bing name in ALIASES",
      [ln for ln in _alias_block.splitlines() if "BING" in ln and "BING_AD_CLIENT_" not in ln and not ln.strip().startswith("#")], [])
check("...which the client reads through the same rows",
      {k: v[0] for k, v in bing_ads.ALIAS_KEYS.items()}, {"BING_AD_CLIENT_ID": "bing_client_id", "BING_AD_CLIENT_SECRET": "bing_client_secret"})
for f in ("env.example", "render.yaml"):
    check(f"{f} documents the second spelling and the two optional settings",
          all(n in (ROOT / f).read_text(encoding="utf-8") for n in
              ("MICROSOFT_ADS_CLIENT_ID", "MICROSOFT_ADS_CLIENT_SECRET", "MICROSOFT_ADS_REDIRECT_URI", "MICROSOFT_ADS_TENANT")))
rep = {r["name"]: r for r in hub_config.settings.status()}
check("/status has a Microsoft Ads row naming all four, and the second spelling", "Microsoft Ads" in rep
      and all(n in rep["Microsoft Ads"]["note"] for n in bing_ads.REQUIRED + ("MICROSOFT_ADS_CLIENT_ID",)))
from hub import help as hub_help                                     # noqa: E402
check("the settings card's bubble is registered", "ads_builder.settings.bing" in {h.key for h in hub_help.REGISTRY})


# ------------------------------------------------------------ Smart 1 Ads
section("Smart 1 Ads: the card, the callback, disconnect, /api/status")

from modules.ads_builder import app as ads                           # noqa: E402
client = ads.app.test_client()
ENV = {"s1hub.user": "Todd"}

html = client.get("/settings", environ_base=ENV).get_data(as_text=True)
check("the settings page draws the Microsoft card connected", "Microsoft Advertising (Bing)" in html and "Disconnect" in html)
check("...with the callback to register", "https://smart1.agency/tools/ads/oauth/bing/callback" in html)
check("...the manager account and its number", "12345678" in html and "No. X0123456" in html)
check("...saying what the connection buys and what it does not",
      "native pull" in html and "not built" in html)
check("...and no phase-two placeholder", "Phase 2" not in html)
# The standalone app has no help_dot global, so the key is read off the
# template; test_ads_explainer holds every key a template places to the registry.
check("...the template places its bubble's key",
      "ads_builder.settings.bing" in (ROOT / "modules" / "ads_builder" / "templates" / "ads_settings.html").read_text(encoding="utf-8"))
check("...and no credential", _secrets_in(html), [])

r = client.post("/api/bing/disconnect", environ_base=ENV)
check("disconnect clears the stored token and names the pin variable",
      (r.status_code, ads_store.get_setting("bing_refresh_token"), "BING_AD_REFRESH_TOKEN" in r.get_json()["note"]),
      (200, "", True))
html = client.get("/settings", environ_base=ENV).get_data(as_text=True)
check("...after which the card offers Connect", "Connect Microsoft Ads" in html and "/connect/bing" in html)

r = client.get("/connect/bing", environ_base=ENV)
check("Connect sends the browser to Microsoft with a state cookie",
      (r.status_code, r.headers["Location"].startswith(bing_ads.AUTH_URL), "s1ads_bing_oauth_state" in r.headers.get("Set-Cookie", "")),
      (302, True, True))
state = parse_qs(urlsplit(r.headers["Location"]).query)["state"][0]

EXCHANGED = []
_real_exchange = bing_ads.exchange_code


def fake_exchange(code):
    EXCHANGED.append(code)
    return {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600}


bing_ads.exchange_code = fake_exchange
try:
    r = client.get(f"/oauth/bing/callback?code=auth-code-1&state={state}", environ_base=ENV)
    html = r.get_data(as_text=True)
    check("the callback exchanges the code once", (r.status_code, EXCHANGED), (200, ["auth-code-1"]))
    check("...keeps the refresh token in the settings table", ads_store.get_setting("bing_refresh_token"), REFRESH)
    check("...shows it once against the Microsoft pin variable",
          REFRESH in html and "BING_AD_REFRESH_TOKEN" in html and "GOOGLE_ADS_REFRESH_TOKEN" not in html)
    check("...naming the provider and what it buys", "Microsoft Advertising is connected" in html and "reports module" in html)
    check("...and the state cookie is cleared", "s1ads_bing_oauth_state=;" in r.headers.get("Set-Cookie", ""))
    client.set_cookie("s1ads_bing_oauth_state", state)
    r = client.get("/oauth/bing/callback?code=x&state=wrong", environ_base=ENV)
    check("a state mismatch is refused", (r.status_code, "state mismatch" in r.get_data(as_text=True).lower()), (400, True))
    r = client.get("/oauth/bing/callback?error=access_denied", environ_base=ENV)
    check("a cancelled consent is refused in words", (r.status_code, "cancelled" in r.get_data(as_text=True)), (400, True))
    r = client.get("/oauth/bing/callback", environ_base=ENV)
    html = r.get_data(as_text=True)
    check("the callback opened with no code says where sign-in starts, and what a mismatch looks like",
          (r.status_code, "Connect Microsoft Ads" in html, "redirect URI" in html, EXCHANGED), (400, True, True, ["auth-code-1"]))
finally:
    bing_ads.exchange_code = _real_exchange

r = client.get("/api/status", environ_base=ENV).get_json()
check("/api/status carries the connection in the Google card's shape",
      (r["bing"]["configured"], r["bing"]["connected"], r["bing"]["deploy_ready"], "note" in r["bing"]), (True, True, True, True))
check("...and no credential", _secrets_in(r), [])
r = client.get("/api/bing/campaigns", environ_base=ENV)
check("campaign management still answers 501, saying what is built", (r.status_code, "not built" in r.get_json()["error"]), (501, True))

os.environ.pop("BING_AD_CLIENT_ID")
r = client.get("/connect/bing", environ_base=ENV)
check("Connect refuses by name until the variables are set",
      (r.status_code, "BING_AD_CLIENT_ID" in r.get_data(as_text=True)), (400, True))
html = client.get("/settings", environ_base=ENV).get_data(as_text=True)
check("...and the card names what is missing rather than offering the button",
      "BING_AD_CLIENT_ID" in html and "Connect Microsoft Ads" not in html)
os.environ["BING_AD_CLIENT_ID"] = "app-client-id"
os.environ["MICROSOFT_ADS_REDIRECT_URI"] = "https://smart1-hub.onrender.com/auth/microsoft/callback"
r = client.get("/connect/bing", environ_base=ENV)
check("Connect refuses a pinned redirect at a page this Hub does not answer, by name",
      (r.status_code, "MICROSOFT_ADS_REDIRECT_URI" in r.get_data(as_text=True)), (400, True))
html = client.get("/settings", environ_base=ENV).get_data(as_text=True)
check("...and the card says so rather than offering the button",
      "MICROSOFT_ADS_REDIRECT_URI points somewhere" in html and "Connect Microsoft Ads" not in html)
os.environ["MICROSOFT_ADS_REDIRECT_URI"] = "https://smart1-hub.onrender.com/tools/ads/oauth/bing/callback"
html = client.get("/settings", environ_base=ENV).get_data(as_text=True)
check("...and a pin at the right path is printed as the URI in use, with no finding",
      "smart1-hub.onrender.com/tools/ads/oauth/bing/callback" in html
      and "from MICROSOFT_ADS_REDIRECT_URI" in html and "points somewhere" not in html)
os.environ.pop("MICROSOFT_ADS_REDIRECT_URI")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
