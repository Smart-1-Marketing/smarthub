## The audit was already paid for, and four screens read it differently

`hub/website_audit.py`, `/tools/website-audit`, the **Full website audit**
placement in `modules/scans`, and the panel on the Proposal Builder's customer
step. One Insites audit carries 440 fields about a business, and it was being
read by a client record that showed it as a reference card and by nothing else.
A rep writing a proposal was retyping what the audit had measured, or — far more
often — writing the proposal without it.

**What they are already spending is the first block, and it is the whole point.**
A business putting $2,400 a month into Google Ads is a different sale from one
putting in nothing, and the second question a rep asks is what that money is
buying. `hub/scan_facts.py` carries the same five fields as one collapsed
reference group among ten, which is right for a card on a client record and
wrong for the document the conversation is built on. So `spend()` is its own
block and `audit()` **drops the scan_facts group by title** rather than printing
it twice — two panels answering one question differently on one page is how a
reader learns to believe neither, the trap `jsonstore.unmirrored_json_writers()`
exists to close. `check_spec()` fails if that title ever stops matching, because
the failure mode is silent duplication rather than an error.

**A total is only a total when every part of it was measured.** Meta publishes
the ads and never the spend; Google's transparency centre publishes the fact of
display and no figure either. A monthly total that counted those as zero would
report a business spending $6,000 as spending $2,400, in a clean confident row,
on the page somebody quotes from. `total` covers only what carries a number,
everything left out is **named** in `total_excludes`, and the note on screen says
so in words.

**Arithmetic shows its working, and none of it is borrowed.** Annualising a
third-party estimate is our `× 12`; a cost per visit is *their* two numbers
divided, so it carries both margins of error and the row says which two. What
their organic traffic would cost is computed **only** from a cost per visit their
own campaign produced — with no campaign there is no CPC, and the row says *not
measured* rather than applying a sector average, because a benchmark multiplied
by a real visit count produces a five-figure "value" that reads as a measurement
of their business.

**What they told us and what was observed never merge.** The intake is the
business's own answer and the audit is a crawler's. Where both exist and
disagree, the disagreement is the finding — the point `hub/analytics_ids.py`
makes about a recorded GA id against an observed one — so `stated` sits beside
`observed` and neither is folded in. A band that broadly agrees raises nothing;
a gap raises one sentence naming it. *Rather not say* is an answer and is kept
as one.

**Sixty days.** `STALE_DAYS`. A reading older than that describes a site that may
have been rebuilt since, and it carries no sign of its own age once it is quoted
into a document. Every screen gets `staleness()` rather than computing an age
itself, the Proposal Builder offers the rescan unprompted, and a date that cannot
be read is **not measured** and never zero — zero reads as "scanned today" on the
one screen that decides whether to spend a credit.

**A finding carries its evidence; a product name does not.** `OPPORTUNITIES` is
what was measured and what it costs them ("no retargeting pixel of any kind is on
the site — every visitor who leaves is gone for good"), with the product as the
consequence. The first half survives being read out to the client; the second is
what a rep gets argued with over. Every rule tests `is True` / `is False`, so a
plan that does not check for pixels cannot produce "no pixel found" — the
absent-is-not-zero rule, wearing a sales finding.

**The discovery questions were already answered on their own website.**
`discovery_answers()` maps the audit onto `hub/current_marketing.QUESTIONS` —
paid search from the spend, paid social from the ad library, retargeting from the
pixels, reputation from the review count. They arrive as **proposals with the
evidence beside them** and one press takes them, because "are they doing SEO" is
a judgement from a measurement and a judgement written into a client document on
our say-so is the paragraph a client checks hardest. `applyAudit()` fills **only
the blanks**: a rep who answered a question is the better source, the overlay rule
`hub/client_urls.py` works to. A question the audit cannot speak to is **left
out** rather than answered `unknown` — thirteen rows of "we don't know" is a
screen nobody reads to the bottom of.

**Every intake question changes something, and the check is what keeps that
true.** `hub/current_marketing.py` shipped four discovery questions read by
nothing, so a rep could answer all four and the document came out identical.
Every entry in `INTAKE` carries `feeds`, and `check_spec()` names one that
changes nothing. `ask` is `both` or `staff`: a prospect on somebody else's
website answers six questions or leaves, and the rest are things a rep fills in
after the call. Every yes/no is tri-state — **"not asked" is not "no"**.

**Every audit is a lead.** Somebody typed a business and a website into this Hub,
which is a prospect whatever else it is. It goes through `hub/leads.py`, the one
store, delivery and panel; there is no second lead book here, for the reason
`modules/scans/leads.py` gives at length. A lead with neither an email nor a
phone number is **refused by name** rather than created — a contactless lead
reads as a live prospect on every count that follows, the rule
`modules/ads_builder` arrived at independently. `lead_fields()` is flat strings
only, because `hub/leads.py` cleans and truncates every value and a nested one
arrives in the Suite as the repr of a dict.

### A record nobody is told to open is a record nobody opens

`hub/prospect_queue.py` and **QA → Sales → Prospects To Chase**. The prospect
record was worth opening and nothing said *which* one: the Leads panel is
sorted by date and its five figures are all about **delivery** — confirmed in
Suite, not yet in Suite, needs attention — with none about whether anybody is
working the lead, and every other report on the QA page is about a client. That
is `hub/stale_creative.py`'s lesson one step later: a list that can only be
read is a list nobody works.

**The bands are the work, in the order it has to happen — not a score.** A
ranking number nobody can reproduce is a ranking nobody trusts, so each row
sits in a named band and the order is fixed: *not in Smart 1 Suite* (invisible
to every follow-up that lives there, so nothing else about them matters yet),
*two rows one business* (working one of a pair wastes the call and files the
answer against the row nobody opens), *audited and nothing quoted* (the band
the whole audit pipeline exists to fill), *never audited* (a credit each, and
not a verdict on them), then *quoted and waiting*, oldest first.

**Smart 1 Suite is deliberately not read here.** The stage would cost one HTTP
call per prospect, and a report that makes several hundred outbound calls on
its first open of the day is one somebody turns off — the note
`services/provider_check.py` makes about eight calls on a page load, several
hundred times over. The queue ranks on what the Hub already holds for nothing;
`hub/prospect.py` reads the stage for one prospect at a time, and the note says
so rather than leaving a rep to wonder where the pipeline is.

**A converted prospect is a client and leaves the queue — counted, not
dropped.** A queue that silently gets shorter cannot be told from one that
failed to read, which is why the note carries the number that went to Client
360.

**A source that fails is named and never empties the queue.** A proposal store
that will not answer would otherwise move every quoted prospect into "nothing
quoted" in silence, sending a rep to re-quote somebody who already has one;
the error rides in the note and the rows stay. And `leads.listing()` failing
outright is `measured: False`, which is what stops `hub/report_cache.py`
freezing an empty pipeline into the day's answer.

**Nothing here re-derives a lead's domain.** Six landing pages and two widgets
each name the website differently, so the queue calls `prospect._lead_domain`
rather than carrying a second resolution that would disagree with the record
page about which site a prospect has. The audits come back through
`upsell.audits_for()` — one query per chunk, not one per prospect.

**And a queue nobody is told has anything in it is the same failure one step
later.** There is no mailer in this Hub, so the number goes where people
already look: a **Prospects to chase** card on the dashboard, above Proposals
because a quote is the middle of the funnel and a prospect nobody has called
is the top of it. `scoreboard()` reads the **cached** report rather than
rebuilding — the dashboard loads on every visit and this walk reads the lead
store, a batch of audits, the proposal store and the merge candidates, which
is the note `hub/social_status.py` makes about a number that costs a page load
being a number somebody turns off. Reading the same run is also what stops the
tile and the report answering "how many are waiting" differently, which is the
`/api/db/structure` versus `/api/integrity` trap. The bands stay apart on the
tile too, each figure opens the queue, the age of the reading is printed
beside them, and a report that could not be built is **not measured** rather
than a confident nought.

### What we could sell each client, out of audits already paid for

`hub/upsell.py` and **QA → Clients → What We Could Sell Each Client**.
`hub/website_audit.py` turns one audit into findings that carry their own
evidence, and it fired for a prospect and for one client at a time on the audit
tool and for nobody else. Several hundred clients had been audited and nothing
read the answer across the book, so the upsell conversation that data exists to
start was had from memory or not at all.

**Coverage is the honest half of the report.** A client nobody has audited is
*not measured*, never a clean bill; one whose reading is over `STALE_DAYS` old
is named as stale rather than counted as current; one with no website on file
is its own band. Run against this deployment's own export that is **156 active
clients, 88 of them never audited and 66 with no website on file** — without
the coverage bands the report would have shown one client to sell to and read
as a healthy book. A sales report that gets quieter the worse our coverage
gets is failing in the one direction that matters.

**Recorded and observed are different claims, and the disagreement is the
finding.** The two reports beside it — *Clients Without Analytics* and *Clients
Without GTM* — read `_google_coverage()`, which is what we have **attached**: a
property on the website record, an account somebody linked. This reads what is
**on the page**. A client can have a property attached and no tag on the site,
or a tag we have never attached — and that second one is somebody else
administering their analytics, which is worth knowing before the renewal. Both
directions are reported, they are never folded together, and the comparison is
tri-state: a check the plan did not run raises nothing at all, because "we did
not look" printed as a disagreement is the confident wrong answer the whole
report avoids. `_disagreements()` reads `has_ga` and `has_gtm` — the coverage
helper's own spellings — and `test_upsell_report.py` asserts that against its
source, because guessing the key reads every client as "no disagreement" and
kills the comparison in silence, which is what a first pass here did.

**The finding leads and the product follows.** "Their Google listing is
unclaimed — anybody can edit the hours and the phone number" survives being
read out to the client; "they should buy Local Listings" is what a rep gets
argued with over. The cell carries the finding and the product it points at is
on the tooltip.

**One query per batch, not one per client.** `scan_facts` reads the newest
audit for one domain, and asking it several hundred times is several hundred
round trips and several hundred 440-field blobs held at once. `audits_for()`
takes the newest complete scan for a chunk of domains in one statement, reduces
each payload to the dozen facts the report needs, and lets the blob go.

**Rows carry the rescan**, so the report is a queue rather than a list — the
`hub/stale_creative.py` rule — and it confirms first, because it spends a
credit. The row is *not* removed on success: the audit takes minutes and the
report is held for the day, so a row that vanished would be claiming a result
that does not exist yet.

**And a run that could not look is never the day's answer.** `measured` is
False when the scans table or the client list will not answer, which is what
stops `hub/report_cache.py` freezing "we could not read the audits" into the
shape of "there is nothing to sell" until tomorrow.

### A scanned business is a lead, and a lead needs somewhere to be worked

`hub/prospect.py`, `hub/prospect_routes.py` and `/prospect/<lead id>`. The
audit filed a lead and stopped there: a row in a flat table with a name, an
email and a delivery pill. Everything that made the prospect worth calling —
what they are already spending, what the audit found, the proposal somebody
drafted, the mock-up they were sent — was in four tools and one CRM with
nothing joining them up, so the row was a record of a prospect rather than a
place to work one.

**The lead id is the record.** Not the domain and not the company name: a
prospect is often a business with no website on file and a name typed by
whoever took the call, and both of those change. `hub/leads.py` already
allocates an id, already survives a merge and is already what the Suite
contact is filed against. `leads.get()` **follows a merge** rather than
dead-ending, because that id is in browser history and a link from before a
merge must resolve to the survivor — with a ceiling on the walk, so a cycle
written by a bug shows a record rather than hanging the request.

**Smart 1 Suite owns the working state; the Hub owns the evidence.** That
line is the whole design. The stage, the owner, the notes and the
conversation are in the CRM, which is where the calls and the texts already
are — a stage stored here as well is two systems answering "where has this
got to" differently with nothing on either screen saying which to believe,
which is the failure `jsonstore.unmirrored_json_writers()` exists to close
wearing a sales pipeline. So `suite_state()` **reads** stage, owner and notes
through `hub/suite_opportunity.py` and **never writes a stage**, and a note
typed on the record is posted to the Suite contact so it lands where the next
person to pick the prospect up will look. A prospect with no Suite contact is
**refused by name** rather than having the note kept locally: that local copy
is exactly the second notebook this rule exists to prevent.

**Four empties on that card, and only one of them means "chase this".** Suite
not configured, the lead never delivered, Suite refused the read, and Suite
read fine with no deal open are four different situations. The first three are
*not measured* and each says which it is; only the fourth is an empty that
means somebody should open a deal. Collapsing them into "no stage" sends
somebody to the wrong screen or, worse, makes them stop chasing.

**A section that fails costs only itself.** The audit is worth reading when
Suite is down and the notes are worth reading when Insites is. `_section()` is
the one shape every card answers in — rows, `measured`, `error`, `note` — so
no card can invent its own kind of nothing, and `_caught()` turns any source's
failure into a named non-fatal section rather than a 500 on the whole record.

**A timeline that quietly loses a week is worse than no timeline.** It is
assembled from the sections that were actually measured, and the ones that
were not are **named on it** (`incomplete`) rather than shortening it in
silence — a history missing exactly the fortnight somebody is asking about,
with nothing saying so, is the confident wrong answer this codebase keeps
undoing.

**A prospect collects things before they are a client** — the mock-up they
were sent, a screenshot of the competitor they complained about, the rate
sheet they emailed over. Those lived in somebody's inbox. Files go through
`hub/storage.py` and are indexed through `hub/jsonstore.py`, in a folder of
their own rather than the client tree: a prospect has no client key yet, and
filing them together is how one company's assets land on another's record.
Deleting reports **the record row and the stored copy apart**, the
`hub/domain_links.py` rule — one tick covering both is how somebody learns not
to trust the tick — and the index row is marked rather than dropped.

**Converting is a link, never a creation.** A client in this Hub is a business
with a product in Knack, which is what billing reads, so `convert()` refuses a
name the registry does not know rather than inventing an account the Hub shows
and no invoice ever mentions. What it adds over `leads.mark_converted` is the
carry-across: the assets are re-filed under the client by being **named**
against it rather than re-uploaded, because the bytes are already in storage
and a second copy is a second thing to keep in step.

**And the Leads panel had to stop hiding what a scan produced.** The Report
column rendered `pdf_url` and nothing else — so a website-audit lead showed a
dash, because that audit is a *page* rather than a PDF and its link was
sitting unread in the row's own `meta`. `reportCell()` offers both, and every
row's name now opens the record.

`test_prospect_record.py` asserts all of it.

**And both screens shipped with no explanation on them, which is how Smart 1
Ads shipped.** `hub/help.py`, `hub/help_routes.py` and `hub/static/hub-help.js`
were all working; the Website Audit tool opted in with two bubbles and the
prospect record with none, and each declared a `data-screen` naming a screen
the registry had never heard of — `website_audit` and `prospect` against keys
filed as `hub.website_audit.*` and `hub.prospect.*`. So the attribute was a
claim nothing backed: `offer()` guards on the tour's length, which is the only
reason a mis-named screen drew nothing rather than drawing four other screens'
steps over elements that are not on the page. Both name the registry's own
screen now and both ask `has_tour()` rather than drawing the attribute on the
truth of a name.

**The record is drawn entirely from a fetch, so its bubbles and its tour
anchors are one argument to `card()`.** `data-help` and `data-card` come off
the same key, decided in the one place that draws a card rather than at the
nine call sites — the reason `hub-thinking.js` upgrades a spinner rather than
fifty call sites being edited, wearing a help layer. A card added next month is
explained by naming itself and cannot end up with a ring pointing at nothing.
`hub-help.js` mounts on a debounced `MutationObserver` for exactly this page's
shape, so the spans it writes are upgraded like any other.

**A tool that files a lead has to say where it went.** Filing ended at the word
*"Filed."* — and the record built two releases later is where a prospect is
actually worked, so a rep went to `/sales/leads`, found the row and clicked the
name, which is the signpost failure `hub/stale_creative.py` names. The response
carries `record_url` now, and it is a *third* fact rather than being folded
into the saved-here/created-in-Suite note the two writes are already reported
apart by. The **empty** branch is deliberately not written server-side —
`capture()` allocates a uuid4 hex, so an id is always there on this path, and a
state nothing can reach reads as one the code handles. The page still guards,
because two gunicorn workers mean a rolling deploy can answer from a version
that has never heard of the key.

**None of it reaches the page a prospect reads.** The customer-facing audit
widget and its report are served to a stranger on somebody else's website, and
a staff note in one is an internal note in front of a client — the rule
`test_ads_explainer.py` already holds the public estimate to.
`test_prospect_explainer.py` asserts every half: every key placed resolves,
every tour step rings something its own screen actually draws, and the two
client-facing templates place none of it.

**And a key concatenated outside an attribute's quotes read as a key nobody
registered.** `hub/help_audit.py` already knew a key can be built at runtime —
the Proposal Builder's reach panel writes `data-help="sales_builder.areas.${key}"`,
where the interpolation is *between* the attribute's own quotes, so the
captured key contains a `+` or a `${` and is named as unresolvable rather than
guessed at. `card()` concatenates the other way round —
`'<span data-help="hub.prospect.'+esc(key)+'"></span>'` — and the pattern stops
at that inner quote, capturing `hub.prospect.`: a **prefix**, with nothing in it
to mark it as built, reported as a dead bubble on a screen that had just been
given nine live ones. The evidence is the character after the quote the match
stopped at, so that is what is read. `test_help_layer.py` asserts the runtime
prefixes as prefixes rather than as one hard-coded count, because a third screen
building a key is a thing that file should keep working.

### The customer-facing half is a second kind of placement, not a second widget

`modules/scans` owns placements, and a second table describing one would be a
second description of what a placement is. `ScanWidget.kind` is `aeo` (the free
five-second AI-visibility pre-check) or `audit` (this one). Four things follow:

- **`create_all()` never adds a column to an existing table**, so
  `_add_missing_columns()` in `modules/scans/app.py` is what puts `kind` and
  `intake_json` on the live Postgres. Asked-then-added rather than fired blindly:
  the columns are on the models too, so on a fresh database `create_all()` has
  already made them and firing unconditionally means two workers printing a
  Postgres ERROR per column on every deploy — which is how a log stops being one
  anybody finds the real error in.
- **`kind_of()` reads NULL as `aeo`.** Every placement written before the column
  existed is an AI-visibility one; reading NULL as the new kind would silently
  change what a live embed on a client's website serves.
- **The kind is fixed at creation**, like the address and for the same reason:
  both are in the three lines of embed code already pasted on somebody else's
  site, and swapping one turns an AI check into a form asking what they sell,
  with the only sign being a save that reported success. Refused server-side as
  well as hidden in the form — a rule the form keeps while the write breaks it is
  not a rule.
- **One step, not two.** The AI widget can afford a teaser because its pre-check
  is free and instant. A full audit has nothing to show a stranger before a
  credit has been spent, so it asks once — contact *and* the handful of answers a
  crawler cannot get at — and files the lead **before** the audit starts, because
  a lead is a lead whether or not Insites ever answers.

The report at `/scans/r/<token>` is the same reading of the same audit a rep
sees, minus the half that is the reason to call: `_audit_view()` **strips** the
discovery mapping and the prefill rather than leaving a template not to print
them, because a subset a renderer merely happens to omit is one the next renderer
prints. There is no PDF — the audit is a page — and `/r/<token>.pdf` says where
the document is rather than handing over the SEO & AEO report under a link that
promised this one.

### Leads merge, and merging is not deleting

The same business reaches the Leads panel more than once and always will: the
widget on a client's site in March, an audit in May, a landing page in between.
`leads.merge_candidates()` proposes and `leads.merge()` acts, and every rule in
it is a way to be confidently wrong:

- **Nothing merges by itself.** An automatic merge on a name files one company's
  enquiry under another, which is the worst outcome available to this panel.
- **Exact or not at all.** Email and canonical domain are joins and group as
  *certain*; an exact company name on its own is *possible* and groups with
  nothing, because two franchises of one brand carry one name and are two
  businesses with two owners. The `hub/client_key.py` rule, wearing a lead.
- **The survivor's own values win.** A rep chose which row to keep. Blanks are
  filled from the others newest-first; a value already there is never written
  over. `created` becomes the **earliest**, because that is when the prospect
  actually came in and it is the field a follow-up queue sorts on.
- **Nothing is deleted.** The absorbed row keeps its place in the file with
  `merged_into` on it and `listing()` filters it out, so a merge somebody regrets
  is still readable. The panel prints how many rows absorbed a duplicate, because
  a count that went down needs a reason on screen beside it.
- **A merge does not undo a delivery.** Two delivered rows mean the Suite holds
  two contacts; every contact id is kept, the panel is told there are two and
  where to merge them, and the survivor is never re-delivered — re-sending a
  delivered row is the duplicate `hub/leads.py` is built to avoid.
- **Two clients is a refusal.** Merging rows converted to different clients
  attributes one company's enquiry to another, and it is not a thing this panel
  decides.

### The Proposal Builder asks before it writes

`?audit=<domain>` opens the builder on the customer step with the website filled
in and the audit read, and the panel lives on that step for the whole session.
Reading is free and scanning is billed, so **Read the audit** costs nothing and
**Run a new audit** confirms first and posts to `/scans/api/scans` — the module
that owns scans — rather than this one learning how to spend a credit. An audit
over sixty days old says so in amber with the rescan one click away, and it
**still prefills**: leaving a rep with nothing while they wait is worse than an
old answer with the date printed on it.

`test_website_audit.py` asserts all of it.
