"""Does the fact table add up to the month the platform would invoice?

    python3 test_reports_reconcile.py

No pytest, no new dependencies, a temporary data directory and the reports
database _reports_testdb.py binds (a SQLite file here; the job's Postgres
under checks.yml's second run). Google is stubbed at the same three seams
test_reports_google_perf.py stubs it at; StackAdapt at its fetch.

What it holds:

  * the window ends yesterday on both sides, and a month with no completed
    day is not measured rather than compared against nothing;
  * Google's customer-level query is the one INDEPENDENT total -- a
    different aggregation the platform computed -- and a refused account is
    named as missing from it rather than silently shrinking it;
  * a provider table is summed whole only through a CONFIRMED map, and a
    StackAdapt month re-read is labeled a re-read, never independent;
  * a platform that cannot be asked is not measured with the reason, and
    the Trade Desk and Suite are not measurable by design and say why;
  * agreement is within a house tolerance the page names; drift is named
    on /status, the dashboard scoreboard and the index; rows held in
    quarantine for the month ride on the row;
  * the ledger keeps one row per platform-month, a state change is logged
    and a state is not, the nightly job is registered, the page and the
    run route refuse a stranger.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_reconcile_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-reconcile-test"
os.environ["PUBLIC_BASE_URL"] = "https://hub.example.test"
for k in ("STACKADAPT_API_KEY", "STACK_ADAPT_API"):
    os.environ.pop(k, None)

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


def entries(event):
    p = Path(os.environ["AUDIT_LOG_PATH"])
    if not p.exists():
        return []
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return [e for e in rows if e.get("type") == event or e.get("action") == event]


from sqlalchemy import text as _sql                                  # noqa: E402
from werkzeug.test import Client                                     # noqa: E402

import wsgi                                                          # noqa: E402
from hub import auth                                                 # noqa: E402
from modules.ads_builder import google_ads                           # noqa: E402
from modules.reports import health, provider_map, reconcile, stackadapt, store  # noqa: E402
_reports_testdb.reset(store, extra_tables=("bing_ads",))

TODAY = date.today()
LAST_START = TODAY.replace(day=1) - timedelta(days=1)
LAST_START = LAST_START.replace(day=1)
LAST = f"{LAST_START:%Y-%m}"
LAST_END = TODAY.replace(day=1) - timedelta(days=1)
H = {"Host": "localhost"}
staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")
anon = Client(wsgi.application)


def fact(platform, account, cid, day, spend, impressions=1000, clicks=5):
    return {"platform": platform, "account_id": account, "campaign_id": cid,
            "campaign_name": "S1M | Acme Co | Paid Search | x", "date": day, "spend": spend,
            "impressions": impressions, "clicks": clicks, "conversions": 0, "source": "native"}


# ---------------------------------------------------------------- window
section("The window ends yesterday, on both sides")

w = reconcile.month_window(LAST, TODAY)
check("a closed month runs through its last day", (w["key"], w["through"]), (LAST, LAST_END))
w = reconcile.month_window(None, TODAY)
check("the current month runs through yesterday", (w["key"], w["through"]),
      (f"{TODAY:%Y-%m}", TODAY - timedelta(days=1)))
w = reconcile.month_window("2026-09", date(2026, 9, 1))
check("on the first of a month there is no completed day yet", w["through"] < w["start"])
row = reconcile.reconcile_platform("google", "2026-09", date(2026, 9, 1))
check("...and the platform is not measured, saying so", (row["state"], row["reason"]),
      ("not_measured", "the month has no completed day yet"))
check("the tolerance is a house figure, and says so", (reconcile.TOLERANCE_PCT, reconcile.TOLERANCE_SOURCE),
      (Decimal("2.0"), "house"))


# ------------------------------------------------------------ google
section("Google's customer-level query is the one independent total")

calls = []
THEIRS = {"micros": 100_000_000, "impressions": 20000, "clicks": 300}


def fake_status(store=None):
    return {"configured": True, "connected": True, "deploy_ready": True, "missing": []}


def fake_accounts(store=None, *, force=False):
    return [
        {"id": "111", "name": "Smart 1 MCC", "is_manager": True, "level": 0, "manager_id": "111"},
        {"id": "222", "name": "Buckeye Lake Winery", "is_manager": False, "level": 1, "manager_id": "111"},
        {"id": "333", "name": "Acme Roofing", "is_manager": False, "level": 1, "manager_id": "111"},
    ]


def fake_search(customer_id, query, *, store=None, login_customer_id=None):
    calls.append((customer_id, login_customer_id, query))
    if customer_id == "333":
        raise google_ads.GoogleAdsError("CUSTOMER_NOT_ENABLED", status=403)
    if "FROM customer" not in query:
        raise AssertionError("the reconcile must ask the customer resource, not campaign rows")
    # The stand-in answers THEIRS for the closed month under test and, for
    # any other window, exactly what our fact table holds -- so only the
    # month this file is about can drift.
    import re
    a, b = re.search(r"BETWEEN '(\S+)' AND '(\S+)'", query).groups()
    if a == LAST_START.isoformat():
        return [{"metrics": {"costMicros": str(THEIRS["micros"]), "impressions": str(THEIRS["impressions"]),
                             "clicks": str(THEIRS["clicks"])}}]
    mine = store.month_totals("google", date.fromisoformat(a), date.fromisoformat(b))
    return [{"metrics": {"costMicros": str(int(mine["spend"] * 1_000_000)),
                         "impressions": str(mine["impressions"]), "clicks": str(mine["clicks"])}}]


google_ads.connection_status = fake_status
google_ads.list_client_accounts = fake_accounts
google_ads.search = fake_search

# Ten days of $10 in the closed month, on one account, two campaigns -- and
# a row in the NEXT month that must not be counted.
store.upsert_rows([fact("google", "222", "g-1", LAST_START + timedelta(days=i), 5) for i in range(10)]
                  + [fact("google", "222", "g-2", LAST_START + timedelta(days=i), 5) for i in range(10)]
                  + [fact("google", "222", "g-1", TODAY.replace(day=1), 999)], today=TODAY)
ours = store.month_totals("google", LAST_START, LAST_END)
check("ours is every google row in the month, mapped or not", (ours["spend"], ours["rows"]), (Decimal("100.00"), 20))

q = reconcile.customer_gaql(LAST_START, LAST_END)
check("the query asks the customer resource with the window as a range and no segment selected",
      "FROM customer" in q and "segments.date BETWEEN" in q and "SELECT metrics.cost_micros" in q
      and "segments.date," not in q.split("FROM")[0])
row = reconcile.reconcile_platform("google", LAST, TODAY)
check("google agrees: $100.00 against $100.00", (row["state"], row["ours"], row["theirs"], row["drift_pct"]),
      ("agree", Decimal("100.00"), Decimal("100.00"), Decimal("0.00")))
check("...independent, naming the aggregation", (row["independent"], row["source_label"]),
      (True, "Google Ads customer-level query across 1 account"))
check("...and the refused account is named as missing from the total, not silently dropped",
      "333: CUSTOMER_NOT_ENABLED" in row["reason"] and "not in the total" in row["reason"])
check("the window sent to Google ends on the month's last day",
      f"BETWEEN '{LAST_START.isoformat()}' AND '{LAST_END.isoformat()}'" in calls[-1][2])
check("...under the manager as the login customer", calls[-1][1], "111")

THEIRS["micros"] = 110_000_000
row = reconcile.reconcile_platform("google", LAST, TODAY)
check("ten dollars apart on their $110 is drift, measured against the platform's figure",
      (row["state"], row["drift_pct"]), ("drift", Decimal("9.09")))
THEIRS["micros"] = 101_500_000
row = reconcile.reconcile_platform("google", LAST, TODAY)
check("a dollar and a half on their $101.50 is inside the tolerance", (row["state"], row["drift_pct"]),
      ("agree", Decimal("1.48")))
THEIRS["micros"] = 0
row = reconcile.reconcile_platform("google", LAST, TODAY)
check("the platform saying nothing ran while we hold rows is a whole-figure drift, not a division by zero",
      (row["state"], row["drift_pct"]), ("drift", Decimal("100.00")))
THEIRS["micros"] = 100_000_000

if TODAY.day > 1:
    store.upsert_rows([fact("google", "222", "g-1", TODAY, 50), fact("google", "222", "g-1", TODAY - timedelta(days=1), 7)],
                      today=TODAY)
    row = reconcile.reconcile_platform("google", None, TODAY)
    check("this month's ours stops at yesterday: today's partial row is not counted",
          row["ours"], Decimal("1006.00"))
    check("...and so does the window sent to Google",
          f"AND '{(TODAY - timedelta(days=1)).isoformat()}'" in calls[-1][2])
else:
    check("(the first of the month: this month is not measured yet)",
          reconcile.reconcile_platform("google", None, TODAY)["state"], "not_measured")

google_ads.connection_status = lambda store=None: {"configured": False, "deploy_ready": False, "missing": ["x"]}
row = reconcile.reconcile_platform("google", LAST, TODAY)
check("not connected is not measured, naming why and never agreeing",
      (row["state"], "not connected" in row["reason"]), ("not_measured", True))
google_ads.connection_status = fake_status


# ----------------------------------------------------- provider tables
section("A provider table is summed whole, through a confirmed map only")

B = provider_map.PLATFORM_SOURCES["bing"]
row = reconcile.reconcile_platform("bing", LAST, TODAY)
check("with no raw table, not measured and said", (row["state"], "does not resolve" in row["reason"]),
      ("not_measured", True))
DT = "DATE" if store.is_postgres() else "TEXT"
with store.engine.begin() as conn:
    conn.execute(_sql(f'CREATE TABLE "{B["table"]}" ("{B["date"]}" {DT}, "{B["account_id"]}" TEXT, '
                      f'"{B["campaign_id"]}" TEXT, "{B["campaign_name"]}" TEXT, "{B["spend"]}" REAL, '
                      f'"{B["impressions"]}" INTEGER, "{B["clicks"]}" INTEGER, "{B["conversions"]}" REAL)'))
    for i in range(5):
        conn.execute(_sql(f'INSERT INTO "{B["table"]}" VALUES (:d, :a, :c, :n, :s, :i, :k, :v)'),
                     {"d": (LAST_START + timedelta(days=i)).isoformat(), "a": "b-acct", "c": "b-1",
                      "n": "x", "s": 20, "i": 100, "k": 3, "v": 0})
    # A row the day before the month, which the SUM must not reach.
    conn.execute(_sql(f'INSERT INTO "{B["table"]}" VALUES (:d, :a, :c, :n, :s, :i, :k, :v)'),
                 {"d": (LAST_START - timedelta(days=1)).isoformat(), "a": "b-acct", "c": "b-1",
                  "n": "x", "s": 5000, "i": 1, "k": 0, "v": 0})
row = reconcile.reconcile_platform("bing", LAST, TODAY)
check("a table that resolves but is unconfirmed is not measured: its columns are still a guess",
      (row["state"], "not confirmed" in row["reason"]), ("not_measured", True))
store.confirm_provider("bing", by="Todd", fingerprint=provider_map.fingerprint("bing"))
store.upsert_rows([fact("bing", "b-acct", "b-1", LAST_START + timedelta(days=i), 20) for i in range(5)], today=TODAY)
row = reconcile.reconcile_platform("bing", LAST, TODAY)
check("confirmed, the table is summed whole for the month and agrees",
      (row["state"], row["ours"], row["theirs"]), ("agree", Decimal("100.00"), Decimal("100.00")))
check("...labeled a re-read, never independent", (row["independent"], row["source_label"]),
      (False, f"the provider's {B['table']} summed whole"))
store.upsert_rows([fact("bing", "b-acct", "b-9", LAST_START + timedelta(days=6), 30)], today=TODAY)
row = reconcile.reconcile_platform("bing", LAST, TODAY)
check("a campaign in our table the provider never had is drift, against the provider's figure",
      (row["state"], row["drift_pct"]), ("drift", Decimal("30.00")))
store.upsert_rows([fact("bing", "b-acct", "b-9", LAST_START + timedelta(days=6), 0)], today=TODAY)


# --------------------------------------------------------------- others
section("StackAdapt re-reads; the Trade Desk and Suite say why they cannot")

row = reconcile.reconcile_platform("stackadapt", LAST, TODAY)
check("StackAdapt unconfigured is not measured, naming the variable",
      (row["state"], "STACKADAPT_API_KEY" in row["reason"]), ("not_measured", True))
os.environ["STACKADAPT_API_KEY"] = "test-key"
fetched = []


def fake_fetch(start, end, sleep=None):
    fetched.append((start, end))
    return {"rows": [{"platform": "stackadapt", "account_id": "sa", "campaign_id": "s-1",
                      "campaign_name": "x", "date": LAST_START + timedelta(days=i), "spend": 12.5,
                      "impressions": 400, "clicks": 4, "conversions": 0, "source": "native"}
                     for i in range(4)],
            "skipped": 0, "pages": 1, "progress_waits": 0}


stackadapt.fetch = fake_fetch
store.upsert_rows([fact("stackadapt", "sa", "s-1", LAST_START + timedelta(days=i), "12.50") for i in range(4)],
                  today=TODAY)
row = reconcile.reconcile_platform("stackadapt", LAST, TODAY)
check("StackAdapt's month is fetched again and summed",
      (row["state"], row["theirs"], fetched[-1]), ("agree", Decimal("50.00"), (LAST_START, LAST_END)))
check("...as a re-read, saying how many campaign-days", (row["independent"], row["source_label"]),
      (False, "StackAdapt's month fetched again (4 campaign-days)"))
os.environ.pop("STACKADAPT_API_KEY", None)
for p in ("ttd", "suite"):
    row = reconcile.reconcile_platform(p, LAST, TODAY)
    check(f"{p} is not measurable by design, with the reason on the row",
          (row["state"], row["reason"]), ("not_measured", reconcile.NOT_MEASURABLE[p]))
row = reconcile.reconcile_platform("linkedin", LAST, TODAY)
check("a platform with no table and no other source names what it lacks",
      (row["state"], "does not resolve" in row["reason"]), ("not_measured", True))


def boom(start, end):
    raise RuntimeError("Google fell over")


_gt = reconcile.google_total
reconcile.google_total = boom
row = reconcile.reconcile_platform("google", LAST, TODAY)
reconcile.google_total = _gt
check("a reader that raises is not measured with the exception named, and the run goes on",
      (row["state"], "RuntimeError: Google fell over" in row["reason"]), ("not_measured", True))


# ------------------------------------------------------------- held rows
section("Rows held in quarantine ride on the row")

store.upsert_rows([fact("google", "222", "g-1", LAST_START + timedelta(days=12), 5, impressions=10, clicks=99)],
                  today=TODAY)
row = reconcile.reconcile_platform("google", LAST, TODAY)
check("a google row held for the month is counted beside the comparison", row["held"], 1)
check("...and not in ours", row["ours"], Decimal("100.00"))


# ----------------------------------------------------------- the ledger
section("The ledger, and a state change logged rather than a state")

res = reconcile.run(today=TODAY, actor="test")
check("the run covers this month and the last", res["months"], [f"{TODAY:%Y-%m}", LAST])
check("...every platform, twice", len(res["rows"]), 2 * len(store.PLATFORMS))
check("...with the three counts adding up", res["agree"] + res["drift"] + res["not_measured"], len(res["rows"]))
led = {(r["platform"], r["month"]): r for r in store.reconcile_rows(months=3)}
check("one ledger row per platform-month", len(led), 2 * len(store.PLATFORMS))
check("...carrying the comparison", (led[("google", LAST)]["state"], led[("google", LAST)]["theirs"]),
      ("agree", Decimal("100.00")))
check("the first run logs nothing: there was no prior state to change from", entries("reports_reconcile"), [])
THEIRS["micros"] = 150_000_000
reconcile.run(today=TODAY, actor="test")
e = entries("reports_reconcile")
check("google moving to drift is logged once, with both figures",
      len(e) == 1 and e[0].get("platform") == "google" and "Agrees -> Drift" in e[0].get("detail", "")
      and "$100.00" in e[0]["detail"] and "$150.00" in e[0]["detail"])
reconcile.run(today=TODAY, actor="test")
check("...and the same state on the next run is not logged again", len(entries("reports_reconcile")), 1)
check("drifting() names it", [(d["platform"], d["month"]) for d in reconcile.drifting()], [("google", LAST)])


# ------------------------------------------------------------- the screens
section("Where it is read")

st, msg = health.status_row()
check("the /status row warns, naming the platform, the month and the gap",
      (st, f"Google Ads {LAST} (33.33%)" in msg and "/reports/reconcile" in msg), ("warn", True))
sb = health.scoreboard()
check("the scoreboard counts it, with somewhere to open", (sb["counts"]["drift"], sb["urls"]["drift"]),
      (1, "/reports/reconcile"))
check("...names it in the line", "1 platform-month not adding up" in sb["line"])
check("...and lists it under the feeds with both figures",
      [(p["state"], p["label"]) for p in sb["platforms"] if p["state"] == "drift"], [("drift", f"Google Ads {LAST}")])
ix = staff.get("/reports/", headers=H).get_data(as_text=True)
check("the index names the drift", "1 in drift" in ix and f"Google Ads {LAST}" in ix)
page = staff.get("/reports/reconcile", headers=H).get_data(as_text=True)
check("the page draws every platform for both months, with the three states and the two kinds of figure",
      page.count("<td>") >= 2 * len(store.PLATFORMS) and "Drift" in page and "Agrees" in page
      and "Not measured" in page and ">independent<" in page and ">re-read<" in page)
check("...and names the tolerance as ours", "within 2.0% (house figure" in page)
check("...and what cannot be measured, by design", "MyReports file is the pull" in page)
check("a stranger cannot read it", anon.get("/reports/reconcile", headers=H).status_code in (302, 401))
check("...or run it", anon.post("/reports/reconcile/run", headers=H).status_code in (302, 401))
r = staff.post("/reports/reconcile/run", headers=H)
check("staff can measure now, and are told", (r.status_code, r.headers.get("Location", "")),
      (302, "/reports/reconcile?saved=run"))
check("...which writes an activity row", bool(entries("reports_reconcile_run")))
THEIRS["micros"] = 100_000_000
reconcile.run(today=TODAY, actor="test")
st, msg = health.status_row()
check("back in agreement, the status row stops naming it", "adding up" not in msg)

from hub import help as hub_help                                     # noqa: E402
from hub import scheduler                                            # noqa: E402
check("the page's heading has a bubble behind it", hub_help.get("reports.reconcile.states") is not None)
check("the nightly job is registered", scheduler.JOBS.get("reports_reconcile", (None,))[0], 1440)
check("...as the thin function", scheduler.JOBS["reports_reconcile"][1].__name__, "job_reports_reconcile")
from flask import Flask                                              # noqa: E402
out = scheduler.JOBS["reports_reconcile"][1](Flask("t"))
check("...which answers with the counts and what changed", sorted(out), ["agree", "changed", "drift", "months", "not_measured"])

with store.engine.begin() as conn:
    conn.execute(_sql(f'DROP TABLE IF EXISTS "{B["table"]}"'))


# ------------------------------------------------------------------ wired in
section("Wired in")

wf = (ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
check("checks.yml runs this file", "python3 test_reports_reconcile.py" in wf)
loop = wf[wf.index("The reports tests against Postgres"):].split("done", 1)[0]
check("...and the Postgres loop runs it too", "test_reports_reconcile.py" in loop)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
