## A client's YouTube channel, read live, on the same key

`hub/youtube.py`, the **YouTube channel** card on Client 360,
`/api/client/youtube*`, the scheduler's `youtube_snapshot`, and
`modules/reports/youtube.py` behind the section on the client's report
page and PDF. The Hub sells YouTube two ways -- TrueView and bumpers on
the rate card, and Social Media Management, which posts to the channel --
and knew nothing about the channel: the SEO record holds a link somebody
typed, and no screen had ever read past it. This is the keyed half, the
Data API v3, and it is the Places module one channel over: propose, a
person confirms, a reading a night, four kinds of nothing kept apart. The
Analytics API half -- watch time, subscribers gained per day, traffic
sources -- is behind OAuth and is not built, because it is a scope on
Google Finder's list and every login connected before it keeps its old
grant and has to re-consent.

**The key is shared and says which variable answered.** The API is
enabled on the same Cloud project as the Places key, so `YOUTUBE_API_KEY`
is read first and `GOOGLE_PLACES_API_KEY` second, and `key_source()` names
the one that answered -- on the card, on the sweep state and on the
/diagnostics row. That name is the fix: a Places key restricted to Places
API (New) is refused by YouTube, and the refusal has to say whether to
widen that key's API restrictions or set the second variable. Not an
`ALIASES` entry, because these are two settings that may hold two values,
not two spellings of one.

**A link is read before a search is spent.** A channel id, a `@handle` or
a `/user/` address resolves through `channels.list` for one quota unit
and names one channel; `search.list` costs a hundred and returns whatever
carries the name. So `candidates()` reads a link first -- one the rep
pastes, else the one on the client's SEO record, which the GET carries as
`hint` -- and searches only with no link, on the name plus whatever was
typed. Words typed beside a linked record are a request to search, not to
re-read the link. A record link that resolves nothing falls through to a
search and says so; a link the rep pasted that resolves nothing says so
and does not search; a video or playlist address is refused in words with
no call made. `parse_ref()` is the one reading of what a pasted string
names.

**One proposal or none, and the exact title is the second-best evidence.**
Places had the client's domain to pick between several listings; a
channel carries no website in the API. The channel a link named is
proposed; the only search result is proposed; among several, the only one
whose title is *exactly* the client's normalised name is proposed; two
carrying the exact name propose neither and both are shown. Never a
substring, never the first row -- a wrong channel on a client's record is
somebody else's subscriber count under their name.

**A hidden subscriber count is hidden, never zero.** A channel may hide
its count, and the API answers `hiddenSubscriberCount` with no figure.
The reading keeps the views and the video count beside a `None`, the card
and the client's page say *hidden*, and the 30-day change carries no
subscriber delta for it while still carrying the views. Views are
lifetime, so the 30-day change is the one period figure the keyed API can
give -- arithmetic on two of our own readings, the Places rule.

**Every call records the units it spent, not the request.** Google meters
this API in units and grants 10,000 a day, so `quotas.record_google()` is
handed `units=1` for a read and `units=100` for a search and the usage
row is in the unit Google counts; recorded per request the row would read
a hundredth of the truth. The recorded URL is the endpoint alone -- the
key rides in the query string on the wire and nowhere else. A spent
quota stops the sweep the way a refused key does, because it is spent for
every client.

**The client's page is gated on a product and a person.** The section is
its own block on the aggregate, not a card inside the organic section:
inside organic it would reach only SEO clients, and the client whose
channel Smart 1 posts to is on a social retainer. `modules/reports/youtube.gate()`
needs a live product `creative_needs.medium_of` calls video or social --
read from the live product book the SEO section reads, matched on the
exact normalised name -- and a channel a person confirmed. A confirmed
channel with no reading yet is gated in and absent from the page, because
"confirmed and not read yet" is a sentence about our tooling on a
document about their business; the staff page prints the gate and says
*waiting on a reading*. "YouTube" is named on the page, because it is the
client's own channel and not a vendor Smart 1 buys from, and
`products.ALLOWED` carries that reason so the forbidden-word sweep and the
next reader both know it was decided. `test_youtube.py` asserts all of it,
the client's page, data.json and PDF included.
