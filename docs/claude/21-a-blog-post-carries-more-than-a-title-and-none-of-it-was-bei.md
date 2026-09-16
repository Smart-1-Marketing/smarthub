## A blog post carries more than a title, and none of it was being asked for

The SEO section planned topics and wrote copy from the client's own website and
nothing else. Four things the account manager knew never reached the writer, and
`hub/blog_spec.py` is where they now live — the taxonomy rules, the approved
topic list, the default author and the client's guardrails, read by the planner,
the writer, the client document and the CMS panel alike, the same way
`hub/proposal_spec.py` is read by four things at once.

**The settings are visible before there is anything to plan.** The Blogs card
used to appear only once blogs were switched on in Client Setup, which hid the
author, the guardrails and the approved-topic list — the things filled in
*before* planning — until after something had been planned. The card is always
shown now, says when blogs are off, and opens its settings panel while it is
still empty. A collapsed panel is exactly as invisible as no panel to somebody
who does not know it is there.

**A plan made before the taxonomy existed can gain one.** Re-planning would do
it and would also replace every title and discard written copy, so
`blog_tag_posts()` fills in categories and tags and touches nothing else —
otherwise those rows read "not set" forever with nothing to do about it.

**Categories are structure; tags are detail.** A model asked for "categories
and tags" invents a fresh category almost every time, and twelve posts arrive
under twelve categories — a sidebar of one-post categories that helps nobody.
So the model is told the categories this client already uses and whatever it
returns goes through `clamp_taxonomy()`, which keeps the known ones, allows at
most **one** new category per post, dedupes case-insensitively and caps the
counts. The client's set grows deliberately and slowly. Same clamp on the edit
route, or the rule holds only until someone types into the box.

**The approved-topic upload sits beside the planning question.** It spent a
release inside the collapsed settings panel, where nobody found it. What a
setting *changes* decides where it lives: the author and the guardrails are
set-and-forget and belong in a drawer; the approved list changes what the next
plan contains, so it sits in the Blogs card in its own panel, above the button
that acts on it, saying what is loaded without anything being opened.

**An approved topic is reproduced, not paraphrased.** A topic list a client
signed off in advance is a commitment. `parse_approved_topics()` reads the
document we emailed them — PDF, Word or pasted text, through the same
`_read_document()` the IO Builder uses — and the approved titles are written
into the plan **in code**, after the model has answered, because "use these
titles as written" is a request and a paraphrased title is a topic the client
did not approve. Each post records whether it came off that list. With
`approved_only` the schedule stops when the list runs out instead of topping
itself up with invented topics.

The parse is two-pass: a document that numbers or bullets its topics has told
us which lines are topics, so every other line is notes on the one above.
Guessing by line length read a 118-character sentence of notes as a topic of
its own.

**"Never mention" is a check, not a sentence in the prompt.** This is the Smart
1 Labs rule again — a prompt is a request, and "the model was told not to" is
not evidence that it did not — and here it is usually a legal instruction. The
list goes to the model *and* `scan_forbidden()` reads the finished copy and the
meta description, flags every hit with the sentence around it, and the flag
follows the post into the table, the client document and the publish panel until
someone rewrites it. It strips the HTML first: scanning raw markup matched
`class="guarantee-band"` and flagged a post whose copy never said it. The free
guidance box still goes to the model unchecked, because most of it is context —
how they operate, what they are licensed for, how the warranty works.
