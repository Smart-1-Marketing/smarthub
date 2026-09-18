"""The config-driven CallRail pull, its check page, and the Phone calls tile.

    python3 test_reports_callrail.py

No pytest, no new dependencies, a throwaway SQLite reports database
(REPORTS_TEST_DATABASE_URL points it at Postgres in CI's second run) and a
stand-in for the platform: ``callrail._http`` is replaced whole. Through
the composed app for the check page, because it is a staff screen behind
the guard, and for the client's page, because the calls tile is the one
thing here a client reads.

What it holds:

  * unconfigured is a sentence, not an exception, and the sentence says
    WHICH half is owed: with the key set and the origin not, the line
    names CALLRAIL_API_BASE, the pull and the check page refuse, and
    NOTHING is called, because a request to a host nobody confirmed
    carries the key in its header;
  * the key is read under exactly CALLRAIL_API_KEY, through hub/config.py,
    with no alias beside it, and travels as Authorization: Token token="...";
  * the call is shaped entirely by callrail_map.py -- paths, header,
    parameter names, the fields asked for -- and an environment override
    changes the call with no edit here;
  * the key is never in a result, an error, a watermark, a quota row or
    the page;
  * accounts are listed, calls are read page by page, and what lands is
    one row per company, source and day with the counts in extras under
    their own names, spend and impressions and clicks and conversions all
    zero, outbound calls counted and left out; a read that stopped short
    says so on the watermark; calls read and none filed is a failure by
    name; a body the map does not resolve against is a refusal by name;
  * every request is recorded under the callrail quota row, the marker
    holds, and the sweep reports no unrecorded call site;
  * the check page lists the accounts, prints the raw keys with the
    caller's own details masked, says whether the map resolves, and with
    the origin unset says nothing was called;
  * the client's page draws a Phone calls tile from those rows, names no
    vendor, draws no bar or table row for them, and data.json and the PDF
    carry the same tile; a row with no calls figure draws no tile at all;
    the cost report carries no $0 column for them;
  * the nightly job skips it cleanly on this deployment's state, /status
    names the key, /diagnostics reads its states, the docs name the
    variables, and the help bubble is registered.
"""
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_cr_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-cr-test"
os.environ["PUBLIC_BASE_URL"] = "https://hub.example.test"
os.environ.pop("PANEL_PASSWORD", None)
for k in list(os.environ):
    if k.startswith(("CALLRAIL_", "GROUND_TRUTH_", "AUDIOGO_", "STACKADAPT_", "TTD_", "BING_")):
        os.environ.pop(k)

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


from werkzeug.test import Client                                     # noqa: E402
import wsgi                                                          # noqa: E402
from hub import auth, quotas                                         # noqa: E402
from hub import config as hub_config                                 # noqa: E402
from modules.reports import callrail, callrail_map, store            # noqa: E402

_reports_testdb.reset(store)

KEY = "cr-live-key-7b2e91-never-on-screen"
TODAY = date.today()
D1 = TODAY - timedelta(days=1) if TODAY.day > 1 else TODAY
PHONE = "+15551234567"


def _secrets_in(text) -> bool:
    s = json.dumps(text, default=str) if not isinstance(text, str) else text
    return KEY in s


# ---------------------------------------------------------------- the fake
calls = []
ANSWERS = []


class _Resp:
    def __init__(self, status, payload, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or json.dumps(payload)
        self.content = self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def fake_http(method, url, *, headers, params, timeout=60):
    calls.append({"method": method, "url": url, "headers": dict(headers), "params": dict(params)})
    return ANSWERS.pop(0) if ANSWERS else _Resp(200, {"calls": [], "total_pages": 1})


_real_http = callrail._http
callrail._http = fake_http

RECORDED = []
_real_record = quotas.record


def fake_record(provider, **kw):
    RECORDED.append({"provider": provider, **kw})


quotas.record = fake_record

ACCOUNTS = {"page": 1, "total_pages": 1, "accounts": [{"id": "ACC1", "name": "Smart 1 Marketing"}]}


def _call(company="COM1", company_name="Acme Tire", source="Google Ads", when=None,
          direction="inbound", answered=True, voicemail=False, first_call=False,
          lead_status=None, duration=61):
    return {"id": "CAL1", "start_time": when or f"{D1.isoformat()}T10:15:00.000-04:00",
            "company_id": company, "company_name": company_name, "source": source,
            "direction": direction, "answered": answered, "voicemail": voicemail,
            "first_call": first_call, "lead_status": lead_status, "duration": duration,
            "customer_phone_number": PHONE, "customer_name": "Pat Caller",
            "tracking_phone_number": "+15559876543", "recording": "https://x.test/rec.mp3"}


# --------------------------------------------------------- unconfigured
section("Unconfigured is a sentence, not an exception")

st = callrail.status()
check("not configured", st["configured"], False)
check("...naming both variables", st["missing"], ["CALLRAIL_API_KEY", "CALLRAIL_API_BASE"])
check("...on the line", st["line"], "CallRail: not configured: CALLRAIL_API_KEY, CALLRAIL_API_BASE unset")
check("...and it points at the check page", st["check_page"], "/reports/callrail-check")
res = callrail.pull()
check("a pull returns rather than raising", (res["ok"], res["error"].startswith("not configured")), (False, True))
check("...and wrote no watermark", store.sync_status().get("callrail"), None)
check("...and called nothing", calls, [])
chk = callrail.check(today=date(2026, 9, 15))
check("the check says what to set", chk["error"], "not configured: CALLRAIL_API_KEY, CALLRAIL_API_BASE unset")
check("...for yesterday", chk["day"], "2026-09-14")
check("the map declares itself a placeholder", callrail_map.config()["placeholder"], True)
src = (ROOT / "modules" / "reports" / "callrail_map.py").read_text(encoding="utf-8")
check("...in the file, loudly", src.count("PLACEHOLDER") >= 8)
check("...and the origin is the placeholder that is never called",
      "never called" in src and callrail_map.config()["base"] == "")
check("...and files nothing as a conversion", callrail_map.FIELDS["conversions"], None)


# ------------------------------------- the key alone: this deployment's state
section("The key alone: the origin is what is owed, and nothing is called")

os.environ["CALLRAIL_API_KEY"] = KEY
st = callrail.status()
check("with the key set, the origin is the one thing missing", st["missing"], ["CALLRAIL_API_BASE"])
check("...and the line says which half is owed",
      st["line"].startswith("CallRail: key set; CALLRAIL_API_BASE unset") and "sent nowhere" in st["line"])
check("...not 'not configured', which sends somebody to check a key that is set",
      "not configured" not in st["line"])
res = callrail.pull(days=3, today=date(2026, 9, 15))
check("the pull refuses by name", (res["ok"], res["error"]), (False, "not configured: CALLRAIL_API_BASE unset"))
check("...and NOTHING was called", calls, [])
chk = callrail.check(today=date(2026, 9, 15))
check("the check calls nothing either", (calls, chk["answer"], chk["accounts"]), ([], None, None))
check("...its request says the origin is not set", chk["request"]["base_set"], False)
check("...and draws the documented origin for the eye only",
      chk["request"]["url"].startswith(callrail_map.DEFAULT_BASE)
      and callrail_map.DEFAULT_BASE == "https://api.callrail.com")
check("the key is read through hub/config.py under exactly that spelling", hub_config._s("CALLRAIL_API_KEY"), KEY)
check("...and the settings field reads the same variable", type(hub_config.settings)().callrail_key, KEY)
cfg_src = (ROOT / "hub" / "config.py").read_text(encoding="utf-8")
aliases = cfg_src[cfg_src.index("ALIASES: dict"):cfg_src.index("\n}\n", cfg_src.index("ALIASES: dict"))]
check("hub/config.py invents no alias for it",
      "CALLRAIL" not in aliases and '_s("CALLRAIL_KEY' not in cfg_src and '_s("CALL_RAIL' not in cfg_src)
rep = {r["name"]: r for r in type(hub_config.settings)().status()}
check("/status has a CallRail row naming the key and the origin",
      "CallRail" in rep and "CALLRAIL_API_KEY" in rep["CallRail"]["note"]
      and "CALLRAIL_API_BASE" in rep["CallRail"]["note"])
from hub import diagnostics                                          # noqa: E402
check("/diagnostics reads the key-alone state as a warning naming the origin",
      (diagnostics.check_callrail().state, "CALLRAIL_API_BASE" in diagnostics.check_callrail().detail),
      ("warn", True))
check("...and the check is on the list", diagnostics.check_callrail in diagnostics.CHECKS)
os.environ.pop("CALLRAIL_API_KEY")
check("...with nothing set it is off", diagnostics.check_callrail().state, "off")
os.environ["CALLRAIL_API_KEY"] = KEY


# ------------------------------------------------------- the call shape
section("The call is shaped by the map and nothing else")

os.environ["CALLRAIL_API_BASE"] = "https://api.callrail.test/"
check("with both set, configured", callrail.missing(), [])
check("...and /diagnostics says nothing has been pulled yet",
      (diagnostics.check_callrail().state, "nothing has been pulled" in diagnostics.check_callrail().detail),
      ("warn", True))
c = callrail_map.config()
acc = callrail.accounts_request_shape()
check("the accounts URL is the origin plus the map's path", acc["url"], "https://api.callrail.test" + c["accounts_path"])
shape = callrail.request_shape("ACC1", date(2026, 9, 1), date(2026, 9, 10), page=2)
check("the calls URL is the origin plus the map's path with the account in it",
      shape["url"], "https://api.callrail.test" + c["calls_path"].replace("{account_id}", "ACC1"))
check("...the origin is the set one, said so", shape["base_set"], True)
check("...the method is the map's", shape["method"], c["method"])
check("...the date params carry the map's names",
      (shape["params"][c["date_params"]["start"]], shape["params"][c["date_params"]["end"]]),
      ("2026-09-01", "2026-09-10"))
check("...the page and its size", (shape["params"]["page"], shape["params"]["per_page"]), (2, 250))
check("...and the optional fields the map wants", shape["params"]["fields"], c["request_fields"])
check("the key rides on the Authorization header in the reference's Token shape",
      callrail.headers()["Authorization"], f'Token token="{KEY}"')
check("...and request_shape never carries it", not _secrets_in(shape) and not _secrets_in(acc))

os.environ["CALLRAIL_CALLS_PATH"] = "/v9/{account_id}/rings.json"
os.environ["CALLRAIL_AUTH_HEADER"] = "X-API-Key"
os.environ["CALLRAIL_AUTH_FORMAT"] = "{key}"
os.environ["CALLRAIL_PARAM_START"] = "from"
os.environ["CALLRAIL_FIELD_DATE"] = "rang_at"
check("an environment override moves the path", callrail.request_shape("A", date(2026, 9, 1), date(2026, 9, 1))["url"],
      "https://api.callrail.test/v9/A/rings.json")
check("...the header and its shape", callrail.headers().get("X-API-Key"), KEY)
check("...a date parameter name", "from" in callrail.request_shape("A", date(2026, 9, 1), date(2026, 9, 1))["params"])
check("...and a field name", callrail_map.config()["fields"]["date"], "rang_at")
for k in ("CALLRAIL_CALLS_PATH", "CALLRAIL_AUTH_HEADER", "CALLRAIL_AUTH_FORMAT",
          "CALLRAIL_PARAM_START", "CALLRAIL_FIELD_DATE"):
    os.environ.pop(k)


# ------------------------------------------------------------- the pull
section("Accounts, then calls page by page, counted by company, source and day")

PAGE1 = {"page": 1, "total_pages": 2, "calls": [
    _call(answered=True, first_call=True, lead_status="good_lead", duration=90),
    _call(answered=False, voicemail=False, duration=0),
    _call(answered=False, voicemail=True, duration=12),
    _call(source="Google Organic", answered=True, duration=30),
]}
PAGE2 = {"page": 2, "total_pages": 2, "calls": [
    _call(direction="outbound"),                              # the client's own staff calling out
    {**_call(), "start_time": None},                          # no day: skipped and counted
    _call(when="09/16/2026 10:00"),                           # an unreadable day costs that call only
]}
ANSWERS.extend([_Resp(200, ACCOUNTS), _Resp(200, PAGE1), _Resp(200, PAGE2)])
res = callrail.pull(days=3, today=TODAY)
check("the pull is ok", (res["ok"], res["error"]), (True, ""))
check("...one account, seven calls read, two rows filed", (res["accounts"], res["calls"], res["rows"]), (1, 7, 2))
check("...the outbound call counted apart, the two unreadable ones skipped", (res["outbound"], res["skipped"]), (1, 2))
check("three requests: the accounts, then both pages",
      [(x["url"].split("callrail.test")[-1], x["params"].get("page")) for x in calls],
      [(c["accounts_path"], None), (c["calls_path"].replace("{account_id}", "ACC1"), 1),
       (c["calls_path"].replace("{account_id}", "ACC1"), 2)])
check("...over a thirty-day window by default", callrail.DAYS, 30)
check("...with the key in the header only",
      calls[-1]["headers"]["Authorization"].endswith(f'"{KEY}"') and not _secrets_in(calls[-1]["params"]))
db = store.SessionLocal()
try:
    rows = {r.campaign_id: r for r in db.query(store.AdPerfDaily).filter(store.AdPerfDaily.platform == "callrail").all()}
    check("one row per source, the source slugged as the campaign id", sorted(rows), ["google-ads", "google-organic"])
    g = rows["google-ads"]
    check("platform callrail, source native, the company as the account",
          (g.platform, g.source, g.account_id, g.campaign_name, g.date), ("callrail", "native", "COM1", "Google Ads", D1))
    check("...no delivery and no spend on the row",
          (str(g.spend), g.impressions, g.clicks, float(g.conversions or 0), g.completes), ("0.00", 0, 0, 0.0, None))
    check("...the counts in extras under their own names",
          {k: g.extras[k] for k in ("calls", "answered", "missed", "voicemail", "first_time_calls",
                                    "good_leads", "duration_seconds")},
          {"calls": 3, "answered": 1, "missed": 1, "voicemail": 1, "first_time_calls": 1,
           "good_leads": 1, "duration_seconds": 102})
    check("...the company's name beside them for the likeness pass",
          (g.extras["advertiser_name"], g.extras["source"]), ("Acme Tire", "Google Ads"))
    check("...and the caller's own details on none of it",
          PHONE not in json.dumps(g.extras) and "Pat Caller" not in json.dumps(g.extras))
    check("the organic row counts its one call", rows["google-organic"].extras["calls"], 1)
finally:
    db.close()
check("the native watermark is stamped", store.sync_status()["callrail"]["source"], "native")
check("...so the provider normalize defers to it", store.native_is_current("callrail"), True)
check("...and /diagnostics reads it as ok, with the rows",
      (diagnostics.check_callrail().state, "wrote 2 rows" in diagnostics.check_callrail().detail), ("ok", True))
by_api = {}
for r in RECORDED:
    by_api[r.get("api")] = by_api.get(r.get("api"), 0) + 1
check("every request was recorded under callrail", {r["provider"] for r in RECORDED}, {"callrail"})
check("...the accounts call apart from the pages", by_api, {"accounts": 1, "calls": 2})
check("...filed under the reports module, all ok", ({r["module"] for r in RECORDED}, {r["ok"] for r in RECORDED}),
      ({"reports"}, {True}))
check("...with no credential in the detail", not _secrets_in(RECORDED))

ANSWERS.extend([_Resp(200, ACCOUNTS),
                _Resp(200, {"calls": [{"when": "2026-09-10", "id": "x", "who": "COM1"}], "total_pages": 1})])
res = callrail.pull(days=3, today=TODAY)
check("a body the map does not resolve against lands nothing", (res["ok"], res["rows"]), (False, 0))
check("...and names the missing fields", "does not resolve" in res["error"] and "start_time" in res["error"])
check("...on the watermark", "does not resolve" in store.sync_status()["callrail"]["error"])
check("...pointing at the check page", "callrail-check" in res["error"])
check("...and /diagnostics carries the pull's own error",
      (diagnostics.check_callrail().state, "does not resolve" in diagnostics.check_callrail().detail),
      ("warn", True))

ANSWERS.extend([_Resp(200, ACCOUNTS),
                _Resp(200, {"calls": [{**_call(), "start_time": ""}, {**_call(), "company_id": ""}], "total_pages": 1})])
res = callrail.pull(days=3, today=TODAY)
check("calls read and none filed is a failure by name, never an ok with zero rows",
      (res["ok"], res["rows"], "2 calls read and none filed" in res["error"]), (False, 0, True))

ANSWERS.append(_Resp(403, None, text=f"forbidden for key {KEY}"))
res = callrail.pull(days=3, today=TODAY)
check("a refusal is a sentence", "HTTP 403" in res["error"])
check("...never carrying the key", not _secrets_in(res) and not _secrets_in(store.sync_status()))
check("...and neither does status()", not _secrets_in(callrail.status()))
check("...and it was recorded as refused", RECORDED[-1]["ok"], False)

ANSWERS.extend([_Resp(200, ACCOUNTS), _Resp(200, {**PAGE1, "total_pages": 3})])
_pages = callrail.MAX_PAGES
callrail.MAX_PAGES = 1
res = callrail.pull(days=3, today=TODAY)
callrail.MAX_PAGES = _pages
check("a read that stopped short files what it read and says so",
      (res["ok"], res["rows"], "read stopped at 1 pages" in res["error"] and "ACC1" in res["error"]), (False, 2, True))
check("...on the watermark, rather than a clean night on a partial month",
      "read stopped" in store.sync_status()["callrail"]["error"])

ANSWERS.extend([_Resp(200, ACCOUNTS), _Resp(200, PAGE1), _Resp(200, PAGE2)])
res = callrail.pull(days=3, today=TODAY)
check("a good pull after a refusal clears the watermark", (res["ok"], store.sync_status()["callrail"]["error"]),
      (True, ""))

n = len(calls)
os.environ["CALLRAIL_ACCOUNT_ID"] = "ACC9"
ANSWERS.append(_Resp(200, {"calls": [], "total_pages": 1}))
res = callrail.pull(days=3, today=TODAY)
check("a pinned account skips the accounts call", (len(calls) - n, "ACC9" in calls[-1]["url"]), (1, True))
check("...and a window with no calls is ok and lands nothing", (res["ok"], res["rows"], res["calls"]), (True, 0, 0))
os.environ.pop("CALLRAIL_ACCOUNT_ID")


# --------------------------------------------------------- the check page
section("The check page lists the accounts, prints the raw keys, and masks the caller")

anon = Client(wsgi.application)
r = anon.get("/reports/callrail-check")
check("a stranger is refused", r.status_code in (302, 401))
staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")

ANSWERS.extend([_Resp(200, ACCOUNTS),
                _Resp(200, {"page": 1, "total_pages": 4,
                            "calls": [{"when": "2026-09-10", "id": "x", "who": "COM1",
                                       "customer_phone_number": PHONE, "note": "call me back"}]})])
r = staff.get("/reports/callrail-check")
page = r.get_data(as_text=True)
check("staff get the page", r.status_code, 200)
check("...calling the endpoint for yesterday, one small page",
      (calls[-1]["params"][c["date_params"]["start"]] == calls[-1]["params"][c["date_params"]["end"]],
       calls[-1]["params"]["per_page"]), (True, 25))
check("...listing the accounts the key sees", "ACC1" in page and "Smart 1 Marketing" in page)
check("...printing the top-level keys", "calls, page, total_pages" in page)
check("...and the row keys the endpoint answered with", all(k in page for k in ("when", "who", "customer_phone_number")))
check("...with the caller's own details masked", PHONE not in page and "call me back" not in page and "[masked]" in page)
check("...saying the map does not resolve", "Does not resolve" in page)
check("...the request as configured", c["calls_path"].split("{")[0] in page and "Authorization" in page)
check("...and never the key", not _secrets_in(page))
check("...nor the origin-unset notice while the origin is set", "Nothing was called" not in page)
tpl = (ROOT / "modules" / "reports" / "templates" / "reports_callrail_check.html").read_text()
check("...with the help bubble guarded", "help_dot('reports.callrail.check') if help_dot is defined" in tpl)
from hub import help as hub_help                                     # noqa: E402
check("...and the bubble registered", hub_help.get("reports.callrail.check") is not None)
ANSWERS.extend([_Resp(200, ACCOUNTS), _Resp(200, PAGE1)])
page = staff.get("/reports/callrail-check").get_data(as_text=True)
check("against a body it resolves, the page says so", 'class="s1d-pill ok">Resolved' in page)
check("...saying how many calls the names were judged against, not just the first",
      "Judged against" in page and "rather than the first" in page)
check("...and the unread box measures what the map leaves on the row",
      "customer_phone_number" in page.split('id="unread-fields"')[-1])
index = staff.get("/reports/").get_data(as_text=True)
check("the index links the check page", "CallRail check" in index)
check("...and lists the pull as a native one, connected", "CallRail: configured" in index)
prov = staff.get("/reports/provider-check/callrail")
body = prov.get_data(as_text=True)
check("the provider page renders with the map and the Render list",
      (prov.status_code, "CALLRAIL_API_KEY" in body, "CALLRAIL_API_BASE" in body, "callrail_map.py" in body),
      (200, True, True, True))

n_calls = len(calls)
os.environ.pop("CALLRAIL_API_BASE")
page = staff.get("/reports/callrail-check").get_data(as_text=True)
check("with the origin unset the page says what to set rather than 500ing", "CALLRAIL_API_BASE unset" in page)
check("...says nothing was called", "Nothing was called" in page)
check("...printing the documented origin as the guess", "api.callrail.com" in page)
check("...with the unread box saying no row has come back", "not measured: no row has come back" in page)
check("...and nothing was", len(calls), n_calls)
index = staff.get("/reports/").get_data(as_text=True)
check("...and the index says which half is owed", "key set; CALLRAIL_API_BASE unset" in index)
os.environ["CALLRAIL_API_BASE"] = "https://api.callrail.test/"


# ------------------------------------------------------ the client's page
section("The client's page draws Phone calls, and names no vendor")

CLIENT = "d:acmetire.com"
NAME = "Acme Tire"
for cid in ("google-ads", "google-organic"):
    store.map_campaign("callrail", "COM1", cid, client=CLIENT, client_name=NAME,
                       product="Call Tracking", mapped_by="Todd")
store.upsert_rows([{"platform": "google", "account_id": "123", "campaign_id": "g-1",
                    "campaign_name": "S1M | acme | Paid Search | fall", "date": D1, "spend": "40.00",
                    "impressions": 2000, "clicks": 50, "conversions": 3, "source": "native"}])
store.map_campaign("google", "123", "g-1", client=CLIENT, client_name=NAME,
                   product="Paid Search", mapped_by="Todd")
link = store.create_link(CLIENT, client_name=NAME, created_by="Todd")
URL = f"/reports/r/c/{link.token}"
r = anon.get(URL)
html = r.get_data(as_text=True)
check("the page opens for a stranger", r.status_code, 200)
check("...with a Phone calls tile", "Phone calls" in html)
check("...naming no vendor", "callrail" not in html.lower() and "call rail" not in html.lower())
data = anon.get(URL + "/data.json").get_json()
tiles = {t["key"]: t for t in data.get("tiles", [])}
check("data.json carries the same tile, summed across the sources", (tiles.get("calls") or {}).get("value"), 4)
check("...labeled the same", (tiles.get("calls") or {}).get("label"), "Phone calls")
check("...and the search campaign's own figures beside it",
      (tiles["impressions"]["value"], tiles["clicks"]["value"]), (2000, 50))
check("no bar, no table row and no investment line for the calls: they are outcomes, not delivery",
      ([p["product"] for p in data["products"]], [t["product"] for t in data["table"]]),
      (["Paid Search"], ["Paid Search"]))
check("...and the search product's conversions are still a measurement", data["products"][0]["conversions"], 3)
r = anon.get(URL + ".pdf")
pdf = r.get_data()
check("the PDF renders", (r.status_code, pdf[:5]), (200, b"%PDF-"))
check("...carrying the tile", b"Phone calls" in pdf)

# The gate: a call-tracking row with no calls figure draws no tile.
store.upsert_rows([{"platform": "callrail", "account_id": "COM2", "campaign_id": "direct",
                    "campaign_name": "Direct", "date": D1, "spend": 0,
                    "impressions": 0, "clicks": 0, "source": "windsor"}])
store.map_campaign("callrail", "COM2", "direct", client="d:nocalls.test", client_name="No Calls Co",
                   product="Call Tracking", mapped_by="Todd")
link2 = store.create_link("d:nocalls.test", client_name="No Calls Co", created_by="Todd")
html2 = anon.get(f"/reports/r/c/{link2.token}").get_data(as_text=True)
check("a call-tracking row carrying no calls figure draws no Phone calls tile", "Phone calls" not in html2)
check("...rather than a measured nought", "No Calls Co" in html2)

from modules.reports import pacing, reconcile, products              # noqa: E402
cost = pacing.cost(f"{D1:%Y-%m}", TODAY)
mine = [r for r in cost.get("rows", []) if r["client"] == CLIENT]
check("the cost report carries no $0 column for the calls",
      (bool(mine), "callrail" not in (mine[0]["by_platform"] if mine else {"callrail": 1}),
       "callrail" not in cost.get("platforms", [])), (True, True, True))
check("...and the reconcile names the platform as not measurable, with the reason",
      "no spend to reconcile" in reconcile.NOT_MEASURABLE.get("callrail", ""))
check("the outcome platforms are named once, in the store", store.OUTCOME_PLATFORMS, ("suite", "callrail"))
check("...and the product the platform files under is in the catalog",
      products.DEFAULT_PRODUCT_FOR_PLATFORM["callrail"] in products.PRODUCTS
      and products.DEFAULT_PRODUCT_FOR_PLATFORM["callrail"] == "Call Tracking")


# ------------------------------------------------ the scheduler and the sweep
section("The nightly job, the marker, the docs")

callrail._http, quotas.record = _real_http, _real_record
for k in ("CALLRAIL_API_KEY", "CALLRAIL_API_BASE"):
    os.environ.pop(k, None)
from hub import scheduler                                            # noqa: E402
from flask import Flask                                              # noqa: E402
job = scheduler.JOBS["reports_native"][1]
check("the job's docstring names it", "CallRail" in (job.__doc__ or ""))
out = job(Flask("t"))
check("unconfigured, the nightly job skips it cleanly", "callrail" in out["skipped"] and out["errors"] == {})
check("the index lists it as a native pull",
      ("callrail", "callrail") in __import__("modules.reports.app", fromlist=["NATIVE_PULLS"]).NATIVE_PULLS)
from modules.reports import provider_fields                          # noqa: E402
check("...and the provider registry, with a check page and a Render list",
      ("callrail" in provider_fields.NATIVE, provider_fields.CHECK_PAGES.get("callrail"),
       provider_fields.ENV["callrail"][0]["name"], provider_fields.ENV["callrail"][0]["required"]),
      (True, "/reports/callrail-check", "CALLRAIL_API_KEY", True))
check("the quota row exists, in calls, unmeasured against a ceiling",
      (quotas.QUOTAS["callrail"].unit, quotas.QUOTAS["callrail"].thresholds()), ("calls", (0, 0)))
check("...and the marker is the domain", "callrail" in quotas._PROVIDER_MARKERS
      and quotas._PROVIDER_MARKERS["callrail"]["calls"]("requests.get('https://x.callrail.com')"))
blind = quotas.untracked_provider_calls(force=True)
check("no unrecorded CallRail call site", blind.get("callrail"), [])
for fname in ("env.example", "render.yaml"):
    text = (ROOT / fname).read_text(encoding="utf-8")
    check(f"{fname} documents the key and the origin",
          "CALLRAIL_API_KEY" in text and "CALLRAIL_API_BASE" in text)
    check(f"...and no CALLRAIL_KEY or CALL_RAIL_ twin",
          re.search(r"^\s*(- key: )?(CALLRAIL_KEY\b|CALL_RAIL_)", text, re.M) is None)
guide = "\n".join(p.read_text(encoding="utf-8") for p in
                  [ROOT / "CLAUDE.md", *sorted((ROOT / "docs" / "claude").glob("*.md"))])
check("the guide has the section", "## CallRail: a phone call is an outcome, not a conversion" in guide)


# ------------------------------------------- the readings a review found
section("The readings that would have looked like a working map")

os.environ["CALLRAIL_API_KEY"] = KEY
os.environ["CALLRAIL_API_BASE"] = "https://api.callrail.test"

os.environ["CALLRAIL_FIELD_ACCOUNT_ID"] = ""
chk = callrail.check_map([_call()])
check("a required field with no name does not resolve", chk["resolved"], False)
check("...and is named by the fact column it leaves unfilled", any("account_id" in m for m in chk["missing"]))
facts = callrail.to_facts([_call()])
check("...so the pull refuses rather than filing the row's own repr as the company",
      (facts["rows"], "does not resolve" in facts["error"]), ([], True))
os.environ.pop("CALLRAIL_FIELD_ACCOUNT_ID")

mixed = callrail.to_facts([_call(), _call(when="09/16/2026 10:00", source="Direct")])
check("an unreadable day costs that call, not every call in the answer",
      ([r["campaign_id"] for r in mixed["rows"]], mixed["skipped"]), (["google-ads"], 1))
check("a source spelled two ways is one source",
      [r["campaign_id"] for r in callrail.to_facts([_call(source="Google Ads"), _call(source="google ads")])["rows"]],
      ["google-ads"])
check("...and a call with no source still files, under a name that says so",
      [(r["campaign_id"], r["campaign_name"]) for r in callrail.to_facts([_call(source="")])["rows"]],
      [("no-source", "(no source)")])


# ------------------------------- the map is judged on names, not on one row
section("A null on the first call is not a wrong name")

# An unattributed call has no source. It is the FIRST call on the page as
# often as any other, and judging the map on row zero alone made it refuse
# the whole night and send somebody to correct a name that was right.
unattributed = dict(_call()); unattributed["source"] = None
page = [unattributed, _call(source="Google Ads"), _call(source="Google Organic")]
chk = callrail.check_map(page)
check("a required field that is null on the first call still resolves", chk["resolved"], True)
check("...judged against every call read, not the first", chk["sampled"], 3)
facts = callrail.to_facts(page)
check("...so the page files rather than refusing", (len(facts["rows"]), facts["error"]), (3, ""))
check("...with the unattributed call under its own name",
      sorted(r["campaign_id"] for r in facts["rows"]),
      ["google-ads", "google-organic", "no-source"])
check("...and the same three calls file whatever order they arrive in",
      len(callrail.to_facts(list(reversed(page)))["rows"]), 3)

os.environ["CALLRAIL_FIELD_CAMPAIGN_ID"] = "utm_source_nope"
chk = callrail.check_map(page)
check("a name nobody answers to is still caught", (chk["resolved"], chk["missing"]),
      (False, ["utm_source_nope"]))
check("...and still refuses the pull rather than filing under it",
      callrail.to_facts(page)["rows"], [])
os.environ.pop("CALLRAIL_FIELD_CAMPAIGN_ID")

gone = dict(_call()); gone.pop("source")
check("a key absent from every row is a wrong name, not an empty field",
      callrail.check_map([gone, dict(gone)])["resolved"], False)
chk = callrail.check_map([unattributed, dict(unattributed)])
check("a key present and null on every call resolves...", chk["resolved"], True)
check("...and says so rather than passing in silence",
      ("source" in chk["empty"], "empty on every call read" in chk["why"]), (True, True))

# ------------------------------------- a correction tried is not a correction settled
section("What the environment is answering for")

check("nothing overridden reads as nothing", callrail_map.overrides(), [])
os.environ["CALLRAIL_FIELD_CAMPAIGN_ID"] = "utm_source"
os.environ["CALLRAIL_PARAM_START"] = "from"
over = callrail_map.overrides()
check("an overridden field name is named with its variable",
      [(o["what"], o["env"], o["value"]) for o in over if o["what"] == "fields.campaign_id"],
      [("fields.campaign_id", "CALLRAIL_FIELD_CAMPAIGN_ID", "utm_source")])
check("...and so is an overridden parameter", any(o["env"] == "CALLRAIL_PARAM_START" for o in over))
check("...and config() carries the list for the page", len(callrail.cfg()["overrides"]), 2)
os.environ["CALLRAIL_CALLS_PATH"] = callrail_map.CALLS_PATH
check("a variable set to the value already in the file is not a disagreement",
      any(o["env"] == "CALLRAIL_CALLS_PATH" for o in callrail_map.overrides()), False)
os.environ.pop("CALLRAIL_CALLS_PATH")


def _check_page() -> str:
    ANSWERS.extend([_Resp(200, ACCOUNTS), _Resp(200, PAGE1)])
    return staff.get("/reports/callrail-check").get_data(as_text=True)


page_html = _check_page()
check("the check page names what is still owed to callrail_map.py",
      ("not settled" in page_html and "CALLRAIL_FIELD_CAMPAIGN_ID" in page_html), True)
os.environ.pop("CALLRAIL_FIELD_CAMPAIGN_ID")
os.environ.pop("CALLRAIL_PARAM_START")
check("with nothing overridden the page says the file is the whole answer",
      "every name below is the one in" in _check_page(), True)

os.environ["CALLRAIL_AUTH_FORMAT"] = f'Token token="{KEY}"'
check("a header somebody pasted the key into never reaches the page",
      _secrets_in(_check_page()), False)
os.environ.pop("CALLRAIL_AUTH_FORMAT")


os.environ["CALLRAIL_API_BASE"] = "http://api.callrail.test"
check("an http origin is refused by name rather than sent the key",
      (callrail.configured(), callrail.BASE_ENV in callrail.missing()), (False, True))
check("...saying why, and never that a variable somebody can see is unset",
      ("in clear" in callrail.not_configured_line(),
       "CALLRAIL_API_BASE unset" in callrail.not_configured_line()), (True, False))
callrail._http = fake_http
before = len(calls)
res = callrail.pull(days=3, today=TODAY)
check("...and the pull sends nothing at all to it", (res["ok"], len(calls)), (False, before))
check("...carrying no key in what it says", KEY in json.dumps([res, callrail.status()], default=str), False)
os.environ["CALLRAIL_API_BASE"] = "http://localhost:8099"
check("loopback over http is somebody testing against a stub, and is allowed", callrail.configured(), True)
callrail._http = _real_http


shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
