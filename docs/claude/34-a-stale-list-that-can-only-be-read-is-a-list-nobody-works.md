## A stale list that can only be read is a list nobody works

`hub/stale_creative.py` says how long it has been since we last made creative
for each client running a product today, and every row was a fact with nothing
to do about it: a rep read the number, went and found the client somewhere else,
and the row aged another week. The end of the row is three actions now —
**Evergreen**, **New** and **Create** — each opening the thing that already
exists rather than a fourth copy of it. *New* is `/campaign-request.js`, the
same Campaign Change Request form Client 360 and the dashboard open, handed the
client's real insertion orders from `/api/c360` so the campaign/IO dropdown is
populated rather than a free-text box. *Create* is
`/tools/display-ads/_hub/start?client=…`, which fills the client in and files
the build against that record.

**The Source column went with it.** Which of our tools filed the last creative
is not a decision the person reading the row makes — the note
`modules/ads_builder/logo.py` already makes about naming Brandfetch to somebody
who cannot rotate its key. Each creative in the expanded panel still says where
it came from, which is where opening it is the point.

**Evergreen is the answer to a row that is not a gap.** An always-on brand
spot, a sponsorship board, a rebate banner that runs unchanged until the offer
ends: the creative is fixed for the campaign, so the elapsed time is not
something anybody is going to close, and left in the list those rows are
permanent red on a report whose whole job is to say what to act on this week.
`hub/creative_evergreen.py` is that overlay, and four rules hold it up.

**It is applied on every read of the cache, not baked into it.** The audit is
cached for five minutes and there are two gunicorn workers, so a mark taken in
one of them would go on being ignored by the other until its own cache expired —
a button that appears to do nothing, which is exactly the failure
`hub/client_urls.missing()` had to undo. **The mark is stored against the
client's name, never the derived match key**, for the reason `hub/client_key.py`
gives at length; the key is re-derived on read with whatever matcher the report
is using. **Nothing disappears in silence**: the row moves to an Evergreen
section with the group it came from, who marked it and when, and one press puts
it back — a list that quietly gets shorter cannot be told from a list that
failed to load, and the tile row carries the count beside the other five.
And **a mark says who and when**, because "this is evergreen" is somebody's
decision about a campaign and one nobody can attribute is one nobody can
revisit.

**The blueprint had no guard at all.** `/qa/stale-creative` and its APIs
answered 200 to anyone with the URL — every active client and how far behind we
are on each — because `wsgi.py` wraps only *dispatcher-mounted* modules in
`AuthGuard` and the hub app guards its own views one at a time. The gate sits on
the blueprint now rather than on each route, the arrangement
`modules/commercial_builder/__init__.py` arrived at for the same reason: the
write route added here must not have to remember, and neither must the next one.

**And it was reading two of its four sources.** `SOURCES` is a table of field
names for four modules this file does not own, and three of the four guessed
wrong — silently, each differently, on the report whose entire purpose is
*what have we made for this client*. **Image Picker** reflected over
`SavedImage` expecting Flask-SQLAlchemy's `.query`; it is a plain declarative
model with its own `session()`, so `_load_source` returned `[]` at the guard —
and that is the store `filing.file_asset` writes to, which is every asset every
tool files against a client. **Image Creator** asked for `created_at` /
`updated_at` where the index writes `created` / `updated`, so every row was
dropped by `if not when: continue`. **Commercial Builder** asked for
`client_name` where the row carries `client_id`, and that one is the worst of
the three because the rows were *not* empty: the source counted as **live**,
inflated `totals.creatives`, and every record was then dropped for having no
client. So a client whose gallery, canvas graphics and commercial were all
produced this month read as *"No creative on file"*, with two of the three
showing only as a footer line and the third showing as nothing at all.

**`hub/image_audit.py` reads exactly those stores and had them right the whole
time.** Two modules each guessing at one store's columns is the drift
`hub/storage.py` exists to stop, wearing a report — so the image sources are
not described here any more: `store` names one of its `STORES` entries and the
tuples read that reader's normalised shape. The reflection branch is gone with
them, because a table of column names for somebody else's model is what broke
this; a new source writes a reader, and `_commercial_rows()` is the example.
That one is **an approved render rather than a project row**: a cut nobody has
watched is not creative the client received, the distinction `approve_render`
already draws, and it resolves the name through `cb_clients` rather than
guessing at a column. `_thumb()` went with it — it was a second reading of
`hub/storage.preview_url()` that disagreed on both halves, `c_fill` where the
rule is `c_limit` and a second derived size Cloudinary caches and bills
separately, with neither of that function's guards, so a video URL was rewritten
as an image transformation.

**And `measured` covered one half of a join.** `_registry_clients()` tries four
paths and swallows each failure, so a client list that refused returned `[]` and
the audit reported **nought clients** while the creative sources answered
perfectly well — `measured: True`, and held as the day's answer. It is both
halves now, and each is named, because *the client list refused* and *no source
answered* send somebody to different places. The page draws it: `measured` was
on the payload for exactly this and **no template read it**, so a morning where
everything refused rendered the full six tiles and a "No creative on file"
section naming the whole book, with the only clue being *"Sources read: none."*
in the footer below everything. `test_stale_creative.py` seeds each store and
requires **every** entry in `SOURCES` to produce a record with a client, a date
and a title — a sweep, because a test naming the three that were wrong proves
nothing about the fourth.

**Four smaller ones, each the same shape: a rule computed and then dropped
on the way to the reader.** `campaign_assets.report()` put `labels()` on the
payload and `labels()` copied out the label and its source, throwing away the
`warning` `field_check()` had just computed — so the page's own
`(d.warnings||[])` loop read a key the report never wrote and was always
empty. Renumber `field_2346` and the report says *"No campaign in scope is
waiting on a clarification or an asset"* about the whole book, because
`measurable()` still passes on `clarification` alone; the one warning that
exists to make that visible stopped at the function that produced it.

`file_orphan()` ran **two vocabularies through one door**: the folder key from
`RECONCILE_KINDS` went through as the *provider*, and the kind was looked up in
`_KIND_FOR`, which is keyed on `STORES` names. Only `seo_images` is in both, so
eight of the nine fell through to `"upload"` — which `filing.SOURCE_LABELS`
calls **"Client upload"**. Attaching an orphaned commercial filed it in the
client's gallery as a file *the client sent us*, under a bare `commercials`
chip the gallery has no heading for, in the tier that claims nothing.
`_FOLDER_FILING` maps each folder to the `(kind, provider)` pair its own tool
files with, so a row this audit writes is indistinguishable from one the tool
wrote — which is the only way the gallery's grouping stays true. It also
surfaced a live one: `modules/social_planner` has filed under
`provider="social_request"` since it was written and the table never named it,
so a photograph a location manager sent in arrived as a bare key under no
heading and, unlisted, in the tier that claims nothing.

`io_records._summary()` stores the **shared** campaign start, and the wizard
clears it the moment one product is given its own term — *"Because at least
one product runs its own dates, I'll ask for dates product by product"*. So
`start` is `""` on every multi-product IO, and `io_reconcile`'s `started`
could never be true for one: the report's own headline urgency, *"it should be
running now and nothing is trafficked"*, silently blind to a whole class of
orders. `flight_start()` is the shared reading — the campaign start where
there is one, otherwise the earliest line's — and the record still stores the
agreement rather than the derivation.

And `check_orphan_templates()` **could not see the orphan in its own
templates folder**. Its include pass had no *"not its own name"* guard, the
one the bare-`.html` pass beside it has always had, so a template whose header
comment reads `drop {% include "me.html" %} into the dashboard` registered
itself as rendered. `_scorecard_stale_creative.html` did exactly that: written
for the dashboard, fetching a live and tested `/api/qa/stale-creative/scorecard`,
included by nothing, with the check reporting no orphans at all. The guard is
there now and the tile is wired where the partial's own first line asks for
it — above System status, the reason `hub/celebrations.py` gives, because a
key that is set is housekeeping and a client we have made nothing for is work.
