"""Fact rows that cannot be true are held, not filed and not refused whole.

    python3 test_reports_quarantine.py

No pytest, no new dependencies, a temporary data directory and the reports
database _reports_testdb.py binds (a SQLite file here; the job's Postgres
under checks.yml's second run).

Every writer lands rows through store.upsert_rows(), so the screen stands
in that one door. What this holds:

  * the four rules -- more clicks than impressions, a negative figure, a
    day after today, spend over fifty times the campaign's own trailing
    average -- each named with the figures behind it, and the rest of the
    batch written;
  * a zero is not a rule, and a spike needs a baseline: seven days of
    history averaging a dollar, so a test campaign going live is not one;
  * an hourly re-sync of the same held row is one entry proposed twice,
    never two entries;
  * Accept writes THAT row and the same figure passes through next time;
    Discard drops it and the same figure is dropped in silence and counted;
    a different figure under the same key is a new proposal either way;
    a clean restatement supersedes a held row by itself;
  * the page, the index tile, the dashboard scoreboard, the /status row
    and the client's staff page all read the same ledger, the decide route
    refuses a stranger, and the store is the only writer of the fact table.
"""
import ast
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

TMP = tempfile.mkdtemp(prefix="s1reports_quarantine_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-quarantine-test"
os.environ["PUBLIC_BASE_URL"] = "https://hub.example.test"

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


from werkzeug.test import Client                                     # noqa: E402

import wsgi                                                          # noqa: E402
from hub import auth                                                 # noqa: E402
from modules.reports import health, quarantine, store                # noqa: E402
_reports_testdb.reset(store)

TODAY = date.today()
H = {"Host": "localhost"}
ACME, ACME_NAME = "n:acme-co", "Acme Co"
staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")
anon = Client(wsgi.application)


def fact(campaign_id, day, spend=10, impressions=1000, clicks=5, conversions=0,
         platform="ttd", account="adv-1"):
    return {"platform": platform, "account_id": account, "campaign_id": campaign_id,
            "campaign_name": "S1M | Acme Co | Streaming TV | Q3", "date": day, "spend": spend,
            "impressions": impressions, "clicks": clicks, "conversions": conversions,
            "source": "native"}


def held_for(cid):
    return [h for h in quarantine.held() if h["campaign_id"] == cid]


def decided_for(cid):
    return [h for h in quarantine.decided() if h["campaign_id"] == cid]


# --------------------------------------------------------------- the rules
section("Four rules, each naming the figures, and the rest of the batch written")

rep = {}
n = store.upsert_rows([fact("c-1", TODAY - timedelta(days=i)) for i in range(1, 16)], report=rep)
check("fifteen ordinary days are written and nothing is held", (n, rep["quarantined"]), (15, 0))
check("...and the report says so", (rep["written"], rep["reasons"]), (15, {}))

rep = {}
n = store.upsert_rows([
    fact("c-1", TODAY, spend=10),                              # clean
    fact("c-1", TODAY + timedelta(days=1)),                    # tomorrow
    fact("c-2", TODAY, clicks=2000),                           # clicks > impressions
    fact("c-3", TODAY, spend=-4),                              # negative
    fact("c-1", TODAY - timedelta(days=1), spend=800),         # 80x a $10/day baseline
], report=rep)
check("one clean row written, four held", (n, rep["written"], rep["quarantined"]), (1, 1, 4))
check("...each under its rule",
      rep["reasons"], {"future": 1, "clicks_over_impressions": 1, "negative": 1, "spend_spike": 1})
held = {(h["campaign_id"], h["date"]): h for h in quarantine.held()}
check("the future row names the day and today",
      held[("c-1", (TODAY + timedelta(days=1)).isoformat())]["reason"],
      f"dated {(TODAY + timedelta(days=1)).isoformat()}, after today ({TODAY.isoformat()})")
check("the clicks row names both figures",
      held[("c-2", TODAY.isoformat())]["reason"], "2,000 clicks against 1,000 impressions")
check("the negative row names the figure",
      held[("c-3", TODAY.isoformat())]["reason"], "negative spend (-4.00)")
check("the spike names the spend, the average and the days behind it",
      held[("c-1", (TODAY - timedelta(days=1)).isoformat())]["reason"],
      "spend $800.00 against a trailing average of $10.00/day over 14 days")
check("the held row carries the figures as they would have been written",
      (held[("c-2", TODAY.isoformat())]["spend"], held[("c-2", TODAY.isoformat())]["clicks"]), ("10.00", 2000))
check("the fact table holds the clean row and none of the four",
      (store.fact_count(), [f["date"] for f in store.facts_for(ACME, TODAY - timedelta(days=1), TODAY + timedelta(days=1))]),
      (16, []))
check("the rule table says whose numbers the spike floors are", quarantine.RULES_SOURCE, "house")
check("...and what they are",
      (quarantine.SPIKE_MULTIPLIER, quarantine.BASELINE_DAYS, quarantine.BASELINE_MIN_DAYS, quarantine.BASELINE_MIN_SPEND),
      (50, 14, 7, Decimal("1.00")))

# What is deliberately not a rule.
rep = {}
store.upsert_rows([fact("c-1", TODAY - timedelta(days=16), spend=0, impressions=0, clicks=0)], report=rep)
check("a zero row is the truth about a paused campaign and is written", (rep["written"], rep["quarantined"]), (1, 0))
rep = {}
store.upsert_rows([fact("c-new", TODAY - timedelta(days=3), spend=2),
                   fact("c-new", TODAY - timedelta(days=2), spend=2)], report=rep)
store.upsert_rows([fact("c-new", TODAY - timedelta(days=1), spend=900)], report=rep)
check("a campaign with two days of history cannot spike: no baseline, so written", (rep["written"], rep["quarantined"]), (1, 0))
store.upsert_rows([fact("c-pennies", TODAY - timedelta(days=i), spend="0.20") for i in range(2, 12)])
rep = {}
store.upsert_rows([fact("c-pennies", TODAY - timedelta(days=1), spend=40)], report=rep)
check("a campaign averaging pennies going live is not a spike either (the $1 floor)",
      (rep["written"], rep["quarantined"]), (1, 0))
# Two campaigns with the same fourteen days of $10 behind them: one spends
# exactly fifty times that on the next day and one spends a cent more.
for cid in ("c-steady", "c-steady2"):
    store.upsert_rows([fact(cid, TODAY - timedelta(days=i), spend=10) for i in range(2, 16)])
rep = {}
store.upsert_rows([fact("c-steady", TODAY - timedelta(days=1), spend=500)], report=rep)
check("exactly fifty times the average is not over it", (rep["written"], rep["quarantined"]), (1, 0))
rep = {}
store.upsert_rows([fact("c-steady2", TODAY - timedelta(days=1), spend="500.01")], report=rep)
check("...a cent past it is", rep["quarantined"], 1)
check("the baseline window sits before the batch's own earliest day, so an hourly "
      "re-read of the same restate window measures against the same history",
      quarantine.rules_for(store._fact_values(fact("c-steady", TODAY, spend="500.01")),
                           quarantine._baselines([store._fact_values(fact("c-steady", TODAY - timedelta(days=1)))]),
                           TODAY)[0][1].endswith("over 14 days"))


# ------------------------------------------------------------ re-syncs
section("The hourly re-sync proposes the same row again: one entry, counted")

rep = {}
store.upsert_rows([fact("c-2", TODAY, clicks=2000)], report=rep)
check("the same impossible figure is held again, not written", (rep["written"], rep["quarantined"]), (0, 1))
check("...on the one entry, proposed twice", [h["times"] for h in held_for("c-2")], [2])
check("...so the count is unchanged: the four above and the cent-past spike", quarantine.counts()["held"], 5)

# ------------------------------------------------------------- decisions
section("Accept, Discard, and a clean restatement")

check("a stranger cannot decide",
      anon.post("/reports/quarantine/decide", headers=H,
                data={"platform": "ttd", "account_id": "adv-1", "campaign_id": "c-2",
                      "date": TODAY.isoformat(), "action": "accept"}).status_code in (302, 401))
check("...and the row is still held", [h["status"] for h in held_for("c-2")], ["held"])
r = staff.post("/reports/quarantine/decide", headers=H,
               data={"platform": "ttd", "account_id": "adv-1", "campaign_id": "c-2",
                     "date": TODAY.isoformat(), "action": "accept"})
check("staff Accept redirects back, saying so",
      (r.status_code, r.headers.get("Location", "")), (302, "/reports/quarantine?saved=accepted"))
d = decided_for("c-2")
check("the entry says who accepted it", (d[0]["status"], d[0]["decided_by"]), ("accepted", "Todd"))
row = [f for f in store.facts_for(ACME, TODAY, TODAY) if f["campaign_id"] == "c-2"] or \
      [f for f in store.facts_for("nobody", TODAY, TODAY)]
db = store.SessionLocal()
try:
    written = db.get(store.AdPerfDaily, ("ttd", "adv-1", "c-2", TODAY))
finally:
    db.close()
check("...and THAT row is in the fact table, figures as proposed",
      (written is not None and written.clicks, written is not None and written.impressions), (2000, 1000))
e = entries("quarantine_accepted")
check("an activity row records it", bool(e) and (e[-1].get("actor"), e[-1].get("campaign_id")) == ("Todd", "c-2")
      and "more clicks than impressions" in e[-1].get("detail", ""))
rep = {}
store.upsert_rows([fact("c-2", TODAY, clicks=2000)], report=rep)
check("the same figure arriving again passes through -- somebody accepted it", (rep["written"], rep["quarantined"]), (1, 0))
rep = {}
store.upsert_rows([fact("c-2", TODAY, clicks=3000)], report=rep)
check("a DIFFERENT impossible figure for the same day is a new proposal", (rep["written"], rep["quarantined"]), (0, 1))
h = held_for("c-2")
check("...held afresh, saying an earlier figure was accepted, proposed once",
      (h[0]["status"], h[0]["note"], h[0]["times"]), ("held", "an earlier figure for this day was accepted", 1))
r = staff.post("/reports/quarantine/decide", headers=H,
               data={"platform": "ttd", "account_id": "adv-1", "campaign_id": "c-2",
                     "date": TODAY.isoformat(), "action": "discard"})
check("Discard redirects back, saying so", r.headers.get("Location", ""), "/reports/quarantine?saved=discarded")
db = store.SessionLocal()
try:
    written = db.get(store.AdPerfDaily, ("ttd", "adv-1", "c-2", TODAY))
finally:
    db.close()
check("...the accepted row still stands in the fact table; the discarded figure never reached it", written.clicks, 2000)
rep = {}
store.upsert_rows([fact("c-2", TODAY, clicks=3000)], report=rep)
check("the discarded figure arriving again is dropped in silence -- held count in the report, "
      "no new entry", (rep["written"], rep["quarantined"], len(held_for("c-2"))), (0, 1, 0))
check("...and counted on the decided entry", [h["times"] for h in decided_for("c-2") if h["status"] == "discarded"], [2])
e = entries("quarantine_discarded")
check("an activity row records the discard", bool(e) and e[-1].get("campaign_id") == "c-2")

rep = {}
store.upsert_rows([fact("c-3", TODAY, spend=4)], report=rep)
check("a clean figure for a held day is written", (rep["written"], rep["quarantined"]), (1, 0))
check("...and supersedes the held entry by itself, saying why",
      [(h["status"], h["note"]) for h in decided_for("c-3")],
      [("superseded", "the sync restated the day with a figure the rules accept")])

try:
    quarantine.decide("ttd", "adv-1", "c-3", TODAY, action="accept", by="Todd")
    check("deciding a row that is no longer held is refused by name", False)
except ValueError as exc:
    check("deciding a row that is no longer held is refused by name", "already superseded" in str(exc))
try:
    quarantine.decide("ttd", "adv-1", "c-1", TODAY + timedelta(days=1), action="keep", by="Todd")
    check("an unknown decision is refused", False)
except ValueError as exc:
    check("an unknown decision is refused", "accept or discard" in str(exc))
try:
    quarantine.decide("ttd", "adv-1", "c-1", TODAY + timedelta(days=1), action="accept", by="")
    check("a decision with no name is refused", False)
except ValueError as exc:
    check("a decision with no name is refused", "name" in str(exc))
r = staff.post("/reports/quarantine/decide", headers=H,
               data={"platform": "ttd", "account_id": "adv-1", "campaign_id": "nope",
                     "date": TODAY.isoformat(), "action": "accept"})
check("the route turns a bad ask into a sentence", "error=" in r.headers.get("Location", ""))


# ------------------------------------------------------------- the screens
section("Where it is read")

store.map_campaign("ttd", "adv-1", "c-1", client=ACME, client_name=ACME_NAME,
                   product="Streaming TV", mapped_by="Todd")
q = staff.get("/reports/quarantine", headers=H).get_data(as_text=True)
check("the page lists the held rows with rule, reason and both decisions",
      "dated after today" in q and "$800.00 against a trailing average" in q
      and 'value="accept"' in q and 'value="discard"' in q)
check("...says whose numbers the spike floors are", "house figures" in q)
check("...and shows the decisions made", "superseded" in q and "accepted" in q and "discarded" in q)
ix = staff.get("/reports/", headers=H).get_data(as_text=True)
check("the index tile counts what is held", "Held in quarantine" in ix)
check("...and the platform row says how many", ">3 held<" in ix.replace("\n", ""))
sb = health.scoreboard()
check("the dashboard scoreboard carries the count, with somewhere to open",
      (sb["counts"]["held"], sb["urls"]["held"]), (3, "/reports/quarantine"))
check("...in words", "3 rows held in quarantine" in sb["line"])
st, msg = health.status_row()
check("the /status row warns, naming the page", (st, "3 rows held in quarantine (/reports/quarantine)" in msg), ("warn", True))
cp = staff.get(f"/reports/client/{ACME}", headers=H).get_data(as_text=True)
check("the client's staff page names the days missing from their page",
      "held in quarantine" in cp and "spend more than 50x" in cp)
check("...for confirmed campaigns only",
      sorted(h["campaign_id"] for h in quarantine.held_for_client(ACME)), ["c-1", "c-1"])
dash = staff.get("/", headers=H).get_data(as_text=True)
check("the dashboard card draws the tile", "Held in quarantine" in dash)
from hub import help as hub_help                                     # noqa: E402
check("the page's heading has a bubble behind it", hub_help.get("reports.quarantine.rules") is not None)

for cid, day in (("c-1", TODAY + timedelta(days=1)), ("c-1", TODAY - timedelta(days=1)),
                 ("c-steady2", TODAY - timedelta(days=1))):
    quarantine.decide("ttd", "adv-1", cid, day, action="discard", by="Todd")
st, msg = health.status_row()
check("with nothing held the status row no longer warns about it", "quarantine" not in msg)
check("...and the count is a real nought, not None", quarantine.counts()["held"], 0)


# ----------------------------------------------------------- one door
section("One door: the store is the only writer of the fact table")

writers = []
for py in sorted((ROOT / "modules" / "reports").rglob("*.py")):
    tree = ast.parse(py.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in ("_write_values", "AdPerfDaily"):
                writers.append((py.name, name))
            if name == "upsert_rows" and any(k.arg == "screen" for k in node.keywords):
                writers.append((py.name, "upsert_rows(screen=)"))
check("_write_values and the AdPerfDaily constructor are reached from the store and the "
      "quarantine's Accept, and nothing passes screen= to upsert_rows",
      sorted(set(writers)), [("quarantine.py", "_write_values"), ("store.py", "AdPerfDaily"),
                             ("store.py", "_write_values")])
from modules.reports import normalize                                # noqa: E402
import inspect                                                       # noqa: E402
check("the provider normalize carries the count through to the run's answer",
      "quarantined" in inspect.getsource(normalize.run) and "report=held" in inspect.getsource(normalize.run))


# ------------------------------------------------------------------ wired in
section("Wired in")

wf = (ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
check("checks.yml runs this file", "python3 test_reports_quarantine.py" in wf)
loop = wf[wf.index("The reports tests against Postgres"):].split("done", 1)[0]
check("...and the Postgres loop runs it too", "test_reports_quarantine.py" in loop)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
