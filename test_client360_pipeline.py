"""Client 360's Pipeline & leads card -- hub/suite_pipeline.py.

    python3 test_client360_pipeline.py

Same shape as the other test files here -- no pytest, no new dependencies,
a temporary data directory and a throwaway SQLite database. HighLevel is
stood in for by a fake ``requests.get``; the renderer is lifted out of the
template and driven in node, test_client360_layout.py's arrangement.

What it holds:

  1. **The counting is right, from raw rows.** Open by stage, new this week
     and month, won and lost inside 30 days, open-and-untouched as "going
     cold" -- with the dates HighLevel actually sends (ISO with a zone,
     ISO without, epoch milliseconds).
  2. **Three answers, never two.** No sub-account is ``not_connected``; a
     token that will not mint is ``not_connected``; a read that fails is
     ``not_measured`` with the reason. Zero leads is never the answer to
     "could not read".
  3. **The token never lands in the answer** -- not in ``detail``, not
     anywhere in the JSON.
  4. **Paging stops when Suite says so**, and a client past the cap is
     flagged rather than mis-totalled.
  5. **The route and the card**: gated, under /api/client/, drawn beside
     the Suite Account card under Overview, loaded with the record.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="c360pipe_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "c360-pipe-test"
os.environ["PANEL_PASSWORD"] = "c360-pipe-pass"
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")

_passed, _failed = 0, 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import suite_pipeline as sp          # noqa: E402

NOW = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)
TOKEN = "eyJ-secret-location-token-DO-NOT-LEAK"


def ago(days, hours=0):
    return (NOW - timedelta(days=days, hours=hours)).isoformat().replace("+00:00", "Z")


PIPES = [{"id": "p1", "name": "Sales", "stages": [
            {"id": "s1", "name": "New lead"}, {"id": "s2", "name": "Quote sent"}, {"id": "s3", "name": "Booked"}]},
         {"id": "p2", "name": "Events", "stages": [{"id": "e1", "name": "Inquiry"}]}]

ROWS = [
    # open, staged, dated three ways
    {"id": "o1", "name": "Smith wedding", "status": "open", "monetaryValue": 2500, "pipelineId": "p1",
     "pipelineStageId": "s2", "createdAt": ago(2), "updatedAt": ago(1), "contact": {"name": "Ann Smith"}, "source": "Website form"},
    {"id": "o2", "name": "Tasting for 12", "status": "open", "monetaryValue": "480", "pipelineId": "p1",
     "pipelineStageId": "s1", "createdAt": (NOW - timedelta(days=5)).replace(tzinfo=None).isoformat(), "updatedAt": ago(5)},
    {"id": "o3", "name": "Corporate retreat", "status": "open", "monetaryValue": 9000, "pipelineId": "p1",
     "pipelineStageId": "s2", "createdAt": int((NOW - timedelta(days=50)).timestamp() * 1000),
     "updatedAt": int((NOW - timedelta(days=40)).timestamp() * 1000)},          # going cold
    {"id": "o4", "name": "Harvest festival", "status": "open", "monetaryValue": 0, "pipelineId": "p2",
     "pipelineStageId": "e1", "createdAt": ago(20), "updatedAt": ago(3)},
    {"id": "o5", "name": "Ghost stage", "status": "open", "monetaryValue": 100, "pipelineId": "p1",
     "pipelineStageId": "gone", "createdAt": ago(1), "updatedAt": ago(1)},
    # closed
    {"id": "w1", "name": "Jones wedding", "status": "won", "monetaryValue": 4200, "pipelineId": "p1",
     "pipelineStageId": "s3", "createdAt": ago(60), "lastStatusChangeAt": ago(10), "updatedAt": ago(10)},
    {"id": "w2", "name": "Old win", "status": "won", "monetaryValue": 999, "pipelineId": "p1",
     "pipelineStageId": "s3", "createdAt": ago(120), "lastStatusChangeAt": ago(45), "updatedAt": ago(45)},
    {"id": "l1", "name": "Went elsewhere", "status": "lost", "monetaryValue": 700, "pipelineId": "p1",
     "pipelineStageId": "s2", "createdAt": ago(25), "lastStatusChangeAt": ago(4), "updatedAt": ago(4)},
    {"id": "a1", "name": "Abandoned", "status": "abandoned", "pipelineId": "p1",
     "pipelineStageId": "s1", "createdAt": ago(9), "lastStatusChangeAt": ago(2), "updatedAt": ago(2)},
]

# ------------------------------------------------------------------------
section("1. The counting, from raw rows")

s = sp.summarize(PIPES, ROWS, now=NOW)
t = s["totals"]
check("open counts every open row, ghost stage included", t["open"], 5)
check("open value sums them, strings and zeros included", t["open_value"], 12080.0)
check("new this week: created inside 7 days, any status", t["new_week"], 3)
check("new this month: created inside 30 days, any status", t["new_month"], 6)
check("won in 30 days counts by status change, not creation", (t["won_month"], t["won_value_month"]), (1, 4200.0))
check("lost in 30 days includes abandoned", t["lost_month"], 2)
check("going cold: open and untouched past the stale window", t["stale"], 1)
check("a lead in a stage that no longer exists is counted, and flagged", t["unstaged"], 1)
check("every row is on the total", t["all"], 9)

sales = next(p for p in s["pipelines"] if p["id"] == "p1")
check("per-pipeline open and value exclude the ghost stage", (sales["open"], sales["value"]), (3, 11980.0))
check("stages keep Suite's order with their counts",
      [(st["name"], st["count"]) for st in sales["stages"]], [("New lead", 1), ("Quote sent", 2), ("Booked", 0)])
events = next(p for p in s["pipelines"] if p["id"] == "p2")
check("a second pipeline is counted on its own", (events["open"], events["stages"][0]["count"]), (1, 1))

check("newest leads are newest-created first", [r["name"] for r in s["recent"]][:3],
      ["Ghost stage", "Smith wedding", "Tasting for 12"])
first = s["recent"][1]
check("a recent row carries stage, source and contact",
      (first["stage"], first["source"], first["contact"], first["pipeline"]), ("Quote sent", "Website form", "Ann Smith", "Sales"))
check("the recent list is capped", len(sp.summarize([], [dict(ROWS[0], id=str(i)) for i in range(20)], now=NOW)["recent"]), sp.RECENT)

check("dates: zoned ISO", sp._when("2026-09-14T15:00:00Z").isoformat(), "2026-09-14T15:00:00+00:00")
check("dates: naive ISO is taken as UTC", sp._when("2026-09-14T15:00:00").isoformat(), "2026-09-14T15:00:00+00:00")
check("dates: epoch milliseconds", sp._when(1789398000000).year, 2026)
check("dates: rubbish is None, never today", (sp._when("soon"), sp._when(""), sp._when(None)), (None, None, None))
check("no pipelines and no rows is an empty, measured answer",
      sp.summarize([], [], now=NOW)["totals"]["open"], 0)

# ------------------------------------------------------------------------
section("2. Three answers, from a faked Suite")

import requests  # noqa: E402
from hub import suite_accounts  # noqa: E402

CALLS = []


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = 200 <= status < 300
        self.text = json.dumps(body)

    def json(self):
        return self._body


def fake_get(url, headers=None, params=None, timeout=None):
    CALLS.append((url, dict(params or {}), headers.get("Authorization", "")))
    if url.endswith("/opportunities/pipelines"):
        return _Resp(200, {"pipelines": PIPES})
    if url.endswith("/opportunities/search"):
        page = int(params.get("page") or 1)
        if FAKE.get("forbid"):
            return _Resp(401, {"message": "Bad token " + TOKEN})
        if FAKE.get("many"):
            return _Resp(200, {"opportunities": [dict(ROWS[0], id=f"m{page}-{i}") for i in range(100)],
                               "meta": {"nextPage": page + 1}})
        if FAKE.get("two_pages"):
            if page == 1:
                return _Resp(200, {"opportunities": [dict(ROWS[0], id=f"a{i}") for i in range(100)],
                                   "meta": {"nextPage": 2}})
            return _Resp(200, {"opportunities": ROWS, "meta": {"nextPage": None}})
        return _Resp(200, {"opportunities": ROWS, "meta": {}})
    return _Resp(404, {})


FAKE = {}
ACCT = {"state": "connected", "detail": "", "token": TOKEN, "location_id": "loc123"}
_real_get, _real_token_for = requests.get, suite_accounts.token_for
requests.get = fake_get
suite_accounts.token_for = lambda name, url="": dict(ACCT)

d = sp.for_client("Buckeye Lake Winery", "buckeyelakewinery.com", now=NOW, fresh=True)
check("connected: measured, with the numbers", (d["state"], d["measured"], d["totals"]["open"]), ("connected", True, 5))
check("the read used the client's own sub-account token",
      all(a == f"Bearer {TOKEN}" for _, _, a in CALLS) and all(p.get("locationId", p.get("location_id")) == "loc123" for _, p, _ in CALLS), True)
check("the token is nowhere in the answer", TOKEN in json.dumps(d), False)
check("the card gets a link into that sub-account", d["suite_url"], "/suite/?manage=loc123")
check("a single page stops after one search call", sum(1 for u, _, _ in CALLS if u.endswith("/search")), 1)

CALLS.clear()
FAKE["two_pages"] = True
d = sp.for_client("Buckeye Lake Winery", now=NOW, fresh=True)
check("paging follows nextPage and stops when it is gone", (sum(1 for u, _, _ in CALLS if u.endswith("/search")), d["totals"]["all"], d["truncated"]), (2, 109, False))
FAKE.clear()

CALLS.clear()
FAKE["many"] = True
d = sp.for_client("Buckeye Lake Winery", now=NOW, fresh=True)
check("a client past the page cap is flagged, not mis-totalled", (sum(1 for u, _, _ in CALLS if u.endswith("/search")), d["truncated"]), (sp.MAX_PAGES, True))
FAKE.clear()

FAKE["forbid"] = True
d = sp.for_client("Buckeye Lake Winery", now=NOW, fresh=True)
check("a refused read is not measured, and names the scope", (d["state"], d["measured"], "opportunities.readonly" in d["detail"]), ("not_measured", False, True))
check("and the body HighLevel sent back is not echoed", TOKEN in json.dumps(d), False)
check("nothing is counted on that path", "totals" in d, False)
FAKE.clear()

ACCT.update(state="not_connected", token=None, detail="Smart 1 Suite would not issue a token for this client's sub-account (GhlOAuthError). Usually that means the app is not installed on it.")
d = sp.for_client("Buckeye Lake Winery", now=NOW, fresh=True)
check("no token is not connected, with Suite's reason", (d["state"], "not installed" in d["detail"]), ("not_connected", True))
ACCT.update(state="no_location", detail="No Smart 1 Suite sub-account is recorded for this client.")
d = sp.for_client("Buckeye Lake Winery", now=NOW, fresh=True)
check("no sub-account is not connected", d["state"], "not_connected")
ACCT.update(state="not_measured", detail="The Suite connection could not be read (KeyError).")
d = sp.for_client("Buckeye Lake Winery", now=NOW, fresh=True)
check("a link that could not be read is not measured", d["state"], "not_measured")
ACCT.update(state="connected", token=TOKEN, detail="")

check("an unnamed client is not measured", sp.for_client("", now=NOW)["state"], "not_measured")


def boom(*a, **k):
    raise RuntimeError("wire down")


requests.get = boom
d = sp.for_client("Buckeye Lake Winery", now=NOW, fresh=True)
check("an exception in the read is an answer, not a 500", (d["state"], "RuntimeError" in d["detail"]), ("not_measured", True))
requests.get = fake_get

CALLS.clear()
d1 = sp.for_client("Buckeye Lake Winery", now=NOW, fresh=True)
d2 = sp.for_client("Buckeye Lake Winery", now=NOW)
check("a second read inside the TTL is served from cache", (d2.get("cached"), sum(1 for u, _, _ in CALLS if u.endswith("/search"))), (True, 1))

requests.get, suite_accounts.token_for = _real_get, _real_token_for

# ------------------------------------------------------------------------
section("3. The scope table knows this call site")
from hub import ghl_scopes  # noqa: E402
scope = next((s_ for s_ in ghl_scopes.READ + ghl_scopes.WRITE if s_.name == "opportunities.readonly"), None)
check("opportunities.readonly names hub/suite_pipeline.py", bool(scope) and "hub/suite_pipeline.py" in scope.needed_by, True)

# ------------------------------------------------------------------------
section("4. The route and the card")

from hub import auth, create_hub_app  # noqa: E402
from hub.extensions import create_all  # noqa: E402
app = create_hub_app()
create_all(app)
check("a stranger is refused", app.test_client().get("/api/client/pipeline?name=x").status_code, 401)
staff = app.test_client()
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"))
body = staff.get("/api/client/pipeline?name=Nobody+Known+Here").get_json()
check("the route answers the card's tri-state shape", (body.get("state") in ("not_connected", "not_measured"), "detail" in body), (True, True))
check("an unnamed client is not measured", staff.get("/api/client/pipeline").get_json().get("measured"), False)

def _c360_source():
    """The Client 360 record as one text: the template plus its script modules
    (hub/client360_assets.MODULES), because the record's JavaScript lives in
    files now and a check that asks what the record does reads all of it."""
    import importlib, os as _os, sys as _sys
    _root = _os.path.dirname(_os.path.abspath(__file__))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    return importlib.import_module("hub.client360_assets").source_text()

REC = _c360_source()
check("the card is emitted right after the Suite Account card", REC.find('id="c-ghl"') < REC.find('id="c-pipeline"') < REC.find('data-demo="proposals-card"'), True)
check("the section map claims it for Overview", "'pipeline & leads'" in REC, True)
check("the loader honors the generation guard",
      REC[REC.find("function loadPipeline"):].find("if(gen!==c360Generation) return;") > 0, True)
check("it is loaded with the rest of the record", "loadPipeline(name);" in REC[REC.find("function loadBrandAndWork"):], True)
check("the copy says Smart 1 Suite, never the vendor",
      any(w in REC[REC.find("/* ---- pipeline (lifted"):REC.find("/* ---- end pipeline ----")] for w in ("GoHighLevel", "HighLevel", "GHL")), False)

a = REC.find("/* ---- pipeline (lifted")
b = REC.find("/* ---- end pipeline ----")
SRC = REC[a:b] if 0 < a < b else ""
check("the renderer is marked for lifting", bool(SRC))
if shutil.which("node") and SRC:
    connected = {"state": "connected", "measured": True, "suite_url": "/suite/?manage=loc123", "stale_days": 30,
                 "totals": {"open": 5, "open_value": 12080, "new_week": 4, "new_month": 6, "won_month": 1,
                            "won_value_month": 4200, "lost_month": 2, "stale": 1, "all": 9, "unstaged": 1},
                 "pipelines": [{"id": "p1", "name": "Sales", "open": 3, "value": 11980,
                                "stages": [{"name": "New lead", "count": 1, "value": 480}, {"name": "Quote sent", "count": 2, "value": 11500}, {"name": "Booked", "count": 0, "value": 0}]},
                               {"id": "p2", "name": "Events", "open": 0, "value": 0, "stages": [{"name": "Inquiry", "count": 0, "value": 0}]}],
                 "recent": [{"name": "Smith wedding", "contact": "Ann Smith", "status": "open", "value": 2500, "stage": "Quote sent", "source": "Website form", "created": "2026-09-12T15:00:00+00:00"}]}
    driver = ("const esc=s=>String(s??'');const scStat=(l,v,h,w)=>'['+l+'='+v+(w?'!':'')+']';\n"
              "const Date_=Date;globalThis.Date={now:()=>Date_.parse('2026-09-14T15:00:00Z'),parse:Date_.parse};\n"
              + SRC + "\nconst out={"
              "nc:renderPipeline({state:'not_connected',detail:'No sub-account recorded.'}),"
              "nm:renderPipeline({state:'not_measured',measured:false,detail:'Suite rejected the request (401).'}),"
              "empty:renderPipeline({state:'connected',measured:true,no_pipelines:true,totals:{all:0},pipelines:[],recent:[]}),"
              "ok:renderPipeline(" + json.dumps(connected) + ")"
              "};console.log(JSON.stringify(out));\n")
    r = subprocess.run(["node", "-"], input=driver, capture_output=True, text=True)
    check("the lifted block runs on its own", r.returncode, 0)
    out = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    check("not connected says so and points at the account card", "No sub-account" in out.get("nc", "") and "Suite Account card" in out.get("nc", ""), True)
    check("not measured shows the reason, never a zero", "401" in out.get("nm", "") and "[Open leads=0" not in out.get("nm", ""), True)
    check("no pipeline yet is its own message", "no pipeline set up" in out.get("empty", ""), True)
    ok = out.get("ok", "")
    check("the tiles carry the totals", all(x in ok for x in ("[Open leads=5]", "[Open value=$12,080]", "[New this week=4]", "[Won, 30 days=1 · $4,200]", "[Going cold=1!]")), True)
    check("stages draw with their counts, only for pipelines with something open", "Quote sent" in ok and "Inquiry" not in ok and "Nothing open in Events" in ok, True)
    check("the ghost-stage lead is footnoted", "stage that no longer exists" in ok, True)
    check("the newest lead names its contact, source and age", all(x in ok for x in ("Smith wedding", "Ann Smith", "Website form", "2 days ago")), True)
else:
    print("  skip  node not available")

CI = (ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
check("this file runs in CI", "python3 test_client360_pipeline.py" in CI, True)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
