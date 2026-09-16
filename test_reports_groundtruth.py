"""The config-driven GroundTruth pull, its check page, and the visits tile.

    python3 test_reports_groundtruth.py

No pytest, no new dependencies, a throwaway SQLite reports database
(REPORTS_TEST_DATABASE_URL points it at Postgres in CI's second run) and a
stand-in for the platform: ``groundtruth._http`` is replaced whole. Through
the composed app for the check page, because it is a staff screen behind
the guard, and for the client's page, because the visits tile is the one
thing here a client reads.

What it holds:

  * unconfigured is a sentence, not an exception, and the sentence says
    WHICH half is owed: with the key set and the origin not -- this
    deployment's own state -- the line names GROUND_TRUTH_API_BASE, the
    pull and the check page refuse, and NOTHING is called, because a
    request to a host nobody confirmed carries the key in its header;
  * the key is read under exactly GROUND_TRUTH_API, through hub/config.py,
    with no alias beside it;
  * the call is shaped entirely by groundtruth_map.py -- method, path, the
    header the key rides under, the date parameter names -- and an
    environment override changes the call with no edit here;
  * the key is never in a result, an error, a watermark, a quota row or
    the page;
  * a body the map resolves against lands rows as platform groundtruth /
    source native with visits in extras under their own name and NOT in
    conversions or completes; one it does not is a refusal by name on the
    watermark, never a guessed row;
  * every call is recorded under the groundtruth quota row, the marker
    holds, and the sweep reports no unrecorded call site;
  * the check page prints the raw keys and whether the map resolves, and
    with the origin unset says nothing was called;
  * the client's page draws a Store visits tile from those rows, names no
    vendor, and data.json and the PDF carry the same tile; a row with no
    visits figure draws no tile at all;
  * the CSV door reads a Visits column into extras under the same name;
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

TMP = tempfile.mkdtemp(prefix="s1reports_gt_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-gt-test"
os.environ["PUBLIC_BASE_URL"] = "https://hub.example.test"
os.environ.pop("PANEL_PASSWORD", None)
for k in list(os.environ):
    if k.startswith("GROUND_TRUTH_") or k.startswith("AUDIOGO_") or k.startswith("STACKADAPT_") \
            or k.startswith("TTD_") or k.startswith("BING_"):
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
from modules.reports import groundtruth, groundtruth_map, store      # noqa: E402

_reports_testdb.reset(store)

KEY = "gt-live-key-8f3a1c-never-on-screen"
TODAY = date.today()
D1 = TODAY - timedelta(days=1) if TODAY.day > 1 else TODAY


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
    return ANSWERS.pop(0) if ANSWERS else _Resp(200, {"data": []})


_real_http = groundtruth._http
groundtruth._http = fake_http

RECORDED = []
_real_record = quotas.record


def fake_record(provider, **kw):
    RECORDED.append({"provider": provider, **kw})


quotas.record = fake_record


# --------------------------------------------------------- unconfigured
section("Unconfigured is a sentence, not an exception")

st = groundtruth.status()
check("not configured", st["configured"], False)
check("...naming both variables", st["missing"], ["GROUND_TRUTH_API", "GROUND_TRUTH_API_BASE"])
check("...on the line", st["line"], "GroundTruth: not configured: GROUND_TRUTH_API, GROUND_TRUTH_API_BASE unset")
check("...and it points at the check page", st["check_page"], "/reports/groundtruth-check")
res = groundtruth.pull()
check("a pull returns rather than raising", (res["ok"], res["error"].startswith("not configured")), (False, True))
check("...and wrote no watermark", store.sync_status().get("groundtruth"), None)
check("...and called nothing", calls, [])
chk = groundtruth.check(today=date(2026, 9, 15))
check("the check says what to set", chk["error"], "not configured: GROUND_TRUTH_API, GROUND_TRUTH_API_BASE unset")
check("...for yesterday", chk["day"], "2026-09-14")
check("the map declares itself a placeholder", groundtruth_map.config()["placeholder"], True)
src = (ROOT / "modules" / "reports" / "groundtruth_map.py").read_text(encoding="utf-8")
check("...in the file, loudly", src.count("PLACEHOLDER") >= 8)
check("...and the origin is the placeholder that is never called",
      "never called" in src and groundtruth_map.config()["base"] == "")


# ------------------------------------- the key alone: this deployment's state
section("The key alone: the origin is what is owed, and nothing is called")

os.environ["GROUND_TRUTH_API"] = KEY
st = groundtruth.status()
check("with the key set, the origin is the one thing missing", st["missing"], ["GROUND_TRUTH_API_BASE"])
check("...and the line says which half is owed",
      st["line"].startswith("GroundTruth: key set; GROUND_TRUTH_API_BASE unset") and "sent nowhere" in st["line"])
check("...not 'not configured', which sends somebody to check a key that is set",
      "not configured" not in st["line"])
res = groundtruth.pull(days=3, today=date(2026, 9, 15))
check("the pull refuses by name", (res["ok"], res["error"]), (False, "not configured: GROUND_TRUTH_API_BASE unset"))
check("...and NOTHING was called", calls, [])
chk = groundtruth.check(today=date(2026, 9, 15))
check("the check calls nothing either", (calls, chk["answer"]), ([], None))
check("...its request says the origin is not set", chk["request"]["base_set"], False)
check("...and draws the placeholder origin for the eye only",
      chk["request"]["url"].startswith(groundtruth_map.DEFAULT_BASE))
check("the key is read through hub/config.py under exactly that spelling", hub_config._s("GROUND_TRUTH_API"), KEY)
check("...and the settings field reads the same variable", type(hub_config.settings)().groundtruth_key, KEY)
cfg_src = (ROOT / "hub" / "config.py").read_text(encoding="utf-8")
aliases = cfg_src[cfg_src.index("ALIASES: dict"):cfg_src.index("\n}\n", cfg_src.index("ALIASES: dict"))]
# A call site, never the word: hub/config.py EXPLAINS that there is no twin,
# and a check that reads that sentence as the twin is the prose-is-not-a-call-site trap.
check("hub/config.py invents no alias for it",
      "GROUND_TRUTH" not in aliases and "GROUNDTRUTH" not in aliases and '_s("GROUNDTRUTH' not in cfg_src)
rep = {r["name"]: r for r in type(hub_config.settings)().status()}
check("/status has a GroundTruth row naming the key and the origin",
      "GroundTruth" in rep and "GROUND_TRUTH_API" in rep["GroundTruth"]["note"]
      and "GROUND_TRUTH_API_BASE" in rep["GroundTruth"]["note"])
from hub import diagnostics                                          # noqa: E402
check("/diagnostics reads the key-alone state as a warning naming the origin",
      (diagnostics.check_groundtruth().state, "GROUND_TRUTH_API_BASE" in diagnostics.check_groundtruth().detail),
      ("warn", True))
check("...and the check is on the list", diagnostics.check_groundtruth in diagnostics.CHECKS)
os.environ.pop("GROUND_TRUTH_API")
check("...with nothing set it is off", diagnostics.check_groundtruth().state, "off")
os.environ["GROUND_TRUTH_API"] = KEY


# ------------------------------------------------------- the call shape
section("The call is shaped by the map and nothing else")

os.environ["GROUND_TRUTH_API_BASE"] = "https://api.groundtruth.test/"
check("with both set, configured", groundtruth.missing(), [])
check("...and /diagnostics says nothing has been pulled yet",
      (diagnostics.check_groundtruth().state, "nothing has been pulled" in diagnostics.check_groundtruth().detail),
      ("warn", True))
shape = groundtruth.request_shape(date(2026, 9, 1), date(2026, 9, 10))
c = groundtruth_map.config()
check("the URL is the origin plus the map's path", shape["url"], "https://api.groundtruth.test" + c["path"])
check("...the origin is the set one, said so", shape["base_set"], True)
check("...the method is the map's", shape["method"], c["method"])
check("...the date params carry the map's names",
      (shape["params"][c["date_params"]["start"]], shape["params"][c["date_params"]["end"]]),
      ("2026-09-01", "2026-09-10"))
check("...and the extra params ride along", all(shape["params"].get(k) == v for k, v in c["extra_params"].items()))
check("the key rides under the map's header, with its prefix",
      groundtruth.headers()[c["auth_header"]], c["auth_prefix"] + KEY)
check("...and request_shape never carries it", not _secrets_in(shape))

os.environ["GROUND_TRUTH_REPORT_PATH"] = "/v2/stats"
os.environ["GROUND_TRUTH_AUTH_HEADER"] = "X-API-Key"
os.environ["GROUND_TRUTH_AUTH_PREFIX"] = ""
os.environ["GROUND_TRUTH_PARAM_START"] = "from"
os.environ["GROUND_TRUTH_FIELD_VISITS"] = "store_visits"
check("an environment override moves the path", groundtruth.request_shape(date(2026, 9, 1), date(2026, 9, 1))["url"],
      "https://api.groundtruth.test/v2/stats")
check("...the header and its prefix", groundtruth.headers().get("X-API-Key"), KEY)
check("...a date parameter name", "from" in groundtruth.request_shape(date(2026, 9, 1), date(2026, 9, 1))["params"])
check("...and a field name", groundtruth_map.config()["fields"]["visits"], "store_visits")
for k in ("GROUND_TRUTH_REPORT_PATH", "GROUND_TRUTH_AUTH_HEADER", "GROUND_TRUTH_AUTH_PREFIX",
          "GROUND_TRUTH_PARAM_START", "GROUND_TRUTH_FIELD_VISITS"):
    os.environ.pop(k)


# ------------------------------------------------------------- the pull
section("A body the map resolves against lands rows; one it does not is refused by name")

f = c["fields"]
GOOD = {"data": [
    {f["date"]: D1.isoformat(), f["account_id"]: "org-1", f["account_name"]: "Acme Tire",
     f["campaign_id"]: "gt-1", f["campaign_name"]: "S1M | acme | Geofencing | fall",
     f["spend"]: "42.10", f["impressions"]: 3000, f["clicks"]: 12,
     f["visits"]: 57, f["visit_rate"]: "1.9%"},
    {f["date"]: D1.isoformat(), f["account_id"]: "org-1", f["campaign_id"]: "",
     f["spend"]: "1", f["impressions"]: 1},
]}

ANSWERS.append(_Resp(200, GOOD))
res = groundtruth.pull(days=3, today=TODAY)
check("the pull is ok with one row", (res["ok"], res["rows"], res["skipped"]), (True, 1, 1))
check("it called the configured endpoint", calls[-1]["url"], "https://api.groundtruth.test" + c["path"])
check("...over a thirty-day window by default", groundtruth.DAYS, 30)
check("...with the key in the header only",
      calls[-1]["headers"][c["auth_header"]].endswith(KEY) and not _secrets_in(calls[-1]["params"]))
db = store.SessionLocal()
try:
    row = db.query(store.AdPerfDaily).filter(store.AdPerfDaily.platform == "groundtruth").one()
    check("platform groundtruth, source native", (row.platform, row.source), ("groundtruth", "native"))
    check("...spend in dollars, delivery counted", (str(row.spend), row.impressions, row.clicks), ("42.10", 3000, 12))
    check("...visits in extras under their own name, the rate beside them",
          (row.extras["visits"], row.extras["visit_rate"], row.extras["advertiser_name"]), (57, 1.9, "Acme Tire"))
    check("...and NOT in conversions or completes", (float(row.conversions or 0), row.completes), (0.0, None))
finally:
    db.close()
check("the native watermark is stamped", store.sync_status()["groundtruth"]["source"], "native")
check("...so the provider normalize defers to it", store.native_is_current("groundtruth"), True)
check("a row with no visits figure carries none, so the tile has something to gate on",
      "visits" not in groundtruth.to_facts({"data": [{f["date"]: "2026-09-09", f["account_id"]: "a",
                                                        f["campaign_id"]: "c"}]})["rows"][0]["extras"])

ANSWERS.append(_Resp(200, {"data": [{"when": "2026-09-10", "id": "x", "spent": 3}]}))
res = groundtruth.pull(days=3, today=TODAY)
check("a body the map does not resolve against lands nothing", (res["ok"], res["rows"]), (False, 0))
check("...and names the missing fields", "does not resolve" in res["error"] and f["date"] in res["error"])
check("...on the watermark", "does not resolve" in store.sync_status()["groundtruth"]["error"])
check("...pointing at the check page", "groundtruth-check" in res["error"])
check("...and /diagnostics carries the pull's own error",
      (diagnostics.check_groundtruth().state, "does not resolve" in diagnostics.check_groundtruth().detail),
      ("warn", True))

ANSWERS.append(_Resp(403, None, text=f"forbidden for key {KEY}"))
res = groundtruth.pull(days=3, today=TODAY)
check("a refusal is a sentence", "HTTP 403" in res["error"])
check("...never carrying the key", not _secrets_in(res) and not _secrets_in(store.sync_status()))
check("...and neither does status()", not _secrets_in(groundtruth.status()))

by_ok = {}
for r in RECORDED:
    by_ok[r.get("ok")] = by_ok.get(r.get("ok"), 0) + 1
check("every call was recorded under groundtruth", {r["provider"] for r in RECORDED}, {"groundtruth"})
check("...three calls, the refused one with ok=False", (len(RECORDED), by_ok), (3, {True: 2, False: 1}))
check("...filed under the reports module, by api", ({r["module"] for r in RECORDED}, {r["api"] for r in RECORDED}),
      ({"reports"}, {"report"}))
check("...with no credential in the detail", not _secrets_in(RECORDED))

ANSWERS.append(_Resp(200, GOOD))
res = groundtruth.pull(days=3, today=TODAY)
check("a good pull after a refusal clears the watermark", (res["ok"], store.sync_status()["groundtruth"]["error"]),
      (True, ""))
check("...and /diagnostics reads it as ok, with the rows",
      (diagnostics.check_groundtruth().state, "wrote 1 rows" in diagnostics.check_groundtruth().detail), ("ok", True))


# --------------------------------------------------------- the check page
section("The check page prints the raw keys and whether the map resolves")

anon = Client(wsgi.application)
r = anon.get("/reports/groundtruth-check")
check("a stranger is refused", r.status_code in (302, 401))
staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")

ANSWERS.append(_Resp(200, {"meta": {"page": 1}, "data": [{"when": "2026-09-10", "id": "x", "spent": 3, "visited": 9}]}))
r = staff.get("/reports/groundtruth-check")
page = r.get_data(as_text=True)
check("staff get the page", r.status_code, 200)
check("...calling the endpoint for yesterday",
      calls[-1]["params"][c["date_params"]["start"]] == calls[-1]["params"][c["date_params"]["end"]])
check("...printing the top-level keys", "data, meta" in page)
check("...and the row keys the endpoint answered with", all(k in page for k in ("when", "spent", "visited")))
check("...saying the map does not resolve", "Does not resolve" in page)
check("...the request as configured", c["path"] in page and c["auth_header"] in page)
check("...and never the key", not _secrets_in(page))
check("...nor the origin-unset notice while the origin is set", "Nothing was called" not in page)
tpl = (ROOT / "modules" / "reports" / "templates" / "reports_groundtruth_check.html").read_text()
check("...with the help bubble guarded", "help_dot('reports.groundtruth.check') if help_dot is defined" in tpl)
from hub import help as hub_help                                     # noqa: E402
check("...and the bubble registered", hub_help.get("reports.groundtruth.check") is not None)
ANSWERS.append(_Resp(200, GOOD))
page = staff.get("/reports/groundtruth-check").get_data(as_text=True)
check("against a body it resolves, the page says so", 'class="s1d-pill ok">Resolved' in page)
index = staff.get("/reports/").get_data(as_text=True)
check("the index links the check page", "GroundTruth check" in index)
check("...and lists the pull as a native one, connected", "GroundTruth: configured" in index)

n_calls = len(calls)
os.environ.pop("GROUND_TRUTH_API_BASE")
page = staff.get("/reports/groundtruth-check").get_data(as_text=True)
check("with the origin unset the page says what to set rather than 500ing", "GROUND_TRUTH_API_BASE unset" in page)
check("...says nothing was called", "Nothing was called" in page)
check("...and nothing was", len(calls), n_calls)
index = staff.get("/reports/").get_data(as_text=True)
check("...and the index says which half is owed", "key set; GROUND_TRUTH_API_BASE unset" in index)
os.environ["GROUND_TRUTH_API_BASE"] = "https://api.groundtruth.test/"


# ------------------------------------------------------ the client's page
section("The client's page draws Store visits, and names no vendor")

CLIENT = "d:acmetire.com"
NAME = "Acme Tire"
store.map_campaign("groundtruth", "org-1", "gt-1", client=CLIENT, client_name=NAME,
                   product="Geofencing", mapped_by="Todd")
link = store.create_link(CLIENT, client_name=NAME, created_by="Todd")
URL = f"/reports/r/c/{link.token}"
r = anon.get(URL)
html = r.get_data(as_text=True)
check("the page opens for a stranger", r.status_code, 200)
check("...with a Store visits tile", "Store visits" in html)
check("...carrying the figure", ">57<" in html.replace("\n", "") or "57</div>" in html)
check("...and naming no vendor", "groundtruth" not in html.lower() and "ground truth" not in html.lower())
data = anon.get(URL + "/data.json").get_json()
tiles = {t["key"]: t for t in data.get("tiles", [])}
check("data.json carries the same tile", (tiles.get("visits") or {}).get("value"), 57)
check("...labeled the same", (tiles.get("visits") or {}).get("label"), "Store visits")
check("...and no completes tile was invented for it", "completes" not in tiles)
r = anon.get(URL + ".pdf")
pdf = r.get_data()
check("the PDF renders", (r.status_code, pdf[:5]), (200, b"%PDF-"))
check("...carrying the tile", b"Store visits" in pdf)

# The gate: a geofencing row with no visits figure draws no tile.
store.upsert_rows([{"platform": "groundtruth", "account_id": "org-2", "campaign_id": "gt-2",
                    "campaign_name": "Provider rows", "date": D1, "spend": "5.00",
                    "impressions": 100, "clicks": 1, "source": "windsor"}])
store.map_campaign("groundtruth", "org-2", "gt-2", client="d:novisits.test", client_name="No Visits Co",
                   product="Geofencing", mapped_by="Todd")
link2 = store.create_link("d:novisits.test", client_name="No Visits Co", created_by="Todd")
html2 = anon.get(f"/reports/r/c/{link2.token}").get_data(as_text=True)
check("a geofencing row carrying no visits figure draws no Store visits tile", "Store visits" not in html2)
check("...rather than a measured nought", "Store visits" not in html2 and "No Visits Co" in html2)


# ---------------------------------------------------------- the CSV door
section("The CSV door reads a Visits column into extras")

from modules.reports.parsers import audiogo_csv                      # noqa: E402
parsed = audiogo_csv.parse("Date,Advertiser ID,Campaign ID,Spend,Impressions,Clicks,Visits\n"
                           "2026-09-14,org-1,gt-1,$10.00,100,5,3\n"
                           "2026-09-14,org-1,gt-1,$2.00,10,1,4\n", platform="groundtruth")
check("the file parses", parsed["error"], "")
check("...as groundtruth rows", parsed["rows"][0]["platform"], "groundtruth")
check("...with visits summed across a split row, in extras", parsed["rows"][0]["extras"]["visits"], 7)
check("...and not in conversions", parsed["rows"][0]["conversions"], 0)


# ------------------------------------------------ the scheduler and the sweep
section("The nightly job, the marker, the docs")

groundtruth._http, quotas.record = _real_http, _real_record
for k in ("GROUND_TRUTH_API", "GROUND_TRUTH_API_BASE"):
    os.environ.pop(k, None)
from hub import scheduler                                            # noqa: E402
from flask import Flask                                              # noqa: E402
job = scheduler.JOBS["reports_native"][1]
check("the job's docstring names it", "GroundTruth" in (job.__doc__ or ""))
out = job(Flask("t"))
check("unconfigured, the nightly job skips it cleanly", "groundtruth" in out["skipped"] and out["errors"] == {})
check("the index lists it as a native pull",
      ("groundtruth", "groundtruth") in __import__("modules.reports.app", fromlist=["NATIVE_PULLS"]).NATIVE_PULLS)
check("the quota row exists, in calls, unmeasured against a ceiling",
      (quotas.QUOTAS["groundtruth"].unit, quotas.QUOTAS["groundtruth"].thresholds()), ("calls", (0, 0)))
check("...and the marker is the domain", "groundtruth" in quotas._PROVIDER_MARKERS
      and quotas._PROVIDER_MARKERS["groundtruth"]["calls"]("requests.get('https://x.groundtruth.com')"))
blind = quotas.untracked_provider_calls(force=True)
check("no unrecorded GroundTruth call site", blind.get("groundtruth"), [])
for fname in ("env.example", "render.yaml"):
    text = (ROOT / fname).read_text(encoding="utf-8")
    check(f"{fname} documents the key and the origin",
          "GROUND_TRUTH_API" in text and "GROUND_TRUTH_API_BASE" in text)
    check(f"...and no GROUNDTRUTH_ twin", re.search(r"^\s*(- key: )?GROUNDTRUTH_", text, re.M) is None)
# The guide is CLAUDE.md plus the long-form docs/claude/ files split out of it.
guide = "\n".join(p.read_text(encoding="utf-8") for p in
                  [ROOT / "CLAUDE.md", *sorted((ROOT / "docs" / "claude").glob("*.md"))])
check("the guide has the section", "## GroundTruth: the key arrived before the document" in guide)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
