"""Feed health, and the day a client's figures run through.

    python3 test_reports_health.py

No pytest, no new dependencies, a temporary data directory and the reports
database _reports_testdb.py binds (a SQLite file here; the job's Postgres
under checks.yml's second run).

What it holds:

  * modules/reports/health.feeds() gives every platform one of four states
    -- never, failing, stale, ok -- and the counts, and a store that will
    not answer is measured: False rather than an empty, healthy-looking list;
  * status_row() is skipped with nothing synced, warn naming the failing and
    stale platforms, and ERROR when the module is on the SQLite fallback in
    production -- the file on the data disk a deploy wipes;
  * scoreboard() says which kind of empty it is, and the hub route that
    serves it refuses a stranger;
  * the dashboard draws the card and the index draws the pill from the SAME
    reading, so the three screens cannot disagree about a feed;
  * the client's page, data.json and PDF say the day the figures run through,
    and say in words when that day is more than two days old -- never
    implying today.
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_health_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-health-test"
os.environ["PUBLIC_BASE_URL"] = "https://hub.example.test"
os.environ.pop("RENDER", None)
os.environ.pop("RENDER_SERVICE_ID", None)

_passed = _failed = 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from werkzeug.test import Client                                     # noqa: E402

import wsgi                                                          # noqa: E402
from hub import auth                                                 # noqa: E402
from modules.reports import client_view, health, store               # noqa: E402
_reports_testdb.reset(store)

TODAY = date.today()
H = {"Host": "localhost"}


# ---------------------------------------------------------- four states
section("Every platform is one of four states, and a store that refuses is not measured")

f = health.feeds(TODAY)
check("measured, thirteen platforms", (f["measured"], len(f["platforms"])), (True, 13))
check("nothing has synced: every platform is 'never'", f["never"], 13)
check("...which is a fact, not a fault: no failing, no stale", (f["failing"], f["stale"]), (0, 0))
check("the binding is reported", f["binding"] in ("reports", "hub", "sqlite"))

store.upsert_rows([{"platform": "ttd", "account_id": "adv-1", "campaign_id": "c-1",
                    "campaign_name": "S1M | Acme Co | Streaming TV | Q3",
                    "date": TODAY - timedelta(days=1), "spend": 100, "impressions": 10000,
                    "clicks": 20, "conversions": 1, "completes": 8000, "source": "native"}])
store.record_sync("ttd", rows=1, source="native")
store.upsert_rows([{"platform": "google", "account_id": "123", "campaign_id": "g-1",
                    "campaign_name": "old", "date": TODAY - timedelta(days=6), "spend": 10,
                    "impressions": 100, "clicks": 1, "conversions": 0, "source": "native"}])
store.record_sync("google", rows=1, source="native")
store.record_sync("meta", rows=0, error="table facebook_ads is not in the schema")

by = {p["platform"]: p for p in health.feeds(TODAY)["platforms"]}
check("a platform with a row from yesterday is ok", by["ttd"]["state"], "ok")
check("...and its detail names the day it runs through", by["ttd"]["detail"], f"through {(TODAY - timedelta(days=1)).isoformat()}")
check("a platform whose newest day is six days old is stale", by["google"]["state"], "stale")
check("...saying how old", "6 days ago" in by["google"]["detail"])
check("a platform whose last run recorded an error is failing, whatever its age",
      (by["meta"]["state"], by["meta"]["detail"]), ("failing", "table facebook_ads is not in the schema"))
check("a platform nothing has written is never", by["linkedin"]["state"], "never")
check("the stale threshold is three days", health.STALE_DAYS, 3)
check("...so a two-day-old newest day is still ok", (lambda: (
    store.upsert_rows([{"platform": "bing", "account_id": "b", "campaign_id": "b-1", "campaign_name": "b",
                        "date": TODAY - timedelta(days=3), "spend": 1, "impressions": 1, "clicks": 0,
                        "source": "native"}]), store.record_sync("bing", rows=1, source="native"),
    {p["platform"]: p for p in health.feeds(TODAY)["platforms"]}["bing"]["state"])[-1])(), "ok")

_ps = store.platform_status
store.platform_status = lambda: (_ for _ in ()).throw(RuntimeError("db gone"))
f = health.feeds(TODAY)
store.platform_status = _ps
check("a store that will not answer is not measured", (f["measured"], f["platforms"]), (False, []))
check("...with the reason", "RuntimeError" in f["note"])


# ------------------------------------------------------------ /status row
section("The /status row")

state, msg = health.status_row()
check("with a failing and a stale feed the row is a warn", state, "warn")
check("...naming both platforms, and where to look",
      "failing: Meta" in msg and "stale" in msg and "Google Ads" in msg and "/reports/" in msg)

store.record_sync("meta", rows=3, error="")
store.upsert_rows([{"platform": "google", "account_id": "123", "campaign_id": "g-1",
                    "campaign_name": "old", "date": TODAY - timedelta(days=1), "spend": 10,
                    "impressions": 100, "clicks": 1, "conversions": 0, "source": "native"}])
state, msg = health.status_row()
check("with every feed current the row is ok, counting the never-synced apart",
      (state, "3 feeds current" in msg or "4 feeds current" in msg, "never synced" in msg), ("ok", True, True))

_ps = store.platform_status
store.platform_status = lambda: []
check("with nothing synced at all the row is skipped, not an ok",
      health.status_row()[0], "skipped")
store.platform_status = _ps

# The SQLite fallback in production. Render sets RENDER on every service;
# binding() reads the two URL variables at call time.
_saved = {k: os.environ.pop(k, None) for k in ("REPORTS_DATABASE_URL", "DATABASE_URL")}
os.environ["RENDER"] = "true"
check("on Render with no database variable the binding is the SQLite fallback", store.binding(), "sqlite")
check("...which the status row reports as an ERROR naming the variable to set",
      (health.status_row()[0], "REPORTS_DATABASE_URL" in health.status_row()[1]), ("error", True))
check("...and the scoreboard refuses to measure rather than drawing counts",
      health.scoreboard()["measured"], False)
os.environ.pop("RENDER", None)
check("off Render the same binding is not an error", health.binding_problem(), "")
for k, v in _saved.items():
    if v is not None:
        os.environ[k] = v
check("with the variable back the problem is gone", health.binding_problem(), "")


# ------------------------------------------------------------- scoreboard
section("The scoreboard, and its route")

store.map_campaign("ttd", "adv-1", "c-1", client="n:acme-co", client_name="Acme Co",
                   product="Streaming TV", mapped_by="Todd")
sb = health.scoreboard()
check("measured, with the nine counts", (sb["measured"], sorted(sb["counts"])),
      (True, ["alerts", "drift", "failing", "held", "never", "ok", "pending", "stale", "unmapped"]))
check("nothing is held in quarantine", sb["counts"]["held"], 0)
check("a mapping a person made is not pending", sb["counts"]["pending"], 0)
check("unmapped counts the campaigns filed under nobody", sb["counts"]["unmapped"], 2)
check("alerts is a number rather than None when the snapshot table answers", sb["counts"]["alerts"], 0)
check("the line says what is filed and what is pacing, in words",
      "filed under nobody" in sb["line"] and "nothing pacing off" in sb["line"])
check("every figure has somewhere to open", sorted(sb["urls"]), ["alerts", "drift", "feeds", "held", "pacing", "pending", "unmapped"])

_ps = store.platform_status
store.platform_status = lambda: [dict(r, synced_at=None, sync_run_at=None, sync_error="", latest_date=None)
                                 for r in _ps()]
sb0 = health.scoreboard()
store.platform_status = _ps
check("with no feed ever synced the empty is named rather than drawn as noughts",
      (sb0["empty"], "No feed has synced yet" in sb0["line"]), ("no_feeds", True))

anon = Client(wsgi.application)
check("the hub route refuses a stranger", anon.get("/api/reports/scoreboard", headers=H).status_code in (401, 302, 403))
staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")
r = staff.get("/api/reports/scoreboard", headers=H)
check("...and answers staff with the same reading", (r.status_code, r.get_json()["counts"]["unmapped"]), (200, 2))
st = staff.get("/api/status", headers=H).get_json()
row = [x for x in st.get("checks", []) if x["name"] == "Reports feeds"]
check("/api/status carries the feeds row", bool(row) and row[0]["status"] in ("ok", "warn", "skipped", "error"))
dash = staff.get("/", headers=H).get_data(as_text=True)
check("the dashboard draws the card", 'id="reportsboard"' in dash and "/api/reports/scoreboard" in dash)
from hub import help as hub_help                                     # noqa: E402
check("...and its bubble is registered", hub_help.get("hub.dashboard.reports") is not None)
ix = staff.get("/reports/", headers=H).get_data(as_text=True)
check("the index draws a Health column from the same reading", "<th>Health</th>" in ix and ">Current<" in ix)


# ---------------------------------------------------- the day the figures run through
section("The client's page says the day its figures run through")

link = store.create_link("n:acme-co", client_name="Acme Co", created_by="Todd")
agg = client_view.aggregate(link, "mtd", TODAY) if TODAY.day > 1 else client_view.aggregate(link, "90d", TODAY)
yday = TODAY - timedelta(days=1)
check("data_through is the newest day with a figure", agg["data_through"], yday.isoformat())
check("...labeled the way a person says a date", agg["data_through_label"], f"{yday:%B %-d}")
check("...one day old is not stale", (agg["data_lag_days"], agg["data_stale"]), (1, False))
check("the threshold is two days", client_view.DATA_STALE_DAYS, 2)

pub = anon.get(f"/reports/r/c/{link.token}?period=90d", headers=H).get_data(as_text=True)
check("the page prints the through-date under the title", f"figures through {yday:%B %-d}" in pub)
check("...and no stale note while it is fresh", "still arriving" not in pub)

# A client whose newest row is a week old: the page says so, and so does the PDF.
OLD = "n:old-co"
store.upsert_rows([{"platform": "ttd", "account_id": "adv-9", "campaign_id": "o-1", "campaign_name": "old",
                    "date": TODAY - timedelta(days=7), "spend": 5, "impressions": 500, "clicks": 3,
                    "conversions": 0, "completes": 400, "source": "native"}])
store.map_campaign("ttd", "adv-9", "o-1", client=OLD, client_name="Old Co", product="Streaming TV", mapped_by="Todd")
old_link = store.create_link(OLD, client_name="Old Co", created_by="Todd")
old = client_view.aggregate(old_link, "90d", TODAY)
check("a week-old newest day is stale", (old["data_lag_days"], old["data_stale"]), (7, True))
pub = anon.get(f"/reports/r/c/{old_link.token}?period=90d", headers=H).get_data(as_text=True)
check("the page says so in words, naming the day", "the days since are still arriving" in pub
      and f"Figures run through {(TODAY - timedelta(days=7)):%B %-d}" in pub)
check("...naming no platform and no plumbing", not any(w in pub for w in ("sync", "feed", "provider", "Trade Desk")))
pdf = anon.get(f"/reports/r/c/{old_link.token}.pdf?period=90d", headers=H)
check("the PDF builds", (pdf.status_code, pdf.mimetype), (200, "application/pdf"))
from modules.reports import client_pdf                               # noqa: E402
import inspect                                                       # noqa: E402
check("...and prints the through-date on the same line as the period",
      "figures through" in inspect.getsource(client_pdf.build))
data = anon.get(f"/reports/r/c/{old_link.token}/data.json?period=90d", headers=H).get_json()
check("data.json carries the same three facts",
      (data["data_through"], data["data_lag_days"], data["data_stale"]),
      ((TODAY - timedelta(days=7)).isoformat(), 7, True))
empty_link = store.create_link("n:nobody", client_name="Nobody", created_by="Todd")
e = client_view.aggregate(empty_link, "mtd", TODAY)
check("a client with no rows has no through-date rather than a stale one",
      (e["data_through"], e["data_stale"]), (None, False))


# ------------------------------------------------------------- the gate
section("Wired in")

ci = (ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
check("checks.yml runs this file", "python3 test_reports_health.py" in ci)
loop = ci[ci.index("The reports tests against Postgres"):].split("done", 1)[0]
check("...and the Postgres loop runs it too", "test_reports_health.py" in loop)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
