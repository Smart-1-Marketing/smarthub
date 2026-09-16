## A client's Google listing, read live rather than remembered from the scan

`hub/places.py`, the **Google Business Profile** card on Client 360,
`/api/client/places*`, the scheduler's `places_snapshot`, and the same
card on the client's own report page and PDF. Client 360 already printed
a rating and a review count, read off the last Insites scan -- a snapshot
as of the day it ran, weeks old on most records -- and the client's
report page had carried a Business Profile card reading *"Coming soon:
calls, direction requests and profile views"* since the day it was drawn.
The first is stale and says so nowhere; the second is a promise on a page
a client reads. This is the keyed half of the answer, the Places API,
which needs no consent and so answers for the majority of local
businesses whose profile nobody has ever connected. The OAuth half -- the
Business Profile Performance API, which is what *calls and directions*
actually means -- is not built, and the card no longer promises it.

**A place is resolved once, and a person confirms it.** `candidates()`
asks Text Search for the client's name plus whatever a rep adds and
proposes exactly one answer: the only candidate, or the only candidate
whose website is the client's own domain. Two candidates propose neither
and both are shown, the `client_key.resolve()` rule wearing a listing --
a wrong place on a client's record is somebody else's reviews under their
name, on the one screen everybody reads. The store's own door refuses a
string that is not shaped like a place id before any call is made, so the
refusal does not depend on the wire. The place is keyed on the client's
**name**, never the derived key, and confirming it is logged under
`places` -- declared in `client_brand.NOT_WORK`, because a join is not
work and a client whose listing was confirmed has not had a deliverable
made for them.

**A reading is taken once a night, never on a page load.** Place Details
is billed, and the client's report page is opened by clients. `sweep()`
runs on the hourly tick and `due_for_refresh()` decides, inside a window
`PLACES_REFRESH_HOUR` can move -- the `purchased_domains` shape, so a
leader that restarted through the window picks the read up rather than
skipping a night in silence, and one that restarted minutes after a good
sweep does not spend the book again. Under a wall-clock budget, because
scheduler jobs share one thread. `snapshot()` on its own is a **button**
-- Confirm and Refresh -- and never a GET.

**Review text is not asked for.** The rating and the count are one SKU;
the text is a dearer one and the reviewer's own words, and nothing here
has a screen that needs it. The field mask is a decision written down
rather than a default.

**No listing is not measured, never a zero rating.** A rating is always
printed with its review count, and "nobody has confirmed a listing", "the
key is not set", "we could not read it" and "read last night" are four
states `reading()` names and the card draws apart. The client's page
takes `public_view()`, which carries no staff note and no state name, and
`organic.public_view` drops the block entirely where nothing was
measured -- a card that says *not measured* to a client is a sentence
about our tooling on a document about their business.

**The 30-day change needs a reading 30 days old.** Until one exists the
change is *not measured*, with the date of the first reading, rather than
a comparison against the reading taken a minute ago -- the
`hub/knack_data._snapshot()` rule, which will not compare two numbers
measured on two different days as if they were one series.

**Snapshot and scan stay apart.** `hub/scan_facts.py` reports what the
audit observed on the day it ran; this reports what Google answered last
night. Both are true on different days, the card names which, and neither
is folded into the other. The upsell report's *unclaimed listing* finding
still reads the scan, deliberately: it is one reading for the whole book
and this one exists only for clients somebody has confirmed.

**Every call goes through `quotas.record_google()`**, and
`places.googleapis.com` is in the Google hosts table with its own daily
row, or the usage page could not name this API -- the HeyGen failure one
provider over. The key is read through `hub/config.py` at call time,
under exactly one spelling: `GOOGLE_PLACES_API_KEY` is not in `ALIASES`,
because that table is for names actually in use and a speculative second
spelling is how thirteen correct modules became findings once. It never
reaches a result, an error or a log line. `check_places` on
`/diagnostics` asks Google with one known place and nothing billed beyond
that, and no key is *not measured* rather than a cross.

**Stored through `hub/jsonstore.py` rather than a table.** The readers
are three Flask apps and a background thread, and a Flask-SQLAlchemy
session bound to whichever app is current is the `flask.g` trap one
layer down. Readings are bounded per client, and the sweep's own last
run and count are on `/api/client/places` beside the reading, so a card
can say the job has stopped rather than printing a date and leaving
somebody to do the arithmetic. `test_places.py` asserts all of it, and
`test_reports_seo.py` asserts the client's page and the PDF read one
`_gbp()` rather than each deciding what the listing says.
