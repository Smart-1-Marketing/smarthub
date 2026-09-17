"""How the reports store is read: by client, by campaign key, and never by
sweeping a capped global list.

    python3 test_reports_map_reads.py

No pytest, no new dependencies, a throwaway SQLite reports database.

One defect class, held in one place: a capped global read filtered in
Python. It hit the campaign map and the budget book the same way, and the
guard against it is the same guard.

Every mapping read used to go through ``store.mapped_campaigns(limit=N)``,
which orders by ``mapped_at`` descending and truncates. Filtering that
result by client therefore drops that client's OLDEST mappings once the
book passes N -- silently, and only the OLDEST, so the newest client on the
screen looks right while the longest-standing one goes wrong. Worse, it goes
wrong asymmetrically: ``facts_for`` queries by client and keeps returning
the spend, so the money reads right and the campaign count reads zero. On
the pacing board that is a funded, spending line printed ``unmapped`` on a
page a client's rep reads.

What it holds:

  * the truncation is real and reproduced, not asserted from the source:
    past the cap ``mapped_campaigns`` does not carry the oldest mapping
    while ``facts_for`` still returns its spend;
  * ``campaign_maps_for`` returns a client's mappings complete, filtered in
    the database, for one key or many, however old;
  * ``campaign_map`` finds a mapping by its primary key past any cap, and
    answers None rather than raising for one that is not filed;
  * ``campaign_maps_by_key`` finds the mapped members of a touched set,
    dedupes, and ignores a malformed key rather than throwing the batch;
  * ``pending_mappings`` surfaces the OLDEST waiting item -- a queue whose
    oldest items fall off it is a queue nobody can finish;
  * the budget book has the same shape and a worse consequence: a line
    filtered out of a capped ``budget_lines()`` is not wrong on the pacing
    board, it is ABSENT from it, and a line nobody sees is a line nobody
    paces. ``budget_lines_for``, ``all_budget_lines`` and
    ``budget_line_count`` answer it, and a count is counted in SQL rather
    than ``len()``-ed over a capped read;
  * the guard: no client-scoped or by-key reader may reach
    ``mapped_campaigns`` or ``budget_lines`` at all. Both stay, bounded,
    for the screens that page; every reader that filters is asserted here
    to go through the uncapped readings instead.
"""
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_mapreads_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-map-reads-test"

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


from modules.reports import client_card, normalize, pacing, store     # noqa: E402
_reports_testdb.reset(store)

TODAY = date(2026, 9, 20)
OLD, NEW = "d:oldest.test", "d:newest.test"

# ------------------------------------------------------------ the book
# The oldest client is filed FIRST and never touched again -- exactly the
# client a cap drops. FILLER mappings are filed after it, each one newer.
FILLER = 12
CAP = 5                                   # stands in for the real 500/5000/10000

store.upsert_rows(
    [{"platform": "ttd", "account_id": "old", "campaign_id": "c-old",
      "campaign_name": "Oldest CTV", "date": d, "spend": "100", "impressions": 1000,
      "clicks": 10, "source": "native"}
     for d in (date(2026, 9, n) for n in range(1, 21))]
    + [{"platform": "ttd", "account_id": "new", "campaign_id": "c-new",
        "campaign_name": "Newest CTV", "date": d, "spend": "50", "impressions": 500,
        "clicks": 5, "source": "native"}
       for d in (date(2026, 9, n) for n in range(1, 21))],
    today=TODAY,
)
store.map_campaign("ttd", "old", "c-old", client=OLD, client_name="Oldest Co",
                   product="Streaming TV", mapped_by="Todd")
for i in range(FILLER):
    store.map_campaign("google", f"f{i}", f"g{i}", client=f"d:filler{i}.test",
                       client_name=f"Filler {i}", product="Paid Search", mapped_by="Todd")
store.map_campaign("ttd", "new", "c-new", client=NEW, client_name="Newest Co",
                   product="Streaming TV", mapped_by="Todd")

# map_campaign stamps mapped_at from the clock, and thirteen rows written in
# one breath can tie to the microsecond. The order is the whole point here,
# so it is written rather than hoped for: oldest first, one minute apart.
_db = store.SessionLocal()
try:
    _rows = _db.query(store.CampaignMap).order_by(store.CampaignMap.mapped_at.asc(),
                                                  store.CampaignMap.campaign_id.asc()).all()
    _base = datetime(2026, 9, 1, 9, 0, 0)
    _ordered = ([r for r in _rows if r.client == OLD]
                + [r for r in _rows if r.client not in (OLD, NEW)]
                + [r for r in _rows if r.client == NEW])
    for _i, _r in enumerate(_ordered):
        _r.mapped_at = _base + timedelta(minutes=_i)
        if _r.confirmed_at is not None:
            _r.confirmed_at = _r.mapped_at
    _db.commit()
finally:
    _db.close()


# ------------------------------------------------- the defect, reproduced
section("The truncation, reproduced rather than asserted from the source")

capped = store.mapped_campaigns(limit=CAP)
check("a capped global read returns exactly the cap", len(capped), CAP)
check("...and it is the NEWEST rows, so the newest client is in it",
      NEW in {m["client"] for m in capped})
check("...while the oldest client's mapping is not",
      OLD in {m["client"] for m in capped}, False)
check("the old way -- filter that list by client -- therefore counts zero campaigns",
      len([m for m in capped if m["client"] == OLD]), 0)
facts = store.facts_for(OLD, date(2026, 9, 1), TODAY)
check("...while facts_for, which queries BY CLIENT, still returns the spend",
      sum(f["spend"] for f in facts), Decimal("2000.00"))
check("...which is the quiet kind of wrong: money right, campaign count zero",
      (len(facts) > 0, len([m for m in capped if m["client"] == OLD])), (True, 0))


# ------------------------------------------------------ campaign_maps_for
section("campaign_maps_for: complete, filtered in the database")

filed = store.campaign_maps_for(OLD)
check("the oldest client's mapping comes back however old it is", len(filed), 1)
check("...with the dict every screen reads it as",
      (filed[0]["client"], filed[0]["platform"], filed[0]["campaign_id"],
       filed[0]["campaign_name"], filed[0]["product"], filed[0]["pending"]),
      (OLD, "ttd", "c-old", "Oldest CTV", "Streaming TV", False))
check("a string argument is one client, not a set of its letters",
      [m["client"] for m in store.campaign_maps_for(OLD)], [OLD])
check("many keys at once, in one query",
      sorted({m["client"] for m in store.campaign_maps_for({OLD, NEW})}), sorted([OLD, NEW]))
check("newest first, as every screen shows them",
      [m["client"] for m in store.campaign_maps_for([OLD, NEW])], [NEW, OLD])
check("no keys is no rows, not every row", store.campaign_maps_for([]), [])
check("...and neither is None", store.campaign_maps_for(None), [])
check("a client with nothing filed reads as empty rather than as everything",
      store.campaign_maps_for("d:nobody.test"), [])
check("mapped_campaigns_for is the same reading",
      store.mapped_campaigns_for(OLD), filed)


# ----------------------------------------------------------- campaign_map
section("campaign_map: one mapping by its primary key")

m = store.campaign_map("ttd", "old", "c-old")
check("the oldest mapping is found past any cap", (m or {}).get("client"), OLD)
check("a campaign that is not filed answers None, it does not raise",
      store.campaign_map("ttd", "old", "no-such"), None)
check("a blank campaign id answers None", store.campaign_map("ttd", "old", ""), None)


# -------------------------------------------------- campaign_maps_by_key
section("campaign_maps_by_key: the mapped members of a touched set")

touched = {("ttd", "old", "c-old"), ("ttd", "new", "c-new"), ("ttd", "old", "never-mapped")}
by_key = store.campaign_maps_by_key(touched)
check("only the mapped ones come back", sorted(m["campaign_id"] for m in by_key),
      ["c-new", "c-old"])
check("...including the oldest, which a capped sweep missed",
      OLD in {m["client"] for m in by_key})
check("a key repeated is a row returned once",
      len(store.campaign_maps_by_key([("ttd", "old", "c-old")] * 3)), 1)
check("a malformed key is skipped rather than throwing the whole batch",
      sorted(m["campaign_id"] for m in store.campaign_maps_by_key(
          [("ttd", "old", "c-old"), None, ("ttd",), "ttd/old/c-old"])), ["c-old"])
check("an empty set is no query at all", store.campaign_maps_by_key(set()), [])
check("...and so is None", store.campaign_maps_by_key(None), [])
check("more keys than one chunk still finds the one that is mapped",
      [m["campaign_id"] for m in store.campaign_maps_by_key(
          [("google", "x", f"pad{i}") for i in range(450)] + [("ttd", "old", "c-old")])],
      ["c-old"])


# ------------------------------------------------------- pending_mappings
section("pending_mappings: the oldest waiting item is still reachable")

store.map_campaign("bing", "b1", "p-old", client="d:pending.test", client_name="Pending Co",
                   mapped_by=store.AUTO_MAPPED_BY, auto_rule="name-match")
_db = store.SessionLocal()
try:
    _r = _db.get(store.CampaignMap, ("bing", "b1", "p-old"))
    _r.mapped_at = datetime(2026, 1, 1, 9, 0, 0)         # older than everything above
    _db.commit()
finally:
    _db.close()
for i in range(FILLER):
    store.map_campaign("google", f"n{i}", f"n{i}", client=f"d:newfiller{i}.test",
                       client_name=f"New filler {i}", mapped_by="Todd")

pend = store.pending_mappings(limit=CAP)
check("the one pending item is in the queue, however old",
      [p["campaign_id"] for p in pend], ["p-old"])
check("...and it is flagged pending", pend[0]["pending"])
check("pending_count agrees with the queue a person can actually reach",
      (store.pending_count(), len(store.pending_mappings())), (1, 1))


# ------------------------------------------------------- the budget book
section("The budget book: same shape, and the line goes ABSENT rather than wrong")

# The oldest client's line is filed FIRST, then a dozen newer ones.
L_OLD = store.add_budget_line(client=OLD, client_name="Oldest Co", product="Streaming TV",
                              monthly_budget="3000", sold_amount="4500", owner="Erik",
                              flight_start="2026-09-01", flight_end="2026-12-31")
for i in range(FILLER):
    store.add_budget_line(client=f"d:bfiller{i}.test", client_name=f"B filler {i}",
                          product="Paid Search", monthly_budget="100")
L_PAUSED = store.add_budget_line(client=OLD, client_name="Oldest Co", product="Paid Search",
                                 monthly_budget="500", status="paused")
_db = store.SessionLocal()
try:
    _base = datetime(2026, 2, 1, 9, 0, 0)
    for _i, _b in enumerate(_db.query(store.BudgetLine)
                              .order_by(store.BudgetLine.id.asc()).all()):
        _b.created_at = _base + timedelta(minutes=_i)
    _db.commit()
finally:
    _db.close()

capped_b = store.budget_lines(limit=CAP)
check("a capped global read of the book returns exactly the cap", len(capped_b), CAP)
check("...and the oldest client's line is not in it",
      L_OLD.id in {b["id"] for b in capped_b}, False)
check("budget_lines_for returns it anyway, however old",
      sorted(b["id"] for b in store.budget_lines_for(OLD)), sorted([L_OLD.id, L_PAUSED.id]))
check("...and active_only leaves the paused one out",
      [b["id"] for b in store.budget_lines_for(OLD, active_only=True)], [L_OLD.id])
check("a string argument is one client",
      [b["client"] for b in store.budget_lines_for(OLD, active_only=True)], [OLD])
check("no keys is no rows, not every row", store.budget_lines_for([]), [])
check("all_budget_lines is the whole book, uncapped",
      len(store.all_budget_lines()), FILLER + 2)
check("...and active_only drops the paused line and nothing else",
      len(store.all_budget_lines(active_only=True)), FILLER + 1)
check("the count is counted in SQL, not len()-ed over a capped read",
      (store.budget_line_count(), store.budget_line_count(active_only=True)),
      (FILLER + 2, FILLER + 1))
check("...so it disagrees with the capped read, which is the whole point",
      store.budget_line_count() > len(store.budget_lines(limit=CAP)))

# A row written before the status column existed reads active: nothing else
# could have written a status then. active_only must honor that or it drops
# every line filed before that migration.
_db = store.SessionLocal()
try:
    _b = _db.get(store.BudgetLine, L_PAUSED.id)
    _b.status = None
    _db.commit()
finally:
    _db.close()
check("a line predating the status column counts as active",
      L_PAUSED.id in {b["id"] for b in store.all_budget_lines(active_only=True)})
check("...through budget_lines_for too",
      sorted(b["id"] for b in store.budget_lines_for(OLD, active_only=True)),
      sorted([L_OLD.id, L_PAUSED.id]))
_db = store.SessionLocal()
try:
    _b = _db.get(store.BudgetLine, L_PAUSED.id)
    _b.status = "paused"
    _db.commit()
finally:
    _db.close()


# ------------------------------------------------------------- the guard
section("The guard: no filtering reader may reach the capped global list")

_real_mapped_campaigns = store.mapped_campaigns
_real_budget_lines = store.budget_lines
_reached: list[str] = []


def _spy(name, real):
    """Counts rather than raises. pacing._overlay_pending and the cost
    report both catch every exception on purpose -- a board that cannot
    count the pending ones still draws the pacing -- so a guard that raised
    would be swallowed there and the test would pass on the broken code."""
    def _wrapped(*a, **kw):
        _reached.append(name)
        return real(*a, **kw)
    return _wrapped


def guarded(label, fn):
    _reached.clear()
    store.mapped_campaigns = _spy("mapped_campaigns", _real_mapped_campaigns)
    store.budget_lines = _spy("budget_lines", _real_budget_lines)
    try:
        fn()
    except Exception as exc:                            # noqa: BLE001 - report it as itself
        check(label, f"{type(exc).__name__}: {exc}", "no capped read")
        return
    finally:
        store.mapped_campaigns = _real_mapped_campaigns
        store.budget_lines = _real_budget_lines
    check(label, f"reached {', '.join(sorted(set(_reached)))}" if _reached else "no capped read",
          "no capped read")


_pacing_rows = []
guarded("pacing.compute reads each client's mappings by client",
        lambda: _pacing_rows.extend(pacing.compute(TODAY)))
guarded("pacing's pending overlay reads only the clients on the board",
        lambda: pacing._overlay_pending(_pacing_rows))
guarded("client_card.summary reads by client",
        lambda: client_card.summary(["Oldest Co"], today=TODAY))
guarded("normalize's sync log reads by campaign key",
        lambda: normalize._log_clients({("ttd", "old", "c-old")}, 1, 5, "scheduler"))
guarded("the cost report reads the whole book uncapped",
        lambda: pacing.cost(today=TODAY))
guarded("client_card's key index reads the whole book uncapped",
        lambda: client_card._filed())

check("mapped_campaigns is still there, bounded, for the recent-activity screen",
      len(store.mapped_campaigns(limit=3)), 3)
check("...and budget_lines, bounded, for the page that pages",
      len(store.budget_lines(limit=3)), 3)


# --------------------------------------------------- what the board prints
section("The consequence: the pacing band a client's rep reads")

row = next((r for r in _pacing_rows if r["client"] == OLD), None)
check("the oldest client's line is on the board", row is not None)
check("...and its campaign's spend is counted",
      (row or {}).get("actual_to_date"), Decimal("2000.00"))
check("...so the band is paced, not 'unmapped'", (row or {}).get("band"), "on")


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
