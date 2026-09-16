## A featured image named after a title two posts share

`hub/blog_images.py` generates the image every blog post needs before it can
be published, holds it `pending` until a person looks at it, and files the
approved one into the client's gallery. It named the Cloudinary object after
the post's **title**, with `overwrite=True` and `unique_filename=False` — and
a title is chosen by a model and is not unique. That is not a coincidence to
guard against: `hub/seo.py` tops a short plan up from a list of **six**
fallback titles and **cycles** it, so a client on twelve posts a month gets
each of those titles twice, verbatim, in one plan. Both posts then generate
into one object. **Post 3's featured image becomes post 9's picture** — at
the same URL, in the store, in the client's gallery and on their live site —
and approving the second overwrote the first's approved, filed copy as well.
A long title reached the same collision through the 60-character truncation.
Nothing errors at any point: two posts, one perfectly good photograph.

The post id is unique by construction and was already being written into the
upload context, so `image_name()` puts it in the name. **Nothing is re-keyed**
— every existing row carries its own `public_id` and `_promote()` and
`_file_in_gallery()` read that rather than deriving one, so a post with no id
falls back to exactly the old spelling: the rule `audit.LOG_NAMES` and
`video_library.TAG_ALIASES` already work to.

**A badge counting posts the list no longer shows.** `/api/seo/blogs` filters
`archived` out of the working list and says so in a comment directly above the
filter; `status()` did not, and its number is drawn as a badge on a Blogs
section that is **collapsed by default** — the one signal that says somebody
needs to look at these. Archiving a post with a pending image left *"1 image
to approve"* above a table with no row to click, amber for ever with nothing
anywhere to clear it: two readings of which posts are in play, disagreeing on
one screen, which is the `/api/db/structure` versus `/api/integrity` trap
wearing a badge. What leaves the badge is **counted rather than dropped** —
that post's file is still sitting in `pending/` and nobody is going to approve
it — because a badge that quietly gets shorter cannot be told from one that
failed to load.

**A 3 MB hero, filed in silence.** `_optimise_bytes()` returns nothing at all
when Pillow cannot read the bytes and `staged or raw` fell back to the
original, which is right — an image nobody can shrink is still the image.
Saying nothing was not: this module's own docstring calls a 3 MB PNG *"a Core
Web Vitals problem on the very page the post was written to rank"*, and one
went into the client's gallery with `bytes` recording 3 MB and every screen
reporting a clean success. `optimised` is on the record now and the note names
the size, because that is the one number on it somebody would act on.

**And the pending folder's whole purpose was undone by the audit.** Pending
images live in `seo_images/<client>/Blogs/pending/` *"so an unapproved image is
never mistaken for a finished asset by anything browsing the gallery"* — and
`hub/image_audit.reconcile()` lists that tree by prefix like any other, while
no store it reads had a row for what is in it. So an unapproved image read as
an **orphan**, on QA → Unattached Images, with a client picker beside it: one
press files the six-fingered plumber into the client's gallery labelled *"SEO
images"*. The approved half was safe only by accident, because `file_asset()`
had already given it a row. `image_audit.STORES` has a reader for the SEO
stores now, so the audit is told the store has a row rather than the folder
being quietly skipped — a folder silently left out of a completeness report is
the same failure the report is about.

**And `gallery_folder` was assigned after the save.** `img` is a reference
into `store`, so writing to it once `save_store()` had run left the value in
memory alone: it reached the browser and the next read of the record had never
heard of it. It saves twice now — once before the gallery write, because a
gallery that is unavailable must not cost somebody an approval, and once after
when there is something new to keep.

**`_optimise_and_store()` is deleted rather than left standing.** Sixty-nine
lines implementing resize-then-convert-then-file, written when approval was
meant to be the step that optimised; nothing has called it since that work
moved into `generate()`, and the module's docstring still described its path.
`test_unwired.py` could never have said so — it skips names beginning with an
underscore, because a private helper called from inside its own module is the
ordinary case. `test_blog_images.py` asserts all of it.
