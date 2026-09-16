## Everyone has their own login, and there are two levels of it

Fourteen people, uploaded from the company census. `hub/user_directory.py`
holds the roster as data — level, name, title, phone, birthday, date of hire,
work email — and `sync_roster()` creates the missing accounts on boot. The
census fields live in `hub_user_profiles`, a **table of their own**, because
`create_all()` creates missing tables and never adds a column to an existing
one: six columns added to `hub_users` would exist on every local SQLite run and
be silently absent on the live Postgres, with every test green and every read
of them `None` in production.

**A re-run creates and nothing else, and each half of that is a way to be
wrong quietly.** A password is written at creation only — a sync that
re-applied it would hand all fourteen accounts back to a password printed in
this repository, on every deploy, with nothing on any screen saying so. A role
is never demoted, so a promotion made in the Users panel survives the next
deploy. A profile field is filled in, never overwritten, because somebody who
corrected a phone number has better information than the export does. What is
left is the one case a re-run is for: a person added to the roster afterwards
gets an account on the next boot.

**The starting password is valid for exactly one sign-in, and that is enforced
rather than noted.** `Smart12026!` is eleven characters and contains "smart1",
so `users.check_password()` refuses it — correct for a password somebody
chooses and wrong for a credential that exists to be replaced. It is written
through `users.set_starting_password()`, which bypasses the policy **and** sets
`must_change_password` in the same function, precisely so a starting password
cannot be issued without the gate that retires it. `must_change_password` was
already on the model and stopped nothing; it now blocks every page until it is
cleared. Both halves of that: the hub app's `before_request`, and **`AuthGuard`
in `wsgi.py`** — the hub gate covers hub routes only, so opening
`/tools/social/` instead of the dashboard was a way past the whole thing, with
the panel still showing the pill against their name. The flag rides in the
signed session cookie so the middleware can answer without a database read per
module request, and it cannot go stale: setting a starting password and
changing one both bump the session epoch, which invalidates the cookie carrying
the old answer.

**General Access is everything except Utilities.** `hub/access.py` is one
prefix list — `/diagnostics` (the Users panel included), `/status`,
`/activity`, and the APIs each of those pages fetches — checked in one
`before_request`. Not a decorator on forty views: that shape shipped once
already, and `hub/auth.py` names the result in its own docstring. The APIs are
in the list because gating the page while its data stays readable is a gate in
name only. Prefixes are matched on **path segments**, so `/statuses` is not
`/status`. `/login/health` and `/api/version` are exempt, because being locked
out of the sign-in diagnostic is how somebody locked out reports the problem.

The sidebar hides the Utilities section for a General account and that is
**only the hiding** — a General user who types `/diagnostics` still meets the
gate, and `test_user_accounts.py` asserts every admin-only nav entry is a path
`access.is_utility()` actually refuses, so the two cannot drift. Inside a
mounted module the nav reads the role from the **signed cookie** rather than
the database, because `HubBar` runs with no app context in front of every
module page; a stale nav is cosmetic and the gate re-reads the row.

**And the page was gated while its own data was not.** The list above says
the APIs are on it because "gating the page while its data stays readable is
a gate in name only" — and four of the Diagnostics page's own APIs were not
on it. `/api/db/structure` is the sibling of `/api/integrity`, the same panel
answering the same question, and only one of the two was covered;
`/api/environment` describes every setting and which name answered;
`/api/scheduler` reports the jobs. All four answered **200 to a General
account** while `/diagnostics` answered 403 at the same person, and nothing
on either side said so.

**One of them is not a read.** `POST /api/scheduler/run/<name>` fires a job
on demand — the Google sweep is 180 rate-limited Tag Manager accounts against
a per-day project quota, the Cloudinary reconcile is billed Admin API calls,
and the Knack pulls are a full paged object each. A POST any of the eleven
General accounts can fire is the cost `hub/domain_purchase.py` already
refuses to carry on a GET.

**Nothing outside the Diagnostics page fetches any of them**, which is what
made this safe to close: gating an API a General-visible page reads is how
`/api/status` came to render "✓ 0 checks OK · no issues" at somebody who had
just been refused it. `test_user_accounts.py` asserts that too, per path,
rather than leaving it to the reasoning that made the change look safe.

**The check is a sweep, not a list of the five that were open.**
`test_blueprint_guards.py` probes every route with **no session at all**, so
a route open to every signed-in member of staff is invisible to it — which is
why these five stood. The new sweep walks every admin-shaped route on the hub
app and requires each one to be gated or named in an exemption list **with
its reason**, held to `check_stale_json_exemptions()`'s rule that an entry
outliving its route goes on covering whatever is served there next. Three are
named: `/api/version` (the sign-in page and every footer), `/login/health`
(being locked out of it is how a locked-out person reports the problem) and
`/api/presence` (the headcount is everybody's; the account-by-account list on
`/status` stays in Utilities).

**The shared password counts as Admin, and that is a decision rather than an
oversight.** `PANEL_PASSWORD` grants a session with no account behind it, so
there is no role to read — and it is the emergency door, which is how somebody
reaches Diagnostics when sign-in itself is what is broken. Every use of it on
a Utilities path is logged as `shared_password_utility`. Clearing the variable
on Render is what closes the door once every account exists.

**Forgotten passwords name a person.** There is no mail sender here, so
"Forgot password?" on the sign-in page opens `/forgot`, which says so and names
John. The form it replaced collected an address and reported that an admin had
been flagged — a queue nobody watches, presented as though something had
happened. `/reset` still completes an admin-issued token; a GET with no token
redirects to `/forgot`, so there is one answer rather than two pages
disagreeing about whether the Hub can email you.

The Users panel has both routes and they trade differently: the **key icon**
sets a password directly and shows it once, for reading down a phone line; the
**link icon** issues the one-time reset link, for when you would rather not
know it. Both force a change at next sign-in — a password two people know is
not a password — and neither is stored. A blank box generates one, from an
alphabet with no `O/0` or `I/l/1` in it, because these get dictated as often as
they get pasted and a generated password nobody can read aloud gets replaced by
a typed one that is worse.

### Whose birthday it is, and how long they have been here

`hub/celebrations.py`, the block above System checks on the dashboard, and
`hub/static/hub-cheers.js`. The dates were already here: the census carried a
birthday and a date of hire for all fourteen people and both sat in
`hub_user_profiles`, readable one row at a time on the Users panel — which is
behind Utilities, so most of the company could not have found a colleague's
birthday if they had thought to look. A date nobody reads is the same as a
date nobody recorded.

The month block and the popup answer two different questions and are kept
apart deliberately. `this_month()` is **what is still to come**, today
included — a day that has passed is dropped rather than dimmed, because the
card is read to know who to say something to and there is nothing to say about
the 8th on the 9th. `today()` is the only thing allowed to interrupt anybody:
a popup about a birthday four days out teaches people to close it unread, and
then they close the one that mattered.

The birthdays, the anniversaries and the System status card sit in **one
column** on the dashboard, in that order. Two lists of three names side by
side was mostly empty table, and what somebody opens the dashboard for is the
same short question twice: who to say something to, and whether the Hub is
up.

- **A date nobody recorded is named.** Somebody with no birthday on file drops
  out of the list, and a list that quietly shrinks reads as a quiet month —
  so `not_recorded` carries the count and the block prints it with a link to
  where it is fixed.
- **A placeholder date is a missing date.** Seven of the fourteen census rows
  carry a hire date of 1 August 2019, which is when the Hub's book starts
  rather than when any of them started. `PLACEHOLDER_HIRE_DATES` reads it as
  *not recorded*, so those seven appear as start dates to fill in instead of
  half the company being congratulated on one day a year on a date none of
  them recognises — and they are counted **apart from the blanks**, because
  "we have no date" and "we have a date nobody believes" are explained
  differently to somebody who can see one sitting on the Users panel. Correct
  a row in the panel and that person appears here by themselves; empty the
  set once they all have real dates.
- **The year of birth is never published.** The panel holds it, this module
  reads it to work out the day, and it does not leave: a block that prints the
  whole company's ages is a different feature from one that says whose
  birthday it is. Years of service are the opposite — they are the point of an
  anniversary, so those are carried.
- **29 February is marked on the 28th in a common year.** Dropping it means
  one person's birthday never appears, and nothing reports that absence.
- **Somebody who started this month is welcomed, not given a 0th
  anniversary** — and somebody whose start date is still ahead of them is not
  congratulated for a job they have not begun.
- **The popup greets the right Todd.** Two people on this roster share a first
  name and two share a birthday, so `mine()` matches the signed-in
  **account's email**, and falls back to an exact display name only for the
  shared-password session, which has no account behind it at all.
- **It fires once per person per day**, and the marker is written when the
  popup is *shown* rather than when it is dismissed — a reload must not bring
  it back, and somebody who pressed Escape has still seen it. It is
  `localStorage`, so it is per browser: the failure mode is seeing it twice
  on a second machine, never a page that breaks because storage is blocked.
- **It never fires inside somebody else's iframe.** Hub pages are framed in
  Smart 1 Suite (`hub/suite_embed.py`), and a confetti cannon going off in a
  client-facing panel is not a feature.
- **The confetti is skipped for `prefers-reduced-motion`.** A full-screen
  particle system is exactly what that setting is for.

`?cheers=demo` shows a sample popup, marked as a sample, on any hub page;
`?cheers=preview` replays today's real one. The script is loaded from
`base.html` alone and not from the chrome `HubBar` injects into mounted
modules: one place that can raise an interruption is how you stay sure it is
raised once. `test_celebrations.py` asserts all of it, including that the
block sits above System checks and that the API refuses an anonymous request —
these are staff dates of birth.

### A warning nobody reading it can act on

`hub/housekeeping.py`, the **Housekeeping** card at the top of `/diagnostics`,
and `/api/housekeeping`. The birthday block ended with a sentence naming the
seven placeholder start dates and telling the reader to fill them in under
Users. Every word of it was true and it was on the wrong screen: eleven of the
fourteen accounts are General Access, and `/diagnostics/users` answers those
eleven **403** — a to-do addressed to people who cannot do it, printed under a
card somebody opened to find out whose birthday it is. And because that
sentence was the only record of the gap anywhere in the Hub, the three people
who *could* fix it learned about it by looking over somebody's shoulder. A
warning with no reader who can act on it is not a warning; it is furniture.

So a warning of that shape is collected here and listed where the person who
can act is already looking. Each finding names **the page a reader meets it
on** — the whole point of moving it is that the person who can fix it never
saw that page, so "7 placeholder start dates" without "on the dashboard, under
birthdays" has lost the half that makes it actionable — and names where it is
fixed. The panel sits above API health because its rows are somebody's to-do
rather than a machine's report.

What is *not* in it matters as much: a defect (`/api/integrity`), a provider
that is down (`hub/diagnostics.py`) and a setting that resolved oddly
(`/api/environment`) each already have a panel on that page, and two checks
asking one question and answering it differently on one screen is the trap
`jsonstore.unmirrored_json_writers()` exists to close. Housekeeping is data
somebody has to type in, and nothing else.

Four rules hold it up. **A source that could not be read is a finding, not an
absence** — `roster_gaps()` carries an error *beside* a perfectly good
fallback answer, so the error alone is not the test, and a report reading it
that way would call a working roster unmeasurable; the row says which store
answered when it was not the first-choice one, because the profile table is
where a corrected date lands and a census-roster answer may name somebody
already fixed. **A source that fails costs only itself**, named by the
exception it raised. **Nothing here reaches a provider**: each source reads
what the page it describes already reads, since a triage panel that costs
eight outbound calls is one people stop opening. And **a clean source is still
named**, in `clean`, or a panel with one row on it cannot be told from a panel
that only ran one check.

**The block still says it is not the whole roster.** That sentence existed for
a good reason — a list that quietly shrinks reads as a quiet month — so what
was withheld is only the half a General account cannot act on: the counts, the
names and the link. `housekeeping.withheld()` is the one place that decides,
because a template deciding it would be a second description of what a General
user sees, drifting the day a fourth kind of gap is added; the API applies it,
so the page cannot render a count that was never sent. `test_housekeeping.py`
asserts all of it, including that `/api/housekeeping` is refused to General —
it names staff and what is missing about them.

`celebrations._gaps()` is now the single classifier both screens read, and
`knack_data.export_state()` is the single answer to whether the committed
products export is behind the calendar — two copies of either would let the
dashboard and this panel disagree, with nothing on either screen saying which
to believe.

**A page that exists is not a page anybody can reach.** The dashboard's
partner row was five buttons written into `dashboard.html` — four links and a
grey "New Partner · Page coming" placeholder — while `partner.available()` sat
there written for exactly this job with no caller. The Digital Dictionary had
been in the repo, served and reachable, since the day the other four arrived,
and the dashboard went on offering four links and a promise. `partner.tiles()`
draws the row from the files on disk now: a page filed here appears without a
template edit, and one not yet filed greys out under its own name.

**And the same thing one layer down: a template nothing renders.** That one
at least had a caller waiting for it. A template has nothing at all to notice
it — it is valid Jinja, `tools/pagecheck.py` never requests it because no
route serves it, and `tools/linkcheck.py` names one only when it *also* finds
a broken `url_for` inside, so an orphan whose links happen to resolve is
invisible to every check here. What it costs is not disk. Three were found:
`modules/sites_admin/templates/site_detail.html`, rendered by nothing and
**restyled anyway** in the sweep that made Sites read like the rest of the
Hub — real effort spent on a page no request can produce; and
`modules/google_finder`'s `gtm_logs.html` and `reports.html`, which were
**byte-identical apart from the `<title>`**, a copy-paste nobody finished,
sitting in front of live `/api/reports/save` and `/api/reports/search` routes
with no screen. Reading the directory, all three looked like features.

`integrity.check_orphan_templates()` asks it directly, and two rules keep it
from being worse than the gap. **A computed name is still a render**:
`modules/scans` picks between `widget.html` and `widget_audit.html` in a
conditional and passes the result, so a check reading only the literal
arguments of a `render_template()` call reports the two most client-facing
pages in that module as dead — which is how somebody deletes a live page. So
a name is matched as a **string constant anywhere in the source**, looser
than a call site on purpose: missing an orphan costs a file nobody deletes,
and naming a live page costs the page. And **a test naming a template is not
a route rendering it** — the drift check's "prose is not a call site" rule
one step over, and not hypothetical, since the sweep that restyled the dead
`site_detail.html` added a test naming it, which would have hidden it for
ever. A partial reached by `{% include %}` and a layout reached by
`{% extends %}` are read out of the templates themselves rather than assumed.

It is **low** severity: an orphan breaks no page, it wastes the next person
who edits it. And it started at zero, because the three it found were deleted
in the change that added it.

**And the audit that would have said so was measured against a list nobody
had touched.** `hub/help.py` says the registry "can be audited for coverage —
`missing_for()` will tell you which screens have no help at all", and the
function is fine. What decided the answer was a **hand-typed list** at each
of the two call sites in `hub/help_routes.py`, and both had stopped keeping
up: `/api/help/coverage` named 23 screens and answered **`missing: []`** — a
clean bill of health — while the Proposal Builder carried bubbles on one
panel of its fourteen steps and the IO Builder, the Social Content Planner,
Web Tickets, Stock Photo Search, Scan Widgets, Website Blocks, GPT Ads and
Google Access carried none at all. `/api/demos/coverage` was worse: its one
finding was a walkthrough for `modules/proposal_builder`, whose own docstring
opens *"The retired Proposal Builder — a redirect and an archive"*. It was
reporting a gap in a module that no longer does anything and silence about
two dozen live ones.

That is the shape this file already names twice — a check measured against a
restated copy, reading as clean because the copy went stale — so
`hub/help_coverage.py` reads the **tiles on the two staff index pages**
instead. That is this codebase's own definition of a tool somebody opens, and
the conventions above already require one. Four rules. **Finding no tiles is
a failure**, not a clean sweep: the templates are parsed, and a parse that
comes back empty means the markup moved, so `measured` is False and the
report says as much rather than answering that nothing is missing. **An
unmapped tile is named, never counted as covered** — a help key's prefix is a
label chosen for the registry (`utm` is `modules/utm_builder`, `display_ads`
is the TypeScript renderer), so it is declared, and a tile in neither table
comes back under `unmapped`. **A page a client reads takes no staff help and
says why**: the nine landing pages and the MSA signing page are tiled for
staff and served to a prospect, and a bubble there is an internal note in
front of somebody we are selling to. And `stray_prefixes()` runs the other
way, because that direction is silent — help written under a prefix no tile
maps to leaves the tool reading as *missing* while its copy sits there
written, and somebody writes it twice.

**And the third side, which `stray_prefixes()` structurally cannot see.** It
reduces every screen to its **first segment**, so help written as
`hub.website_audit.*` reduces to `hub`, which `NOT_A_TOOL` exempts as the
dashboard and Client 360 — an exemption that has to be broad, since the Hub's
own pages genuinely are not tiled tools. So the forward direction is the only
one that can catch a tile mapped to a prefix naming the wrong screen, and it
fails in the **safe-looking** way: the tool reads as never explained, which
is a backlog entry rather than a defect, so nobody looks. That is what
happened to the Website Audit tool the release after it was given six bubbles
and a six-step tour — declared as bare `website_audit` against keys filed
under `hub.website_audit`, matching nothing, reported as carrying no help at
all. A prefix may name two segments now, because `hub.website_audit` is a
tool and `hub.prospect` is a record page and the bare `hub` they share names
neither.

`mislabeled_prefixes()` is the check, and it is deliberately **narrower than
"this prefix backs nothing"** — twenty-three tiled tools have genuinely never
had help written, and reporting those here would be a list somebody
re-triages on every run. The finding is a prefix that resolves to no screen
*while the registry holds one whose name contains it*: the only case where
"no help written" is a wrong answer rather than a true one. It is one line to
fix and a paragraph to write, so the two are counted apart. `test_help_layer.py`
feeds it the bug it was written for and requires it to say so, because a
check that reads green either way is one nobody can trust.

It **reports rather than gates**. Twenty-three of the forty-seven tiles had
no help behind them when it first ran; none of that broke a page, and a build
failing on it is a check switched off within a week. `env_report()`'s shape —
the thing that stands beside a check and says what the check cannot see.

**And the backlog it held is written down to zero.** The last twelve tools it
named — the two image optimizers, the PDF Optimizer, Client Image Uploads,
Landing Page Ads, Stock Photo Search, both radio builders, the IO Builder, the
Landing Page Maker, the GPT Ads Builder and Google Access — carry their copy
now, each key placed by the tool's own staff template under the prefix
`PREFIXES` declares, guarded `if help_dot is defined` like every call in this
Hub. Three of them are the shapes worth remembering. The **PDF Optimizer** is
a static file served by `send_file`, so there is no Jinja and no `help_dot`
global on it: it carries the raw `<span data-help>` that helper emits, which
`hub-help.js` mounts like any other. The **Image Optimizer** and **Page Image
Optimizer** had placed bubbles all along — borrowed from `image_creator.*` and
`seo_images.*`, so the audit read them as helped while coverage read them as
missing, and both were right; the borrowed keys are replaced with their own,
saying what *this* tool's control does rather than what a neighboring tool's
did. And none of it reaches a page a client reads: Fan Radio's `/r/` link, the
picker's `/pick/` page, Google Access's `/connect` flow and the built landing
pages stay outside the help layer, the rule `test_ads_explainer.py` holds the
public estimate to.

**And it is on the panel the other two halves are already on.** Bubbles,
walkthroughs and coverage are one question asked of three mechanisms — does
an explanation resolve, can a step still be driven, was one ever written —
and split across screens they come to disagree about which tools are
explained, which is the trap `jsonstore.unmirrored_json_writers()` exists to
close. `/api/help-audit` carries all three and the Diagnostics panel draws
them together. The renderer checks `measured` before it draws a count, for
the reason the whole change exists: *nothing to measure* and *nothing
missing* must never render alike.

**And the tool the report named loudest is explained now.** The Proposal
Builder is fourteen steps a rep spends a quarter of an hour in, and it
carried four bubbles on one panel of it — the reach figures — and nothing
anywhere else. The keys are written where this file already documents a
trap, because those are the places a rep gets it wrong and the copy can then
say what the field *does to the output*: the card rate is the buy-side
number and CPM lines are quoted at twice it, the working budget is what the
client asked for while the **plan** is what gets billed, a ZIP rule nobody
could read says *not applied* rather than quietly doing nothing, and an
acceptance is tied to one revision.

**They are placed on the step heading, by the one function that draws all
fourteen.** `renderStep()` is where a step's `help:` key becomes the
`<span data-help>` — so a step added next month gets its bubble by naming a
key rather than by anybody editing markup, and `hub-help.js`'s debounced
observer mounts it like any other. There is deliberately **no
`data-screen`**: a tour is anchored by selector and this is one page whose
markup is replaced on every step, so a tour written for it could not drive
past the step it started on — the silence `hub-demo.js` was fixed to stop.

**That made a third way of placing a bubble, and two checks could not see
it.** `hub/help_audit.py`'s note says a bubble is placed two ways —
`help_dot()` in Jinja and `data-help` on an element — and a key named in a
step descriptor is neither, so thirteen live entries read as registered and
never placed. It is a *literal* either way: the key is written in the file
and resolves from it, unlike the runtime-assembled kind that is named rather
than guessed at. And the guarded-call check matched the bare token
`help_dot(`, so the comment in that template *explaining* that `help_dot()`
is a Jinja global and cannot be used from JavaScript was reported as an
unguarded call — **prose is not a call site**, for the fourth time in this
file, so it matches a call inside a Jinja delimiter now.

**The coverage number does not move, and that is correct.** Coverage asks
whether a *tool* has any help, with the tile as the unit, and this tool
already counted as covered on the strength of those four bubbles. Thirteen
of fourteen steps gaining an explanation is invisible to it, because what a
tool's screens are is not derivable from anything the Hub holds. Per-tool is
the honest granularity; the finer answer would need a list, and a list is
what the audit had before.

**And the second one down that list is the document that bills the client.**
The IO Builder is a conversation rather than a stepped wizard, so the anchors
are the decisions that are static markup — where the campaign is loaded from,
the unfinished-order list, the creative checklist, the rates on the report,
the two PDFs and Submit — and the interview asks its own questions in words
already. Each entry is on a trap this file names: a line carried from a
proposal arrives at the **quoted** rate rather than the card's buy-side one,
so the order bills what the proposal promised; the fee fields take an amount,
a percentage, INCLUDED or NONE and not a sentence; the browser draft is
instant and the server copy is what survives a different machine, with a
colleague's unfinished order listed rather than hidden, because hiding it is
how the same IO gets built twice.

**And two of them were written twice, from two branches, against the same
screen.** Two sessions explained this tool in parallel; both merges were
textually clean, and what landed was `io_builder.report.rates` registered
**twice**, with two different accounts of what the rate on that pane is. One
said every rate comes off the shared card — true of where the number is
derived from, and the exact confusion `lineForIO()`'s own comment exists to
undo, since it sends `sellRateOf()` and the pane shows $8.50 where the card
lists $4.25. `_BY_KEY` is `{h.key: h for h in REGISTRY}`, so the later entry
silently won and the earlier became dead copy behind a dot that still drew;
`tour()` walks the list instead, so a duplicated key carrying `step=` would
have put one step on a walkthrough twice. Nothing reported any of it — every
key resolved, every dot rendered, and `help_coverage` counted the tool as
covered, which is the whole difficulty: a collision here reads as success
from every direction. `test_help_layer.py` asserts a key is registered once
and that **every registered entry survives into `as_json()`** — said against
`len(REGISTRY)` rather than against a set of the same keys, because both
sides of that comparison collapse the duplicate and the check passes while
the entry is being lost.

**And what submitting does not do is the one worth saying out loud.** It
files the order, sends it to Suite and registers a genuinely new business as
an overlay — and it does not set the campaign up. An order whose products
never arrive looks exactly like one that was handled, which is why
`hub/io_reconcile.py` exists; the bubble names that report, so the tool says
where its own blind spot is answered rather than leaving a rep to find out
when a client asks why nothing ran.

### Who is signed in, and what that number is allowed to claim

`hub/presence.py`, the top of the **System status** card on the dashboard, and
the list behind it on `/status`. There is no session table: signing in issues a
**signed cookie** and the server keeps nothing, which is what makes two workers
and a restart survivable and also means nothing is ever told that somebody has
left. Closing a laptop and reading a long page are indistinguishable from here.
So "logged in now" is not a question this Hub can answer, and the number it
does answer — **people seen in the last fifteen minutes** — is printed with
those words beside it on every screen that shows it, from
`presence.summary_line()` so none of them can word it differently or print the
count without the window.

- **It is recorded from both halves of the app.** The hub app's
  `before_request` covers hub pages; `AuthGuard` covers the twenty mounted
  modules, and it is WSGI middleware with **no application context** — the
  `flask.g` trap that made the Google sweep report an empty book. It pushes
  the hub app's context itself, and only once the throttle says a write is
  due. Without that half, somebody working in Smart 1 Ads all morning drops
  out of the count fifteen minutes in, which is a wrong number that looks
  exactly like a right one.
- **One write per person per minute per worker.** Both hooks run on every
  request, so without the throttle this is a database write per request. The
  window is fifteen minutes wide; a minute of staleness changes no answer.
- **The page somebody is on is deliberately not recorded**, and the test
  asserts the table's columns to keep it that way. A path column turns a
  headcount into a minute-by-minute log of what each member of staff was
  doing, and the moment it exists somebody reads it that way. The row is
  overwritten rather than appended, so it cannot become a timesheet either.
- **A new table, not a column on `hub_users`** — `create_all()` never adds a
  column to an existing table, the same reason `hub_user_profiles` is its own.
- **Identity resolves to exactly one account or to none.** The module cookie
  carries a display name, so `identify()` is the only way back to a person:
  one match is that person, no match is a `PANEL_PASSWORD` session (counted,
  and **named** as shared rather than folded into a headcount people read as
  "how many of us are here"), and two matches is a real person we cannot name
  — never a guess between them, and never promoted to "shared".
- **`/api/presence` is deliberately not a key on `/api/status`.** That path is
  in `access.UTILITY_PREFIXES`, so for the eleven General accounts it answers
  403 — the headcount would have been admin-only while sitting on everybody's
  dashboard, reading as zero. The count is everybody's; the account-by-account
  list on `/status` stays in Utilities.
- **Nothing in it may raise.** A presence write failing costs a page nothing,
  and `active()` reports that it could not look rather than returning an empty
  list: "nobody is signed in" and "we could not read the table" are different
  answers.

That last trap was already live on the card this sits on. The dashboard's
mini status panel fetches `/api/status`, a General account is refused it, and
the panel rendered the missing `checks` array as **"✓ 0 checks OK · no
issues"** — a green tick over a question that was never asked, for eleven of
the fourteen people. It says what happened now.

**And three of the rules above were true of this module and false of the two
screens that draw it.**

*"One row per person"* held until the account table blinked. `identify()`
enumerates three answers in its own docstring and returned **two bits**, so
*"more than one account has this name"* and *"we could not ask"* were the
identical value — and `touch_display()` then keyed a row on the **name** for
somebody who already had one keyed on their **email**. Two rows, counted
twice, for the fifteen minutes of the window, drawing two chips with one name
on them; and `/status` printed *"no account matched this name"* about
somebody who has one, which is a confident answer to a question that was
never asked. It carries whether it could look now, and not knowing who
somebody is writes **nothing**: the row from a minute ago is still inside the
window and still right, so inventing a second identity is the one thing that
cannot be recovered from.

*"`active()` reports that it could not look"* — and what it reported was
`str(exc)`. Both screens interpolate that straight into the page, so a
SQLAlchemy `OperationalError` puts the **database host, the user it tried to
authenticate as and the SQL it was running** on the dashboard, which every
one of the fourteen accounts opens. An exception is not a message, which is
the rule the image and PDF optimizers were fixed for; it is a sentence now
and the cause goes to the log.

*"every screen that prints the number says so in those words"* was the whole
argument for `summary_line()` existing — *"so none of them can print the
count without the window it was measured over"*. The dashboard's headline
read **"N signed in now"**, the exact phrase this module's docstring calls a
confident answer to a question nobody here can answer, with the window
relegated to an 11.5px grey note beneath it — under a comment in that same
file claiming the window is never left off the number. Read at the size
somebody actually reads it, the caveat was not there. The headline says
*seen recently* and `summary_line()` still gives the exact window below it.
`test_user_accounts.py` asserts all three, the two templates included.

**Nothing here is a crawler's business.** `hub/no_crawl.py`: `robots.txt`,
`/llms.txt`, and an `X-Robots-Tag` on every response — added as WSGI middleware
in `wsgi.py` rather than as a Flask `after_request`, or it would have covered
the hub's own pages and left twenty mounted modules without it, including every
public landing page, which is the only part of this Hub a crawler can actually
reach. The header is also the only layer that reaches a proposal PDF or a CSV
export, which a `<meta>` tag cannot. The AI crawlers are listed **by name**
beside the wildcard because several of them read robots.txt by name only —
`Google-Extended` and `Applebot-Extended` exist as their own tokens precisely
so a site can refuse AI training while staying in the search index, and a
wildcard does not always register with them. There is deliberately no
`Sitemap:` line.

**Three shapes of brute force, and the old counter caught one.** One account
hammered from one place is what six-strikes-per-IP was for. One account
hammered from everywhere is caught by the per-account lockout on the user row,
which is shared across both gunicorn workers and survives a restart — the
in-memory counter does neither. **Credential stuffing** was caught by nothing:
one guess against each of fourteen known addresses never reaches six on any
account, and the per-IP counter was only ever reset by a success.
`throttle_fail()` takes the address now and locks an IP that has tried more
than four distinct ones; the addresses are **hashed**, because that dict is
read by a status report. Lockouts **escalate** — 15 minutes, an hour, six
hours — since an attacker who can wait fifteen minutes has unlimited six-guess
batches, and the ladder resets on a success so three bad mornings do not
compound. Every credential endpoint goes through it now, `/reset` and
`/account` included: completing a reset was the one with no throttle at all.
`auth.client_ip()` is the single place the last-hop rule lives — it was written
out longhand at four call sites and one of them had it backwards.

Google sign-in is the intended destination and `hub/identity.py` already has
it, behind `HUB_GOOGLE_LOGIN`; it stays off until the OAuth consent screen
clears review. Both routes resolve to the same account row, so nothing above
has to change when it lands.
