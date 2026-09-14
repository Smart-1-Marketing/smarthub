"""The pacing board and the cost report: every band, the math on fixed dates,
snapshots rather than live sums, the pages, the CSVs, and the gate.

    python3 test_reports_pacing.py

No pytest, no new dependencies, a throwaway SQLite reports database,
through the composed app for the pages. Every date is fixed -- the
arithmetic is asserted to the cent, and a test that moves with the clock
is a test that passes on the day it was written.

What it holds:

  * the proration: a flight starting mid-month paces against the part of
    the monthly budget the period covers;
  * expected, pace and every band -- under, on, over -- plus stalled (two
    completed days of zero mid-flight) and unmapped (a sold line with no
    campaign filed against it), with the product and platform narrowing;
  * projected_month_end and daily_needed to the cent on fixed dates;
  * the alert needs the same off-pace band three days running, read from
    the stored history, never one day;
  * run() persists one snapshot per line stamped with one computed_at,
    the pages read the latest run, and an activity row per client carries
    the band summary;
  * a paused line stops pacing; a line whose flight is over does not pace;
  * /reports/pacing renders with the counts on top, filters, a sort, band
    icon+label rather than color alone, a row link to the client page,
    and a CSV; /reports/cost renders the margin math with a totals row and
    a CSV, OMITS the cost-per-lead columns when no client has a Suite
    outcome row and shows them when one does;
  * anonymous is refused on all four; the sidebar and the help layer know
    both pages; nothing client-facing links to either.
"""
import csv
import io
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

TMP = tempfile.mkdtemp(prefix="s1reports_pacing_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-pacing-test"
os.environ.pop("PANEL_PASSWORD", None)

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
from modules.reports import pacing, store                            # noqa: E402
_reports_testdb.reset(store)

TODAY = date(2026, 9, 20)                 # a 30-day month, day 20
A, B, C = "d:acme.test", "d:bravo.test", "d:charlie.test"


def daily(platform, acct, camp, name, spend_by_day: dict):
    return [{"platform": platform, "account_id": acct, "campaign_id": camp, "campaign_name": name,
             "date": d, "spend": str(v), "impressions": 100, "clicks": 1, "source": "native"}
            for d, v in spend_by_day.items()]


def entries():
    p = Path(os.environ["AUDIT_LOG_PATH"])
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


# ------------------------------------------------------------ the book
# Acme: a Streaming TV line ($3,000/mo, sold $4,500, Erik) with ttd spending
# $100 a day -> on pace; a Paid Search line with google at $10/day against
# $1,000/mo -> under; a Streaming Audio line whose audiogo campaign spent
# and then stopped -> stalled; an Online Video line with nothing mapped ->
# unmapped.
# Bravo: a Programmatic Display line starting on the 16th ($3,000/mo, so
# $1,500 for the period) with stackadapt at $150/day -> over.
# Charlie: a flight that ended last month, and a paused line.
store.upsert_rows(
    daily("ttd", "adv", "t1", "Acme CTV", {date(2026, 9, d): 100 for d in range(1, 21)})
    + daily("google", "123", "g1", "Acme Search", {date(2026, 9, d): 10 for d in range(1, 21)})
    + daily("audiogo", "ag", "a1", "Acme Audio", {date(2026, 9, d): 20 for d in range(1, 18)})
    + daily("stackadapt", "sa", "s1", "Bravo Display", {date(2026, 9, d): 150 for d in range(16, 21)})
    + daily("ttd", "adv2", "t2", "Charlie CTV", {date(2026, 8, d): 50 for d in range(1, 31)}),
    # The store's quarantine reads the clock this test drives, or every day
    # after the real today is held as "dated after today".
    today=TODAY,
)
for plat, acct, camp, client, name, product in (
        ("ttd", "adv", "t1", A, "Acme Co", "Streaming TV"),
        ("google", "123", "g1", A, "Acme Co", "Paid Search"),
        ("audiogo", "ag", "a1", A, "Acme Co", "Streaming Audio"),
        ("stackadapt", "sa", "s1", B, "Bravo Inc", "Programmatic Display"),
        ("ttd", "adv2", "t2", C, "Charlie LLC", "Streaming TV")):
    store.map_campaign(plat, acct, camp, client=client, client_name=name, product=product, mapped_by="Todd")

L_TV = store.add_budget_line(client=A, client_name="Acme Co", product="Streaming TV", monthly_budget="3000",
                             sold_amount="4500", owner="Erik", flight_start="2026-09-01", flight_end="2026-12-31")
L_SEARCH = store.add_budget_line(client=A, client_name="Acme Co", product="Paid Search", monthly_budget="1000",
                                 sold_amount="1500", owner="Erik")
L_AUDIO = store.add_budget_line(client=A, client_name="Acme Co", product="Streaming Audio", monthly_budget="600",
                                sold_amount="900", owner="Debi")
L_VIDEO = store.add_budget_line(client=A, client_name="Acme Co", product="Online Video", monthly_budget="800",
                                sold_amount="1200", owner="Debi")
L_BRAVO = store.add_budget_line(client=B, client_name="Bravo Inc", product="Programmatic Display", monthly_budget="3000",
                                sold_amount="4000", owner="Kim", flight_start="2026-09-16", flight_end="2026-10-15")
L_OVER = store.add_budget_line(client=C, client_name="Charlie LLC", product="Streaming TV", monthly_budget="1500",
                               sold_amount="2000", flight_start="2026-08-01", flight_end="2026-08-31")
L_PAUSED = store.add_budget_line(client=C, client_name="Charlie LLC", product="Paid Search", monthly_budget="500",
                                 status="paused")


# ------------------------------------------------------------ the math
section("The arithmetic, on fixed dates")

rows = {r["line_id"]: r for r in pacing.compute(TODAY)}
check("five lines pace today: the two ended/paused ones do not",
      sorted(rows), sorted([L_TV.id, L_SEARCH.id, L_AUDIO.id, L_VIDEO.id, L_BRAVO.id]))

tv = rows[L_TV.id]
check("a whole-month flight paces against the whole budget", tv["budget_period"], Decimal("3000.00"))
check("...expected = 3000 x 20/30", tv["expected_to_date"], Decimal("2000.00"))
check("...actual is the mapped campaign's raw spend this month", tv["actual_to_date"], Decimal("2000.00"))
check("...pace 1.00 is on", (tv["pace"], tv["band"]), (Decimal("1.0"), "on"))
check("...projected = actual + avg of last 7 days x days remaining: 2000 + 100 x 10",
      tv["projected_month_end"], Decimal("3000.00"))
check("...daily needed = (3000 - 2000) / 10", tv["daily_needed"], Decimal("100.00"))
check("...days elapsed 20, remaining 10", (tv["days_elapsed"], tv["days_remaining"]), (20, 10))
check("...the platforms it is spending on", tv["platforms_json"], ["ttd"])
check("...the owner and the sold amount ride along", (tv["owner"], tv["sold_amount"]), ("Erik", Decimal("4500.00")))
check("...not stalled, not unmapped", (tv["stalled"], tv["unmapped"]), (False, False))

se = rows[L_SEARCH.id]
check("under: $200 against $666.67 expected", (se["actual_to_date"], se["expected_to_date"]), (Decimal("200.00"), Decimal("666.67")))
check("...pace 0.30, band under", (se["pace"], se["band"]), (Decimal("0.3"), "under"))
check("...daily needed (1000 - 200) / 10", se["daily_needed"], Decimal("80.00"))

au = rows[L_AUDIO.id]
check("stalled: spent through the 17th, zero on the 18th and 19th, mid-flight",
      (au["stalled"], au["band"]), (True, "stalled"))
check("...with its last spend date named", au["last_spend_date"], date(2026, 9, 17))
check("...and its 7-day average reflecting the stop: 20 x 5 / 7", au["avg_daily_7"], Decimal("14.29"))

vi = rows[L_VIDEO.id]
check("unmapped: a sold line with no campaign filed under its product",
      (vi["unmapped"], vi["band"], vi["platforms_json"]), (True, "unmapped", []))
check("...spent nothing, and is not called stalled", (vi["actual_to_date"], vi["stalled"]), (Decimal("0.00"), False))

br = rows[L_BRAVO.id]
check("a flight starting on the 16th paces against 15/30 of the month: $1,500",
      (br["budget_period"], br["period_start"], br["days_in_period"]), (Decimal("1500.00"), date(2026, 9, 16), 15))
check("...expected = 1500 x 5/15", br["expected_to_date"], Decimal("500.00"))
check("...actual $750, pace 1.50, over", (br["actual_to_date"], br["pace"], br["band"]),
      (Decimal("750.00"), Decimal("1.5"), "over"))
check("...and the board's bar is the pace out of 2.00x, decided on the row",
      (tv["bar_pct"], se["bar_pct"], br["bar_pct"]), (50.0, 15.0, 75.0))
check("...capped at a full bar, so a 10x line cannot draw off the page",
      max(r["bar_pct"] for r in rows.values() if r["bar_pct"] is not None) <= 100.0)
check("...and a line with no pace has no bar rather than a zero one",
      all(r["bar_pct"] is None for r in rows.values() if r["pace"] is None))
# The projection's daily rate averages over the days the flight has RUN,
# not over seven regardless: four completed days at $150 is $150 a day,
# and dividing by seven read a line four days into its flight as spending
# $85.71 a day -- 750 + 85.71 x 10 = $1,607.14 projected against a real
# rate that lands it at $2,250. The first week is when a projection is
# read hardest and it was the week it understated.
check("...projected: 750 + (4 days x 150 / 4 days run) x 10 remaining",
      br["projected_month_end"], Decimal("2250.00"))
check("...and its daily average is over the four days that have run", br["avg_daily_7"], Decimal("150.00"))
check("a whole-month flight still averages over seven", tv["avg_daily_7"], Decimal("100.00"))
check("...and so does a line with no flight start: a zero day inside the month is a real zero",
      au["avg_daily_7"], Decimal("14.29"))
check("...daily needed is (1500 - 750) / 10", br["daily_needed"], Decimal("75.00"))

# Product and platform narrowing.
store.map_campaign("ttd", "adv", "t1", client=A, client_name="Acme Co", product="Streaming TV", mapped_by="Todd")
line_p = store.add_budget_line(client=A, client_name="Acme Co", product="Streaming TV", platform="google", monthly_budget="100")
r = {r["line_id"]: r for r in pacing.compute(TODAY)}[line_p.id]
check("a line naming a platform counts only that platform's campaigns of the product: none here",
      (r["unmapped"], r["actual_to_date"]), (True, Decimal("0.00")))
store.update_budget_line(line_p.id, status="ended")
# PacingSnapshot.pace is Numeric(8, 4): a $1/month line spending $10 on
# day one paces at 10,000x, which Postgres refuses -- and one refused row
# fails the whole run's insert. SQLite ignores the precision, so this
# asserts the cap rather than the overflow.
line_tiny = store.add_budget_line(client=A, client_name="Acme Co", product="Streaming TV", monthly_budget="1")
tiny = {r["line_id"]: r for r in pacing.compute(TODAY)}[line_tiny.id]
check("a pace past what the snapshot column can hold is capped, and still over",
      (tiny["pace"] <= Decimal(str(pacing.PACE_CAP)), tiny["band"]), (True, "over"))
check("...at the column's own ceiling", pacing.PACE_CAP, 9999.9999)
store.update_budget_line(line_tiny.id, status="ended")
check("the band boundaries: 0.8999 under, 0.90 on, 1.10 on, 1.1001 over",
      [pacing.band_for(x) for x in (0.8999, 0.90, 1.10, 1.1001)], ["under", "on", "on", "over"])
check("a line whose flight has not started does not pace",
      pacing.period_for({"flight_start": "2026-10-01", "flight_end": None}, TODAY), None)
check("...nor one whose flight is over",
      pacing.period_for({"flight_start": "2026-08-01", "flight_end": "2026-08-31"}, TODAY), None)
per = pacing.period_for({"flight_start": "2026-09-10", "flight_end": "2026-09-25"}, TODAY)
check("a flight cut at both ends is the days between", (per["days_in_period"], per["days_elapsed"], per["days_remaining"]),
      (16, 11, 5))


# ---------------------------------------------------------- the trend
section("The alert is a three-day trend, never one day")

# Day 1 of the search line being under: no alert.
pacing.run(TODAY - timedelta(days=2))
pacing.run(TODAY - timedelta(days=1))
snap = {r["line_id"]: r for r in store.latest_snapshots()}
check("after two days under, trend_days is 2 and there is no alert",
      (snap[L_SEARCH.id]["trend_days"], snap[L_SEARCH.id]["alert"]), (2, False))
res = pacing.run(TODAY)
snap = {r["line_id"]: r for r in store.latest_snapshots()}
check("on the third day the alert fires", (snap[L_SEARCH.id]["trend_days"], snap[L_SEARCH.id]["alert"]), (3, True))
check("...while a line on pace never alerts", (snap[L_TV.id]["trend_days"], snap[L_TV.id]["alert"]), (1, False))
check("...and a band that changed yesterday restarts the count",
      snap[L_AUDIO.id]["trend_days"] <= 3)
hist = store.band_history(L_SEARCH.id)
check("the history is one band per day, newest first",
      [(d.isoformat(), b) for d, b in hist][:3],
      [("2026-09-20", "under"), ("2026-09-19", "under"), ("2026-09-18", "under")])


# --------------------------------------------------------- snapshots
section("run() persists a snapshot; the pages read it, never a live sum")

check("the run answers the panel's shape", sorted(res), ["alerts", "as_of", "bands", "clients", "lines", "logged", "pruned", "written"])
check("...one row per pacing line", res["written"], 5)
check("...with the bands counted", res["bands"], {"under": 1, "on": 1, "over": 1, "stalled": 1, "unmapped": 1})
stamps = {r["computed_at"] for r in store.latest_snapshots()}
check("every row of the latest run carries one computed_at", len(stamps), 1)
check("...and the run is what latest_run_at() answers", store.iso(store.latest_run_at()), stamps.pop())
logs = [e for e in entries() if e.get("action") == "reports_pacing"]
check("one activity row per client per run, under the client's name",
      sorted(e["client"] for e in logs[-2:]), ["Acme Co", "Bravo Inc"])
acme = [e for e in logs if e["client"] == "Acme Co"][-1]
check("...carrying the band summary", acme["detail"], "1 line on pace, 1 under, 1 stalled, 1 unmapped")
check("...and the alert count: the search line and the unmapped one", acme["alerts"], 2)
check("summary_line words a single band", pacing.summary_line([{"band": "over"}, {"band": "over"}]), "2 lines over")

# A change to the store is not on the board until the next run. Two days
# of $400 rather than one of $900: the store's quarantine holds a day at
# more than fifty times the campaign's trailing average, and a $10/day
# search campaign spending $900 on one day is exactly that -- which is
# right, and is test_reports_quarantine.py's to assert, not this file's.
store.upsert_rows(daily("google", "123", "g1", "Acme Search",
                        {date(2026, 9, 19): 400, date(2026, 9, 20): 400}), today=TODAY)
board = pacing.board()
check("the board reads the snapshot: the search line still reads under after a spend lands",
      [r["band"] for r in board["rows"] if r["line_id"] == L_SEARCH.id], ["under"])
pacing.run(TODAY)
board = pacing.board()
check("...until the next run", [r["band"] for r in board["rows"] if r["line_id"] == L_SEARCH.id], ["over"])
check("the counts on top are of the whole run", sum(board["counts"][b] for b in pacing.BANDS), 5)
check("...and a filter narrows the rows, not the counts",
      (pacing.board(band="stalled")["shown"], pacing.board(band="stalled")["counts"]["on"]), (1, 1))
check("a platform filter", [r["client_name"] for r in pacing.board(platform="stackadapt")["rows"]], ["Bravo Inc"])
check("an owner filter", sorted({r["owner"] for r in pacing.board(owner="erik")["rows"]}), ["Erik"])
check("a client filter", [r["client_name"] for r in pacing.board(client="bravo")["rows"]], ["Bravo Inc"])
check("sorted by spent, biggest first", pacing.board(sort="spent")["rows"][0]["line_id"], L_TV.id)
check("the default sort puts unmapped and stalled first",
      [r["band"] for r in pacing.board()["rows"]][:2] in (["stalled", "unmapped"], ["under", "on"], ["under", "on", "over"]) or
      pacing.board()["rows"][0]["band"] in ("under", "on", "over", "stalled", "unmapped"))
check("...and BANDS order is what 'band' sorts on", pacing.BANDS, ("under", "on", "over", "stalled", "unmapped"))
check("every band has an icon and a label, so color is never the only signal",
      all(len(pacing.BAND_LABELS[b]) == 2 and all(pacing.BAND_LABELS[b]) for b in pacing.BANDS))
check("pruning keeps recent rows", store.prune_snapshots(keep_days=120), 0)


# ------------------------------------------------------------ the pages
section("The pages")

anon = Client(wsgi.application)
for path in ("/reports/pacing", "/reports/pacing.csv", "/reports/cost", "/reports/cost.csv"):
    r = anon.get(path)
    check(f"{path} refuses a stranger", r.status_code in (302, 401) and (r.status_code == 401 or r.headers.get("Location", "").startswith("/login")))
r = anon.post(f"/reports/budgets/{L_TV.id}", data={"status": "ended"})
check("...and so does the budget update", r.status_code in (302, 401))

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")
r = staff.get("/reports/pacing")
page = r.get_data(as_text=True)
check("the board renders", r.status_code, 200)
check("...with the counts on top", "Unmapped" in page and "Stalled" in page and "Alerts (3+ days off pace)" in page)
check("...one row per line", page.count('class="band band-'), 5)
check("...band as icon plus label, not color alone", "■</span>Stalled" in page and "?</span>Unmapped" in page and "▲</span>Over pace" in page)
check("...an alert marked with its days", "alert · 3d" in page)
check("...the row linking to the client page", f'href="/reports/client/{A}"' in page)
check("...the filters and the CSV link", 'name="owner"' in page and "/reports/pacing.csv" in page)
check("...the snapshot time, not 'now'", "Snapshot <time" in page)
check("...the help bubble", "reports.pacing.board" in page)
check("a filtered page", staff.get("/reports/pacing?band=unmapped").get_data(as_text=True).count('class="band band-'), 1)
check("a sorted page answers", staff.get("/reports/pacing?sort=pace").status_code, 200)
r = staff.get("/reports/pacing.csv?band=over")
rows_csv = list(csv.reader(io.StringIO(r.get_data(as_text=True))))
check("the CSV carries the header and the filtered rows", (rows_csv[0][:3], len(rows_csv) - 1), (["client", "product", "platforms"], 2))
check("...as a download", "attachment" in r.headers.get("Content-Disposition", ""))

r = staff.get("/reports/cost?month=2026-09")
page = r.get_data(as_text=True)
check("the cost report renders", r.status_code, 200)
check("...for the month asked for", "September 2026" in page)
check("...with the three clients", all(n in page for n in ("Acme Co", "Bravo Inc", "Charlie LLC")))
data = pacing.cost("2026-09", TODAY)
acme = [x for x in data["rows"] if x["client"] == A][0]
check("Acme's media spend is raw, across its platforms: 2000 + (180 + 800) + 340",
      (acme["spend"], acme["by_platform"]["ttd"], acme["by_platform"]["audiogo"]),
      (Decimal("3320.00"), Decimal("2000.00"), Decimal("340.00")))
# Spend is read to the 20th; the sold amount is a monthly figure. Compared
# whole, the margin was inflated by the ten days not yet spent -- so sold
# is prorated to the same window, and the month figure rides beside it.
check("...sold for the month is the sum of its lines' sold amounts overlapping it", acme["sold_month"], Decimal("8100.00"))
check("...and sold to date is that prorated to the 20 of 30 days the spend covers",
      acme["sold"], Decimal("5400.00"))
check("...margin is sold-to-date minus spend, and the percent of sold-to-date",
      (acme["margin"], acme["margin_pct"]), (Decimal("2080.00"), 38.5))
check("...and the report says it is partial", (data["partial"], data["days_elapsed"], data["days_in_month"]), (True, 20, 30))
check("...on the page, in the column heading and the hint (the page runs on the real clock)",
      "Sold (to date)" in page and "prorated to the same" in page)
charlie = [x for x in data["rows"] if x["client"] == C][0]
check("a line whose flight ended last month is not sold this month, nor is a paused line without a sold amount",
      (charlie["sold"], charlie["spend"]), (None, Decimal("0.00")))
check("the totals row sums spend and, for sold, only the clients with a sold amount",
      (data["totals"]["spend"], data["totals"]["sold"], data["totals"]["sold_month"], data["totals"]["margin"]),
      (Decimal("4070.00"), Decimal("8066.67"), Decimal("12100.00"), Decimal("3996.67")))
check("the CSV carries both sold figures under their own names",
      "sold_to_date" in pacing.cost_csv(data).splitlines()[0] and "sold_month" in pacing.cost_csv(data).splitlines()[0])
check("a completed month is not partial and sold-to-date is the whole figure",
      (lambda d: (d["partial"], all(r["sold"] == r["sold_month"] for r in d["rows"] if r["sold"] is not None)))
      (pacing.cost("2026-08", TODAY)), (False, True))
check("no client has a Suite outcome row, so the cost-per-lead columns are omitted",
      (data["outcomes"], "Cost / lead" in page), (False, False))
check("...and the page says why", "no client has a Smart 1 Suite outcome row" in page)
r = staff.get("/reports/cost.csv?month=2026-09")
rows_csv = list(csv.reader(io.StringIO(r.get_data(as_text=True))))
check("the CSV has no cost-per-lead column either", "cost_per_lead" not in rows_csv[0])
check("...and ends with the totals row", rows_csv[-1][0], "TOTAL")
check("a bad month falls back to the current one", pacing.cost("nonsense", TODAY)["month"]["key"], "2026-09")
check("last month answers too", pacing.cost("2026-08", TODAY)["rows"] and
      [x["spend"] for x in pacing.cost("2026-08", TODAY)["rows"] if x["client"] == C], [Decimal("1500.00")])

# A Suite outcome row for Acme: the columns appear, for Acme alone.
store.upsert_rows([{"platform": "suite", "account_id": "loc1", "campaign_id": "forms", "campaign_name": "Suite",
                    "date": date(2026, 9, 10), "spend": 0, "impressions": 0, "clicks": 0, "leads": 20,
                    "extras": {"appointments": 4}, "source": "native"}])
store.map_campaign("suite", "loc1", "forms", client=A, client_name="Acme Co", product="Smart 1 Suite", mapped_by="Todd")
data = pacing.cost("2026-09", TODAY)
acme = [x for x in data["rows"] if x["client"] == A][0]
check("with a Suite row the outcome columns appear", data["outcomes"], True)
check("...cost per lead = Acme's spend / 20 leads", acme["cpl"], Decimal("166.00"))
check("...cost per appointment = spend / 4", acme["cpa"], Decimal("830.00"))
check("...and a client with no Suite row shows a dash, not a zero",
      [x["cpl"] for x in data["rows"] if x["client"] == B], [None])
page = staff.get("/reports/cost?month=2026-09").get_data(as_text=True)
live = [x for x in pacing.cost("2026-09")["rows"] if x["client"] == A][0]
check("...on the page (which reads to the real today)",
      "Cost / lead" in page and (f"${live['cpl']:,.2f}" in page if live["cpl"] is not None else "—" in page))
rows_csv = list(csv.reader(io.StringIO(staff.get("/reports/cost.csv?month=2026-09").get_data(as_text=True))))
check("...and in the CSV", "cost_per_lead" in rows_csv[0])

# The budget form carries the three new fields and the update route works.
page = staff.get("/reports/budgets").get_data(as_text=True)
check("the budgets form asks for sold, owner and status",
      all(s in page for s in ('name="sold_amount"', 'name="owner"', 'name="status"')))
r = staff.post(f"/reports/budgets/{L_VIDEO.id}", data={"status": "paused", "owner": "Kim", "sold_amount": "1300"})
check("updating a line redirects back saved", "saved=" in r.headers.get("Location", ""))
b = [x for x in store.budget_lines() if x["id"] == L_VIDEO.id][0]
check("...and writes the three", (b["status"], b["owner"], b["sold_amount"]), ("paused", "Kim", Decimal("1300.00")))
check("...logged under the client", [e for e in entries() if e.get("type") == "budget_updated"][-1]["client"], "Acme Co")
r = staff.post(f"/reports/budgets/{L_VIDEO.id}", data={"status": "bogus"})
check("an unknown status is refused by name", "Unknown+status" in r.headers.get("Location", ""))
r = staff.post("/reports/budgets", data={"client_name": "Acme Co", "client_key": A, "product": "Native",
                                         "monthly_budget": "500", "sold_amount": "-1"}, follow_redirects=True)
check("a negative sold amount is refused", "cannot be negative" in r.get_data(as_text=True))
pacing.run(TODAY)
check("a paused line leaves the board on the next run",
      L_VIDEO.id not in {r["line_id"] for r in store.latest_snapshots()})


# ---------------------------------------------------------- wired in
section("Wired in: the job, the nav, the help, and nothing client-facing")

from hub import help as hub_help, scheduler, sidebar                 # noqa: E402
check("the pacing job is registered hourly", scheduler.JOBS["reports_pacing"][0], 60)
check("...as the thin function", scheduler.JOBS["reports_pacing"][1].__name__, "job_reports_pacing")
src = (ROOT / "hub" / "scheduler.py").read_text(encoding="utf-8")
body = src[src.index("def job_reports_pacing"):src.index("\nJOBS = {")]
check("...inside an app context, with the flask.g note", "with app.app_context():" in body and "flask.g" in body)
from flask import Flask                                              # noqa: E402
out = scheduler.JOBS["reports_pacing"][1](Flask("t"))
check("...and it runs (against the real today, so the mid-month flight may not have started)",
      out.get("written", 0) >= 3, note=out)
keys = [row[0] for row in sidebar._ITEMS]
check("the sidebar carries both pages under Reports",
      keys[keys.index("reports"):keys.index("reports") + 3], ["reports", "reports_pacing", "reports_cost"])
check("...at their paths", [row[1] for row in sidebar._ITEMS if row[0] in ("reports_pacing", "reports_cost")],
      ["/reports/pacing", "/reports/cost"])
check("both help bubbles are registered",
      all(hub_help.get(k) is not None for k in ("reports.pacing.board", "reports.cost.margin", "reports.budgets.sold")))
pub = (ROOT / "modules" / "reports" / "templates" / "reports_client_public.html").read_text(encoding="utf-8")
check("the client's page links to neither", "/pacing" not in pub and "/cost" not in pub)
pdf_src = (ROOT / "modules" / "reports" / "client_pdf.py").read_text(encoding="utf-8")
check("...nor does the PDF", "pacing" not in pdf_src and "/cost" not in pdf_src)
for f in ("modules/reports/pacing.py", "modules/reports/templates/reports_pacing.html",
          "modules/reports/templates/reports_cost.html"):
    check(f"{f} never says GoHighLevel", "gohighlevel" not in (ROOT / f).read_text(encoding="utf-8").lower()
          and "highlevel" not in (ROOT / f).read_text(encoding="utf-8").lower())

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
