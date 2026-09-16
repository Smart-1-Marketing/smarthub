## Getting a file back out is storage's job, not each module's

`hub/storage.attachment_url()` and `hub/storage.bundle_zip()`. Three modules
were solving this separately and a fourth was about to.

**A cross-origin `download` attribute does nothing.** Browsers ignore it, so an
`<a download href="https://res.cloudinary.com/…">` opens the image in a tab and
the button reads as broken. `fl_attachment` is what actually works — Cloudinary
sends `Content-Disposition` — and the name after the colon is what the file is
called on the way down. In the SEO Image Pipeline that is the whole point of
the tool: a file that lands in Downloads as `v1699_xk3.webp` has lost the work.
`attachment_url()` rewrites a Cloudinary delivery URL and returns anything else
unchanged rather than into something that 404s.

**More than one file is a zip, not a loop.** A browser blocks every download
after the first when they are triggered in sequence, so the person gets one
file, no error, and no reason to think anything went wrong. `bundle_zip()`
fetches each stored file, de-duplicates names (two images can genuinely share
one, and a zip silently keeps only the last), skips what it cannot fetch into a
`MISSING.txt` *and* returns that list so the page can say so too, and caps both
file count and total bytes — this streams through the Hub, and an unbounded
"select all" on a thousand-row archive is the one request that takes two
gunicorn workers down.

**A zip is delivery, and delivery is what Cloudinary bills.** A credit is a
gigabyte delivered, so `bundle_zip()` records the bytes it pulled rather than
the number of files — counting files would make a 40 KB thumbnail and a 4 MB
hero cost the same on the usage page. Single downloads redirect to the CDN
instead, so they cost the Hub nothing and are not counted here.

The SEO Image Pipeline's saved step and its project archive both offer download
of one or several, and the archive's row actions are icons — download, copy
URL, edit alt, delete — each carrying a rollover that says what it does and, for
delete, that it cannot be undone. An icon with no label is a guess.

**Every archive row needs an id.** The row's buttons all address it by one, and
the Image Optimizer's save path wrote rows without it: those images appeared in
the archive and then ignored every button on their row. `load_archive()`
backfills once on the first read that finds one missing.

**And a gallery drew the original into a box a fraction of its size.** Every
tile in this Hub was `<img src="{the full asset}">` — the client gallery's
64x48 row thumb, Client 360's 120px tiles, the picker's grid, the SEO archive.
A client uploads forty photographs off a phone and the staff gallery delivers
something like a hundred and sixty megabytes to draw forty small boxes.
Nothing errors and the pictures are right; the cost is a slow page and a
Cloudinary bill, which is charged in **credits, one of which is a gigabyte
delivered** — so this is the line item, not what we upload.
`hub/storage.thumb_url()` had been written for exactly this, docstring and
all — *"Galleries must never request the full asset"* — and had **no caller**,
the fourth declared-but-unwired integration point in this corner after
`RECORD_HOOK`, `io_creative` and `manifest()`. The one place the rule was
being applied was Google Drive, whose thumbnails are asked for at `sz=w400`,
because that one is not ours and somebody had to think about it.

`preview_url()` is the sibling that takes a stored delivery URL, and four
rules keep it from turning a working tile into a broken one. **Anything not
ours comes back unchanged** — a stock CDN, a Drive link, a `data:` URI — the
answer `attachment_url()` already gives, since rewriting a URL we do not own
produces a 404 where there was a picture. **Only an image**: Cloudinary keeps
images, raw files and video in separate namespaces, so an image
transformation on a PDF is "not found", the lesson `cloudinary_sink.destroy()`
paid for; the row's own `resource_type` is believed first and the URL's own
segment decides when the row says nothing. **Idempotent**, so a row rewritten
here and handed to a caller that rewrites again does not chain two. And
**`c_limit`, never `c_fill` or a bare width** — it caps without upscaling or
cropping, so a 180px logo stays 180px rather than being blown up and
re-encoded.

**One derived size for the whole Hub, and deliberately not one per box.**
Cloudinary bills a credit per thousand transformations and caches each
derivative separately, so a 64px row, a 120px tile and a 300px cell asked for
at their own sizes is three derivatives of every image in the account to save
bytes nobody would notice.

**It is derived on the row, never stored and never mirrored into JavaScript.**
`SavedImage.to_dict()` and `seo_images.load_archive()` add a `thumb` beside
the `url`, and `image_audit._read()` does it in the one funnel all six store
readers pass through — deriving it per reader is six chances for the seventh
store to draw full assets and for nobody to notice. Stored, it would outlive
the size it was computed at and be *restored* from the jsonstore mirror rather
than recomputed, the `client_key` rule. Written into the twelve templates that
draw a tile, it would be the drift `hub/storage.py` exists to stop. Each tile
reads `thumb || url`, so a row from a producer nothing has wired yet draws
exactly what it drew before.

**And the two places it must not happen are the deliverables.** The gallery's
copy button and the CSV export hand out `<img>` markup that goes onto the
client's own website; a 400px gallery thumbnail pasted there is the wrong file
on their page for ever. Those keep the original, as does the lightbox, whose
whole job is the full asset.

**What is still full-size is a table with a reason against each row**, not an
omission — a screen silently missing from a completeness report is the same
failure the report is about. Logos (small, one per page, as often observed off
the client's own site as stored by us), the just-uploaded strip (that URL comes
back from the Cloudinary widget in the browser and never passes through a row
here, so previewing it would mean a copy of the rule in JavaScript), and the
lightbox, whose whole job is the full asset. `test_image_download.py` fails on
a tile with no reason on file **and** on a reason whose line has gone, and it
started green.

**And that staleness half is what retired the one exemption that was wrong.**
The Display Ad Builder's background grid went into that table as *the one
gallery this cannot reach from Python* — the renderer is TypeScript and its
rows do not pass through `hub/storage.py`. They pass through
`hub/ad_builder_link.client_gallery()`, which is Python: the editor fetches
`/_hub/gallery` precisely because **the renderer does not know who our clients
are and must not learn**. So the preview is derived there, on the Hub side,
rather than mirrored into the renderer — and the check reported its own
exemption as stale the moment that was done, which is the whole reason it
carries markers from the lines themselves rather than file names.

**A tile and a picture are different things there.** The grid draws `thumb`;
`applyBackground()` and the magnifier read `url`, because a 400px preview
placed behind an ad is the wrong file in the creative, and "see it full size"
means what it says. Only the gallery source carries a preview at all — stock,
AI and a fresh upload pass none and fall back to the asset, so they draw
exactly what they drew before.

**And a fixture that does not look like the real thing leaves the rule
untested.** `test_ads_module.py` seeded rows as
`res.cloudinary.com/x/<id>.jpg` — no `/upload/` segment, so `preview_url()`
correctly declined to touch them and the first version of the assertion passed
against a preview that had never been computed. The fixture carries the real
delivery shape now. The assertion that went with it was worse: `all(row.get
("thumb"))` is true when `thumb` falls back to the asset, so it passed on the
bug as well — it requires the cap now, and reads every field with `.get()`,
because an assertion that raises on the missing field takes every check after
it out of the run.

`test_image_download.py` asserts all of it, including that the image picker
still returns a zip now that it runs on the shared builder.
