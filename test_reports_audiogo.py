"""The config-driven AudioGo pull, its check page, and the CSV parser.

    python3 test_reports_audiogo.py

No pytest, no new dependencies, a throwaway SQLite reports database and a
stand-in for the platform: ``audiogo._http`` is replaced whole. Through the
composed app for the check page, because it is a staff screen behind the
guard.

What it holds:

  * unconfigured is a clean status line, a pull that returns rather than
    raises, and a check page that says which variables to set;
  * the call is shaped entirely by audiogo_map.py -- method, path, the
    header the key rides under, the date parameter names -- and a change
    to the map (or its environment overrides) changes the call with no
    edit here;
  * AUDIOGO_API_BASE is the URL that gets called, exactly as set: nothing
    is appended unless AUDIOGO_REPORT_PATH asks for it, and it can be
    cleared again, which is what the September 18 404 cost;
  * AUDIOGO_METHOD=POST sends the dates and AUDIOGO_BODY as a JSON body;
  * a base that cannot carry a credential is owed, not configured;
  * a refusal names the method and URL it called;
  * the key is never in a result, an error, a watermark or the page;
  * the check page renders the raw keys an endpoint answered with and
    says whether the map resolves, naming what is missing;
  * a body the map resolves against lands rows as platform audiogo /
    source native with listens in completes and in extras; one it does
    not is a refusal by name on the watermark, never a guessed row;
  * the CSV parser reads the alias spellings (Listens, Completed Listens,
    LTR, Media Cost ...), sums a split row, skips and counts a keyless
    row, and refuses a keyless file naming its columns.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_ag_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-ag-test"
os.environ.pop("PANEL_PASSWORD", None)
for k in list(os.environ):
    if k.startswith("AUDIOGO_") or k.startswith("STACKADAPT_") or k.startswith("TTD_"):
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
from hub import auth                                                 # noqa: E402
from modules.reports import audiogo, audiogo_map, store              # noqa: E402
_reports_testdb.reset(store)
from modules.reports.parsers import audiogo_csv                      # noqa: E402

KEY = "ag-key-77aa19c3"


# --------------------------------------------------------- unconfigured
section("Unconfigured is a sentence, not an exception")

st = audiogo.status()
check("not configured", st["configured"], False)
check("...naming both variables", st["missing"], ["AUDIOGO_API_KEY", "AUDIOGO_API_BASE"])
check("...on the line", st["line"], "AudioGo: not configured: AUDIOGO_API_KEY, AUDIOGO_API_BASE unset")
check("...and it points at the check page", st["check_page"], "/reports/audiogo-check")
res = audiogo.pull()
check("a pull returns rather than raising", (res["ok"], res["error"].startswith("not configured")), (False, True))
check("...and wrote no watermark", store.sync_status().get("audiogo"), None)
chk = audiogo.check(today=date(2026, 9, 11))
check("the check says what to set", chk["error"], "not configured: AUDIOGO_API_KEY, AUDIOGO_API_BASE unset")
check("...for yesterday", chk["day"], "2026-09-10")
check("the map declares itself a placeholder", audiogo_map.config()["placeholder"], True)
src = (ROOT / "modules" / "reports" / "audiogo_map.py").read_text(encoding="utf-8")
check("...in the file, loudly", src.count("PLACEHOLDER") >= 8)


# ------------------------------------------------------- the call shape
section("The call is shaped by the map and nothing else")

os.environ["AUDIOGO_API_KEY"] = KEY
os.environ["AUDIOGO_API_BASE"] = "https://api.audiogo.test/"
shape = audiogo.request_shape(date(2026, 9, 1), date(2026, 9, 10))
c = audiogo_map.config()
check("the URL is the base plus the map's path", shape["url"], "https://api.audiogo.test" + c["path"])
check("...the method is the map's", shape["method"], c["method"])
check("...the date params carry the map's names",
      (shape["params"][c["date_params"]["start"]], shape["params"][c["date_params"]["end"]]),
      ("2026-09-01", "2026-09-10"))
check("...and the extra params ride along", all(shape["params"].get(k) == v for k, v in c["extra_params"].items()))
check("the key rides under the map's header, with its prefix",
      audiogo.headers()[c["auth_header"]], c["auth_prefix"] + KEY)
check("...and request_shape never carries it", KEY not in json.dumps(shape))

os.environ["AUDIOGO_REPORT_PATH"] = "/v2/stats"
os.environ["AUDIOGO_AUTH_HEADER"] = "X-API-Key"
os.environ["AUDIOGO_AUTH_PREFIX"] = ""
os.environ["AUDIOGO_PARAM_START"] = "from"
check("an environment override moves the path", audiogo.request_shape(date(2026, 9, 1), date(2026, 9, 1))["url"],
      "https://api.audiogo.test/v2/stats")
check("...the header and its prefix", audiogo.headers().get("X-API-Key"), KEY)
check("...and a date parameter name", "from" in audiogo.request_shape(date(2026, 9, 1), date(2026, 9, 1))["params"])
for k in ("AUDIOGO_REPORT_PATH", "AUDIOGO_AUTH_HEADER", "AUDIOGO_AUTH_PREFIX", "AUDIOGO_PARAM_START"):
    os.environ.pop(k)


# ------------------------------------------- the URL, and only the URL
section("The base is called exactly as it is set")

os.environ["AUDIOGO_API_BASE"] = "https://api.adswizz.com/domain/v8/reports/query"
check("a base that is the whole endpoint is called as it is, nothing appended",
      audiogo.request_shape(date(2026, 9, 17), date(2026, 9, 17))["url"],
      "https://api.adswizz.com/domain/v8/reports/query")
check("...which is the September 18 404, and it does not recur",
      "/v1/reports/campaigns/daily" not in audiogo_map.config()["url"])
check("the module ships no path of its own", audiogo_map.REPORT_PATH, "")
check("...and the documented AdsWizz base is what the map prints",
      audiogo_map.DEFAULT_BASE, "https://api.adswizz.com/domain")

os.environ["AUDIOGO_API_BASE"] = "https://api.adswizz.com/domain"
os.environ["AUDIOGO_REPORT_PATH"] = "v8/reports/query"
check("an origin plus a path still composes, slash or no slash",
      audiogo_map.config()["url"], "https://api.adswizz.com/domain/v8/reports/query")
for blank in ("", "-", "/", "none"):
    os.environ["AUDIOGO_REPORT_PATH"] = blank
    check(f"...and {blank!r} clears it back to nothing",
          audiogo_map.config()["url"], "https://api.adswizz.com/domain")
os.environ.pop("AUDIOGO_REPORT_PATH")

check("an http origin is owed rather than configured, because the key would go in clear",
      audiogo._carries_a_credential("http://api.example.com"), False)
check("...loopback is a person testing against a stub", audiogo._carries_a_credential("http://127.0.0.1:5000"), True)
os.environ["AUDIOGO_API_BASE"] = "http://api.adswizz.com/domain"
check("...and missing() says which half is owed, not 'not configured'",
      audiogo.missing(), ["AUDIOGO_API_BASE (set, but not https -- the key would go in clear)"])


# ----------------------------------------------- the report as a query
section("AUDIOGO_METHOD=POST sends the report as a body")

os.environ["AUDIOGO_API_BASE"] = "https://api.audiogo.test/"
os.environ["AUDIOGO_METHOD"] = "post"
os.environ["AUDIOGO_BODY"] = '{"metrics": ["demandAudioImp"], "splitters": ["campaign"]}'
shape = audiogo.request_shape(date(2026, 9, 17), date(2026, 9, 17))
check("the method is the one set, upper-cased", shape["method"], "POST")
check("...the query string is empty", shape["params"], {})
check("...the dates ride in the body, over AUDIOGO_BODY",
      (shape["json"]["start_date"], shape["json"]["metrics"], shape["json"]["granularity"]),
      ("2026-09-17", ["demandAudioImp"], "day"))
check("...and the key is still nowhere near it", KEY not in json.dumps(shape))
os.environ["AUDIOGO_BODY"] = "{not json"
check("a body that is not JSON is ignored rather than raising", audiogo_map.config()["body"], {})
for k in ("AUDIOGO_METHOD", "AUDIOGO_BODY"):
    os.environ.pop(k)
check("the default is still the GET that was tried", audiogo_map.config()["method"], "GET")

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


def fake_http(method, url, *, headers, params, json=None, timeout=60):
    calls.append({"method": method, "url": url, "headers": dict(headers),
                  "params": dict(params), "json": json})
    return ANSWERS.pop(0) if ANSWERS else _Resp(200, {"data": []})


audiogo._http = fake_http

f = c["fields"]
GOOD = {"data": [
    {f["date"]: "2026-09-10", f["account_id"]: "adv-1", f["account_name"]: "Acme Co",
     f["campaign_id"]: "ag-1", f["campaign_name"]: "S1M | acme | Streaming Audio | fall",
     f["spend"]: "42.10", f["impressions"]: 3000, f["clicks"]: 12,
     f["listens"]: 2800, f["completed_listens"]: 2500, f["ltr"]: 0.89},
    {f["date"]: "2026-09-10", f["account_id"]: "adv-1", f["campaign_id"]: "",
     f["spend"]: "1", f["impressions"]: 1},
]}


# ------------------------------------------------------------- the pull
section("A body the map resolves against lands rows; one it does not is refused by name")

ANSWERS.append(_Resp(200, GOOD))
res = audiogo.pull(days=3, today=date(2026, 9, 11))
check("the pull is ok with one row", (res["ok"], res["rows"], res["skipped"]), (True, 1, 1))
check("it called the configured endpoint", calls[-1]["url"], "https://api.audiogo.test" + c["path"])
check("...with the key in the header only", calls[-1]["headers"][c["auth_header"]].endswith(KEY)
      and KEY not in json.dumps(calls[-1]["params"]))
db = store.SessionLocal()
try:
    row = db.query(store.AdPerfDaily).filter(store.AdPerfDaily.platform == "audiogo").one()
    check("platform audiogo, source native", (row.platform, row.source), ("audiogo", "native"))
    check("...spend in dollars, delivery counted", (str(row.spend), row.impressions, row.clicks), ("42.10", 3000, 12))
    check("...completed listens in completes", row.completes, 2500)
    check("...and every audio figure in extras",
          (row.extras["listens"], row.extras["completed_listens"], row.extras["ltr"], row.extras["advertiser_name"]),
          (2800, 2500, 0.89, "Acme Co"))
finally:
    db.close()
check("the native watermark is stamped", store.sync_status()["audiogo"]["source"], "native")

ANSWERS.append(_Resp(200, {"data": [{"when": "2026-09-10", "id": "x", "spent": 3}]}))
res = audiogo.pull(days=3, today=date(2026, 9, 11))
check("a body the map does not resolve against lands nothing", (res["ok"], res["rows"]), (False, 0))
check("...and names the missing fields", "does not resolve" in res["error"] and f["date"] in res["error"])
check("...on the watermark", "does not resolve" in store.sync_status()["audiogo"]["error"])
check("...pointing at the check page", "audiogo-check" in res["error"])

ANSWERS.append(_Resp(404, None, text="{\"code\":\"not.found\"}"))
res = audiogo.pull(days=3, today=date(2026, 9, 11))
check("a 404 names the method and the URL it called, not just the path",
      "HTTP 404 on GET https://api.audiogo.test" in res["error"])
check("...and says which two settings compose that URL",
      "AUDIOGO_API_BASE" in res["error"] and "AUDIOGO_REPORT_PATH" in res["error"])
check("...and what to try next", "AUDIOGO_METHOD=POST" in res["error"])

ANSWERS.append(_Resp(403, None, text=f"forbidden for key {KEY}"))
res = audiogo.pull(days=3, today=date(2026, 9, 11))
check("a refusal is a sentence", "HTTP 403" in res["error"])
check("...never carrying the key", KEY not in json.dumps(res) and KEY not in json.dumps(store.sync_status()))
check("...and neither does status()", KEY not in json.dumps(audiogo.status()))
check("listens alone land in completes when no completed figure is reported",
      audiogo.to_facts({"data": [{f["date"]: "2026-09-09", f["account_id"]: "a", f["campaign_id"]: "c",
                                  f["listens"]: 7}]})["rows"][0]["completes"], 7)


# --------------------------------------------------------- the check page
section("The check page prints the raw keys and whether the map resolves")

anon = Client(wsgi.application)
r = anon.get("/reports/audiogo-check")
check("a stranger is refused", r.status_code in (302, 401))
staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")

ANSWERS.append(_Resp(200, {"meta": {"page": 1}, "data": [{"when": "2026-09-10", "id": "x", "spent": 3, "plays": 9}]}))
r = staff.get("/reports/audiogo-check")
page = r.get_data(as_text=True)
check("staff get the page", r.status_code, 200)
check("...calling the endpoint for yesterday", calls[-1]["params"][c["date_params"]["start"]] == calls[-1]["params"][c["date_params"]["end"]])
check("...printing the top-level keys", "data, meta" in page, note=page[page.find("Top-level"):page.find("Top-level")+200])
check("...and the row keys the endpoint answered with", all(k in page for k in ("when", "spent", "plays")))
check("...saying the map does not resolve", "Does not resolve" in page)
check("...naming the field that is missing", f'{f["date"]}</span>' in page.replace(" ", "").replace("\n", "")
      or f["date"] in page and 'class="s1d-pill bad">missing' in page)
check("...the request as configured", audiogo_map.config()["url"] in page and c["auth_header"] in page)
check("...naming the two settings the URL is composed of",
      "AUDIOGO_API_BASE" in page and "AUDIOGO_REPORT_PATH" in page)
check("...and never the key", KEY not in page)
check("...with the help bubble guarded", "help_dot('reports.audiogo.check') if help_dot is defined" in
      (ROOT / "modules" / "reports" / "templates" / "reports_audiogo_check.html").read_text())
from hub import help as hub_help                                     # noqa: E402
check("...and the bubble registered", hub_help.get("reports.audiogo.check") is not None)
ANSWERS.append(_Resp(200, GOOD))
page = staff.get("/reports/audiogo-check").get_data(as_text=True)
check("against a body it resolves, the page says so", 'class="s1d-pill ok">Resolved' in page)
check("the index links the check page", "AudioGo check" in staff.get("/reports/").get_data(as_text=True))
check("...and lists the keys the endpoint answered with that the map does not name",
      'id="unread-fields"' in page and "Answered and not read" in page)
ANSWERS.append(_Resp(200, {"meta": {"page": 1}, "data": [{"when": "2026-09-10", "id": "x", "spent": 3, "plays": 9}]}))
page = staff.get("/reports/audiogo-check").get_data(as_text=True)
seg = page[page.find("Answered and not read"):page.find("Answered and not read") + 400]
check("...naming when, spent and plays as answered and not read", all(k in seg for k in ("when", "spent", "plays")))
check("...beside the documented list, with its source named", "demandAudioImp" in page and "Transcribed from" in page)
check("...and the providers submenu with AudioGO marked", 's1d-subnav' in page and 'class="on"' in page)
check("the key goes in the x-api-key header AudioGo's FAQ documents, bare",
      (audiogo_map.AUTH_HEADER, audiogo_map.AUTH_PREFIX), ("x-api-key", ""))
check("...and the provider page carries the Render list naming AUDIOGO_API_BASE as owed from the spec",
      "AUDIOGO_API_BASE" in staff.get("/reports/provider-check/audiogo").get_data(as_text=True))

os.environ.pop("AUDIOGO_API_KEY")
os.environ.pop("AUDIOGO_API_BASE")
page = staff.get("/reports/audiogo-check").get_data(as_text=True)
check("unconfigured, the page says what to set rather than 500ing",
      "AUDIOGO_API_KEY, AUDIOGO_API_BASE unset" in page)


# ---------------------------------------------------------- the CSV
section("The CSV parser reads the alias spellings")

CSV = """Date,Advertiser Name,Advertiser ID,Campaign,Campaign ID,Impressions,Clicks,Listens,Completed Listens,LTR,Media Cost
09/10/2026,Acme Co,adv-1,Fall spots,ag-1,"1,000",5,900,800,88.9%,$10.00
09/10/2026,Acme Co,adv-1,Fall spots,ag-1,500,1,450,400,88.9%,$5.50
09/11/2026,Acme Co,adv-1,Fall spots,ag-1,700,2,600,500,83.3%,$7.00
09/11/2026,Acme Co,,Nobody,ag-9,1,0,1,1,100%,$0.10
"""
p = audiogo_csv.parse(CSV)
check("no error", p["error"], "")
check("two campaign-days, one keyless row skipped", (len(p["rows"]), p["skipped"]), (2, 1))
by = {r["date"].isoformat(): r for r in p["rows"]}
d10 = by["2026-09-10"]
check("a split day is summed", (d10["impressions"], d10["clicks"], d10["spend"]), (1500, 6, 15.5))
check("...listens and completed listens too", (d10["extras"]["listens"], d10["extras"]["completed_listens"]), (1350, 1200))
check("...completed listens in completes", d10["completes"], 1200)
check("...LTR read as a number", d10["extras"]["ltr"], 88.9)
check("...platform audiogo, source csv", (d10["platform"], d10["source"]), ("audiogo", "csv"))
check("...the campaign name kept", d10["campaign_name"], "Fall spots")
p2 = audiogo_csv.parse("day,brand id,campaignid,plays,spend\n2026-09-10,b1,c1,50,2.00\n")
check("the other spellings resolve", (p2["rows"][0]["completes"], p2["rows"][0]["spend"]), (50, 2.0))
p3 = audiogo_csv.parse("Foo,Bar\n1,2\n")
check("a keyless file is refused naming its columns",
      "does not carry date, account_id, campaign_id" in p3["error"] and "Foo, Bar" in p3["error"])
check("an empty file is refused", audiogo_csv.parse("")["error"], "The file is empty.")
check("the rows land through the store", store.upsert_rows(p["rows"]), 2)

for fname in ("env.example", "render.yaml"):
    check(f"{fname} documents AUDIOGO_API_KEY and AUDIOGO_API_BASE",
          all(k in (ROOT / fname).read_text(encoding="utf-8") for k in ("AUDIOGO_API_KEY", "AUDIOGO_API_BASE")))
body = (ROOT / "hub" / "scheduler.py").read_text(encoding="utf-8")
body = body[body.index("def job_reports_native_pull"):body.index("\nJOBS = {")]
check("the native job pulls AudioGo in the same loop", '("audiogo", audiogo.pull)' in body)

shutil.rmtree(TMP, ignore_errors=True)


# --- Render's spelling of the key is read too -----------------------------
import os as _os
from modules.reports import audiogo as _ag
_saved = {k: _os.environ.pop(k, None) for k in _ag.KEY_ENV}
_os.environ["AUDIO_GO_API"] = "alias-key"
check("AUDIO_GO_API is read as the key", _ag.cfg()["key"], "alias-key")
for k, v in _saved.items():
    _os.environ.pop(k, None)
    if v is not None:
        _os.environ[k] = v

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
