# A copy of the landed rows, and two indexes the live table lacked

Todd named two things for the phase after the history backfill: make the
database fit the reports and pacing reads once it carries years rather than
weeks, and keep a copy of the landed rows so none of it has to be pulled
twice. Both are in `modules/reports/store.py` and the new
`modules/reports/backup.py`.

## The reads, measured, and the two indexes

The fact table (`reports_ad_perf_daily`) carried one index, its key:
`(platform, account_id, campaign_id, date)`. Read on the live table before
the change: about 30,000 rows and 8.5 MB. The query sites fall into three
shapes:

* by platform and account with a date range — the client views, the pacing
  arithmetic. The key covers these;
* every platform since a date — the unmapped list and the automap's recent
  spend. A scan of the whole table;
* one platform over a date range — the reconcile's month, the quarantine's
  neighbors, the health reading. A scan of the platform's rows.

So two indexes: `(date)` and `(platform, date)`. They are declared on the
model, which covers a new table, and in `_LATE_INDEXES`, which covers the
live one: `create_all()` creates a missing table with its indexes and never
adds an index to a table that exists, the same gap `_LATE_COLUMNS` closes
for columns. `CREATE INDEX IF NOT EXISTS` runs at boot under the same probe,
on Postgres and SQLite alike, and a race with the other worker is swallowed
the way the columns' is. `index_status()` reads what the live table carries,
index by index, and the Backups card prints it — a reading, not the
declaration.

Not done, and worth knowing: the reports tables live in the Hub's own
1 GB Postgres (`smart1sitesdata`), not in the dedicated `smart1-hub-db`
instance `render.yaml` describes for them, which is empty. That is
`REPORTS_DATABASE_URL` being blank or pointing at the Hub database. Pointing
it at the dedicated instance is an environment change plus a one-time copy
of the rows, and now that the backup exists the copy is a restore.

## The backup

`/var/data/reports/backups/` on the persistent disk, which outlives a
deploy:

* `facts/<platform>/<YYYY-MM>.jsonl.gz` — one platform-month per file. The
  platforms restate for four weeks and then settle, so an old month is
  written once and a recent one is rewritten when its rows change. Changed
  is a hash of the serialized rows against the manifest, so a run with
  nothing new writes nothing, and the set never grows past the data;
* `tables/<table>.jsonl.gz` — the campaign map, aliases, budget lines,
  markups, links, sync watermarks and map refusals, whole. Small, and the
  map is what nobody wants to confirm twice. Not the pacing snapshots, the
  quarantine or the reconcile: those are recomputed from the rows;
* `manifest.json` — rows, bytes, sha256, written_at and the off-site URL
  per file.

The off-site copy goes through `hub/storage.put` into the `backups` bucket,
only when Cloudinary is ready — `put()`'s disk fallback would be a second
copy on the same disk, which is not a second copy. Unset, the card says
"No copy off this disk" with the reason.

`restore()` puts the rows back through `store.upsert_rows`, the one door
every writer goes through, screen and all: a figure somebody discarded stays
out, and a spike is held again for a person rather than restored past them
(the restore's result counts what was held). The small tables go back by a
keyed merge. It adds and updates and never deletes, so running it against a table
that still has its rows gives the same table, and a row somebody added
since stays. That is why the button on the Reports index is a button and
not a ceremony.

## When it runs

`job_reports_backup` in `hub/scheduler.py`, checked hourly, runs once a day
from 8 AM Eastern, after the 3 AM pull and the 4 to 8 AM history window,
on the background lane (`docs/claude/77`). A day with a finished backup is
not backed up again; **Back up now** forces it. A deploy that ends a run
mid-way leaves the manifest a step behind the files, and the next run
rewrites what differs.

## Moving the tables to the dedicated database

The reports tables live in the Hub's own 1 GB Postgres, not in the
dedicated `smart1-hub-db` instance `render.yaml` describes for them, which
is empty: `REPORTS_DATABASE_URL` is blank or points at the Hub database.
The move is an environment change plus one restore, and the backup makes
it safe to do in that order:

1. On `/reports/`, press **Back up now** and wait for the Backups card to
   read "current" with today's time. The copy is on the persistent disk,
   which the next deploy keeps.
2. On Render, set `REPORTS_DATABASE_URL` to the dedicated instance's
   Internal Database URL. Check the linked env group first: a service-level
   value overrides a group's silently. The deploy restarts the Hub, the
   store creates the tables on the new instance with their indexes, and
   the Backups card reads: "The dedicated reports database (host/name):
   0 fact rows and 0 mapped campaigns live; the copy holds N and M", with
   the **database** pill red and "The live tables hold fewer rows than the
   copy". The nightly backup refuses to run over that — an empty table is
   never written over a full file.
3. Press **Restore from backup**. It runs on the background lane; reload
   in a minute or two. The card reads live equal to the copy, the pill goes
   green, and the platform table on the same page shows the rows.
4. The old tables stay in the Hub database, untouched. Drop them later or
   not at all; nothing reads them once the binding moved.

The nightly ledger, the history ledger, the refresh note and the Trade
Desk and Amazon DSP notes are files on the disk, not rows, so they carry
across unchanged. The hourly provider normalize writes to the new instance
from its first tick after the restart.

`test_reports_backup.py` holds the indexes on a table that lacked them, the
partition files, the unchanged run that writes nothing, the changed row
that rewrites one file, the run over emptied tables that refuses, the
restore into them, the row it never deletes, the scheduler's gates and
the buttons.
