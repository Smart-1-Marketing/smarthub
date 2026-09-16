## Except that one of the two CMSes has had a write API all along

The file before this one opens by saying neither CMS has a write API we can
use. That is true of Smart 1 Sites and it has **not** been true of WordPress
since 5.6: core has had `/wp-json/wp/v2/` since 4.7 and **Application
Passwords** since 5.6, so a client's blog posts and image alt text can be
written directly with nothing installed on their site. `hub/wordpress.py`
writes them and `hub/cms_credentials.py` holds what the calls are made with.
The Claude path is not replaced — it still covers Smart 1 Sites, which
genuinely has no content API, and it covers the two kinds this refuses.

**Blogs and alt text, fully. Schema and FAQs, not at all, and the refusal is
by name rather than by absence.** Yoast and Rank Math keep their fields in
postmeta that is not `show_in_rest`, and JSON-LD pasted into post content is
stripped by `wp_kses` for any user without `unfiltered_html`. So
`/api/seo/wordpress/publish` answers a `schema` or `faqs` kind with **what is
in the way and which button does work**, rather than with "unknown kind" — a
rep who has just used this for blogs will try it for schema, and an
unrecognised-value error is not an answer they can act on. The two routes that
would open it later are written down rather than left to be rediscovered: a
must-use plugin of ours registering one `show_in_rest` meta, or an
administrator credential on a single-site install. Neither is a decision this
change makes.

**Alt text is the easiest of the four kinds, not the hardest.** `alt_text` is
a first-class field on `/wp/v2/media/<id>`, so it is one PATCH per image —
where the blog path is three calls.

### The rules, each of which is a way this goes quietly wrong

**Everything lands as a draft.** `status: "draft"`, always, and nothing here
schedules. It is the same rule the Chrome prompt has carried since it was
written, and the argument is *stronger* over an API, because there is no human
approving each step. It is asserted on the payload rather than on the prose
promising it.

**A flagged post is refused by name.** `blog_spec.scan_forbidden()` reads the
finished copy against the client's own never-mention list and `p["flags"]` is
its evidence — usually there for a legal reason. Writing that copy unattended
to the client's live site is precisely what the flag exists to stop, so the
post is refused with the terms quoted rather than written as a draft somebody
might not read. `approve_render` refusing a mock render is the same shape.

**A pending image is not a featured image.** `hub/blog_images.py` holds
generated art at `pending` until somebody has looked at it, and approving is
the press that files it. Sending a pending one would put an unreviewed
generated storefront on the client's website.

**A second press updates the post it made.** The WordPress post id is recorded
back onto the post and the next publish addresses it. Two identical drafts on
a client's blog with no way to tell which is current is what `upsert_from_ghl`
learned from GoHighLevel first.

**A term matches exactly or is created; never the nearest hit.**
`?search=` is a `LIKE`, so asking for **Heating** answers *Heating & Cooling*
as well — and taking it files the post under a category nobody chose, the
`hub/client_key.py` substring rule wearing a taxonomy. Matching is on the
normalised name and nothing else; what is genuinely missing is created with
the exact name, bounded by `blog_spec.MAX_NEW_CATEGORIES_PER_POST` so a
client's structure cannot grow by surprise, and what the budget would not
create is **named** rather than dropped.

**An author who is not on the site is reported, never substituted.** The
Chrome prompt says this in words and the API keeps it: a wrong byline is worse
than ours plus a sentence saying so. Two users of one name resolve to neither.

**Alt text is a property of the attachment, not of the page.** One photo used
on three pages has one alt, so two pages asking for two different strings is a
conflict this cannot resolve — **named and refused**, because last-one-wins
changes a page nobody was looking at. An image that is not in the media
library at all (a theme asset, a page-builder background, a hotlink) is named
too: that is most of what a scan of a built page finds, and reporting it as a
failure would bury the ones that are real. `_find_media()` matches the **full
source URL** against the original and every generated size, because a built
page routinely carries `photo-1024x768.jpg` where the library holds
`photo.jpg` — and never the first search hit.

**The REST root is discovered, not composed.** `<origin>/wp-json/` is right on
most sites and wrong on one with plain permalinks, which answers at
`/?rest_route=/`. WordPress advertises the real address in a `Link` header and
in the page head, so both are read one hop at a time — `hub/llms_hosting.verify()`'s
arrangement — before anything is assumed. Plain http is refused **by name**
before a credential is sent, because WordPress itself refuses application
passwords over an unencrypted connection.

**A 401 has two meanings and they are different jobs.** `incorrect_password`
is a revoked or mistyped application password. `rest_not_logged_in` **while we
sent an Authorization header** is the host having stripped it — ordinary on
CGI/FastCGI, fixed with one `.htaccess` line, and indistinguishable from a bad
password to anybody reading the status code. Reported as the second one, it
sends somebody to rotate a credential that was fine: the
`services/provider_check.py` rule, one provider further out.

**Every item reports its own outcome, and the work is bounded on both axes.**
One number back hides the two that failed — `client_urls.accept_many()`'s
rule. These are HTTP calls to somebody else's server from a request thread, so
there is a count cap and a wall-clock budget, and what was not reached is
**counted and said**: a run that stops part-way and says nothing reads exactly
like one that finished.

**The button is hidden until a connection check has passed.** A control that
fails at the moment somebody is waiting is worse than one that is not there,
so *Send to WordPress* is drawn only where the credential is readable and its
probe came back ok. The probe itself is a **button, never a page load** — it
is one authenticated round trip, and what it answers changes when somebody
edits the site rather than when somebody opens a page.

### The credential, and what was there before it

**`setup.password` on the SEO record is stored in plain text.** `hub/seo.py`
strips it on read and `/api/seo/detail` only ever returns
`setup_has_password`, so the API surface was already careful — and the value
itself sits unencrypted in `data/seo/<client>.json` and, because that goes
through `hub/jsonstore.py`, is mirrored verbatim into Postgres and into every
database backup. That is the whole book of client website logins, and it is
named here rather than quietly fixed under this change: migrating it is its
own piece of work with its own test.

**What this adds does not repeat it.** `hub/cms_credentials.py` is its own
store — not the SEO blob, so it is not dragged into every SEO read, every
report and every AI prompt that touches that record — one file per client per
CMS (the `hub/drafts.py` rule), keyed on the client's **name** the way
`hub/seo.py` keys its own, and Fernet-sealed under `TOKEN_ENCRYPTION_KEY`, the
key four modules have used since Google Finder.

**It is an application password, never the site login, and that is a security
property rather than a preference.** It is minted per integration at Users →
Profile, it cannot be used to sign in to wp-admin interactively, and the
client revokes it from their own Users screen without changing anybody's
password or telling us. Storing the human's real login would give this Hub a
book of credentials that open every one of those sites to anything.

**Three states, kept apart.** Sealed and readable; **stored in the clear**,
because no key was configured when it was saved, which is said out loud on the
panel rather than passing as encrypted; and **cannot be decrypted**, because
the key has been rotated since — which is *never* reported as "no credential",
the failure `connected_accounts_result()` cost Google Finder months over. It
is a state with a fix, and the fix is naming the variable.

**Nothing returns the secret.** `state()` is what every route answers with and
it is **built as a subset** rather than by deleting a key, so there is nothing
for the next renderer to forget to omit — the rule `modules/scans` applies to
a client's audit. `get()` is reached only from `hub/wordpress.py`, on its way
into an `Authorization` header; no error message carries it, no activity row
carries it, and `test_wordpress_publish.py` asserts it over **every value**
`state()` returns rather than over the one name somebody remembered to strip.

**Disconnecting says what it did not do.** Removing the credential here leaves
the application password live in WordPress until the client revokes it, and a
disconnect that implies otherwise is the confident wrong answer. It goes
through `jsonstore.delete_json`, never `os.remove`, or the mirror restores it
and the disconnect undoes itself — which on a credential store is the worst
version of that bug.

`test_wordpress_publish.py` asserts all of it against a stubbed site, because
what is worth asserting is what this does with each answer a WordPress can
give. Every rule above was confirmed red against the defect it was written
for first.
