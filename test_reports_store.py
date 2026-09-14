"""The Reports store: the fact table, the campaign map, budgets and markup.

    python3 test_reports_store.py

Same shape as the other test files: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

What it holds:

  * the engine binding prefers REPORTS_DATABASE_URL, then DATABASE_URL, then
    a SQLite file -- the order modules/reports/store.py documents, asserted
    rather than trusted, because a reports module quietly writing its fact
    table into the 1 GB Hub database is the failure the variable exists to
    prevent and nothing on screen would say so;
  * upsert_rows() is idempotent: the same day synced twice is the same spend,
    not twice the spend;
  * a row naming a platform outside PLATFORMS is refused, and refused before
    anything in its batch is written;
  * the unmapped query lists a campaign until it is mapped and not after;
  * PlatformMarkup refuses a row carrying both a markup and a fixed CPM, and
    one carrying neither.
"""
import os
import shutil
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_store_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["SECRET_KEY"] = "reports-store-test"

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


def raises(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except ValueError as exc:
        return str(exc)
    return ""


# ------------------------------------------------------------ the binding
section("The engine is bound to the reports database first")

from modules.reports import store                                   # noqa: E402
_reports_testdb.reset(store)

check("with REPORTS_DATABASE_URL set, that is the database",
      store.database_url(), os.environ["REPORTS_DATABASE_URL"])
check("and the module reports which binding it is on", store.binding(), "reports")
# Against whichever database this run was bound to: the SQLite file by
# default, the Postgres in REPORTS_TEST_DATABASE_URL under checks.yml.
check("the engine the module built is on that URL",
      (str(store.engine.url).endswith("reports.sqlite3") if REPORTS_DB == "sqlite"
       else store.engine.dialect.name.startswith("postgres")))
check("...and says which engine that is", store.is_postgres(), REPORTS_DB == "postgres")
check("and the boot DDL reported no error", store.DB_BOOT_ERROR, "")

_saved = os.environ.pop("REPORTS_DATABASE_URL")
check("with it blank, DATABASE_URL is the fallback",
      store.database_url(), os.environ["DATABASE_URL"])
check("...and says so", store.binding(), "hub")
_hub = os.environ.pop("DATABASE_URL")
check("with neither, a SQLite file on the data disk",
      store.database_url(),
      "sqlite:///" + os.path.join(os.environ["HUB_DATA_DIR"], "reports.sqlite3"))
check("...and says so", store.binding(), "sqlite")
os.environ["DATABASE_URL"] = "postgres://u:p@h/db"
check("Render's postgres:// spelling is normalized the way the Hub's is",
      store.database_url(), "postgresql://u:p@h/db")
os.environ["DATABASE_URL"] = _hub
os.environ["REPORTS_DATABASE_URL"] = _saved


# --------------------------------------------------------- the fact table
section("upsert_rows() is idempotent")

ROW = {"platform": "google", "account_id": "123-456", "campaign_id": "c-1",
       "campaign_name": "Brand", "date": "2026-09-01", "spend": "100.00",
       "impressions": 1000, "clicks": 40, "conversions": "3", "source": "windsor",
       "extras": {"quality_score": 7}}

check("one row written", store.upsert_rows([ROW]), 1)
check("...is one row", store.fact_count(), 1)
check("written again, still one row", (store.upsert_rows([ROW]), store.fact_count()), (1, 1))

db = store.SessionLocal()
try:
    got = db.get(store.AdPerfDaily, ("google", "123-456", "c-1", date(2026, 9, 1)))
    check("the spend is the spend, not twice it", got.spend, Decimal("100.00"))
    check("the extras rode along as JSON", got.extras, {"quality_score": 7})
    check("the source is recorded", got.source, "windsor")
    check("synced_at carries an offset", store.iso(got.synced_at).endswith("+00:00"))
finally:
    db.close()

changed = dict(ROW, spend="150.50", campaign_name="Brand (renamed)")
store.upsert_rows([changed])
db = store.SessionLocal()
try:
    got = db.get(store.AdPerfDaily, ("google", "123-456", "c-1", date(2026, 9, 1)))
    check("a re-sync replaces the figures rather than adding a row",
          (store.fact_count(), got.spend, got.campaign_name),
          (1, Decimal("150.50"), "Brand (renamed)"))
finally:
    db.close()

check("a second day is a second row",
      (store.upsert_rows([dict(ROW, date="2026-09-02")]), store.fact_count()), (1, 2))
check("a date object is accepted as well as a string",
      store.upsert_rows([dict(ROW, date=date(2026, 9, 3))]), 1)


section("A platform outside PLATFORMS is refused")

check("PLATFORMS is the thirteen the work order names",
      store.PLATFORMS,
      ("ttd", "google", "bing", "linkedin", "tiktok", "audiogo", "stackadapt",
       "meta", "groundtruth", "x", "amazon_sa", "amazon_dsp", "suite"))
msg = raises(store.upsert_rows, [dict(ROW, platform="facebook")])
check("'facebook' is refused with the list in the message",
      "facebook" in msg and "meta" in msg)
check("...and 'Google' is not a second platform", store.upsert_rows(
    [dict(ROW, platform="Google", date="2026-09-04")]), 1)
before = store.fact_count()
msg = raises(store.upsert_rows, [dict(ROW, date="2026-09-10"),
                                 dict(ROW, platform="nope", date="2026-09-11")])
check("a bad row in a batch refuses the batch", bool(msg))
check("...before anything in it was written", store.fact_count(), before)
check("a row with no date is refused", "date" in raises(store.upsert_rows, [dict(ROW, date="")]))
check("a source outside SOURCES is refused",
      "source" in raises(store.upsert_rows, [dict(ROW, source="guess")]).lower())
check("nothing is nothing", store.upsert_rows([]), 0)

section("Every platform is listed on the status board, synced or not")
status = store.platform_status()
check("thirteen rows", len(status), 13)
google = next(r for r in status if r["platform"] == "google")
check("Google shows its rows and a sync time",
      google["rows"] >= 4 and bool(google["synced_at"]))
ttd = next(r for r in status if r["platform"] == "ttd")
check("a platform that never synced says so rather than being left off",
      (ttd["rows"], ttd["synced_at"]), (0, None))
check("Smart 1 Suite is named as such",
      next(r for r in status if r["platform"] == "suite")["label"], "Smart 1 Suite")


# -------------------------------------------------------- the campaign map
section("The unmapped query excludes what is mapped")

today = date.today().isoformat()
store.upsert_rows([
    dict(ROW, platform="meta", account_id="act_1", campaign_id="m-1",
         campaign_name="Meta Big", date=today, spend="900"),
    dict(ROW, platform="meta", account_id="act_1", campaign_id="m-2",
         campaign_name="Meta Small", date=today, spend="10"),
    dict(ROW, platform="ttd", account_id="t1", campaign_id="old",
         campaign_name="Ancient", date="2020-01-01", spend="99999"),
])
rows = store.unmapped_campaigns(days=30)
ids = [(r["platform"], r["campaign_id"]) for r in rows]
check("every campaign is unmapped to start with",
      set(ids) >= {("meta", "m-1"), ("meta", "m-2"), ("google", "c-1"), ("ttd", "old")})
check("biggest recent spend first", ids[0], ("meta", "m-1"))
old = next(r for r in rows if r["campaign_id"] == "old")
check("spend from years ago does not count as recent", old["spend_30d"], Decimal(0))
check("...so it sorts to the bottom", ids[-1], ("ttd", "old"))
check("the count agrees with the list", store.unmapped_count(), len(rows))

m = store.map_campaign("meta", "act_1", "m-1", client="d:acme.com",
                       client_name="Acme Co", product="Social", mapped_by="Todd")
check("mapping writes the client key, name and product",
      (m.client, m.client_name, m.product, m.mapped_by),
      ("d:acme.com", "Acme Co", "Social", "Todd"))
check("a person's mapping carries no auto rule", m.auto_rule, None)
after = [(r["platform"], r["campaign_id"]) for r in store.unmapped_campaigns()]
check("the mapped campaign has left the list", ("meta", "m-1") not in after)
check("...and only that one", len(after), len(ids) - 1)
check("the count moved with it", store.unmapped_count(), len(ids) - 1)
check("it is on the recently-mapped list",
      store.mapped_campaigns()[0]["campaign_id"], "m-1")

m2 = store.map_campaign("meta", "act_1", "m-1", client="d:other.com",
                        client_name="Other", mapped_by="Todd")
check("re-mapping replaces rather than duplicates",
      (m2.client, len(store.mapped_campaigns())), ("d:other.com", 1))
auto = store.map_campaign("meta", "act_1", "m-2", client="n:acme",
                          auto_rule="name:S1M")
check("an auto-mapped row says which rule filed it", auto.auto_rule, "name:S1M")
check("a mapping with no client is refused",
      "client" in raises(store.map_campaign, "meta", "act_1", "m-3", client=""))
check("a mapping on an unknown platform is refused",
      "platform" in raises(store.map_campaign, "fb", "a", "b", client="n:x").lower())
check("the rename hint is the documented shape",
      store.RENAME_SHAPE, "S1M | <ClientKey> | <Product> | <anything>")


# --------------------------------------------------------------- budgets
section("Budget lines")

b = store.add_budget_line(client="d:acme.com", client_name="Acme Co", product="CTV",
                          monthly_budget="2500", platform="ttd",
                          flight_start="2026-09-01", flight_end="2026-12-31",
                          notes="Q4", created_by="Todd")
check("a typed line is marked manual", b.source_json, {"manual": True})
check("the figure is a Decimal to the cent", b.monthly_budget, Decimal("2500.00"))
check("the flight is dated", (b.flight_start, b.flight_end),
      (date(2026, 9, 1), date(2026, 12, 31)))
listed = store.budget_lines()
check("it lists, newest first, with the manual flag",
      (listed[0]["product"], listed[0]["manual"], listed[0]["platform_label"]),
      ("CTV", True, "The Trade Desk"))
check("a zero budget is refused",
      "zero" in raises(store.add_budget_line, client="n:a", product="x", monthly_budget="0"))
check("a flight that ends before it starts is refused",
      "before" in raises(store.add_budget_line, client="n:a", product="x",
                         monthly_budget="5", flight_start="2026-10-01",
                         flight_end="2026-09-01"))
check("a budget with no product is refused",
      "product" in raises(store.add_budget_line, client="n:a", product="", monthly_budget="5"))
check("a platform is optional", store.add_budget_line(
    client="n:a", product="Search", monthly_budget="10").platform, None)
check("an imported line keeps its own source", store.add_budget_line(
    client="n:a", product="Search", monthly_budget="10",
    source={"io": "IO-1234"}).source_json, {"io": "IO-1234"})


# ---------------------------------------------------------------- markup
section("PlatformMarkup is a markup or a fixed CPM, never both")

msg = raises(store.set_markup, "google", markup="0.15", cpm="12.50")
check("both set is refused", "not both" in msg)
check("...naming the platform", "Google Ads" in msg)
check("neither set is refused", bool(raises(store.set_markup, "google")))
check("blank strings count as unset, so both-blank is refused",
      bool(raises(store.set_markup, "google", markup="", cpm="")))
r = store.set_markup("google", markup="0.15", updated_by="Todd")
check("a markup alone is stored as a fraction", (r.markup, r.cpm), (Decimal("0.1500"), None))
r = store.set_markup("ttd", cpm="12.5", updated_by="Todd")
check("a CPM alone is stored to the cent", (r.markup, r.cpm), (None, Decimal("12.50")))
r = store.set_markup("google", cpm="9", updated_by="Todd")
check("switching Google to a CPM clears its markup", (r.markup, r.cpm), (None, Decimal("9.00")))
check("15 on the screen is 0.15 in the column",
      store.markup_from_percent("15"), Decimal("0.1500"))
check("...and 12.345 rounds to four places", store.markup_from_percent("12.345"),
      Decimal("0.1235"))
check("a negative markup is refused", "negative" in raises(store.set_markup, "bing", markup="-1"))
check("a zero CPM is refused", "zero" in raises(store.set_markup, "bing", cpm="0"))
check("an unknown platform is refused", bool(raises(store.set_markup, "fb", cpm="1")))
rows = store.markups()
check("every platform is listed, set or not", len(rows), 13)
bing = next(r for r in rows if r["platform"] == "bing")
check("an unset platform reads as unset rather than as zero",
      (bing["markup"], bing["cpm"], bing["updated_at"]), (None, None, None))
check("the screen's percentage is derived from the fraction",
      next(r for r in rows if r["platform"] == "ttd")["cpm"], Decimal("12.50"))
check("clearing removes the row", (store.clear_markup("ttd"), store.clear_markup("ttd")),
      (True, False))


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
