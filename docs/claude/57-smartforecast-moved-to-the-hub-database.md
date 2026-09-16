# SmartForecast moved to the Hub database, and what the move found

SmartForecast kept its own SQLite file on the Render disk
(`SMARTFORECAST_DB_PATH`, defaulting under `HUB_DATA_DIR`). That file was the
last large store outside the Hub's Postgres, and it is why the service still
needs a disk at all.

## Why a file was the problem, and a backup was not the answer

The module already had half a recovery story: `backup()` dumped the database
and mirrored the dump through `hub/jsonstore.py`, so a recreated disk could be
restored. That half was never the problem.

The other half is that **a file is local to one instance.** The two halves of
a zero-downtime deploy each keep a SmartForecast database, each one complete
looking, and which one answers is whichever container the browser reached. A
site paused on one is live on the other, and no error is raised on either.
Nothing a nightly dump does fixes that.

## The shim, and why the SQL was not rewritten

`modules/smartforecast/db.py` holds a `sqlite3`-shaped API over the shared
engine from `hub/extensions.py`, so the module's 115 SQL statements stayed as
they were. It carries:

- **`to_named()`** — `?` to `:p0`, because SQLAlchemy's `text()` takes named
  binds only. It skips `?` inside string literals, escaped quotes and quoted
  identifiers, and it **refuses a parameter-count mismatch** rather than
  sending a statement with the wrong number of values.
- **`portable()`** — `INSERT OR IGNORE` to `ON CONFLICT DO NOTHING`.
- **`autoid()`** — `BIGSERIAL PRIMARY KEY` or `INTEGER PRIMARY KEY`. The schema
  carries `%%AUTOID%%` in its 13 generated-id slots rather than either
  spelling, so there is one schema rather than two that drift.
- **`statements()`** — splits a script on `;` outside literals *and* comments.
  Written the obvious way first, it split on the semicolon inside one of the
  schema's own `--` comments.
- **`Row`** — answers to a name and to a position, because `sqlite3.Row` does
  and the module reads both ways.
- **PRAGMA** — `user_version` becomes a row in `schema_migrations`;
  `quick_check` runs a real read and reports `unreadable: <type>` if it fails,
  rather than returning a passing constant from the one check whose entire job
  is to say whether the database is sound. Any other PRAGMA is refused **by
  name**, so a new caller gets an error rather than a silent no-op.

## The three defects the move found, every one invisible on SQLite

Found by running the module against Postgres, not by reading it:

1. **Postgres refuses a forward foreign-key reference.** `engagement_events`
   was declared four tables ahead of the `embed_tokens` it references. SQLite
   resolves references lazily and has accepted this for the life of the
   module. `check_declaration_order()` is now a test, not a comment.
2. **A generated-id sequence does not advance on an explicit id.** The demo
   seed writes `clients(id,...) VALUES(1,...)` six times, so a fresh
   deployment seeded perfectly and then raised a duplicate key the first time
   anybody added a client. `fix_sequences()` runs at the end of
   `initialize()`, covering the seed, the import and a restore.
3. **A SELECT alias is not visible in `HAVING` on Postgres.** `due_sites()`
   read `HAVING latest_expiry ...` and would have taken the whole weather
   refresh with it.

A fourth trap shaped the tests rather than the code: a **failed** insert still
consumes a Postgres sequence value, so a sequence test that probes by
inserting passes or fails depending on what ran before it. `sequence_state()`
asks the sequence directly instead, guarded by `to_regclass` because
`pg_get_serial_sequence` *raises* on a table that is not there — and a
verification that crashes instead of reporting is the one shape it may not
take.

## The one-time import, and how it is checked

`initialize()` calls `_import_legacy_sqlite()`, which copies the legacy file
in table order and then **verifies row counts per table** before writing the
"done" marker through `jsonstore`. If verification fails the marker is not
written, so the import is retried rather than silently half-applied.

## Two rows on the health screen were reading the file

`operational_health()` measured `store.path`, which after the move names the
legacy file — absent or frozen at import day:

- **`database_bytes`** reported a confident `0` for a database holding rows.
  It is `db.database_bytes()` now: the SQLite file on the fallback, the total
  size of this module's own tables on the shared cluster. It reports
  `database_bytes_scope` so nobody reads a scope change as "the database
  shrank", and `database_bytes_measured: False` rather than a `0` it never
  measured.
- **`backup_fresh`** went permanently false, because `backup()` deliberately
  takes no dump on a managed database — a dump beside a backed-up database is
  a second copy of the truth on a different schedule. It reports
  `backup_by: "hub_database"` and stays fresh there; on the SQLite fallback,
  where the database sits on the very disk this was trying to survive the loss
  of, the dump still earns its keep and its age is still the question.

## The table names are shared now, and two of them are the obvious ones

Every other module in this repo prefixes its tables — `ads_`, `cb_`, `hub_`.
SmartForecast's predate the shared engine and do not: `clients`, `sites`,
`locations` and `schema_migrations` are exactly the names a second module
would reach for, and in one database a collision is not an error — it is two
modules reading each other's rows.

**Nothing collides today** (checked across `hub/` and every other module), and
renaming fifteen tables under 115 statements is a mechanical change that does
not belong in the same commit as the move. So the risk is held by a test
rather than left to be noticed: `OneDatabaseForEveryModule` in
`test_smartforecast_store.py` fails if any module outside
`modules/smartforecast/` ever declares one of the fifteen names, and the list
it guards is `db.TABLES` itself rather than a second copy that can go stale.
A prefix migration, if it is wanted, is its own change on top of this one.

## What stays SQLite

The fallback, and only the fallback: a test run with no `DATABASE_URL`
pointing at Postgres, and a local boot. CI runs `test_smartforecast.py` and
`test_smartforecast_store.py` against **both**, because a backend no test
exercises is a backend nobody has checked.

## Render environment

Nothing new. The rows follow `DATABASE_URL`, which the service already sets.
`SMARTFORECAST_DB_PATH` names the legacy file for the import and the fallback
and **selects no backend** — the rule `hub/audit.py` arrived at one store
earlier, because a variable that is set on the live service and also chooses
the backend keeps production on the disk while every test passes on the new
path.

## Still on the disk after this

The disk block in `render.yaml` stays until the rest is moved: the remaining
JSON writers that do not go through `jsonstore`, the legacy SQLite files
`hub/extensions.legacy_databases()` reports, `modules/check_reconciliation`'s
hardcoded `/var/data`, and the assets fallback in `hub/storage.py`.
