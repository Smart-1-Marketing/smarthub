## Amazon DSP: five claims, and four of them fail as a working configuration

`modules/ads_builder/amazon_ads.py` (the connection, the entity and the async
report), `modules/reports/amazon_dsp.py` (the pull), `/reports/amazon-check`,
the Connect card on `/tools/ads/settings`, and the Amazon DSP row on
`/diagnostics`. The endpoint-by-endpoint account -- what each verb needs,
which are built, and the first-live-pull checklist -- is
`docs/amazon-dsp-api-integration.md`; this is what the code decided and why.

**"We have Amazon API access" is five separate claims, and one flag for all
five is the failure this is built around.** Credentials set; the refresh
token still consented; the entity's profile visible to whoever pressed
Connect; the advertisers readable; and writes available in this region. Four
of them fail in ways that look like one of the others, and three of those
four look exactly like a wrong key:

* a revoked consent, or one given against a different Login with Amazon
  application, answers `invalid_grant` -- which reads as a bad secret and
  sends somebody to rotate a credential that was never wrong;
* a consent given by a rep rather than by an admin on the DSP entity
  succeeds, and then lists no profiles. Nothing refuses; the entity is
  simply not there;
* an Amazon Ads API *application* that has not been approved for the entity
  answers `403` on every call -- the Google Explorer-tier lesson, one
  platform over. `AmazonApiError.kind == "not_permitted"` is what keeps it
  apart from `auth`, and the sentence on `/reports/` says which it is.

So `preflight()` asks all five at once and reports every unmet one, and the
check page draws the ladder with the rung it stopped at. **Rung 5 is never
probed.** Nothing in the Hub writes an Amazon campaign -- `/api/amazon/*`
answers 501 in words that say so -- so nothing in the Hub says whether writes
are available to this seat in this region. `preflight()` answers `unprobed`,
which is not the same as "no".

**The consent inherits the access of whoever presses the button**, which is
said on the Settings card, in the help bubble, on the connected page and in
the callback's own blurb rather than in one place somebody might not be
looking at. It is the one thing about this connection a person can get wrong
while doing everything the screen asked.

**The paths are a transcription, and the field map is a claim until somebody
has looked.** Amazon is mid-way through moving DSP onto the `/adsApi/v1`
model, and the legacy `/dsp/` paths answer today as well, for different
things: `ENDPOINTS` is the one place a path lives and each carries
`confirmed`. `FIELD_MAP` and `CONFIRMED` in `amazon_dsp.py` are the same
discipline `groundtruth_map.py` records -- the index line says the pull is
reading a claim, `/diagnostics` says it as a warning, and
`/reports/amazon-check` prints a raw row beside the map so the correction is
read rather than guessed at. This is not a fault; it is the honest reading of
a feed no live entity has answered.

**One report per advertiser, and *pending* is not a failure.** Submit, poll
inside `BUDGET_SECONDS`, and past the budget record the reportId and stop
holding the scheduler's one thread -- the rule `stackadapt.py` learned. The
next tick asks for the same reportId rather than paying for a second report,
carried between ticks in the module's own note, so the scheduler wires this
up like every other pull. A pending tick stamps no watermark: nothing landed
and nothing failed, so the last good pull is still the current one and
`/status` reads a feed whose age is growing rather than a fault stamped every
night. A run that stopped at "not configured" is not a pull either, and
does not stamp a last-pull time for a feed nobody has ever read.

**The order is the campaign; the line item is not.** The auto-mapper files a
client from the order name the way it does from a Google campaign name, and
an order whose name carries no client waits on `/reports/unmapped`. The line
items ride in `extras` by name, which is where they can be read and where
they cannot be mistaken for a second campaign.

**And the report's grain is folded before it is written, which the first
version of this did not do.** The request asks for ORDER *and* LINE_ITEM, so
one order-day comes back as one row per line item; the fact table's key is
the order-day. Handed over unfolded, those rows are several upserts into one
row and the last one wins — an order that spent sixty dollars filed as
whatever its last line item spent. Nothing would have shown it: the number is
plausible, it is on a client's report, and every screen would have read
healthy. It survived the first test file because every fixture had one line
item per order, which is the shape the documentation's examples happen to
use and not the shape the request asks for. The figures are summed per
order-day now, `ttd_myreports`'s rule for a split row, and the regression
test is two line items on one order-day asserting the sum rather than the
survivor.

**Purchases are not conversions**, and the limit of that is written down
rather than implied. `totalPurchases` and `totalDetailPageViews` are
Amazon-storefront metrics: they land in `extras` under their own names and
nothing writes either into `conversions`. What this does not do is stop a
client page printing *Conversions 0* for this platform -- the fact table's
conversions column defaults to zero and cannot say "not reported", which is
the state AudioGo and GroundTruth are already in. Suppressing that tile is
one change to the client view for every such platform at once; it is not made
here rather than made for one platform and left inconsistent for the others.

**Spend is `totalCost` with no divisor**, in the advertiser's own currency
(which rides in `extras`). A first live row a thousand times too big means
the field is wrong, not the divisor, and the check page says so -- dividing
until a number looks right is how the wrong figure gets filed under the right
name.

**The pre-signed download gets no Authorization header — and its signature is
not written down either.** The report body sits at an S3 URL Amazon hands
back; a bearer token sent to a third-party host is a leak. The other half of
that took a second pass to see: the URL's own query string carries
`X-Amz-Credential` and `X-Amz-Signature`, so the URL *is* the credential —
whoever holds it can fetch the report until it expires — and the first
version recorded it whole into the usage ledger, which is rendered onto a
page and pasted into chats. The module's test asserted that no credential
reached a recorded call and passed, because the fixture URL had no signature
on it. `_bare()` drops the query from anything written down, and the fixture
now carries a real signed shape so the assertion means something.

**A report that never lands is given up on, and said out loud.** The pending
reportId is carried with the moment it was first seen -- carried, never
re-stamped, because a report re-stamped every tick is one that is always
"pending since a moment ago" and can never be called stuck. Past
`STUCK_AFTER_HOURS` the run stops asking for that id, asks Amazon for a fresh
report, and names it in the result, on the `/reports/` line, on `/diagnostics`
and on the check page. Without the ceiling the shape of the failure is the one
this codebase keeps meeting: every screen healthy, a line that reads *still
preparing* every night, and nothing ever arriving.

**And the check page cannot 500.** It is the page somebody opens precisely
when the connection is behaving oddly, so a raise anywhere under it comes back
as an error printed on the page rather than as a stack trace that takes the
other panels with it -- the rule `hub/oauth_redirects.py` states for a
diagnostics panel, applied here. A refusal from the ladder itself is still
named where it happened, rather than as the page failing.

**One advertiser refused is that advertiser's problem.** `not_permitted` on
one is named and the rest of the entity still lands; the whole entity refused
is reported as the approval rather than the key. Every call is recorded under
`amazon_ads` by endpoint family, so the usage page can say which half of a
pull spent what -- the Login with Amazon token endpoint deliberately
excepted, because a token refresh is not a metered call.
