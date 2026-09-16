## A fallback secret in the source is a forgeable token

`hub/signing.py`. Eight things here are signed with `itsdangerous` and six
resolved the secret their own way, which is the drift `hub/storage.py` exists
to stop wearing a signature. The difference between the six is what happens
when **no secret is set**, and two of them had it right.

**`hub/auth.py` and `hub/identity.py` fell back to a random ephemeral
secret**, and auth.py's comment says why: everybody re-logs-in after a
restart, which is noticed and cannot be forged. It fails **closed**.

**Four fell back to a literal in their own source** — `"dev-only"`,
`"smart1-client-links-development"`, `"s1hub"`, `"s1hub-social-dev"` — which
fails **open**: it is the same string on every deployment, so anybody who can
read the file can mint a token. The worst is `hub/users_routes.py`, which
signs the **per-account session cookie** carrying the user id, the role and
the must-change-password flag, read by the middleware in `wsgi.py` in front
of every mounted module. With `SECRET_KEY` unset, a cookie signed `"dev-only"`
claiming `{"r": "admin", "c": false}` was accepted as an **Admin session
belonging to no account**. That was minted and accepted before the fix.

**And the safe half was not safe either.** `auth.py` and `identity.py` sign
the *same salt* (`s1hub-session`) and each generated its **own**
`secrets.token_hex(32)` at import — so with nothing set they disagreed inside
a single process and each refused the other's cookie, silently, which reads
as a sign-in that does not stick. identity.py's own docstring claimed the
opposite: *"both read hub.config so neither can know a spelling the other
does not"* — true of the spellings, false of the fallback. The ephemeral
secret is resolved **once per process** now, so two readers of one salt agree
by construction rather than by both being configured.

Three rules. **Never a literal** — there is a real secret or an ephemeral
one and no third branch. **A placeholder is not a secret**: `hub/config.py`
has detected the env.example values all along and no signing site asked it,
and the four literals are on that list too, because from the day they were
written down they were known secrets; nothing speculative is added beside
them, the `ALIASES` rule. And **say what it costs, because it is not the same
cost for everybody** — an ephemeral secret is a re-login for a session cookie
and a **dead link on somebody else's website** for a client's social or
approvals page, so `report()` names the state and `/status` prints it.

**The status row was describing two of the eight.** `hub/config.py` has said
*"sessions are not signed without it, so everyone is logged out by every
restart"* the whole time — a true account of the two that failed closed and a
wrong one about the four where nobody was logged out and anybody could forge
a cookie. It reads `signing.report()` now, so the row and the thing it
describes cannot disagree; and `bool(secret_key)` was not the question
either, since a placeholder is set, is not a secret, and used to read **ok**.

`test_signing.py`'s core is a **sweep**: a test naming the four call sites we
fixed proves nothing about the ninth, so it reads the **AST** of every file
constructing a serializer and requires the secret to come from
`hub/signing.py`. Prose is not a call site, for the seventh time in this file
— `hub/signing.py` quotes all four literals to explain them. Both defects were
reverted and confirmed red before they were confirmed green.

**Placeholder values are worse than blanks.** `CLOUDINARY_URL` sat at
`cloudinary://API_KEY:API_SECRET@CLOUD_NAME` and every "is it configured?"
check said yes. `hub/config.py` detects the known placeholders. Render also
stores quotes literally — `SCANS_CALLBACK_TOKEN="abc"` includes the quotes,
which silently breaks callback matching.

**A period that comes from a file nobody refreshes is not a period.** The
dashboard's scorecard trends were keyed on `products.json`'s `thisMonth` —
a committed export refreshed by hand, which has carried one value since it was
generated. Every load wrote today's numbers into that one bucket, so a second
bucket could never appear and every card read "– vs last mo – vs last yr" for
ever, with Website Movement promising history "next month" in a month that
would never come. `hub/knack_data._current_period()` reads the clock; the
export's month now labels only the counts that genuinely come from the export,
and `export_stale` says when those have gone out of date.
`test_dashboard_trends.py` moves the clock rather than promising.

**And a snapshot history cannot answer about a month before it existed** —
which is why the scorecard now carries no comparison at all. The fix above is
correct and, on its own, still shows a dash on every card: the first reading is
taken the month the Hub is opened, so last month has no bucket and the same
month last year does not arrive for twelve. The obvious way round it is to
rebuild the missing months from the export — every insertion order has a start
date, an end date and a monthly rate, so *what was billing in July* is
arithmetic. That was built. It reproduced Knack's own `thisM` / `lastM` flags
exactly, and it was still removed.

**Because it was not measured the same way as the number it sat under.**
`is_running` is deliberately a *union*: an IO counts if its term covers today
**or** Knack still calls it Live, which takes in about 140 month-to-month rows
nobody has closed out. A term rebuild cannot see those, so the card read
"516 live products, ▼26.9% vs Jul" where the two figures behind that
percentage were 510 and 373 — arithmetic no reader could reproduce from
anything on screen, printed in red on the CEO's dashboard. Marking it `≈` and
explaining it in a legend is not a fix: a number that needs a paragraph before
it can be believed is a number nobody should be reading off a scorecard.

`_snapshot()` still runs on every load, because a reading taken this month is
the only thing that can ever produce a comparison measured the same way at
both ends and it cannot be taken retrospectively. When there are two of them,
a comparison can come back without inventing anything.
`test_dashboard_trends.py` holds both halves: the readings accumulate, and
nothing on the page claims a comparison.

**A customer is matched to a client exactly, or not at all.**
`invoice_off()` fell through to `next(... if norm in n or n in norm)` — an
unbounded substring, both directions, first out of a dict ordered by the
export. That is the rule `hub/client_key.py` exists to refuse, and it was
live: **32 of this deployment's 547 client names contain or are contained by
another**, and `cirilla s` alone matches 18. So a QuickBooks customer named
"Cirilla's" was costed against whichever of eighteen came first, and the
variance printed with no sign a guess had been made.

**It failed in both directions, and the second is the expensive one.** Forward,
a customer was attributed to a client nobody chose. Backward, an active client
with live billing and *no invoice at all* dropped off the report the moment any
customer name merely contained theirs — nine clients carrying **$22,091 a month**
sit in that shape here, seven of them the `N2 Advertising - Cirilla's <city>`
rows, every one of which contains the parent client `Cirilla's`.

Nothing is dropped to fix it, which is what made the change safe to make: a
resemblance is **printed and still counted as unmatched**, the answer
`sites_billing` and `domain_renewals` both arrived at, so the confident wrong
rows become labelled unmatched ones and the hidden findings come back. A
customer that only resembles a client is listed with **no difference at all**,
because there is no client we can stand behind to compute one against, and it
names what it resembles. A client invoiced under a similar but different name
is listed too, with that name on the row: *"no invoice found"* and *"no invoice
under this name; QuickBooks has X"* are different things to chase, and only the
first is a billing gap. `_join_names()` caps the naming at three and says how
many more, because a row is not a list.

**And eleven reports read that export without ever asking whether it could
be read.** `knack_data._load()` swallows `OSError` and returns `None`, so a
missing, unreadable or malformed `products.json` yields `[]` — and to a caller
that is indistinguishable from a client base with nobody on it. Six client
reports and both Scorecards rendered a clean empty table saying **every client
has a dashboard, nobody has lapsed, nobody is missing Analytics and nobody
churned**, and `report_cache.is_answer()` stored it as the day's answer, frozen
until tomorrow, on a source that was never read. The three billing reports
behind it were worse than quiet: an unreadable export makes every Suite
sub-account look like one with no live product, which is
`ghl_billing_no_products`' own finding, so it would have *invented* rows
rather than merely missed them.

`knack_data.products_error()` is the question, and it is a **sentence rather
than a bool** so the report can print why it is not measured. It tells the two
empties apart — the file could not be read, or it was read and holds no rows —
because they are different things to do about it.

**The sweep is what found the last three.** `test_qa_reports.py` reads the
**AST** of `hub/qa.py`, takes the transitive closure of every report function's
own calls, and asks which of them reach `_client_groups()` or `_month_rollup()`
— so a report added next month is swept without anybody remembering. Written
against the six that were obvious, it immediately named `invoice_off` and both
Suite billing reports as well. And the assertion is **"never a green tick"
rather than "always `measured: False`"**: two of the eleven reach a provider
before they reach the export and say *that* first, which is a true statement
about why they could not look — asserting the flag would pass or fail on which
providers the environment happens to have configured.

**And the two Scorecards were measuring "running" a third way, on the same
page as the reports that do not.** `qa._active_in_month()` was written beside
the scorecard rather than beside `is_running()`, and it tested `status in
("live", "complete")` — the narrow test that function's own docstring says
"missed about a third of the work actually running". So Active Clients, No
Dashboards and the renewal queue counted the union while the Salesperson and
Partner Scorecards counted two statuses, three rows apart on `/qa`, with
nothing on either saying they were measured differently. On this deployment's
own export that hid **147 rows and $140,439 a month** from August, and took
**Debi Greenfield and Kim Marshall** off the Scorecard entirely — two people
with live work, each listed on Active Clients immediately above. Every screen
was internally consistent, which is why it survived: the `/api/db/structure`
versus `/api/integrity` trap, wearing a scorecard.

`knack_data.ran_in_month()` is that rule now, a **neighbour** of `is_running()`
rather than a second reading of it, because the two must still differ and the
reasons only make sense read together. **Complete is a pass here and a fail
there** — a finished row cannot cover today, and a row that ran January to
June plainly delivered in March, so dropping it empties every historical
month. **Live does not override the dates**: there it is a union, because an
IO nobody has closed out is still delivering; asked about a month, a Live row
with no term would land in all twelve. And **a row with no dates at all is in
no month**, which the old test got right by accident — it trusted an undated
row only when Live, and none of the export's 33 undated rows is Live, so the
branch had never matched anything.

What it keeps is the tolerance, **Cancelled included**: those rows sit inside
their dates and bill, which is the reading `is_running()` already applies to
the 73 of them worth $85,105 a month that Active Clients counts today. The
limit is written down rather than discovered — Knack publishes no cancellation
*date*, so an IO cancelled mid-term is counted for every month its term spans;
the alternative was dropping rows the rest of the Hub counts, which is a third
definition rather than one fewer. `test_qa_reports.py` asserts the invariant
that binds them over the **real export** rather than a fixture — anything
`is_running()` calls live today counts for the month containing today, its one
documented exception named — and reads `_active_in_month`'s **AST** to require
it be nothing but a call to the shared rule, because that function's own
docstring quotes the old allowlist to explain the fix and a text match reports
the explanation as the defect.

**A filtered list that reports an unfiltered total is a wrong answer with
two right ones either side of it.** `/tools/seo-images/api/gallery` filtered
its rows by client and then returned `len(load_archive())` as the total, so
Client 360 — which prints "Showing N of total" — said **"Showing 1 of 7 saved
images"** about a client with exactly one, and the gallery that sentence
linked to then showed the one. Neither screen was wrong; the sentence joining
them was, which is harder to notice than either being wrong. `total` is now
the total of what was *asked for* and the archive-wide figure is carried
beside it under its own name.

**And one screen along, the same sentence built from the page instead.** The
UTM Builder's saved-links table prints `savedRows.length + ' of ' + d.total`,
and `savedRows` is what the API sent — capped at 300. So a search matching 450
of 900 tracked links read **"300 of 900"**: the page reporting its own length
as the match count, which is the failure `google_links.orphans()` names in as
many words ("a page reporting its own length as the total is how somebody
concludes there are 25 orphans"). It survives because it is internally
consistent — the table really does hold the 300 rows it drew, so counting them
by hand confirms it. `matched` is on the answer now beside `shown` and
`total`, three numbers because they are three questions, and the page says
*showing the first 300* rather than leaving somebody to conclude there were
300. Only where they differ: a caveat on every search is a caveat nobody
reads.

**And the CSV button beside it searched a different question.** The table
matched on eleven fields and `/api/links/export` on five, and the two the
export did not know — `label` and `created_by` — are the two that appear
nowhere in the tagged URL either. So searching for a flyer's name or a
colleague's narrowed the table to the rows you wanted, and the download
carrying that same `?q=` came back with **a header row and nothing else**: not
a subtle divergence but a valid, empty spreadsheet saying there were none, on
the same press, contradicting the table it was downloaded from. Nothing
errored at either end. `filter_links()` is the one reading now — the rule
`hub/storage.py` and `hub/images.py` exist for, wearing a search box — and
`SEARCH_FIELDS` is written down once beside it.

**And the archive is capped at 8,000, which nothing said.** New rows go on
the front, so a save past the cap drops the **oldest** tracked URLs — which
is exactly the thing this module exists to prevent, *a tagged URL nobody can
trace back to a campaign*, arriving as a save that reported a clean success.
`save_links()` returns what it dropped and the page says so. Bounded, and
never in silence: `hub/drafts.py`'s rule, one tool along.

**A card that shows a wrong image and offers nothing to do about it sends
somebody through two screens.** The Client 360 image tiles linked out to the
file and to the gallery, and the gallery — the one screen a client record
opens — had no delete and no alt edit either; both existed only on the
pipeline's own archive table, which is not where anybody looking at a client
was. Both screens post to the *same* `api/gallery/update`, so there is one
description of what deleting an image means and one place that decides
whether the Cloudinary copy goes with it. It is named in the confirmation and
says it cannot be undone, because for an image a client sent us our copy is
very often the only copy.

**And the gallery that link opened was one source's slice, read as the whole.**
"See client image gallery" opened the SEO pipeline's archive scoped to the
company — real images, correctly filtered, and a rep read it as everything we
hold for the client while their uploads, display ads, logos and stock sat in
the full gallery one module over. The link goes through
`/tools/image-picker/gallery/for-client` now, which resolves the name under
`provisioning.py`'s rules — exactly one gallery or none, never a substring —
and lands on the full gallery, every folder and every source; a client with
no full gallery yet lands on the SEO archive scoped to them, which is
everything the Hub holds outside one. The two cannot bounce a reader between
them, because the scoped SEO view offers a **Full client gallery** link only
when the server resolved exactly one, and says it is the pipeline's own view
rather than claiming to be every image saved. A view narrowed inside the full
gallery — a group chip, a search, or both stacked — carries one **Show the
full gallery** press back to everything, because the All chip and Clear each
undo only half and "N of M shown" is a state somebody should not have to
reverse-engineer their way out of. `c360` rides through the resolver's
redirect, so "Back to <client>" survives the hop. `test_image_picker.py` and
`test_client_images.py` assert all of it.

**"Back" from a tool means the tool, and from a client record it means the
client.** Every link out of Client 360 landed somewhere whose idea of back was
its own parent, so a rep who opened the image gallery for Icon Solar got
"← SEO Image Pipeline" and had to search for the client again. `hub-crumbs.js`
stamps `c360=<client>` onto the links that leave the record and draws
**Back to <client>** on every page downstream — one script, loaded on hub
pages by `base.html` and injected into all twenty mounted modules by `HubBar`,
so a tool linked from Client 360 next month gets it without being edited. Four
things it does not stamp, each for its own reason: the chrome (following the
sidebar to the Dashboard is not still working on this client), anything
cross-origin (QuickBooks and Cloudinary are not ours to add parameters to), an
API path, and a download. It stamps again on a debounced `MutationObserver`,
because Client 360 and half the tools it opens draw themselves from fetches
and a single pass at load would stamp the shell and miss every link not yet
drawn. Landing back on `/client360` clears it: a bar offering the way back to
the page you are standing on is noise, and one pointing at yesterday's client
is worse than noise.

**And the rest of that trail was a second description of where every tool
lives.** `hub-crumbs.js` carries a hand-written map of URL segment to tool
name and of tool to index page; the first description is the tile on
`/creative`, `/tools` or `/qa`. It has drifted **twice**, and its own comment
records the first: the creative tools moved onto `/creative` while their URLs
stayed under `/tools/`, so the trail went on offering the way back to a page
the tool is no longer listed on. It happened again the day Scan All Clients,
Match Sites, Match Google Accounts, Web Tickets, Domain Renewals and Campaign
Assets Needed moved to QA Reports — six tools whose "back" landed on Client
Tools, where none of them is tiled. Nothing reports this: the link resolves,
the page renders, and it simply lands somewhere the tool is not.

**A segment with no entry is title-cased, which is right for `site-blocks` and
wrong for `io`.** The IO Builder's trail read **"Io"**, Smart 1 Ads read
"Ads", the GPT Ads Builder read "Gpt Ads", and Video Search read *Video
Backgrounds* — the mount kept that name so existing links resolve, and the
tool did not. Every tiled tool is named now, spelled the way its tile spells
it.

**Only `/tools` was read as holding several tools**, so every page under
`/sales`, `/qa` and `/scans` took its **mount's** name — twice.
`/qa/stale-creative` came out *Dashboard / QA Reports / QA Reports*, naming
the report nowhere; `/sales/builder` and `/sales/landing` were both *Sales*.
`CONTAINERS` is the list, `keyOf()` is the one reading of which segment names
the tool, and the *"where you came from"* crumb reads it too — asked
separately, that crumb said **← Sales** for the Proposal Builder and **← Site
Scans** for Scan All Clients, naming the mount rather than the page somebody
had just been on.

**The map is held against the tiles rather than remembered.**
`test_menu_layout.py` lifts the resolution block out of the file — it is
marked for it, and it is pure — runs it in **node**, and requires every tile
on the three index pages to resolve to a trail that names the tool the tile
names and offers back the index the tile is on. That is the arrangement
`test_proposal_targeting.py` uses on the target-area step, for the same
reason: a copy restated in the test is a third thing to keep in step. It
started green and it bites on both kinds of drift — a renamed tool and a
moved tile — which is the only way it was worth adding. A tile pointing at a
page *inside* another tool is exempt **by name with the tool it belongs to**,
and an exemption naming a tile that no longer exists fails, the rule
`check_stale_json_exemptions()` works to.

**The brand card read stored data and nothing ever stored any.** Three modules
ran live Brandfetch lookups — Image Creator, Smart 1 Ads and the Suite Panel —
and only the Suite Panel ever saved the answer, and only when it was handed a
`?client=`. So Image Creator spent one of the plan's hundred monthly calls on
every search and threw the result away, while the Client 360 brand card, which
*only* reads what is stored, said "No brand data on file yet" about clients
somebody had looked up that morning. Nothing errored at either end. And the
card asked by client **name** alone, so the domain-keyed half of the store —
which is where every saved lookup actually lands, since a tool with a URL has
no client name to file under — was never consulted at all. `hub/brand_lookup.py`
is the one live path now and it keeps what it paid for, saving against the
domain *and* the client (the two readers key on different things, and a
payload filed under one is invisible to the other); Client 360 passes the
website; and there is a **button**, because the call is billed and a page load
must not spend one.

**And an empty answer has to say which kind of empty it is.** "Nobody has
looked yet", "there is no website to look up by", "the key is not set" and "we
looked and they publish nothing" are four situations, one of them is a button
press, and they read identically before this. A refused key (401) and an
unreachable service are kept apart for the reason `services/provider_check.py`
gives: calling the second one a bad key sends somebody to rotate a good one.

**And the push out of that card was remembered by nobody.**
`client_brand.mark_pushed()` was written, was documented — *"Record that the
brand guide reached Suite"* — and had **no caller**, so nothing had ever
written `suite_brand_guide`. That field is what the card reads to decide
between drawing the state and drawing the button, and the card's own comment
says why it matters: *"Once the guide is in Suite the button is a trap:
pressing it again just overwrites what's there."* The guard was real and it
held for the life of **one page view** — `pushBrand()` swaps the button out in
the browser — so a reload brought the button back and the trap the comment
describes is what actually happened: the same guide pushed again and again,
each press silently overwriting Suite, with the toast saying success every
time. Invisible from either end.

`mark_pushed()` **returns the stamp it wrote** now, so the route hands back the
string the next page load will read rather than the browser inventing a second
idea of when this happened — and it is called **only where the delivery
actually succeeded**. A push that was refused, that could not be reached, or
that was merely offered for somebody to paste by hand has not reached Suite,
and a green pill over any of those is the confident wrong answer this corner
keeps having to undo.

**Three outcomes on that button, not two.** "The variable is not set", "Suite
refused it" and "we could not reach Suite" send somebody to three different
places, and all three were reported as the first — telling a rep to set
`GHL_BRAND_WEBHOOK_URL` when it was already set, the
`services/provider_check.py` rule one card along. The refusal carries Suite's
own status line, because discarding a provider's own sentence is how every
button comes to report its own invented diagnosis of one shared failure.

**An Insites audit carries 440 fields and Client 360 read four of them.**
The score, the broken-link count, the image count and the speed band — while
the logo, the brand colours, the Google Business Profile and review standing,
the social accounts with their follower counts, what the client is already
spending on Google and Meta, whether a pixel or a tag is on the site at all,
the organic estimate, the platform, and the registrar all sat in a JSON blob
nobody opened, already paid for. `hub/scan_facts.py` reads them, grouped by
the question each one answers, and it is where the **logo** comes from for the
majority of local businesses that have no published brand anywhere: Brandfetch
has nothing for them and the last scan photographed their home page. That
sighting is carried as `observed`, with the date and a link to the scan, and
is **never merged into `logos`** — a logo lifted off a page is a candidate,
and a wrong logo on a client-facing document is worse than none, because
nobody proof-reads the thing they recognise. Three more rules in it: it reads
the scans table through the shared engine rather than importing the mounted
module (the `flask.g` trap above), it answers *not measured* with the reason
carried when the table will not answer, and a section the account's plan does
not include is **left out** rather than printed — forty rows of "not measured"
is a wall nobody reads, and a zero there would be a lie. A `False` boolean is
an answer and is kept. `test_client_images.py` asserts all of it.

**And Client 360 was the fourth screen, reading the same audit differently.**
The audit tool, the prospect record, the customer-facing placement and the
upsell report all lead with `website_audit.spend()` — the total, the annualised
figure, the implied cost per visit and what is deliberately left out of it.
Client 360 showed the same five fields as one collapsed reference group among
ten, so the same client's own money read two ways depending on whether somebody
opened the *client* record or the *prospect* record. It reads
`/api/client/audit` now, which is `website_audit.audit()` and therefore drops
the thin group itself — the figures are on the page once rather than twice —
and it gained the findings, which that record has never shown and which are the
whole upsell conversation.

**The route is `/api/client/audit` and not the audit tool's own, and that is
the embed rule rather than a preference.** Client 360 is framed inside Smart 1
Suite, and `suite_embed.EMBEDDABLE` allowlists `/api/client/` and not
`/api/website-audit`. A card pointed at the tool's blueprint renders on every
screen except inside the frame — which is the half-broken embed
`hub/suite_embed.py` exists to prevent, and it fails silently: the record loads
and one card is empty. Same function behind both routes, different guards.
`test_website_audit.py` asserts both halves, and that every exit from that one
fetch draws the card — a path that returns early leaves it spinning for ever
on a failure the card beside it has already reported.

**Two brand cards is the reader deciding which of our services to believe.**
The record drew the brand kit and, underneath it, a second block for what had
been read off the client's own website — so the same company's colours
appeared twice, in two sizes, under two headings, one of them tagged with the
name of the provider that answered. And for a local business the lookup
publishes nothing at all, which made the *upper* block the empty one: the card
led with **"No brand data on file yet"** directly above the logo it plainly
had. It is one card. `hub/client_brand._merge()` builds one set of tiles and
one palette from both sources, deduped on the hex, and the card asks
`has_brand` — is there anything to draw — rather than `found`, which is the
answer to *has a lookup ever run* and is what the button reads.

What does **not** merge is the claim. Each tile says where it came from — *on
file* or *seen on their website* — and `logos` is left exactly as it was,
because that is what `brand_guide_payload()` pushes to Suite and what
`io_prefill`, `landing_maker` and `client_context` read: merging is a thing
the card does for a reader, never a thing done to the data. Nor does the
wording carry the plumbing. Which of our tools did the reading is not a fact a
rep can act on — the note `modules/ads_builder/logo.py` already makes about
naming Brandfetch to somebody who cannot rotate its key — so the sources are
named as *their website* and *on file*, the date still travels with the
sighting, and the reference card underneath is titled by the question it
answers rather than by the audit that answered it. The audit itself keeps its
name, on the Site Health card, where opening it is the point.

**A contact strip that says "none on file" about a record holding the
address.** The name, the address and the phone number were read off the
client's own site and sat three cards down under a heading about our tooling,
while the strip at the top of the same record offered a blank form. Nobody
types a client's address in twice, so it stayed blank. `contact_observed()`
reads them and `contact_suggestions()` offers them **into the empty fields
only** — a value somebody typed is the better source and is never offered
over, the overlay rule `hub/client_urls.py` works to. They are drawn dotted so
an offer reads as an offer, one press keeps them, and nothing is written until
that press. Contact fields are gated on the record having *no* contact at all
rather than field by field: a contact row is a person, and dropping a phone
number read off a home page into the row holding the owner's name is us
inventing who answers it. First non-empty wins across the sources, and the
order is the point — the business describing itself on its own site beats the
Google listing address, which is the one most often out of date.

**Smart 1 sells in the US, and half the Hub was written in British
English.** `modules/scans/reports.py` has normalized the copy it lifts from
Insites for a while -- Insites writes British in its callback and American in
its own PDF -- and everything the Hub wrote itself was drifting the other way:
"colour", "behaviour", "licence", "organisation", "analyse" and "centre"
across forty templates, the help layer, and a proposal a client reads. Nothing
caught it, and nothing could: a British spelling is not a defect any check
here can see, because the page renders, the link resolves, and the English is
correct.

`tools/spellcheck.py` is the rule now and CI runs it. What it reads is the
**copy**, which is why Python is scanned as **string literals only, through
the AST**: a full-text pass reports `from hub.images import optimise`,
`client_key.normalise_name` and thirty other shared function names, which is a
rename of the codebase dressed up as a copy change. Four shapes are code
rather than copy and each has cost something -- a word touching `_` is a
snake_case identifier or an **external field name** (`colour_scheme` and
`pages_analysed` are Insites' spellings in Insites' payload, and correcting
them reads back on Client 360 as a site with no palette and no page count); a
word followed by `(` is a function; a word touching `/` is a URL segment, and
a route already in a browser's history is not a spelling anybody reads; and a
Python literal that is one bare lowercase token is a key, a stored id or a
tag. That last one matters most: the landing-page goal id `enquire` is written
into every page already saved and `colourful` is a Cloudinary tag on every
clip indexed before today, so **renaming those is a data migration wearing a
copy change**. Where a term genuinely had to move, the old spelling is still
*matched* -- `video_library.TAG_ALIASES` -- rather than re-indexing a library
to correct a label.

**And it could not see the one module that is not Python.** It scanned
`.html`, `.js`, `.css` and `.py` — and the Display Ad Builder renders a page a
**client** reads out of TypeScript, so the proof footer said *"Colours may vary
slightly"*, the delivery panel said *"organised by platform"*, and the AI copy
prompt told the model a proof point could be *"a licence"*. Ten thousand lines
outside every spelling rule in the Hub, reported by nothing, because the page
renders and the English is correct.

`.ts` is read the same way Python is — **string literals only**, so
`rasterise`, `normalise`, `optimise` and sharp's own `colours` option are not
reported and this stays a copy check rather than a rename of the renderer.
Four things it has to get right, and each is a way that goes wrong: a **`//` or
`/* */` comment is not copy**, the same rule docstrings follow; a **`${…}`
interpolation is code**, blanked to same-width filler rather than removed so
line numbers still land; a **regex literal is a pattern**, skipped whole, and
which of `/` is a regex or a division is decided from the previous significant
character, or the scanner ends up inside a string it never entered; and a
literal that is **one bare lowercase token** is a stored value — `focal:
'centre'` is what saved concepts carry and renaming it is a data migration, the
`_IDENTIFIER` rule one language over. The line reported is the **word's**, not
the literal's: `proof.ts` draws its whole page from one 400-line template
literal, and a report putting every finding on the backtick is one nobody can
act on. `test_spelling.py` asserts all of it against a sample that contains
each shape.

`ALLOW` is per file **and** per word, with the reason written down, and
`stale_allowances()` fails on an entry naming a file that is gone or a word it
no longer contains: an exemption that outlives what it exempted goes on
covering whatever is written at that path next, the failure
`check_stale_json_exemptions()` names. `test_spelling.py` hands the matcher a
sentence that plainly drifts and requires it to say so, because a check that
can be silenced by an edit somewhere else is worse than no check -- and it
started green, which is the only way it was worth adding.

Two things it deliberately leaves alone. **Comments and docstrings in Python**
are for whoever opens the file, not for a screen. And a **class token** is not
a spelling on the page -- though the ones that were already paired across a
template and its stylesheet (`.bub-grey`, `.pill.grey`) were converted with
their values, because converting one half of a pair is a pill with no
background.

**A link that exists and nobody can reach is a link nobody uses.** Client
Image Uploads has always been able to hand a client a page they upload their
own photographs through (`/tools/image-picker/pick/<token>`). Getting one meant
opening that tool, finding or adding the client, and copying the link out of a
row — so the two screens that actually need it offered nothing, and the assets
arrived by email. `modules/image_picker/provisioning.py` is that step done
once, from anywhere: it is on the Client 360 images card, in the IO Builder's
creative checklist, on both requirement PDFs and in the Suite payload, all
reading `_upload_link_for()` rather than each building an address of its own.

Four rules in it, and the first is the expensive one. **A gallery matches on
the derived client key or an exactly normalised name and on nothing else** —
a link that collects one client's photographs into *another client's* gallery
is worse than no link, so "Icon Solar Supply" is offered its own rather than
Icon Solar's. **Two candidates propose neither** and name both. **Creating is
asked for, not assumed**: `create=False` answers "is there one?" so a page can
show the state, and the PDF builders only ever read — a document is generated
repeatedly, often twice in a row for the client and internal versions, and a
gallery created on each of those is a side effect nobody asked for. And **the
base is trimmed to an origin**: a dispatcher-mounted module's
`request.url_root` carries its own mount, so pasting the picker's path onto
the IO Builder's root builds `/tools/io/tools/image-picker/…` — a 404 the
client meets and nobody else does. `request.host_url` is what that route
passes, and `_origin()` trims anyway, because `PUBLIC_BASE_URL` has held a
path before now.

**A client whose only trace is an insertion order was invisible on their own
record.** Client 360 is a view, not a table: it reads Knack's products and
website records and joins the Hub's overlays onto them. A business written up
on their first IO has neither until the campaign is set up in Knack — so the
day the record is most worth opening it came back empty, which reads exactly
like a name typed wrong. `hub/io_clients.py` registers them at submit and
`knack_data.search_client()` merges them, with a banner saying the cards are
empty because there is nothing to read.

**Only when they are genuinely new**: a client who resolves through
`hub/client_key.py` or the registry writes nothing at all, because a second row
under a name that already exists is how one company becomes two on every report
keyed on a client. It is an overlay and never a write to Knack — the day the
real record appears it wins — and the row is marked `source: "io"` /
`is_io_only`, never touching `source` or `is_house` on a Knack client, the
mistake the discovered-URL merge made once already.

**And "elsewhere" excludes this overlay, which is the whole subtlety.** These
rows are merged into `clients_registry.all_clients()`, so a naive check reads
*its own output* as proof somebody else already knew the client: the second
order for them would be silently dropped, and only once the registry's
two-minute per-process cache had refreshed — so it would pass in a test, work
on one gunicorn worker and fail on the other. A row we wrote is ours to update;
only a client nobody has registered is put to the "is this new?" test, and that
test is therefore asked exactly once per client. A registry that could not be
read counts as **known**, because refusing to register beats inventing a
duplicate of a client Knack holds and was briefly unable to answer for.
`test_client_uploads.py` asserts all of it.

**Twelve tools make an image and six of them recorded nobody.** Each wrote to
Cloudinary and filed a record of its own, and the question anybody actually
asks — *what have we made for this client?* — was answerable only from the
ones that named a client. `hub/image_audit.py` and **QA → Unattached Images**
audit both halves, and the second half is why a row count alone misleads.

**The stores** are counted and split into filed, unfiled and *not measured*.
**The producers** — the code paths that create, upload or let somebody choose
an image — are checked for whether they reach a client gallery at all: a tool
that has never filed anything has no unfiled rows to count, so it is invisible
to a data audit and reads as the cleanest tool in the building.

Two of the six were invisible in the worst way. **Page Image Optimizer**
shipped an ">>> INTEGRATION POINT <<<" naming three candidate writers —
`modules.seo_images.store.add_record` and two more — and not one of those names
has ever existed, so `_resolve_hook()` returned `None` from the day it was
written and every image it saved went to a private JSON file nothing reads,
with `archive_backend()` reporting *local* to a screen nobody read it on.
`modules/seo_images.add_archive_record` is the real name now. And
**`io_creative`** sat in `filing.KIND_LABELS` with no writer at all — the
`display_ads` failure this file already describes, one tool later.

The producer check reads the **AST**, not the text: several files here explain
this trap by naming `file_asset` in prose, and a text match reports the
explanation as the defect — the rule `hub/config.py`'s drift check gives at
length. It also accepts filing done **over the route**, because the IO Builder
uploads straight from the browser and files through
`/tools/image-picker/api/staff/file`; an AST-only check called the one tool
that does file its worst offender, and the module that *defines* `file_asset`
its second.

**A stock photo chosen for a client is copied, not linked.** A gallery row
pointing at somebody else's CDN empties itself the day that provider
reorganises, with nothing saying why — so `modules/stock_photos` stores the
file first and files the stored copy. Our own library is already in Cloudinary
and is filed as it stands.

**A video is not filed into an image gallery.** `SavedImage` models an image or
a raw file, so the Commercial Builder files stills and logos and deliberately
leaves the commercial, the spokesperson clip and the voice track in the
client's Cloudinary tree: a row whose thumbnail can never render is worse than
an absent one. And it files by client **name**, never by that module's own
slug — resolving a slug back to a client is a guess, and filing one client's
creative into another's gallery is the single mistake here that cannot be
undone by editing a row. No name, no filing, and the audit reports it.

**"A client or a lead" is two right answers, not one.** A client's work goes
to a gallery through `filing.file_asset`; a prospect's goes to that prospect's
own record through `hub/prospect.add_asset`, keyed on the lead id. A producer
check that demanded `file_asset` of everything would report the lead half as
unfiled — which is the exact thing this audit exists to find — so the filing
call is per producer. The `prospect` heading is in `SOURCE_LABELS` even though
those files sit outside a gallery today, because a conversion carries them
across and they would otherwise arrive in the new client's gallery as a bare
key under nothing.

**A report that cannot fix the row it names is a signpost.** Each unattached
image carries a client picker and one press writes both the tool's own record
and the gallery — reported **separately**, because "attached" and "attached in
one of two places" are different outcomes and one tick for both is how
somebody learns not to trust the tick.

**And the gallery's labels are a table, read by whatever renders one.** The
gallery template kept its own hand-typed copy, so a kind added since arrived
in a client's gallery as a bare key under no heading, sorted in with stock.
`filing.SOURCE_LABELS` and `source_tiers()` are the one source now; the page
gained a search and per-group chips with counts, and a filtered view says *N
of M shown* rather than reporting the whole as the part.

**And the other direction: what is in Cloudinary that no store knows about.**
Everything above audits what the Hub *recorded*; the account is the ground
truth for what *exists*, and an asset no store has a row for is invisible to
all of it — which is most of what the six silent tools produced before they
were fixed. `hub/storage.manifest()` was written for exactly this and had no
caller at all: its docstring says it "feeds the orphaned-asset audit", and
that audit had never existed. The third declared-but-unwired integration point
in this corner, after `RECORD_HOOK` and `io_creative`.

`reconcile()` is a **POST behind a button** — a paged Admin API call per
folder tree is billed and slow, and a GET that costs money is one a reload or
a prefetch fires without anybody asking. Four rules. An account that cannot be
listed is **not measured**, never a clean bill. A **store that would not
answer is named**, because everything it knows about would otherwise read as
an orphan — one outage turned into a page of false findings. A folder listing
that hit the cap **says it was capped**, since an undercount here looks like
good news. And the folders deliberately left out (`proposals`, `backups`) are
**named with the reason**: a folder silently missing from a completeness
report is the same failure the report is about.

**A proposed owner is a guess from a path, and says so.** `<folder>/<client
-slug>/…` is the shape three tools use, so it is read — resolved against the
real client list where one matches, and otherwise offered as *the folder it is
in, but no client of that name*. Nothing is applied without a press, and
`unfiled/` proposes nobody, because that is the folder a cut-out with no
client already lands in.

**Forty orphans is not forty presses.** `attach_many()` takes a selection and
one client, and every row still reports its own outcome — a bulk action that
returns one number hides the two that failed, the rule
`client_urls.accept_many()` works to. `file_orphan()` is its own function
rather than a branch of `attach()`: there is no store row to update, and the
absence of one is the finding.

**And the tile on the dashboard read that refusal as four noughts.**
`build_audit()` computes `measured` for exactly this, and the report page
draws it — while `scorecard()`, which copies eleven keys out of the same audit
for the dashboard card, dropped it. So a morning where the client list refused
drew **0 · 0 · 0 · 0** above System status: every client up to date on
creative, in four confident zeros, with `/qa/stale-creative` one click away
saying *Not measured*. Two screens answering one question differently, which
is the trap `by_client()` avoids one function later by returning a pair.

The card's own note says it fails quietly so the dashboard never goes down
when it cannot load — right about a fetch that fails, and exactly what made
this invisible, because **this fetch succeeds**. It draws dashes and names
which half refused now, since the client list refusing and every creative
store refusing are different outages; and it stays *visible*, because a card
that hides itself cannot be told from one that had nothing to report.
`test_qa_reports.py` sweeps every `_scorecard_*.html` that fetches for the
same branch, with an exemption list that is **empty** — every card on
`dashboard.html` already branches on `measured`, and this partial was the one
outlier.

**And a section heading was a client in the CSV.** Two spellings of "this
row is a heading" on one page: `active_clients`, `prospect_queue` and the
upsell report mark the **cell** `{"group": true, "tone": …}` and the renderer
draws a coloured band; `no_gtm` wrote a bare string and marked the **row**
`row_styles="sub"`, which draws grey text. Same concept, two treatments, two
reports apart — and neither of them legible to the **export**, which wrote all
eight of this page's headings out as data. Active Clients downloaded as 154
rows for a book of 151, three of them named *"Ending this month (15)"* with
every other column blank, in the file somebody takes to a meeting.

Dropping them is not the fix either, because on two of those reports the band
**is** the finding — *"Never audited"*, *"No website on file, so nothing to
audit"* — so a heading thrown away loses the only thing its rows say. The band
is lifted into a **Group** column and the heading row is not written, so
nothing is lost and the count matches the note.

**A heading carries a label and nothing else, and that distinction is what
kept the totals.** The Scorecards mark their **TOTAL** row `group` too — it
wants the same band on screen — so reading the marker alone drops the one row
somebody downloads that CSV for. `isGroupRow()` requires the rest of the row
to be empty, and it is the **one** reading of what a heading is, used by the
export and available to the renderer, because this page carried two spellings
already and neither reached the file. It is lifted out of the template and
driven in **node** against every report's real payload, the arrangement
`test_menu_layout.py` uses over `hub-crumbs.js`.

**A row with a cell no column names.** The renderer writes one `<th>` per
entry in `columns` and one `<td>` per cell, so `no_dashboards`' six cells
against five headings put its Add-dashboard button under the heading belonging
to the value on its left — and the CSV export, which writes `columns` as its
header row and the cells beneath it, gave every row an unlabelled trailing
field. The two functions that also emit an action cell head it `""`, which was
the fix already sitting two functions away. Both invariants are sweeps now:
one cell per heading on every report, and a handler on the page for every
action a row puts on a button — a button with no branch does nothing, which on
a report is indistinguishable from one that failed silently.

**A QA report is named for its finding, not its process.** "Image Audit" tied
with Image Creator on the bare query `image` and took the top slot off it —
`search_index` breaks an equal score alphabetically, so a name is a ranking
decision. It is **Unattached Images**, beside "No Dashboards" and "Stale
Creative". `test_image_audit.py` asserts all of it.

**A day carried through four functions and dropped in the fifth.**
`record_health.client360()` takes a day so a caller can ask what a client's
record looked like as of a date, and #338 carried it down through
`_seo()`, `seo.record_health()`, `blogs_health()` and `_days_since()` after
`test_client360_health.py` went red at midnight UTC on three assertions —
counts drifting by exactly one day while the product code and the test were
each right and only the wiring between them was not. `_blogs_state()` was the
one reader left on the wall clock, and it is the one that decides `state`: with
a day injected it answered **"current" beside an `overdue` of 1**, which is the
disagreement `blogs_health`'s own docstring says it exists to prevent.

**Half a threaded clock is the worse half**, because the three values that did
move made the one that did not look like a rule being wrong rather than a
parameter being missed — and it is invisible on every day the two clocks agree,
which is most of them. `blogs_health()` hands `_blogs_state()` the same day it
counts `overdue` from, so the two halves of one rule cannot answer to two days.

Two assertions hold it, and the second is the one worth keeping: **state and
overdue answer to the same injected day**, checked at a date far from the real
one because that is the only place they can differ; and **passing no day is the
same answer as passing the real one**, because a day threaded for a test that
quietly moves what production computes is the fix being worse than the bug.
That second one is itself guarded on the day not turning between its two
readings — unguarded it is the bug it was written to catch.

**A pill with four answers was a bool, and the page contradicted itself.**
The SEO client list draws four status pills per client. Three are a tick
somebody makes and are genuinely yes/no; `blogs` is *derived*, and `False`
covered both **"this client does not buy blogs"** and **"their plan is
behind"**. On this deployment's own book that drew a permanent red *"Blogs —
not yet"* on **16 of the 21** SEO clients, for a product they have never
bought, in the one column that says what to act on — the permanent-red failure
`hub/creative_evergreen.py` exists to undo. And the summary tile at the top of
the *same page* counts the **product** and read **"With blogs: 5"**, so the
screen said five clients have blogs and twenty-one are behind on them. Neither
figure is wrong on its own, which is why it stood: the `/api/db/structure`
versus `/api/integrity` trap, wearing a pill.

`client_status()` returns a `BLOGS_STATES` key now — `not_sold`, `none`,
`behind`, `current` — with the label beside it, and both screens read that one
function rather than each deciding from truthiness, so the list and the record
cannot come to disagree about who is behind. The client record already *knew*
the difference (`blogsVisible()` draws a "blogs are off" note) and drew its
dot without it. **`not_sold` is never reached by inference**: it is the state
that takes a row out of the queue, so a caller that could not look reads as
`none` — unknown owes a plan, and silencing a row on a guess is how a client
who is genuinely behind stops being chased. `/api/seo/checks` is handed the
same fact, or ticking *Setup* would move a no-blogs client's pill from gray to
amber and read as the tick having done something it did not do.

**A name nobody gave matched everybody.** `_client_websites("")` tested
`ck in wk`, and `"" in wk` is true of every string — so `/api/seo/detail?name=`
came back with the **whole 610-row website registry** attributed to a nameless
client, and `webs[0]` then supplied that client's "website", its GA id and the
domain its Brandfetch is looked up under. The `client_key.resolve()` rule about
substrings, one field along. The route refuses an empty name now, the way
`/api/seo/tasks` already did.

**And a record that could not be built rendered as a record with nothing in
it.** `/api/seo/detail` answered **200** with an `error` key — and `fetch()`
resolves for 4xx and 5xx alike, so the page's `.catch()` never saw it. Reading
straight past it, every card drew its own empty state: *"no site on file"*, no
business info, no schema pages. A client with months of work behind them,
drawn as a client with none, on the one screen that would have shown the work.
The page reads `error` before it assigns anything now, and the route answers
502, so the next reader of it does not inherit the same silence.

**Every route in that section filed its work under `hub`.** Schema, blogs,
FAQs, alt text, the publish instructions — all `audit.log("hub", …)`, and
`client_brand.NOT_WORK` calls `hub` housekeeping, so `work_log()` dropped the
lot and a client who had just had a month of blogs written read as a client
nobody had done any work for. The `display_ads` failure, one section later.
`check_work_kinds()` **cannot** see this one: it flags a module in *neither*
table, and `hub` is in one — so `test_seo_page.py` walks the call sites by AST
and requires every `seo`/`faq` event to log under a module the record can name.
The three helper modules beside them (`schema_questions`, `blog_images`,
`llms_txt`) had been logging under `seo` the whole time, so the section was
filing half its output as a deliverable and half as housekeeping.

**And the tickets that carry that work to the site deduped on the string
rather than the page.** `hub/seo_tasks.py` opens by forbidding exactly what it
did — *"It must never create the same ticket twice … A queue that fills with
duplicates is a queue people stop reading"* — and then keyed on the **raw
URL** while the title beside it was `_short()`. So the module already knew how
to reduce a URL to the page a person means, and used that only for what
somebody reads:

    https://acme.com/services          Add schema markup to /services
    https://acme.com/services/         Add schema markup to /services
    http://acme.com/services           Add schema markup to /services
    https://www.acme.com/services      Add schema markup to /services

Four tickets, one page, identical titles — unreadable *as* duplicates, which
is what made them worse than noisy. The URLs arrive from a crawled sitemap or
a list posted by the browser, so trailing-slash and www variation between a
crawl and a typed entry is ordinary, and an **http → https migration would
have duplicated the whole book in one pass**.

`page_key()` is the canonical form: **host and path**, because the store is
per client and a client with two domains would otherwise collide on
`/services`; query and fragment dropped, since `?utm_source=x` is the same
page to somebody adding schema to it. The **path keeps its case and the host
does not** — a hostname is case-insensitive by specification and a path
genuinely is not, and merging `/Services` with `/services` would silence a
ticket for a page that never gets its schema. A duplicate is noise in a queue;
an absence is work that never reaches the site, so the tie breaks toward the
duplicate.

**And every key already on disk is a raw URL.** Reading only the canonical one
would make each of them invisible and raise a second ticket for everything
already ticketed — a migration wearing a bug fix. `already()` matches the old
spelling as well, the rule `audit.LOG_NAMES` and `video_library.TAG_ALIASES`
already work to, and new records are written canonically so the fallback walk
is for old rows rather than the normal path.

**An editor rebuilt underneath somebody loses what they typed.** Two on the
SEO client record: the alt-text list and the FAQ draft. Both are a container
of live inputs redrawn with `innerHTML` — and the trigger is not the typing,
which is what makes it a different bug from the Smart 1 Ads target-area rows.
It is a **sibling row's** button and a **fetch that lands tens of seconds
later**. The FAQ half was the worse of the two: those inputs carry no change
handler at all, so half-writing an answer on question 3 and pressing Approve
on question 5 discarded question 3 immediately, in silence. Removing a focused
field does not fire `change`, so the alt half went the same way whenever the
AI write finished while somebody was typing in another box.

`faqHarvest()` and `altHarvest()` read the open editors back into the model
before any redraw, so a redraw cannot destroy what nothing has read yet.
Three rules on them. **Only a field somebody typed in is harvested** — a
`dirty` flag set on `input`, never a comparison against the model, because a
fetch response makes *every* field differ at once and harvesting on
difference would revert a whole page of freshly AI-written alt text to what
the boxes held before the write, which is worse than the loss it fixes.
**Cancel skips its own row**, or the harvest would keep the text that button
exists to throw away. And **an empty box is not an edit**: clearing a
question keeps it, the rule `savedit` already worked to, so a cleared field
cannot delete the answer behind it. `altSave()` sets `new_alt` from the typed
value *before* the request goes out, so a redraw mid-flight shows what was
typed and the harvest then sees nothing to write twice. `keepCaret()` puts
the cursor back, and costs the caret rather than the page when it cannot.
`test_seo_page.py` lifts both blocks and drives them in **node**, the
arrangement `test_menu_layout.py` uses on `hub-crumbs.js` — a copy restated
in the test would be a third thing to keep in step.

**None of it was checked, because the page redirects.** `tools/pagecheck.py`
names parameterized pages like `/prospect/none` explicitly, and `/seo/client`
was on no list: without a `?name=` the route **redirects to `/seo`**, so a
sweep of bare paths lands on the list, reports it green, and the largest
template in the Hub — 3,600 lines, drawn almost entirely from fetches — goes
unchecked while reading as covered. Both it and `/seo/webmaster` are named now.

**The mapping that every location-scoped feature waits on, in its own
store.** Reading a client's Forms, pushing their Social Planner posts, minting
a token at all — each needs one fact, which sub-account is theirs, and until
it is recorded the feature has no answer for that client. It lived on
`image_picker_clients.ghl_location_id`, a hand-typed column that exists
because somebody provisioned an upload gallery. That was fine while the
mapping was incidental and is the wrong home with the app installed across
several hundred sub-accounts: it couples **"this client has a Suite
sub-account"** to **"somebody made them an upload gallery"**, so mapping the
book through it would create hundreds of gallery rows as a side effect nobody
asked for — which `modules/image_picker/provisioning.py` is explicit about
refusing.

`hub/suite_map.py` is that store, and **nothing is migrated**: the old column
is still read, the way `audit.LOG_NAMES` and `video_library.TAG_ALIASES` keep
matching a spelling already on disk. **`suite_accounts.location_for()` stays
the one reader** — it consults the new store then the old column — because two
functions answering *which sub-account is this client* is how they come to
disagree, and on this question a disagreement puts one client's work in
another client's account.

`proposals()` pairs sub-accounts to clients on **canonical domain first, then
an exact normalised name, and never a substring**: "Riverside HVAC" must not
collect "Riverside HVAC Supply". **Two candidates propose neither** and name
both — two client records on one domain is the ambiguity that actually
occurs. **Nothing is written by looking**; `link()` is the press, and a
sub-account already recorded against somebody else is **refused by name**
rather than reassigned, because silently taking the newer answer is exactly
how the wrong page gets posted to. `accept_many()` reports every row's own
outcome, the `client_urls.accept_many()` rule. It reads `/locations/search`
rather than `ghl_oauth.installed_locations()` — that answers a different
question and carries no website, and the domain is the only field in a
location record that identifies a business exactly.

**A card that asks for nobody's data gets somebody's.** Client 360's forms
card fetches `/api/client/forms?name=…&period=…` and names no sub-account —
and `ghl_forms.summary()` fell back to `GHL_LEAD_LOCATION_ID`, which
`hub/config.py` describes as the sub-account *"leads are written into"*: Smart
1's own. A form belongs to exactly one sub-account, so there was **no code
path by which a client's own forms could reach that card**, and every client's
record showed the agency's form submissions under that client's name. Not an
empty card — a wrong one, wrong identically for all of them, which is why it
read as a feature that had not been finished rather than as a bug. The
location is resolved from the **client** now, through
`suite_accounts.location_for()`, and there is no default: a client whose
sub-account nobody has recorded has no answer, and saying so is the answer.

**And two more in the same module, each rendering as a tidy nought.** A form
whose submission count raised was dropped with `continue` placed *before* the
`skipped` tally, so it left no trace at all — when every form failed, the card
said *"No form submissions in September"* with `no_submissions: 0` and nothing
anywhere reporting that nothing had been read. And a previous period that
could not be counted was recorded as **0**, which prints "14 vs 0" and an
up-arrow over a comparison that never happened. `unreadable` is counted and
named beside `skipped` — *nobody filled it in* and *we could not ask* are
different sentences — and an unmeasured baseline is `None`, which makes the
aggregate baseline unmeasured too rather than comparing against a smaller sum
and reporting a rise that is an artifact of the failure.

**None of it was covered, which is how three failures shared one module.**
`test_ghl_forms.py` stubs `_get` and asserts what the module does with each
answer. Its own first draft then repeated the mistake it was written for: it
set `GHL_LEAD_LOCATION_ID` *after* importing `hub`, and `config.settings` is a
frozen dataclass built once at import — so the fallback field was `""`, there
was no agency location to reach, and *"nothing was asked of the agency's own
location"* passed against the unfixed code. The variable is set before the
import now and the test asserts the fallback is really configured first.
Reverted, eighteen checks go red; the `_delta` assertions are guarded because
the old code raises on a `None` baseline and an assertion that raises takes
every check after it out of the file.

**Absent data must read as "not measured", not zero.** A clean-looking zero
is a wrong answer presented confidently.

**A column named `query` on a Flask-SQLAlchemy model hides `Model.query` on
that one class, and nothing errors until the first read.** `db.Model` carries
the `query` descriptor every `Model.query.filter_by(...)` in this Hub reads;
`SEORecommendation` declared a **column** called `query` -- a Search Console
search term is the obvious thing to call one -- so `SEORecommendation.query`
answered the column's `InstrumentedAttribute` and `.filter_by()` on it raised
`AttributeError`. The model imported, `create_all()` made the table, and every
screen looked fine. Behind that one line: `_save_recommendations()` reaches
it unconditionally, so **every weekly Search Console refresh rolled back**
before the snapshot or the memory was written, with the error recorded on
the property row where no page drew it; the action queue and the client
overview answered **500**, and the overview page `await r.json()`-ed the
HTML, rejected unhandled, and left the Agency Action Queue blank -- not even
its own empty state; and the daily job folded each property's failure into
a list and returned normally, so the scheduler panel drew a **green pill
over a run that had refreshed nobody**. The only place it ever surfaced was
a traceback swallowed in `test_hub_help_layer.py`'s page sweep, which
requests every route and asserts nothing about a 500.

The attribute is `search_query` and the **column keeps its name**
(`db.Column("query", …)`), so the table already on the live Postgres needs
no migration -- the `audit.LOG_NAMES` rule, wearing a column. The wire key
stays `query` too, because the page reads `x.query` and nothing about the
JSON changed. `hub/integrity.check_shadowed_model_query()` refuses the next
one at **high**, from the AST -- prose is not a mapping -- and scoped to a
base spelled `Model`, since a classic declarative `Base` has no `query` to
shadow; `query_class` is deliberately not on its list, because assigning one
is how Flask-SQLAlchemy is *told* to use a custom query. The job **raises**
now when it attempted refreshes and landed none, because `_run_job` reads an
exception as a failure and a returned dict as success; one property failing
beside others that landed stays that row's own state, drawn on the overview
page with its error, or a job red for one client's revoked grant is the
check people learn to skip. `test_seo_intelligence.py` drives the refresh
against a stubbed Search Console, the routes, the page, the job's verdict
and the sweep -- and was confirmed red against the unfixed model first.

**Two blueprints must not offer a template of the same name.** Module Jinja
environments are separate *for a dispatcher-mounted module* — a blueprint
registered on the hub app shares the hub's environment, and that environment
resolves a bare name against the hub's own templates first and then each
blueprint's folder **in registration order**. So `render_template("index.html")`
does not necessarily get you your own: Calculators and Page Image Optimizer each
shipped a plain `index.html`, Calculators registers first, and
`/tools/page-images/` rendered the calculator index and 500'd on `'delivery' is
undefined`. That one at least announced itself. Calculators also shipped a
`leads.html`, which the hub's own `leads.html` outranks, so
`/tools/calculators/leads` answered **200 with the Hub's leads page in it** —
every template valid, every link resolving, nothing in any log. Name a
blueprint's templates so nobody else can claim the name: `tickets_*`, `picker_*`
and `commercial_*` already do, which is why those five were never caught by it.
`/api/integrity` has a high-severity check for it now.

**A blueprint-registered module is not behind AuthGuard.** `wsgi.py` wraps
each *dispatcher-mounted* app in `AuthGuard`; a module registered as a
blueprint on the hub app never passes through it, and the hub app has no
blanket gate of its own — its own pages are guarded view by view. Commercial
Builder had neither, so every page and API route in it answered 200 to anyone
with the URL, client names and briefs included, while the tile next to it
redirected to `/login`. The guard now sits on the blueprint
(`modules/commercial_builder/__init__.py`) rather than on 40 views, because
the next route added must not have to remember. `hub/auth.py` names this
failure in its own docstring; `test_commercial_heygen.py` asserts it.

**And three more were still open, because each fix was written out again
rather than shared.** Commercial Builder was fixed that way and so was
`modules/calculators`, which is two copies of one `before_request` and no
answer for the fourth blueprint. An anonymous sweep of the composed app found
ten routes across three more modules answering **200 to anyone with the
URL**: Web Tickets, its setup screen and its Knack field map; the Page Image
Optimizer and its saved-job archive; and Video Search with the Cloudinary
library, its search and its status. Every one of them is a staff tool sitting
on the Client Tools page behind a tile that redirects to `/login`, and nothing
anywhere reported the difference.

`hub/blueprint_guard.py` is that gate, once. `install(bp, mount=…,
public=…)` puts one `before_request` on the blueprint, so the route added
next month is covered without anybody remembering, and `public` takes the
same shape a dispatcher-mounted module's `PUBLIC_PREFIXES` has — a module
that later becomes mounted needs no second spelling of what is public. It
**never raises**: a module that cannot import `hub.auth` is one running
standalone, and refusing to start it would be worse than a gate not applying
where there is nothing to protect. And it redirects rather than answering
403, because the reader is a member of staff who followed a bookmark, and
`next` puts them back.

**Exempting a path from the login is only half of "public".** The hub app's
own `after_request` injects the sidebar, the help layer and the feedback tab
into any HTML it returns, so a client-facing blueprint path needs an entry in
`CHROMELESS` as well — the two halves `modules/commercial_builder`'s review
routes already carry separately. Either one missing is its own failure, in
opposite directions: login-exempt and chrome-bearing is a client reading our
staff nav, chrome-exempt and guarded is a sign-in form in front of somebody
who will never have an account.

**The check is a sweep, not a list of the three we fixed.** A test naming
those modules proves nothing about the next blueprint. `test_blueprint_guards.py`
boots the composed app, requests **every** route it serves with no
session at all — the parameterized ones too, which for a long time it skipped
and which are where every client-facing surface in this Hub lives — and
requires each one it reaches to be in an allowlist that says *why* it is
public — the crawler files, the health probes, the chrome's
own scripts, the help registry, the Suite SSO frame, the calculator embed,
the nine landing pages, the MSA signing page. A new open route fails the run
without anybody having thought to add an assertion for it. The allowlist is
held to the rule `check_stale_json_exemptions()` works to: an entry naming a
route that no longer exists, or one that is not actually reachable, fails
too, because an exemption that outlives what it exempted goes on covering
whatever is served at that path next.

**And a sweep can quietly stop sweeping, which is worse than not having
one.** That check reached the mount table with
`getattr(wsgi.application, "mounts", {})` — and `wsgi.application` is a
`ProxyFix` wrapping `NoIndex` wrapping `ErrorMirror` wrapping the
`DispatcherMiddleware` that actually holds it. So the **default answered**,
the walk found no mounts at all, and the sweep covered the hub app and its
blueprints while reporting that it had asked the composed app: 199 routes
where there are 415, with every landing page, Smart 1 Ads, the Proposal
Builder and Site Scans never asked. It passed, which is the whole failure —
the same shape as a drift check regexing calls that had become a table and
reading no groups as a clean bill of health.

`_dispatcher()` unwraps to whichever layer holds `mounts`, so the next
middleware added to `wsgi.py` cannot switch it off, and **finding none is a
failure** rather than an empty sweep. The count is asserted against what
`wsgi.py` mounts *and* four prefixes are named, because a set of the right
size and the wrong contents is the same failure one step on.

**Read and write are different permissions.** The same version asked GET and
nothing else, so a POST that creates, sends or deletes was never asked at
all. Both are swept now, against **separate** baselines: a page a stranger
may look at is not a form a stranger may submit, and one list would let an
entry written for a readable page cover a route that writes. A **400 counts
as reached** — the route answered and the guard is not what refused, which
is what an open write looks like when an empty body happens not to satisfy
it. Nothing but an empty JSON body is ever sent, so the sweep creates no
rows.

**A provider job is not done when the call that started it returns.** HeyGen
renders a spokesperson clip in minutes, so `POST .../spokesperson` hands back
a job id and nothing else. Nothing polled it, so the scene kept
`asset_type="spokesperson"` and an empty `asset_url` forever — and because no
QC check asked whether a scene owned an asset at all, `creatomate_service`
built an element with no `source` and the finished commercial carried a blank
segment with nothing reading as an error. Attaching the clip is the *status
route's* job, not the browser's: any request for it writes the finished URL
onto the row, so closing the tab no longer loses the clip. The same shape
applies to any provider added later.

**AI video animates a frame; it does not conjure one.** Runway's Gen-4 models
take a starting image — there is no usable text-only path — and return clips
of **5 or 10 seconds and nothing else**. Both constraints are in
`config.runway_duration()` / `runway_ratio()` rather than spread through the
service: a scene is requested at the shortest clip that *covers* it, and a
scene over 10s is refused with its own length named instead of being handed a
clip that stops early. That is also why "Generate AI" (OpenAI stills) and
"Generate Video" are two buttons — the still is the input to the video, and
`openai_service.write_runway_prompt()` sat written and uncalled until it was.
QC fails a scene whose clip is shorter than the scene, because the element
simply runs out and the segment goes black with nothing reading as an error.

**A key that is set is not a key that is read, and neither is a key that
works.** Every provider in the Commercial Builder degrades to mock data rather
than erroring, which is right — and is also what makes a misnamed key
invisible: concepts come back from a template, stock search returns
placehold.co images labelled like footage, the voice track is silent, and the
render is a job id with no file behind it. ElevenLabs and Creatomate read
`os.environ` at *import* under one spelling each, on a deployment that sets
`PEXELS_API`, `PIXABAY_API`, `HEYGEN_API` — so adding `ELEVENLABS_API` and
`CREATOMATE_API` would have changed nothing at all, with every screen healthy.
Every key in the module now reads through `hub/config.py` at call time, and
`/api/integrity` has a check that names any module still reading one spelling
directly. Runway was quieter still: it had a working service and a real key
check, and the dashboard drew it from a separate "V1.5" list as a hard-coded
grey chip, so connecting Runway could not change what the page said about it.

`bool(key)` is also the weak question. A truncated paste, a revoked key, an
account out of credit and a key from the wrong workspace all look identical to
it, and all fail at the moment somebody is waiting on a render.
`services/provider_check.py` asks each provider — one cheap authenticated
call, nothing generated, nothing billed — behind a **Check keys** button
rather than on page load, because eight outbound calls per visit is a slow
dashboard. Its four rules are the ways that answer goes confidently wrong: no
key is *not measured* and never a cross; refused (401) and unreachable
(timeout) are different answers, and calling the second one "bad key" sends
somebody to rotate a key that was fine; a 404 means this file is out of date,
not that the key is bad; and a result never carries the key value, because it
is rendered into a page and pasted into chats.

**A library with no scope is the whole account, and it reads as a deep
library.** Video Backgrounds counted and searched bare `resource_type:video` —
every video in the Cloudinary product environment. On the account it was built
against that put 33 clips of genuine stock footage in a row headed "Clips in
Cloudinary" beside a client's solar spots, a chiropractor's social cuts, an
internal rebate explainer and four of Cloudinary's own demo files, on a tool
whose entire job is footage you may put behind a headline. Nothing errored; the
number was simply about a different question from the one the heading asked.
`video_library.FOLDERS` is an allowlist of folder trees now — `Smart 1 Ads` and
`Video Backgrounds`, each including everything beneath it — and it scopes the
counts, the search *and* `index_asset`, which takes a public_id from its caller
and is therefore the path that goes round a search filter.

Four things it has to do that a filter alone would not. The clause asks for
**both** `asset_folder` and `folder`, because Cloudinary publishes a folder
under the first in dynamic folder mode and the second in fixed, and asking for
only the wrong one returns zero with every screen healthy. It matches the
folder itself *and* `"<path>/*"`, since neither alone catches both a clip
sitting in the folder and one in a subfolder. It goes in **second**, straight
after `resource_type`, because a comparison clause after a negated one is a
parse error in that expression language and `pending_expression()` already
carries the scars. And a folder **named here and absent there** is reported by
name — `folder_report()` is tri-state, and the dashboard calls it an *error*
rather than printing a count — because a renamed folder, a `CLOUDINARY_URL`
pointing at another product environment, and nobody having uploaded anything
yet all render as `0` and only two of them are something to act on. An empty
allowlist refuses rather than widening back to the account: a scope that fails
open the moment somebody deletes a line is worse than no scope.

**And scoping it made the indexing gate the bug.** Indexing was forward-only —
right when the in-scope library was thirty nameless supplier clips, and exactly
wrong once the scope became the two folders that already hold the real footage:
every clip in them would have been permanently unsearchable while the page
reported a healthy count beside a zero. `INDEX_BACKLOG` is on and
`index_backlog()` runs on `hub/scheduler.py`, twenty clips an hour under a
**wall-clock budget** — scheduler jobs share one thread and a vision call has no
useful ceiling, so a count limit alone lets one slow batch hold up every job
behind it.

Two things fall out of that. **A clip that fails comes straight back**, so
without a ceiling one unreadable file costs a vision call an hour for ever, and
every individual run looks like a normal batch that happened to have one
failure in it; three attempts are counted in the state file and then the clip
is given up on **in writing**, because a give-up held in memory forgets itself
on the next deploy. And the give-up marker cannot be a second tag: Cloudinary's
expression language takes exactly one trailing `-tags:x` here, and both
alternatives are worse than a parse error — `-(tags:a OR tags:b)` parses and
returns **nothing**, while `... (scope) NOT tags:a` parses and returns the
**whole account**, folder scope silently discarded. So "described" and "given
up on" are one `SEEN_TAG`, the sweep negates that, and *search* still filters on
`INDEX_TAG` so a given-up clip is skipped by the sweep and invisible to search
rather than surfacing undescribed. `waiting_count` and `undescribed_count` are
on the page beside the other two, because "3,900 clips, 40 indexed" cannot say
whether the sweep is moving or has stopped.

**A picker that lists only what somebody typed into it cannot pick a
client.** The Commercial Builder's Start page offered a `<select>` of
`cb_clients` — its own table — which is empty on a fresh install and only ever
holds businesses somebody has retyped. On a Hub whose client book is several
hundred businesses in Knack, "pick an existing client" was a thing the page
appeared to offer and could not do, so a client of eleven years' standing got
entered as new and the finished commercial was filed under a name that joins
to nothing: no products, no scans, no Client 360 card, no logo or phone number
that were on file all along. `modules/commercial_builder/client_link.py` is
the join, and it inherits `modules/ads_builder/client_link.py`'s rules rather
than restating them — look the client up, never match on a substring, never
store the derived key, and name a source that could not be read, because
"no such client" and "we could not reach the client list" send somebody to two
different places and only the first means *create them as new*. Adopting is a
copy and deliberately a one-way one: the brand profile fields on top of it —
fonts, pronunciation, preferred voice — exist nowhere else, and nothing is
ever written back to Knack.

**A tool that produces creative for a buy, and never asks the specification
that buy is sold under.** The Commercial Builder rendered finished video for
CTV, YouTube and social and never once consulted `hub/creative_specs.py` —
which was answering that identical question for the IO Builder's upload
manager and the client galleries the whole time. The kit sells Connected TV at
**15–30 seconds**, so a `:05` or a `:60` CTV cut is outside the buy, and both
are lengths the Start page offers: the only way to discover it was to build
one and have a platform refuse the delivery. `qc_service` asks the kit now, at
QC *and* at the moment a length is picked (`spec_preview`), because length and
aspect ratio are what creative gets refused over and both are decided before a
frame exists. Three rules in the mapping. **A "both" buy must satisfy every
channel** — one file runs on CTV *and* YouTube, so a cut half the buy refuses
is not a pass — while a **social buy is bought per network**, so one that takes
it is a real pass and the networks that would refuse are *named* rather than
dropped ("runs on Meta and TikTok, but not Snapchat — 60s is outside the 3-30s
the kit allows"). And a **format the kit maps no unit for is "not measured"**,
never judged against the nearest channel: a 1:1 cut of a CTV buy is not a
placement anybody sells, and it is said so even when it rides alongside a
format that passed. `SPEC_KIT_URL` is in `hub/creative_specs.py` beside the
numbers, printed on every verdict, because the source of a refusal should be
one click away rather than folklore. It is a URL and **not a fetch**: a spec
table pulled live changes what a check says with no diff to point at, so the
numbers stay transcribed.

**HighLevel publishes no QR endpoint, and the question that actually matters
is whose scan it is.** `hub/qr_codes.py` settles both so nobody has to ask
again. HighLevel renders QR codes as a funnel/website page element and exposes
no v2 API returning one, so there is nothing to call — and a code that lives
inside a funnel page stops working the day that page is unpublished, on a spot
that runs for a quarter. The image stays local (`qrcode`, no key, no expiry).
What was genuinely missing is the half HighLevel *does* decide: a client with
a Suite sub-account owns their scans, and a business we are pitching does not,
so theirs are filed to Smart 1 Marketing — which is where `hub/leads.py` puts
their contact already. Getting that wrong breaks nothing and makes the campaign
report quietly wrong for the whole flight. Three rules. **A destination is
never invented** — no `https://<clientname>.com`, no favicon-scraped guess; with
nothing on file the code is refused and the field that would fix it is named,
the rule `modules/ads_builder/logo.py` works to, because nobody proof-reads the
thing that scans. **Tracking rides on the URL, not in a shortener** — a
shortener is a second service that has to still be running in a year — and a
parameter already on the destination *wins*, because a landing page handed over
tagged was built that way and overwriting it re-attributes traffic somebody is
reporting on. And **`attribution()` is tri-state**: filed to their own
sub-account, filed to the agency, or *not measured* because the Suite is not
configured at all — a tick over the third tells somebody scans are being
counted when nothing is counting them.

**`gpt-image-1` returns `b64_json` and never a `url`.** "Generate AI" read
`resp.data[0].url` unconditionally, so on this deployment both options came
back empty — and the picker drew Option A and Option B exactly as it would for
a success, with clicking either reporting "This option failed to generate" and
nothing anywhere saying why. Only the older `dall-e-*` models return a hosted
URL, and that one expires within the hour regardless. Both shapes resolve to a
data URL now, and a failed option carries **its own** error rather than the
batch collapsing into one: asking for two and getting one is ordinary (a
content refusal on one prompt, a timeout on the other), and reporting the whole
thing as failed throws away the option that worked.

**Two buttons that read as alternatives, and are two halves of one job.**
"Generate AI" makes a still; "Generate Video" animates the still it made.
Runway has no usable text-only path, so the second cannot run before the first
— and they sat side by side as peers with nothing saying so. They are one
numbered pair now (`1 · Make a frame → 2 · Animate it`) and the second is
*disabled* until the scene has a frame, because a button that explains itself
only after being pressed has already wasted the press.

**A wizard step that is four jobs is three jobs nobody finds.** The Commercial
Builder's Storyboard step carried the scenes, the Voice Studio, Music and the
CTA Builder on one page — so the QR toggle, the switch deciding whether a CTV
spot has any response mechanism at all, sat below everything else on the
longest screen in the tool. It is **Blueprint → Voice & music → CTA** now, in
the order the work is done: a voice is cast against a script, and a CTA card is
built to hold whatever the last scene turned out to be. `routes/pages.STEPS` is
the one description of the sequence and `_stepper.html` draws from it — five
templates each carried their own hand-typed copy, and the one that got missed
said "4. Storyboard" on step five of seven. A finished step is a **link**, or
"change the voice" is three presses of Back. `/storyboard` still redirects:
that URL is in browser history, and a wizard step that 404s reads as the whole
tool being broken.

**The checks belonged where the work is.** Every QC check is about something on
the Blueprint screen — a scene with no footage, a clip shorter than the scene
it sits in, narration outside the word budget — and all of them lived on
Preview, two steps later. Pressing Render then re-ran the identical set, so the
tool answered a question it had just been asked. They run on Blueprint now, and
a **recommendation is drawn amber rather than red**: a page of red is a page
people scroll past, which is the note `hub/templates/diagnostics.html` already
carries about a resolved finding in an open finding's colour. `scene_assets`
was also missing from the Preview panel's label map entirely — and a key absent
from that map is skipped **silently** by the render loop, so the one check that
catches an unfinished scene never appeared on the panel it was written for.
`test_commercial_wizard.py` asserts both maps are complete against what
`run_qc` actually returns.

**A script writer that sizes the read once is why a :60 came back thin.** A
:60 has room for about 150 words; nothing would ever write the extra hundred,
and typing them by hand turned the word count red because nothing re-measured.
Expansion is its own call and **budget-aware in code, not in the prompt** —
`narration_budget()` computes the room and the model is told the number,
because one asked to "write a bit more" writes a bit more whether there were
four words of room or forty. With no room it **refuses in words** rather than
appearing to work and changing nothing, and a locked scene is never rewritten
under somebody.

**One casting question, asked by two tools.** The Commercial Builder's Voice
Studio was a flat `<select>` of every voice on the ElevenLabs account, in
whatever order the API returned it, with nothing to listen to — so the answer
was always whichever name came first. The Radio Promo builder, against the
*same account*, asks what the read should sound like and offers three ranked
voices with a sample on each. `hub/voice_casting.py` is that question now and
both read it; `modules/radio_promo/voices.py` re-exports the old names so its
callers are unchanged. `elevenlabs_service.list_voices()` had been discarding
`labels` and `preview_url` — enough to fill a dropdown, not enough to rank
anything or play a sample, which is the whole reason one tool could do this and
the other could not. The scoring is a **ranking, never a filter**: an account of
cloned voices carries no labels, and coming back empty from a question that was
answered perfectly well is wrong — so `match_quality()` says which of "no
voices at all", "these carry nothing to match on" and "a real ranking" happened,
and the screen prints it.

**A platform choice that only changes the crop is not a platform choice.**
Social is its own platform in the Commercial Builder, not a third aspect ratio,
because a 9:16 render of a CTV spot is still a CTV spot: it opens on a slow
establishing shot, carries a QR code nobody can scan on the phone they are
holding, and argues its case aloud on a feed that plays muted. So the platform
drives the **beat structure** (`SOCIAL_STRUCTURE_TEMPLATES` — the hook is one
beat and it is at zero) and two checks that are code rather than prompt text,
for the reason `hub/blog_spec.py` gives about a client's "never mention" list.
QR is *required* only where nothing can be clicked, which is CTV: reporting its
absence on every social spot is how a warning stops being read.

**The audit line that stopped every render there has ever been.**
`submit_render` opened with `audit.log`-style detail built from `project.name`
and `project.length` — attributes `CommercialProject` does not have; it has
`title` and `length_seconds`. So the f-string raised `AttributeError` at the
top of the route, **before** the QC gate and before Creatomate, and every
render this tool has ever been asked for returned a 500. The browser got HTML,
`CB.api` could not parse it as JSON, and a three-second toast said "Bad
response from server" over an empty panel: press Render, nothing happens,
nothing in any log. This is `audit.log()`'s first-positional trap one step
further on — `hub/audit.py` swallows what it is *given*, and the caller
evaluated the arguments before the swallow could apply, so the guard that was
supposed to make logging safe never saw them. The detail is built **inside**
the try now, and the call moved after the commit so a spot refused by QC is no
longer logged as submitted work.

**One size at a time, and approving is what files it.** Rendering every ticked
format at once means the second and third are built from a storyboard nobody
has watched — so a note on the first applies to two cuts already paid for. And
`check_render`
used to copy the finished video into the client's Cloudinary library the
moment Creatomate said "succeeded", before any human had seen it: a cut nobody
has watched is not a deliverable, and one already sitting in the client's
gallery is one somebody can send. Filing is `POST …/approve` now, it reports
the Cloudinary write and the activity-log write **separately** for the reason
`hub/domain_links.py` gives, and a mock render — which reports success and
produces no file — is refused rather than filed as a delivered commercial.
Approval state is `cb_render_approvals`, its own **table**: `create_all()`
creates missing tables and never adds a column to an existing one, so an
`approved_by` on `cb_render_jobs` would be silently absent on the live
Postgres with every local test green.

**And that rule is about unwatched creative, not about batching.** It stops
being true the moment a cut of the spot has been approved: the remaining
formats then come off a storyboard somebody has signed off, which is exactly
the condition one-at-a-time was protecting — so a rep with three sizes left
was pressing the same button three times and waiting each time for no reason
the rule could name. The gate is an **approval on the project**, not a count.
Before one exists a second format is refused *by name*, with what would lift
it, because a route that quietly rendered the first of three would be the old
failure wearing a new response; and a size already approved inside a batch is
refused rather than dropped from it, since a silent skip is how an approved
cut is quietly replaced — or quietly not replaced — with the panel reporting
the same success either way. Whether the batch is open is `can_batch` on
`/render-jobs`, decided by the route that enforces it: `preview.js` kept an
`ADVISORY` set of its own once already, and a second reading of a server rule
is the copy that drifts.

**A client answering a review reached the activity log and nothing else.** So
the rep who sent the link found out by opening the spot and looking — the
emailed-MP4 arrangement this whole feature replaced, minus the email. There is
no mail sender in this Hub, so `review_spec.inbox()` is the
`hub/social_content.py` answer: a card on the tool's own dashboard, above the
provider row because a key that is set is housekeeping and a client waiting on
a reply is work, with every figure opening the rows behind it rather than a
screen the reader then has to filter. Four rules in it. **An answer is not
only a decision** — a client who left four timecoded notes and pressed no
button has answered, and dropping them because no `outcome` row exists loses
exactly the reply somebody needed. **A round sent after a filing is a live
question again**, so `_acted_on()` compares the approval's time against the
round's rather than asking whether the project has *an* approval: reading it
the other way drops the round somebody is waiting on, silently, from the one
card that would have told them. **Rounds still out with the client are counted
apart** — nobody here is holding those up. And **four empties, not one**:
nothing waiting, nothing yet sent, everything acted on, and a table that could
not be read are different situations, only two of them mean there is nothing
to do, and `inbox_unmeasured()` is what a failed read answers with rather than
a clean zero.

**The module that spends the most was invisible on the usage page.** HeyGen,
Runway and Creatomate all bill per generation, and none of them was recorded
anywhere — while `quotas._PROVIDER_MARKERS` knew four providers and none of
these three, so there was no check that could ever have named the gap. That
is why it stood: the thing that would have caught it had nothing to catch it
with. The OpenAI **image** path was the same story one level down —
`hub/ai.note_sdk_usage()` records the text calls by reading `.usage`, which an
images response does not carry, so two billed options per press went uncounted
while every chat call was tracked.

All four record now, and all three have markers, so `untracked_provider_calls()`
and `test_api_usage.py` fail on the next one rather than understating the bill.
Three rules on the new rows. **Runway is counted in seconds**, because it bills
by duration — counting requests would make a :10 clip cost the same as a :05,
which is the mistake counting ElevenLabs renders rather than characters would
have made. **A refused call keeps its row** with `ok=False`: it spent nothing
and is out of every billable total, but a wall of them is what a spent
allowance looks like from this side. And **no ceiling is invented** — none of
the three publishes a plan figure this deployment can cite, so each row reads
*not measured* against a limit and still says what was spent.

**A price nobody published is a price this tool would be inventing.**
`modules/commercial_builder/cost_spec.py` answers what a spot will consume
*before* it is built — the decision that moves the number is three lengths or
one, AI video or stock, and it is made on the Start page, where nothing said
anything. Every figure is a count in the provider's own unit, and a dollar
figure appears **only** where the Hub already holds a published rate, which
today is OpenAI's image price and nothing else, read from
`hub/quotas.IMAGE_PRICING` rather than restated. A total that quietly covered
two of five rows would be the same confident low number, so the unpriced rows
are **named** beside it. The shot count comes from `abcd_service.shot_targets()`
— the same table the Blueprint scores against — so the estimate and the thing
it estimates cannot drift. It is what the tools consume and never what a client
pays; `hub/rate_card.py` is the other thing, and the caveat says so on every
render of it.

**A rendered cut is not a delivered one.** The dashboard lists the 25 most
recently touched projects, which answers *what was I working on*; the Spot
Library answers *what have we actually made*, and those are different rows
sorted on different things. It reads `RenderApproval` rather than a succeeded
`RenderJob`, because a render that succeeded is a file nobody has watched —
the distinction `approve_render` already draws, read from the other end. It
filters server-side and reports **both** numbers, since a filtered list quoting
an unfiltered total is the wrong answer with two right ones either side of it
(the SEO gallery's "Showing 1 of 7"). A spot whose Cloudinary copy is missing
says so rather than offering a link to a provider URL that expires.

**One field held two questions, and answered neither.** `COMMERCIAL_TYPES` is
a single-select mixing how a spot gets **made** (`stock_vo`,
`ai_spokesperson`) with what it **is** (`testimonial`, `promo_sale`,
`seasonal`), so "an AI spokesperson testimonial" was unsayable and the concept
writer was told half of what a rep had decided.
`modules/commercial_builder/library_spec.py` splits them — and
`commercial_type` keeps its column and its meaning, because `create_all()`
adds no column to an existing table and `compliance_spec` reads that value (a
`testimonial` engages 16 CFR 255). The archetype lives in the **brief JSON**,
and `LEGACY_ARCHETYPE` reads the five narrative values as the archetype they
always were, so nothing is migrated — `hub/target_areas.from_legacy()`'s rule.
`archetype_for()` returns `(key, source)`, because *a rep picked this* and *we
inferred it from a column that meant two things* are different confidences and
the screen says which rather than drawing a selection nobody made.

**An archetype is a promise about what the client has to supply.** Twelve of
them, each naming what it is good at, what it is bad at, and what it **needs**
— a testimonial needs a customer who has agreed; a before-and-after needs the
BEFORE, which nobody photographs because at the time it was just a Tuesday.
`readiness()` turns those into an advisory QC finding, so an archetype nobody
can supply surfaces while it is still free to change rather than at the shoot;
`hub/creative_needs.py` asks the same question one medium earlier. `NEED_KEYS`
is derived from the table rather than typed out, so an archetype that gains a
need is saved by the route without anybody widening a list. Each also names
which published regimes it tends to engage — read by nothing, since
`compliance_spec.py` scans the finished copy and is the authority, but worth
knowing before the script exists.

**A pack is creative data; `hub/industries.py` is the media plan.** Hooks,
what proof looks like, stock vocabulary, the shape of the offer, what falls
flat. Different data, same clients — so where an industry exists in both it is
the **same id**, and `test_commercial_library.py` asserts it, because two
taxonomies for one client is the year the two proposal builders cost. Four
packs are Commercial Builder-only (`hvac`, `solar`, `medical_dental`,
`home_services`), for categories the Proposal Builder has no page for.

**A wrong pack is worse than none**, because it reads as research somebody did
rather than as a gap. `pack_for()` is tri-state — matched, unmatched, or
nothing recorded — and `prompt_guidance()` carries that state to the model
with an instruction not to invent a category it was not given. What suits a
category is a **suggestion and never a filter**: an unusual spot for a
category is often the reason it works, and a picker that hides nine of twelve
makes that impossible, the rule `hub/voice_casting.match_quality()` works to.

**And the choice has to change something.** `check_spec()` names any archetype
or pack field read by nothing, the way `current_marketing.unanswered_keys()`
does — this module shipped four discovery questions read by nothing, so a rep
could answer all four and the document came out identical. It returns an empty
list today, which is the only way it was worth adding. Mock concepts reflect
the archetype too, because mock mode is where a developer forms their
impression of whether a field does anything at all.

**A tool that renders finished video and never asks what the rules require.**
`testimonial` is a commercial type on the Start page and the offer field
invites exactly the copy Truth in Lending triggers on — "$79 a month", "0%
APR", "no money down" — and nothing anywhere asked. The first person to find
out was whoever had to answer for the spot after it ran.
`modules/commercial_builder/compliance_spec.py` holds five published regimes
as data — Reg Z (12 CFR 1026.24), the FTC's endorsement guides (16 CFR 255),
FINRA 2210, attorney advertising (ABA 7.1–7.3 as each state adopts it) and TTB
(27 CFR 4/5/7) — each carrying the citation and the authority, the
`abcd_service.py` rule for the same reason: a citation is an argument a client
cannot talk a rep out of, and "our tool thinks you need a disclaimer" is not.

**It never says a spot is compliant, and that is the whole design.** Whether
an ad complies is a judgment about a specific spot in a specific state, made
by somebody qualified — and a green tick over that question is worse than
silence, because the tick is what somebody relies on. Every finding is phrased
as *this engages X* and never as *this violates X*: the first is a fact about
the copy and the second is a legal conclusion. `summary()` says "nothing in
the copy engaged one of these rules", never "passed".

**Nothing here blocks a render.** `QR_CODE_RULES` paid for that lesson — a
check that refuses the correct thing is a check somebody switches off, and
switching it off costs every finding it would have raised. What a finding does
instead is require an **acknowledgment** before a rendered cut is *filed*: one
explicit "I have read what these require", recorded against a name in
`cb_compliance_acks`, the shape `hub/creative_needs.py` uses for a comp
confirmation. A shared-password session cannot give one — "Shared login" is a
true statement about the session and a useless one in a record whose entire
value is the name on it, the `hub/ad_copy.py` refusal.

**A sign-off is about the copy as it was.** `findings_key` fingerprints the
rule ids and the quoted evidence, so rewriting the offer retires the
acknowledgment and the panel says it was **superseded** rather than absent —
"nobody has looked" and "somebody looked at a different script" are different
situations and only the second has a name to go back to. It is keyed on the
evidence rather than the whole payload deliberately: rewording a `requires`
sentence in that file is our edit, not the client's copy changing, and must
not silently invalidate every sign-off on the book.

**An empty industry is not an unregulated client.** Three of the five regimes
are decided by who the client is, and `cb_clients.industry` is free text that
is often blank — so `industries_engaged()` returns `(regimes, known)` and a
blank reads *not measured*, with the panel saying "that is not the same as
them not applying". Reg Z is the opposite and is detected from the **copy**
alone: a furniture shop advertising "$40 a month" engages it and a bank
advertising its brand does not.

**A rule that fires on every spot is a rule people stop reading**, which is
the same note. "20% off everything" is the commonest line in retail copy and
is not a rate of finance charge, so the credit word is *required* beside the
percentage. Every pattern also carries its match through to the end: the first
draft quoted `recovered $` and `$40 a mo`, and evidence a reader cannot find
in the script is not evidence.

**Two things are deliberately absent.** The **FTC CARS Rule** (16 CFR Part
463) was vacated in its entirety by the Fifth Circuit in January 2025, so
flagging it would raise a rule that does not exist — it is in `NOT_ENFORCED`
with the reason, named rather than silently dropped so nobody adds it back
from memory. And **fifty states** are not encoded: attorney advertising is
genuinely state-by-state, so `state_bar` says which state's rules govern is
the first question and does not pretend to answer it.

**A rep approving a cut is not the client approving it.** Filing was a rep
pressing Approve & file; the client saw the spot when somebody emailed an MP4,
replied with three changes in the body of an email, and a person retyped them
into the storyboard. So nothing recorded which cut the client approved, who at
the client approved it, or what they asked for on the round before — which is
fine right up until a client says "we never signed off on that".
`modules/commercial_builder/review_spec.py` is that question and
`routes/review.py` is the two doors onto it.

**Both halves of "public" had to be written out, because this module is a
blueprint.** `modules/ads_builder` and `modules/scans` declare
`PUBLIC_PREFIXES` and `wsgi.py`'s `_mount()` hands it to *both* `AuthGuard`
(so a client with no login can open the page) and `HubBar` (so the sidebar is
not injected into it). Commercial Builder is registered as a blueprint on the
hub app, so nothing in `wsgi.py` ever sees it: the login exemption is
`review.PUBLIC_PATHS`, read by the guard in
`modules/commercial_builder/__init__.py`, and the chrome exemption is a
separate entry in `hub/__init__.py`'s `CHROMELESS`. Either one missing is its
own failure — a page exempted from the login and not from the chrome is a
client reading our staff nav, and the other way round is a login form in front
of somebody who has no account and will be emailed the file instead.

**And a dispatcher-mounted module fails it the other way: by declaring
nothing at all.** Commercial Builder is a blueprint, so both halves had to be
written out by hand. Fan Radio is mounted, so `_mount()` would have handed
one list to *both* `AuthGuard` and `HubBar` — and it was called with no
`public_prefixes` argument, so it handed them nothing. The module's own
docstring had said since the day it was written that `/r/…`,
`/api/public/…` and `/audio/…` are the customer's; `wsgi.py` had never been
told. So the approval link a rep mails a client opened a **staff sign-in
form** for an account they will never have, the page's `<audio>` element
404'd behind the same redirect, and the approve button posted into it.
Nothing errored at either end — a redirect is a perfectly correct answer to
a question nobody had asked, and the rep who tested the link was signed in,
which is the one state in which it works. The other half was armed and
waiting the same way: with the list finally passed, `HubBar` is what keeps
the sidebar, the help layer and live links to Client 360 and `/sales/leads`
off a page a customer reads. `PUBLIC_PREFIXES` is declared in the module and
read by `wsgi.py` with `getattr` now, the arrangement `modules/scans`,
`modules/ads_builder` and `modules/sales_builder` already use, so the mount
and the module cannot disagree about what is public.

**Neither radio store read `HUB_DATA_DIR`.** Both carried their own copy of
the six-line data-root expression — the thing `hub/jsonstore.data_root()`
exists to be the single reader of — and neither consulted the variable at
all. On this service it is unset, so they agreed with every other module by
luck; on a deployment that sets it, every radio project would land outside
the root the database mirror keys against and `/api/backup` reports on,
while everything else moved. Both go through `jsonstore.data_dir()` now.
`test_radio_builders.py` asserts all of it, and asserts the two speech
passes' **divergence** rather than the identical reading `fan_radio/speech.py`
used to claim: Radio Promo says numbers as words and spells a web address
and an email out loud, and Fan Radio does neither, so a spot whose whole
call to action is the website is handed to the voice raw. Named rather than
quietly merged — one shared reader is what closes it, and it changes what a
client hears.

**A music bed that generated nothing, on the tool whose whole output is
sound.** `modules/radio_promo` is the general radio builder — fifteen tones,
the brief read off the client's own site, a matched :15/:30 written to the
clock, ElevenLabs casting, measured runtime, a one-button tighten. Its Music
step saved a **text prompt** and the builder then played three oscillators at a
pitch chosen by a regex over that prompt. So a rep described a bed, pressed
Listen, heard a tone, and filed a spot with **silence under the voice** — the
placeholder failure `qrcode_service` paid for on a CTV end card, one medium
over, and invisible from both ends because every screen reported success and
the thing that was missing is a thing you have to play the file to notice.

The module's own docstring said why it was like that, and it was true: *"What
did not [carry over]: ffmpeg music beds and loudness mastering (no ffmpeg in
the Hub runtime)."* There is still no ffmpeg, ffprobe, pydub or numpy here.
**Neither half of the job needs one.** A bed is *composed* by ElevenLabs at the
spot's own length — `services/elevenlabs_audio_service.py` was built for
exactly this and its own note already said the audio-only path reaches it as
it stands — so nothing is ever trimmed to fit. And the voice is mixed over it
**in the browser**, through the Web Audio API, which decodes both tracks, ducks
the bed under the read and hands back a WAV.

**Which is what makes the length honest.** A WAV states its sample rate,
channel count and data length in its own header, so `radio_spec.wav_seconds()`
is arithmetic on the bytes we stored — measured by us, not reported by the page
that made them, which is the `_dimensions()` rule in `modules/bg_remover`
wearing a stopwatch. An uploaded **MP3** is the opposite case and says so: it
is at a bitrate nobody here chose, so its length is *not measured*, never a
number and never zero. The mix route refuses anything that is not a readable
WAV rather than filing a deliverable of unknown length, and it ignores a
`seconds` field the page supplies — `test_radio_ads.py` posts a 29.9s file
claiming 30.0 and requires 29.9 to be what is filed.

**The encoder and the probe are two implementations of one format, and if they
drift nothing can be filed at all.** So `toWav` is lifted out of the template
and driven in **node** against `radio_spec.wav_seconds()` across mono, stereo
and two sample rates — the arrangement `test_menu_layout.py` uses over
`hub-crumbs.js`, for the same reason: a copy restated in the test is a third
thing to keep in step.

**The dB pair is Commercial Builder's, read and not restated.**
`config.ducked_db()`'s own note says two lookups of one table is how the panel
and the render come to disagree about how loud something is — so a radio spot
and a video spot duck their beds by the same amount, from the same table, and
`hub/radio_spec.py` keeps **no fallback copy of it**. The consequence is stated
rather than discovered: where that import fails, `available()` says so and the
step refuses, because a bed mixed at numbers nobody published is worse than a
step that says it cannot run. The test matches those numbers as **numbers**,
not as substrings — the first draft's `"-9" in source` was true of the
character class `[A-Za-z0-9-]` in the phone-number pattern and reported a file
with no dB literal in it at all, which is the false positive that gets a check
switched off.

**"Licensed bed" was the wrong check, and provenance is the right one.** The
build spec asked for a blocking check against a catalog of cleared tracks.
There is no such catalog — a bed is composed on demand or uploaded by whoever
is making the spot — so what actually protects a client is `bed_source`: real
audio, with a source recorded against it. A **described-only** bed blocks and a
**mock-mode** bed blocks, because both ship as silence; a mock bed is refused
at the door rather than saved and blocked later. And **no bed at all passes**,
because a sponsor mention and a news-style read are ordinary radio spots that
ship without music, and a check that refuses the correct thing is one somebody
switches off — which here would cost the call-to-action check with it.

**And the route that made those beds is gone rather than kept.** Once the
builder composed instead of describing, nothing posted to
`/api/projects/<pid>/music-beds` any more — and the only state it could still
produce is a bed with no audio behind it, which is precisely what `bed_source`
now blocks. Keeping a write path whose only product is a state the checks
refuse is keeping a way to make the mistake, and its docstring had already
started claiming a role in a flow that no longer called it. The **rows** are
not orphaned: a project on disk carries `music_beds`, and the Music step offers
each of those descriptions as a one-press prompt to compose from, so words
somebody wrote before any of this existed are reachable and now worth
something.

**`not_measured` is never folded into `pass`**, and `measured` excludes the one
row that is deliberately reserved. Loudness and clipping need a decoder this
runtime does not have, so `vo_clarity` is a row that says so rather than a row
that is absent — an absent row is a report shape that changes the day somebody
adds the check. Counted into `measured`, it would make that flag False on every
spot ever built and therefore say nothing, which is the assertion that cannot
fail wearing a QC report.

**Nothing here refuses a render; filing is what needs a reason.** `QR_CODE_RULES`
is the precedent. A blocking finding answers **409 with the report** rather
than filing quietly, and an override is available — recorded against a name,
with the reason required, because an override nobody can explain later is not a
record.

**The call-to-action check exists because both platforms this was specced
against report the same finding**: the commonest reason a self-serve radio spot
underperforms is that it never says what to do next. A phone number, a web
address or a code, with the **matched words quoted** so a reader can find them
in the script — and the spoken form counted too, since `speech.py` spells a web
address out loud and a script through that pass carries no dot at all. It
refuses to fire on ordinary copy: a zip code, an area code, a year, a founding
date. A check that fires on every spot is one nobody reads.

**The browser has to fetch both tracks to mix them, and a CORS refusal is a
button that does nothing.** So both are read back through one same-origin route
whose allowlist is **the project's own row** — a `ref` names a slot and a role
and the URL comes from what this service already recorded. Nothing takes a URL
from the caller, which is the rule `assets.generatedImagePath()` in the ad
builder had to be given after a path in a POST body could lift any readable
file into a web-served folder.

**A variation carries the choices and never the audio.** The intake, the brief,
the tone, the pronunciations and the scripts come across; a rendered read and a
finished mix do not, because audio of the previous wording filed under a new
name is the wrong file and it plays perfectly well. The lineage is on both
rows. What is *not* there is the denylist that was written beside the
allowlist: it named spots, mixes, beds, versions, banner, share, feedback and
pushed, and **`store.create()` already refused every one of them** — a second
guard that cannot fire reads as the mechanism and is not one, which is the
shape this file counts six of, so it is gone and the test asserts the store
instead.

**The :60 is opt-in, and its budget is not the one the spec asked for.** Each
length is a model call and a slot somebody then has to record, so a project
still writes the :15/:30 pair unless it says otherwise, and a row saved before
that field existed reads as the pair rather than being migrated. The spec asked
for **150–180 words**; at the house pace of 2.6 words/second that `speech.py`
holds, 180 words is a **69-second read** — a :60 written to the top of that
range cannot be recorded inside its own slot, so it comes back over, gets
tightened, and the budget that sent it there was ours. It is **140–170**, the
same deliberate overshoot the :15 and :30 already carry.

**And the numbers stopped being written down twice.** `renderCopy` carried 42
and 85 as literals and the AI system prompt stated the budgets in prose, which
is why a :60 could not be added without editing three places. The prompt line
is derived from the table, the template reads the table the server sends, and
the JSON shape the writer is asked for is built from the slots in play — so the
model is never asked for a length nobody wants. Its token ceiling scales with
the words asked for, anchored on the 1400 the pair has always used, because a
ceiling sized for two scripts truncates three and a truncated response arrives
as an **empty text body** rather than as the ceiling it is.

**One setting the browser read was served by nothing.** `lead_in_ms` — the
moment of bed before the read starts — was read as `(M.lead_in_ms||0)` and sent
by no route, so the bed began on the same sample as the first syllable on every
mix, and the `||0` is what hid it. Fixed, and then swept: `test_radio_ads.py`
reads every `M.<setting>` the template touches and requires each to be a key
`mix_defaults()` actually returns, because a one-off fix does not catch the
next one.

**A read that overruns is never trimmed to fit.** The mix is rendered at the
longer of the slot and the voice, so a long read comes back **measured and
over** and the length check names it — trimming clips the end off the phone
number, which is the rule `grade_duration()` already states about a render and
`save_links()` states about an archive. A bed shorter than the spot is
**reported rather than looped**: a loop puts an audible seam in the middle of a
client's commercial, and saying the last few seconds carry no music is
something somebody can act on.

**Two things from the build spec are deliberately not here, and both are the
same decision.** The spec's step 6 is a **client review link** and its step 7 a
**Suite notification** on mix-ready, and each is written in the spec itself as
"reuse the shared thing once it exists" — `hub/review_share.py`, and the
Commercial Builder's render-completion workflow. Neither exists. What does exist
is `modules/fan_radio`, which has a complete client approval surface for a radio
spot already: one share link per project, a random token, no login, `noindex`,
approve or comment per spot, feedback landing back against the spot it belongs
to. Building a second one here would be two client-facing approval pages for
one medium, differing in whatever each remembered — which is the two-proposal
-builders failure this file opens with, and the reason this work went into
`radio_promo` rather than into a third radio module in the first place.

So the honest next step is **not** a share page in `radio_promo`: it is lifting
Fan Radio's out into the shared layer beside `hub/radio_spec.py` and having
both tools read it, at which point the notification has one place to hang off
too. That is its own piece of work with its own test, and it is named here
rather than half-started, because a second half-built approval flow is worse
than one tool having the only one.

**A public_id that names a slot has to replace what is already there.**
`upload_asset` passed `overwrite=False` on every call, and the public_ids it
builds are deterministic — `mix-thirty`, `bed-thirty`, `<slot>-<voice>` — so
re-mixing or re-recording one slot lands on the asset the last attempt wrote.
With overwrite off, Cloudinary keeps the **old bytes** while the store records
the **new measured length**, so the file a client is sent and the duration filed
against it disagree; and where Cloudinary refuses outright, `upload_asset`'s own
`except` drops the asset onto the persistent disk instead, which is wiped on
every redeploy, reporting a clean success either way. The disk branch has
always overwritten, which is the other half of the same inconsistency. This
was **already true of the re-render path** before any of this work, so that one
is fixed with the four new ones rather than left as the older defect it is.
`test_radio_ads.py` sweeps every `upload_asset` call whose public_id carries a
slot — matched on **balanced parens**, because the regex the first draft used
stopped early on the multi-line calls and passed "every one of them" against
three of five, which is the sweep that quietly stops sweeping.

**And Fan Radio had none of that second half, while its own README said it
did.** That module opens by saying it is "built to the same shape as Radio
Promo: same tone list, same word budgets, same pronunciation pass, same
ElevenLabs casting and measured runtime, so a script can move between the two
tools without re-timing." Four of those five were a **local copy**, and three
had drifted — silently, because each screen was internally consistent and the
only way to notice was to open both tools on one client.

**The word budgets were the sentence's own subject and were not the same.** A
:15 was 30–38 words in Fan Radio against 35–42 in the Radio Ad Creator, and a
:30 was 65–75 against 65–85. So a script that read *on the clock* in one tool
read as short or long in the other, on the number a rep writes to. And the two
lengths either side of the pair — the :10 sponsorship tag and the :60, the one
radio length with room for a story rather than an offer — were **unbuildable
here at all**, which is the finding `test_radio_parity.py` already records
about the Radio Ad Creator, one tool later. `hub/radio_spec.DURATIONS` is the
one table now and `modules/radio_promo/catalog.py` re-exports it under its old
names, the arrangement `voices.py` already used over `hub/voice_casting.py`:
one table, and no call site had to change.

**The pair is still the default, and that is where the old reasoning actually
belonged.** `test_radio_builders.py` used to argue that Fan Radio need not sell
a :60 because "a :60 is not a unit anybody buys" on a football daypart — a
judgement about the buy, made by the tool, in the one place it could not be
argued with. The menu offers all four and `DEFAULT_LENGTH_IDS` is the pair,
because ticking four lengths across three dayparts is **twelve billed model
calls on a job that wants six**.

**The casting question was five tables deep and missing two of its own
answers.** `modules/fan_radio/voices.py` carried its own `CHARACTERISTICS`,
`ACCENT_ALIASES`, `ENERGY_WORDS`, `DELIVERY_WORDS` and scoring pass — with no
`neutral` voice type and no `transatlantic` accent, so the model could
recommend, and a rep could pick, two answers the matcher had never heard of.
Same client, same ElevenLabs account, two shortlists with two sets of reasons
under them and nothing saying which to believe. It reads `hub/voice_casting.py`
now, and the AI prompt's own vocabulary is **derived from that table** rather
than typed beside it, which is how it drifted in the first place.

**And the duration estimate behind every render was wrong.** Fan Radio's
`_mp3_seconds` advanced **four bytes** per candidate sync word rather than by
the frame's own length, so it counted sync patterns *inside* frame data as
frames and reported a multiple of the real duration — on the number a rep reads
to decide whether a read fits its slot. `hub/radio_spec.mp3_seconds` is the one
reading, and `test_radio_ads.py` drives it against a fixture whose frame body
deliberately contains a byte pair that looks like a sync word: padded with
zeros instead, the broken reader and the correct one agree, which is a fixture
hiding the defect it was written for. That one passed a first version of the
check.

**`pronunciation` was on every project row since the day it was written and no
route ever set it.** `decorate()` reads it on every spot and it could only ever
be empty, so every render went out with whatever ElevenLabs made of the
business name. The declared-and-never-wired failure this file counts six of, on
the one thing a client notices immediately when it is wrong. It is saved and
**applied to every spot** rather than only the ones written afterwards — a
pronunciation added halfway through a job would otherwise apply to half of them
with nothing on screen saying which — and it is stored `{"from", "to"}`, which
is the shape `speech.normalize_for_speech()` has always read: `{"word", "say"}`
would have been the identical failure one level on. *Show me what the voice
reads* prints the copy ElevenLabs is actually handed, because until that line
existed a pronunciation that was not taking looked identical to one that was
and the other way to find out was to spend a render.

**The music half is `hub/radio_spec.py`'s, which is what that module was
written for.** Its opening paragraph says so in as many words — the bed
vocabulary, the mix levels, the length arithmetic, the QC checks and the one
honest way to measure a finished file live there "so `modules/fan_radio` can
read the same rules later without a second copy of them being written first."
So a bed is composed by ElevenLabs at the spot's own length, the mix is
rendered in the browser and measured off its own WAV header on the way in, the
dB pair comes from the Commercial Builder's one music table, and a blocking
finding answers **409 with the report** rather than filing quietly.

**The unit is the spot, not the length, and that is the one thing that could
not be copied.** The Radio Ad Creator keys its beds and mixes on a slot because
a project there writes one script per length. A Fan Radio project writes
several spots that share a length and differ by daypart and outcome, so a bed
keyed on ":30" would be the same music under the pre-game and the post-game
read. They live on the spot's own row.

**A mix must not outlive what went into it**, and the rule is in `decorate()`
rather than at the four routes that can change a script — an edit, a rewrite, a
tighten and a pronunciation save — because a rule three of four call sites
remember is not a rule, and that is the one function all four already pass
through. The **read is marked stale and never deleted**: it cost money, it is
still the right voice, and a player that vanishes reads as a fault. The **mix
is dropped**, because it plays perfectly well while being of the wrong script,
which is exactly what makes it worth removing rather than flagging.

**The trademark scan runs before a mix is filed as well as before a render is
paid for, and it is the one finding an override cannot clear.** A script can be
hand-edited after a read was recorded, and the mix is the file a client is
actually sent. An override is a judgement about a length; shipping somebody's
mark is not a judgement anybody here gets to make.

**And the customer hears the mix.** `public_view()` prefers it over the raw
read, falls back to the read while a bed is being composed, and says **which of
the two is playing** — a player that switches between them silently reads as
the file having changed under the client. Two fields were added to that
allowlist and nothing else: no URL, no level, no override reason.

**Two things are deliberately not carried across, each with its reason.**
**Voice cloning** stays in the Radio Ad Creator: it creates a voice on the
shared ElevenLabs account out of somebody's recordings, which is a consent
question rather than a button, and one place to answer it is the right number —
a voice cloned there reaches this tool through `/api/voices/by-id`, which is
also how a cloned voice is reachable at all, since it carries no labels for the
ranking to score. And **the script QC panel** (`modules/radio_promo/qc.py`)
stays there too: Fan Radio's copy screen already runs a scan of its own — the
trademark verdict, the result-neutral rule, the football language detected —
and a second named panel beside it would be two readings of *is this script
ready* on one screen, which is the trap this file counts a dozen of.

**The tile is the Radio Ad Creator; everything underneath it is still
`radio_promo`.** The mount, the help keys, the log name and the Cloudinary
folder do not move — renaming the mount breaks every link in a rep's history,
renaming a help key orphans the bubble, and renaming the log name orphans every
row already on disk. It is the `billboard` and Video Search rule, and
`test_menu_layout.py` is what holds the tile's name against the trail that
names it.

**Three answers, and the fourth state is not an answer.** Approve/reject
forces "yes, but fix the phone number" into whichever end is nearest, the rule
`modules/ads_builder/spec.py` arrived at for the paid-search estimate; the
vocabulary here is the one every video proofing tool uses, so a client who has
reviewed video before already knows what it means. *No answer yet* is grey and
is deliberately not a decision — "not sent", "sent and ignored" and "they said
no" are three situations and only the last is a rejection.

**The most restrictive answer wins, because the link gets forwarded.** The
marketing manager sends it to the owner and both reply. Taking the latest
answer lets a colleague's "looks good" overwrite the compliance officer's "you
cannot say that", after which the cut ships. `verdict()` resolves by
precedence rather than recency, keeps every reply with the name against it,
and says when they *disagreed* rather than merely how many answered — one
person approving and three people answering with one refusal read identically
once they have been collapsed into a single word.

**A refusal blocks filing; an approval-with-changes does not.** Filing is what
puts the video in the client's library and on their 360 record, so a cut they
explicitly refused must not get there. Blocking the middle answer as well
would teach people to answer "approved" to get past the gate. It is a 409 with
an override rather than a wall — a rep who has settled it on the phone must
not be stuck behind a rule the client has already moved past — and the
override is written into the activity log *as* an override, because a record
that does not say so is one nobody can reconstruct. A project nobody sent for
review files exactly as it did before: an internal-only sign-off is still how
most of these are built.

**The round cap stops the agency, never the client.** Four rounds, drawn on
the client's page as `Round 2 of 4` because somebody who can see they are on
the last round asks for everything at once. A fifth is **flagged**, not
refused: turning the client away from the page means the rep emails them the
file, and every note, name and timestamp goes back to being untraceable. Same
shape as `QR_CODE_RULES` — a check that refuses the correct thing is a check
somebody switches off.

**A new round is a new link.** A link that has been answered is the record of
that answer, so reopening it for round two would overwrite round one's
decision with no trace there had been one; the previous round is revoked so a
client working from an old email cannot answer about a cut that has been
replaced, and its answers stay on file. Revoked, deleted and never-existed all
return the same 404, the rule `modules/ads_builder` settled for the estimate.

**A comment carries a timecode, and no timecode is an answer.** "The phone
number is wrong" and "the phone number at 0:12 is wrong" are different pieces
of work, and only the second can be actioned without watching the spot again
to find it. `at_seconds` is nullable on purpose: a note about the music is not
at a timestamp, and storing it as `0.0` files every general comment at the
first frame where the reader looks for something that is not there.

**`review_spec.py`, not `review.py`.** `__init__.py` does `from .routes import
(..., review)`, which binds the name `review` **on the package** — so a spec
module of that name beside it is invisible to `from . import review` in any
sibling. Nothing errors at import; the first call to a function that is not
there is where it surfaces, and it cost the filing gate a 500 before the
rename. `hub/proposal_spec.py` and `hub/blog_spec.py` carry the suffix for the
same kind of reason. `test_commercial_review.py` asserts both names resolve to
what they should.

**A walkthrough drives one page, and this one walked seven.** `hub-demo.js`
does not navigate: it rings the current step's selector on the page you are
standing on, and `perform()` opens with `if (!node) return`. So
`commercial_builder.first_spot` — nine steps across the whole wizard, offered
by the floating button on **every** screen in the module because nothing set
`data-demo="off"` — drew no ring and did nothing, nine times, from wherever it
was pressed. Not one of its nine `data-demo` hooks existed in any template at
all. That is the Smart 1 Ads failure verbatim, and `ads_builder` had already
paid for the fix: **split it per screen**, because a walkthrough drives one
page. It is `start_a_spot` on the Start page and `blueprint` on the Blueprint —
the two screens a rep works in — with every other screen opted out, and the
Blueprint carrying its own `[data-demo-start]` because the launcher offers a
module's *first* scenario and a page holding that attribute is skipped by it.
A billed step is `simulated` so the button is never drawn: a walkthrough that
spends money on a press somebody made to learn the tool is the one thing it
must not do.

**And it was describing a tool that no longer exists**, which is worse than
describing none — a rep believes a walkthrough. It walked a Storyboard step
the wizard had replaced with Blueprint / Voice / CTA, offered lengths with no
:06 in them, said "eleven checks" where `run_qc` returns twenty-four, and
called a QR code **required** with QC **hard-failing** without it, which is the
exact rule `QR_CODE_RULES` reversed. `test_commercial_explainer.py` asserts
against each of those by name, and asserts no count is quoted at all — a number
in that copy is a number that drifts.

**A screen is offered its tour only where the registry has steps of its own.**
`hub/help.tour()` falls back to the **module** prefix when a screen has none,
which is right for serving a tour somebody asked for and wrong for deciding
whether to offer one: named that way, a screen with no steps draws all
seventeen of four other screens' steps over elements that are not on the page,
ring anchored to nothing, narration reading confidently. `has_tour()` is the
exact question, and `_layout.html` asks it rather than drawing `data-screen` on
the truth of a name — guarded `is not defined`, the pattern every helper call
here uses, so a Jinja environment that never got the global loses the guard
rather than the page.

**Several lengths are built :30 first, not shortest first.**
`config.BUILD_ORDER` is `[30, 15, 6, 5, 60]`. The :30 is the length the others are
cut down from, so getting it approved first means every later cut starts from
creative somebody has signed off; the :60 is last because it is the most
expensive and the first to be dropped when the budget lands. Approving one
hands back the **next spot's Blueprint** rather than leaving somebody on a
finished Preview screen — `_next_in_campaign()` — because a campaign of three
lengths where two never get built is the ordinary outcome otherwise.

**A :06 is a unit, not a rounding of the :05.** Google Ads caps a bumper at
six seconds, and the spec kit already carried `youtube_bumper` at `(0, 6)` —
so a :06 is the longest cut that still buys bumper inventory and a :05 leaves a
second of it unbought. Both are offered because some CTV and social bumper
slots are sold at :05, and neither substitutes for the other. It is its own
row everywhere the lengths are data: `VO_WORD_TARGETS[6]`, two beats rather
than three (`Hook`, `Brand` — there is no room for a middle), its own social
structure, and a place in `BUILD_ORDER` between the :15 and the :05.

**Shots sit inside beats, and a Scene row is a shot.** A :30 built one scene
per beat is three shots averaging ten seconds; Google open-sources the
evaluator it machine-scores YouTube creative with, and that detector's own
quick-pacing threshold is **two**. So the script model is asked for beats each
carrying several shots, `_shots_from_beats()` recomputes the timing from the
beat spans rather than trusting what comes back, and the narration lands on the
first shot of its beat. Every shot carries `beat`, `beat_index` and a
`shot_no` numbered in **tens**, the way an edit list is — a shot inserted
between 20 and 30 becomes 25 rather than renumbering the board.

Each also carries **grammar**: a size, an angle and a move, from closed
vocabularies (`SHOT_SIZES`, `SHOT_ANGLES`, `SHOT_MOVES`). Not decoration — the
two things downstream of a shot are a stock query and a Runway prompt, and both
are the difference between "technician working" and "close-up, low angle, slow
push". The lists are closed because a value from neither reaches a search box:
`_clean_grammar()` replaces an unknown one with the default rather than writing
it through, and the merge is into `asset_meta` rather than over it, or changing
a camera angle drops the shot out of its beat — the `set_music` trap again.

**A threshold with no name on it is an opinion.**
`services/abcd_service.py` holds the published numbers as data and every row
carries **whose** it is, because "your average shot is ten seconds and Google's
own detector wants two" is an argument a client cannot talk us out of, where
"our tool thinks this is slow" is not. Four sources, named in `SOURCES`: the
ABCDs Detector's `configuration.py`, Amazon's Streaming TV guidance, Roku's
windows, and **`house`** for the one thing nobody publishes.

That last one is the rule the module exists for. No platform states a minimum
type size for 10-foot viewing — the guidance stops at "minimize text, prioritize
voiceover" — so a number there would be ours wearing somebody else's name.
`HOUSE_LEGIBILITY` is kept out of `THRESHOLDS` entirely and says in its own note
that it is not a platform rule.

Three more rules. It scores the **plan**, not a rendered file, because a pacing
problem found on an MP4 is a re-render and one found on the Blueprint is free.
A rule a plan cannot answer — face size, logo size, both of which need pixels —
is **not measured** and never a tick. And **a bumper is scored on none of the
pacing rules**: cutting a :06 to a two-second average is three cuts a second,
which is a strobe. The CTV brand window is judged against **Amazon's** 3s
rather than Google's 5s, because passing the looser rule and being refused by
the buy is the failure worth avoiding. `MEASURED_LIFT` sits beside it as
guidance and fails nothing — it is a sales table, not a gate.

It is its **own route** (`/<id>/abcd`) rather than a slice of `/qc`: the panel
updates as shots are edited, and QC makes an OpenAI call for the spelling pass,
so re-running the set on every camera-angle change would be a model call per
keystroke.

**A QR code is required nowhere now, and that is the fix rather than a
loosening.** `QR_CODE_RULES["required_platforms"]` is empty and
`default_on_platforms` is `["ctv", "both"]`; the QC check is advisory. The
reason is Amazon: **Amazon Streaming TV supports no QR code at all**, and its
own creative guidance says an ad should not carry call-to-action elements that
encourage clicking, because there is nothing there to click. A check that
refused to render a perfectly correct Amazon spot is a check somebody switches
off — and switching it off costs the CTV default too.

So the requirement became a default and gained the one thing that makes a
default safe: **something that says when it is wrong.** `CTV_PUBLISHERS` is the
smallest possible publisher field — a "Which streaming platforms?" multi-select
on the Start page, shown only on a CTV buy, driving no targeting and no spec.
It drives one warning, and `PUBLISHER_RULES` is where a publisher's refusals
are data. Nothing ticked says **nothing**, rather than reading as an all-clear:
absence of a publisher is not evidence of permission, and a rep who named none
has told us nothing about Amazon either way.

**A logo bug is not a QR code, and one fact had four readings.** Which
lengths carry a persistent logo bug is `LOGO_PERSISTENCE_RULES`, and
`logo_persistence_eligible()` was written to read it and called by **nothing**.
QC and the CTA route asked `qr_eligible()` instead — right by coincidence,
since both tables hold the same three lengths, and a change to where a QR code
makes sense would have moved where a logo bug is drawn with neither table
saying so. `creatomate_service` asked a fourth way, `length_seconds != 5`,
which had already stopped agreeing the day the **:06** arrived: only the CTA
route's own gate kept a :06 from rendering a logo bug that QC would
simultaneously report as not applicable. They are separate questions with
separate reasons — a QR code is a response mechanism and needs seconds on
screen to be scannable, a logo bug is brand recall and needs none — so they
are asked separately, of their own tables. A **bare literal is the reading
that cannot be kept in step**, because nothing points at it. And the copy is
derived: the panels said *":05 bumpers"* and went on saying only that after
the :06 was added, describing two lengths while naming one, so
`short_form_phrase()` names them from the table.

**A placeholder QR is worse than no QR.** `generate_qr()` failed soft when the
`qrcode` package was missing, handing back a placehold.co image of the letters
"QR" marked `_mock`. Nothing read that mark, and `is_available()` had no
caller — so the placeholder would be stored on the CTA and rendered onto the
end card of a CTV spot, where the code is the only response mechanism there
is and **nobody proof-reads the thing that scans**, the rule
`hub/qr_codes.py` refuses to invent a destination for. It also walked straight
past the check written for exactly this: `_check_qr_code` blocks a code that
is enabled and not generated, and a truthy placeholder is indistinguishable
from a real one to that test. Nothing is invented now — no image, and the
reason named on the CTA — so the blank says *why* rather than reading as a
button nobody pressed. The dependency is pinned and installed, so this
fallback has never fired; it is the same shape as filing a mock render as a
delivered commercial, which `approve_render` already refuses.

**And the QR upload beside it had never once run.** That fallback was
hypothetical; this was live on every spot ever built. `routes/projects.py`
hands `cloudinary_service.upload_asset` a **BytesIO**, and that function took
a path or a URL: `str()` on a BytesIO is `<_io.BytesIO object at 0x7f…>`,
`open()` raises `FileNotFoundError` on it, and its own `except Exception`
turned that into a quiet `{"secure_url": None}`. So `qr_image_url` was never
populated, and the failure was swallowed a **second** time at the call site by
an `or` that never read `error`. Both readers fall back to `qr_data_url`, so
what reached Creatomate as the image `source` was a base64 data URI rather
than a hosted one — and whether it accepts those is not a thing this repo can
answer, which is the point: the intended path was dead and nothing said so.

`_read_bytes()` is the fix and it is also the migration `hub/storage.py`
exists for — `storage.put()` has always taken bytes, so the shared service
could do this the whole time. Three rules on it. A **file object is rewound
first**, because a caller that has already read it would otherwise store an
empty file, which is the same silent-empty failure one layer down. The
**filename is asked for rather than guessed**, since bytes carry no name and
the extension is what the format is read from — inventing one puts a `.png` on
an MP3. And a storage failure now lands in the `qr_error` the CTA already
carries, as a **note rather than a refusal**: the code still renders from the
data URL, and saying nothing is what let this run silently for so long.

**Severity is the server's, and it was two JavaScript files' before.**
`blueprint.js` and `preview.js` each kept an `ADVISORY = new Set([...])` by
hand — two copies of a decision `qc_service` has every fact to make, and the
fastest way to have one panel draw a finding red while the other draws the same
finding amber. `ADVISORY_CHECKS` is the list, every check result carries a
`level`, `_all_passed` counts only failures and `_warnings` carries the rest.
`test_commercial_wizard.py` asserts neither screen keeps a set of its own.

**A voiceover generated and thrown away is a silent commercial.**
`routes/render.py` reads `project.music["voice_track_url"]` to put narration on
the timeline, and nothing in the module had ever written that key: the full
voiceover was generated, ElevenLabs was billed for every character of it, the
estimated duration was reported and the audio was discarded. Worse,
`set_music` **assigned** a fresh `{mood, level}` dict, so even a key written by
something else was wiped by the next save on the Music panel. Both are fixed —
the MP3 is stored to the client's library and the URL merged onto the project,
and every write to `project.music` merges. `test_commercial_wizard.py` asserts
the voice track survives a music save, because the failure is invisible from
both ends: the panel says "generated", the render says "succeeded", and the
file is silent.

**And the Music step it fixed was still a preset picker with no presets.** A
mood, a level, and nothing to duck: `MUSIC_LEVELS` fed two real dB numbers
into the render's automation keyframes and the track those keyframes acted on
did not exist, so the level slider was the most carefully-drawn control in the
tool and it changed nothing. Sound effects were absent altogether — a whoosh
on a transition, a stinger under the end card, the noise of the thing being
advertised — on a tool that will render a finished commercial.

`services/elevenlabs_audio_service.py` is both, wired from the two official
ElevenLabs Agent Skills (`sound-effects` and `music`). It is a **neighbour**
of `elevenlabs_service.py` rather than part of it, and the split is the whole
reason the usage page still says anything true: that module renders speech,
billed **by the character**, and every one of its decisions — the casting, the
pronunciation dictionaries, `record_tts` counting the text actually sent —
follows from that. These two endpoints bill **by the generation**. One module
answering to both names would make "what did the voice cost" unanswerable, and
`quotas.record_audio_generation()` files them under their own `api` so
`elevenlabs_estimate()` counts them on their own line: a thirty-second bed
folded into the character total reads as a handful of characters of script,
and the voice figure goes on looking right while quietly absorbing a cost
source that is not measured in characters at all. The marker went with it —
`_PROVIDER_MARKERS["elevenlabs"]` knew `/text-to-speech` and nothing else, so
either new endpoint could have spent with **nothing able to name the gap**,
which is exactly the state HeyGen, Runway and Creatomate were in.

**A published limit is refused by name, never clamped.** 0.5-30s for an
effect, 3s-10min for a bed, transcribed into `config.py` rather than fetched
for the reason `hub/creative_specs.py` gives about the spec kit. Somebody who
asked for forty seconds of rain and silently got thirty has been told
something different from what they asked for, on a file that then goes into a
spot — `hub/quote_validity.py`'s rule about a quote window, wearing a noise.
And the **music length is not asked of the caller at all**: it is
`config.music_length_ms(project.length_seconds)`, the same runway QC measures
the scenes against, so the track lands at the right length rather than being
trimmed afterwards and a browser cannot request a length the spot does not
have.

**A duration is derived or it is not measured.** Nothing here decodes audio.
Both endpoints are asked for a constant-bitrate MP3 and the length is
arithmetic on the byte count; where the response comes back as anything else
`seconds` is `None`, and `music_length_mismatch` renders that as *not
measured* rather than as a tick over a length nobody checked. Which is the
only reason that check is worth having: its two other answers are cheap and
this one is the honest half.

**A retry never re-spends, on either worker.** The cache is keyed on the
content — prompt, duration, influence, length — and lives on the **shared data
disk**, because gunicorn runs two workers and a module-level dict is a cache
that works about half the time. That is the trap `modules/bg_remover` had to
undo on the one module whose own docstring opens by saying it is deliberately
careful with credits, and `suite_panel`'s double-submit claim before it. The
client is **part of the key**: a hit points at a stored asset in somebody's
own Cloudinary tree, and handing one client's folder to another as a cache hit
would put their audio on another client's spot.

**On the timeline, an effect is not a second gain system.** It gets a track of
its own (`TRACK_SFX`) because elements sharing a track play *sequentially*,
and it is **capped to the shot it sits on** — which is what makes "nothing on
that track overlaps" true by construction rather than by hope. Its volume is
the bed's own pair, through `config.ducked_db()`, which `creatomate_service`
and `qc_service` now both read: two lookups of one table, each with its own
fallback, is how the panel and the render come to disagree about how loud
something is. `sfx_gain_conflict` judges it against the **middle** setting read
out of `MUSIC_LEVELS` rather than a literal, and reports a level the table
does not know as its own finding — because the render is then using a pair
nobody picked.

**Both checks advise and neither refuses.** A bed a second short of the runway
is a real thing to notice before the render and a perfectly shippable spot
either way; a check that refuses the correct thing is a check somebody
switches off, which is the `QR_CODE_RULES` lesson. Both are in `ADVISORY_CHECKS`
and in **both** screens' `QC_LABELS` — a check absent from a label map is
skipped silently by the render loop, which is how `scene_assets` never
appeared on the panel it was written for.

**The CTA stinger, the transition whoosh and the audio-only spot needed no
code.** The end card *is* a scene and so is the boundary a whoosh lands on, so
both are the scene route with a different prompt; and a VO-only radio spot is
this wizard with the visual steps unused, which is why `modules/fan_radio` and
`modules/radio_promo` gain nothing here and need nothing — the service is
shared, so wiring it into either later is additive rather than a rewrite.
`test_commercial_audio.py` asserts all of it, and every new check was
confirmed red against the defect it was written for first.

**A picture may encode something, or it may assert something.** The Voice and
Music steps are tiles rather than dropdowns now, and the rule they follow is
worth keeping: **draw a graphic where it carries real information, and use a
plain labelled tile where it would not.** Energy draws an amplitude because
`STYLE_BY_ENERGY` is literally the `style` value sent on the render, so the
choice changes the read and not just the shortlist; the level picker draws the
two dB figures in `MUSIC_LEVELS`, which are the same numbers
`creatomate_service` turns into the ducking keyframes, so what is on screen is
what renders. Nothing is drawn for gender, age or accent — a face or a flag
there would assert something the tool does not know. The mood waveforms are the
one illustration, and they are **labelled as one**: nothing has been composed
at the moment a mood is pressed, so a picture that reads as a waveform of a
chosen track would be claiming a track exists. That sentence used to end "no
music library is connected", which stopped being true the day the bed was
composed rather than picked — a note contradicting the panel directly beneath
it costs both of them their credibility, which is why it moved with the
feature rather than after it. Each casting tile also prints the words it will
actually match on (`characteristics_detail()`), because "Announcer" is not a
mood — it is a search for *announcer, commercial, broadcast, promo* in the text
ElevenLabs publishes, and a screen that says so lets somebody pick differently
before listening to three wrong voices.

**And the read path was the last thing here still doing its own
Cloudinary.** The write path moved onto `hub/storage.py` when `upload_asset`
learned to take bytes; `_ensure_configured()` and `list_client_assets()` did
not, which left this module carrying a second answer to *how do we reach the
account* and a second answer to *how do we list a folder*. The configure had
already drifted in the way that matters: `hub/config.export_cloudinary_url()`
composes `CLOUDINARY_URL` from the three-part credential group and exports it
for exactly this, so a deployment given only the three parts was configured in
the Hub and configured **here by a separate hand-written branch** — right
today, and one edit away from not being. `hub/storage.configure()` is public
now for the legitimate direct uses (`services/provider_check.py` pings the
account to tell a refused key from an unreachable one), and the local branch
survives only as the standalone fallback this module is written to have.

**The listing was quietly showing some of a client's photographs.** It asked
`cloudinary.api.resources` for `max_results=100` with no paging and reported
what came back as the whole folder — the truncation `connection_choices()`
already pays for one form up, where 500 of several thousand records came back
in a complete-looking `<select>`. `hub/storage.manifest()` takes a `prefix`
now rather than only a bucket, so the shared reader — which pages properly —
answers this too. Extending the shared one rather than leaving the copy in
place is the rule that file exists for: the next fix to paging, or to what a
row carries, lands once. `manifest()`'s own caller is the orphaned-asset
audit, so the check asserts a prefixed row still carries everything that audit
reads.

**A provider's asset URL is signed and expires.** A HeyGen clip linked
directly plays today and 404s next week. Finished clips are mirrored into
Cloudinary through `cloudinary_service.upload_asset`, the way rendered
commercials already were, and the storyboard says out loud when a mirror
failed and it is showing you a link that will die.

**A module in the repo is not a module in the app.** `modules/ads_builder`
--- Smart 1 Ads, 1,745 lines of Google Ads campaign operations --- sat in this
repo unreachable for months. Nothing was broken: `hub/extensions.py` provisioned
its database, `hub/client_key.py` read its proposals table, `test_ads_module.py`
passed in CI on every pull request, and `hub/demos.py` walked staff to
`/tools/ads`, which 404'd. It shipped with an installer that made the four edits
registering it --- the `wsgi.py` mount, the sidebar entry, the tile and the env
block --- against "a Hub checkout", and nobody ever ran it against this one.
A module is mounted in `wsgi.py` or registered in `hub/__init__.py`; anything
else is a directory. The tile rule below is the same failure one step later, and
`tools/linkcheck.py` will tell you a path does not resolve --- it had
`/tools/ads/` on an allowlist saying so, with the installer named as the excuse.

**A credential with lead time on it must not gate the whole tool.** Smart 1
Ads opened on Live campaigns, which is the one screen that cannot work without
the Google Ads API — so a tool whose first three steps were fully working
greeted everyone with a warning about `GOOGLE_ADS_DEVELOPER_TOKEN`, a token
Google approves on its own timetable and which nobody here has yet. The
generator is the front door now and live campaigns sit after the approval hub,
because generating is OpenAI, review and approval are the Hub's own, and only
the last step is Google's. What was missing was the last step's second route:
`modules/ads_builder/export.py` writes the same campaign as a **Google Ads
Editor** import, which posts under the account owner's own sign-in and needs no
API access at all, so an approved proposal reaches the client account today and
the identical proposal still deploys through the API later, unchanged. It
imports `parse_keyword`, `_build_rsa`, `_clamp` and `normalise_url` from
`google_ads` rather than restating them — two descriptions of one campaign is
how the CSV and the API come to build different things depending on which
button was pressed. What Editor's asset columns cannot be guessed for
(sitelinks, callouts, snippets) goes on a build sheet **named as such** rather
than dropped, and a missing budget or final URL is reported there and left
blank, because a blank Editor refuses is better than a number nobody chose.
`connection_status()` answers `deploy_ready` and names what each missing
variable costs, so a page can say what is unavailable instead of describing the
whole tool as down.

**A key that a page asks for is a key the deployment already has.** The
generator carried an "OpenAI key override" box. It invited a key from outside
this deployment into a form post, and its presence read as *this page needs a
key from me* on a Hub that has had `OPENAI_API_KEY` set all along. It is gone,
and `campaign_ai` reads the key through `hub/config.py` at call time like
everything else — the provider-key trap above, one call site further on.

**`audit.log()`'s first positional is `module` — and here it cost the whole
module.** `store.log_event` mirrored into the Hub with
`audit.log(f"ads.{action}", actor=..., **details)`: module supplied, `type_`
missing, `TypeError`, swallowed by the `except` beside it. So nothing Smart 1
Ads recorded ever reached the Hub activity log or Client 360, while its own
Activity page looked complete — and `hub/client_brand.py` had carried an
`ads_builder` entry in `WORK_KINDS` the whole time, waiting for a call that
could never arrive. `test_ads_module.py` now asserts the mirror with a stub,
because the failure is invisible from either end.

**A campaign generated for nobody reaches nobody.** The generator took a
business name as free text, so the campaign and its proposal existed and the
client's own record showed no sign that anything had been quoted. The client is
looked up now — `modules/ads_builder/client_link.py`, over
`clients_registry.search_clients()` — or explicitly marked new, and generating
files the proposal onto the client record and reports each write separately,
the way `hub/domain_links.py` does: "filed" and "filed in one of two places"
are different outcomes. Three rules it inherits rather than reinvents. The
lookup **matches exactly or not at all**, because attributing one company's
campaign to another is the worst thing available here. Nothing is written to
Knack and no registry row is invented for a prospect — a new business becomes a
**lead** in Smart 1 Suite, which is where prospects live, and because the work
and the proposal are filed under the name and domain they join the client
record by themselves the day it exists. And a lead with neither an email nor a
phone number is **refused by name** instead of created, because a contactless
lead reads as a live prospect on every count that follows.

The proposal is filed as a **link**, through `proposals.add_link_proposal()`,
not as an uploaded snapshot: it is a live page that gains comments and changes
status, and a PDF of it on the client record would sit there contradicting the
thing it is a copy of. `ref` carries the module's own proposal id and the row
is **updated** rather than appended, or approving, commenting and deploying one
campaign would leave three identical entries on Client 360 with no way to tell
which is current — `upsert_from_ghl` learned that from GoHighLevel first. What
the join wrote is kept inside the campaign JSON, never in a new column:
`create_all()` adds no column to an existing table, so one added here would be
silently absent on the live Postgres while every local test passed.

**An estimate a client reads is a different document from the one a rep
builds — and it must not be a second copy of it.** The paid search estimate is
now what Smart 1 Ads produces: the intake answers, the target areas, the
landing-page findings, the competitive picture and Good/Better/Best, printed in
the order a client reads them. `_estimate_doc.html` is included twice — by the
internal preview and by the public client link — with one flag deciding whether
the per-section change buttons render and *nothing else* differing, because two
templates is how the version a client reads comes to differ from the version
somebody approved. `test_ads_estimate.py` asserts both renderings carry the
same sections, numbers and caveats.

The client link is `/tools/ads/estimate/<token>`, and `PUBLIC_PREFIXES` in
`modules/ads_builder/app.py` is read by `wsgi.py` for both halves of the mount:
`AuthGuard`, so a client with no Hub login can open it, and `HubBar`, so the
sidebar, help layer and feedback tab are not injected into a document sent to a
prospect. Same arrangement as `modules/scans`, for the same reason — the mount
and the module cannot disagree about what is public. It does not extend
`ads_base.html` at all: that template draws the module's own tab bar, and a
prospect has no business seeing Live campaigns or a version tag. **Revoked,
deleted and never-existed all answer the same 404 page**, because a
client-facing URL that says "this one expired" tells somebody probing which
tokens are real.

Three things a client can answer, not two. "Yes", "yes with my changes" and
"let's talk" are the three real replies, and an approve/reject pair forces the
middle one into whichever end is nearest — so `spec.OUTCOMES` carries all three
with the colour each comes back as in the approval hub (green / yellow / red),
and **no answer yet is grey rather than a fourth kind of bad**: "not sent" and
"sent and ignored" and "they said no" are three different situations. A change
request **requires a name and an email**, because "the client wants the budget
lower" is not actionable and three people at one company will disagree with
each other; each request is stamped with who asked and kept beside the others
rather than over them.

**Approving is a statement about a specific document.** So an edit clears the
approval and says it was superseded, and a *material* edit — the budget, the
audience, the do-not-target list, a removed keyword, a removed negative — sends
the estimate back through the model before it can be approved again. That is
two presses on purpose: the first returns the re-check rather than approving,
so a rep who quartered a budget sees what it did to the plan *before* the
document they signed off becomes the one a client reads. Removing a negative is
always material however small it looks, because it reopens spend the vault
existed to stop. `store.update_campaign` writes the whole blob and every change
lands in `editLog` inside the campaign JSON — never a new column, for the
`create_all()` reason above.

**A model handed a URL writes confident recommendations about a page it has
never seen.** `modules/ads_builder/landing_page.py` **fetches** the page and
counts its conversion points off the markup — `tel:` links, forms and their
field counts, booking tools and chat widgets by their own script signatures,
map links, CTA buttons — and every one carries the **evidence**, because "this
page has a phone number" and "this page has (317) 555-0142" are different
claims and only the second can be checked. The model is given those facts and
asked only for judgment, and the two are kept apart on screen. A page that
could not be fetched is **not measured**, never zero, and the prompt then tells
the model not to describe the page at all. Chat and booking are matched on the
widget's own signature rather than the word "chat", because a page with a "Chat
with us" heading and no widget converts nobody. The finding that changes a
campaign is `missing_for()`: a conversion action the client asked for that the
page cannot do — bidding for appointment bookings against a page with no
booking tool spends the budget and books nobody.

**The intake is the campaign.** `modules/ads_builder/spec.py` holds the
questions, the eight conversion goals with what each one *costs the campaign*,
the audience guidance, the tiers and the outcomes, read by the form, the AI
prompt, the estimate and the client page alike. Two rules in it: **an answer
that was captured must be shown** — the estimate used to open on a budget and a
keyword list with none of what the rep had asked, so the client could not tell
the campaign was built around their answers; and **"not asked" is not "no"**, so
every yes/no is tri-state and an unanswered question is left off the client
document rather than printed as a confident No. `for_prompt()` hands the model
*what to do about* each answer rather than the answer alone — a model told
"B2B" writes B2B-flavoured adjectives, one told to keep consumer intent out of
the keyword set builds a different campaign.

**Every average CPC is a sector benchmark, and it sits on a page somebody
spends money from.** `spec.CPC_NOTE` is one string, `analyse_budget()` returns
it alongside the numbers so no screen can render a CPC without having been
handed the words for it, and `test_ads_estimate.py` asserts each template
carries it.

**A developer token is not one thing, and the tier is what decides.** Google
grants a new token **Explorer** access automatically — production accounts,
2,880 operations a day, and the keyword planning services **excluded**. So
`generateKeywordIdeas` answers `DEVELOPER_TOKEN_NOT_APPROVED` on a token that
is entirely healthy, and read as a bad key it sends somebody to rotate a
credential that was fine. Basic access is the first tier that can measure
anything, and it is applied for and reviewed rather than granted.
`keyword_plan.PlanningUnavailable` carries `tier_needed` so a page says *apply
for Basic access* rather than printing an error code at a rep who cannot act on
one, and the refusal is **saved onto the campaign** — "we asked Google and the
tier does not allow it" is a fact the estimate should carry, and dropping it
leaves the benchmark on screen with nothing saying the measured number was
tried for. Google publishes the tier nowhere an API can read it, so
`api_readiness.tier()` treats `GOOGLE_ADS_ACCESS_LEVEL` and the stored setting
as **claims** and lets an actual probe outrank both.

**A top-of-page bid is not a cost per click.** Google's two planning services
return two different numbers: `generateKeywordIdeas` gives the bid you would
need to show at the top of the page (20th/80th percentile), and
`generateKeywordForecastMetrics` gives a forecast `averageCpcMicros`. Only the
second is what you pay, and the first is always the larger — so printing it
under the word "cost" overstates every estimate this tool produces, by a margin
that grows with the sector, and looks exactly like a better number than the
benchmark it replaced. `spec.CPC_SOURCES` holds all three provenances with the
caveat each must appear beside, `keyword_plan.py` imports that rather than
restating it, and the estimate reads `spec.cpc_provenance()` so a label cannot
drift from the call that produced the number under it. The forecast is
preferred, the bid range is the labelled fallback, and the sector benchmark is
what you get when neither answered. Measuring also **re-costs the tiers** —
`campaign_ai.retier()`, recomputed and never re-asked, so wording a rep edited
survives — because a measured headline over tiers costed at the sector rate
shows a client two different campaigns on one page. An area Google could not
place is **named on the client document**, never widened to the state it sits
in: a CPC measured across three of a client's five counties is not this
campaign's CPC.

**"Not ready" is useless; "the client has not accepted the link invitation" is
a phone call.** Reaching a client's Google Ads account is a separate act from
authorising ours — there is no "add this email" call, so we send a manager link
invitation and *they* accept it, and until they do the API reports an empty
customer list rather than an error. `api_readiness.preflight()` asks every
question in the order it bites and returns a **named checklist**: credentials,
authorisation, tier, account reachability, then the Hub's own three approval
rungs. `api_deploy` refuses on that checklist and returns the whole thing, so a
rep who fixes the status is not then told the account is unreachable — one
press, every blocker. Three rules in it: a check that could not run is *not
measured* and never a red cross (an unreachable Google is not a bad key); the
client's own answer is shown but does not block, because a rep may have an
approval by phone and "never sent", "asked to talk first" and "said yes" are
three different situations; and the **dry run is never gated**, because
validating is how somebody finds out what is wrong and gating the diagnostic
behind the conditions it diagnoses makes it unavailable exactly when it is
needed. `docs/google-ads-api-integration.md` is the rollout order.
`test_ads_keyword_plan.py` asserts all of it with Google stubbed, because what
is worth asserting is what the module does when Google says no.

**A budget nobody has named is the ordinary case.** Refusing to build anything
until a client picks a number is how the conversation stops before it starts,
so the budget is optional and the model sizes Good/Better/Best — asked for
either way, because with a budget it is how a rep shows what the next step up
buys. Each tier's click estimate is **recomputed** from the sector CPC rather
than trusted, since it is the number a client checks the tier against and a
model that rounds generously makes the cheapest tier look workable when it is
not. With no stated budget the campaign is costed at the recommended tier and
`budgetSource` says so in as many words on the client document.

**Target areas here are the Proposal Builder's, and there is no third
mirror.** `hub/target_areas.py` already carries one JavaScript copy, with
`test_target_areas.py` existing solely to prove the two halves still agree; a
second copy would need a second such test and would drift the day either was
edited. So `/tools/ads/api/areas/preview` normalises and sizes server-side and
the browser renders what comes back — the choice Social Planner made about its
calendar, for the same reason.

**A field that redraws itself while you are typing in it eats what you type.**
The target-area rows asked the server for labels on every keystroke and then
redrew the whole list from the answer — which replaced the `<input>` mid-word,
so "Carmel showroom" came out as "Car". The structure and the derived text are
drawn by two different functions now: `drawAreas()` builds the inputs and runs
only when the shape changes (add, remove, change of type), and `paintMeta()`
writes the label and the reach into reserved spans and can never touch an
input. Anything that re-renders a container a person is typing into has this
bug; `test_ads_estimate.py` asserts the two halves stay apart.

**The provider that answered is not what a rep needs to know.** The logo panel
said "Brandfetch" — a name that means nothing to the person reading it and
invites the question of what to do when it says no. What a screen shows is
where the logo came from: the client record, a lookup, or an upload. The
variable that switches the lookup on is named on Settings, where somebody can
act on it, and not in front of a rep who cannot.

**A client is filed under a name and a domain, and a campaign reliably has
neither.** A logo plainly on file came back empty because brand data is stored
two ways — under the slugified client name and in a cache keyed by domain — and
the generator has the name a rep typed and the URL of a landing page, which is
often a microsite rather than the client's own site. `logo._candidates()`
resolves the client through `hub/client_key.py` first, then tries the registry's
name and the registry's URL alongside what the campaign carries, and **names
what it looked under** when it finds nothing: "this client has no logo" and "we
asked under a name they are not filed as" are different answers.

**Most calls to action are links, not `<button>`s.** The conversion-point scan
counted `<button>` elements and missed every "Get a free quote" anchor on every
page built with a page builder. A link counts when it says what a CTA says or
carries a class a builder gives its buttons — matched on the whole class token,
because a substring match on `btn` also matches `subtle`. A styled button with
no words in it is skipped: it is a chevron, and it tells a reader nothing.

**A name the model researched is a suggestion until a person ticks it.** The
competitor list arrives `accepted: False` and only ticked names reach the client
document. Printing all of them is us telling a client who their competitors are
on the model's say-so, and that is the paragraph a client checks hardest.

**`navigator.clipboard` is not available on http, and refusing is allowed.**
The copy button reported success it never had. It tries the clipboard API, then
`execCommand`, and only if both fail does it put the link on screen selected
with "press Ctrl-C" — a button that lies about copying is worse than one that
asks.

**The step that blocks everything else belongs where the queue is.** An
estimate that has not been approved cannot be sent to a client at all — the
share route refuses it — and that was visible only inside each proposal, so the
approval hub read as "nothing to do" while every row waited on the same press.
"Approve these estimates first" sits at the top of the hub, links straight to
the approve card, and leaves archived proposals out: nobody is going to approve
those.

**Quiet controls need saying out loud.** The per-section pencils on the client
estimate are deliberately faint so eight of them do not turn a proposal into a
form — which means nobody finds them. The page now says so above the document,
before the first section a pencil applies to.

**A logo is looked up, never guessed at.** `modules/ads_builder/logo.py` tries
the brand data already stored against the client, then a live Brandfetch
lookup **behind a button** because that one is billed, then upload — and each
answer names which source it came from. No `https://<clientname>.com/logo.png`
and no favicon scraped off the landing page: a wrong logo on a client-facing
estimate is worse than none, because nobody proof-reads the thing they
recognise.

**A help layer three tools deep is not installed until a screen opts into
it.** Smart 1 Ads had no explanation on any of its screens — no bubbles, no
tour — while `hub/help.py`, `hub/help_routes.py` and `hub/static/hub-help.js`
sat there working: a bubble appears where a template places `help_dot('key')`,
and a tour is offered only where `<body data-screen="…">` names one. Nothing reports
a screen that placed neither, and every failure in between is silent by
design. A bubble whose key is not in the registry is **removed** client-side
rather than left as a dead "?", so a typo'd key reads as helped from the
template and shows nothing on the page. A tour step whose selector matches no
element keeps its narration and **hides the ring**, so a renamed card costs the
step its anchor and says so nowhere. And the guided walkthrough was worse than
absent: `hub-demo.js` floats "Walk me through this" onto every page carrying
`data-module`, so the module's one scenario — written against a generator that
has since been rebuilt — was offered on Settings and Live campaigns, where
`#geography`, a `#budget` text field and four `data-demo` hooks that exist in
no template all resolved to nothing and "Do it for me" returned in silence.
The walkthrough is per **screen** now (`data-demo="off"` opts a screen out of
the floating button), the tour is per screen, and the two screens a rep works
in carry both. `test_ads_explainer.py` asserts every key resolves, every
selector is anchored **on its own screen's template**, and that none of it
reaches `/tools/ads/estimate/<token>` — that document is chrome-free for a
prospect, and a staff note in it is an internal note in front of a client.

**That assertion was true of one module, and three other tools had a bubble
with nothing behind it.** Website Blocks, the Social Content Planner and
Video Search each placed `help_dot()` on their own title and no entry was ever
written, so the dot was removed client-side on every visit: the template read
as helped, the screen showed nothing, and nothing errored at either end.
Video Search's template even carries a comment saying its key must not be
renamed *because renaming would orphan the bubble* — protecting a key that
pointed at nothing. All three say something now, and `hub/help_audit.py` is
the check, at **medium** on `/api/integrity`: the page still works and nobody
is waiting on output, but a screen that opted into the help layer and got
nothing is indistinguishable from one that never tried.

Three things it has to get right. **A bubble is placed two ways** —
`help_dot('key')` in Jinja and `data-help="key"` on an element a script
writes — and the Proposal Builder's reach panel uses the second, so a scan for
the first alone reports four live entries as dead. **A key built at runtime is
named, never resolved**: that panel writes ``data-help="sales_builder.areas.
${key}"`` from a loop, and guessing at the interpolation in either direction is
the mistake `tools/linkcheck.py` already refuses to make about a URL built by
concatenation — the audit lists it as built at runtime and says which
registered keys its prefix reaches. And **a registered key nothing places is
not a finding**: a tour step is anchored by a selector rather than a dot, and
calling those dead would make the check report its own blind spot.

**An unconditional `data-screen` must name a tour that exists.** Three pages —
Prospect 360, the Website Audit and Stock Photos — named a screen the registry
has no steps for. `hub-help.js` already declines to offer an empty tour, so it
cost nothing on the day; what makes it worth fixing is that `tour()` falls
back to the **module prefix**, so the day somebody registers a sibling
screen's steps those three would serve them over elements that are not on the
page — which is the Smart 1 Ads failure, and precisely why `has_tour()` exists
for a layout to ask. They are guarded on it now, `if has_tour is defined` like
every other helper, so a Jinja environment that never got
`install_template_helpers()` loses the attribute rather than the page. The
rule the check enforces is not *never name an empty screen* — a guarded
declaration is correct whether or not the tour exists yet.

**And the walkthrough returned in silence, which is the third layer of the
same failure.** `hub/demos.py` drives a tool's *real* screen — filling its
real fields, clicking its real buttons — so every step names the element to
act on. A step whose element is not there hid its ring and, on **Do it for
me**, `perform()` did `if (!node) return;`: the learner presses a button that
promises to fill a field in and nothing at all happens, with no message. This
file already named that failure for Smart 1 Ads' one scenario; what was fixed
then was *offering* a walkthrough on a screen it was not written for, and
never *running* one. The step says so now, in amber rather than red — the
narration above it is still correct and still worth reading, and only the
driving cannot happen — and the button that could only do nothing is not
drawn, because a button pressed once with no effect makes the whole
walkthrough read as broken rather than one step of it.

**And the audit was crediting a word rather than an attribute.** `_found()`
tested `name in everything` — a bare substring against every template and
script in the repo — so `data-demo='unmatched'` read as anchored because the
word *unmatched* appears in another tool's prose, and
`data-demo='client-name'` because something, somewhere, has a class of that
name. Twenty-two steps that drive nothing read as anchored, and **two whole
walkthroughs read as working while every driving step in them resolved to no
element at all**: Image Creator's and the UTM builder's, which is the Smart 1
Ads failure the floor below exists to catch, hiding inside the check that
would have caught it. `_spellings()` is what the audit looks for now — the
attribute in either quoting — and a selector kind it cannot look for asks for
nothing rather than matching everything.

Both are anchored now, along with the PDF optimizer, the calculators and the
two radio builders: twenty-one hooks, seven of them in
`modules/image_creator/static/editor.js`, because that tool's panels are drawn
by script when the rail is clicked and `hub-demo.js` repaints on a debounced
`MutationObserver` for exactly that shape. Two more scenarios drive controls
that are **not there to anchor** — Background Remover's walkthrough offers a
free "remove white background" option beside the paid one and the tool has a
single button, and its step 4 asks for a preview it never draws. That is the
Web Tickets *"Sort by age"* case: a walkthrough describing a tool that does
not exist is worse than one describing none, so those want rewriting rather
than a hook pointed at the nearest thing.

**`elsewhere` is the third answer.** Asking whether the element exists
*anywhere* is deliberate and stays — a walkthrough drives a screen whose
markup half a dozen scripts write — but *anywhere* also credits a step whose
only match is in a different tool, and that step drives nothing when the
walkthrough runs. Those are named rather than counted as missing, since the
element may still be drawn at runtime, the way a target accepted on a prefix
already is.

**Fifty-five of the 165 steps that name an element named one that is in no
template**, across eighteen of the twenty-eight scenarios. That was a
**backlog, not a regression**, and it was deliberately not an integrity
finding: a check switched on red is a check somebody turns off, and it would
have taken the bubble check down with it. `help_audit.demo_targets()` gathers
it and the **help layer** panel on `/diagnostics` lists it, so the scenarios
written against a screen that has since been rebuilt were a list somebody
works down rather than something a learner meets one step at a time.

**It is at zero, and the count is asserted now rather than the list.** All
twenty-eight scenarios drive every step they name. Asserting the number was
the wrong check while a backlog existed — it would have started red, which is
the failure this section is about — and it is the right one now, for the
opposite reason: it starts green and it bites the first time a control a
walkthrough drives is renamed, which is how fifty-five accumulated. A step
whose selector the audit cannot test clears nothing, so *untestable* cannot
satisfy it either.

**Seven of the fifty-five were never dead.** `_needs()` reads a
`[data-tour='…']` selector into a requirement and `_spellings()`, which says
how one would be written in markup, was never told that attribute exists — so
it returned an empty tuple and `_found()` could only answer False. Every
tour-anchored step in the two Smart 1 Ads walkthroughs was counted as driving
nothing while its hook sat in the templates. That is the false positive
`_spellings()`'s own docstring warns about, inside the function written to
stop the opposite mistake, and it is held by an invariant rather than by
naming the attribute: every kind the parser emits must have a spelling the
finder looks for.

**And six scenarios named a page this Hub does not serve** — four guessed a
`/tools/` prefix for a module mounted at the root. `catalogue()` prints that
string into the index a rep reads and asks no route to resolve it, and
`hub-demo.js` never navigates, so neither end could notice. Three of the six
were among the scenarios whose steps read as anchored to nothing, which looks
like a forgotten hook when the cause is the line above them.
`test_help_layer.py` asks the composed app, the assertion
`test_oauth_redirects.py` already makes about the OAuth callbacks; a redirect
counts as served, because these are staff pages behind `AuthGuard`.

**And the list is being worked down, which is what a backlog is for.** Three
scenarios are repaired: `seo_images.first_batch` (eight of eleven steps dead),
both `sales_builder` scenarios (seven between them). The repairs are two
different jobs and the difference is the whole point. Most steps named a
control that **exists under another selector** — `[name='max_edge']` where
the page has `#maxEdge`, `[data-demo='save']` where it has `#btnSave` — and
those are simply anchored, at the real id where the page's own script already
depends on one and at a `data-demo` hook where the control is drawn by
JavaScript from a row template and has no id to point at.

**Two named a control the tool does not have, and those are rewritten rather
than anchored** — the Web Tickets *"Sort by age"* rule, because a rep
believes a walkthrough. The SEO Image Pipeline's step 2 said *"the specific
page URL, not just the domain"* and drove a `page_url` field: that form asks
for the **site** (its own placeholder is a bare domain) and, separately, an
optional **Page name**, which is a name rather than a URL — so the step asked
for the opposite of what the field wants. And the Proposal Builder's step 9
said to **set the status to Converted**, which is not a status anybody sets:
the pills offer Draft, Sent, Approved and Lost, and Converted is what a quote
becomes once *Convert to IO* has built the insertion order behind it — the
reason `hub/quote_validity.py` refuses to expire one. Both now describe the
tool that is there, and step 9 points at the control
`sales_builder.deliver` already named, so one hook serves both scenarios.

**A repaired scenario is named in the test, and the backlog still is not.**
Asserting the *count* would be the check switched on red that this section
exists to avoid. What `test_help_layer.py` asserts instead is that a scenario
somebody has worked to zero does not quietly come apart when a control it
drives is renamed — and, in the other direction, that every scenario the list
names still exists, or an entry outliving its scenario would pass by
describing nothing, which is `check_stale_json_exemptions()`'s failure one
shelf over. Both were confirmed red before they were confirmed green.

**Five of them drove nothing at all, and that half is not a backlog.** A
scenario with one step out of date is a walkthrough with a gap in it; one
where *every* driving step names an element that is not there is a button
somebody presses nine times for nothing, which is the Smart 1 Ads failure
verbatim. Those five are placed — the SEO client page's schema and FAQ
builders, the two Suite billing reports, Stale Creative, Landing Page Ads and
the ticket queue — and `test_help_layer.py` asserts the floor rather than the
backlog: **no scenario may drive none of its steps.** The rest of the list
stays a list.

**And one step described a control that is not there rather than one that
moved.** Web Tickets' *"Sort by age"* — nothing on that page sorts, and the
filter does the same job better because it names the SLA instead of leaving
somebody to judge which ages matter. A walkthrough describing a tool that
does not exist is worse than one describing none, because a rep believes it,
so the step is rewritten rather than anchored to the nearest thing.

**And the floor it stands on could be cleared by a selector that tests
nothing.** `_needs()` reads a step's selector for an `#id`, a `[data-demo]` or
a `[name]`, and a selector carrying none of those returned **no requirement at
all** — so `absent` was empty, the step counted as anchored, and the check had
put a tick over a question nobody asked. Four steps were written
`input[type='file']`, which matches a file input on any page in the Hub and
identifies nothing.

That is what let **`client360.proposal`** clear *no scenario may drive none of
its steps*: three of its four hooks are in no template, and the fourth was that
selector, so three-of-four is not four-of-four and the floor passed a
walkthrough that drives nothing. **`bg_remover.logo_cutout` was hiding behind
the identical selector** and had four dead hooks — so the floor was reporting
one clean sweep over two scenarios that could not drive a single step between
them. Absent data reading as a measurement, in the check written to find
exactly that.

A selector the check cannot test is its own state now — counted apart, drawn
under the *unverified* pill the runtime-prefix rule already has, and it
**clears nothing**: `dead` is measured against the steps that carry something
testable, so an untestable step neither proves a scenario drives something nor,
where every step is one, asserts that it drives nothing.

**And `data-tour` was a whole attribute the parser had never heard of.** It is
how a tour step anchors and how seven of Smart 1 Ads' driving steps anchor too,
and **39 anchors in this repo were tested by nothing**: renaming one out from
under the step that drives it changed no count on any screen. It is read like
`data-demo` now.

Both scenarios are repaired rather than retired, and the two repairs are
different jobs. `client360.proposal`'s hooks were simply never placed, so they
are placed. `bg_remover.logo_cutout` described a free **"Remove white
background"** button that runs in the browser — and that tool has never had
one: its free option is a *preview* cut at a quarter of a megapixel, too small
to deliver and exactly big enough to see whether the edges came out clean. The
advice was right and the control was imaginary, so the steps are rewritten
against the tool that exists — Web Tickets' *"Sort by age"* rule, because a rep
believes a walkthrough.

**And the injector answering the same question kept a second description of
it.** A page that does not extend `base.html` — a blueprint-registered tool,
`client_owners.html`, `unattached_images.html` — is tagged by the hub app's own
`after_request`, and that tagged it from a **hand-typed slug map** rather than
from where the scenarios are. It had drifted in both directions before anybody
read it: three entries named a module whose only scenario is written for a
different page, and `qa` matched on the **first URL segment**, so it claimed
every path under `/qa`.

Measured on the running app, four pages carried a button that could not work —
`/qa/client-owners`, `/qa/unattached-images`, `/tools/calculators/leads` and
`/tools/tickets/setup`, each offered its module's *first* scenario, written for
the index page. On `/qa/client-owners` that is `qa.billing_audit`, whose four
targets are **0 of 4** present there: it rings nothing on every step. And
`client_owners.html` declares no module **on purpose** — this file says why, a
few sections up — so the injector was overruling an opt-out with the very thing
it opted out of.

`_demo_module_for()` is the one reading now and both callers use it. Matched on
the scenario's **own path** and nothing looser: matched on a segment it lands on
every page under a prefix, and matched on a prefix it lands on a tool's
sub-pages, and neither is the screen the steps were written against — a
walkthrough drives one page.

**The sweep that proves it had to survive itself.** `test_hub_help_layer.py`
requests every hub page and fails any that offers a walkthrough written for
another — and the first version signed itself out partway through, because
`/signout` is a GET like any other, so every page after it came back as the
sign-in form and was skipped. It reported two wrongly-tagged pages where there
were four. A sweep that quietly stops sweeping, in the check written to catch a
map that had quietly stopped matching. It skips the auth routes and asserts it
still held its session at the end. It also models the two real opt-outs —
`data-demo="off"` and a page's own `[data-demo-start]`, both of which the
launcher honours — and judges where a request **landed** rather than where it
was aimed, since `/seo/client` with no `?name=` redirects to `/seo` and that
page's module is its own. A check with false positives is one somebody switches
off.

One thing it deliberately does not report: `website_audit.html` declares
`data-module="website_audit"` and **no scenario is registered for that module
at all**. `autoLauncher()` returns early on an empty list, so no button is
drawn and the page is right today. Calling it a finding would start the check
red over a page nothing is wrong with — the `has_tour()` shape one layer over,
and worth knowing before somebody registers a `website_audit` scenario for a
different screen.

**A hook can be derived, and a substring search calls a derived hook dead.**
The QA index writes `data-demo="qa-report-{{ key }}"` once for every report it
lists, so a scenario naming a report added next month is anchored without that
template being edited again — the reason `card()` on the prospect record takes
one key rather than nine call sites doing it. Nothing then contains
`qa-report-ghl-billing-no-products` whole, which is the guess `tools/linkcheck.py`
refuses to make about a concatenated URL and the one `placements()` already
refuses to make about a help key. What is knowable from the source is the
**literal prefix** in front of the interpolation, so a target starting with one
is *accepted and not verified* — named on the panel under the pill that state
already has, never folded into the anchored count. At least three characters,
because a bare `data-demo="{{ x }}"` names no prefix and one that matched
everything would switch the check off.

The rest is placed. What it took was not one job: most steps named a control
that exists under another selector and were simply anchored — `#runBtn`,
`#checkNow`, `#refresh`, `#snapshotId`, `#prospectFirstName`, `#btnDownload` —
and the rest named a control the tool does not have and were rewritten against
the tool that does, the Web Tickets *"Sort by age"* rule. Site Scans promised
that the image findings hand to the SEO Image Pipeline and the schema to
Schema Builder, and that page links to neither; what it does have is the
Reports card, which is the honest version of the same point. Google Finder
offered an *SEO snapshot* it has never had and an *anomaly check* whose route
(`/api/ga4/anomalies`) is live with **no caller anywhere in the repo** — that
one is worth knowing on its own, since `ga_tools.html`'s own copy tells a rep
the page has it.

It asks whether the element exists **anywhere**, not on the scenario's own
page — a walkthrough drives a screen whose markup half a dozen scripts write,
so tying a target to one template would report a hook drawn at runtime as
missing. A target in no file at all is missing beyond argument; one that
appears somewhere is *not verified*, and the panel says which rather than
implying it surveyed the pages.

The step is repainted on a **debounced `MutationObserver`**, the arrangement
`hub-help.js` already uses to mount bubbles on late-rendered content: half
this Hub draws its panels from a fetch, so a target routinely arrives a
second after its step was painted, and without this the amber line would
stand and the button stay hidden on a step about to become perfectly
workable — a worse answer than the silence it replaced. It **filters its own
writes**, because `paint()` writes into the panel and moves the ring, and an
unfiltered observer would repaint every 150ms for as long as a walkthrough is
open.

**Seven hub tours were written, registered, anchored — and unreachable.**
`hub-help.js` offers a tour only to a screen that names itself in
`data-screen`, and `hub/templates/base.html` rendered a fixed `<body>` that
never carried one. So `hub.dashboard`, `hub.client360`, `hub.creative`,
`hub.activity`, `hub.leads`, `hub.seo` and `hub.status` — sixteen steps whose
selectors ring real elements — could not be offered on any page. The declared-
and-never-wired trap, at the layer that explains the Hub to its own staff.

What made it invisible is that the layer plainly worked *somewhere*:
`prospect.html` and `website_audit.html` own their own `<body>` rather than
extending the base template, so those two tours are offered and the mechanism
looked fine. `HUB_TOUR_SCREENS` maps the path to the screen, because there is
no mechanical route from `/` to `hub.dashboard`; `test_hub_help_layer.py`
holds it against the registry in **both** directions, and counts a template
that names itself as reachable — an entry naming a screen with no tour fails,
and so does a `hub.*` tour no path reaches.

**And the button beside it was offered where it could not run.**
`{{ hub_demo_module or 'hub' }}` — the launcher tests that attribute for
truthiness, so the default made every *unmapped* hub page offer the hub
module's first scenario, and four of the eight mapped entries named a module
whose only scenario lives on a different page. **Fifteen pages** offered a
Client 360 walkthrough: "it highlights nothing and Do it for me silently does
nothing, which is worse than no button", which is the note `hub-demo.js`
already carries about Smart 1 Ads. The module is *derived* from where the
scenarios actually are now, so the hand-typed half cannot drift, and there is
no default — one page still offers it, `/client360`, which is the scenario's
own page.

**Two things it turned up and did not fix.** `client360.proposal` anchors
three of its four steps to nothing, and passes `demo_targets()`'s
drives-none-of-its-steps floor only on the strength of step 3's
`input[type='file']` — a selector generic enough to match anywhere, so the
check clears it without the scenario being drivable. And the hub app's
`after_request` injector tags `<body data-module>` by **first URL segment**
for pages that declare none, so every `/qa/*` page gets the qa module: it puts
one back on `client_owners.html`, whose author deliberately left it off for
exactly this reason.

**A tour that opens itself is a dialog in front of somebody doing a job.**
`data-screen` used to *start* the tour on a screen's first visit — modal, over
the form, before anyone had asked for anything. It **offers** it now, in a
corner card with the page fully usable behind it, and both answers are final:
a prompt that comes back is the thing being fixed, and "How this works" in the
header is how a tour is reached afterwards. The same screen showed why the
modal was worse than it looked: the layer painted `rgba(9,22,38,.62)` **and**
the ring painted the same value as a 9999px shadow, so everything outside the
ring was dimmed twice — 86%, dark enough that the form behind could not be read
— and the layer's own scrim also covered the one element the ring had punched
out, which is the entire point of a spotlight. The dim belongs to the ring
alone. `test_ads_explainer.py` asserts both halves.

**The Render disk is not backed up. The database is.** Render backs up managed
Postgres; the 5 GB disk at `/var/data` is outside that, and a plan change,
region move or resize hands back an empty one. Anything whose only copy was a
JSON file on that disk was unrecoverable — and it fails *silently*, because a
module reading a missing file shows an empty list, not an error. Write JSON
through `hub/jsonstore.py`, which mirrors each write into the database and
restores on a miss. Pass `durable=False` only for something genuinely
rebuildable, and say in a comment what rebuilds it. `/api/integrity` flags any
module still writing its own; `/api/backup` and `/diagnostics` say what is
actually mirrored.

**And two logs decided their own location.** `jsonstore.data_root()` says why
it exists — *"every module had its own copy of this expression. They all
agreed, which is luck rather than design: the moment one of them disagreed,
its files would land somewhere the backup sweep never looks."* `hub/audit.py`
and `hub/errors.py` were two such copies, and they did disagree: both
preferred `/var/data` unconditionally and read **`HUB_DATA_DIR` not at all**,
which is the first thing `data_root()` reads.

Nothing moves on Render, where `HUB_DATA_DIR` is unset and the disk is
mounted. What it cost was every test that sets it and then reads one of those
logs — it was handed the **real, shared** one. `test_msa_embed.py` asserts
that signing writes an activity entry carrying the client, and on a machine
that had run the suite before, fourteen `msa` rows were already in
`/var/data/hub-audit.log.jsonl`, `entries[0]` already carried *"Acme Marine,
LLC"*, and **both assertions passed before the test ran a line**. Dropping
`client=` from the route — the regression the test's own comment calls "the
same as not logging" — left it green. It fails now.

Both defer to `data_root()`, and the explicit `AUDIT_LOG_PATH` /
`ERROR_LOG_PATH` overrides still win, because naming one file is the more
specific answer than naming a root. Neither may raise: a log that can break a
boot is worse than one in the wrong place, so both fall back to the
expression they replaced. `test_jsonstore.py` asserts a named root moves both,
that the overrides still beat it, and that neither file goes back to deciding
for itself — beside the section already there about a fresh data directory not
being isolation on its own, which is the same trap one layer up.

**And it was seven copies, not two.** The logs were the pair that was
provably biting; the same expression sat in `hub/leads.py` (the lead book),
`hub/extensions.py` (the SQLite fallback), `hub/scheduler.py` (the leader
lock), `modules/landing_ads/store.py` and `modules/google_finder/app.py` (the
OAuth refresh tokens — one of the files this page counts as having no second
copy). All five skipped `HUB_DATA_DIR` too, so naming a root moved the
jsonstore files and left those five on the shared disk.

The scheduler had a spelling of its own: its fallback was **`"."`**, the
current working directory — the one answer that depends on where somebody
happened to start the process, and it drops a lock file into a developer's
checkout. And the token database had no fallback at all: a machine with no
`/var/data` got a path nothing could create.

All seven defer to `data_root()` now, each keeping the override that names
one *file* — `AUDIT_LOG_PATH`, `ERROR_LOG_PATH`, `HUB_LEADS_FILE`,
`TOKEN_DB_PATH`, `DATABASE_URL` — because naming a file is more specific than
naming a root, and several test files already rely on exactly that. None may
raise: each falls back to the expression it replaced, since a store that
cannot resolve a path is worse than one in the wrong place. Nothing moves on
Render. `test_jsonstore.py` asserts a named root moves all seven, that the
overrides still beat it, and that no file goes back to deciding for itself.

**One file holding every record, changed one record at a time.** A store that
keeps its whole collection in a single JSON file changes one row by reading
the list, editing an entry and writing the **whole list** back. So two writers
each start from the same snapshot and the second one to finish drops the
first, and it needs no contention over a single row for that to happen: two
people editing two *unrelated* projects lose one of the two edits, because
what is written is the whole list either way. Both are told it saved. Nothing
errors, nothing is logged, and the row that vanished looks exactly like one
nobody made.

**`modules/radio_promo` lost writes inside a single worker**, because its
`threading.Lock` covered the write and not the read. Measured: two threads,
two unrelated projects, one edit gone. Worse, `add_version()` read the
versions list through `get()` and wrote it whole through `update()`, so
**eight concurrent appends kept one** — in the module whose own docstring
opens by promising that every draft, rewrite, tighten and hand edit is
*appended* rather than overwriting, "so nothing a client approved can be
silently lost". The append was the thing being lost.

**`modules/social_planner/intake.py` had the other half.** Its lock covered
the read as well, so within one worker it was right — and it is per-process,
and this deployment runs two gunicorn workers, so it never saw the other one.
Threads cannot show that failure, which is why it stood: it takes two real
processes, and with them **one request of two survived**. That is a location
manager submitting from a phone, on the form whose own comment says turning
one away "has cost us the photograph".

`hub/jsonstore.update_json(path, mutate)` is the missing half of `read_json`
and `write_json`, and it takes **two locks, because there are two ways to lose
a write**: a per-path `threading.Lock` for the threads inside one worker, and
an `flock` on a sidecar file for the workers. Either one alone leaves half the
problem, and each was separately confirmed — reverting the lock's *scope* back
outside the read fails the thread checks and the process checks together;
removing only the flock fails only the process ones.

Three rules on it. **Failing to take the flock never costs the write** — a
filesystem that will not take one is a reason to serialise less, not a reason
to refuse to save, the rule every other entry point in that module works to.
**Returning `None` from `mutate` writes nothing**, which is how "no such
project", "nothing to delete" and "already there" are said: `google_index`'s
rule, so a lookup that misses does not queue a pointless write on each of the
two workers. And **a `mutate` that raises is the caller's own bug and is left
to surface** — the locks release either way, because a lock held after an
exception is a store that hangs rather than one that lost a row.

**What is deliberately not done is the rest of the class, and the reason is
that they are not all the same failure.** About fifty functions here read a
store and write it back. A store keeping **one file per record** — `fan_radio`
is the shape — collides only when two people edit the *same* record, which is
the far narrower case and is why `hub/drafts.py` states one-file-per-draft as
a rule. The ones that carry this failure in full are the single-file
many-record stores, and the two migrated are the two where it was measured
rather than reasoned about. The remainder is a list to work down with whoever
knows each tool, not a sweep to land red: `test_jsonstore.py` holds the two
that moved so neither can quietly go back to a lock of its own, which is what
a per-process lock reads as when you find one.

**And that flock stops working the day the service stops having one disk.**
The pair above is right about the two ways a write is lost *on one machine*,
and the second of them is a lock on a **local sidecar file**. It serialises
workers that share a filesystem, which is every worker for as long as there
is one disk mounted at one path — and it is not the two halves of a
zero-downtime deploy, which are separate instances with separate
filesystems. Each takes its own flock on its own file, succeeds, and
serialises nothing. Measured, two processes each appending 30 rows through
`update_json()`:

    one data root  (one filesystem):   60 of 60 survived
    two data roots (two filesystems):  34 of 60 survived

That is the same arithmetic `hub/leads.py` already paid for — 30 of 60 — and
the fix for *that* was this flock. It is the worst shape a lock can fail in:
both processes reported success, and `status()` reported no `lock_error`,
because each flock really was taken. A lock that succeeds and means nothing.

Every instance talks to the same database, so a **Postgres advisory lock** is
the only lock available here that spans them, and `exclusive()` takes it
first wherever the mirror is on Postgres — the flock standing behind it for
SQLite, for a database that is down, and for the timeout case. `_advisory_key()`
is derived from `key_for()` rather than the absolute path, because two
instances whose roots differ have to agree on the id or they take two
different locks and serialise nothing, which is the bug wearing a hat.

**The lock alone was not the fix, and this is the half that is easy to miss.**
Serialise the two instances perfectly and each one still reads its **own**
copy of the file, mutates that, and writes the whole collection back. So the
read half of a read-modify-write has to come from the mirror too, which is
`_authoritative()`: the mirror is the only copy the instances share.
Confirmed red separately — 39 of 60 with the lock removed, 43 of 60 with the
read left on the disk, 60 of 60 with both.

Three rules on it. **The disk still wins in the cases where the mirror is not
the better source** — `durable=False`, a database that did not answer (which
is also what happens while it is down, so the degraded path is the old
behaviour), and a key in `_unmirrored_keys`, which is a file this process
wrote that the mirror would not take. Without that last one the fix is worse
than the bug: a payload over the size cap is permanently behind in the
mirror, and reading it would revert a real write on every save. **Nothing may
raise and nothing may refuse a save** — a failed lock is a reason to
serialise less, never to lose the write — so a timeout falls back, and is
*counted and named* rather than absorbed, since it is the return of the
measured defect. And **the lock connections are bounded** (`LOCK_MAX_HELD`),
because each holder needs a second connection for its own `_upsert` against a
pool of 5 + 10: unbounded, a burst of 24 writers took **120.3 seconds**
against 0.7 bounded, which in production is every write route stalling.

`status()` carries `lock_backend`, `lock_timeouts` and `unmirrored`, and
`/diagnostics` **warns on each** — the whole finding is a mechanism that is
invisible from every screen when it silently stops working, so leaving the
state in a JSON dict nobody opens would have left it exactly as invisible.
`test_jsonstore_locking.py` drives two real **processes** with two real data
roots, because threads share a filesystem and cannot show any of this. Its
gate is a **deterministic alternation** rather than the race: the race
version failed the unfixed code only three runs in five, because whichever
child finished first left the other a clean restore-from-mirror and nothing
was lost — a check that catches the defect sometimes reads as a flake
somebody re-runs. Alternating removes the timing entirely and still
reproduces it every time.

**What this does not fix, and neither does any lock.** `hub/leads.py` gets
the cross-instance lock for free, since it calls `exclusive()` — and
`leads.jsonl` is not mirrored at all, so two instances hold two different
lead books and there is nothing authoritative to read. The same is true of
every SQLite file on the disk. Those are a store problem rather than a lock
problem, and the lock is what had to be right first.

**And the log the whole Hub writes through was a file on that same disk.**
`hub/audit.py` is where every module files what it did, and it appended JSONL
to `/var/data`. Two things were wrong with that and only one of them was the
backup. The disk is outside the database backup, which is the argument this
module opens with. The other is worse and is what the flock above cannot
reach: **a file is local to one instance**, so the two halves of a
zero-downtime deploy keep two histories, each complete-looking, and
`/activity` shows whichever one the browser reached. This is the record
somebody reconstructs an incident from, and a record that depends on which
worker answered is not one.

The rows are `hub_activity` now, through the shared engine.

**`AUDIT_LOG_PATH` had to stop deciding anything, and that is the half the
obvious design gets wrong.** It names *where the file is*, and this
deployment sets it — so "the file when it is set, the database otherwise"
reads as a sensible migration switch and would have kept **production on the
disk** while every test passed on the new path. The reverse is as bad and is
why the database is deliberately not gated on Postgres: **78 of the 79 test
files that set that variable pin `DATABASE_URL` at a SQLite file of their
own**, so a Postgres-only rule leaves every one of them exercising the file
backend while production runs the table. A backend no test exercises is a
backend nobody has checked. The rule is *the database wherever the shared
engine answers*, which is Postgres in production and SQLite in a test — the
same shape at both ends. `test_audit_store.py` asserts both by name, and
reads the module's **AST** to require `AUDIT_LOG_PATH` to be consulted in
`_path()` and nowhere else: a rule reading it back in somewhere else is
invisible to any behavioural check that happens to run with it set.

**Read and tail are one function.** They were two because the file had a
cheap way and an expensive one — load the whole JSONL and reverse it, or seek
a byte window from the end and guess how many rows fitted — and a query with
an `ORDER BY` and a `LIMIT` is neither. Both names stay, because ten call
sites use one or the other and which of the two somebody reached for was
never a decision about the answer. Ordering is by **`id`**, which is
insertion order and therefore the exact analogue of the file's own: ordering
on the timestamp would reorder every row written inside one second, and the
log stamps to the second.

**The history is carried across once, across every instance.** The check for
whether the import has run and the insert have to be inside one lock, or two
workers both read *not yet* and the whole of it lands twice — so the marker
is written through `jsonstore.update_json()`, which is the lock the section
above exists to have built. Two more rules on it. The marker is **durable and
is not the row count**: "the table is empty" would replay the entire old file
the first time `rotate()` pruned it back to nothing, a migration firing again
years later on a log somebody had pruned on purpose. And **a file that could
not be read is not a file with nothing in it** — nothing is marked done on a
failed read, so the next boot tries again rather than recording that a
history we never saw had been carried across.

**A database that will not answer writes a file, and it is a different
file.** `log()` has always swallowed its own failures, because the action is
what matters and a log that breaks it is worse than a missing row. What that
now needs is somewhere to put the row, and it is deliberately *not* the
legacy log: those two answer different questions — one is the history being
migrated *from* and the other is what this process could not write *today* —
and one file holding both leaves the import unable to tell them apart, so the
rows written during an outage are either imported twice or not at all.

Three rules on the fallback, and the second was found by asserting the order
rather than the contents. `read()` puts those rows **in front of** the
table's, because they are newer than everything in it and left out
altogether an outage reads on `/activity` as an hour in which nothing
happened. The flush runs **before** the row being written, not after: run
afterwards it gives the outage's rows ids *above* the row being written now,
so the first thing shown once the database comes back is the outage with
everything since it underneath. And a batch the database then refuses is
**put back rather than dropped** — these are the rows that have already had
one chance to be lost.

**And it says so on a screen**, because the whole finding is a mechanism that
is invisible from every screen when it quietly stops: a database that will
not answer degrades to a file, silently, correctly, and for as long as nobody
looks. `/diagnostics` has an **Activity log** row — in the database, or
writing a file and therefore local to this instance and outside the backup,
with anything still owed named rather than counted quietly. `status()`'s
error is **one line**, because SQLAlchemy puts the statement and every bound
parameter into `str(exc)` and those are activity rows naming clients and
members of staff, on a page that gets pasted into chats: the
`services/provider_check.py` rule, wearing a traceback.

**Fifteen test files were reading the log off the disk**, and that is not a
detail of the migration — it is the same "check that cannot fail" this file
counts a dozen of. `test_proposal_progress.py` seeded its evidence by
appending JSONL and **went on passing** against a file no reader looks at.
They read through `audit.read()` now, which is what they meant to assert
either way; the two that back-date a fixture pass `time=` in the extras,
which `log()` documents as winning rather than leaving it as a property of a
dict update that a tidy-up would remove.

**What this does not fix.** `leads.jsonl` and every SQLite file on the disk
are still one-instance stores with nothing authoritative behind them. Those
are the next phase, and they are a store problem rather than a log one.

**Deleting a mirrored file needs `jsonstore.delete_json`, not `os.remove`.**
Removing only the file leaves the database copy to be restored by the next
read, so the delete appears to work and then undoes itself. This is the one
way the backup can bite you.

**And a test's throwaway data directory is the same trap wearing a harness.**
`key_for()` keys the mirror **relative to the data root** — deliberately, so a
production blob restores into a development checkout — which means a fresh
`HUB_DATA_DIR` in front of an *inherited* `DATABASE_URL` is refilled with the
last run's rows. The file looks isolated, the directory really is empty, and
the second run reads the first one's writes. `checks.yml` carried a paragraph
headed **RUN THIS FILE EXACTLY ONCE** recording exactly that: two lineages
each added a target-areas step, git merged both cleanly, and the duplicate
failed on the first run's rows.

Only one combination breaks. Setting **neither** is fine — the file inherits
both and they agree. Setting **both** is the `test_blog_publish.py` pattern.
Only *own directory, inherited database* gives you an empty disk in front of a
full mirror, and `test_dashboard_trends.py` and `test_google_index.py` were
the first two files in it: three failures and four, on the second run, every
time. They assign `DATABASE_URL` now.

**And the sweep that pins it asked for that pair by its spelling rather than
by what a file ends up with.** It looked for `HUB_DATA_DIR` *assigned* and
`DATABASE_URL` not — so a file that `setdefault`s **both** was invisible to
it, while reaching the identical state whenever only the database is set in
the environment: fresh directory, inherited mirror. Two were, and both write
durable rows, so both passed on the first run against a database and failed
on every run after. `test_io_records.py` reported "two rows under one number"
and `test_sales_status.py` a pipeline count; neither had anything to do with
the code it was testing.

Nothing could see it. **CI is structurally blind to this class**: every run
gets a new Postgres, so every file passes its first run for ever. It became
reachable the day a session-start hook began exporting `DATABASE_URL` for a
whole session — after which the second time anybody runs the suite, two files
fail for reasons the output cannot explain.

`test_jsonstore.py` reads either spelling now, and the exemption is
**evidence** rather than an assumption: the twenty-three files in the shape
that were run twice against one database and came back identical are named,
and a file in the shape that is not on that list fails. The note this
replaces claimed the same thing about thirteen files and was wrong about two,
because nobody had run them twice. Held to `check_stale_json_exemptions()`'s
rule in both directions — an entry naming a file that is gone, or one that
has since started owning its database, is named too — and it started green.
Fixing them all by pinning SQLite is what is *not* done: several boot the
composed app, where that would drop Sites Admin out of the gate, and a check
landing with two dozen findings it cannot act on is the one people learn to
skip.

**Two checks asking one question will answer it differently, and both
answers are on screen.** `/api/db/structure` and `/api/integrity` both report
who still writes JSON outside `hub/jsonstore.py`, on the same Diagnostics
panel, and each kept its own copy of the test. Integrity exempted build
scripts and repo tooling; the structure report did not — so the page read
**"1 file writes JSON outside hub/jsonstore.py — ad_builder"** directly above
an audit of the identical question that had found nothing. The file was
`modules/ad_builder/scripts/fix_safezones.py`, a one-off script that rewrites
layout JSON *committed to the repo*, where git is the backup; and `ad_builder`
is the Node renderer, which keeps no Python state on the data disk at all. So
the row named a module with nothing to move, and being contradicted on its own
panel is what teaches somebody to stop reading the panel. The rule is
`jsonstore.unmirrored_json_writers()` now and both callers read it, for the
same reason `hub/storage.py` and `hub/images.py` exist.

Two things that rule had to stop doing. It exempted each scanner **by
accident** — the test was `"jsonstore" not in src`, and each one's own
explanatory text contains the word, so rewording a string would have started
it reporting itself. And its exemption list had outlived its files: it named
`ui_check.py`, plus `hub/errors.py` and `hub/audit.py`, neither of which has
matched `json.dump(` since they moved to append-only JSONL. An exemption that
outlives what it exempted goes on covering whatever is written at that path
next, while the audit stays green doing it, so
`check_stale_json_exemptions()` names one — and it started green, which is the
only way it was worth adding.

**A resolved finding rendered in the same colour as an open one is not a
resolved finding.** `renderStructure()` painted every level that was not
`high` amber, so *"3 client key columns, joined on read"* — the row whose
entire content is that `hub/client_key.py` handles this — sat in warning
amber, and its presence turned the panel's header pill amber too. `low` is
grey here now, and the header counts only what is actually open. The panel's
standing help text had drifted the same way: it still said several modules
build their own database engine and identify a client their own way, on a
deployment where that reads 0 own engines and 13 sharing. A help paragraph
that contradicts every row beneath it costs the rows their credibility.
`test_jsonstore.py` asserts both halves, and that the two scanners return the
same set.

**`os.environ.get("HUB_DATA_DIR", "data")` is not the data directory.**
`HUB_DATA_DIR` is unset on this service, so that spelling silently resolves to
`./data` inside the container and is wiped on *every deploy* — not merely if
the disk is recreated. Page Image Optimizer and Tickets both had it, which is
where their saved jobs and field map were going. Use `jsonstore.data_dir()`.

**And a bare relative path is the same trap with the deploy wipe hidden.**
`hub/ad_assets.py` handed `jsonstore.write_json` the literal
`"ad_assets/runs.json"`, and `_atomic_write` resolves a path against the
**process working directory** — so on Render both of that module's stores
landed at `/app/ad_assets/*.json`, inside the container image, and `key_for()`
saw a path outside the root and keyed the mirror `abs:/app/…` rather than
root-relative. What made it survive review is that nothing was lost on the
ordinary path: the mirror is written at save time, `read_json` restores by
key, and `WORKDIR` is stable, so a redeploy really did come back with the data.

**What was lost is the repair.** `sweep()` walks the data root, so it never
scanned either file — measured, `scanned: 1` over a rooted store and this one
side by side — and that sweep is precisely what picks up a save made while the
mirror was unavailable. For every other store in the Hub it does; for these two
the gap stood until somebody saved again, and the next redeploy took the file
with it. Reproduced end to end: written with the mirror down, swept, disk
recreated, and the rooted store came back while this one read `None`. The
`abs:` key defeats the other half of `key_for()`'s own docstring too — a
production blob restoring into a development checkout.

**Both of that module's stores were also read-modify-write of a whole
collection**, the class `update_json` exists for, and it is not theoretical
here: the scheduled catch-up sweep and a rep pressing Migrate overlap by
design, there are two workers, and **eight concurrent records kept 1 of 8**.
What that drops is the only account of who moved which client's creative and
when. `apply_proposals` does its Knack writes *before* it touches the store,
deliberately — `update_json` holds a lock across both workers, and a network
call inside it would hold the other worker off for as long as Knack takes to
answer.

**The old spellings are still read**, so nothing already recorded is orphaned
— the `audit.LOG_NAMES` rule — and only while the rooted file is empty, or
removing a row would resurrect it from the old location. Each store moves
itself the first time it is written.

**And that fallback re-created the file it exists to abandon.** `read_json`
writes a restored blob back to disk, and a `default=` argument is evaluated
whether or not it is needed — so passing the legacy read as `update_json`'s
default did the old read on **every** run and rewrote the pre-move file every
time, for ever. The fallback belongs *inside* the mutate, which runs under the
lock and only where the rooted store is empty: once. The check written for it
first could not fail — it ran in a fresh directory where the mirror holds no
pre-move key, so there was nothing to restore and nothing to rewrite, and the
defect passed it. It seeds that key now, which is the live deployment's state
and not a fresh directory's. `test_ad_assets.py` asserts all of it and
sweeps `hub/` and `modules/` for the shape: a **string literal** handed to a
store function is unambiguously CWD-relative and is a finding, while a path
built from a call or a name is *not determinable* and is deliberately not
reported — a check with false positives is one somebody switches off, and
switching this one off costs the real finding. It started green, which is the
only way it was worth adding.

**And the reason first given for the test half of that was wrong.** Its commit
says `jsonstore` caches its engine on the first `_init()`, so a `DATABASE_URL`
assigned after `from hub import …` is a no-op. Measured, that is not what
happens: importing `jsonstore`, importing `hub` and importing `ad_assets` all
leave `_engine` at `None`, and a late assignment takes effect perfectly well.
The engine is opened by the first **read or write**, and in that file it was
`ad_assets.migrate()` in section 3 — a hundred lines above the assignment in
section 8 — so the engine was opened against the session's Postgres and ~70
`abs:/tmp/adassets-*` rows accumulated there, two per run. Moving the
assignment above the imports was the right fix for the wrong stated reason:
what makes it right is that after the first hub import *any* call may be the
first write, not that the import itself latches anything.

**A rule nothing enforces is one the next file gets wrong the same way**, and
the obvious enforcement does not work: a static check on where the assignment
sits reports `test_ad_copy.py` and `test_help_layer.py`, both of which assign
after their first hub import and **neither of which leaks a row**, because
neither writes anything durable first. That is the false-positive shape this
file names a dozen times over.

So the latch is gone instead. `_init()` re-resolves the URL and re-opens when
it has changed — `extensions.engine_for()` already re-resolves per call and
keys its pool on the URL, so that latch was the only thing freezing it. In
production the variable never changes, `wanted` equals `_engine_url`, and it
costs one environment read: measured, 300 writes in 0.45s against one engine
object with no switch reported. Three rules on it. **It never raises** —
`_database_url()` answers `""` where it cannot resolve, and `""` means *do not
re-resolve*, which leaves the engine exactly where it was; `_init()` is on the
write path, and a mirror that cannot name its own database must still let the
disk write succeed. **The switch is reported rather than made silently**,
because rows already written are in the previous database and are **not
moved** — a mirror that looks complete may be missing everything written
before it — and `status()` carries the count and **never a URL**, since a URL
carries a password and that dict is rendered into `/diagnostics` and pasted
into chats. And **a failure cached against one database is not a verdict about
another**: the retry cooldown is cleared on a change of URL, or a healthy
database would be held down for the rest of a window recorded about something
else.

That last one is what `test_jsonstore.py`'s section 10 had been quietly
asserting the opposite of. It simulated the database *waking* by swapping
`database_url()` and then required the cached failure to still hold — which
conflates two different things, because on Render a database wakes at the
**same** address and a URL that changes is a different target entirely. Both
halves are asserted separately now.
