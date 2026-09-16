## A plan that comes back as JSON is a plan nobody reads

`hub/proposal_plan.py`, the plan card on `/proposal-execution`, and
`test_proposal_plan.py`. Analyzing a proposal produced a task graph the
scheduler drains and a board to watch it on, and three things a person
actually wants on the day the proposal is signed were nowhere on it: **the
creative that has to exist**, **the one-time work before launch**, and **the
monthly promises** -- which are what a client notices when they stop. What the
board did show of a finished task was its result dict printed as JSON in a
`<pre>`: a page of braces to somebody who wanted to know what to do next.

Three rules, each a way the lists become confident and wrong.

**Sizes come from the spec kit, never from here.** A list of banner sizes
typed into the plan module would be the fourth copy of the table
`kit_drift()` holds to the published page, and the one no check reads. Each
recipe names the product string `hub/creative_specs.channels_for_product()`
already maps and, where the kit's whole channel is wider than the buy, the
unit ids to keep -- a YouTube in-market buy is one skippable spot, not the
six formats the kit sells -- and the dimensions, formats and lengths are read
through `hub/creative_needs.required_units()`, the same reader the Proposal
Builder's creative gate uses. `test_proposal_plan.py` asserts the module's
code carries no `WxH` literal at all.

**The model proposes, the rules decide, a person presses.** The rule-derived
items always exist, so the lists are complete with no OpenAI key. With one,
the model is asked for the *specific* promises and every item it returns must
quote the line it came from; a quote the text does not contain is **kept and
marked** -- *check: no matching line in the proposal* -- never silently
trusted and never silently dropped, because a task nobody can find in the
document is exactly the one a person should look at before ticking. Nothing
arrives accepted: every item is a proposal until somebody keeps it or marks
it not needed, an item they add is theirs (`source: manual`) and is the one
kind that can be *removed* rather than merely dropped, and **only what was
kept** reaches a working brief or a handoff packet. An AI item with a rule
item's title is folded into it (the rule item gains the quote) rather than
listed twice. A refused decision -- an unknown id, an unknown list, a blank
title -- is refused by name and leaves the plan exactly as it was.

**What the proposal does not say is asked, not guessed.** A channel with no
dollar amount beside it, a campaign with no start date, creative whose
supplier the text never names -- each is a question carrying *why* it is
being asked, answered beside the plan. What the proposal *does* say is not
asked: a product that is by definition our production (the monthly sales
video, the social posts) is answered from the recipe, and a supplier the
model quoted from a line really in the text is answered from the text, both
marked *answered from the proposal* and still changeable. The supplier is
asked only for channels with **files** to supply -- asking who writes the
paid-search ad copy is the question that teaches people to stop reading the
list.

Two things about where it lives. **`plan_json` is its own column**, added
through `_LATE_COLUMNS` in `add_missing_columns()` because `create_all()`
adds no column to a live table: the analysis is what the parser read and the
plan is what somebody kept, dropped, added and answered, and a re-analysis
must not be able to overwrite the second while refreshing the first. A run
analyzed before the column existed gets a plan built from its stored analysis
on first read -- rules only, no model call on a page load -- and says so. And
**superseding carries the review forward**: a verdict follows an item still
proposed, an item a person added comes across whole, an answer follows a
question still asked.

**The result renderer writes directions.** Summary first, each list under a
heading a person would use (*What to produce*, *Steps*, *Check before
approving*), a link to the tool where one exists, the template fallback said
in words, and the plumbing -- the inputs echoed back, the channel payload,
the upstream states -- behind a *Technical details* fold. It is lifted out of
the template between markers and driven in **node**, the arrangement
`test_menu_layout.py` uses over `hub-crumbs.js`, and the assertion is that no
`{"` reaches the reader.

### The questions were asked, answered, and read by nothing

The launch date, the budget per channel, the reporting cadence and who
supplies each channel's files were stored on the plan, carried forward when
a run was superseded -- and read by no brief, no packet and no task. The
brief's prompt carried the shared inputs and the kept items; the answers sat
in `plan_json` beside them. That is the failure `hub/current_marketing.py`
was written to undo, inside the module written after it: a question somebody
answers and nothing reads is a form field.

`proposal_plan.resolve()` lays the answers over the plan **at read time**. A
launch date becomes a due date on every launch task -- each recipe row
carries the days before launch it has to be done, zero being launch day --
and on every creative item, which is wanted `LEAD_DAYS_CREATIVE` ahead; a
supplier answer marks every creative item of its channel; a cadence lands on
the report tasks, and only those. `answers_for()` hands a brief or a packet
the answers for its channel and the run-wide ones, so `_brief_runner` tells
the model the launch date as fact and `_launch_runner` prints the date, the
supplier, the budget and the cadence as their own lines rather than burying
them in a list. **Derived and stored nowhere** -- `as_dict()` serves the
resolved plan and the column keeps the answers -- because a date written into
the items would outlive the answer that produced it, and two gunicorn
workers would disagree about which copy is current; `test_proposal_plan.py`
asserts the stored plan carries no `due`. A launch date nothing can parse
costs the due dates and says so on the page, never the plan.

### A quote built in the Hub was read back as a PDF

A run started from a document filed on the client record and read its
**text** -- a regex pass over the prose, then a model asked to find the
channels and the dollar amounts. Right for a proposal somebody wrote in Word.
Wrong for a quote built in `modules/sales_builder`, which already holds every
fact the analysis was trying to recover: the line items with their rate-card
product and category, the dollars and the term, the start date, the target
areas, the KPIs, and the creative gate's own answers about who supplies each
medium's files. A delivered quote is filed as a PDF and `Quote.client_filed_as`
points back at it, so execution was reading the rendered document and asking
the rep again for things the quote had been told.

`hub/proposal_quote_facts.py` reads the quote's state into the same analysis
shape the engine and the plan already consume, so nothing downstream knows
which kind of document a run started from. Four rules. **A channel is a
rate-card family, matched on the category first and the product second** --
the card files four products called "Demographic" under four headings, and a
video product sits under the DISPLAY heading, so `channel_for_item()` reads a
line the way `creative_needs.medium_of()` does; the ten keys the text
analyzer knows keep their names so the task graph still builds, and the
card's other families (display, connected TV, digital radio, paid social,
email, signage, web) get keys and recipes of their own. **The creative is the
gate's own reading**: the channel carries its products and `_kit_creative()`
asks `required_units()` about *them*, medium by medium -- a Snapchat buy is
filed under the card's video heading, so asking for one medium found none of
its lines. **What the quote answered arrives as an answer marked as the
quote's** -- the start date, the supplier from the creative step or from a
production line on the plan, a cadence the Reporting section actually states
-- still changeable, and the page says *answered from the quote*. And
**another client's quote is refused as not found**: a quote id in a URL must
not pull one client's proposal onto another's record, and a quote that is not
this client's answers exactly what a quote that does not exist answers.

The picker offers the saved quotes first and leaves out a PDF a quote points
at, so one proposal is not offered twice; picking the PDF by id still resolves
to the quote. The text the plan's model pass grounds against is rendered from
the facts, deterministically, so the run's `source_hash` moves only when the
quote does. `test_proposal_plan.py` sweeps the **real rate card** -- every
product lands on a channel or on a category named as not one -- because a
hand-written list of products proves nothing about the row somebody adds.

### A creative item that names a set and offers nothing to do about it

Every creative item said what had to exist and left a rep to find the tool
that makes it, or the link the client uploads through -- two screens away,
which is the signpost failure `hub/stale_creative.py` names, one screen
earlier. The tools already exist. `proposal_plan.CREATIVE_TOOLS` is the
table -- the Display Ad Builder for banners (with the client filled in, the
same press Stale Creative offers), the Commercial Builder for a spot, the
Radio Ad Creator for audio, Image Creator for everything else -- and
`item_actions()` decides from the item's **kind and its supplier**: Smart 1
produces it → make it in the tool; the client supplies it → the upload link;
nobody has said → both, because the item is still somebody's to act on. Copy
points at the board task that drafts it. The page draws what the server
decided and decides nothing itself.

**The upload link is the gallery's own share link, and the press that makes
one is a press.** `provisioning.link_for(create=False)` only asks, so
`as_dict()` can show the link where one exists; `POST
…/run/<id>/upload-link` is the creation, the rule `modules/image_picker/
provisioning.py` states about a gallery being asked for rather than assumed.
Two galleries that could be this client is the one case nothing is created,
because the wrong one collects their photographs. The link is built from the
host that served the page, because a link handed to a client has to be
absolute and `PUBLIC_BASE_URL` is unset on more deployments than it is set.

### The plan lived on one page

Client 360, `/my-clients` and the QA reports did not know an execution plan
existed, so a client with items nobody had reviewed, questions nobody had
answered and creative nobody had a supplier for was invisible everywhere a
rep actually looks. `proposal_execution.plan_summary_for_client()` is the
**counts beside a link, never the items** -- the plan is worked on its own
page -- and `/api/client/execution-plan` serves it under `/api/client/` for
the reason `/api/client/orders` gives: the Suite frame allowlists that
prefix and nothing else. The card sits in Client 360's *Work & requests*
section, and `test_client360_layout.py` holds it there. Three empties are
kept apart on it: the table would not answer, no plan has been built, and a
plan with nothing left to do here.

`hub/client_health.py` reads the same summaries in **one query for the
book** (`open_plan_summaries()`) and raises two kinds, apart because they
send somebody to different presses: `plan_review` for a plan nobody has
finished, and `plan_creative` for creative nobody has said who supplies.
Both carry the run's own link. A superseded or completed run raises nothing,
which is what `_OPEN_STATES_EXCLUDED` says in one place for both readers.

### A landing page nobody's record mentioned

`landing_maker.for_client()` was written with the docstring *"used by the
Client 360 / proposals card"* and had **no caller**, because there was no
such card. So a client could have three landing pages live, taking leads,
and the one screen a rep opens to ask what we are doing for somebody said
nothing about any of them — the declared-and-never-wired failure this file
counts a dozen of, and `test_unwired.py` could not see this one because the
token `for_client` is also a function name in three other modules.

**It answered a bare list, and that is the half that mattered.**
`listing()` carries `views_measured` and `conversion_measured` precisely
because a visit table that will not answer and a page nobody has opened
both render as a nought, and only one of them is a reason to stop spending
on the campaign — and taking `["pages"]` off the answer dropped exactly
those two on the floor. A card built on the old shape would have drawn
*"0 opens"* over a database that was down. `summary_for_client()` replaces
it: the flags travel, and the rows are trimmed to what a card prints (the
stored pictures and the source website are the tool's own screen's
business, and are kilobytes a row on a record drawing twenty other cards).

**Three reads stand behind one card and each says separately when it could
not answer.** The pages store, the visit table and the lead store — the
rate needs all three, and a page can perfectly well be listed with no rate
behind it. `measured` is False only where the listing itself **raised**: a
store that reads back empty is not claimed as an outage, because
`jsonstore.read_json` answers `default` for a missing file, so *no pages*
and *the file is gone* are genuinely one answer here and inventing a
distinction the data cannot support is its own confident wrong answer.
The flags are ANDed over the group's members that actually **had rows**,
since a member with none never asked the visit table anything and comes
back measured — ANDing those in would read a client with one empty sibling
as fully measured while the real member's counts were missing.

The card is in *Overview*, beside Ad performance, because what it answers
is whether a campaign is working rather than what our website records say.
It draws the four non-rate states as themselves — nobody has opened it,
too early under 25 opens, more leads than opens (the opens are
undercounted, not a page converting above a hundred per cent), or not
measured — and never as `0%`. A page with no `url` says *no public
address* rather than drawing an empty link, because that is a fact about
`PUBLIC_BASE_URL` and not about the page. `renderLandingPages()` is lifted
out of the template and driven in **node**, the arrangement
`test_menu_layout.py` uses over `hub-crumbs.js`, and every one of those
states was confirmed red against a card that drew it as a rate first.

### A monthly promise kept once, and nothing asked about month two

Every-month items were kept on the plan and that was the end of it: a kept
"monthly sales video" was a fact about the plan and never a question about
the calendar, and the things a client notices when they stop are exactly
these. `hub/proposal_promises.py` turns each kept monthly item into a row
per month since launch -- the month after the launch date, the same reading
`resolve()` puts on the items as `starts` -- and says one of five things
about each: **landed**, the activity log holds work of that kind for this
client in that month, named by tool and day; **marked**, a person recorded
it done, with who and when; **due**, this month before the 25th; **missed**,
the month is over or this month is past the 25th with nothing; or **not
measured**. `/qa/monthly-promises` lists this month and last across the
book, Client 360 prints missed and due as pills on the plan card, and
`hub/client_health.py` raises `plan_promise` once per missed promise-month
with the plan's own link -- "the report for August" and "the sales video for
August" are two pieces of work for two people.

**What proves a promise landed is a table, and every module in it is one
the work log can name.** Each monthly recipe row carries a kind now --
`report`, `content`, `video`, `social_plan`, `social_post`, `email`, `web`,
`creative_refresh`, `optimize`, `review` -- and `proposal_plan.PROMISE_KINDS`
maps each to the modules and events that count as evidence.
`test_proposal_promises.py` holds every module there to
`client_brand.WORK_KINDS`, because a row `work_log()` drops on the way to the
record is one this cannot see either: the `display_ads` failure, one reader
over. A kind with **no** evidence modules is recorded by hand only -- nothing
here logs that a report was sent to a client -- and the row says so rather
than reading a missing mark as a missing report. An item the model found or
a person typed has no kind and reads as a deliverable recorded by hand: the
Hub has no way to see it land and no grounds to call it housekeeping.

**Housekeeping is drawn and never raised.** `deliverable` is the line
between what a client notices when it stops and what is our own upkeep.
Reviewing bids, checking frequency, confirming a game schedule and the
generic "review what was promised" get their month strip on the plan and
reach no report, no issue and no count -- a finding that fires on "review
search terms" for every client every month is the crying-wolf failure
`QR_CODE_RULES` paid for, and it takes the missed sales video with it.

**A month the log cannot answer for is not a miss.**
`client_brand.work_index()` reads the log once for the whole book -- one
tail per client is fifty reads of one file -- and reports the oldest entry
it reached. A tracked promise's month before that horizon is *not
measured*, with the horizon named; a log that could not be read at all makes
every tracked month not measured and leaves the hand-recorded ones
answering. Reading either as "nothing landed" would be a report accusing the
whole team on the strength of a rotated file. `work_log()` and `work_index()`
read one `_work_row()`, because two walks over the same entries with their
own idea of which key names the client is how the record and the schedule
come to disagree about whether a blog was written this month.

**Derived on read; the mark is the one thing written.** The schedule is
arithmetic over the launch date, the calendar and the log, served on
`as_dict()` as `plan["schedule"]` and never stored -- `hub/creative_evergreen.py`'s
rule, since a stored copy would outlive the launch date being corrected and
the two workers would disagree. A mark is keyed on the run, the item and the
month (`mark_key()`), written through `jsonstore.update_json` so two workers
cannot drop each other's, and it drops the QA report's cached copy **beside
the write**. The same key is the `subject` of the health issue, so
`_apply_overlay()` reads the promise marks per request and a month marked
done on the plan page leaves `/my-clients` on the next read rather than at
tomorrow's rebuild. The QA report and the health issue look at **this month
and last** (`REPORT_MONTHS`); older misses are counted and named, and the
plan page still shows twelve months, because a plan nobody has marked for a
year would otherwise raise twelve rows per promise on a report whose job is
to say what to act on this week.

### Whose item it is, and the two documents the day the proposal is signed

The plan said what had to happen and when, and nothing said **whose** it
was -- so a kept launch task belonged to the plan rather than to a person,
which on the day is the same as belonging to nobody. And nothing came off
the plan as a thing to hand somebody: the team walked the kickoff call from
the screen, and the client had the rep's email.

**Every item follows the client's owner, and only a hand-named one is
stored.** `proposal_plan.resolve()` takes the client's owner from
`hub/client_owner.owner_of()` -- a direct assignment or a standing partner
rule -- and lays it over every item on read, marked `owner_source: client`;
an owner named on one item through the same plan route (`owners` beside
`accept`, `add`, `remove` and `answers`) is stored as `owner_override` and
reads as `item`. Derived because a handover is the ordinary case: reassign
the client on the Client Owners report and every item that was following
them moves, while the one somebody named by hand stays exactly where it
was. Nothing but the override reaches the column, and `test_proposal_kickoff.py`
asserts the stored plan carries no `owner` at all. **An account the Hub
does not know is refused by name**, against the same `assignable_users()`
the picker is drawn from -- a typed address that matches nobody is a plan
item owned by a string. A roster that could not be read is the one case a
well-formed address is taken as typed: refusing every assignment over a
table that blinked is a check somebody switches off. The picker's blank
option reads *Follows <the client's owner>* rather than *none*, because
those are different states and only one of them is a gap.

**The kickoff document is built from the kept plan, and counts what is
not.** `kickoff_document()` serves `/proposal-execution/run/<id>/kickoff`:
the launch date, each channel with its budget and who supplies its
creative, the creative with its due date, owner and the tool that makes it,
the launch tasks in the order they fall due, the monthly promises with their
kind, the open questions, the client's link and the upload link. What it
leaves off it **counts at the top** -- items nobody has reviewed, items
found by AI with no matching line, creative with no supplier, items with no
owner -- because a document that quietly leaves items off is the list that
gets shorter with nothing saying so, on the one page printed for the room.
It owns its own `<body>` so it prints as a document, and the Hub's injected
chrome is hidden by its print rules rather than by putting a staff page in
`CHROMELESS`.

**The client's page carries the fields and nothing else.** `client_needs()`
serves `/proposal-execution/needs/<token>`: the files the client agreed to
supply with the date each is wanted by, their upload link where one exists
(never created by a page load -- `provisioning.py`'s rule), the questions
that are theirs to answer, and their Smart 1 contact by name. Built
server-side as a subset rather than left to a template to omit, the
`modules/scans` rule, so no dropped item, no item Smart 1 is producing, no
internal note, no flag about how an item was found and no staff email can
reach it. `CLIENT_QUESTION_WORDING` is the whole list of questions a client
may be asked -- the launch date, who produces each channel's creative, the
report cadence -- reworded for the person being asked; a missing budget or
a channel we have no recipe for is our reading problem and stays ours.

**The token is stored on the plan and never derived**, the opposite of
`hub/client_key.py`'s rule and for `hub/llms_hosting.py`'s reason: it is
going into an email on somebody else's side, so it has to outlive a restart
and a rotated `SECRET_KEY`, and revoking it has to make *that* address
answer 404 rather than a second copy of what it used to say. Minted by
`POST .../client-link` -- a press, because a link that exists is one
somebody may have sent, and a second press hands back the live one rather
than a second address. **Revoked, unknown and malformed answer the same
404**, since a client-facing URL that says "this one expired" tells somebody
probing which are real; a store that would not answer is a **503**, because
a client meeting a 404 concludes the link expired and one meeting a 503
tries again. The revoked token stays on the plan as the record that a link
was sent.

**Both halves of public, because this is a blueprint.** The route is named
in `install_guard(bp, public=("/needs/",))` and in `CHROMELESS`, and
`test_blueprint_guards.PUBLIC_DYNAMIC` names it with its reason -- the
Commercial Builder's review link paid for this: exempt from the login and
not from the chrome is a client reading our staff nav, and the other way
round is a sign-in form in front of somebody with no account. The staff
kickoff sits under the guarded mount and is in neither list.

### A due date nothing checks, and creative the plan did not notice arriving

`hub/proposal_progress.py`, the Done press on the plan page, the Status
column on the kickoff document, and `plan_overdue` on My Clients.
`resolve()` had put a due date on every kept launch task and creative item
the moment the launch date was answered, the kickoff document printed them
-- and the plan's decisions were keep, drop, add, answer and owner. Nothing
could say a task was done and nothing computed overdue, so a task due last
Tuesday looked identical to one due next month and My Clients could not
raise it. And a banner set the Display Ad Builder had already delivered for
the client sat on the plan exactly as it did the day the plan was built,
with a *Make it in Display Ad Builder* button beside it. The monthly
promises had all of this answered one list over; this is the same reading
for the two lists that are not monthly.

**Done is a press with a name on it, and the only thing written.** It goes
through the same plan route as every other decision (`done`), is stored on
the item with who and when, follows the item onto a superseding plan like
its verdict, and is refused by name where it would be a tick on nothing:
an item nobody has kept, or a monthly promise, which is marked month by
month on its own strip.

**Landed is derived on every read and stored nowhere.** The evidence is the
work log, through the same `client_rows()` the promise schedule reads, and
it is keyed on **the tool that makes the item** -- `proposal_plan.tool_for()`,
the table the item's own button is drawn from -- rather than on the kind of
file, because the smoke run of the first version closed a social post
graphic with a Display Ad Builder pack: both are images, and they are made
in different tools. It **follows the supplier answer**: the client uploading
through their link proves what the client supplies, our tools prove what we
produce, and an item nobody has answered for is proved by either. And it
**starts when the run started**, at the earliest run in the supersede chain,
because the same client's display pack from two years ago is not this
plan's banners. What that costs is the file that arrived before the plan
was built, which reads as open until somebody presses Done -- the safe
direction, since a false landed hides a gap and a false open costs a press.
What it still cannot tell apart is two kept items of one tool on one plan,
so the row names the delivery and the module does not guess.

**Overdue is a fact about the calendar, so it still counts when the log
could not be read** -- and the item then says whether it landed is *not
known*, rather than the silence reading as nothing having arrived. Copy
lands when its board task does; a launch task lands never, because nothing
here can see one happen, and is done by hand.

**One issue per past-due item on My Clients, and the fingerprint does not
move with the date.** The first draft put the day count in the detail, and
the detail is in `fingerprint()` -- so a mark made there would have read as
superseded every morning. The days are on the title; the subject is
`done_key()`, and a Done pressed on the plan page clears the issue on read
through `proposal_execution.done_index()`, the overlay the promise marks
already ride. The Client 360 card prints the count beside the others, and
the client's own page says *received* about a file that has landed and
nothing about how we know. `test_proposal_progress.py` drives the clock
rather than waiting on it, and holds `TOOL_EVIDENCE` to `CREATIVE_TOOLS` in
both directions so a seventh tool cannot join with no evidence behind it.

### The client's page asked and could not listen, and a won proposal told nobody

`proposal_plan.record_client_answers()`, `POST /proposal-execution/needs/<token>`,
`proposal_execution.start_won()` and the hourly `proposal_autostart` job. Two
gaps at the two ends of a plan's life, and each was a page that looked
finished.

**The client's page listed their questions and ended with "reply to your
Smart 1 contact".** So the launch date came back in an email, somebody
retyped it onto the plan, and the question stayed open on every screen until
they did -- the Proposal Builder's discovery questions, one audience further
out. The page is a **plain HTML form** now, posting back to its own address:
`a:<key>` for a question, `h:<channel>` for a tick handing a file back to
Smart 1, which is the same supply key answered `smart1` rather than a new
question. No fetch and no Hub script, because it is a stranger's page on
somebody else's website and a failed post has to be a page saying why rather
than a button that did nothing.

**What arrives is a proposal, never the plan's own answer.** It lands in
`plan["client_answers"]` with the client's name on it and `plan["answers"]`
does not move until a person presses *Use their answer* -- the rule the
researched competitor list works to, for a harder reason here: the page is
reachable by anybody holding the link, and a value posted at a token must not
move the due date on every task by arriving. Three refusals, each by name.
**Only the client's keys**: `client_answerable()` is the open client
questions plus the supply key of a kept item they had agreed to supply, and a
budget or what the model was unsure of is ours -- a page a stranger can post
to must not be able to set a budget. **Only the offered choices**, so a
cadence of "hourly" is refused rather than stored. And **a name is
required**, because an answer nobody can attribute is one nobody can ring
back about. Revoked, unknown and malformed answer the same 404 on the POST as
on the page; a store that would not answer is a 503; a success redirects so a
refresh cannot post twice.

**A reply read by nothing is the form-field failure**, so a pending answer is
counted everywhere an open question is: `summarize()` carries
`client_answers_pending`, `resolve()` lays each beside its question as
`client_proposed` with `taken` (the plan already carries the same value, by a
press or because the document said it), the kickoff document prints *the
client says* on an open question, the Client 360 card counts them and
`client_health`'s `plan_review` issue names them -- which retires a mark on
that issue, correctly, because a client's reply is new information.
`carry_forward()` carries them onto a superseding run for the questions still
asked. `test_proposal_client_answers.py` asserts all of it, including that
the page loads no script and no staff email reaches it.

**And the Proposal Builder knew the moment a quote was won, and Proposal
Execution was told nothing.** Approved is the client accepting at their link;
Converted is an insertion order written from it. A plan existed only when
somebody opened the tool and pressed Analyze, which on the day the proposal
is signed is the press most likely to be forgotten. `start_won()` reads the
sales book through the module `wsgi.py` loaded -- never a second import, the
`hub/sales_status.py` arrangement -- and starts a run keyed `quote:<id>` for
every won quote that has none, through the same `create_run()` the button
calls, with `reason=` written onto the plan's notes and the first event, and
an activity row on the quote carrying the link so the rep who sold it finds
the plan from the screen they already read.

Four counts that are never a silent skip. **Once per quote**: a run in any
state -- including one started from the PDF the quote was filed as, which
`create_run` resolves to the same key -- reads as `already`. **A client with
a different run open is a named conflict and is never superseded**:
superseding carries approved work and shared inputs forward, and that is a
person's press, not a sweep's. **Won more than `AUTOSTART_MAX_AGE_DAYS` ago
is `too_old`**, since a plan built for a campaign somebody set up a month ago
is a list about work that happened. And the sweep starts at most
`AUTOSTART_LIMIT` a tick and counts the rest as `deferred`, because each run
is a model pass where a key is set and the scheduler's jobs share one thread.
A table that would not answer is `measured: False`, never a clean sweep of
nothing. The job sits ahead of the slow provider sweeps for the reason the
QA-task jobs do, a quiet hour reads as skipped with a standing conflict in
the sentence rather than as an empty result, and `POST
/api/proposal-execution/start-won` runs the same sweep from the plan page.
Nothing an automatic run creates is kept: it arrives as proposals for the
client's owner to review, so its whole footprint is a `plan_review` issue on
their desk. `test_proposal_autostart.py` asserts all of it.
