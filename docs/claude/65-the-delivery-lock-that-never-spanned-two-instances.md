# The delivery lock that never spanned two instances

`modules/io_builder/submission_attempts.py` kept two tables in a SQLite file
on the Render disk. They are not records anybody reads:

- **`attempts`** reserves an order before the external write, so a second
  request for the same order is refused rather than sent.
- **`receipts`** replays a completed response, so a retry returns the first
  answer instead of delivering again.

Together they are how a **duplicate opportunity is kept out of Smart 1
Suite**. This is a lock, not a store, and that changes what the move is for.

## What was actually wrong

`BEGIN IMMEDIATE` takes SQLite's **database-wide write lock on one file**.
That serialises the two gunicorn workers sharing that file and says nothing
whatever about a second instance — and the two halves of a zero-downtime
deploy are two instances, each with its own disk and its own file.

So the reservation that exists to stop a duplicate delivery **was not held
across the one event it most needed to be held across.** Losing the disk lost
the receipts too, which turns a later retry of a completed order back into a
delivery.

## What it is now

A transaction-scoped Postgres advisory lock keyed on the order:

```python
db.execute("SELECT pg_advisory_xact_lock(?)", (_order_key(order),))
```

Three properties, each of which matters:

- **It spans instances**, which the file lock never did.
- **It is released when the transaction ends**, including on a crash. The
  session-scoped `pg_advisory_lock` would survive the transaction and, on a
  pooled connection, leak onto whoever got that connection next — wedging an
  order until the process restarted. That is not theoretical: see below.
- **It is narrower.** The file lock was database-wide, so two reps sending two
  *different* orders queued behind each other for no reason. This one is per
  order.

`_order_key()` derives a signed 64-bit key with SHA-256 rather than calling
Postgres's `hashtext()`, which is internal with no compatibility promise — a
lock key that silently changed meaning between server versions would stop two
callers excluding each other while every screen looked fine.

The SQLite branch keeps `BEGIN IMMEDIATE`, which is still the right answer on
the fallback: one file, one process pool.

## Two statements that were SQLite's own spelling

`INSERT OR REPLACE` does not exist in Postgres. The reservation is written as
`ON CONFLICT (order_id) DO UPDATE`, the one form both dialects take, with the
columns named rather than relying on positional `VALUES`.

The receipt insert gained `ON CONFLICT (order_id, request_id) DO NOTHING`. A
duplicate there used to raise, get caught by the decorator's handler, and
answer **503 about a delivery that had in fact succeeded**.

## Why this needed a second test file

`test_io_delivery.py` drives the decorator end to end with real threads and
asserts the delivery function ran once. It is a good test and it **cannot see
the lock**: the reservation is committed before the delivery function runs, so
a second request there is refused by reading `state='pending'`, not by the
lock. The read-then-reserve window the lock actually protects is microseconds
wide, and a lock that had quietly stopped working would pass that suite every
time.

`test_io_delivery_lock.py` asks the database directly.

## The test hung, and fixing that was the interesting part

Written the obvious way — a holder thread and a *contender* thread that takes
the lock — it caught the session-scoped defect by **hanging**. The contender
blocked inside Postgres for ever on a lock that was never released, holding a
pooled connection, and a later test that took the lock blocked behind it. A
test that hangs on the defect it exists to catch is a CI job timeout nobody
can read.

Two changes fixed it, and both are now the rule in that file:

1. **Ask, never wait.** The contender uses `pg_try_advisory_xact_lock`, which
   answers immediately. Nothing in the file can block on a lock.
2. **Only the lock tests take the lock.** A test about the upsert statements
   was calling `_lock_order()` for no reason, which made it the thing that
   stalled when a leaked lock was in play.

All three broken variants now fail in under a second, naming what broke:

| Broken variant | What fails |
|---|---|
| session-scoped `pg_advisory_lock` | the lock outlived its transaction; the SQL is the wrong form |
| no lock at all | two callers held the same order at once |
| one key for every order | a different order waited on this one |

Before those two changes, the first of those took 200 seconds and reported
nothing.

## Render environment

**Nothing to add.** `DATABASE_URL` has to reach the service, which
`render.yaml` already wires from the Hub database. There is no variable for
this store — it had none before and has none now.
