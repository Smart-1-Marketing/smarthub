# A client's asset home has five folders, and the uploads get a web-ready copy

Requested on Client 360's **Client Images** card, September 2026. Six things
were asked for at once and they are one feature: the card, the gallery it
opens, the upload link, the files our own team adds, what happens to an image
after it lands, and who is told.

## The card was the SEO pipeline's archive, and it should be the client's home

The Client Images card drew whatever the SEO Image Pipeline had saved for the
client and, when nothing had, said "Optimize their first batch". That is one
tool's slice of a client's assets presented as the whole, and the empty state
sent every new client to the optimizer whether or not that was the next thing
to do.

It draws the **asset home** now, from the same catalog the gallery page reads
(`modules/image_picker/catalog.py`): five section tiles with counts, the named
folders beneath them, and the pipeline's own archive behind a click. The five
are **Client Uploads, Creative, Hub Projects, Logos, Internal**, offered empty
or not -- the point of a fixed set is that the person adding a logo finds the
Logos folder waiting rather than inventing `logo`, `Logo` and `logos` across
three clients. `SECTIONS` in `catalog.py` is the one list; the card, the
gallery page and the upload panels all read it. The `help_dot` on the card
title came off; the entry stays in `hub/help.py` as `ask_only`.

The menu on the card is four actions in the order they are asked for: **Open
gallery**, **Client upload link**, **Optimize images** (a link to the pipeline,
not the card's body), **Add more images**.

## Logos and Internal are sections, not new kinds

`organize()` routes a row by what it is: kind `logo` (what `hub/client_logos.py`
files), a provider spelled `logo_*`, or **a folder named "Logos"** all land in
the Logos section under one folder called Logos -- `client_logos` files under
the label "Logo", an upload panel types "Logos", and two chips for one mark is
exactly the drift the fixed list exists to stop. Kind `internal` is a file our
team added from a Drive folder, a Dropbox or a desk; the share link can never
claim it, because the flag is read only from a staff session.

## A folder is chosen, on both pages

`docs/claude/40` argued the project box should be staff-only: "project" is our
word, and a client on their share link is sending photographs in rather than
filing them. That held until the request was specifically that a client
sending "the new logo versions" should be able to put them beside the logos
they sent last month. So `_upload_panel.html` offers a **folder chooser on both
pages**: the five sections, then every folder already in use with its count,
then "New folder…". `GET /api/folders` answers for a share token or a staff
session and reads the gallery's own rows and nothing else. What stays
staff-only is the duplicate question (keep / copy / move), which is a filing
decision about a file already here; the route still answers a client's link
with no choices.

## One script opens the widget

The widget-opening JavaScript lived inline in `_upload_panel.html` with five
Jinja-injected values. Client 360's **Add more images** needed the same widget
on a page this module does not render, so it moved to
`modules/image_picker/static/picker-upload.js` (served at
`/tools/image-picker/static/`), and `/api/upload-signature` now returns the
source tabs, the formats, the ceiling and the per-source keys beside the
signature. Three pages open one widget one way, and nothing has to be kept in
step with a second copy. The panel keeps the strip, the duplicate cards and
the ids the tests pin (`uploadDupes`, `uploadFolder`).

`addMoreImages()` on Client 360 creates the gallery if there is none -- a
press to add files to a client is somebody choosing to, not a page load --
then offers the folder chooser and opens the widget with `internal: true`.

## The original is kept; a web-ready copy is made later

`modules/image_picker/optimize.py`. Every image recorded through
`/api/uploads` gets one `image_picker_optimizations` row (its own table, for
the reason `ImageDescription` gives: a column added to `image_picker_images`
exists on every local SQLite run and is silently absent on the live Postgres).
`job_optimize_client_uploads` in `hub/scheduler.py` runs every five minutes
under the leader lock and works the backlog eight at a time inside a
two-minute wall clock: fetch the original, `hub.images.optimise()` to WebP
under the configured edge, `page_image_optimizer.naming.suggest()` for the
filename and alt (never raises, says whether the model or the fallback wrote
them), `storage.put()` under `<gallery folder>/optimized/<name>-<id>`.

The original row is never touched -- not re-encoded, not renamed. The copy
sits on the optimization row and the catalog attaches it to the original as
`optimized`, so the gallery draws one tile with an "SEO copy" link rather than
two tiles and a doubled count. Deleting the original destroys the copy's
Cloudinary object too (`optimize.forget`).

**Under load it waits.** There was no load signal anywhere in the scheduler;
the wall-clock budget was the whole protection. `busy()` reads the one-minute
load average against the core count (`LOAD_FACTOR = 1.5`) and the sweep
returns `{"deferred": True, "why": ...}` rather than running or silently doing
nothing, so the scheduler panel reads "deferred" and the Client 360 counter
reads "paused while busy". The Diagnostics "run now" passes `force=True`.

Failure gives up in writing after three attempts (`given_up`), a file the
copy makes no sense for is `skipped` at once (PDF, video, SVG, GIF), and
`progress()` carries `measured` so a store that cannot be read is "not
measured" on the counter, never "all done".

## Who is told

There is no mail sender in this Hub (`hub/scheduler.py` says so twice). Two
channels that exist, both used, in `modules/image_picker/notices.py`:

- **`hub/job_notify.py`**, the corner card that follows a person to whatever
  page they are on. One pointer per person per client per hour, re-registered
  under the same id as files land, so forty photographs are one card saying
  forty. A second one when the last SEO copy for a client is made.
- **The activity log**, read into the inbox bell by
  `hub/help_center.asset_notifications()` -- by *client*, not by actor, because
  a client uploading through their share link has no staff actor and the
  person who needs to know is whoever is attached to the account.

"Attached" is the owner (`client_owner.owner_of`), everybody following the
client (`followers_of`) and the Client Success contact Knack names
(`client_success_of`), deduplicated.

## A logo the Hub holds is in the gallery without a press

`file_logos()` was behind a button and ran after a paid Brandfetch lookup. It
takes `only_missing=True` now: a source that already has a logo row in the
gallery is not fetched again, so the automatic call Client 360 makes as the
brand card loads (`autoFileLogos`, `POST /api/client/logos {auto: true}`)
costs one query when nothing is new. The brand logos are no longer spliced
into the images card as tiles that exist on no record; they are filed, and
counted, in the Logos folder.

## The two questions, answered before the client sees them

`profile.autofill()`. When the client-facing pick link (or the staff picker)
opens a General Business gallery with nothing described yet, the Hub answers
"what kind of business is this" and "what do you sell" itself: the client
brief (`hub/client_brief.for_prompt`, the site scan and the record, every line
attributed) goes to the model with instructions to invent nothing, and the two
answers go through the existing `build()` -- which now takes a `context` of
industry, city, state and the confirmed audience, so the search terms relate
to the customer, the location and the industry rather than the category
alone. The attempt is marked on the row **before** the model is asked, so a
failure, or two workers opening the same link at once, does not spend a call
on every page load; "Change this" on the page is how to ask again. The page
says the words were worked out from their website, because a client reading
"your topics are built from marine upholstery" about words they never typed
should know where they came from.

## Tests

`test_master_gallery.py` (five sections at zero, Logos/Internal routing, the
folder index, the folders API scoped by token, the copy beside the original,
notices reaching everyone attached), `test_image_picker.py` (a client's folder
lands, the internal flag is staff-only, the queue and the sweep with the
storage patched, deferral under load, the autofill once and only once),
`test_client_images.py` (the card reads the client's own total and no longer
splices logos in).

## Render environment

Nothing to add. The sweep needs what uploads already need: `CLOUDINARY_URL`
(or the three-part spelling) for the copy to have a delivery URL, and
`OPENAI_API_KEY` for the model to name it -- without the key the copy is still
made and named from the client and the original filename, with `named_by:
fallback` on the row. `/status` already reports both.
