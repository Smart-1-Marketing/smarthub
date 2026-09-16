## Data sources, and which are stale

| Source | How it's read | Freshness |
|---|---|---|
| Knack products (IOs) | live API, `hub/knack_products.py` (object_135), export as fallback | current |
| Knack websites | live API via `hub/knack_websites.py` (object_153), export as fallback | current |

| Knack object_153 (website registry) | live API, `hub/knack_websites.py` | current |
| Knack tickets | live API, `hub/knack_api.py` | current |
| Insites scans | own SQLite/Postgres tables | current |
| GoHighLevel | live API | current |

**The static JSON exports are the biggest known problem.** Products are now
read live: `hub/knack_data.search_client()` prefers `hub.knack_products`
(object_135) and falls back to the export, and Client 360 labels which source
it used — before that, a client's insertion orders showed the last export's
line-up while the Knack pull reported success, because the two are different
sources and only one was live.

**And the SEO section was the half still on the export.** That fix went into
`search_client()`, and `hub/seo.py` kept its own `knack_data.products()` read
— so Client 360 and the SEO list read the *same object* two different ways,
and the screen that decides who is on the SEO book at all took the stale one.
Nothing errored: a short list looks exactly like a complete one, so a client
whose SEO product was written last week read as a client we do not do SEO for,
and one whose product ended read as still on the book. `seo_clients_result()`
and `_client_base()` go through `_product_source()` now, which is the same
live-first, export-as-named-fallback shape, and **both** screens print which
source answered from **one** `products_note()` — two descriptions of one
staleness is how the list and the record come to disagree about whether a
number can be trusted.

The fallback is the load-bearing half and it is inherited rather than
rewritten: a live pull that raises **and** one that answers with nothing both
fall back to the export, because a client list that came back empty would read
as *we have no SEO clients* rather than as an outage. `test_seo_page.py`
drives both of those with the pull stubbed, since what is worth asserting is
what the section does when Knack says no.

**What is deliberately not taken from the live rows is the matching.** They
carry an `organization` beside the `client` where the export only ever had
one, and `search_client()` matches either — but the SEO section keys on
`client` alone, and widening it here would quietly pull one company's products
onto a sibling's record through a shared parent, which is what
`hub/client_groups.py` exists to do **on purpose and by opt-in**. The rows
came live; the rule that decides whose they are did not move.

**And `/qa` was the third reader on the export, behind two flags that made it
look like a one-line swap.** Every client report on that page — Active
Clients, No Dashboards, Lapsed, Lost by Partner, both Scorecards, the two
Analytics reports — is built by grouping `_client_groups()`, which read
`knack_data.products()` while Client 360 read the same object live. Same
failure as the SEO section, one page later, and silent in the same way: a
short book looks exactly like a complete one.

**The reason it had stood is that pointing it at the live source would have
made four reports go quiet rather than wrong.** `thisM` and `lastM` are
Knack's own flags and they exist **only on the export** —
`knack_products._row()` emits neither — so the swap alone would set both False
on every row: "billed this month" reads $0 for the whole book, `lost_by_partner`
reports that nobody has ever churned, `stale_90` loses the guard that keeps a
client we are billing off the lapsed list, and `no_gtm` loses half the test
that decides which clients are priority. Four confident wrong answers to fix
one staleness problem, each of them an *empty* answer, which is the shape this
page's own cache is built to refuse.

**And the flags do not mean what the reports read them as.** They describe the
month the export was generated **for**, and nothing recomputes them — so on a
deployment whose export has slipped a month, "billed this month" is a true
statement about a month that has passed, printed under a heading that says
otherwise. `export_state()` has known that all along and no report on `/qa`
asked it.

`knack_data.ran_in_month()` — the neighbour of `is_running()` written for the
Scorecards — answers the same question from the dates and the status, which
live rows *do* carry, against the calendar rather than against whenever
somebody last exported. On this deployment's own export the two agree
**exactly**, 373 of 373 rows for this month and 510 of 510 for last, which is
what makes this safe: every row of all nineteen reports is unchanged today,
and the change only bites when the export slips or Knack answers. That is
deliberately *not* the scorecard rebuild this file describes being removed —
nothing here is compared against a differently-measured number; `live` is
still `is_running()`'s union and only the two month flags moved, from being
read to being computed.

Three smaller rules. The two reports that read a row's flag **outside** the
grouping (`no_dashboards`' product fallback, `no_gtm`'s priority test) go
through the same computed test, or the fix covers the grouping and leaves two
call sites behind — and `test_qa_reports.py` sweeps the **AST** of `hub/`
for any product row's flag read, with `month_over_month()` named as the one
allowed reader **and its reason**: the dashboard scorecard is deliberately
measured against the export's own period, and that decision predates this one.
`products_error()` asks **whichever source answered** rather than the export
alone — those were one question while `/qa` read the export directly, and
asking the export now would refuse to measure on the strength of a file
nothing read. And the source is **named on the report**, appended once in
`run()` from what `_products()` recorded rather than by a table of which
reports read products: a report that asks gets the sentence, one that does not
gets nothing, and there is no list to keep in step. `products_note()` moved to
`knack_data` while it was at it — `hub/seo.py`'s own comment already said the
wording was knack_data's while the string sat in seo, which is how a third
screen comes to word it a third way.

**What is deliberately not here is a memo.** `products_error()` and
`_client_groups()` now ask within a few lines of each other and a scorecard
asks four times, so a minute's cache of the shape `_WEB_CACHE` uses next door
is the obvious addition. It was written and removed: it costs about a tenth of
a second a day, because these reports are built once and held by
`hub/report_cache.py`, and it buys a window in which a source swapped
underneath is invisible to every caller — `test_seo_page.py` swaps one, and
found the memo hiding it within minutes of it being added.

**Websites now read live too, and the split between the two readers is the
point.** `clients_registry.all_clients()` — which feeds client search, every
client picker, Client 360's lookup and the social content link — built its
domains from a 610-row `websites.json` committed to the repo and refreshed by
hand, while `hub/knack_websites.py` had been reading *the same object* live
for the domain record, the renewals calendar and the orphan list. The Hub held
a live answer and a stale one to "what websites does this client have", and
every load-bearing reader took the stale one — silently, because a short list
looks exactly like a complete one, so a site added in Knack last week read as
a client with no website at all.

`knack_data.websites()` prefers the live pull and falls back to the export,
the shape `_product_source()` already had. Four rules on it:

- **A failed pull never empties a good export.** An outage that turned 610
  sites into zero would take every domain-keyed join in the Hub apart with
  nothing on any screen saying why. Stale beats empty, and the reason travels
  with the rows.
- **Live rows arrive in the export's own field names.** `website_row_from_live()`
  is the one mapping — it was written inside `_attachment_only_websites()` for
  the attachment path and is read from both now. Eight call sites needed no
  edit, and none of them can tell which source answered.
- **`summary()` deliberately keeps reading `export_websites()`.** It measures
  the dashboard scorecard against the export's own period and its `active`
  field, which object_153 does not publish. Pointed at the live list it
  reports **2 active websites and no H&M billing** — `test_knack_websites_source.py`
  asserts exactly that number, because it is a confident wrong answer on the
  CEO's dashboard rather than an error.
- **Nothing is invented in the mapping.** `active`, `hmFreq`, `notes`,
  `created` and `domainCost` are absent from a live row rather than defaulted:
  a `False` `active` would read as a dead site on every row.

Client 360 and `/status` say which source answered, exactly as the products
card already does — a stale export looks identical to live data on screen,
which is the whole reason this went unnoticed.

**And that assertion was counting on a 610-row export.** It proved the
scorecard read the export by comparing `websites_total` against the export's
own length and then against the live pull's — and the second half only means
anything while the two lists are different lengths. That was free while the
export was committed and 610 rows long. The day it moved out of source control
the fixture behind it held **two**, which is exactly how many rows the test's
synthetic live list holds, so the guard could no longer tell *reads the export*
from *reads whichever source happens to hold the same number of rows*, and it
reported a working `summary()` as broken. Going red is the lucky half: the same
collision one row the other way would have passed on the bug as well, which is
the failure `test_help_layer.py` had to undo when a count was compared against
a set that collapsed the duplicate it was looking for.

**The fix is to stop counting.** Whether two lists are the same length is a
fact about a fixture; whether `summary()` consulted the live pull at all is a
fact about `summary()`, and no fixture can collide with it. The live reader is
a function that records having been called, and the assertion is that it never
was — so the property the test exists to hold is asserted directly rather than
inferred from an arithmetic coincidence, and the next person to re-sanitize a
fixture cannot silently switch it off. The obvious repair — padding the live
list until the counts differ — was written first and thrown away: it keeps the
comparison alive and therefore keeps the collision possible, one fixture later.

**`campaigns.json` and `live_products.json` are gone.** 7,854 rows and 2.1 MB
of the first, 96 KB of the second, and not one reference to either anywhere in
the repo — no reader, and for campaigns not even a `campaigns()` function.
They were described here as *stale*, which implies a refresh would fix them;
nothing would. Real exports no longer live under `clients_app/data/`.

**Fallback exports are private.** `hub/knack_data.py` reads them only from the
directory named by `CLIENTS_DATA_DIR`, which must be a private mounted volume
outside the checkout. `.github/workflows/` uses sanitized fixtures. Never
commit real client, campaign, website, analytics, or billing exports.

**A staleness check measured against the wrong clock is worse than none.**
`/status` read `products.json`'s mtime and printed it as "Refreshed Xh ago",
warning past 48 hours — and `data_age_hours()`'s own docstring already said
why that is wrong: in a Docker deploy every file is written at image build
time, so it measures **time since the last deploy**. Wrong in both directions.
A months-old export reads as "refreshed 2h ago" for two days after any
deploy, and a container simply left up for a week warns that data nothing has
touched needs refreshing. The row is not about the data and is read as though
it is. It reads `export_state()` now — the month the export was generated
*for*, against the calendar — which is the signal the dashboard and
`hub/housekeeping.py` already share, so the three cannot disagree.

**The URL is the join key, not the name.** Eleven field names hold a URL
across this codebase (`url`, `domain`, `website`, `web_url`, `site_url`…).
`hub/client_context.canonical_domain()` is the single place that decides what
a domain means. Name matching produces false positives — "Riverside HVAC" vs
"Riverside HVAC LLC" — and is why billing audits report phantom problems.

**One client key, derived on read.** The modules key a client three different
ways and always will: Scans on `domain_key`, Ads and Google Access on a typed
`client_name`, Image Picker on its own table. `hub/client_key.py` joins them
without changing any of it — `client_key(name, url)` returns `d:<domain>` where
there is a URL and `n:<name-slug>` where there is not, and `resolve()`,
`same_client()` and `crosswalk()` are built on that. Use them rather than
comparing names.

Two rules it enforces, both learned the hard way:

- **Never store the key.** `create_all()` creates missing tables and never adds
  a column to an existing one, so a `client_key` column would be silently
  absent on the live Postgres while every local test passed. Deriving it also
  means a client renamed in Knack is re-joined on the next request instead of
  leaving a stale copy behind.
- **Never match on a substring.** `resolve()` matches on domain, then on an
  exact normalised name, and offers a near match only when exactly one client
  can possibly be meant — otherwise it returns *no* match and lists the
  candidates. The billing audit used to take the first Knack name containing
  the sub-account name, so "Acme" was attributed to whichever of Acme Plumbing,
  Acme Roofing and Acme Electric came out of the dict first, and nothing in the
  report showed that a guess had been made.

`/api/clients/crosswalk` shows what is joined, what shares a domain, and what
carries a name with no URL and therefore cannot be joined to anything.

### Where a model proposes, and what stops it deciding

Three places where the Hub already held everything a model needed and asked it
nothing. All three are the same shape, and it is the shape that makes them
safe: **the model proposes a candidate, existing code decides, and a person
presses.** None of them writes anything by arriving. `test_ai_proposals.py`
asserts that for all three at once, including that the source of each carries
no path to a write.

**One reader, three configurations.** `hub/name_reading.py` holds the
batching, the grounding check, the store and the give-up; `site_names_ai`,
`invoice_names` and `google_names_ai` are a prompt, a store and a rule about
what is not worth sending. Three copies of that machinery is the drift
`hub/storage.py` exists to stop, and the safety argument is a property of the
shared reader rather than of each caller remembering: **it takes a prompt and
never a client book**, so no configuration of it can name a client.

**The model reading a project name is never shown the client list.**
`hub/site_names_ai.py`. The hand-written shapes in `hub/site_names.py` turned
42 raw matches into 305 exact and 60 candidates out of 1,021 projects; 229 more
are placeholders and are correctly unmatchable. The rest carry a real business
name in a shape no rule anticipated, and each is a client whose website cannot
be joined to anything. So a model is handed project *titles* and asked one
question — which run of words is the business — and what it returns goes into
`site_names.exact_matches()` against the real book like any other candidate.
The client book is not in the prompt, so it cannot name a client; a bad answer
costs a candidate nobody accepts.

Four rules. **A reading must be *in* the title it came from** —
`_is_grounded()` requires every word of the answer to appear in the original,
because a model asked to extract a name will occasionally tidy it, and "SERVPRO
of Fresno NW" coming back as "…Northwest" is a different string that matches a
different client or none. Ungrounded readings are dropped and **counted**.
**A placeholder is never sent**, since `is_placeholder()` has answered already
and paying a model to find a business in "S1M Test" invites it to. **Nothing is
read twice**, keyed on the normalised title, which is what makes the whole
portfolio one pass of about forty calls and every later run free. And it is
**a button, never a page load** — `suggest()` is opened several times a day and
the call is billed, the rule `hub/brand_lookup.py` arrived at. The readings
live under `jsonstore.data_dir("site_names")`; a bare relative path lands in
the repo checkout and is wiped on every deploy.

**A client sends forty photographs and nothing looked at any of them.**
`modules/image_picker/vision.py`. `alt_text` on an upload comes from
`body.get("alt")` — typed, or blank — and the gallery had no search, so what a
client sent was forty thumbnails nobody could find anything in. Meanwhile
`modules/seo_images` runs vision on images a rep picked and
`hub/video_library.index_backlog()` describes every clip in two folder trees.
This is that sweep aimed at the missing bucket, inheriting its rules rather
than restating them: a **closed tag vocabulary** (terms outside it dropped and
counted, or the search vocabulary grows in silence), **three attempts and then
given up on in writing** (a give-up held in memory forgets itself on the next
deploy, and one unreadable file otherwise costs a vision call an hour for
ever), and a **wall-clock budget** beside the count, because scheduler jobs
share one thread.

Its own rule is the important one: **a description is an observation, never the
alt text.** The reason `alt_text` is sometimes blank is that nobody typed it,
and the reason it is sometimes filled is that somebody did — a sweep that wrote
into it would overwrite the second to fix the first, silently, on wording a
client may have chosen. So it is stored beside the image, drawn dotted, and
offered into an **empty** field only; `accept()` is the press and refuses a
field that is not empty **by name** rather than reporting a clean success. The
row is its own table (`image_picker_descriptions`) because `create_all()` never
adds a column to an existing one. A file that is not an image is given up on at
once rather than retried twice more to learn the same thing.

**A ticket arrives with a paragraph describing the work and every dropdown
above it untouched.** `hub/request_triage.py`. object_107 writes a type and a
billable flag; object_121 writes a Campaign Support type, a Timeline and a
rush. The classification is sitting in prose the person has already written,
and the dropdown gets skipped — which is `hub/knack_api.py`'s own finding one
step on, that twenty questions became eight answers and twelve blanks.

It proposes **into the empty fields only** — the `contact_suggestions()`
overlay rule — and the gate is on the endpoint as well as the form, because a
rule the form keeps while the write breaks it is not a rule. **Every suggestion
is one of Knack's own published choices, verbatim**, matched exactly or on
punctuation and case alone and never on the nearest: Knack refuses the *whole
record* over one bad choice, so an invented option would cost the request
rather than the field, and anything else is dropped and counted. A **connection
is never offered** (it is a record id, not a name), nor is a field publishing
fewer than two options, nor a free-text box. A field it cannot answer is **left
out** rather than filled with something plausible — thirteen rows of a guess is
a form somebody stops reading, and one wrong row in it is the one that gets
sent. Nothing is applied by arriving: `KnackForm.triageButton()` draws each
suggestion dashed with the reason beside it, Keep takes it and Dismiss puts the
field back. One control, drawn once, so both objects get it and a third form
added later gets it without being edited.

**The third form was already there, and did not get it.** That sentence was
written about `hub/static/knack-form.js`, which draws the web ticket, campaign
support **and** the Ad Copy Request — and only the browser half was shared.
The route knew two kinds, so `ad-copy.js` had nothing to call and drew no
button, on the one of the three whose whole content is a paragraph describing
a change. `/api/client/requests/triage` reads a table of three now, and an
unrecognised kind is **refused by name** rather than falling through: it was
written `if ticket … else campaign`, so any other spelling answered with the
campaign change form's dropdowns against an ad copy request's prose — every
suggestion then either dropped for not being one of that field's options or,
worse, kept for a field of the same name on a different object. Both read as a
button that half works. The tag each kind bills under is in the same table,
because left at `tickets` every triage call in this Hub reads as the ticket
form's on the page that says what the models cost.

**It reads two boxes, because the request is written in two.** What is being
asked for is split across *Change for What?* and *Is there Something Else we
need to know?*, and the deadline or the URL change is as likely to be in the
second — so `textKey` takes one key or several, and reading one of them would
miss the half the answer was in.

**And the control now keeps its own stated rule.** Its comment has said
*"hidden entirely where there is nothing to suggest into — a button that can
only ever say no is one people learn to skip past"* since the day it was
written, and the code drew the button on every form and only said so once
somebody had pressed it. What is knowable before the press is whether the
object publishes any choice field **at all**, which is a fact about the form
rather than about what has been typed into it; a form with none gets no
button. Deliberately *not* hidden when every choice field merely happens to be
answered — those can be cleared, and a control that vanishes while somebody is
filling a form in is worse than one that says so. `emptyChoiceKeys` also
carried its own copy of the four control names beside `request_triage`'s
`CHOICE_CONTROLS`; `test_ad_copy.py` holds the one list against the other,
because a control added on one side and not the other means the button offers
a field the server will not answer for, with nothing on screen saying so.

**A charge is joined to a record through a sentence somebody typed.**
`hub/invoice_names.py`. A domain renewal is invoiced to the media partner —
one invoice to a radio group carries five renewals for five businesses — so
the only place the client appears is the free-text line description.
`parse_description()` and Sites Billing's five rules answer most of them; what
both keep is an explicit bucket for what they could not join, and **that
bucket is the only place this is used**. A line the rules answered is never
sent: it costs a call to be told what is known, and it invites a second
opinion on a domain, which is an identifier rather than a guess.

What comes back resolves through the matcher's *own* name passes and can never
be better than **`probable`** — and `domain_purchase.year_to_date()` already
counts a probable charge as having no record here, in both directions, until
somebody presses Link. So a reading can move a charge from *nothing to look
at* to *here is a candidate*, and it cannot mark a renewal billed. That matters
more here than anywhere else, because a charge attributed to the wrong
client's domain marks a renewal billed that was not **and** hides a real one
from the reconciliation.

**A Google resource label is as improvised as a project title.**
`hub/google_names_ai.py`. `google_links.suggest_for()`'s loosest rule is a
shared word, and what it cannot do is read "FabLocal – SERVPRO Fresno GTM" the
way a person does. The reading goes through the *same* `client_key.resolve()`
the raw label already goes through, so the rules that decide are unchanged and
a reading only changes which string is asked about. `_add()` keeps the best
confidence anything gave a client, so it can never displace a recorded id or a
domain — those are identifiers and a reading is a guess about what somebody
meant. A label made only of platform words is never sent.

**The audit a prospect reads had 26 findings and no reason to call.**
`hub/audit_summary.py`. `widget_audit_report.html` is tables; underneath it
`OPPORTUNITIES` carries a measured finding and what it costs them, and
`spend()` leads with what the business is already putting into Google and Meta.
The rep-facing half already feeds a model; the client-facing half stopped at
the tables. Two paragraphs now open it, and every rule on them is a way that
document becomes a confident wrong claim about somebody's business:

**Only what fired reaches the prompt** — a finding that did not match is absent
entirely, so there is nothing to soften into "you may also want to consider".
**A total that excluded something says so**: Meta publishes the ads and never
the spend, so a paragraph quoting the total without `total_excludes` is a
five-figure understatement printed confidently, and it is required in the
prompt *and* checked on the way back. **A promise is discarded rather than
patched** — the Smart 1 Labs precedent, which throws copy away rather than
paraphrasing it into something nobody wrote.

**And the money rule is grounded rather than banned.** The summary is supposed
to lead with what they are already spending, and that figure carries a dollar
sign — so a flat refusal of every `$` refuses the correct answer, which is how
a check comes to be switched off (`hub/qr_codes.py`'s note about a QR warning
that fires on every social spot). A figure is allowed when it is one we
measured and refused when it is not: the grounding rule applied to a number
instead of a name.

**And it compared the string, so the measured figure came back as an invented
one.** `$2,400` in the facts and `$2400` in the summary are the same amount and
were two different strings, so a model that merely re-typed a figure it had
been handed — which is what a model does with a figure — was reported as having
invented it. `$2,400.00` went the same way. Every consequence below is correct
on its own and they compound: the **whole summary is discarded** rather than
patched, the report **renders nothing** because `widget_audit_report.html`
guards on `summary.text`, the `why` explaining it is read by **no template**,
and `for_scan()` **stores the refusal** on the stated reasoning that it "will
not change on the next view" — which is true of a real refusal and false of
this one. So one dropped comma cost that prospect's audit its opening
paragraphs *permanently*, for them and for every rep who opened the link, with
the only record in a JSON blob nothing reads.

Compared as an **amount** now, and the rule is not loosened anywhere: an
amount nobody measured is still refused, a figure that cannot be parsed is
still refused rather than passed as measured, and **rounding is still not
tolerated** — `$2,437` written as `$2,400` is a different amount on a document
about somebody's money. What is **reported rather than fixed** is that a
genuine discard is invisible to everybody: `why` reaches no screen, and there
is no staff view of the summary to put it on, so building one is a feature
rather than this fix. And `_forbidden()` enforces a discount, a promise, a
guarantee, a timeline and our own name — **not** product names, which the
prompt asks for and nothing checks, because a list of product names has to
match ordinary English ("local listings", "display") and a false positive
there discards a correct summary, which is the failure being undone here.

**One call per audit, ever.** `for_scan()` writes on the first open and reads
thereafter — a prospect refreshing, a rep checking the link and the mailed copy
opened on a phone are three views of a paragraph that cannot have changed.
Keyed on the scan's `public_id`, so a re-scan gets its own rather than
inheriting last month's. A summary that could not be grounded is **absent
silently**: the report renders as it did before, because a line saying "we
could not summarize this" is a sentence about our tooling on a document about
their business.

### A client with no URL is invisible, and the URL is usually not missing

`/tools/sites-match` had one half of this: it proposes a client for every
Simvoly project by domain. It now only proposes **live** ones. Simvoly gives a
project ACTIVE, TRIAL or EXPIRED, and Sites Admin keeps CANCELLED and SUSPENDED
beside it for the two states Simvoly cannot express; an expired project's
domain has usually been repointed or picked up by somebody else, so matching a
client to one attributes them a website that is no longer theirs. What is
skipped is counted and named — "we checked 1,200 projects" and "we checked the
380 that are live" are different claims — and a toggle shows the rest.

**And a Simvoly project name is not the business's name.** Where there is no
domain to match on, the name is all there is — and matching on the raw project
title found 42 of this deployment's 1,021 projects, because that is not what a
project is called. 548 of them begin with a **media partner**
("TMRG - JWS Pottery", "FabLocal -  SERVPRO of Fresno NW"), 249 are
**placeholders** naming a person rather than a company ("Anna's Website",
"chatita521@yahoo.com's Website", "S1M Test"), and a good number carry a
trailing marker describing the job rather than the client ("Helena Valley
Addiction Services - 2026 Refresh"). `hub/site_names.py` reads those three
shapes and hands the matcher **candidates a human confirms**, each saying how
it was derived: the same portfolio export then matched **305 projects exactly
and offered a candidate for 60 more**, with nothing ambiguous.

Four rules in it, each a way to be confidently wrong:

- **A placeholder is named as one, never matched loosely.** A fuzzy pass over
  "Anna's Website" eventually finds an Anna and attaches a stranger's site to
  her. Those are counted on the page as *names nobody*, which is a different
  situation from a matcher that found nothing.
- **A name two clients answer to proposes neither**, and shows both.
- **A remainder that is only a label is not a name.** Stripping the prefix
  from "Elsie Consulting - Main Site" leaves "Main Site", which identifies
  nobody and would join every project called that; the trim is done on the
  *parts* rather than the string, or it cuts the word "Site" off the end and
  leaves "Elsie Consulting - Main" — a shorter version of the same wrong
  answer.
- **A shared word that identifies nobody is no evidence.** The same rule
  `hub/google_links.py` applies to its word index. It is also the cheap gate
  before the expensive ratio: without it the pass takes 24 seconds instead of
  1.2, and the two suggestions it costs were one right and one wrong.

**The substring rule that ranked the media partner above the client.**
`knack_websites._similar()` scored a containment at a flat **0.92** — above
almost every genuine resemblance — which is the rule `hub/client_key.py` exists
to refuse, wearing a score. A Simvoly project is named "<partner> - <business>",
so every one of FabLocal's thirty-seven SERVPRO franchises contained the string
"FabLocal" and was offered, **top of the list**, as the website of *FabLocal*:
on this deployment's own export the top suggestion was the media partner rather
than the client on **39 of 242** suggested rows, and accepting one files a
client's website under their agency. It is the ratio now, and its normaliser is
the shared one — the local copy ran the words together, so "ab cd" and "abcd"
read as one business. A genuine containment still clears the threshold on its
own merits ("Smitty's Fireplace" against "Smitty's Fireplace Shop" is 0.88)
while "Acme" against "Acme Plumbing" is 0.47 and is refused, which is the
point. `suggest_for()` is also handed the *cleaned* name now: comparing the raw
"FabLocal -  SERVPRO of Southwest San Antonio" against the registry ranked the
**neighbouring** franchise above the right one, because half of what it was
comparing was the media partner.

**A project with no domain is exactly where the name is all there is, and it
was offered nothing at all.** Those rows were listed under "No real domain yet"
with no candidates and no button. They carry the name matches now — and the
confirmation never sends the domain, because the domain on those rows is a
*platform* one (`something.simvoly.com`) and attaching that to a client would
file every unlaunched site under whoever was confirmed first. Confirming a
match on a row that does have a real domain now sends it, too: without it
`apply()` could only write the Simvoly project, so a confirmed match landed in
one of the four systems and Client 360 went on saying the client had no
website — the join real and invisible, which is the failure `hub/domain_links.py`
exists to stop.

The other half is `hub/client_urls.py`. `client_context.url_audit()` could
already say *which* clients have no URL, which is the useless half: a client
with no URL cannot be joined to a scan, a brand lookup or anything else keyed
on domain, and a list of names nobody can act on does not change that. Their
website is rarely actually missing — it is in a different table. So five are
read and grouped by canonical domain: the **click-thru on their live
products** (`knack_products.scan_domains()`), the **Knack website registry**,
our own **live Simvoly projects**, their **site scans** and their **Google
access requests** — the last two through `client_key._read_store`, which
already handles a table that does not exist yet.

**A file host is not a website, and this is not hypothetical.** Run against
this deployment's product export, *every single* click-thru domain was
`res.cloudinary.com` (33), `drive.google.com` (22), `we.tl`, `dropbox.com` or
an S3 bucket — where the creative was delivered from, not where the campaign
points. Without `NOT_A_WEBSITE` the tool would have proposed Cloudinary as the
website of thirty-three unrelated clients, a rep would have accepted one
because the row looked plausible, and every domain-keyed report would then have
agreed that several companies are the same business. Rejected sightings are
counted and named on the page rather than coming back as silence.

Agreement is the confidence: two independent sources on one domain is close to
proof, one is a suggestion, and the proposal shows which sources and why. Names
match exactly or not at all (`client_key.normalise_name`) — no substring, no
fuzzy pass. A source that could not be read is reported by name, because
"Knack is down" and "Knack has nothing for them" must never look alike.

Accepting one writes a small **overlay**, not an edit: Knack owns the client
record and this Hub does not write to it, so the day the real record gains a
URL that one wins. `clients_registry.all_clients()` applies the overlay only to
clients that still have none, marks the row `url_source: "discovered"`, and
**does not touch `source` or `is_house`** — an earlier shape of this reused
`house_clients()` for the same job and quietly relabelled real Knack clients as
ours.

**The overlay holds a list, because a client has more than one website.** The
shop, the campaign landing pages, the microsite for one location. `accept()` is
additive and mirrors the primary onto the row's `url`/`domain` so the
one-URL-per-client readers are unchanged; `sites_of()` reads a row written
before the list existed as a one-item list rather than migrating it.

**And accepting one has to stick.** It did not: accepting a domain was followed
by the same client being proposed the same domain again on the next scan, as if
the click had done nothing. `clients_registry` caches for two minutes *per
process* and there are two gunicorn workers, so the scan after an accept
usually runs in the worker that never saw it. `missing()` reads the overlay
directly and treats an accepted client as answered — the file is the durable
record, and it decides rather than whichever cache answered.

### A match is not one write

`hub/domain_links.py`. Matching a site used to write `internal_client_name` on
the Simvoly project and stop, so a rep who matched a site opened Client 360 and
found the client still had no website: the join was real and invisible, which is
the same as not having made it. `attach(domain, client)` writes all four —
the Hub's client overlay, the client's 360 record (`seo.set_link`), every live
Simvoly project on that domain, and the client onto the Knack website record —
and **reports each one separately**. "Attached" and "attached in two of four
places" are different outcomes, and one tick for both is how a rep learns not to
trust the tick. `sites_match.apply()`, the Match Clients page, the orphan list
and the Sites Admin table all go through it, so there is one description of what
attaching means.

A project already carrying a *different* client's name is never relinked
without `force`: a wrong `internal_client_name` attributes revenue to the wrong
client, and quietly overwriting one is worse than refusing to.

### A row with no client needs a customer picker, not a signpost

The domain cell on the Sites Admin table is a pair of halves and only one was
built. A project that already had a client could search orphan domains; a
project with **no** client — the far more common row, and the one somebody
opens the page to fix — got "there is no client to attach a domain to yet …
use Match clients in the Hub" and stopped. A row that reports a problem beside
a control that refuses to fix it is not a control, and sending somebody to
another screen to find the same row again is how a list stays unactioned.

Both halves are offered now, from the same cell, through the one
`/api/domain/attach` that writes all four systems and reports each. The
customer half is a searchable list of real clients and never a text box, for
the reason `client_key` gives at length: a typo'd name files the site under a
client nothing joins to and still reads as success.

Two things kept this invisible. `/sites/projects/<id>` **500'd on every
visit** — `project_detail.html` posts its "Check plan limits" form to
`url_for('website_check_limits')` and no route of that name existed, and Flask
raises `BuildError` while *rendering*, so it was never a broken button, it was
the whole page. `simvoly_client.check_limits()` had been written and had no
caller at all, which is `TICKET_CREATE_FIELDS` again. And a `url_for` to a
missing endpoint was invisible to every check we had: `tools/linkcheck.py`
reads URL literals and an endpoint name is not one. It checks them now —
against the route table of whichever app renders that template — and a
template nothing renders is *named* rather than failed on, because a check
that starts life red is a check somebody switches off.

### Orphan URLs — the other direction

`domain_links.orphans()` answers "whose site is this?", which is asked more
often than "which project is this client's". Same four systems, read rather
than written: a website record with no organisation, a live Simvoly project with
no internal client name, a site scan and a Google access request nobody filed
against a client. One row per canonical domain however many systems saw it, a
source that could not be read named rather than counted as zero, and a file host
rejected *and counted* — which is why `_orphans_knack` iterates `rows()` rather
than `knack_websites.orphan_rows()`: everything a source offers goes through
`add()` so the rejects can be named. It is on Match Clients, with a search box
and a client search per row, and in the **domain column of the Sites Admin
table**, where the person looking at an account can close the pair without
leaving the page.

### object_153 is written now, not only read

`hub/knack_websites.py` pins the website registry's field ids and writes them
through `knack_api.coerce_field()` against the *live* schema rather than a
second copy of those rules: a connection is resolved to the one record it can
only mean, a value Knack does not publish is **refused by name**, and every
write returns `rejected` — Knack refuses the whole record over one bad value, so
a value it would refuse is refused here and the rest of the record still goes.
Reads are cached for a minute, because `suggest_for()` is called once per
unmatched project and uncached that was a full paged pull of the object each
time.

The **domain record** on Client 360 — website live date (`field_3048`), client
status (`field_3193`), did we buy the domain (`field_2964`, asked in those words
rather than in Knack's "S1M Purchase Domain for Client?"), purchase date
(`field_3063`), renewal date (`field_3101`) and registrar (`field_2926`) — is
drawn from that schema, so a dropdown's choices are Knack's own. A domain with
no object_153 record says so rather than drawing empty boxes that cannot save.

**A registrar we recorded and a registrar WHOIS observed are different claims.**
Where `field_2926` is empty, the latest Insites scan of the same domain usually
knows (`domain_age.registrar`, with the registered and expiry dates beside it).
`registrar_for()` offers it *labelled as observed*, to be copied in by a person
— never written back on its own.

**The domain record is the second column of the website, not its footer.** It
sat underneath a card that already had ten rows in it, below the fold, so the
live date and the renewal — the things somebody opens that card for — were
reached by scrolling past everything else and mostly were not. One website is
a two-column block now, stacking under 900px where two definition lists stop
being readable.

**And a question that cannot apply is not a blank somebody forgot.** With "did
we buy the domain?" answered **no**, the purchase date, the renewal date and
the registrar are not missing data — there is nothing to record — and a panel
that goes on asking for them reads as unfinished for ever. They are hidden,
with two rules on it. **Only the empty ones**: a registrar we actually hold
stays on screen whatever the tickbox says, because hiding a recorded value is
the panel deciding the record is wrong. And **never in silence** — what was
left out is counted in one line with a link that brings it back, since a panel
that quietly gets shorter is one nobody can tell from a panel that failed to
load. *Not answered* is not *no*, so an unanswered question hides nothing.

**An object number in front of a rep is not information.** `object_153` and
`field_3298` are pinned in the code for the reason this file gives at length,
and they were also being printed onto Client 360 and the renewals page —
where they name nothing a person can act on and make a working panel read as
a debug screen. The prose says *the website record*; `test_domain_links.py`
asserts no `object_`/`field_` reaches any of the five strings these modules
hand a page. Same reason the save button says **Save** rather than *Save to
Knack*: which system it lands in is the Hub's business, not a decision the
person pressing it makes.

### The same join, for Google accounts

`hub/google_links.py` and `/tools/google-match`. `hub/google_index.py` already
sweeps every connected Google login across GA4, Tag Manager, Search Console and
Business Profile and joins each resource to a client — attached, then domain,
then an exact name. What nothing did was the half left over: the resources it
could not join to anybody were counted on no page and actionable nowhere.

The orphan list is that half, searchable, with a suggested owner per row and
the evidence for it. The suggestions are deliberately **looser than the index's
own matching** — a fuzzy hit written into a stored index becomes a fact nobody
re-examines, so these are proposals a human accepts:

- **recorded** — Knack's website record already carries this exact GA or GTM id
  against a client. Not a guess at all: object_153 records what the client uses
  whether or not anybody connected the account, which is why this finds owners
  the index cannot. A GA4 property summary carries no URL, so for most of them
  this is the only hard evidence there is.
- **domain** — the resource carries a URL that is a client's domain. The index
  only misses this when several client records share the domain, so **all of
  them are offered** rather than one being picked — that is the guess the
  billing audit used to make.
- **name** / **possible** — an exact normalised name, or a near name or the same
  registrable name on another TLD, labelled as worth an eyeball.
- **possible, on a shared word** — the loosest rule and the last one tried. A
  GTM container called "Buckeye Marina - new" matches a client filed as
  "Buckeye Lake Marina" on none of the above, and a person reading the row can
  see in a second that they are the same business. The row names *which* word
  did it, because that is the only evidence there is. Company and platform
  words (`llc`, `inc`, `analytics`, `container`) are not words that identify
  anybody, and a word shared by more clients than a ceiling **computed against
  the book** identifies none of them — on a client list where a tenth of the
  names contain "heating", matching on it proposes a tenth of the book for
  every resource, which is worse than proposing nobody because it buries the
  two rows that meant something. Capped at three, for the same reason.

**A platform that refused is not a platform with nothing in it.** Each fetcher
in Google Finder swallows its own exception and returns an empty list — which
is right, since one platform failing must not cost the other three, and is also
how "this login has no Tag Manager containers" and "Tag Manager refused this
token" came to look identical on every screen. A login consented before a scope
was added to `SCOPES` keeps the grant it was given: Google does not widen an
existing refresh token, so the call 403s for ever and the page shows nothing.
Every sweep now files a note per login per platform — **ok** (with a count,
which may legitimately be zero), **refused** (a scope this token never got, or
an API not enabled; reconnecting re-consents, because the connect URL forces
`prompt=consent`), **failed** (we could not ask) and **disabled** (we did not
ask — Business Profile is behind `GOOGLE_GMB_ENABLED` because those APIs need
per-project access granted by Google on top of the OAuth scope). The notes ride
on the stored index, so `/tools/google-match` can say why a platform is empty
rather than drawing a clean nothing, and an index built before they existed
reports *not measured*.

**A rate limiter with no memory makes the retry path the normal path.** Tag
Manager refuses far faster than Analytics, so `gtm_get` paced itself at a
fixed 0.35s and retried on 429. The live service is what showed the fixed part
was the half that does not work: one sweep of this login's **180** Tag Manager
accounts logged a 429 on very nearly every *first* attempt, paid 1s + 2s + 4s
of backoff to push most of them through, exhausted its four attempts on 13 of
them — and then the next account started again at 0.35s and rediscovered the
same refusal from scratch. Roughly two and a half requests spent per account,
440 seconds of wall clock, and every wasted one counting against the 10,000-a-
day project quota exactly as a useful one does. Nothing was broken and nothing
read as broken: the containers were mostly there, just slowly and not all of
them.

So the interval **adapts**. A 429 widens it for everything that follows, a
sustained clean run narrows it back, and Google's own `Retry-After` beats our
guess whenever it sends one. Widening is fast and recovery is deliberately
slow — the opposite ratio oscillates, spending a 429 to rediscover the ceiling
every twenty calls. The interval lives behind the same lock that serialises
the calls, which is what makes it *shared*: the limit Google applies is per
user, so a refusal one of the eight threads meets is news the other seven
need. `gtm_pace_state()` reports what it settled at, because an adaptive
limiter nobody can inspect is a magic number that moves.

**And a refused account keeps its last reading.** Some will be refused however
politely we ask, and dropping their containers reports this login owning fewer
than it does — a smaller number, in a complete-looking list, with nothing
saying a reading is missing. `fetch_gtm_items` takes the previous sweep's
containers and carries them for an account that was rate-limited, marked
`carried_over` and counted apart: the rule `knack_products` and
`domain_purchase` already work to, that a failed pull never empties a good
snapshot. That makes a third answer necessary — **`partial`**, beside `ok`,
`refused`, `failed` and `disabled` — because "everything is here, some of it
second-hand" is neither a clean sweep nor a hole, and only the accounts with
*no* earlier reading actually cost the index a container. The carry-forward
map strips `client`, `match` and `match_detail` on the way out: those are
derived against the client list as it stands now, and one carried forward is a
six-hour-old guess promoted to a fact.

**A sweep this expensive must not run because a process restarted.** Every
scheduler job starts due (`due = {name: 0.0 ...}`), so a deploy re-ran the
Google sweep however recently it had finished — the live service swept at
19:19, deployed at 19:29, and swept the identical 180 accounts again at 19:33,
into the same per-user limit the last sweep had just finished annoying, for no
information the index did not already hold. `google_index.due_for_refresh()`
decides now, at half the job's interval: a genuine three-hourly tick always
clears it, a restart minutes after a good sweep never does, and the skip is
reported *with the age* rather than passed off as a run. Same shape as
`domain_purchase`, for the same reason. `/api/google/rebuild` still forces —
the guard is the scheduler's, not the button's.

**A count derived from the answer shrinks to fit the answer.** The index's
`accounts` list was built from `{i["google_login"] for i in raw}`, so a login
that came back with *nothing* — a dead refresh token, every platform refused —
was simply not in the set. The activity log read `accounts: 1` on a sweep
whose own `errors` array named a second login that had dropped out entirely,
and the handler that recorded that error did not log it either. `accounts` is
the connected list now, with `accounts_answered` and `accounts_silent` beside
it, because **"connected" and "came back with something" are different numbers
and only the gap between them is actionable**. `accounts_error` carries a
rotated `TOKEN_ENCRYPTION_KEY` even when the sweep found plenty — it used to
be reported only when the sweep came back completely empty, so one unreadable
row behind two working logins was invisible.

**A refusal is a call.** `google_estimate()` counted failures once, for the
whole of Google, which cannot say that Tag Manager is refusing a quarter of
its requests while Analytics is fine — and that rate is the entire early
warning, since a 429 spends the daily quota and returns nothing for it. It is
per API now, with the percentage, and zero refusals prints as a dash rather
than as a worrying 0%.

**The orphan list is paged on the server, 25 at a time.** The suggestions are
the expensive half — a Knack read, the alias index and a word index per
resource — so a long book paid for all of them before drawing a row. Searching
and filtering moved with it: a filter over whichever rows had been sent is a
filter that quietly answers about part of the list. Every count on screen is of
the whole filtered list, never of the page, because a page reporting its own
length as the total is how somebody concludes there are 25 orphans. **Rows are
appended, never re-rendered**: each row holds a client-search box, and a
container that redraws itself while somebody is typing into it eats what they
typed — the same trap the Smart 1 Ads target-area rows had.

**Bulk actions are two different statements, and both say which.** *Attach to
their suggested owner* accepts what the Hub already worked out, per row, and
names how many of the selection have no suggestion rather than skipping them
silently. *Attach to one customer* overrides it with one name — and it is a
searchable list of real clients, never a typed name. It used to be a `prompt()`
box, which is the `client_key` trap in its purest form: a name typed into it
that matches nothing files the resource under a client nothing joins to and
reads as a clean success. Select-all selects the rows that are **loaded** and
says so, because ticking 25 and calling it "all 400" is the confident wrong
answer this codebase keeps having to undo.

Attaching writes three systems and reports each: the **client record**
attachment (the index's own strongest rule, so the next sweep re-applies it),
the **stored index row** (so the resource leaves the orphan list now rather than
at the next sweep — a button that appears to do nothing gets clicked again), and
the **Knack website record**, which is what `hub/analytics_ids.py` compares
against. Search Console and Business Profile have no field on object_153 and say
so instead of being written somewhere they do not belong.

**A recorded id that disagrees is never overwritten without being asked.** That
disagreement is `analytics_ids`' whole point — either the site is running a
property we do not administer or the record is stale — and flattening it
destroys the only evidence of it.

**And that disagreement was mostly not one.** GA has two identifiers for one
property: the **measurement id** `G-XXXXXXX`, which is on the site, in the GTM
tag and on every report and is therefore what a person types into Knack — and
the **property id**, a bare number, which is all a GA4 property summary
returns, because it carries no measurement id at all. `_state()` normalised
both, found them different, and answered **mismatch**. On this deployment's
own 610-row registry every one of the 166 recorded GA ids is a `G-` (159) or a
legacy `UA-` (7) and **not one is a property id**, so for GA the verdict could
only ever be `mismatch` or `recorded_only`: **`match` was unreachable.** Client
360 drew a red pill and the advice *"reports built on the wrong property are
silently wrong"* about properties we administer, correctly recorded, and
`audit_all()` collected every one into a report whose premise is that each
entry means somebody's reporting may be pointed at the wrong place — while
`in_agreement` counted only `match` and so could never count a GA row at all.
Overstating the problems and understating the agreement, at once.

`not_comparable` is the answer, because that is what is true: nothing here can
tell whether the two names refer to the same property, and judging it either
way invents one. It is drawn neutral rather than red or amber — there is
nothing to act on — and it is **not** counted in `needs_attention`. The
module's own note under `_norm_ga` had warned about exactly this in the
abstract: a false mismatch *"is worse than no check at all because it trains
people to ignore the warning."*

**GTM had the same hole, quieter, and the rule is per platform rather than
special-cased.** `google_finder` stores `public_id or container_id`, so where
the API returns no publicId the value lands in the numeric space and produces
the identical false mismatch — rarer only because publicId is usually present,
which is a reason to expect it rather than to leave it. What must **keep**
saying mismatch is asserted just as hard, because a fix that silences the real
findings with the false one is worse than the bug: two different measurement
ids, two different property ids, two different containers, and a legacy `UA-`
id against a live GA4 property, since Universal Analytics stopped processing
in 2023 and that record is genuinely stale. `bucket_for()` is the one reading
of which audit column a state lands in, so the client record and the book-wide
report cannot come to disagree about whether a state is a finding.

Client 360's own "attach a property" button goes through the same path, so
attaching there records in Knack and clears the orphan too. So does the
customer picker on the **Google Accounts & Mapping** QA report — the report is
where somebody notices that a property maps to nobody, so it is where they can
say whose it is, rather than being sent to another screen to find the row
again. It sits immediately after the *Mapped to* cell it changes, and not at
the end of the row: on the end it was the seventh column of a table wider than
its own scroll box, so on an ordinary laptop the header read "MAP TO CLIE" and
the button read "Map to c", past the right edge with no scrollbar showing
until you tried. A control you cannot see is a control that does not exist. It is a searchable list of real customers and never a text box: a
typo'd name files the attachment under a client nothing joins to and reads as
success. The suggestions open it and a person still chooses.

**The domain rule runs on every load of that report, not only at the sweep.**
`google_index.apply_domain_matches()`. The join itself is the index's own rule
2 — what changed is when it runs. The stored index only ever saw the client
list as it stood at sweep time, and that list moves constantly: a URL
discovered by `hub/client_urls.py`, a site matched in Sites Admin, a Knack
record that finally gained a website. Every one of those makes a resource
joinable that the last sweep left orphaned, and waiting six hours for it reads
on the page as a property nobody can explain sitting next to the client whose
domain it plainly carries. It never touches a resource that already has a
client — a domain that disagrees with an attachment is a finding, and a page
load is the worst possible place to resolve one — it leaves a domain two
clients share to a human, and it writes nothing when nothing changed, because
this runs in two gunicorn workers on every open of the page. Only the index is
written: this is a derivation re-made on every build, and writing it onto the
client record would turn it into a stored fact that outlives the domain it
came from.

### A service that cannot be granted must not be offered

Google Access asked a client for five things and could deliver four. Google Ads
is the odd one: there is no "add this email" call, so we send a manager-account
link invitation *from* our own MCC and the client accepts it in their own Ads
UI. That needs an approved `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_MANAGER_ID`
and a long-lived `GOOGLE_ADS_REFRESH_TOKEN` that is **ours** — the only stored
credential a module built around never holding one ever had. None of the three
is set on this deployment.

Left on the list it failed in the worst place available to it: the client ticked
Google Ads on a page that promised it, signed in with Google, and the grant
failed at our end for a reason that was nothing to do with them. A tickbox that
consents and then fails is worse than an absent feature, so Ads is **out** —
service, scope, grant branch, API helpers, status route and every line of
client-facing copy — with the PARKED note at the top of
`modules/google_access/config.py` saying what would bring it back.

**A parked service is not a deleted one.** Requests created before the pause
still carry `"ads"` in their stored service list, and those rows must survive
every page they appear on: `config.label_for()` names the key *Google Ads
(paused)* rather than printing a raw string or dropping the row, the detail
table walks the request's own list rather than only `SERVICE_ORDER`, `mark`
accepts a retired key so a human can still close it, and `start()` reads the
registry with `.get`. A row nobody can mark reads "waiting" for ever.

**Which OAuth client is in use is not the same question as whether one is set.**
`GOOGLE_ACCESS_CLIENT_ID` falls back to the Hub's shared `GOOGLE_CLIENT_ID` —
the client Google Finder and Hub sign-in already share, and the only one
actually set here — and the admin page **names which**, because that decides
whose Authorised redirect URIs need `<PUBLIC_BASE_URL>/connect/callback` on
them. A green "configured" over the wrong client is a `redirect_uri_mismatch`
in front of a paying customer.

**Existing client and new business are different questions, and the form asks.**
Existing is matched against `clients_registry.find_client` exactly — no
substring, for the reason `hub/client_key.py` gives at length — and a name
matching nothing is refused with New named as the way out, rather than filed
against a client nobody can find. New has no record to join to, so the business
is written through `hub/leads.py` on the way past; without that the only trace
of a prospect we just asked for Google access is a row in one module. Filing a
business as New when the registry already knows the name is refused, not
deduplicated: a duplicate contact in the Suite is the one thing the Leads panel
cannot undo. Delivery to the Suite runs only when an email was given — a
contact nobody can call lands there looking handled.

The Hub client ID field is gone with it. It was optional, typed by hand and
blank on nearly every row, which is exactly why the Client 360 access card
answered "no access on file" for clients whose Analytics we had been granted
months earlier. `AccessRequest.client_key()` derives the join instead.
`test_google_access.py` asserts all of it.

### Domains we bought renew whether or not anyone bills them

`hub/domain_purchase.py` and `/tools/domains`. The record was always in Knack
and nothing read it, so the only way to know what renews next month was to open
object_153 and sort it by eye. Only records where `field_2964` says yes appear —
`is_ours()` reads a Knack boolean *and* a yes/no dropdown, because the field can
be published either way. `field_3298` sorts it, the current month and the next
three are laid out from the clock (a hard-coded window is right the month it is
written), and a row with no renewal billing date goes in its own group saying so
rather than sorting to the top as if it were overdue.

**A page does not pull an object in full to render it.** Every open of
`/tools/domains` pulled object_153 over the wire, paged, to answer a question
whose answer changes when somebody buys a domain — a few times a month. The
registry is **snapshotted** now: the scheduler re-pulls it once a night
(`purchased_domains` ticks hourly and `due_for_refresh()` decides, so a leader
that restarted through the window picks the pull up rather than skipping a day
in silence) and the page renders a dictionary scan. **Refresh** is the one
control on it that reaches a provider, and it is a POST, because a GET that
rewrites a cache is one a reload or a prefetch fires without anybody asking.

Four rules hold it up, each a way a cache lies. **The age travels with the
rows and is printed** — a cached figure with no date on it is read as today's,
and `cache_state()` also says when the pull has not run for longer than a
night, which is the only sign the scheduler has stopped. **A failed pull never
empties a good snapshot**: the `knack_products` rule, because a transient Knack
failure would otherwise turn a year of renewals into "we have bought no
domains"; the failed attempt is recorded *beside* the rows it could not
replace and named on the page. **Only the Knack half is cached this way** —
the billed ticks, the month window and the search run per request, so a tick
reads back at once and the calendar rolls into a new month on the day rather
than at the next pull. And **a write to object_153 drops it**:
`knack_websites.forget()` calls `domain_purchase.invalidate()`, or ticking
"did we buy the domain?" on Client 360 leaves this calendar showing
yesterday's answer until tomorrow, which reads as a save that did not happen.
The one page-load pull that remains — no snapshot at all, on a fresh disk with
no mirror — is behind a cooldown, or a Knack that is up and slow costs every
visitor the full timeout in turn, which is the per-visit pull back in its
worst form.

**The second source is cached the same way, and one button pulls both.**
Billed comes from QuickBooks (below), which is a year of invoices — a larger
read than the registry. Refresh pulls both, because a refresh that pulled one
of them would put a fresh timestamp over a stale answer to the question the
page is actually asked, and the age line names each. **`SNAPSHOT_VERSION`
earned its keep here**: the snapshot gained the media partner on every row and
a slim index of the records we did *not* buy, and served at the old number it
would have answered "no partner" on every row and "no record here" for every
domain somebody else owns — with the age reading perfectly current.

There is no billed field in Knack. **There is one in QuickBooks**, though not
under that name: every renewal we invoice is a line carrying the product
`Website Hosting:Website Domain Renewal`, and `hub/domain_renewals.py` reads
those and matches each one back to a website record — so "billed" is an
observation with an invoice number, a date and an amount behind it, and the row
says which invoice said so. The Hub's own tick survives beside it for a renewal
paid another way, kept **against the renewal billing date it was ticked for**,
not against the record: a domain renews every year, and a tick that stayed green
when next year's date arrived would be a confident wrong answer of exactly the
kind this codebase keeps having to undo. A QuickBooks charge is held to the same
rule — it bills the renewal it is *near* (`WINDOW_DAYS`), so last year's invoice
can never mark this year's. `billed_source` is always printed: **quickbooks**,
**hub**, or neither.

Everything in that matcher exists because **the client is not the customer**. A
domain renewal is invoiced to the media partner — one invoice to a radio group
carries five renewals for five businesses — so the only place the client appears
is the free-text line description, typed by a person in whatever shape that day
suggested: `syrons-market.com<TAB>Syrons`, `Foreman Mechanical Services, LLC -
foremanmechanical.com`, `http://friendsofbridges.org/ - Annual renewal`. The
rules follow from that. **The domain in the description is the join key, never
the name** — it is the one thing in that string that identifies a business
exactly, and on this deployment's own invoices all 23 renewal lines carry one.
**A name matches exactly or not at all**, and a near name is a *suggestion* that
does not tick anything: a charge attributed to the wrong client's domain marks a
renewal billed that was not *and* hides a real one from the reconciliation, so
`confidence` says "probable", the row is offered for confirmation, and until
somebody confirms it the charge counts as having no record here — in both
directions, because a probable match that quietly satisfied one side would
vanish from the report entirely. **"Annual renewal" is not a name**, the
`hub/site_names.py` rule about "Main Site" again; a label-only remainder is
dropped rather than matched loosely. And the item is matched on the **leaf** of
its name, so a report asking for "Website Domain Renewal" is not defeated by a
parent nobody knew about (`QB_DOMAIN_RENEWAL_ITEM` / `..._ITEM_ID` override it).

**Billed is this month's question; do-not-renew is next month's.** Asking
whether a renewal three months out was billed is asking about something that has
not happened. So the current month carries the billed tick and every later month
carries **do not renew** — and that flag is deliberately *not* retired when the
date rolls the way the billed tick is. Somebody said this domain should not
renew; a renewal billing date that has since moved on means it renewed anyway,
which is a charge to chase rather than a mark to quietly clear. The
do-not-renew report keeps those two apart for that reason: **Still to cancel**
is the queue, **Renewed anyway** is the exception report, and a mark whose
record has left the registry is *named* rather than dropped.

**And the year-end question has two directions.** `year_to_date()` asks both:
renewals that came due this year with **no invoice** behind them (money we paid
a registrar and did not bill), and Website Domain Renewal charges that match
**no record here** (money we billed for a domain this Hub has never heard of, or
one whose description nothing can join up — each row carrying what was read out
of it, and a searchable list of real purchased domains to attach it to, never a
text box). Neither is presented as a total when either side failed to read: a
Knack that would not answer makes every charge look unrecorded and a QuickBooks
that would not answer makes every renewal look unbilled, so both errors travel
with the numbers and `measured` is false. A domain marked do-not-renew is listed
apart, because not billing one of those is correct rather than a finding.

**A domain attached to a client is answered on Client 360.** The renewal
standing rides on `/api/client/website-record` rather than being a second fetch,
and the domain record panel prints the billing date, the fee, the partner and
whether this year's renewal was invoiced, with the invoice linked — sending
somebody to `/tools/domains` to find the same row again is how a list stays
unactioned. Three answers are kept apart there: a domain we did not buy has no
renewal for us to bill (which is not "not billed"), a record with no renewal
billing date is *not measured*, and a QuickBooks that could not be read says so
instead of reading as a clean nothing.

**A page does not pull an object in full to render it.** Every open of
`/tools/domains` pulled object_153 over the wire, paged, to answer a question
whose answer changes when somebody buys a domain — a few times a month. The
registry is **snapshotted** now: the scheduler re-pulls it once a night
(`purchased_domains` ticks hourly and `due_for_refresh()` decides, so a leader
that restarted through the window picks the pull up rather than skipping a day
in silence) and the page renders a dictionary scan. **Refresh** is the one
control on it that reaches Knack, and it is a POST, because a GET that rewrites
a cache is one a reload or a prefetch fires without anybody asking.

Four rules hold it up, each a way a cache lies. **The age travels with the
rows and is printed** — a cached figure with no date on it is read as today's,
and `cache_state()` also says when the pull has not run for longer than a
night, which is the only sign the scheduler has stopped. **A failed pull never
empties a good snapshot**: the `knack_products` rule, because a transient Knack
failure would otherwise turn a year of renewals into "we have bought no
domains"; the failed attempt is recorded *beside* the rows it could not
replace and named on the page. **Only the Knack half is cached** — the billed
ticks, the month window and the search run per request, so a tick reads back
at once and the calendar rolls into a new month on the day rather than at the
next pull. And **a write to object_153 drops it**: `knack_websites.forget()`
calls `domain_purchase.invalidate()`, or ticking "did we buy the domain?" on
Client 360 leaves this calendar showing yesterday's answer until tomorrow,
which reads as a save that did not happen. The one page-load pull that
remains — no snapshot at all, on a fresh disk with no mirror — is behind a
cooldown, or a Knack that is up and slow costs every visitor the full timeout
in turn, which is the per-visit pull back in its worst form.

### Which websites are billed for, and which are not

`hub/sites_billing.py` and **QA → Billing & Accounting → Sites Billing
Report**. Three QuickBooks products pay for a site we host — **Monthly Web
Hosting**, **Monthly Website Hosting & Maintenance** and **Website
Maintenance** — and nothing joined them to the sites they pay for, so neither
half of the obvious question had an answer: which live sites nobody is
invoicing, and which expired or cancelled ones are still being charged every
month. QuickBooks knows the charge and not the site, Sites Admin knows the site
and not the charge, and the only string connecting them is the **description a
person typed on the invoice line**.

`quickbooks.invoice_lines_since()` is what made it possible at all:
`invoices_since()` answers "how much did this customer pay", which is the wrong
question for anything keyed on what was sold — the product name and the
description live on the **line**. Each line also carries `invoice_text`, every
other description on the same invoice plus the customer memo, because
QuickBooks users routinely put the domain on a description-only line under the
item; it is kept in its own field so a match found there can be *labelled* as
found on the invoice rather than on the line.

Five rules match a charge to a site, strongest first, and each is a way to be
confidently wrong:

- **A domain is a join; a name is a comparison.** A domain in the description
  identifies one project. A business name is matched exactly on the normalised
  form through `hub/client_key.py` — never a substring, so "Riverside HVAC"
  cannot collect "Riverside HVAC Supply".
- **The client registry is the rule that finds what the other four cannot.**
  A project titled "Legacy Build 2019" whose client field was never filled in
  still carries the domain, and `client_key.resolve()` — `allow_fuzzy` off — is
  what turns a QuickBooks customer name into that domain. A registry that could
  not be read costs that one rule and is **named on the page**: "the customer
  name matched nothing" and "we could not check the registry" are different
  claims about the same empty cell.
- **A resemblance is printed and still counted as unmatched.** `site_names`'
  near pass runs and what it finds is shown as *possible* beside a row that
  stays in the unmatched list. A fuzzy hit folded into the totals is a fact
  nobody re-examines.
- **A project title is indexed alongside the client it is filed under**, not
  instead of it. Folding the two into one field made the ambiguity check
  unreachable — and the ambiguity is real: two projects titled the same thing
  filed under two different companies match neither, and both are named.
- **An email address is not a website, and neither is a file name.**
  `billing@acme.com` contains "acme.com", and `acme.com/index.html` yields a
  second "domain" called `index.html` because every domain test in this
  codebase accepts a four-letter last label. Emails are stripped before the
  scan, the path is consumed by the regex, file extensions are refused by name,
  and `client_urls.looks_like_a_website` rejects the Cloudinary and social URLs
  as it does everywhere else.

**A product name that matches no QuickBooks item is not a product with no
charges**, and this is the silent zero the whole report could have become:
rename "Website Maintenance" in QuickBooks and every site on the book reads as
unbilled, in a clean complete table, with nothing saying why. The catalogue is
read first — `quickbooks.items()` — and if none of the three names resolves the
report says **not measured** instead. A product that merely *resembles* one
("Monthly Web Hosting - Annual") is **named and not counted**: matching it is
the substring rule `client_key` refuses, and dropping it silently loses a tier
of revenue from a report that looks complete.

**Lapsed is not unbilled, and stopped is not overbilled.** A live site last
charged eight months ago and one never charged at all are separate rows saying
which. An inactive site is a finding only while the billing is **current** — a
cancelled project whose charges also stopped is the system working, and
flagging it buries the ones still being paid for. A year of invoices is read
because an annual plan invoiced last November is billing; three months counts
as current, because these are invoiced monthly and quarterly depending on the
client and a monthly invoice not yet raised this month is not a lapse.

**One charge that names only a customer says nothing about which of their sites
it covers.** A client with three live sites and one hosting line is its own
finding — *fewer hosting charges than live sites* — rather than three sites
reported as billed or three reported as unbilled. A charge that names a
**domain** covers that one site and leaves the client's others where they were.
Only invoices are read: sales receipts and recurring templates are not, and the
note says so rather than letting a site billed either way read as unbilled.
`test_sites_billing.py` asserts all of it.
