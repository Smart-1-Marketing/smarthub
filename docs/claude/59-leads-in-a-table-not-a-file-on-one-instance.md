# Leads in a table, not a file on one instance

`hub/leads.py` is the one place every landing page and every calculator writes
to. The module exists because each app used to have its own GoHighLevel
webhook, each of which could be unset or silently failing, with the visitor
seeing a success message either way. So: every source writes here first, and
the row exists before anything is sent anywhere.

It kept those rows in an append-only JSONL on the Render disk.

## Two problems, and the disk is the smaller one

**The disk is outside the database backup and does not survive being
recreated.** Every lead the business has captured was in one file that nothing
copied anywhere. The check that is supposed to say so —
`jsonstore.unmirrored_json_writers()` — could not see this file at all, for the
two reasons written up in `58`. It reported a clean bill.

**And a file is local to one instance.** Two gunicorn workers today, and the
two halves of a zero-downtime deploy tomorrow. Each holds its own file, and
which one answers is whichever container the browser reached: a lead captured
on one is invisible on the other, and the panel that exists to answer "how
many leads did we get last week, and from which pages" gives a different
answer per refresh.

## The reason it is worth doing beyond the disk

A file has no way to change one row. So every mutation — marking converted,
tagging a temperature, recording a delivery attempt, the hourly retry sweep —
read the whole store, changed one dict and wrote the lot back.

`_rewrite()` carries the incident: a visitor fills in a landing page on worker
B while worker A is part-way through a retry sweep; B appends the row, A's
`os.replace` lands the file it read before that append, and **the lead is
gone, atomically and silently, with a 200 already in front of the visitor.**

The mitigation was to re-read inside the lock and keep any row the caller had
never seen, which is only safe because this store never deletes. An `UPDATE`
by id cannot take a concurrent `INSERT` with it, so the whole class is gone
rather than mitigated.

## What is in the table

`hub_leads`: append order as the primary key, the lead's own id unique beside
it, and the whole dict in `payload`.

The other way round is the obvious one and does not work. A generated column
autoincrements on **neither** backend unless it is the primary key — SQLite
gives a rowid only to a column declared exactly `INTEGER PRIMARY KEY`, and
SQLAlchemy attaches a sequence only to the primary key — so a `seq` sitting
beside a `String` primary key would be NOT NULL with no default and refuse
every insert.

Four columns sit beside the payload: `created`, `source`, `client`,
`delivered`. A column is there because a query needs it, not because the dict
has a key — `fields` and `meta` are open-ended by design and a column per key
would drift from what `capture()` builds the first time a landing page adds
one.

## A lead is never lost to a storage fault

`capture()` has promised since it was written that it never raises, and that
promise is the whole point of the module: the visitor sees a success message
whatever happens underneath. Where the database will not answer the row goes
to a pending file, and the next successful write flushes it — the arrangement
`hub/audit.py` arrived at one store earlier.

The flush runs **before** the new lead, not after. Written the other way round
the recovered leads take higher ids than one captured after them, and append
order — which is the order the panel shows and the order the retry sweep works
— reports the outage as having happened last.

It also runs unconditionally rather than when a counter says this process
spilled something. That counter is per process, and **the deploy is usually
what ends the outage** — so gating on it leaves the leads captured during the
outage sitting in the file, readable and off the backup, until somebody
happens to capture another one from that same worker. `flush_pending()` stats
the file and returns immediately when there is nothing there.

`/diagnostics` carries a lead-store row, because a store that degrades to a
file is invisible from every screen when it happens: correct, silent, and for
as long as nobody looks.

## "The database said no" is not "the database is gone"

A test written to check that a failed `replace_all` rolls back found
something else. The rollback was correct — the rows were all still there — and
the store was unreadable afterwards anyway.

Every error took the backend down for the full cooldown. A unique-constraint
violation means one caller passed something the table refused: the database
answered, it answered *no*. Treating that as an outage meant two minutes in
which every lead went to the pending file and every read fell back to the
legacy file, because somebody sent a duplicate id.

An `IntegrityError`, `DataError` or `ProgrammingError` is the statement's
fault and is recorded without standing the backend down. Anything else still
is — asserted in both directions, because a rule like this widens very easily
into never standing down at all.

## The import is verified per lead, not by counting

`import_legacy()` runs once across every instance — the check and the copy are
inside one `jsonstore.update_json()`, which holds the thread lock, the flock
and the Postgres advisory lock — and it is safe to run twice, because it is
retried whenever the last attempt did not verify.

Verification asks which legacy ids are **not** in the table. A count that
matches is a count, and this table already holds rows the file never had —
anything captured since the cutover — so arithmetic on totals would report a
clean import for a file whose leads were never read. "Could not ask" and
"nothing is missing" are different answers, and only the second one marks the
import done.

## What this changed in the tests

`test_lead_delivery.py` simulated the other worker by appending to
`HUB_LEADS_FILE`. On the table that writes to a file nothing reads, so the
mid-sweep check would have passed by testing nothing. It captures through the
store now, and the check that used to count `jsonstore.exclusive` calls on the
main path asserts the file **fallback** still takes the lock — that path is
still two workers sharing a file, and may not quietly lose it.

It also pinned only `HUB_LEADS_FILE`, so every invented lead went into
whatever database the environment pointed at — and on a shared one each run
read the run before it. It pins `DATABASE_URL` too now.

## Render environment

**Nothing to add.** `DATABASE_URL` has to reach the service, which
`render.yaml` wires from the Hub database and every other store already
depends on. `HUB_LEADS_FILE` names the legacy file for the import and the
fallback and **selects no backend** — the rule `hub/audit.py` arrived at and
`modules/smartforecast` repeated, because a variable that is set on the live
service and also chooses the backend keeps production on the disk while every
test passes on the new path.
