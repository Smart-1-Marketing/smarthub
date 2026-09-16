# The Google tokens, off their own SQLite file

`modules/google_finder` kept four tables in a SQLite file on the Render disk:
`google_accounts`, `saved_reports`, `report_alerts` and `gtm_change_logs`. One
of them holds **Google OAuth refresh tokens**.

The disk is outside the database backup and does not survive being recreated,
so losing that file means every connected account has to reconnect — with
nothing on any screen saying why. And a file is local to one instance, so the
two halves of a zero-downtime deploy each kept their own set of accounts.

## The shim moved to `hub/` because a second caller asked for it

`modules/smartforecast/db.py` was written for that module's move: a
`sqlite3`-shaped API over the shared engine, so 115 statements did not have to
be rewritten. Google Finder needed exactly the same thing, and the choice was
to copy it or to share it. **A second copy of a translation layer is the drift
`hub/jsonstore.py`, `hub/storage.py` and `hub/images.py` all exist to stop.**

So it is `hub/dbshim.py` now, with the table-dependent functions taking the
table list as an argument. `modules/smartforecast/db.py` is a thin binding
that supplies its own list and re-exports the rest, so nothing that imports it
changed — proven by its 43 + 32 checks passing unaltered on both backends.

One thing came out in the move: `verify()` took a `tables` argument it never
read. It iterated the counts it was given. Gone rather than shipped.

## `flask.g` went with the file, and so did the hazard

The old `_db()` carried a careful `has_app_context() and current_app is app`
dance around a `g`-cached sqlite handle, and a long comment explaining the
incident behind it: the account table is read from two places that are not
routes — the scheduler's index sweep, in a background thread with no
application context, and `/api/google/rebuild` under the *hub* app's context.
Outside a context every read raised inside `connected_accounts()`'s except and
came back as an empty list, so **the sweep announced "No Google accounts are
connected" every three hours while the accounts sat in the table untouched.**

A pooled engine has no per-request handle to cache. The dance is gone because
there is nothing left to get wrong, and the incident is kept in the docstring
because the *reason* is still worth knowing.

## The schema is created under the advisory lock

`init_db()` is `CREATE TABLE IF NOT EXISTS` written as SQL, which was fine
against a SQLite file one process owned. On the shared database it is the
concurrent-DDL race `hub/extensions.py` already documents: IF NOT EXISTS is
**not atomic** against a second worker running it at the same moment, and on
Postgres that is a duplicate key on `pg_type_typname_nsp_index` — a stack
trace in the deploy log on every single deploy, which is how a deploy log
becomes one nobody reads.

`create_all_metadata()` covers a declarative `Base`. There was nothing for DDL
written as SQL, so `extensions.create_all_sql(run, retry=False)` is the same
advisory lock and the same benign-race handling for a `run` callable.
`retry=False` because this is reached from a request rather than from boot, so
the boot backoff would otherwise be spent inside somebody's page load — the
reason `hub/jsonstore.py` passes the same.

It returns the error rather than raising, so a caller that ignores the return
value gets a module that looks fine and has no tables. This one raises.

## The tokens move as ciphertext

`refresh_token_enc` is copied column to column and never passes through
`_fernet()`. Decrypting to re-encrypt would put every connected account's
Google refresh token in the process's memory for no purpose, and would fail
outright on a deployment whose `TOKEN_ENCRYPTION_KEY` had been rotated —
turning a migration into a mass disconnect. The ciphertext is opaque to the
copy, and that is the point.

**The test for this was vacuous when first written.** It rotated
`os.environ["TOKEN_ENCRYPTION_KEY"]`, but `_fernet()` reads the module
attribute, which is bound at import — so the environment rotated nothing and
the check passed over a deliberately broken copy that decrypted every token.
It patches `gf.TOKEN_ENCRYPTION_KEY` now, and was confirmed red against that
same broken copy. Found by breaking the code on purpose and watching the test
go on passing, which is the only way that kind of test is ever found.

## Ids are preserved, and the sequences move with them

`report_alerts.report_id` is a foreign key to `saved_reports.id`, so the copy
keeps the ids — a copy that let them be regenerated would have to rewrite
every reference, and getting one wrong points an alert at another customer's
report.

Which walks straight into the trap this codebase has now hit three times: **a
generated-id sequence does not advance on an explicit id.** After the copy
every Postgres sequence still sits at 1, so the first report anybody saves
collides with a preserved id.

Verification asks both questions, because one is not enough. Driven with the
sequence fix removed, on Postgres:

```
verify : {'ok': False, 'short': {}, 'sequences_behind': ['report_alerts', 'saved_reports']}
next id: 1          # against a preserved id of 7
```

**Every row count matched.** Counting alone would have called that a clean
import and left the failure to be found by whoever saved the next report.

## The marker is mirrored, which a test has to know

The "already imported" marker goes through `jsonstore.update_json()` — which
is what makes the import run once across every instance rather than once per
worker. It is therefore **mirrored into the shared database, keyed relative to
the data root**, so a fresh temp directory does not give a fresh marker: the
second run of a test reads "already imported" about tables that were just
dropped. `_fresh()` clears it explicitly. Found by probing this by hand and
getting a confusing pair of results.

## Render environment

**Nothing to add.** `DATABASE_URL` has to reach the service, which
`render.yaml` already wires from the Hub database. `TOKEN_DB_PATH` names the
legacy file for the one-time import and **selects no backend**; unset, there
is simply nothing to import. `TOKEN_ENCRYPTION_KEY` is unchanged and still
required — it is what decrypts the tokens, and it is bound at import, so
rotating it needs a restart.
