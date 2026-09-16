## Another agency's photograph, captioned as the client's own premises

`hub/landing_images.py` picks the pictures on a landing page a prospect
reads. Its docstring names its best source — *"**The client's own site.** A
photo of their actual premises, van or team beats any stock library, and it
is the only source that is genuinely about them"* — and ends with the rule
the whole module is under: *"Stock photography … is never captioned as the
client's own work … a page may be short, it may not lie."* It was breaking
both halves.

**`from_site()` had no domain check at all.** It regexed every image URL out
of a 400 KB scan payload and labelled all of them `their site`. A scan
payload is 440 fields of whatever the crawler saw, so those URLs belong to
all sorts of people: against a realistic one, six pictures came back and
**five were somebody else's** — the scan vendor's own screenshot, a Facebook
social card, a Google static map, a Google ad creative, and **another
agency's Cloudinary folder**. Any of them could become the hero of a landing
page presented as the client's own premises. That is
`client_urls.NOT_A_WEBSITE` one module over, and it was not hypothetical
there either: on this deployment's own export *every single* click-thru
domain turned out to be a file host.

`theirs()` is the test, and it reads `client_context.canonical_domain()`
rather than comparing strings, so it cannot drift from every other join in
the Hub. A **subdomain counts** — `cdn.`, `www.` and `images.` are ordinarily
theirs — and a lookalike does not: `acme-tyre.com.evil.test` ends with the
domain and is refused, which is why the test is `endswith("." + domain)` and
not a containment. What is dropped is **counted** (`not_theirs`), because a
list that quietly gets shorter cannot be told from a site with no pictures on
it.

**A picture off their site carries no dimensions, and a missing size read as
a large one.** `pick()` asked `img.get("wide", True)`, so every unmeasured
picture qualified as a hero and `_MIN_HERO_WIDE` was skipped entirely for the
source this module prefers — a thumbnail off their page could be the
full-bleed band. It is `wide: None` now, *not measured*, and the test is `is
not False`: a stock image measured and found narrow is still skipped exactly
as before, and their own site still leads, which is this module's stated
order and the reason the old default read as harmless.

**And `source` described the search rather than the set.** It said `their
site` whenever the site search returned anything at all, however much of what
was actually picked came from a stock library. It reads the pictures that
were chosen now, and answers `their site and stock` where it is both — which
is the docstring's own rule, in one word. `test_landing_images.py` asserts
all of it.
