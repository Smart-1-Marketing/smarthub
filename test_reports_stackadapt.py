"""The native StackAdapt pull: the header, the one query, a defensive parse.

    python3 test_reports_stackadapt.py

No pytest, no new dependencies, a throwaway SQLite reports database and a
stand-in for the platform: ``stackadapt._http`` is replaced whole, so
nothing here reaches the network and every request the module makes is
recorded.

What it holds:

  * unconfigured is a clean status line and a pull that returns rather
    than raises, skipped by the scheduler job by name;
  * the key is sent as ``Authorization: Bearer <key>`` -- the header the
    official SDK sends -- to the GraphQL endpoint, and nothing else carries
    it: no result, no error, no watermark, no status, no str() of anything;
  * the query is ONE module constant, naming the fields the schema
    verifies (campaignDelivery, records, metrics.cost ...);
  * a sample response parses: a MoneyValue string is dollars, a missing
    metric is zero, a record with no campaign id or no day is skipped and
    counted, video completions land in completes and video starts in
    video_views;
  * pages are followed on the cursor, and a Progress payload is polled;
  * a pull lands rows as platform stackadapt / source native and stamps
    the native watermark the provider normalize defers to.
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

TMP = tempfile.mkdtemp(prefix="s1reports_sa_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-sa-test"
for k in ("STACKADAPT_API_KEY", "STACKADAPT_API_ENDPOINT", "STACKADAPT_AUTH_HEADER",
          "AUDIOGO_API_KEY", "AUDIOGO_API_BASE", "TTD_API_TOKEN"):
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


from modules.reports import normalize, stackadapt, store             # noqa: E402
_reports_testdb.reset(store)

KEY = "sa-live-key-3f9c2b7e1d"


# --------------------------------------------------------- unconfigured
section("Unconfigured is a sentence, not an exception")

st = stackadapt.status()
check("not configured", st["configured"], False)
check("...the line names the variable", st["line"], "StackAdapt: not configured: STACKADAPT_API_KEY (or STACK_ADAPT_API) unset")
check("...and missing lists it", st["missing"], ["STACKADAPT_API_KEY"])
res = stackadapt.pull()
check("a pull returns rather than raising", res["ok"], False)
check("...saying so", res["error"].startswith("not configured"))
check("...and wrote no watermark", store.sync_status().get("stackadapt"), None)


# ------------------------------------------------------------ the query
section("The query is one constant, naming the verified fields")

q = stackadapt.QUERY
check("it is a query on campaignDelivery", "campaignDelivery(" in q and q.startswith("query "))
for name in ("dataType: TABLE", "granularity: DAILY", "date: {from: $from, to: $to}",
             "... on CampaignDeliveryOutcome", "... on Progress", "records(first: $first, after: $after)",
             "pageInfo { hasNextPage endCursor }", "campaign { id name advertiser { id name } }",
             "granularity { time startTime endTime type }",
             "impressions clicks cost conversions", "videoStarts videoCompletions audioStarts audioCompletions"):
    check(f"...asking for {name}", name in q)
src = (ROOT / "modules" / "reports" / "stackadapt.py").read_text(encoding="utf-8")
check("the module says which names were verified and which are assumed",
      "VERIFIED" in src and "ASSUMED" in src and "pa-typescript-sdk" in src)
check("the default endpoint is production GraphQL", stackadapt.DEFAULT_ENDPOINT,
      "https://api.stackadapt.com/graphql")


# ------------------------------------------------------- the stand-in
section("The header, and where the key never goes")

os.environ["STACKADAPT_API_KEY"] = KEY
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


def fake_http(url, *, headers, body, timeout=60):
    calls.append({"url": url, "headers": dict(headers), "body": body})
    return ANSWERS.pop(0) if ANSWERS else _Resp(200, {"data": {"campaignDelivery": {
        "__typename": "CampaignDeliveryOutcome",
        "records": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}})


stackadapt._http = fake_http
check("configured now", stackadapt.configured(), True)
check("the headers carry the key as a bearer token, the SDK's own spelling",
      stackadapt.headers()["Authorization"], "Bearer " + KEY)
check("...and identify the caller", stackadapt.headers()["Source"], "smart1-hub-reports")
res = stackadapt.pull(days=3, today=date(2026, 9, 10))
check("a pull with an empty answer is ok with no rows", (res["ok"], res["rows"]), (True, 0))
check("it posted to the GraphQL endpoint", calls[-1]["url"], "https://api.stackadapt.com/graphql")
check("...with the query constant", calls[-1]["body"]["query"], stackadapt.QUERY)
check("...from the first day of the window, to the day after the last (exclusive)",
      (calls[-1]["body"]["variables"]["from"], calls[-1]["body"]["variables"]["to"]),
      ("2026-09-08", "2026-09-11"))
check("...and the key in the header, nowhere in the body",
      calls[-1]["headers"]["Authorization"] == "Bearer " + KEY and KEY not in json.dumps(calls[-1]["body"]))

os.environ["STACKADAPT_AUTH_HEADER"] = "X-Authorization"
check("the header name is overridable, and a non-Authorization header carries the bare key",
      stackadapt.headers().get("X-Authorization"), KEY)
os.environ.pop("STACKADAPT_AUTH_HEADER")

# The key never leaves: an error that quotes it is redacted.
ANSWERS.append(_Resp(401, {"errors": [{"message": f"bad token {KEY}"}]}, text=f"unauthorized {KEY}"))
res = stackadapt.pull(days=3, today=date(2026, 9, 10))
check("a refusal is ok=False with the platform's sentence", res["ok"], False)
check("...never carrying the key", KEY not in str(res) and KEY not in json.dumps(res))
check("...and the watermark carries the error without it",
      "HTTP 401" in store.sync_status()["stackadapt"]["error"]
      and KEY not in json.dumps(store.sync_status()))
check("...and neither does status()", KEY not in json.dumps(stackadapt.status()))
ANSWERS.append(_Resp(200, {"errors": [{"message": "Field 'x' doesn't exist"}]}))
res = stackadapt.pull(days=3, today=date(2026, 9, 10))
check("a GraphQL error body is a refusal by message", "GraphQL errors" in res["error"] and "Field 'x'" in res["error"])
ANSWERS.append(_Resp(200, None, text="<html>"))
res = stackadapt.pull(days=3, today=date(2026, 9, 10))
check("a non-JSON answer is said", "not JSON" in res["error"])


# ------------------------------------------------------------ parsing
section("A sample response parses defensively")


def node(cid, day, **m):
    return {"campaign": {"id": cid, "name": f"S1M | acme | Streaming Audio | {cid}",
                         "advertiser": {"id": "adv-7", "name": "Acme Co"}},
            "granularity": {"time": day, "startTime": f"{day}T00:00:00-04:00",
                            "endTime": f"{day}T23:59:59-04:00", "type": "DAILY"},
            "metrics": m}


parsed = stackadapt.parse_records([
    node("c-1", "2026-09-09", impressions=1000, clicks=12, cost="12.50", conversions=2,
         videoStarts=400, videoCompletions=300),
    node("c-1", "2026-09-10", impressions="2,000", clicks=None, cost=None),
    node("c-2", "2026-09-10", impressions=50, clicks=1, cost="0.75", audioStarts=40, audioCompletions=30),
    {"campaign": {"id": "", "name": "no id"}, "granularity": {"startTime": "2026-09-10T00:00:00Z"}, "metrics": {}},
    {"campaign": {"id": "c-9"}, "granularity": {"time": "September"}, "metrics": {"impressions": 5}},
    "garbage",
])
rows = {(r["campaign_id"], r["date"].isoformat()): r for r in parsed["rows"]}
check("three rows parsed, three skipped and counted", (len(parsed["rows"]), parsed["skipped"]), (3, 3))
r1 = rows[("c-1", "2026-09-09")]
check("the MoneyValue string is dollars", r1["spend"], 12.5)
check("...impressions, clicks, conversions as ints", (r1["impressions"], r1["clicks"], r1["conversions"]), (1000, 12, 2))
check("...video completions in completes, starts in video_views", (r1["completes"], r1["video_views"]), (300, 400))
check("...the advertiser as the account, named in extras",
      (r1["account_id"], r1["extras"]["advertiser_name"]), ("adv-7", "Acme Co"))
check("...platform stackadapt, source native", (r1["platform"], r1["source"]), ("stackadapt", "native"))
r2 = rows[("c-1", "2026-09-10")]
check("a missing metric is zero, a formatted number is read",
      (r2["impressions"], r2["clicks"], r2["spend"]), (2000, 0, 0.0))
check("...and no completes key where nothing was reported", "completes" not in r2)
r3 = rows[("c-2", "2026-09-10")]
check("audio completions land in completes too", r3["completes"], 30)
check("...with the audio figures kept in extras",
      (r3["extras"]["audioStarts"], r3["extras"]["audioCompletions"]), (40, 30))
check("the day comes off startTime, in the platform's own zone", r3["date"], date(2026, 9, 10))


# -------------------------------------------------------- paging, progress
section("Pages are followed and Progress is polled")

calls.clear()
page1 = {"data": {"campaignDelivery": {"__typename": "CampaignDeliveryOutcome", "records": {
    "pageInfo": {"hasNextPage": True, "endCursor": "cur-1"},
    "nodes": [node("c-1", "2026-09-09", impressions=10, clicks=1, cost="1.00")]}}}}
page2 = {"data": {"campaignDelivery": {"__typename": "CampaignDeliveryOutcome", "records": {
    "pageInfo": {"hasNextPage": False, "endCursor": None},
    "nodes": [node("c-2", "2026-09-09", impressions=20, clicks=2, cost="2.00")]}}}}
ANSWERS.extend([_Resp(200, {"data": {"campaignDelivery": {"__typename": "Progress", "_": None}}}),
                _Resp(200, page1), _Resp(200, page2)])
slept = []
res = stackadapt.pull(days=2, today=date(2026, 9, 10), sleep=slept.append)
check("a Progress payload was waited on once", slept, [stackadapt.PROGRESS_WAIT])
check("...then two pages read", res["pages"], 2)
check("...the second asked for with the cursor", calls[-1]["body"]["variables"]["after"], "cur-1")
check("...landing both rows", res["rows"], 2)
check("...and counting the advertisers and campaigns", (res["advertisers"], res["campaigns"]), (1, 2))
facts = store.facts_for  # noqa: F841 - the rows are read back below

db = store.SessionLocal()
try:
    got = db.query(store.AdPerfDaily).filter(store.AdPerfDaily.platform == "stackadapt").all()
    check("the rows are in the fact table as native", sorted((r.campaign_id, r.source) for r in got),
          [("c-2", "native"), ("c-1", "native")][::-1])
finally:
    db.close()
check("the native watermark is stamped", store.sync_status()["stackadapt"]["source"], "native")
check("...so the provider normalize defers to it", store.native_is_current("stackadapt"), True)
tables = normalize.schema_tables()
out = normalize.run(today=date(2026, 9, 10))
check("...and says so on its own answer", out["stackadapt"].get("native"), True)

# The wait is bounded by wall clock, because the pull runs on the one
# scheduler thread every job shares. With the clock advancing six seconds
# per five-second sleep and a twenty-second budget, the fourth Progress
# answer finds no room for another wait: the report is PENDING -- not
# failed -- nothing is stamped on the watermark, and the next pull asks
# again. Four answers are queued, four are consumed.
clock = [0.0]
slept = []


def _tick(seconds):
    slept.append(seconds)
    clock[0] += seconds + 1


before_wm = dict(store.sync_status()["stackadapt"])
ANSWERS.extend([_Resp(200, {"data": {"campaignDelivery": {"__typename": "Progress", "_": None}}})] * 4)
res = stackadapt.pull(days=2, today=date(2026, 9, 10), sleep=_tick, clock=lambda: clock[0], budget=20)
check("past the wait budget the report is pending rather than failed",
      (res["pending"], res["ok"], res["rows"]), (True, False, 0))
check("...saying so, with the seconds spent", "still preparing after 18s" in res["error"])
check("...after exactly the waits the budget had room for", slept, [5, 5, 5])
check("...every queued Progress answer was consumed, none left for the next section", ANSWERS, [])
check("the watermark is untouched: nothing landed and nothing failed",
      store.sync_status()["stackadapt"], before_wm)
check("...so the provider normalize still defers to the last good pull",
      store.native_is_current("stackadapt"), True)
check("the module's own note carries pending", stackadapt._remembered().get("pending"), True)
check("...and the index line says so", "still preparing" in stackadapt.status()["line"])
check("the budget is a house number, named beside the polls",
      (stackadapt.BUDGET_SECONDS, stackadapt.PROGRESS_WAIT * stackadapt.PROGRESS_TRIES), (20, 30))
check("ReportPending is a StackAdaptError, so a caller catching refusals still catches it",
      issubclass(stackadapt.ReportPending, stackadapt.StackAdaptError), True)

ANSWERS.extend([_Resp(200, {"data": {"campaignDelivery": {"__typename": "Progress", "_": None}}})] * 8)
res = stackadapt.pull(days=2, today=date(2026, 9, 10), sleep=lambda s: None)
check("a report that never finishes is refused by name after the polls, whatever the clock says",
      "still in progress" in res["error"] and not res["pending"])
check("...and that one IS on the watermark", "still in progress" in store.sync_status()["stackadapt"]["error"])
ANSWERS.clear()


# --------------------------------------------------------- the status line
section("The status line")

st = stackadapt.status()
check("connected, with the advertiser count and the last pull", st["connected"] and "advertisers" in st["line"])
check("...naming the last error", "still in progress" in st["line"])
check("...and never the key", KEY not in json.dumps(st))


# ---------------------------------------------------------- scheduler
section("The scheduler job")

from hub import scheduler                                            # noqa: E402

src = (ROOT / "hub" / "scheduler.py").read_text(encoding="utf-8")
body = src[src.index("def job_reports_native_pull"):src.index("\nJOBS = {")]
check("the native job pulls StackAdapt in the same loop", '("stackadapt", stackadapt.pull)' in body)
os.environ.pop("STACKADAPT_API_KEY")
from flask import Flask                                              # noqa: E402
out = scheduler.JOBS["reports_native"][1](Flask("t"))
check("unconfigured, the job skips it by name", "stackadapt" in out["skipped"])
check("...with no error", "stackadapt" not in out["errors"])

# A pending report is the job's fourth answer about a platform: not
# skipped, not failed, asked again next tick.
_real_pull = stackadapt.pull
stackadapt.pull = lambda **kw: {"ok": False, "rows": 0, "pending": True,
                                "error": "the report was still preparing after 18s; the next pull asks again"}
try:
    out = scheduler.JOBS["reports_native"][1](Flask("t"))
finally:
    stackadapt.pull = _real_pull
check("a pending report is counted apart from the failures", out["pending"], ["stackadapt"])
check("...and is not an error", "stackadapt" not in out["errors"] and "stackadapt" not in out["skipped"])

for f in ("env.example", "render.yaml"):
    check(f"{f} documents STACKADAPT_API_KEY", "STACKADAPT_API_KEY" in (ROOT / f).read_text(encoding="utf-8"))
check("the index prints the status line", "NATIVE_PULLS" in (ROOT / "modules" / "reports" / "app.py").read_text())

# ------------------------------------------- what a review found afterwards
section("The failures that used to read as a working feed")

os.environ["STACKADAPT_API_KEY"] = KEY      # the status-line section unset it

# A record with no advertiser id was filed under "advertiser:unknown", which
# is part of store._FACT_KEY -- so the same campaign-day arrived twice once
# the platform named the advertiser, and a client's report added them.
node = lambda adv: {"campaign": {"id": "c-dup", "name": "Acme CTV", "advertiser": adv},
                    "granularity": {"startTime": "2026-09-16T00:00:00Z"},
                    "metrics": {"impressions": 1000, "clicks": 10, "cost": "50.00"}}
anon = stackadapt.parse_records([node({})])
check("a record with no advertiser id is skipped and counted, never keyed on a sentinel",
      (anon["rows"], anon["skipped"]), ([], 1))
store.upsert_rows(anon["rows"] + stackadapt.parse_records(
    [node({"id": "adv-7", "name": "Acme"})])["rows"])
from modules.reports.store import SessionLocal, AdPerfDaily                # noqa: E402
_db = SessionLocal()
try:
    held = _db.query(AdPerfDaily).filter_by(platform="stackadapt", campaign_id="c-dup").all()
finally:
    _db.close()
check("...so one campaign-day is one row, whatever the platform left out",
      (len(held), float(held[0].spend), held[0].account_id), (1, 50.0, "adv-7"))

# Records read and none filed is the ASSUMED names being wrong. It answered
# ok with an empty error: a clean watermark, "connected" on the index, and
# report_schedule marking the day complete.
WRONG = [{"campaign": {"id": f"c{i}", "name": "n", "advertiser": {"id": "a1"}},
          "granularity": {"beginsAt": "2026-09-16T00:00:00Z"},   # not startTime
          "metrics": {"impressions": 100, "clicks": 2, "cost": "5.00"}} for i in range(200)]
parsed = stackadapt.parse_records(WRONG)
check("every record unreadable parses to nothing, and counts them",
      (parsed["rows"], parsed["skipped"]), ([], 200))
_real_fetch = stackadapt.fetch
stackadapt.fetch = lambda *a, **kw: {"rows": [], "skipped": 200, "pages": 1, "progress_waits": 0}
try:
    res = stackadapt.pull()
finally:
    stackadapt.fetch = _real_fetch
check("a pull that read 200 records and filed none is NOT ok", res["ok"], False)
check("...saying the names this file assumes are wrong",
      ("could file none of them" in res["error"], "200" in res["error"]), (True, True))
check("...and the watermark carries it rather than reading clean",
      "could file none" in (store.sync_status()["stackadapt"]["error"] or ""))
check("...and nothing in it carries the key",
      KEY in json.dumps([res, stackadapt.status()], default=str), False)

# A run that landed nothing must not overwrite last night's counts with zero.
stackadapt._remember({"ok": True, "rows": 120, "advertisers": 7, "campaigns": 30,
                      "at": "2026-09-16T03:00:00+00:00"})
def _pending(*a, **kw):
    raise stackadapt.ReportPending("the report was still preparing after 20s")
stackadapt.fetch = _pending
try:
    res = stackadapt.pull()
finally:
    stackadapt.fetch = _real_fetch
line = stackadapt.status()["line"]
check("a pending tick keeps last night's advertiser count rather than printing zero",
      ("7 advertisers" in line, "0 advertisers" in line), (True, False))
note = stackadapt._remembered()
check("...and keeps when the last pull actually was, rather than stamping now",
      (note["at"], note["rows"], note["campaigns"]), ("2026-09-16T03:00:00+00:00", 120, 30))
check("...while the run itself still reports pending, and stamps no watermark",
      (res["pending"], res["ok"], "preparing" in res["error"]), (True, False, True))


shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
