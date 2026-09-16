## Schema needs one file on the site, and that is all it needs

`hub/wordpress_plugin/smart-1-hub.php`, `hub/wordpress.publish_schema()` and
`/api/seo/wordpress/plugin`. The file before this one wrote blogs and image
alt text over core's own REST API and named schema as the thing it could not
reach — Yoast and Rank Math keep their schema fields in postmeta that is not
`show_in_rest`, and JSON-LD pasted into post *content* is stripped by
`wp_kses` for any user without `unfiltered_html` — and it named two ways past
that: a plugin of ours, or authenticating as an administrator. This is the
first of the two, and the second is deliberately not built.

**The smaller of the two ways is the one worth having.** An administrator
credential needs nothing installed and writes the block into the page **body**,
which is a live page somebody at the client wrote; the plugin writes into the
**head** and never touches content. The plugin is more setup and less power,
and less power over a client's own pages is the point. It is also the one that
works on the ordinary Editor account these connections are made with, so the
credential does not have to be widened to make a feature work — which is the
direction this Hub tries never to move in.

**One meta key, and there was nearly a second.** `register_post_meta` for
`_s1hub_schema` on every public, in-REST post type, printed from `wp_head`.
An early draft registered a second key for FAQ markup and it is not there: the
accordion `hub/faq.py` produces carries its own FAQPage JSON-LD *inside* the
block that goes on the page, so writing it again from here would put two copies
on one page — and writing it without the accordion would be FAQPage markup for
questions no visitor can see, which is a structured-data violation rather than
a shortcut. A meta key nothing writes is the declared-and-never-wired failure
this guide counts a dozen of, so it is absent rather than reserved.

**So FAQs stay on the Claude path for a better reason than the one they were
refused for.** "REST cannot reach it" was never the real obstacle; the real
one is that the deliverable there is a visible accordion and placing it is an
edit to the page body a person makes. The refusal says that now, on the route
and on the panel, because a rep reading the old sentence would reasonably
conclude the plugin had fixed it.

### A 200 is not evidence the block landed

This is the whole of why the module is shaped the way it is. **Core drops an
unregistered meta key without complaining**: `POST wp/v2/pages/12` with a
`meta` object naming a key nothing registered answers **200**, the response
carries no such key, and nothing anywhere says the schema is not on the site.
A plugin that is missing, one older than this Hub, one that does not cover
that post type, and a site that rejected the JSON all look identical from
here — and all four look like a clean run.

So the value is **read back out of the response to the same request that wrote
it** and compared. That comparison is the difference between "WordPress
answered" and "the block is on the site", and it costs no extra call, because
core returns the registered meta on the write. Where it differs, the refusal
**names all three faults it could be** rather than picking the likeliest and
sending somebody to the wrong one.

It is also why `s1hub_sanitize()` stores a valid block **verbatim** rather than
re-encoding it. Canonicalising would change the bytes and break the comparison
for a block that was perfectly good; anything that is not valid JSON is stored
as the empty string instead, so the read-back disagrees and the Hub can say the
site rejected it. Keeping malformed JSON would put a broken script tag in the
head of a client's every page.

### A URL is resolved by WordPress, never guessed from a slug

Core's REST API has no resolver, so the alternative is `GET /wp/v2/pages?slug=`
— and a slug is unique neither across post types nor across a page hierarchy.
Filing one page's schema onto another page with the same slug is the silent
wrong answer `hub/client_key.py` refuses one join over. The plugin exposes
`url_to_postid()`, which is the site's own answer and knows the permalink
structure, the page hierarchy and custom post type rewrite rules — none of
which can be worked out from outside.

Four things fall out of it. **An address that is not a single post is refused
with the site's own reason** — archives, category listings, search results and
a blog-index home page have no post to attach schema to. **A static front page
is resolved from `page_on_front`**, because `url_to_postid()` answers 0 for the
home URL and without it the one page every site has would be the one page this
could not write to. **A post type the site does not serve over REST is named**
rather than written to. And **a post type the plugin does not register is named
before a write is attempted**, rather than discovered by a read-back that
failed for a reason the response cannot express.

### What the plugin may not do, written down rather than discovered

It never touches post content. It never publishes, schedules or unpublishes.
It adds no admin screen, no menu and no notice. Both REST routes are reads and
both require a user who can already edit posts — a resolver open to the world
is a site-structure disclosure nobody asked for. And **nothing is deleted on
deactivation or uninstall**: the schema is the client's own approved content,
and a plugin that empties a table because somebody toggled it off is one nobody
trusts.

**`<` is escaped to its `\u003c` JSON escape in the printed block**, which decodes to the same
character and is invisible to an HTML parser. Every `<` in valid JSON is inside
a string literal, so it cannot change what the block means — and without it a
`</script>` anywhere in the data ends the block early and the rest of the page
is parsed as markup. That is `renderProof`'s `jsonScript()` rule from the
display-ad builder, one language over. U+2028 and U+2029 go with it: both are
legal inside a JSON string and both are line terminators to a JavaScript
parser, so either one unescaped is a syntax error costing the whole block.

### Two ways to install it, because only one of them is always available

A **must-use plugin** cannot be deactivated by anybody, which is not ours to
decide on a client's site, and installing one needs SFTP or a file manager. The
**zip** needs neither: a webmaster uploads it under Plugins → Add New → Upload
and can turn it off again. Both are offered, the same file either way, built
from the one `.php` in this repo — `plugin_zip()` writes it at
`<slug>/<slug>.php` because WordPress requires the file inside a folder rather
than at the archive root. The download is staff-only and carries nothing
client-specific.

**The button is hidden until there is a connection *and* a plugin.** Blogs and
alt text are hidden until the connection check has passed, because a control
that fails at the moment somebody is waiting is worse than one that is not
there; schema is that case one step further on. A plugin state we could not
*read* leaves it hidden too, rather than drawing a button on a guess.

### Nothing unapproved reaches a client's live site

The Schema Builder's approval exists for exactly that, so `_schema_blockers()`
refuses an unapproved page **before a call is made** — the same shape as
`_post_blockers()` refusing an unwritten post and `_upload_featured()` refusing
a pending image. A publish path that ignored it would retire the feature.

**The added-to-site date is stamped by the write that landed, and only by it.**
That column is what somebody reads instead of going to look, so a date on a
page whose block never arrived is worse than an empty one. A refused page is
stamped with nothing.

### The wire contract is written twice, so it is asserted

The meta key and the REST namespace exist once in Python and once in PHP, and
a key spelled one way here and another there is a write that answers 200 and
stores nothing. `test_wordpress_schema.py` reads the PHP and holds the two
against each other, including the version in the plugin header against the one
it defines.

**And the rest of what that file used to assert about the plugin were greps,
which prove the source says something rather than that the code does it.**
`test_wordpress_plugin.php` runs the real functions against the smallest set of
WordPress stubs they actually touch, and the Python file drives it and asserts
every answer. It earned that immediately: the first run went red on the
U+2028 escape for a reason that turned out to be the harness's own needle, and
a grep would have reported both halves green. Where `php` is not installed it
**says so out loud** rather than reporting an unrun path as a clean run —
`test_image_pdf_optimizers.py`'s rule about Ghostscript, one language over —
and finding fewer answers than expected is a failure rather than a clean sweep,
because a harness that stops exercising the plugin reports the same green as
one that does.

### Two things reported rather than acted on

**An SEO plugin also emitting structured data.** Yoast and Rank Math ship their
own `@graph`, and two on one page is legal and is also how a page comes to
describe two different Organizations. Which is right is a judgement about this
client's site, so `plugin_status()` names what it found and the Hub prints it
beside every result rather than deciding.

**A plugin older than this Hub expects.** It still writes, so it is a warning
and not a refusal — the read-back is the real gate. What *is* named separately
is a plugin storing under a different key, because that one takes the write,
stores nothing under the name we read back, and would otherwise report every
page as rejected with no reason a rep could act on.

### And the same registration closes the last hand-copy step in the blog flow

The plugin was written for schema and the mechanism turned out to be wider
than that. `register_post_meta()` is **additive**: registering a key another
plugin owns exposes *that* key to core's REST API, and the value lands in the
field the plugin already reads. So the one field on a blog post core could not
reach -- the meta description -- is reachable after all.

It mattered because it was the last step of publishing a post that a person
still had to do twice. Every post went in with its description in the
**Excerpt**, and the panel said *"Yoast SEO does not accept one over the API,
so copy it across in WordPress."*

**Only the key of a plugin that is actually installed is registered.** Writing
`_yoast_wpseo_metadesc` on a site with no Yoast puts a row in the client's
postmeta that nothing will ever read -- and the Hub would report it as written,
because the read-back would agree with itself. `s1hub_description_keys()`
answers per site, and empty is a real answer.

**All in One SEO is named as out of reach rather than quietly skipped.** It
keeps its metadata in its own `wp_aioseo_posts` **table**, not in postmeta, so
there is no key to register and this route cannot reach it at all. Said in the
plugin, on the panel and in the row, so the next person to wonder does not find
out by trying.

**The Excerpt is still written, and that is deliberate.** It was only ever a
*carrier* for the description, and it is also a real field a theme prints under
the title on a blog index -- so dropping it now that the description has
somewhere better to go would change how the client's own blog index renders,
which nobody asked for.

**A long description is reported, never cut.** Google truncates a snippet
around 160 characters. That is a display limit and not a rejection: nothing
refuses a longer one, and shortening the client's approved copy to fit
somebody else's snippet is the clamp `config.music_length_ms()` refuses one
provider over. The row says how long it is and that it went in as approved.

**No `sanitize_callback` of ours on that key.** It is the SEO plugin's field
and its own filters still run on the way in. What this must not do is impose a
length or strip a character the plugin would have kept, because that is
silently editing copy the client approved.

**And none of it may cost somebody a blog post.** `publish_posts()` asks the
plugin once per run, inside a `try` -- the description is an improvement on
that path, not a precondition for it, so a site with no plugin, or one that
answered oddly, publishes exactly as it did before and falls back to the
Excerpt. `plugin_status()` catches a `Refused`; the guard is there for
everything else, and `test_wordpress_publish.py` drives a status route that
raises something else to prove the post still goes in.

The read-back rule is the same one, for the same reason: core drops an
unregistered meta key without complaining, which on this path is an older
plugin on the site. The row says whose field took it, or that the field would
not take it and what to do about it.

### Stored is not the same claim as on the page

`publish_schema()` reads the value back out of the write, which proves
**WordPress stored it**. That is not the same claim as a visitor seeing it,
and three ordinary things break the second without touching the first: the
plugin deactivated, a **page cache** still serving HTML from before the write,
and a theme that never calls `wp_head()`. All three leave the Hub saying the
schema is on the site, with an added-to-site date stamped against it, and
nothing anywhere disagreeing -- the confident wrong answer the read-back was
written to stop, one hop further out.

`verify_schema()` is that hop, the one `hub/llms_hosting.verify()` already
makes one tool over: fetch the page and report what came back. **Signed out,
deliberately** -- the question is what a visitor and therefore a crawler sees,
and a logged-in request would both ask a different question and bypass most
page caches, which is exactly the fault being looked for.

Four verdicts. **live** is our block, unchanged. **stale** is our block and
*different*, named as a difference rather than as either cause, because the
page cannot say which -- a cache is the usual one and somebody editing the
field in WordPress is the other. **absent** is the page loading without it,
and the note names all three causes rather than picking one, because nothing
from outside can tell them apart. **not_measured** is a page we could not
fetch, a 404 (a draft is not public, and nothing on it is visible to a crawler
either), a non-HTML response, or nothing stored to compare against -- never a
verdict about the page.

**Somebody else's JSON-LD does not count as ours.** Yoast ships its own
`@graph`, so the match is anchored on `SCHEMA_MARKER` -- the comment the
plugin prints its block behind -- and not on any `application/ld+json` on the
page. Without that, every Yoast site would read as live whatever our plugin
was doing.

**And a block cut short by its own data is its own finding.** Unreadable JSON
after our marker is not a stale cache: the plugin escapes `<` precisely so a
`</script>` in the data cannot end the block early, so what is on that page
was written by something that does not -- an older plugin, or an optimizer
rewriting the head. The test fixture renders the escape for the same reason,
and it earned it: `BLOCK` in that file carries a literal `</script>` in its
description, so an unescaped fixture builds markup the plugin would never emit
and tests the verifier against a page that cannot exist.
