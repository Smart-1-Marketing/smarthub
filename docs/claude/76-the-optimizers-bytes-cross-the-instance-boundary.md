# The optimizer's bytes cross the instance boundary

The last store in this repository written to the Render disk with no copy
anywhere else. It is the one finding `jsonstore.disk_binary_writers()` reported
when that check was written (`docs/claude/69`), and with this change the check
reports none.

## What the bytes are

The Page Image Optimizer scans a page, downloads every candidate image,
optimizes each to a `.webp` and renders a `.preview` thumbnail. The person
reviews the batch — editing the AI-written alt text and filenames — and keeps
some, skipping the rest. What they keep goes to Cloudinary through
`archive.upload()`, into `smart1-seo-images`, and that does not change.

Between the scan and the save, the `.webp` and the `.preview` sat on the disk
with a 45-minute TTL.

## Why the disk was wrong, in the module's own words

`store.py` explains why the bytes are not in a module-level dict:

> Deliberately on disk rather than in a module-level dict: the Hub runs more
> than one worker, and an in-memory batch would vanish the moment a save landed
> on a different worker than the scan.

That reasoning is right and stops one level short. A disk is shared by the two
gunicorn **workers** and is local to one **instance** — so the same sentence,
one word changed, is the defect that was left. A scan on one instance and a
save routed to another finds nothing, and `api_save` answers *"The optimized
file expired before saving"* about a file that had not expired. The person
loses the batch and the editing they had just done.

It was not happening. A Render service with a disk attached cannot deploy
zero-downtime, because the disk mounts to one instance at a time, so there is
never a second instance while the disk is there. This is a store that had to
move **before** the disk could be dropped.

## Why the database, and not Cloudinary

CLAUDE.md's rule is that binary goes to Cloudinary through `hub/storage.py`, and
for an image somebody keeps that is still exactly right.

These are not that. They are a 45-minute scratch buffer holding **every**
candidate the scan produced, including all the ones the person is about to
skip. Uploading on the way in would put the rejects into the Cloudinary account
and bill for them. Todd chose the database for that reason.

`page_image_bytes` is `(job_id, name)` as a composite primary key, a `created`
timestamp for the TTL and a `LargeBinary`. The composite key is worth a note:
every other store moved in this project needed a generated `seq` and carries a
paragraph about the trap — a generated column autoincrements on *neither*
backend unless it is the primary key. This one is a keyed cache, `(job, name)`
*is* the identity, and there is no generated column to get wrong.

The write is `UPDATE`-then-`INSERT` rather than a dialect-specific
`ON CONFLICT`, which is `hub/jsonstore.py`'s spelling and behaves the same on
both backends. Re-running a batch writes the same key twice and the second must
replace rather than raise.

## The disk write stays, as the fallback

`put_bytes()` tries the database and falls back to the file. That is Fan
Radio's rule: a render that cost a download and real CPU is not thrown away
because a backend would not answer. The fallback is per-instance, which is the
behaviour it replaces rather than a regression — a database outage leaves this
tool exactly as good as it was and no worse.

`get_bytes()` reads the table first and the disk second, and the second read is
load-bearing **for one TTL after a deploy**: a batch the previous release
scanned has its bytes on the disk and in no table, and reading only the table
would answer "expired" about a batch somebody is still looking at — the same
message this change exists to stop.

## The exemption was rewritten, not removed

`#725` responded to the check's finding by adding
`modules/page_image_optimizer/store.py` to `DISK_BINARY_EXEMPT` rather than
moving the store. That entry was careful, and its Cloudinary half was right and
is kept. But it named the cross-instance cost and accepted it:

> a scan on one instance and a save on another reads as that same expiry
> message, which is why the two workers this service runs share a disk rather
> than a dict

The defect, stated plainly and left in place. The entry now describes what the
file actually does — a fallback reached only when the database refused — which
it has to, because `test_jsonstore.py` asserts that every exempted file really
does write bytes. An exemption here cannot outlive the write it describes.

## No migration

Nothing is carried across, deliberately. The bytes expire in 45 minutes and the
sweep runs on every scan, so whatever is on the disk at deploy time ages out on
its own within the hour. An import would move data that is about to be deleted.

## The test

`test_page_image_bytes.py`, and the assertion that matters is the one the disk
could never satisfy: **bytes written against one data directory are readable
against a different one**, with only the database in common. That is the second
instance, in the only form a test can have it.

It is guarded from passing for the wrong reason twice over. The first test
asserts the table is actually reachable, because every read below would
otherwise be answered by the disk fallback and the file would prove nothing.
And the cross-boundary test has a sibling that asserts the fresh directory
really was empty — without it, a shared `/tmp` could satisfy the read and the
test would look like it had proved something.

Five broken variants confirmed red before any of it was kept:

| Variant | What failed |
|---|---|
| `put_bytes` back to disk-only (the original defect) | 4, including both cross-boundary tests |
| `get_bytes` reads only the disk | 6 |
| the disk fallback deleted | the three fallback tests |
| the sweep ignores the cutoff | "leaves the rest" |
| `drop()` ignores the job id | "dropping one job took another job's bytes" |

This is also the module's **first test file**. It had none, and no step in
`checks.yml` — the scan, the optimize, the save and the archive hook were all
uncovered. This change covers the store; the rest of the module is still
unguarded and worth a separate pass.

## The routes, and what a second instance actually is

`test_page_image_routes.py` is the separate pass the paragraph above asked
for — the same claim one level up, at `api_save`, where a person stands.

It is not the same question. `store.get_bytes()` could be perfect while
`api_save` still failed, because the route reaches it through a job record, an
item id and a filename convention (`f"{item['id']}.webp"`) that the store test
never exercises. Three routes read bytes that way — `api_save`, `api_preview`
and `api_rename` — and `api_save` is the one that costs something, because the
person has edited filenames and alt text by the time they press it.

### The fixture got "a second instance" wrong, and it 404'd

Worth recording, because the first draft looked right and passed nothing.

`test_page_image_bytes.py` models a second instance by patching
`store.DATA_DIR` to a **different path**. That is correct *there*:
`bytes_store` is keyed on `(job_id, name)` and has no path in it at all.

At the route level the same trick fails, and not for the reason it looks like.
`DATA_DIR` comes from `HUB_DATA_DIR` — one configured value that every instance
shares — so a real second instance looks for the job at **exactly the same
path** and finds an empty disk there. Pointing the module somewhere else also
moves the `hub/jsonstore.py` mirror key, which is relative to the data root, so
the **job metadata** went missing too and `_job_or_404` answered 404 before the
route ever reached the bytes. A green run would have been meaningless and a red
one blamed the wrong thing.

What a redeployed container actually hands you is the same path with nothing in
it, so that is what the fixture does now: empty the directory, leave the path
alone. Both mirrors then have to answer in one request — the job record from
`jsonstore`, the bytes from `bytes_store` — which is a stronger test than the
first draft was trying to write.

### The confirm-red is in the suite, not just in the history

`TheHarnessCanSeeTheDefect` puts the module back the way it was — the table
refusing, the disk answering alone — and asserts the same request then fails
with *"The optimized file expired before saving."* So the boundary test is
permanently driven against something that really breaks, rather than against an
assertion that happens to hold.

Verified out of band the same way, by mutating the real body of
`bytes_store.get()` to return `None` — not a shadowing redefinition above it,
which is the trap that made an earlier variant run unbroken code and "pass".
Two tests go red, and the failure message is the production one.

`ExpiryStillReportsWhenBytesAreReallyGone` is the other direction: the fix must
not work by making the error unreachable, so a batch whose bytes are genuinely
swept still has to say so rather than uploading nothing and reporting success.

Still uncovered after this pass: `scan_page`, the optimize path, `naming`, and
the archive hook. Those are network, image decode and an AI call, which is why
they are a pass of their own rather than an afterthought here.

## Render environment

Nothing to add. The table is created through `hub/extensions.py` on the shared
engine, and `DATABASE_URL` is already wired to the Hub Postgres in
`render.yaml`. `PAGE_IMAGES_TTL_MINUTES` and `PAGE_IMAGES_DATA_DIR` keep their
existing meanings and defaults.

Where `DATABASE_URL` is not arriving, the bytes go to the disk exactly as they
did before, and `/status` says the database is not answering.
