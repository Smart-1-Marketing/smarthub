## Two fields said the campaign was blocked and nothing read either

`hub/campaign_assets.py` and `/tools/campaign-assets`. Every product on
object_135 carries **Clarification needed** (`field_2742`) and, behind a
tickbox (`field_2346`), the **additional assets** still outstanding
(`field_2347`). Both have been filled in for years. Neither had ever been
read, so the only way to find the campaigns waiting on artwork was to open the
insertion orders one at a time — which is done by nobody, so it is found at
launch. The list is per **campaign** rather than per product, because the chase
is one conversation with one media partner, and it is sorted by media partner
then internal sales for the same reason. Each blocked product line is listed
inside its campaign: a display line waiting on banners and a video line waiting
on a spot are two asks to two different people.

**The tickbox is the answer, and the text beside it is not.** `field_2347` is
read only where `field_2346` is ticked. But text sitting in 2347 with the box
unticked is not discarded in silence — those rows go in their own panel,
**Need Clarification**, because "nobody needs anything" and "somebody typed
what they need and never ticked the box" are different situations and only one
of them is finished. It carries the media partner and the rep like the main
table does: it is a chase list too, and a row with nobody's name against it is
one nobody picks up. `asset_ask()` is the single place that gate is applied;
`knack_products._row()` carries both fields raw, or a row with unticked text
would be indistinguishable from a row with no text at all.

**The page explains itself; the report does not narrate.** `_note()` returns
nothing at all when the report could answer the question — the heading says
what the list is, the toggle says what is in it and every panel carries its own
count, so a paragraph restating all three is read once and skipped for ever.
The one thing prose still has to carry is the case the screen cannot show:
rows that could not be measured, where an empty table would otherwise read as
a clear queue.

**A cache written before a field existed answers "no" to it, on every row.**
The product cache is a flattened copy of object_135, so the rows it holds have
exactly the keys `_row()` had when they were written — and a missing key reads
as "this campaign needs nothing", about every client at once. Two halves:
`knack_products.FIELDS_VERSION` makes an older cache stale by definition
however recently it was written, so `rows()` refetches rather than serving it;
and `report()` asks whether the rows can answer the question *before* it
reports that the answer is none, saying **not measured** instead of drawing an
empty, confident table. The private fallback carries none of these fields at
all, which is the same statement.

**A blank media partner sorts last, in its own group.** An empty string is not
an early letter of the alphabet, and a campaign nobody has filed must not head
the queue by accident. Same for the rep.

**The question is not "is it running".** `knack_data.is_running` answers "is
this delivering today", which is wrong here by exactly the interval that
matters — the campaign starting in three weeks is the one somebody has to chase
artwork for. So a future start counts, and only a finished status or an end
date already past takes a row off the list. What is skipped is counted and the
whole list is one toggle away, the way `sites_match` names the projects it did
not check.

**A pinned id is not a checked id.** These three were pinned from the field
numbers alone, and a *renumbered* field reads back empty on every record —
which looks exactly like a client base with nothing outstanding.
`field_check()` reads object_135's live schema and reports what Knack calls
each id and what type it is, on the page rather than in a diagnostic nobody
opens; a `field_2346` that is not a boolean is named, because the entire list
is gated on that tick. Each id is overridable by environment variable
(`KNACK_CLARIFICATION_FIELD`, `KNACK_ASSETS_FLAG_FIELD`,
`KNACK_ASSETS_NEEDED_FIELD`), so a renumber is one variable rather than a hunt,
and the page's own script reads the ids handed to it by the server rather than
carrying a second copy. `test_campaign_assets.py` asserts all of it.
