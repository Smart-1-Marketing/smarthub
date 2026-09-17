# The third question: bytes on the disk

Two checks already ask what is on the Render disk without a copy anywhere else.

- `jsonstore.unmirrored_json_writers()` — what JSON is written with no mirror.
  Its two blind spots are `docs/claude/58`.
- `jsonstore.disk_sqlite_stores()` — what opens a database file. Why that
  needed to be a separate question, because **a SQLite file is not JSON**, is
  `docs/claude/66`.

Both report none today. That reads as "the disk is clear", and it was not.

**A .webp is not JSON and is not a database either.** Between them the two
checks could not see a module that writes an image, a PDF or an MP3 — which is
most of what this suite actually produces for a client. `disk_binary_writers()`
is that third question.

## What it found

One store: `modules/page_image_optimizer/store.py`, at the `put_bytes()` on
line 86. Every other binary write in the repo is fine, and now says why.

The module's own docstring explains the disk:

> Deliberately on disk rather than in a module-level dict: the Hub runs more
> than one worker, and an in-memory batch would vanish the moment a save landed
> on a different worker than the scan.

That reasoning is right and stops one level short. A disk is shared by the two
gunicorn **workers** and is local to one **instance** — so the same sentence,
one word changed, is the defect: a scan on one instance and a save routed to
another finds no bytes and tells the person *"The optimized file expired before
saving"* about a file that did not expire. They lose the batch, including the
alt text and filenames they had just edited.

That is not happening today, and the reason is worth being exact about: a
Render service **with a disk attached cannot deploy zero-downtime**, because
the disk mounts to one instance at a time. There is never a second instance
while the disk is there. This is a store that has to move **before** the disk
can be dropped, not a bug someone is hitting now.

## Why a check and not a list

Because the list was written by hand three times while this was being scoped,
and was wrong all three times. It missed `hub/proposals.py`,
`modules/hvac/app.py`, `modules/landing_ads/app.py`,
`modules/radio_promo/app.py` and `modules/restaurant/app.py`. Both stores in
`docs/claude/66` were found by a person grepping rather than by anything on the
page. A list a person maintains is a list that is already stale.

## The exemptions are the substance

Sixteen files write bytes; fifteen are excused by name in
`DISK_BINARY_EXEMPT`, each with a reason that names what losing the file would
cost. They fall into four kinds:

| kind | why it is not a store | example |
|---|---|---|
| scratch inside a `tempfile` context | deleted when the function returns | `hub/image_compress.py`, handing bytes to pngquant |
| a cache | rebuildable from something that *is* kept | `modules/gpt_ads/app.py`, re-fetched from the stored URL |
| Cloudinary first, disk only on failure | Cloudinary holds it; the local copy is reached only when the upload could not happen, and is served by a route that module owns | `hub/proposals.py` → `/api/client/proposals/file/<name>` |
| a branch with no caller | nothing reaches it | `elevenlabs_service.generate_voiceover(out_path=…)` — both callers take `audio_bytes` |

`hub/storage.py` is exempt for its own reason: its fallback is the one write
here that deliberately hands back **no** URL (`docs/claude/68`), and
`local_assets()` already counts what is sitting in it on `/diagnostics`. It is
the reported thing rather than a hidden one.

The Cloudinary-first group is worth separating from the `hub/storage.py` defect
rather than lumping them together. Those five modules serve their fallback
files; `hub/storage.py` did not. What they still are is per-instance, which is
why each exemption names the route that would have to follow the bytes if this
service ever runs more than one instance.

## What counts as a write

`open(path, "wb")`, `Path.open("wb")` and `Path.write_bytes()` — the spellings
that reach a file.

Deliberately **not** `.save()`. That is Werkzeug's upload spelling, no call
site in this repo uses it for an upload, and counting every `.save(` would
report the thirty-odd `store.save(project)` calls that are JSON going through
the mirror. Same trade `_writes_json_to_disk` makes about `json.dumps`: thirty
findings nobody can act on is the same as none, one screen later.

`"b"` alone is not enough either — `"rb"` is a read, and a check that reported
every file this suite opens for reading would be noise.

## Read by AST, and the evidence for it

Same reason as both siblings: this module's own prose names the spelling, in
the docstring explaining why the check is an AST walk.

`test_jsonstore.py` runs the check alongside a deliberately crude second
reading — every non-comment line containing `"wb"` or `write_bytes(` — and
asserts the two differ on **exactly one** file, `hub/jsonstore.py`, and agree
everywhere else. A synthetic probe would show the same thing about a file
nobody ships; this shows it about real source. Swapping the walk for the
substring turns four checks red, that one among them.

The probe directory is the separate guard, and it is what stops an empty list
ever meaning "the scan broke": prose naming the spelling is not a write, `"rb"`
is not a write, and both a real `open(…, "wb")` and a real `write_bytes()` are
found.

Five broken variants were confirmed red before any of this was kept: the scan
neutered to return nothing, `"rb"` counted as a write, the AST walk swapped for
a substring, an exemption with a one-word reason, and an exemption for a file
that writes no bytes at all.

## Where it shows

`/api/integrity` gets `disk_binary` and `stale_binary_exemptions`, beside the
JSON and SQLite pairs. `/api/db/structure` gets a third row. Both read zero
before this change while a store sat on the disk — which is the whole failure
`docs/claude/58` records, one category along.

## Render environment

Nothing. This is a source scan; it reads no setting and reaches no service.
