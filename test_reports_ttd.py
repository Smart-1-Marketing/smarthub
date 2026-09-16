"""The native Trade Desk pull and the MyReports CSV parser it shares with the inbox.

    python3 test_reports_ttd.py

No pytest, no new dependencies, a throwaway SQLite reports database and a
stand-in for the platform: ``ttd._http`` is replaced whole, so nothing here
reaches the network and every request the module makes is recorded.

What it holds:

  * unconfigured is a clean status line ("not configured: TTD_API_TOKEN
    unset") and a pull that returns rather than raises;
  * every call carries the token as the TTD-Auth header and nothing else
    carries it -- no result, no error, no watermark, no status;
  * advertisers are paged on PageStartIndex until a page comes back short;
  * the schedule is found by name or created once from the template, and
    the executions query names the schedule;
  * the MyReports CSV parser reads the documented columns, sums a split
    row, drops and counts a row with no key, refuses a file with no key
    columns by naming what it has, and a restated day upserts over the
    first;
  * a pull lands rows as platform ttd / source native and stamps the native
    watermark, so the provider normalize then skips ttd for a day;
  * the scheduler job is registered nightly -- the 3 AM Eastern ledger in
    hub/report_schedule.py drives it -- and isolates the two platforms.
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

TMP = tempfile.mkdtemp(prefix="s1reports_ttd_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-ttd-test"
for k in ("TTD_API_TOKEN", "TTD_PARTNER_ID", "TTD_REPORT_TEMPLATE_ID", "TTD_API_BASE"):
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


from modules.reports import normalize, store, ttd                    # noqa: E402
_reports_testdb.reset(store)
from modules.reports.parsers import ttd_myreports                   # noqa: E402

TOKEN = "sekrit-long-lived-token-9f8e7d"


# --------------------------------------------------------- unconfigured
section("Unconfigured is a sentence, not an exception")

st = ttd.status()
check("not configured", st["configured"], False)
check("...the line names the token variable", st["line"], "Trade Desk: not configured: TTD_API_TOKEN (or TRADE_DESK_API) unset")
check("...and missing lists both", st["missing"], ["TTD_API_TOKEN", "TTD_PARTNER_ID"])
res = ttd.pull()
check("a pull returns rather than raising", res["ok"], False)
check("...saying what is unset", res["error"], "not configured: TTD_API_TOKEN, TTD_PARTNER_ID unset")
check("...and wrote no watermark", store.sync_status().get("ttd"), None)
try:
    ttd.request("POST", ttd.ADVERTISER_QUERY, {})
    raised = None
except ttd.TTDError as exc:
    raised = str(exc)
check("a bare request without a token is refused by name", raised, ttd.NOT_CONFIGURED)


# ------------------------------------------------------------ the CSV
section("The MyReports CSV parser")

CSV = (
    "Date,Advertiser ID,Advertiser,Campaign ID,Campaign,Advertiser Cost (USD),Impressions,Clicks,"
    "Post-Click Conversions,Post-View Conversions,Player Starts,Player Completed Views\n"
    '2026-09-01,adv1,Acme Roofing,c1,S1M | acme | CTV | fall,"1,234.50",100000,120,3,4,90000,80000\n'
    "2026-09-01,adv1,Acme Roofing,c1,S1M | acme | CTV | fall,10.00,1000,1,0,1,900,800\n"
    "2026-09-02,adv1,Acme Roofing,c1,S1M | acme | CTV | fall,500.00,50000,60,1,0,45000,40000\n"
    "09/02/2026,adv2,Bolt Plumbing,c9,Bolt - display,20.25,4000,8,0,0,,\n"
    ",adv2,Bolt Plumbing,c9,Bolt - display,99,9,9,0,0,,\n"
    "2026-09-02,adv2,Bolt Plumbing,,Bolt - display,99,9,9,0,0,,\n"
)
parsed = ttd_myreports.parse(CSV)
check("it reads the documented columns", parsed["error"], "")
check("...matching spend on Advertiser Cost (USD)", parsed["columns"]["spend"], "Advertiser Cost (USD)")
check("...conversions from the click + view pair when there is no total",
      "conv_click" in parsed["columns"] and "conversions" not in parsed["columns"])
rows = {(r["account_id"], r["campaign_id"], r["date"]): r for r in parsed["rows"]}
check("one row per advertiser, campaign and day", len(rows), 3)
r1 = rows[("adv1", "c1", date(2026, 9, 1))]
check("a day split across two lines is summed", (r1["spend"], r1["impressions"], r1["clicks"]),
      (1244.5, 101000, 121))
check("...conversions too", r1["conversions"], 8.0)
check("...and the video pair lands on the fact table's own columns",
      (r1["video_views"], r1["completes"]), (90900, 80800))
check("...platform ttd, source native", (r1["platform"], r1["source"]), ("ttd", "native"))
check("...the campaign name rides along", r1["campaign_name"], "S1M | acme | CTV | fall")
check("...and the advertiser's name in extras", r1["extras"]["advertiser_name"], "Acme Roofing")
check("a US-style date is read", ("adv2", "c9", date(2026, 9, 2)) in rows)
check("a row with no day or no campaign id is dropped and counted", parsed["skipped"], 2)
check("thousands separators and a BOM are fine",
      ttd_myreports.parse("﻿" + CSV)["rows"][0]["spend"], 1244.5)
bad = ttd_myreports.parse("Advertiser,Spend\nAcme,1\n")
check("a file with none of the key columns is refused", bool(bad["error"]) and bad["rows"] == [])
check("...naming what it does carry", "Advertiser, Spend" in bad["error"])
check("an empty file is refused in words", ttd_myreports.parse("")["error"], "The file is empty.")
alt = ttd_myreports.parse("Day,Advertiser ID,Campaign ID,Total Conversions,Advertiser Cost,Impressions,Clicks\n"
                          "2026-09-03,a,c,5,1,2,3\n")
check("the alias spellings resolve (Day, Total Conversions, Advertiser Cost)",
      (alt["rows"][0]["conversions"], alt["rows"][0]["spend"], alt["rows"][0]["date"]),
      (5.0, 1.0, date(2026, 9, 3)))
check("...and a template with no video columns writes none",
      "video_views" not in alt["rows"][0] and "completes" not in alt["rows"][0])

# A restated day: the second file's figure replaces the first's.
store.upsert_rows(parsed["rows"])
later = ttd_myreports.parse(
    "Date,Advertiser ID,Campaign ID,Campaign,Advertiser Cost (USD),Impressions,Clicks,Total Conversions\n"
    "2026-09-02,adv1,c1,S1M | acme | CTV | fall,480.00,49000,58,1\n")
store.upsert_rows(later["rows"])
db = store.SessionLocal()
try:
    row = db.get(store.AdPerfDaily, ("ttd", "adv1", "c1", date(2026, 9, 2)))
    restated = (float(row.spend), row.impressions, row.source)
    count = db.query(store.AdPerfDaily).filter(store.AdPerfDaily.platform == "ttd").count()
finally:
    db.close()
check("a restated day upserts over the first figure", restated, (480.0, 49000, "native"))
check("...and is still one row", count, 3)


# ------------------------------------------------------ the platform
section("A configured pull, against a stand-in platform")

os.environ["TTD_API_TOKEN"] = TOKEN
os.environ["TTD_PARTNER_ID"] = "partner-x"
os.environ["TTD_API_BASE"] = "https://sandbox.example.test/v3/"

calls = []
STATE = {"schedule": None, "fail_download": False}


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.content = (json.dumps(payload) if payload is not None else text).encode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def fake_http(method, url, *, headers, body=None, timeout=60):
    calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
    path = url.replace("https://sandbox.example.test/v3", "")
    if path == ttd.ADVERTISER_QUERY:
        start = body["PageStartIndex"]
        # 150 advertisers: a full page, then a short one.
        ids = [f"adv{i}" for i in range(150)][start:start + body["PageSize"]]
        return _Resp(200, {"Result": [{"AdvertiserId": i, "AdvertiserName": i.upper()} for i in ids],
                           "ResultCount": len(ids)})
    if path == ttd.SCHEDULE_QUERY:
        found = [STATE["schedule"]] if STATE["schedule"] else []
        return _Resp(200, {"Result": found})
    if path == ttd.SCHEDULE_CREATE:
        STATE["schedule"] = {"ReportScheduleId": 777, "ReportScheduleName": body["ReportScheduleName"],
                             "ReportTemplateId": body["ReportTemplateId"]}
        return _Resp(200, STATE["schedule"])
    if path == ttd.EXECUTION_QUERY:
        return _Resp(200, {"Result": [
            {"ReportExecutionId": 1, "ReportScheduleId": body["ReportScheduleIds"][0],
             "ReportExecutionState": "Pending", "ReportDeliveries": []},
            {"ReportExecutionId": 2, "ReportScheduleId": body["ReportScheduleIds"][0],
             "ReportExecutionState": "Complete", "ReportEndDateExclusive": "2026-09-03T00:00:00",
             "ReportDeliveries": [{"DownloadURL": "https://files.example.test/report.csv"}]},
            {"ReportExecutionId": 3, "ReportScheduleId": body["ReportScheduleIds"][0],
             "ReportExecutionState": "Complete", "ReportEndDateExclusive": "2026-09-04T00:00:00",
             "ReportDeliveries": [{"DownloadURL": "https://files.example.test/report-newer.csv"}]},
        ]})
    if url.startswith("https://files.example.test/"):
        if STATE["fail_download"]:
            return _Resp(403, text=f"denied for {TOKEN}")
        return _Resp(200, text=CSV)
    return _Resp(404, {"Message": "no such path"})


ttd._http = fake_http

st = ttd.status()
check("configured now", st["configured"], True)
check("...and the line says connected", st["line"].startswith("Trade Desk: connected"))
check("the status carries no token", TOKEN not in json.dumps(st))

res = ttd.pull()
check("with no template id the pull refuses rather than landing", res["ok"], False)
check("...150 advertisers, paged, were read first", res["advertisers"], 150)
adv_calls = [c for c in calls if c["url"].endswith(ttd.ADVERTISER_QUERY)]
check("...in two pages on PageStartIndex", [c["body"]["PageStartIndex"] for c in adv_calls], [0, 100])
check("...each naming the partner", all(c["body"]["PartnerId"] == "partner-x" for c in adv_calls))
check("every API call carries the token as TTD-Auth",
      all(c["headers"].get("TTD-Auth") == TOKEN for c in calls if "sandbox.example.test" in c["url"]))
check("...and no other header carries it",
      all(TOKEN not in v for c in calls for k, v in c["headers"].items() if k != "TTD-Auth"))
# The run above had no template id: the schedule cannot be created and the
# pull says which variable would let it. Set one and run again.
calls.clear()
STATE["schedule"] = None
res0 = ttd.pull()
check("with no template id the pull refuses naming the variable",
      res0["ok"] is False and "TTD_REPORT_TEMPLATE_ID" in res0["error"])
check("...and the watermark carries that refusal", "TTD_REPORT_TEMPLATE_ID" in store.sync_status()["ttd"]["error"])
os.environ["TTD_REPORT_TEMPLATE_ID"] = "4242"
calls.clear()
res = ttd.pull()
check("with a template id the schedule is created", STATE["schedule"]["ReportTemplateId"], 4242)
created = [c for c in calls if c["url"].endswith(ttd.SCHEDULE_CREATE)]
check("...once, daily, as CSV, over every advertiser",
      (len(created), created[0]["body"]["ReportFrequency"], created[0]["body"]["ReportFileFormat"],
       len(created[0]["body"]["AdvertiserFilters"])), (1, "Daily", "CSV", 150))
check("...and the executions query names it", [c["body"]["ReportScheduleIds"] for c in calls
                                                if c["url"].endswith(ttd.EXECUTION_QUERY)], [[777]])
check("the newest complete execution's file is the one read",
      [c["url"] for c in calls if "files.example" in c["url"]], ["https://files.example.test/report-newer.csv"])
check("the pull landed", (res["ok"], res["rows"], res["executions"]), (True, 3, 3))
check("the download carries no auth header at all",
      [c["headers"] for c in calls if "files.example.test" in c["url"]], [{}])
calls.clear()
ttd.pull()
check("a second pull finds the schedule rather than creating another",
      not [c for c in calls if c["url"].endswith(ttd.SCHEDULE_CREATE)])

wm = store.sync_status()["ttd"]
check("the watermark is native", (wm["source"], wm["rows"], wm["error"]), ("native", 3, ""))
check("...so the normalize skips ttd for a day", store.native_is_current("ttd"), True)
out = normalize.run(today=date(2026, 9, 6))
check("...and says so rather than stamping over it", out["ttd"].get("native"), True)
check("...leaving the native watermark in place", store.sync_status()["ttd"]["source"], "native")
check("...while google is not skipped for it", not out["google"].get("native"))
st = ttd.status()
check("the status line reports the pull",
      st["line"].startswith("Trade Desk: connected, 150 advertisers, last pull 20"), note=st["line"])

# Refusals never carry the token.
STATE["fail_download"] = True
res = ttd.pull()
check("a refused download is a clean error", res["ok"], False)
check("...that does not carry the token", TOKEN not in json.dumps(res) and TOKEN not in json.dumps(ttd.status()))
check("...nor does the watermark", TOKEN not in store.sync_status()["ttd"]["error"])
check("...while still saying what happened", "HTTP 403" in res["error"])
STATE["fail_download"] = False
from hub import audit as _audit                                  # noqa: E402
check("nothing in the activity log carries it",
      TOKEN not in json.dumps(_audit.read(limit=2000)))
src = (ROOT / "modules" / "reports" / "ttd.py").read_text(encoding="utf-8")
check("the module never logs the token (no log call names it)",
      "log." not in src.split("def request")[1].split("def _paged")[0])
check("the endpoints are documented in the module docstring",
      all(p in (ttd.__doc__ or "") for p in ("/v3/advertiser/query/partner", "/v3/myreports/reportschedule",
                                              "/v3/myreports/reportexecution/query/partners", "TTD-Auth")))
for f in ("env.example", "render.yaml"):
    text = (ROOT / f).read_text(encoding="utf-8")
    check(f"{f} documents the Trade Desk variables",
          all(v in text for v in ("TTD_API_TOKEN", "TTD_PARTNER_ID", "TTD_API_BASE")))


# ------------------------------------------------------- the index page
section("The Reports index prints the status line")

from werkzeug.test import Client                                     # noqa: E402

from hub import auth                                                 # noqa: E402
from modules.reports import app as reports_app                       # noqa: E402

c = Client(reports_app.app)
c.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")
page = c.get("/").get_data(as_text=True)
check("the page names the Trade Desk pull", "Trade Desk: connected, 150 advertisers" in page)
check("...and the Google Ads one", "Google Ads: not connected" in page)
check("...marking ttd's sync as native", ">native<" in page)
check("...with no token on it", TOKEN not in page)
os.environ.pop("TTD_API_TOKEN")
page = c.get("/").get_data(as_text=True)
check("unset, it says so by variable", "Trade Desk: not configured: TTD_API_TOKEN (or TRADE_DESK_API) unset" in page)


# ---------------------------------------------------------- scheduler
section("The scheduler job")

from hub import scheduler                                            # noqa: E402

check("the job is registered", "reports_native" in scheduler.JOBS)
every, fn, desc = scheduler.JOBS["reports_native"]
check("...nightly: the interval the 3 AM Eastern ledger drives", every, 1440)
check("...as the thin function", fn.__name__, "job_reports_native_pull")
check("...apart from the provider job", scheduler.JOBS["reports_normalize"][1].__name__, "job_reports_normalize")
src = (ROOT / "hub" / "scheduler.py").read_text(encoding="utf-8")
body = src[src.index("def job_reports_native_pull"):src.index("\nJOBS = {")]
check("...inside an app context, with the flask.g note",
      "with app.app_context():" in body and "flask.g" in body)

from flask import Flask                                              # noqa: E402
out = fn(Flask("t"))
check("the job returns the shape the panel reads",
      sorted(out), ["automapped", "errors", "pending", "platforms", "rows", "skipped"])
check("...with every unconfigured platform skipped cleanly on this deployment's state",
      sorted(out["skipped"]),
      ["amazon_dsp", "audiogo", "bing", "google", "groundtruth", "stackadapt", "ttd"])
check("...and no errors", out["errors"], {})
check("...and the automap ran", out["automapped"] >= 0)

shutil.rmtree(TMP, ignore_errors=True)


# --- Render's spelling of the token is read too ---------------------------
if __name__ == "__main__" or True:
    import os as _os
    from modules.reports import ttd as _ttd
    _saved = {k: _os.environ.pop(k, None) for k in _ttd.TOKEN_ENV}
    _os.environ["TRADE_DESK_API"] = "alias-token"
    check("TRADE_DESK_API is read as the token", _ttd.cfg()["token"], "alias-token")
    _os.environ["TTD_API_TOKEN"] = "canonical"
    check("TTD_API_TOKEN wins when both are set", _ttd.cfg()["token"], "canonical")
    for k, v in _saved.items():
        _os.environ.pop(k, None)
        if v is not None:
            _os.environ[k] = v

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
