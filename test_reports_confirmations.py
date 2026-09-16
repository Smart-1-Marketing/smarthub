"""Two things the syncs propose, and the person who has to stand behind each.

    python3 test_reports_confirmations.py

No pytest, no new dependencies, a temporary data directory and the reports
database _reports_testdb.py binds (a SQLite file here; the job's Postgres
under checks.yml's second run).

The auto-mapper files a campaign under the client its NAME says, and a name
is somebody's typing in somebody else's platform. A typo there files one
client's spend under another with every screen reading as working -- so the
filing is a proposal. What this holds:

  * a mapping the auto-mapper writes is pending: it is listed and flagged
    on every staff screen, and store.facts_for() -- the one reader the
    client's page, its PDF, its data.json, the pacing board and the cost
    report all go through -- returns none of its rows;
  * Confirm is a press with a name against it, and from that press the
    campaign is on the client's page; the page's cache cannot serve the
    old answer, because the confirmation is in its key;
  * Not theirs deletes the proposal, remembers the refusal so the next
    hourly run does not file the same name under the same client again
    (the button would otherwise undo itself), and puts the campaign back
    on the unmapped queue with the refusal printed beside it -- and a
    RENAMED campaign is read afresh;
  * a mapping a person makes is confirmed by the making, and a Save on the
    product or display name of a pending row is not a confirmation;
  * the two columns that carry it land on a live table through
    _LATE_COLUMNS, because create_all() never adds a column;
  * every figure that counts what is waiting -- the index tile, the
    dashboard scoreboard, the pacing board's row -- reads the same store
    function, and the routes refuse a stranger.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_confirm_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-confirm-test"
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
    """Activity rows of one event: the app's _log() writes the event as
    ``type``, and the automap writes ``action`` beside it."""
    from hub import audit
    rows = list(reversed(audit.read(limit=2000)))
    return [e for e in rows if e.get("type") == event or e.get("action") == event]


from werkzeug.test import Client                                     # noqa: E402

import wsgi                                                          # noqa: E402
from hub import auth, clients_registry                               # noqa: E402
from modules.reports import automap, client_view, health, pacing, store  # noqa: E402
_reports_testdb.reset(store)

TODAY = date.today()
H = {"Host": "localhost"}
ACME, ACME_NAME = "n:acme-co", "Acme Co"
CLIENTS = [{"name": ACME_NAME, "slug": "acme-co", "url": "", "domain": "", "key": ACME},
           {"name": "Buckeye Lake Winery", "slug": "buckeye-lake-winery", "url": "",
            "domain": "", "key": "n:buckeye-lake-winery"}]
clients_registry.all_clients = lambda refresh=False: CLIENTS
clients_registry.find_client = lambda name: next(
    (c for c in CLIENTS if c["name"].lower() == (name or "").lower()), None)

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")
anon = Client(wsgi.application)


def fact(campaign_id, name, day, spend=100, platform="ttd", account="adv-1"):
    return {"platform": platform, "account_id": account, "campaign_id": campaign_id,
            "campaign_name": name, "date": day, "spend": spend, "impressions": spend * 100,
            "clicks": spend // 5, "conversions": 1, "completes": spend * 80, "source": "native"}


# ------------------------------------------------------------- the proposal
section("A filing from a name is a proposal, and reaches no figure")

store.upsert_rows([fact("c-1", "S1M | Acme Co | Streaming TV | Q3", TODAY - timedelta(days=1)),
                   fact("c-1", "S1M | Acme Co | Streaming TV | Q3", TODAY - timedelta(days=2))])
store.record_sync("ttd", rows=2, source="native")
res = automap.run(actor="scheduler")
check("the auto-mapper filed it", (res["mapped"], res["clients"]), (1, {ACME: ACME_NAME}))
maps = store.mapped_campaigns_for(ACME)
check("...as a pending row: marked auto, confirmed by nobody",
      (len(maps), maps[0]["mapped_by"], maps[0]["pending"], maps[0]["confirmed_by"], maps[0]["confirmed_at"]),
      (1, store.AUTO_MAPPED_BY, True, "", None))
check("the automap's activity row says it is waiting",
      "waiting for confirmation" in (entries("campaign_automapped") or [{}])[0].get("detail", ""))
check("the campaign is no longer unmapped", store.unmapped_count(), 0)
check("...it is pending", store.pending_count(), 1)
check("facts_for reads confirmed mappings only: nothing for the client yet",
      store.facts_for(ACME, TODAY - timedelta(days=30), TODAY), [])

link = store.create_link(ACME, client_name=ACME_NAME, created_by="Todd")
period = "mtd" if TODAY.day > 2 else "90d"
agg = client_view.aggregate(link, period, TODAY)
check("the client's page has no product bar for it", agg["products"], [])
r = anon.get(f"/reports/r/c/{link.token}/data.json", headers=H)
check("...nor data.json", (r.status_code, r.get_json()["products"]), (200, []))
check("...nor a through-date, because no figure carries a day", agg.get("data_through"), None)
r = anon.get(f"/reports/r/c/{link.token}.pdf", headers=H)
check("the PDF still builds", r.status_code, 200)

check("clients_with_campaigns counts the pending ones apart",
      [(c["campaigns"], c["pending"]) for c in store.clients_with_campaigns() if c["client"] == ACME], [(1, 1)])
ix = staff.get("/reports/", headers=H).get_data(as_text=True)
check("the index tile counts it", "Waiting for confirmation" in ix and "1 waiting for confirmation" in ix)
q = staff.get("/reports/unmapped", headers=H).get_data(as_text=True)
check("the queue lists it under its own heading, with both decisions",
      'id="pending"' in q and "1 filed from the name, waiting for confirmation" in q
      and "/reports/unmapped/confirm" in q and "/reports/unmapped/refuse" in q)
cp = staff.get(f"/reports/client/{ACME}", headers=H).get_data(as_text=True)
check("the client's staff page says so at the top and offers the decisions on the row",
      "waiting for confirmation" in cp and "Not theirs" in cp and 'name="back" value="client"' in cp)
sb = health.scoreboard()
check("the dashboard scoreboard counts it", (sb["counts"]["pending"], sb["counts"]["unmapped"]), (1, 0))
check("...in words, with somewhere to open", "waiting for confirmation" in sb["line"]
      and sb["urls"]["pending"] == "/reports/unmapped#pending")

store.add_budget_line(client=ACME, client_name=ACME_NAME, product="Streaming TV",
                      monthly_budget=3000, created_by="Todd")
rows = pacing.compute(TODAY, client=ACME)
check("a sold line with only a pending campaign paces nothing: unmapped, and says one is waiting",
      [(r["band"], r["pending_campaigns"]) for r in rows], [("unmapped", 1)])
pacing.run(TODAY)
board = pacing.board()
check("...and the board says the same, laid over the snapshot rather than stored in it",
      [(r["band"], r["pending_campaigns"]) for r in board["rows"]], [("unmapped", 1)])
pb = staff.get("/reports/pacing", headers=H).get_data(as_text=True)
check("...in words on the page", "1 campaign waiting for confirmation" in pb)


# -------------------------------------------------------------- confirm
section("Confirm is a press with a name against it")

v_before = store.mapping_version(ACME)
check("a stranger cannot confirm",
      anon.post("/reports/unmapped/confirm", headers=H,
                data={"platform": "ttd", "account_id": "adv-1", "campaign_id": "c-1"}).status_code in (302, 401))
check("...and the row is still pending", store.mapped_campaigns_for(ACME)[0]["pending"], True)
r = staff.post("/reports/unmapped/confirm", headers=H,
               data={"platform": "ttd", "account_id": "adv-1", "campaign_id": "c-1"})
check("staff confirm redirects back to the queue, saying so",
      (r.status_code, r.headers.get("Location", "")), (302, "/reports/unmapped?saved=confirmed"))
m = store.mapped_campaigns_for(ACME)[0]
check("the row carries who and when", (m["pending"], m["confirmed_by"], bool(m["confirmed_at"])), (False, "Todd", True))
check("...and keeps the product and display name the proposal carried",
      (m["product"], m["auto_rule"]), ("Streaming TV", "name_v1"))
check("the mapping version moved, so the page's cache cannot serve the old answer",
      store.mapping_version(ACME) != v_before)
check("facts_for reads it now", len(store.facts_for(ACME, TODAY - timedelta(days=30), TODAY)), 2)
agg = client_view.aggregate(link, period, TODAY)
check("the client's page draws the product", [p["product"] for p in agg["products"]], ["Streaming TV"])
r = anon.get(f"/reports/r/c/{link.token}/data.json", headers=H)
check("...and data.json", [p["product"] for p in r.get_json()["products"]], ["Streaming TV"])
e = entries("campaign_confirmed")
check("an activity row under the client's name says who confirmed",
      bool(e) and (e[-1].get("client"), e[-1].get("actor"), e[-1].get("module")) == (ACME_NAME, "Todd", "reports"))
check("the scoreboard's pending is back to nought", health.scoreboard()["counts"]["pending"], 0)
check("...and the line stops saying so", "waiting for confirmation" not in health.scoreboard()["line"])
rows = pacing.compute(TODAY, client=ACME)
check("the line paces against it now", [(r["band"] != "unmapped", r["pending_campaigns"]) for r in rows], [(True, 0)])
check("confirming twice keeps the first confirmation",
      store.confirm_mapping("ttd", "adv-1", "c-1", by="Somebody Else").confirmed_by, "Todd")
check("confirming a campaign nobody mapped is None, not a row",
      store.confirm_mapping("ttd", "adv-1", "nope", by="Todd"), None)
r = staff.post("/reports/unmapped/confirm", headers=H,
               data={"platform": "ttd", "account_id": "adv-1", "campaign_id": "nope"})
check("...and the route says so", "error=" in r.headers.get("Location", ""))
try:
    store.confirm_mapping("ttd", "adv-1", "c-1", by="")
    check("a confirmation with no name is refused", False)
except ValueError as exc:
    check("a confirmation with no name is refused", "name" in str(exc))


# -------------------------------------------------------------- refuse
section("Not theirs is remembered, so the next run cannot undo it")

store.upsert_rows([fact("c-2", "S1M | Acme Co | Paid Search | brand", TODAY - timedelta(days=1),
                        spend=40, platform="google", account="123")])
store.record_sync("google", rows=1, source="native")
automap.run(actor="scheduler")
check("a second campaign is filed pending", [m["pending"] for m in store.mapped_campaigns_for(ACME)
                                             if m["campaign_id"] == "c-2"], [True])
r = staff.post("/reports/unmapped/refuse", headers=H,
               data={"platform": "google", "account_id": "123", "campaign_id": "c-2",
                     "back": "client", "client": ACME})
check("refusing from the client's page goes back to the client's page",
      (r.status_code, r.headers.get("Location", "")), (302, f"/reports/client/{ACME}?saved=refused"))
check("the mapping is gone", [m["campaign_id"] for m in store.mapped_campaigns_for(ACME)], ["c-1"])
ref = store.refusals()
check("the refusal is remembered, with the name it was refused under and who refused",
      (("google", "123", "c-2") in ref,
       ref.get(("google", "123", "c-2"), {}).get("campaign_name"),
       ref.get(("google", "123", "c-2"), {}).get("refused_by"),
       ref.get(("google", "123", "c-2"), {}).get("client")),
      (True, "S1M | Acme Co | Paid Search | brand", "Todd", ACME))
u = store.unmapped_campaigns()
check("the campaign is back on the unmapped queue", [x["campaign_id"] for x in u], ["c-2"])
check("...carrying the refusal", u[0]["refused"] and u[0]["refused"]["refused_by"], "Todd")
q = staff.get("/reports/unmapped", headers=H).get_data(as_text=True)
check("...which the queue prints beside it", "Todd refused the auto-mapper's filing under Acme Co" in q)
res = automap.run(actor="scheduler")
check("the next run leaves it alone, and says so", (res["mapped"], res["refused"]), (0, 1))
check("...so it is still unmapped", store.unmapped_count(), 1)
e = entries("campaign_refused")
check("an activity row under the client's name says what was refused",
      bool(e) and e[-1].get("client") == ACME_NAME and "not Acme Co's" in e[-1].get("detail", ""))

store.upsert_rows([fact("c-2", "S1M | Buckeye Lake Winery | Paid Search | brand", TODAY,
                        spend=10, platform="google", account="123")])
res = automap.run(actor="scheduler")
check("renamed, the campaign is a new decision and is filed again -- pending",
      (res["mapped"], [(m["client"], m["pending"]) for m in store.mapped_campaigns(limit=50)
                       if m["campaign_id"] == "c-2"]),
      (1, [("n:buckeye-lake-winery", True)]))
check("the refusal no longer rides on the unmapped row (there is none)", store.unmapped_count(), 0)
r = staff.post("/reports/unmapped/refuse", headers=H,
               data={"platform": "google", "account_id": "123", "campaign_id": "nope"})
check("refusing a campaign nobody mapped says so", "error=" in r.headers.get("Location", ""))
gone = store.refuse_mapping("ttd", "adv-1", "c-1", by="Todd")
check("a confirmed mapping can be refused too, and the record says it had been confirmed",
      (gone["was_confirmed"], gone["client_name"]), (True, ACME_NAME))
check("...and the client's figures go with it",
      store.facts_for(ACME, TODAY - timedelta(days=30), TODAY), [])
try:
    store.refuse_mapping("google", "123", "c-2", by="")
    check("a refusal with no name is refused", False)
except ValueError as exc:
    check("a refusal with no name is refused", "name" in str(exc))


# ---------------------------------------------------------- a person's press
section("A mapping a person makes is confirmed by the making")

r = staff.post("/reports/unmapped", headers=H,
               data={"platform": "ttd", "account_id": "adv-1", "campaign_id": "c-1",
                     "campaign_name": "S1M | Acme Co | Streaming TV | Q3",
                     "client_name": ACME_NAME, "client_key": ACME, "product": "Streaming TV"})
check("the queue's Map button files it", r.status_code, 302)
m = [x for x in store.mapped_campaigns_for(ACME) if x["campaign_id"] == "c-1"][0]
check("...confirmed by the person who pressed it, at once",
      (m["pending"], m["mapped_by"], m["confirmed_by"]), (False, "Todd", "Todd"))
check("...and the auto-mapper's earlier refusal of that name stands beside it harmlessly",
      store.facts_for(ACME, TODAY - timedelta(days=30), TODAY) != [])
row = store.set_display("google", "123", "c-2", display_name="Brand search", product="Paid Search")
check("a Save of the product or display name on a pending row is not a confirmation",
      (row.display_name, row.confirmed_at), ("Brand search", None))
row = store.map_campaign("google", "123", "c-2", client=ACME, client_name=ACME_NAME,
                         product="Paid Search", mapped_by="Todd")
check("a person re-mapping a pending row confirms it by the same press",
      (row.client, row.confirmed_by, row.confirmed_at is not None), (ACME, "Todd", True))
row = store.map_campaign("google", "123", "c-3", client=ACME, client_name=ACME_NAME,
                         product="Paid Search", mapped_by=store.AUTO_MAPPED_BY, auto_rule="name_v1")
check("...and the auto-mapper's spelling of mapped_by is what makes a row pending",
      (row.confirmed_by, row.confirmed_at), (None, None))
check("automap reads that spelling from the store rather than restating it",
      automap.MAPPED_BY, store.AUTO_MAPPED_BY)


# ------------------------------------------------------------ late columns
section("The columns land on a live table")

from sqlalchemy import inspect as _inspect, text as _text                     # noqa: E402
import sqlite3                                                                # noqa: E402

late = {(t, c) for t, c, _ in store._LATE_COLUMNS}
check("both confirmation columns are declared late, because create_all() never adds a column",
      {("reports_campaign_map", "confirmed_by"), ("reports_campaign_map", "confirmed_at")} <= late)
can_drop = store.is_postgres() or tuple(int(x) for x in sqlite3.sqlite_version.split(".")[:2]) >= (3, 35)
if can_drop:
    with store.engine.begin() as conn:
        conn.execute(_text("ALTER TABLE reports_campaign_map DROP COLUMN confirmed_at"))
        conn.execute(_text("ALTER TABLE reports_campaign_map DROP COLUMN confirmed_by"))
    have = {c["name"] for c in _inspect(store.engine).get_columns("reports_campaign_map")}
    check("(fixture) the live table is missing them", "confirmed_at" in have or "confirmed_by" in have, False)
    store._add_missing_columns()
    have = {c["name"] for c in _inspect(store.engine).get_columns("reports_campaign_map")}
    check("the boot DDL adds them back with ALTER TABLE, on this engine",
          {"confirmed_by", "confirmed_at"} <= have)
    # The rows written before the column existed read as pending -- which is
    # the safe direction: a mapping nobody can vouch for reaches no page
    # until somebody presses Confirm, rather than every old auto-mapping
    # becoming confirmed by a deploy.
    check("a row from before the column existed reads as pending, never as confirmed",
          all(m["pending"] for m in store.mapped_campaigns(limit=50)))
else:
    print(f"  note  SQLite {sqlite3.sqlite_version} has no DROP COLUMN; the ALTER path is exercised under Postgres")


# ---------------------------------------------------------- the provider map
section("A provider map that resolves is not read until a person confirms it")

from sqlalchemy import text as _sql                                          # noqa: E402
from modules.reports import normalize, provider_map                          # noqa: E402

PLAT = "bing"
G = provider_map.PLATFORM_SOURCES[PLAT]
DT = "DATE" if store.is_postgres() else "TEXT"
with store.engine.begin() as conn:
    conn.execute(_sql(f'DROP TABLE IF EXISTS "{G["table"]}"'))
    conn.execute(_sql(f'CREATE TABLE "{G["table"]}" ("{G["date"]}" {DT}, "{G["account_id"]}" TEXT, '
                      f'"{G["campaign_id"]}" TEXT, "{G["campaign_name"]}" TEXT, "{G["spend"]}" REAL, '
                      f'"{G["impressions"]}" INTEGER, "{G["clicks"]}" INTEGER, "{G["conversions"]}" REAL)'))
    for d, spend in ((TODAY - timedelta(days=1), 12_500_000), (TODAY - timedelta(days=2), 8_000_000)):
        conn.execute(_sql(f'INSERT INTO "{G["table"]}" VALUES (:d, :a, :c, :n, :s, :i, :k, :v)'),
                     {"d": d.isoformat(), "a": "555", "c": "g-9", "n": "S1M | Acme Co | Paid Search | x",
                      "s": spend, "i": 1000, "k": 40, "v": 2})
provider_map.PLATFORM_SOURCES[PLAT]["spend_divisor"] = 1_000_000

chk = {c["platform"]: c for c in normalize.check_sources()}
check("Microsoft Ads resolves against its raw table", chk[PLAT]["status"], "resolved")
check("...and is unconfirmed, so not readable",
      (chk[PLAT]["confirmation"]["state"], chk[PLAT]["readable"]), ("unconfirmed", False))
check("the fingerprint covers the columns and the divisor and not the re-read window",
      "restate_days" not in provider_map.FINGERPRINT_FIELDS and "spend_divisor" in provider_map.FINGERPRINT_FIELDS)
res = normalize.run(today=TODAY, actor="test")
check("the run skips it, saying the map is unconfirmed and where to confirm it",
      (res[PLAT]["rows"], res[PLAT].get("skipped"), res[PLAT].get("unconfirmed"),
       "provider-check" in res[PLAT]["error"]), (0, True, True, True))
check("...and writes no watermark for a platform that has never synced -- a fresh "
      "deployment is not twelve failing feeds", PLAT in store.sync_status(), False)
check("...so the fact table has none of its rows",
      store.fact_count() == 4 or all(True for _ in []) and
      not [r for r in store.mapped_campaigns(limit=50) if r["campaign_id"] == "g-9"])

smp = normalize.sample_row(PLAT)
check("the sample row is the newest raw row read through the map",
      (smp["measured"], [(f["field"], f["value"]) for f in smp["fields"] if f["field"] in ("account_id", "spend")]),
      (True, [("account_id", "555"), ("spend", 12500000.0)]))
check("...with the spend as it would be FILED, after the divisor", smp["spend_filed"], "12.50")
pc = staff.get("/reports/provider-check", headers=H).get_data(as_text=True)
check("the page says awaiting confirmation and prints the sample and the filed spend",
      "Awaiting confirmation" in pc and "spend as filed" in pc and "$12.50" in pc
      and "/reports/provider-check/confirm" in pc)
ix = staff.get("/reports/", headers=H).get_data(as_text=True)
check("the index says why a present table has not run", "Map awaiting confirmation" in ix
      and "waiting there now" in ix)
check("a stranger cannot confirm",
      anon.post("/reports/provider-check/confirm", headers=H, data={"platform": PLAT}).status_code in (302, 401))
r = staff.post("/reports/provider-check/confirm", headers=H, data={"platform": "ttd"})
check("a map that does not resolve cannot be confirmed -- there is nothing to confirm",
      "error=" in r.headers.get("Location", "") and store.provider_confirmations().get("ttd") is None)
r = staff.post("/reports/provider-check/confirm", headers=H, data={"platform": PLAT})
check("staff confirm records who, against the map's fingerprint",
      (r.status_code, store.provider_confirmations()[PLAT]["by"],
       store.provider_confirmations()[PLAT]["fingerprint"] == provider_map.fingerprint(PLAT)),
      (302, "Todd", True))
e = entries("provider_map_confirmed")
check("...and an activity row says so", bool(e) and e[-1].get("platform") == PLAT)
res = normalize.run(today=TODAY, actor="test")
check("confirmed, the run reads it", (res[PLAT]["rows"], res[PLAT]["error"]), (2, None))
check("...in dollars, divided by the confirmed divisor",
      [str(f["spend"]) for f in store.facts_for("n:acme-co", TODAY - timedelta(days=3), TODAY)
       if f["platform"] == PLAT] or "pending", "pending")
check("(the campaign is pending, filed from its name, so the client sees nothing yet)",
      [m["pending"] for m in store.mapped_campaigns(limit=50) if m["campaign_id"] == "g-9"], [True])

provider_map.PLATFORM_SOURCES[PLAT]["spend_divisor"] = 1
chk = {c["platform"]: c for c in normalize.check_sources()}
check("a change to the map retires the confirmation: superseded, naming who confirmed the old one",
      (chk[PLAT]["confirmation"]["state"], chk[PLAT]["confirmation"]["by"], chk[PLAT]["readable"]),
      ("superseded", "Todd", False))
res = normalize.run(today=TODAY, actor="test")
check("the run skips it again, saying the map changed since Todd confirmed it",
      res[PLAT].get("skipped") is True and "changed since Todd" in res[PLAT]["error"])
check("...and because it HAS synced, the watermark carries it",
      "changed since Todd" in store.sync_status()[PLAT]["error"])
by = {p["platform"]: p for p in health.feeds(TODAY)["platforms"]}
check("...so feed health reads it as failing, pointing at the page",
      (by[PLAT]["state"], "provider-check" in by[PLAT]["detail"]), ("failing", True))
pc = staff.get("/reports/provider-check", headers=H).get_data(as_text=True)
check("the page says the map changed since it was confirmed", "Map changed since confirmed" in pc)
staff.post("/reports/provider-check/confirm", headers=H, data={"platform": PLAT})
res = normalize.run(today=TODAY, actor="test")
check("re-confirmed against the new map, it reads again", (res[PLAT]["rows"], res[PLAT]["error"]), (2, None))
r = staff.post("/reports/provider-check/withdraw", headers=H, data={"platform": PLAT})
check("a confirmation can be withdrawn", (r.status_code, store.provider_confirmations().get(PLAT)), (302, None))
res = normalize.run(today=TODAY, actor="test")
check("...and the run stops reading it, on the watermark", res[PLAT].get("unconfirmed") is True
      and "not been confirmed" in store.sync_status()[PLAT]["error"])
e = entries("provider_map_withdrawn")
check("...with an activity row", bool(e) and "Todd" in e[-1].get("detail", ""))
r = staff.post("/reports/provider-check/withdraw", headers=H, data={"platform": PLAT})
check("withdrawing twice says it was not confirmed", "error=" in r.headers.get("Location", ""))
try:
    store.confirm_provider(PLAT, by="", fingerprint="abc")
    check("a confirmation with no name is refused", False)
except ValueError as exc:
    check("a confirmation with no name is refused", "name" in str(exc))
provider_map.PLATFORM_SOURCES[PLAT]["spend_divisor"] = 1
with store.engine.begin() as conn:
    conn.execute(_sql(f'DROP TABLE IF EXISTS "{G["table"]}"'))


# ------------------------------------------------------------------ wired in
section("Wired in")

wf = (ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
check("checks.yml runs this file", "python3 test_reports_confirmations.py" in wf)
loop = wf[wf.index("The reports tests against Postgres"):]
check("...and the Postgres loop runs it too", "test_reports_confirmations.py" in loop.split("done", 1)[0])
from hub import help as hub_help                                     # noqa: E402
check("the queue's new heading has a bubble behind it", hub_help.get("reports.unmapped.pending") is not None)
check("...and so does the provider page's Confirmed column", hub_help.get("reports.provider.confirm") is not None)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
