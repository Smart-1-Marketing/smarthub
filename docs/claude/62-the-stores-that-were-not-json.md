# The stores that were not JSON

`jsonstore.unmirrored_json_writers()` answers one question: what is written to
the Render disk with no copy in the database? `docs/claude/58` is the write-up
of the two blind spots that made it answer *nothing* while `hub/leads.py` held
every lead the business had captured.

Both were fixed, and the check now answers honestly. This is the third blind
spot, and it is not a bug in the check at all — it is the question's own edge.
**A SQLite file is not JSON.** A store that is a whole database was outside the
only check that asks what is on the disk, whatever it held, and no amount of
fixing the JSON check would have found it.

## What was behind it

| file | what it held |
|---|---|
| `modules/google_finder/app.py` | Google OAuth **refresh tokens**, in `google_tokens.db` |
| `modules/io_builder/submission_attempts.py` | the attempt and receipt tables that stop a retried Suite delivery creating a **second opportunity** against a real insertion order |

Both were found by somebody grepping. Neither was on any list, on any panel, or
in any audit. The structure panel read "one store left to move" with two whole
databases sitting beside it.

The first is being moved as its own change. The second is still there, and is
what the new check reports.

## Why it is `sqlite3.connect` and nothing else

A module that opens a database through SQLAlchemy is already answered:
`hub/client_context.py`'s `_engine_use()` reports who builds their own engine
instead of taking the shared one from `hub/extensions.py`. Adding
`create_engine` here would report those modules twice, and would flag
`hub/extensions.py` and `modules/reports/store.py` for their no-`DATABASE_URL`
fallbacks — which *are* the shared engine, not a second store.

Two checks disagreeing on one page is the defect recorded above
`SCAN_SKIP_DIRS`: `/api/integrity` and `/api/db/structure` each kept their own
copy of the unbacked-JSON rule and reached different answers on the same
Diagnostics screen. So this rule lives in `hub/jsonstore.py` and both callers
read it, like the one beside it.

`sqlite3.connect` is the spelling that reaches a file on the disk with no
engine, no pool and no mirror. That is the shape all three call sites in this
repo have.

## Read by AST, and the evidence for why

The same reason the JSON check is: the text `sqlite3.connect(` appears in this
module's own prose, in the docstring explaining why the check is an AST walk.

That is asserted rather than described. `test_jsonstore.py` runs the check
alongside a **deliberately crude second reading** — every file containing the
literal on a line that is not a comment — and asserts the two differ on exactly
one file, `hub/jsonstore.py`, and agree everywhere else. A synthetic probe would
have shown the same thing about a file nobody ships; this shows it about real
source. Swapping the AST walk for the substring turns four checks red, that one
among them.

## The one exemption

`modules/smartforecast/store.py` has a `sqlite3.connect`, and it is the *read*
side of the one-time import off the legacy file. The store moved to the shared
engine in `docs/claude/57`; that connect exists to empty the old file. Counting
it would report the migration as the thing to migrate.

It is exempt by name, with the reason checked against the file rather than
assumed — `stale_sqlite_exemptions()` is the other half, for the same reason
`stale_exemptions()` exists: a path left in the list after its file is deleted
goes on covering whatever is written there next, and the audit stays green
while doing it.

## The assertion that has to survive the fix

The panel's outstanding set is derived from the live scan, not transcribed:

```python
_left = len(js_scan.disk_sqlite_stores(REPO))
_want = [] if not _left else [("medium", f"{_left} modules open their own …")]
check("the panel names the work that is left, and nothing else", outstanding, _want)
```

Written as a literal it would go red the moment somebody moves a store —
punishing the fix. Written as `== []` it would go quiet the moment the scan
broke, which is the exact shape that let the lead store hide. This way the row
has to be there while the work is, and gone when it is done, and neither is
something a person retypes.

## Render environment

Nothing. This changes what a check can see, not what anything stores.
