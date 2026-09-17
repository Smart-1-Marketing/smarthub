"""A copy of the landed rows, and the two indexes the live fact table lacked.

    python3 test_reports_backup.py

No pytest, no new dependencies, a throwaway reports database (Postgres in
CI through _reports_testdb, SQLite here) and no network: the off-site copy
is a stand-in, and Cloudinary unset is itself one of the cases.

What it holds:

  * the two late indexes are on a fresh table, and a table that lost one
    gets it back at boot, and index_status() reads the live table;
  * a backup writes one file per platform-month plus the small tables, a
    second run with nothing changed writes nothing, a changed row rewrites
    exactly its month, the files are gzip JSONL with every column;
  * with Cloudinary unset the run says there is no copy off this disk; a
    stand-in off-site copy is counted and its URL kept;
  * a restore into an emptied table puts every row and the campaign map
    back, and a row added since is not deleted;
  * the scheduler job waits for 8 AM, does not back up twice in a day,
    and the button forces it on the lane; restore rides the same button;
  * the Reports index draws the card, its state, and the index reading.
"""
import gzip
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_backup_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-backup-test"
os.environ["HUB_SCHEDULER"] = "false"
for k in ("CLOUDINARY_URL", "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"):
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


from modules.reports import backup, store                            # noqa: E402
_reports_testdb.reset(store)
from sqlalchemy import inspect as _inspect, text as _text            # noqa: E402

# ---------------------------------------------------------------- indexes
section("The fact table's two late indexes")

st = {i["name"]: i for i in store.index_status()}
check("both indexes are declared and present on a fresh table",
      (sorted(st), all(i["present"] for i in st.values())),
      (["ix_reports_ad_perf_daily_date", "ix_reports_ad_perf_daily_platform_date"], True), note=st)
with store.engine.begin() as conn:
    conn.execute(_text("DROP INDEX ix_reports_ad_perf_daily_platform_date"))
check("a table that lost one reads as missing it",
      {i["name"]: i["present"] for i in store.index_status()}["ix_reports_ad_perf_daily_platform_date"], False)
store._add_missing_indexes()
check("...and the boot step puts it back", all(i["present"] for i in store.index_status()))
live = {i["name"]: list(i["column_names"]) for i in _inspect(store.engine).get_indexes("reports_ad_perf_daily")}
check("...on the columns declared", (live.get("ix_reports_ad_perf_daily_date"), live.get("ix_reports_ad_perf_daily_platform_date")),
      (["date"], ["platform", "date"]))

# ------------------------------------------------------------- the rows
section("A backup writes one file per platform-month, and only what changed")


def _row(platform, day, campaign="c1", spend="1.50"):
    return {"platform": platform, "account_id": "a1", "campaign_id": campaign, "date": day,
            "campaign_name": "camp " + campaign, "spend": spend, "impressions": 100, "clicks": 3,
            "conversions": "1", "source": "native", "extras": {"k": "v"}}


store.upsert_rows([_row("google", date(2026, 8, 30)), _row("google", date(2026, 9, 1)),
                   _row("google", date(2026, 9, 2), "c2"), _row("ttd", date(2026, 9, 3))], screen=False)
db = store.SessionLocal()
db.add(store.CampaignMap(platform="google", account_id="a1", campaign_id="c1", client="Acme Roofing",
                         client_name="Acme Roofing", product="search", mapped_by="Todd",
                         mapped_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
db.add(store.PlatformMarkup(platform="google", markup=20, updated_by="Todd"))
db.commit(); db.close()

parts = backup.fact_partitions()
check("the partitions are the platform-months on file",
      parts, [("google", 2026, 8, 1), ("google", 2026, 9, 2), ("ttd", 2026, 9, 1)])
res = backup.run(actor="Todd", offsite=False)
check("the first run writes every file", (res["ok"], res["written"], res["files"]),
      (True, 3 + len(backup.TABLES), 3 + len(backup.TABLES)), note=res)
check("...counting the rows", res["rows"], 4 + 2)
root = backup.root()
check("...as gzip JSONL under facts/<platform>/<month>",
      sorted(os.listdir(os.path.join(root, "facts", "google"))), ["2026-08.jsonl.gz", "2026-09.jsonl.gz"])
with gzip.open(os.path.join(root, "facts", "google", "2026-09.jsonl.gz"), "rt", encoding="utf-8") as fh:
    lines = [json.loads(line) for line in fh if line.strip()]
check("...with every column on every row, dates and decimals as text",
      (len(lines), lines[0]["date"], lines[0]["spend"], lines[0]["extras_json"], lines[0]["source"]),
      (2, "2026-09-01", "1.50", {"k": "v"}, "native"))
man = backup.manifest()
check("the manifest carries each file's rows, bytes and hash",
      (man["facts"]["google/2026-09"]["rows"], man["facts"]["google/2026-09"]["bytes"] > 0,
       len(man["facts"]["google/2026-09"]["sha256"]), man["tables"]["campaign_map"]["rows"]),
      (2, True, 64, 1))
check("...and with Cloudinary unset says there is no copy off this disk",
      backup.run(offsite=True)["offsite_note"], "Cloudinary is not configured, so there is no copy off this disk.")
res = backup.run(offsite=False)
check("a run with nothing changed writes nothing", (res["written"], res["files"]), (0, 3 + len(backup.TABLES)))
store.upsert_rows([_row("google", date(2026, 9, 2), "c2", spend="9.99")], screen=False)
res = backup.run(offsite=False)
check("a changed row rewrites exactly its month", res["written"], 1)
with open(os.path.join(root, "facts", "google", "2026-09.jsonl.gz"), "rb") as fh:
    _sept = backup._unpack(fh.read())
check("...and the file carries the new figure", [r["spend"] for r in _sept if r["campaign_id"] == "c2"], ["9.99"])

# ------------------------------------------------------------ off-site
section("An off-site copy is counted, and its URL kept")

_real_offsite, _real_avail = backup._offsite, backup._offsite_available
backup._offsite_available = lambda: ""
backup._offsite = lambda relpath, data: (f"https://res.example/{relpath}", "")
store.upsert_rows([_row("ttd", date(2026, 9, 4))], screen=False)
res = backup.run(offsite=True)
check("the changed file went off-site", (res["written"], res["offsite"]), (1, 1))
check("...with its URL in the manifest", backup.manifest()["facts"]["ttd/2026-09"]["offsite_url"], "https://res.example/facts/ttd/2026-09.jsonl.gz")
backup._offsite, backup._offsite_available = _real_offsite, _real_avail

# ------------------------------------------------------------- restore
section("A restore puts the rows back and deletes nothing")

db = store.SessionLocal()
db.query(store.AdPerfDaily).delete()
db.query(store.CampaignMap).delete()
db.commit(); db.close()
check("the tables are empty", (store.fact_count(), len(backup.table_rows("campaign_map"))), (0, 0))
store.upsert_rows([_row("bing", date(2026, 9, 5))], screen=False)
res = backup.restore(actor="Todd")
check("the restore answers with counts, through the screen (nothing held here)",
      (res["ok"], res["facts"], res["tables"] >= 2, res["held"]), (True, 5, True, 0), note=res)
check("...every fact row is back, with the restated figure", store.fact_count(), 6)
db = store.SessionLocal()
row = db.get(store.AdPerfDaily, ("google", "a1", "c2", date(2026, 9, 2)))
m = db.get(store.CampaignMap, ("google", "a1", "c1"))
bing = db.get(store.AdPerfDaily, ("bing", "a1", "c1", date(2026, 9, 5)))
got = (float(row.spend), row.extras, m.client, m.mapped_at.year if m else None, bing is not None)
db.close()
check("...with the figure, the extras and the map's client and timestamp", got, (9.99, {"k": "v"}, "Acme Roofing", 2026, True))
check("...and the row added since the backup is still there (never deletes)", got[4], True)
st = backup.status()
check("status says a restore happened", (st["last_restore"]["facts"], st["last_restore"]["actor"]), (5, "Todd"))
check("...and the backup is current (bing's month is not in it yet: a restore is not a backup)",
      (st["ever"], st["stale"], st["running"], st["files"]), (True, False, False, 3 + len(backup.TABLES)))

# ---------------------------------------------------------- scheduler
section("The scheduler job and the buttons")

from hub import scheduler                                            # noqa: E402
from flask import Flask                                              # noqa: E402

check("the job is registered on the background lane",
      "reports_backup" in scheduler.JOBS and "reports_backup" in scheduler.BACKGROUND_JOBS)
_real_hour = scheduler._eastern_hour
try:
    scheduler._eastern_hour = lambda: 5
    r = scheduler.job_reports_backup(Flask("t"))
    check("at 5 AM the nightly job waits", "waits for 8 AM" in r.get("skipped", "") or "backed up today" in r.get("skipped", ""), note=r)
    scheduler._eastern_hour = lambda: 9
    r = scheduler.job_reports_backup(Flask("t"))
    check("at 9 AM on a day already backed up it does not run again",
          r.get("skipped") == "backed up today already", note=r)
    man = backup.manifest(); man["finished_at"] = "2026-01-01T09:00:00+00:00"; backup._save_manifest(man)
    r = scheduler.job_reports_backup(Flask("t"))
    check("...and on a day without one it backs up", r.get("ok") is True and "files" in r, note=r)
finally:
    scheduler._eastern_hour = _real_hour
res = scheduler.backup_now(actor="Ann")
check("the button starts a backup in the background", res["started"] and "Backing up" in res["note"], note=res)
t = scheduler._background.get("reports_backup")
if t is not None:
    t.join(20)
check("...which ran as Ann", backup.manifest()["last"]["actor"], "Ann")
res = scheduler.backup_now(actor="Ann", restore=True)
check("the restore button starts a restore", res["started"] and "never deleted" in res["note"], note=res)
t = scheduler._background.get("reports_backup")
if t is not None:
    t.join(20)
check("...which ran", backup.manifest()["last_restore"]["actor"], "Ann")

# ---------------------------------------------------------- the page
section("The Reports index draws the Backups card")

from werkzeug.test import Client                                     # noqa: E402
from hub import auth                                                 # noqa: E402
from modules.reports import app as reports_app                       # noqa: E402

c = Client(reports_app.app)
c.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")
page = c.get("/").get_data(as_text=True)
check("the card is there with both buttons", 'id="backups"' in page and "Back up now" in page and "Restore from backup" in page)
check("...saying the backup is current, with its files and rows", "current" in page and "platform-months" in page)
check("...that only the stand-in's file is off this disk, and why the rest are not",
      "1 of" in page and "also in Cloudinary" in page and "Cloudinary is not configured" in page)
check("...and the index reading from the live table", "date on (date): present" in page and "platform_date on (platform, date): present" in page)
r = c.post("/backup", environ_base={"s1hub.user": "Todd"})
check("Back up now answers at once with a redirect to the card", (r.status_code, "#backups" in r.headers.get("Location", "")), (302, True))
t = scheduler._background.get("reports_backup")
if t is not None:
    t.join(20)
r = c.post("/backup/restore", environ_base={"s1hub.user": "Todd"})
check("Restore answers the same way", (r.status_code, "saved=" in r.headers.get("Location", "")), (302, True), note=r.headers.get("Location"))
t = scheduler._background.get("reports_backup")
if t is not None:
    t.join(20)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
