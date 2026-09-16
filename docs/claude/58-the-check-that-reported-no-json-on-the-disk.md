# The check that reported no JSON on the disk

`jsonstore.unmirrored_json_writers()` exists to answer one question: what is
written to the Render disk with no copy in the database? Getting the service
off that disk depends on its answer.

It answered **nothing**. `hub/leads.py` was holding every lead the business
had captured in an append-only JSONL on that disk, mirrored by nothing.

## Two blind spots, and the lead store sat behind both

```python
if "json.dump(" not in src or "jsonstore" in src:
    continue
```

**The pattern was the literal `json.dump(`.** An append-only store writes
`fh.write(json.dumps(row) + "\n")` — `dumps`, not `dump` — so it never
matched. That is how `hub/leads.py`, `modules/check_reconciliation/app.py`
and `modules/suite_panel/app.py` were all written.

**Any file whose source contained the word "jsonstore" was skipped.** Import
it for `data_root()` or `exclusive()` and you bought an exemption from the
check about what you then did with the path. Eleven files were skipped this
way, `hub/leads.py` and the Google OAuth token store among them.

The list had already been bitten by the second one once: two scanners were
exempting themselves because their own explanatory prose contained the word,
and the fix at the time named those two files outright and left the wording
rule in place for everyone else. **Prose is not a call site** — and the way
to enforce that is not to name the files whose prose we noticed.

## What it is now

Both halves read by AST rather than substring, so a comment naming
`json.dump(` is a comment:

- **Writes JSON to the disk** — a `json.dump()` call, or a `json.dumps()`
  whose result reaches a `write`/`write_text`/`writelines`/`writestr`,
  including the two-step spelling (`text = json.dumps(row)` then
  `fh.write(text)`). Nothing writes that way today except the mirror itself;
  it is covered because the next store written that way would otherwise be
  invisible, which is the failure being fixed.
- **Goes through the mirror** — an actual call to `write_json`,
  `update_json`, `delete_json` or `file_asset`. Not the word.

The containment rule matters as much as the pattern. Requiring only that a
file contain both a `json.dumps` and some `.write(` reported **37** files:
`json.dumps` builds request bodies and database column values throughout this
repo. A check that reports thirty-odd findings nobody can act on is a check
reporting none, one screen further along.

## What it found

Six. Three are exempt, each by name and with a reason that says what losing
the file costs:

| File | Why it is exempt |
|---|---|
| `modules/io_builder/app.py` | a `/tmp` fallback for the order number, reached only when the Postgres sequence is unreachable; the sequence is the store, and a per-instance file is the right shape for a degraded mode |
| `modules/suite_panel/app.py` | idempotency markers with a TTL: losing one lets a retried request run twice inside the window, and mirroring a value that expires would keep it past its expiry |
| `…/elevenlabs_audio_service.py` | an audio cache keyed by content digest; a lost entry is re-synthesised, which costs credits rather than data |

Three are real, and are the remaining work:

- **`hub/leads.py`** — every captured lead, append-only JSONL. A lead lost is
  revenue lost, and on a two-instance deploy each instance holds different
  ones.
- **`modules/check_reconciliation/app.py`** — reconciliation state under its
  own `/var/data/check-reconciliation` root, with its own environment
  variable rather than `hub/config.py`. **Moved.** See below.
- **`modules/seo_intelligence/file_store.py`** — per-client JSON written raw
  under `data_root()`. **Resolved, and not by moving it.** See below.

## One of the three was not a store to move

`modules/seo_intelligence/file_store.py` looked like another store on a disk
nobody backs up. It is not. `context._memory()` reads that file **only where
the `SEOMemory` query raised** — it is the offline copy of a database row, for
the one case where the database cannot answer. Putting it in that same
database would leave the fallback needing the thing it is a fallback for.

So it goes through `jsonstore.write_json(..., durable=False)` — which is what
this check's own fix text says to do with something rebuildable: *"If the file
is genuinely rebuildable, pass `durable=False` and say why."* It is now a
**declared** cache listed on `status()` beside everything else deliberately
not backed up, rather than a raw write a scanner has to guess about. Losing it
costs one weekly regeneration from the row it was built from.

Deliberately not an entry in `UNMIRRORED_EXEMPT`. An exemption says "this
check does not apply here"; `durable=False` says "this is a cache, and here it
is on the status page". The second is the true statement, and it is the one
that survives somebody re-reading the list.

**It kept its `fsync`.** The hand-rolled write flushed to the platter before
the rename, and `_atomic_write`'s own docstring says every module that
hand-rolled this got it right and it is preserved here so none of them lose it
by moving over — so the shared writer takes an `fsync` argument rather than
quietly trading one away. Off by default, because it costs a real disk round
trip and most callers here write something a moment's work rebuilds.

## The last one, and the lock it never had

`modules/check_reconciliation/app.py` held four things in one JSON file under
a hard-coded `/var/data/check-reconciliation`: the **QuickBooks OAuth tokens**,
every payer alias the tool has learned, the reconciliation records, and the
audit trail. Losing the file means re-authorising QuickBooks and losing every
alias.

It is `jsonstore` now — `read_json` for the reads and `update_json` for the
read-modify-write — and the root comes from `data_dir()`, resolved on each
call rather than captured at import. The constant it replaces named
`/var/data` outright, so on a deployment with `HUB_DATA_DIR` set it was
writing somewhere nothing else reads.

**The lock is the part that was not just about backups.** `_LOCK` is a
`threading.RLock`: it serialises the threads inside one gunicorn worker and
says nothing whatever about the other one, and this deployment runs two. Two
reps pressing approve at the same moment on different workers each read the
state, each changed their copy and each wrote the lot back — and the second
write silently dropped the first allocation. That is a QuickBooks payment that
was made and is no longer recorded as made. `update_json()` holds the thread
lock, an flock across the workers and a Postgres advisory lock across
instances, and does the read and the write inside all three.

`_write_state()` went with it. It had exactly one caller, and leaving it would
have left a second door onto the file that takes none of those locks.

**Taken and not-lost are different claims, and only one of them needs two
processes.** The first check written for that lock patches `jsonstore._exclusive`
and counts the call — which is right, and proves the lock is *entered*. It
cannot prove it *serialises*: the wrong scope, a lock per process, or a read
that happened before it was held all count exactly one. Neutering `_take_flock`
demonstrates the gap — the counting check stays green and one of the two
writes silently goes.

So the property is asserted directly, with two real processes that each read,
wait inside the mutation and write. Threads cannot show it, because `_LOCK`
serialises them and every assertion passes while two containers overwrite each
other — the measurement `hub/leads.py` records as 30 of 60 leads surviving.
The check was confirmed red against a neutered flock before it was confirmed
green.

**Not moved:** `uploads/`, which holds the scanned check images. Those are
binary, and the repo's answer for binary is Cloudinary through
`hub/storage.py` — a different change from moving the JSON writers, and one
that deserves its own decision rather than being folded in here.

## The assertion that had to change with it

`test_jsonstore.py` asserted `nothing above low is outstanding` on the
structure panel, and passed — because the check behind that row could not see
the lead store. The panel was reporting a clean bill.

It now asserts the outstanding set **by name**. An empty-set assertion on a
"what work is left" panel turns *find the remaining work* into *stop
reporting it*, which is the pressure that produced the hole. An amber row
naming real work is what the panel is for.

## Render environment

Nothing. This changes what a check can see, not what anything stores.
