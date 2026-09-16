## Alt text is read from the site, not invented for it

`hub/alt_text.py`. The Schema Builder and the FAQ Builder both read a client's
own pages and hand the result to a CMS; alt text was the gap, and it is the
finding an audit reports most often because fixing it by hand means opening
every page and writing a sentence per image.

**The first five sitemap pages, by default.** A crawl is one request per page
against somebody else's server, and a 200-page site is 200 requests before a
word is written. Five is the home page plus the top-level service pages on
almost every site we build. The limit is a parameter so a second pass can go
deeper deliberately, rather than a default that hammers a client's host.

**`alt` absent and `alt=""` are different answers.** An empty alt is a decision
— this image is decorative — and a missing one is an omission. Report both as
`""` and every genuinely missing alt hides inside a list of images that were
already handled correctly, which is exactly the number the audit is counting.

**A decorative image keeps its empty alt.** The whole rewrite path exists to
fill in blanks, so the one case where blank is *correct* has to survive it: a
1px spacer described as "air conditioning repair in Dublin" is worse than the
spacer with no alt at all. `is_decorative()` reads `role="presentation"`, the
filename hints a builder emits, and a tiny declared size.

**Three of the writing rules are enforced, not requested.** Length (both
engines and every screen reader truncate around 125 characters), the "image
of" preamble (a screen reader already says it is an image), and stripped
markup. Asked politely, a model gets each of them wrong often enough to matter,
so `_clean_alt()` runs over whatever comes back — and over anything typed by
hand in the panel, or the rule holds only until someone edits the box.

The output is the same two shapes as schema: **See the code**, which prints the
old tag and the new one because a find-and-replace needs the string that is
actually in the file, and the two Claude buttons above.
