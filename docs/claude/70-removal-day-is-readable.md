# Removal day is readable

Two changes about the moment the Render disk is retired, rather than about any
store on it. One screen that would have lied, and one test that answers the
question the whole project was set to answer.

## The row that was about to raise a false alarm

`/api/status` — the System Status page, the one people refresh — carried this:

```python
add("Persistent disk", "ok" if os.path.isdir("/var/data") else "warn",
    "/var/data mounted — audit log & tokens survive deploys." if …
    else "/var/data not mounted — audit log and Google tokens are ephemeral.")
```

Both halves stopped being true while nobody was looking at that line. The
activity log moved to Postgres in `docs/claude/…` (the audit store), the Google
tokens in `docs/claude/63`, and the leads, forecasts, delivery receipts and
check-reconciliation state went the same way.

So the row credited the disk for durability the database was providing — and,
worse, the day somebody detached the disk it would have flipped to amber and
announced that the activity log and Google logins were now ephemeral. **A false
alarm on the most-watched screen, at the exact moment somebody is scanning for
damage.** Whoever was watching would have had every reason to revert a change
that had worked.

It asks a different question now, because a different one matters. Whether the
disk is mounted is a fact about the machine. Whether the **database is
answering** is what decides if anything written today survives. Green when the
database answers, whether or not the disk is there; amber only when it does
not, which is the state that actually costs something.

### Two details worth keeping

**It reads the directory in use, not `os.path.isdir("/var/data")`.** Those come
apart in the case that matters most. Pointing `HUB_DATA_DIR` off the disk is
how the disk gets retired *safely* — the service runs exactly as it would
disk-free while the disk stays mounted and untouched, so the change is
reversible by unsetting one variable. In that state the disk is mounted and
holds nothing, and a row saying "the mounted disk holds the mirror" would be
describing an empty directory. The first draft of this change did exactly that
and was caught by rendering it with `HUB_DATA_DIR` pointed elsewhere.

**It does not run the binary scan.** `jsonstore.disk_binary_writers()` takes
about 3.7 seconds — it walks every `.py` in the repository — and this is the
endpoint people refresh. `/api/db/structure` and `/api/integrity` already own
that question and render it on `/diagnostics`. A second copy here would be slow
*and* would be the two-panels-disagreeing trap those panels' own comments
record. The row points at `/diagnostics` instead.

## The criterion, as a gate

The disk work was given an acceptance criterion at the start, in these words:

> with HUB_DATA_DIR pointed at an empty temp dir on every boot, the full test
> suite passes and no saved data is lost across two restarts.

Every store that moved has its own test. Each proves its own half. **None of
them proved that sentence**, because it is a claim about the composed
application across a restart, and four suites passing separately is an
inference from four pieces rather than an answer. "Can the disk go?" was being
answered by reasoning.

`test_disk_free_restart.py` answers it mechanically. It boots the real app
twice, in two fresh interpreters, with a brand new empty data directory each
time and one database shared between them; writes an activity entry, a lead and
a mirrored JSON file in the first boot; reads all three back in the second.

### Why separate processes

The failure this guards against is a store that works because its rows are
still in a module-level cache, or an engine bound at import. In one process a
"restart" that is really a re-import proves nothing — the caches are warm and
the second read never touches the backend. Each boot is
`subprocess.run([sys.executable, …])`, so nothing but the database crosses the
gap.

The second directory is a fresh `mkdtemp`, not the first one emptied. Emptying
leaves the inode, the permissions and anything already open; a redeployed
container gets a new directory.

### It guards its own premise

Before trusting any read, the test asserts the second boot was handed a
directory that *started empty*. Without that, a read could be satisfied by a
file the first boot left behind and the test would pass while proving nothing
about the database. That assertion is measured with `os.listdir`, not assumed.

### Confirmed red

| Variant | What failed |
|---|---|
| the JSON mirror stops writing | the mirrored-file test |
| the activity log stops reaching the database | the activity-log test |
| `lead_store.store()` returns False | the lead test |
| the second boot reuses the first directory | the premise guard, and the two-directories test |

The third of those is worth recording because the **first attempt at it was a
no-op**: a `def store(...)` prepended above the real one, which Python simply
overrode with the later definition, so the variant ran the unbroken code and
the test passed. A confirm-red that does not actually break the code proves the
opposite of what it looks like. The variant mutates the real function's body
now.

## What this does not say

It does not say the disk can be detached. That is an operational decision with
a one-way door — a detached Render disk takes its contents with it. The safe
order is to point `HUB_DATA_DIR` off the disk first, leave the disk mounted and
untouched, watch `/status` and `/diagnostics` for a week, and only then detach.
The revert in that window is unsetting one variable, with every byte still in
place.

## Render environment

Nothing to add. The status row reads settings that already exist and the test
runs in CI against the Postgres service container already defined in
`checks.yml`. `render.yaml`'s disk block is untouched.

What this makes checkable is that `DATABASE_URL` has to be arriving — it is
what the amber state of the new row is about. Check the linked env group before
adding a service-level copy: a service-level value overrides the group's for
that service alone, and nothing reports the disagreement.
