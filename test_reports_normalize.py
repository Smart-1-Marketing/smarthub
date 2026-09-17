"""The provider normalize, the provider check and the name-based auto-mapper.

    python3 test_reports_normalize.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database. The provider schema is EMPTY here (REPORTS_PROVIDER_SCHEMA=""),
which is how the raw tables sit beside the fact table on SQLite.

What it holds:

  * provider_map covers every platform except suite, with the 28-day restate
    on the three platforms that restate for a month;
  * schema_tables() lists what is actually present and check_sources() says,
    per platform, resolved / table missing / these columns missing;
  * rows from a fake raw table land in AdPerfDaily as source="windsor" with
    spend divided by the platform's spend_divisor, and only rows inside the
    restate window are read;
  * a platform whose rows are bad is isolated: it records its error on the
    watermark and the other platforms still sync;
  * the name parser accepts the documented variants and rejects near-misses;
  * the auto-mapper resolves a client by exact key, then exact name, files a
    CampaignMap with mapped_by="auto", writes the activity row, and never
    overwrites a mapping a person made;
  * a name without the mark is read for a likeness to a client: whole name,
    domain label or near spelling; filed (as a proposal, rule fuzzy_v1)
    only on a clear best, shown beside the queue row otherwise, never
    under a client somebody refused;
  * the scheduler job is registered and returns the shape the panel reads.
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

TMP = tempfile.mkdtemp(prefix="s1reports_norm_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-normalize-test"

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


from sqlalchemy import text                                          # noqa: E402

from modules.reports import automap, normalize, provider_map, store  # noqa: E402
_reports_testdb.reset(store, extra_tables=("google_ads", "facebook_ads", "ttd"))

TODAY = date(2026, 9, 6)


# --------------------------------------------------------------- the map
section("The provider map")

check("every platform except suite has a source",
      sorted(provider_map.PLATFORM_SOURCES), sorted(p for p in store.PLATFORMS if p != "suite"))
check("the three long-restating platforms re-read 28 days",
      {p: provider_map.PLATFORM_SOURCES[p]["restate_days"] for p in ("ttd", "amazon_sa", "amazon_dsp")},
      {"ttd": 28, "amazon_sa": 28, "amazon_dsp": 28})
check("...and the rest re-read a week",
      all(v["restate_days"] == 7 for k, v in provider_map.PLATFORM_SOURCES.items()
          if k not in ("ttd", "amazon_sa", "amazon_dsp")))
check("an empty schema means no prefix", provider_map.qualified("google_ads"), "google_ads")
os.environ["REPORTS_PROVIDER_SCHEMA"] = "raw_windsor"
check("...and a set one is prefixed, read at call time",
      provider_map.qualified("google_ads"), "raw_windsor.google_ads")
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
check("the docstring says the column names are placeholders",
      "PLACEHOLDER" in (provider_map.__doc__ or ""))


# ------------------------------------------------------- the raw tables
section("Fake raw tables with the placeholder column names")

G = provider_map.PLATFORM_SOURCES["google"]
M = provider_map.PLATFORM_SOURCES["meta"]
T = provider_map.PLATFORM_SOURCES["ttd"]
# The raw date column is DATE on Postgres (what the provider writes there)
# and TEXT on SQLite (which has no date type); normalize binds its bound
# per dialect, and this is the half of that rule a fake table has to keep.
DT = "DATE" if store.is_postgres() else "TEXT"
with store.engine.begin() as conn:
    conn.execute(text(f'CREATE TABLE "{G["table"]}" ("{G["date"]}" {DT}, "{G["account_id"]}" TEXT, '
                      f'"{G["campaign_id"]}" TEXT, "{G["campaign_name"]}" TEXT, "{G["spend"]}" REAL, '
                      f'"{G["impressions"]}" INTEGER, "{G["clicks"]}" INTEGER, "{G["conversions"]}" REAL)'))
    conn.execute(text(f'CREATE TABLE "{M["table"]}" ("{M["date"]}" {DT}, "{M["account_id"]}" TEXT, '
                      f'"{M["campaign_id"]}" TEXT, "{M["campaign_name"]}" TEXT, "{M["spend"]}" REAL, '
                      f'"{M["impressions"]}" INTEGER, "{M["clicks"]}" INTEGER, "{M["conversions"]}" REAL, '
                      f'"video_views" INTEGER)'))
    # The Trade Desk table is present but missing two of the columns the map
    # names -- the state provider-check exists to show.
    conn.execute(text(f'CREATE TABLE "{T["table"]}" ("{T["date"]}" {DT}, "{T["account_id"]}" TEXT, '
                      f'"{T["campaign_id"]}" TEXT, "{T["campaign_name"]}" TEXT)'))
    for d, spend, imps, clicks in (
            (TODAY - timedelta(days=1), 12_500_000, 1000, 40),   # micros
            (TODAY - timedelta(days=3), 8_000_000, 800, 30),
            (TODAY - timedelta(days=30), 99_000_000, 9000, 900)):  # outside 7 days
        conn.execute(text(f'INSERT INTO "{G["table"]}" VALUES (:d, :a, :c, :n, :s, :i, :k, :v)'),
                     {"d": d.isoformat(), "a": "123-456", "c": "g-1",
                      "n": "S1M | Acme Plumbing | Paid Search | Brand",
                      "s": spend, "i": imps, "k": clicks, "v": 2})
    conn.execute(text(f'INSERT INTO "{G["table"]}" VALUES (:d, :a, :c, :n, :s, :i, :k, :v)'),
                 {"d": (TODAY - timedelta(days=2)).isoformat(), "a": "123-456", "c": "g-2",
                  "n": "SIM | Acme Plumbing | Paid Search | typo", "s": 1_000_000, "i": 10,
                  "k": 1, "v": 0})
    # A good meta row, and a bad one (no campaign id) that fails the batch.
    conn.execute(text(f'INSERT INTO "{M["table"]}" VALUES (:d, :a, :c, :n, :s, :i, :k, :v, :vv)'),
                 {"d": (TODAY - timedelta(days=1)).isoformat(), "a": "act_9", "c": "m-1",
                  "n": "s1m | d:acme.com | Social | Q4", "s": 75.5, "i": 900, "k": 12,
                  "v": 3, "vv": 400})
    conn.execute(text(f'INSERT INTO "{M["table"]}" VALUES (:d, :a, :c, :n, :s, :i, :k, :v, :vv)'),
                 {"d": (TODAY - timedelta(days=1)).isoformat(), "a": "act_9", "c": None,
                  "n": "broken", "s": 1, "i": 1, "k": 1, "v": 0, "vv": 0})

tables = normalize.schema_tables()
check("schema_tables() lists the raw tables present",
      all(t in tables for t in (G["table"], M["table"], T["table"])))
check("...with their columns", G["spend"] in tables[G["table"]])
checks = {c["platform"]: c for c in normalize.check_sources(tables)}
check("google resolves", checks["google"]["status"], "resolved")
check("meta resolves", checks["meta"]["status"], "resolved")
check("ttd names its missing columns", checks["ttd"]["status"], "columns_missing")
check("...by name", sorted(checks["ttd"]["missing"]),
      sorted([T["spend"], T["impressions"], T["clicks"], T["conversions"]] + T["extras"]))
check("a platform with no table says so", checks["linkedin"]["status"], "table_missing")
check("every mapped platform is listed", len(checks), len(provider_map.PLATFORM_SOURCES))


# ------------------------------------------------------- a stub registry
section("A stub client registry")

from hub import clients_registry                                     # noqa: E402

CLIENTS = [
    {"name": "Acme Plumbing", "slug": "acme-plumbing", "url": "https://acme.com",
     "domain": "acme.com", "key": "d:acme.com"},
    {"name": "Buckeye Lake Winery", "slug": "buckeye-lake-winery", "url": "",
     "domain": "", "key": "n:buckeye-lake-winery"},
]
clients_registry.all_clients = lambda refresh=False: CLIENTS

check("a key resolves exactly", automap.resolve_client("d:acme.com"), ("d:acme.com", "Acme Plumbing"))
check("...whatever its case", automap.resolve_client("D:ACME.COM"), ("d:acme.com", "Acme Plumbing"))
check("a name resolves case-insensitively",
      automap.resolve_client("acme plumbing"), ("d:acme.com", "Acme Plumbing"))
check("a name with no URL resolves to its name key",
      automap.resolve_client("Buckeye Lake Winery"), ("n:buckeye-lake-winery", "Buckeye Lake Winery"))
check("a substring resolves nobody", automap.resolve_client("Acme"), None)
check("an empty token resolves nobody", automap.resolve_client("  "), None)


# ---------------------------------------------------------- the parser
section("The name parser")

check("the documented shape parses",
      automap.parse_name("S1M | Acme Plumbing | Paid Search | Brand"),
      {"client": "Acme Plumbing", "product": "Paid Search", "rest": "Brand"})
check("lower case and extra spaces are accepted",
      automap.parse_name("  s1m |  Acme   | Streaming TV |   Q4  "),
      {"client": "Acme", "product": "Streaming TV", "rest": "Q4"})
check("three segments is enough",
      automap.parse_name("S1M|Acme|Display"), {"client": "Acme", "product": "Display", "rest": ""})
check("anything after the product is kept whole",
      automap.parse_name("S1M | Acme | Display | a | b")["rest"], "a | b")
check("SIM is not S1M", automap.parse_name("SIM | Acme | Display | x"), None)
check("two segments parses with no product, for the platform default to fill in",
      automap.parse_name("S1M | Acme"), {"client": "Acme", "product": "", "rest": ""})
check("S1M has to lead", automap.parse_name("Acme | S1M | Display | x"), None)
check("an empty client segment is refused", automap.parse_name("S1M |  | Display | x"), None)
check("an empty product segment is an empty product, not a refusal",
      automap.parse_name("S1M | Acme |  | x")["product"], "")
check("no pipes at all is refused", automap.parse_name("S1M Acme Display"), None)
check("a blank name is refused", automap.parse_name(""), None)


# ------------------------------------------------------ a human mapping
section("A mapping a person made survives the run")

store.upsert_rows([{"platform": "bing", "account_id": "b-acct", "campaign_id": "b-1",
                    "campaign_name": "S1M | Acme Plumbing | Paid Search | old",
                    "date": TODAY - timedelta(days=1), "spend": 5, "impressions": 10,
                    "clicks": 1, "source": "csv"}])
store.map_campaign("bing", "b-acct", "b-1", client="n:buckeye-lake-winery",
                   client_name="Buckeye Lake Winery", mapped_by="Todd")


# ------------------------------------------------------------- the run
section("The run")

# The provider reports Google in micros on this deployment.
provider_map.PLATFORM_SOURCES["google"]["spend_divisor"] = 1_000_000
# A resolved map is read only once a person has confirmed it against a raw
# row -- test_reports_confirmations.py holds that gate; here the two
# platforms the run is about are confirmed as they stand, and the Trade
# Desk deliberately is not, because it does not resolve.
for _p in ("google", "meta"):
    store.confirm_provider(_p, by="Todd", fingerprint=provider_map.fingerprint(_p))
res = normalize.run(today=TODAY, actor="test")

check("google synced the rows inside the window", res["google"],
      {"rows": 3, "error": None, "quarantined": 0, "quarantine_reasons": {}})
check("meta failed on its bad row and is isolated",
      bool(res["meta"]["error"]) and res["meta"]["rows"] == 0)
check("...naming the cause", "campaign_id" in res["meta"]["error"])
check("ttd was skipped for its missing columns",
      res["ttd"].get("skipped") is True and "missing columns" in res["ttd"]["error"])
check("a platform with no table is skipped and said",
      res["linkedin"].get("skipped") is True and "not in the schema" in res["linkedin"]["error"])
check("every platform in the map is in the answer",
      sorted(k for k in res if k != "automap"), sorted(provider_map.PLATFORM_SOURCES))

db = store.SessionLocal()
try:
    g1 = db.get(store.AdPerfDaily, ("google", "123-456", "g-1", TODAY - timedelta(days=1)))
    g_old = db.get(store.AdPerfDaily, ("google", "123-456", "g-1", TODAY - timedelta(days=30)))
    g2 = db.get(store.AdPerfDaily, ("google", "123-456", "g-2", TODAY - timedelta(days=2)))
    m1 = db.get(store.AdPerfDaily, ("meta", "act_9", "m-1", TODAY - timedelta(days=1)))
finally:
    db.close()
check("a google row landed", g1 is not None)
check("...with spend in dollars, divided by the divisor", g1.spend, Decimal("12.50"))
check("...source windsor", g1.source, "windsor")
check("...and the counts", (g1.impressions, g1.clicks, g1.conversions), (1000, 40, Decimal("2.00")))
check("a row outside the restate window was not read", g_old is None)
check("the near-miss campaign's rows still land (facts are not mappings)", g2 is not None)
check("meta's good row did not land, because its batch was refused whole", m1 is None)

syncs = store.sync_status()
check("the watermark records google's run", (syncs["google"]["rows"], syncs["google"]["error"]), (3, ""))
check("...and meta's failure", bool(syncs["meta"]["error"]))
check("...and ttd's missing columns", "missing columns" in syncs["ttd"]["error"])
check("...but not a platform with no table", "linkedin" not in syncs)
status = {p["platform"]: p for p in store.platform_status()}
check("/reports/ reads the watermark beside the fact table",
      status["google"]["sync_rows"] == 3 and status["meta"]["sync_error"] != "")

# Idempotent: the same run again is the same spend.
res2 = normalize.run(today=TODAY, actor="test")
db = store.SessionLocal()
try:
    total = db.query(store.func.sum(store.AdPerfDaily.spend)).filter(
        store.AdPerfDaily.platform == "google").scalar()
finally:
    db.close()
check("a second run writes the same rows, not twice the spend", Decimal(total), Decimal("21.50"))


# ------------------------------------------------------------ automap
section("The auto-mapper")

am = res["automap"]
check("the campaign named S1M | Acme Plumbing | ... was filed", "d:acme.com" in am["clients"])
mapped = {(m["platform"], m["campaign_id"]): m for m in store.mapped_campaigns(limit=100)}
g = mapped.get(("google", "g-1"))
check("...under the client's Hub key", g and g["client"], "d:acme.com")
check("...with the product from the name", g and g["product"], "Paid Search")
check("...marked auto with the rule", g and (g["mapped_by"], g["auto_rule"]), ("auto", "name_v1"))
# The SIM near-miss is not read as the S1M shape (the parser test above),
# but its name plainly carries Acme Plumbing's, so the likeness pass files
# it -- as a proposal, marked as filed by likeness, never as name_v1.
g2 = mapped.get(("google", "g-2"))
check("the SIM near-miss is filed by likeness, not read as the mark",
      g2 and (g2["client"], g2["auto_rule"], g2["pending"]), ("d:acme.com", "fuzzy_v1+name_product", True))
check("...counted apart on the run", am["suggested"], 1)
b = mapped.get(("bing", "b-1"))
check("the human mapping survived, unchanged",
      b and (b["client"], b["mapped_by"], b["auto_rule"]), ("n:buckeye-lake-winery", "Todd", ""))
check("a second run maps nothing new", res2["automap"]["mapped"], 0)

# A campaign naming a client nobody can resolve is named, not guessed.
store.upsert_rows([{"platform": "x", "account_id": "x-acct", "campaign_id": "x-1",
                    "campaign_name": "S1M | Nobody Here | Display | z",
                    "date": TODAY - timedelta(days=1), "spend": 1, "impressions": 1,
                    "clicks": 0, "source": "csv"}])
am3 = automap.run(actor="test")
check("an unresolvable client token is reported by name", am3["unresolved"], ["Nobody Here"])
check("...and mapped nowhere", ("x", "x-1") not in
      {(m["platform"], m["campaign_id"]) for m in store.mapped_campaigns(limit=100)})

from hub import audit
entries = list(reversed(audit.read(limit=2000)))   # the log is a table now, not that file
auto = [e for e in entries if e.get("action") == "campaign_automapped"]
check("the automap wrote an activity row under the client's name",
      bool(auto) and auto[0].get("client") == "Acme Plumbing" and auto[0].get("module") == "reports")
sync = [e for e in entries if e.get("action") == "reports_sync"]
# Acme's only mapping so far is the auto-mapper's proposal, and a proposal
# is not yet a fact about whose campaign it is: a sync row on Acme's record
# would say we synced their campaigns before anybody had agreed they were
# theirs. Confirmed, the next run files it.
check("no sync row while the client's only mapping is waiting for confirmation", sync, [])
check("...because the automap's filing is pending",
      store.mapped_campaigns(limit=100)[0].get("pending") in (True, False) and
      all(m["pending"] for m in store.mapped_campaigns(limit=100) if m["client"] == "d:acme.com"))
store.confirm_mapping("google", "123-456", "g-1", by="Todd")
normalize.run(today=TODAY, actor="test")
entries = list(reversed(audit.read(limit=2000)))   # the log is a table now, not that file
sync = [e for e in entries if e.get("action") == "reports_sync"]
check("confirmed, the sync wrote one activity row per client touched",
      sorted(e.get("client") for e in sync[:1]), ["Acme Plumbing"])
check("...saying what it did", bool(sync) and sync[0]["detail"].startswith("synced "))
check("...and not for the client whose campaign received no rows",
      not [e for e in sync if e.get("client") == "Buckeye Lake Winery"])

# A product segment the catalog does not know must not become a product:
# "Strming TV" as a bar on the client's page is one no budget line can pace.
store.upsert_rows([{"platform": "ttd", "account_id": "t-acct", "campaign_id": "t-typo",
                    "campaign_name": "S1M | Acme Plumbing | Strming TV | Q4",
                    "date": TODAY - timedelta(days=1), "spend": 3, "impressions": 30,
                    "clicks": 1, "source": "csv"}])
automap.run(actor="test")
from modules.reports import products as _products                    # noqa: E402
typo = [m for m in store.mapped_campaigns(limit=200) if m["campaign_id"] == "t-typo"][0]
check("a product segment outside the catalog files under the platform default",
      typo["product"], _products.default_for("ttd"))
check("...and the rule says the segment was not understood", "unknown_product" in (typo["auto_rule"] or ""))

# A registry that cannot be read is not a book of unknown clients.
from hub import clients_registry as _reg                             # noqa: E402
_all = _reg.all_clients
def _boom():
    raise RuntimeError("knack is down")
_reg.all_clients = _boom
store.upsert_rows([{"platform": "x", "account_id": "x-acct", "campaign_id": "x-2",
                    "campaign_name": "S1M | Acme Plumbing | Display | reg-down",
                    "date": TODAY - timedelta(days=1), "spend": 1, "impressions": 1,
                    "clicks": 0, "source": "csv"}])
am4 = automap.run(actor="test")
_reg.all_clients = _all
check("a registry that could not be read is named on the run", "knack is down" in (am4.get("registry_error") or ""))
check("...and nothing is reported as unresolved on the strength of it", am4["unresolved"], [])
check("...and the campaign stays unmapped for the next run",
      ("x", "x-2") not in {(m["platform"], m["campaign_id"]) for m in store.mapped_campaigns(limit=200)})

# ------------------------------------------------------- likeness
section("Filing by likeness of the name")

FUZZ = CLIENTS + [
    {"name": "Acme Roofing", "slug": "acme-roofing", "url": "https://acmeroofing.com",
     "domain": "acmeroofing.com", "key": "d:acmeroofing.com"},
    {"name": "AB", "slug": "ab", "url": "", "domain": "", "key": "n:ab"},
]
sug = lambda n, **k: [(h["name"], h["pct"]) for h in automap.suggest_clients(n, rows=FUZZ, **k)]
check("the client's name in the campaign name is a whole match",
      sug("Acme Plumbing - Search - Brand")[0], ("Acme Plumbing", 100))
check("...whatever the separators and case", sug("acme_plumbing|PMAX|2026")[0], ("Acme Plumbing", 100))
check("...and with a legal suffix on the client's side", sug("Buckeye Lake Winery LLC retargeting")[0],
      ("Buckeye Lake Winery", 100))
check("the name run together is the name", sug("acmeplumbing-search")[0], ("Acme Plumbing", 100))
check("the domain label is next best", sug("acmeroofing 2026 leads")[0], ("Acme Roofing", 100))
check("a near spelling scores by likeness",
      sug("Acme Plumbng | Search")[0][0] == "Acme Plumbing" and 90 <= sug("Acme Plumbng | Search")[0][1] < 100)
check("a shared first word is a lead for both, and equal",
      sug("Acme | Search"), [("Acme Plumbing", 75), ("Acme Roofing", 75)])
check("a two-letter client is a lead at most", sug("AB test")[0], ("AB", 75))
check("a name like nobody's suggests nobody", sug("Random Campaign 12"), [])
check("a blank name suggests nobody", sug(""), [])
check("the refused client is left out", sug("Acme Plumbing | Search", exclude="d:acme.com")[0][0], "Acme Roofing")
check("suggestions are capped", len(automap.suggest_clients("Acme Plumbing Roofing", rows=FUZZ, limit=1)), 1)

dec = lambda n: (automap.decide(automap.suggest_clients(n, rows=FUZZ)) or {}).get("name")
check("a whole name with nobody close is filed", dec("Acme Plumbing - Search"), "Acme Plumbing")
check("two clients alike is filed under neither", dec("Acme | Search"), None)
check("a lead is not a filing", dec("AB test"), None)
check("nothing is nothing", dec("Random Campaign 12"), None)
check("a near spelling under the bar is shown, not filed",
      sug("Buckeye Winery - Search")[0][0] == "Buckeye Lake Winery" and dec("Buckeye Winery - Search") is None)
check("the file bar is above the show bar", automap.FUZZY_FILE_SCORE > automap.FUZZY_SHOW_SCORE)

check("a catalog product named whole in the name is read", automap.product_from_name("Acme | Streaming TV | Q4"), "Streaming TV")
check("...case apart", automap.product_from_name("acme paid search brand"), "Paid Search")
check("a word that is not a catalog name is not a product", automap.product_from_name("Acme | Search"), "")

# Through run(): the likeness pass files the clear one, leaves the
# ambiguous one with the queue, and never touches a refusal.
_all_clients = clients_registry.all_clients
clients_registry.all_clients = lambda refresh=False: FUZZ
store.upsert_rows([
    {"platform": "meta", "account_id": "act_f", "campaign_id": "f-clear",
     "campaign_name": "Acme Roofing - Leads - Streaming TV",
     "date": TODAY - timedelta(days=1), "spend": 5, "impressions": 50, "clicks": 1, "source": "csv"},
    {"platform": "meta", "account_id": "act_f", "campaign_id": "f-ambig",
     "campaign_name": "Acme - Leads",
     "date": TODAY - timedelta(days=1), "spend": 5, "impressions": 50, "clicks": 1, "source": "csv"},
    {"platform": "meta", "account_id": "act_f", "campaign_id": "f-none",
     "campaign_name": "Spring promo 2026",
     "date": TODAY - timedelta(days=1), "spend": 5, "impressions": 50, "clicks": 1, "source": "csv"},
])
am5 = automap.run(actor="test")
filed = {(m["platform"], m["campaign_id"]): m for m in store.mapped_campaigns(limit=200)}
clear = filed.get(("meta", "f-clear"))
check("the clear likeness is filed as a proposal", clear and (clear["client"], clear["mapped_by"], clear["pending"]),
      ("d:acmeroofing.com", "auto", True))
check("...with the product the name carries", clear and (clear["product"], clear["auto_rule"]),
      ("Streaming TV", "fuzzy_v1+name_product"))
check("the ambiguous one is left for the queue", ("meta", "f-ambig") not in filed)
check("...and counted as ambiguous", am5["ambiguous"], 1)
check("the one like nobody is left too", ("meta", "f-none") not in filed)
check("the run counts the likeness filings", am5["suggested"], 1)
entries = list(reversed(audit.read(limit=2000)))
by_like = [e for e in entries if e.get("action") == "campaign_automapped" and e.get("campaign_id") == "f-clear"]
check("the activity row says it was filed by likeness, and how alike",
      bool(by_like) and "by likeness (100%" in by_like[0]["detail"] and by_like[0].get("client") == "Acme Roofing")

# Refused, it is not filed again under that client from that name, and the
# queue no longer suggests that client for it.
store.refuse_mapping("meta", "act_f", "f-clear", by="Todd")
am6 = automap.run(actor="test")
check("a refused likeness filing stays refused", ("meta", "f-clear") not in
      {(m["platform"], m["campaign_id"]) for m in store.mapped_campaigns(limit=200)})
check("...and is counted as refused", am6["refused"] >= 1)
queue = store.unmapped_campaigns(days=30, limit=100)
pending = store.pending_mappings()
annotated = automap.annotate(queue, pending)
check("annotate() answers with no error", annotated, {"error": ""})
q = {r["campaign_id"]: r for r in queue}
check("the queue's ambiguous row carries both leads",
      [c["name"] for c in q["f-ambig"]["suggestions"]], ["Acme Plumbing", "Acme Roofing"])
check("the refused row does not suggest the refused client",
      [c["name"] for c in q["f-clear"]["suggestions"]], ["Acme Plumbing"])
check("a row like nobody carries an empty list, not a missing key", q["f-none"]["suggestions"], [])
pend = {m["campaign_id"]: m for m in pending}
check("a pending likeness filing says why", pend["g-2"]["match"] and pend["g-2"]["match"]["pct"], 100)
check("...and a pending S1M filing carries no likeness", pend.get("t-typo", {}).get("match"), None)

# A registry that cannot be read: nothing suggested, nothing filed, named.
clients_registry.all_clients = _boom
am7 = automap.run(actor="test")
check("the likeness pass stops on an unreadable registry, naming it", "knack is down" in (am7.get("registry_error") or ""))
check("...and files nothing", am7["mapped"], 0)
ann = automap.annotate(queue)
check("annotate() names the unreadable registry", "knack is down" in ann["error"])
check("...and every row still has its (empty) list", all(r["suggestions"] == [] for r in queue))
clients_registry.all_clients = _all_clients

# A table that synced and is then gone is a finding on the watermark; one
# that never synced (linkedin, above) still records nothing.
with store.engine.begin() as conn:
    conn.execute(text(f'DROP TABLE "{G["table"]}"'))
normalize.run(today=TODAY, actor="test")
syncs_after = {s["platform"]: s for s in store.platform_status()}
check("a platform whose table vanished after it synced records the error on its watermark",
      "not in the schema" in (syncs_after["google"]["sync_error"] or ""))
check("...and one that never synced still has no watermark", "linkedin" not in syncs)


# ------------------------------------------------------ the scheduler
section("The scheduler job")

from hub import scheduler                                            # noqa: E402

check("the job is registered", "reports_normalize" in scheduler.JOBS)
every, fn, desc = scheduler.JOBS["reports_normalize"]
check("...hourly", every, 60)
check("...as the thin function", fn.__name__, "job_reports_normalize")
src = Path(ROOT / "hub" / "scheduler.py").read_text(encoding="utf-8")
body = src[src.index("def job_reports_normalize"):src.index("\nJOBS = {")]
check("...inside an app context, with the flask.g note",
      "with app.app_context():" in body and "flask.g" in body)

from flask import Flask                                              # noqa: E402

# The job reads the WALL clock (its signature is (app) -> dict, and the
# scheduler hands it nothing else), while every row above is dated against
# the pinned TODAY -- so the bad meta row that failed the direct run above
# sits inside meta's seven-day window only for a week after that date, and
# this section went red on the eighth day with the job unchanged. Seed the
# same bad row against the real clock so what the job is asked about is
# what it can see: the test_client360_health.py rule, one clock over.
with store.engine.begin() as conn:
    conn.execute(text(f'INSERT INTO "{M["table"]}" VALUES (:d, :a, :c, :n, :s, :i, :k, :v, :vv)'),
                 {"d": (date.today() - timedelta(days=1)).isoformat(), "a": "act_9", "c": None,
                  "n": "broken (real clock)", "s": 1, "i": 1, "k": 1, "v": 0, "vv": 0})
out = fn(Flask("t"))
check("the job returns the shape the panel reads",
      sorted(out), ["automapped", "errors", "platforms", "rows", "skipped", "synced"])
check("...naming the failed platform and not the skipped ones",
      "meta" in out["errors"] and "ttd" in out["skipped"] and "ttd" not in out["errors"])


# ---------------------------------------------------- provider-check page
section("The provider-check page")

from werkzeug.test import Client                                     # noqa: E402

from modules.reports import app as reports_app                       # noqa: E402

from hub import auth                                                 # noqa: E402

c = Client(reports_app.app)
c.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")
r = c.get("/provider-check")
page = r.get_data(as_text=True)
check("it renders", r.status_code, 200)
check("...listing the tables present", G["table"] in page and M["table"] in page)
check("...and every platform's verdict",
      page.count("Table missing") + page.count("Columns missing") + page.count("Resolved"),
      len(provider_map.PLATFORM_SOURCES))
check("...naming ttd's missing columns", T["spend"] in page)
check("...with no script on it", "<script" not in page.split("s1d-page")[-1].split("</body>")[0]
      or page.count("<script") <= 1)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
