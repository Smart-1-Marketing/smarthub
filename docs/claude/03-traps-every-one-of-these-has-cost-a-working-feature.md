## Traps — every one of these has cost a working feature

**A hub route under a mounted prefix is unreachable.** DispatcherMiddleware
routes purely by URL prefix, so `@app.route("/sites/match")` on the hub app
never gets called — `/sites` belongs to Sites Admin. This has bitten three
times. `/api/integrity` has a high-severity check for it.

**Module Jinja environments are separate.** Globals registered on the hub app
are invisible inside a mounted module. `{{ help_dot(...) }}` in a module
template raises `UndefinedError` and 500s the page unless
`install_template_helpers()` ran for that app. Every helper call is written
`{{ help_dot('x') if help_dot is defined else '' }}` so a missing registration
degrades to a missing icon rather than a dead page. Keep that pattern.

**The scheduler saying it is running is not the jobs working.** `status()`
answered one question well — is a worker holding the lock — and drew a green
pill for any job whose last run succeeded, however long ago that was. The
jobs share one thread, so a loop stuck on a long one stops every job
behind it: an hourly job that last ran three days ago read as healthy, and the
only way to notice was to do that arithmetic eleven times. Overdue is measured
now (`_overdue()`, past twice the interval with a five-minute floor so a
one-minute job is not called late at sixty-one seconds) and drawn as a fault,
because the thing the job refreshes is stale whether it raised or simply never
ran.

**A streak is not a failure.** `_state[name]` was overwritten every run, so a
job that had failed fourteen times running and one that blipped a minute ago
rendered identically. `fails` counts consecutive failures and `last_ok` keeps
the last good run, so a broken job says how long it has been broken.

**And half of all page loads could not see any of it.** `_state` is
per-process and in memory; the standby worker holds nothing. Every job there
read *"Not run yet this boot"* behind a grey pill — indistinguishable from a
scheduler that has never run at all, so the same panel was alarming or
reassuring depending on which of the two workers answered. `timings_visible`
is on the answer now and the panel says *not measured on this worker* once,
rather than drawing eleven rows of nevers. Nothing is called overdue on the
strength of what a process cannot see: unknowable is `None`, never `False`.
`test_scheduler_health.py` drives the clock rather than waiting on it.

**Two gunicorn workers.** Anything with a timer or a background thread runs
twice unless it takes the leader lock in `hub/scheduler.py`. Same reason
`create_all()` is wrapped in a Postgres advisory lock — concurrent `CREATE
TABLE` produces a `pg_type_typname_nsp_index` unique violation on every deploy.

**A scheduled job has no request, so `flask.g` is not there — and the module
whose database hangs off `g` reports an empty book instead of an error.**
Google Finder reached its token table through `get_db()`, which caches on `g`
and is closed by a teardown when the request ends: right for a route and
unusable from anywhere else. The scheduler sweeps Google from a background
thread, so every read raised `RuntimeError: Working outside of application
context` *inside* `connected_accounts()`'s `except Exception: return []` — and
the sweep concluded **"No Google accounts are connected"**, every three hours,
while the accounts sat in the table and the Google Finder pages listed them
happily. Nothing errored at either end. The same swallow hid the other half:
`mark_account_reauth()` could not write either, so an account whose refresh
token had died was never marked `REAUTH_REQUIRED` and went on reading as
healthy. `/api/google/rebuild` calls the same sweep under the *hub* app's
context, where `g` exists but google_finder's teardown never runs for it —
one leaked sqlite handle per rebuild.

`modules/google_finder/app.py` reaches that table through `_db()` now, a
context manager that uses the `g` cache only when the context in play is that
app's own and otherwise opens and closes its own connection. Every call site
goes through it, so the next function added there does not have to know which
of the two worlds it will be called from — the same reason the Commercial
Builder's guard sits on the blueprint rather than on forty views. And
`connected_accounts_result()` returns `(accounts, error)`, because **"nobody
has connected one" and "we could not look" are different answers** and only
the first means there is nothing to do; a stored row that will not decrypt is
named too, or a rotated `TOKEN_ENCRYPTION_KEY` reads as an empty book. It also
sorts what the scheduler writes into the activity log: nothing connected is a
*state*, so `hub/google_index.py` logs `build_skipped` only when the reason
**changes**, while a genuine `build_failed` is logged every run — an
unconfigured Hub was writing eight failures a day for ever and the real ones
were sitting in the middle of them. `test_google_index.py` asserts all of it,
including a read from a background thread.

**The hub app injects its chrome into every HTML response it returns.** The
`after_request` in `hub/__init__.py` adds the sidebar, the help layer and the
feedback tab to any 200 `text/html` reply whose path is not in `CHROMELESS`.
That is right for a staff page and wrong for anything a client or a prospect
sees, and it fires on *hub* routes — `bare_prefixes` in `wsgi.py` only covers
dispatcher-mounted modules, so it does not save you here. A built landing page
under `/sales/landing/p/` is served to a prospect and is often pasted onto the
client's own domain; it is in `CHROMELESS` for that reason, and the entry is
the longer prefix so the maker at `/sales/landing` keeps its chrome. Any new
public hub route needs the same treatment. `test_landing_maker.py` asserts it.

**A page can ask the Hub chrome for its icon rail, and asking is not
choosing.** The wide tools — the Display Ad Builder's bench, the Proposal
Builder's wizard, the IO's printable documents — lose 224px of a laptop's
width to a nav nobody is reading while they work, which is what turns a step
into a horizontal scroll. Two ways to ask, because two arrived at once and
both are in use: `render_sidebar(collapsed_default=…)`, decided server-side
from the path, and `data-s1hub-collapse="1"` on the body, declared by the
page's own template. The attribute simply sets the same flag, so there is one
decision rather than two racing each other.

**The distinction that matters is between asking and recording.** `coll()`
writes `s1hub:collapsed`, and the automatic call that applies a page's request
used to write it too — so one visit to a collapsed-by-default tool stored the
preference globally and every other screen in the Hub came up collapsed,
without anybody having pressed anything. It also quietly retired the feature:
after that first visit there was no longer such a thing as "no stored
preference", so the page default was never consulted again. Only a real press
of the toggle records a preference now (`coll(on, persist)`), and a stored one
still wins in both directions — a tool that starts collapsed can be opened for
good.

**A spinner says something is happening and cannot say what.** `.spin` was
defined seven times across this repo and `.spinner` and `.cb-spinner` twice
more — hub.css, sales_builder, page_image_optimizer, stock_photos, ads_base,
the two scan widgets, the Commercial Builder and the two Node-served ad
builder pages — each a 2px border arc at a slightly different size in a
slightly different gray. That is the drift `hub/storage.py` and
`hub/images.py` exist to stop, wearing a spinner: the next improvement to it
would have had to land ten times and would have landed in one.

`hub/static/hub-thinking.js` is the one implementation, loaded the way
hub-crumbs.js is — base.html on hub pages, injected by `HubBar` into the
twenty mounted modules and by the hub app's own injector into the
blueprint-registered ones, which is **three** code paths and the reason a
script wired into only the first works on the screen it was tested on and on
none of the twenty it was not. The animation lives in `hub-help.css`, which
is already injected everywhere the script is; the glyph is drawn by the
script. Two files on purpose — a page that failed to run the script still
gets a static mark rather than an empty box — and therefore two files that
can come to disagree, which `test_thinking.py` asserts they have not.

**It upgrades what is already on the page.** Every `.spin`, `.spinner` and
`.cb-spinner` becomes the glyph, on a debounced `MutationObserver` because
Client 360, the SEO client page and half the tools draw their panels from a
fetch and a single pass at load upgrades the shell and misses everything
drawn after it. Fifty call sites needed no edit. The class list is
`hub/config.py`'s ALIASES rule for the same reason — **only spellings
actually in use**: `.search-spinner` is Google Finder's own SVG and
`.spin-cap` is stadium's caption, so neither is listed on the chance it might
one day mean this.

**Three glyphs, because the three waits are not alike.** `ai` is a model
writing — tens of seconds, billed, and the answer is prose somebody reads; it
draws the ✨ Client 360 already puts on its own AI control, so the two read as
the same thing happening. `scan` is us reading somebody else's website or
sweeping an account — minutes, and the wait is their server; it draws a dish.
`wait` is our own database, and it draws the arc all ten copies already drew.
A bare `.spin` upgrades to `wait`, because that is what it meant; a screen
asks for the other two with `data-s1-think` on the element or
`data-s1-thinking` on any ancestor — one attribute on a panel rather than one
per call site, so a button added to that panel next month is right by
default.

**A spinner for a minute reads as a hung page.**
`modules/ads_builder/templates/ads_generator.html` had worked that out and had
its own copy of the stage timer; both halves move here so nothing else has to
discover it again. `attach()` takes **stages**, timed rather than reported —
the server does this in one request and streams nothing back, so the wording
says what is being worked on and never that a step has finished — and it draws
an **elapsed line past six seconds**. Not from the first second: a stopwatch
on a two-second read is noise and a screen that counts at you teaches people
to expect a wait. Past six it is the only thing separating a slow answer from
a dead one, which is why the QA reports have it: a first run of the day there
is a year of QuickBooks invoices and a name match per row, and it used to say
"Running report…" in two words with no mark and no sign it was still going.

Four rules on it. **Nothing in it may raise** — an indicator that breaks the
page it is reporting on is worse than none, so `attach()` returns a handle
with a `.done()` even when it found nothing to attach to, and every caller
guards on `window.S1Think` so a missing script costs the mark and never the
message. **It never claims what it does not know**: `.done()` stops the
animation and does not write "Done" or draw a tick, because whether the call
succeeded is the caller's answer and a tick over a failed one is the
confident wrong answer this codebase keeps undoing. **`currentColor`, never a
palette** — forty modules with no shared stylesheet, and inheriting the
surrounding text color is the only way one glyph reads on a white card, a
navy button and a dark landing page without any of them being edited. And
**`prefers-reduced-motion` drops the motion and keeps the mark**: that setting
asks for less movement, not less information, and a wait that goes invisible
for those readers is the feature failing in exactly the place it was needed.

The elapsed timer **stops itself when its box leaves the page**
(`isConnected`). Half this Hub ends a wait by assigning `innerHTML` over
whatever was there, and a caller that does so has not done anything wrong;
requiring fifty call sites to remember `.done()` is how one of them forgets
and leaves a timer running for the life of the tab.

**The three pages a prospect sees carry it inline instead.**
`/scans/w/<slug>`, the audit widget and the waiting page are served to a
stranger on somebody else's website, where a Hub script is a new outbound
dependency on a page whose whole job is to load. So the dish is inlined —
**once**, as `modules/scans/templates/_scan_mark.html`, a macro all three
import rather than the fourth, fifth and sixth copy of the border spinner
they each carried. Same path, same speed, same reduced-motion rule as the
Hub's own, and `test_thinking.py` holds the two in step rather than memory: a
prospect who starts a scan on a client's site and a rep who starts one from
Site Scans are waiting on the identical thing and it must not look like two
features.

**And the pages a *client* opens were chrome-free, which switched the mark off
with the chrome.** Every client-facing surface here is deliberately outside
the injector — `CHROMELESS` in `hub/__init__.py`, and the `PUBLIC_PREFIXES`
each mounted module declares — because the staff sidebar, the help layer and
a feedback tab have no business on a document a client reads.
`hub-thinking.js` rides in with that chrome, so the exemption took the mark
with it and nothing anywhere said so. A client approving a finished TV cut
pressed a button that grayed out and said nothing at all; a client asking for
a change to an estimate got the same; and a client swiping an idea on a phone
got **no visible change whatever** — the double-tap guard, which is correct,
then met the second tap by returning, which is a button that reads as broken.
Where they did say something they each said it in their own words, four ways,
which is the drift `hub/storage.py` exists to stop one audience further out.

`hub/thinking.py` is the block those seven pages carry instead, and it is a
**Jinja global** rather than a macro because they are spread across four apps
with four separate environments — the first trap this file names. It is
registered by `install_template_helpers()` for every mounted module and by
`register_help()` for the hub app, where the blueprint-registered Commercial
Builder renders, and **both halves are needed**: three of the seven are
mounted and the review link is not. Every call site is written
`{{ s1_wait_assets() if s1_wait_assets is defined else '' }}`, the `help_dot`
guard, so an environment that never got the registration loses the mark
rather than the page.

It is `window.S1Wait` and deliberately not a second `S1Think`. It draws the
glyph, swaps a button's label and fills a status line; it has no stage timer,
no elapsed line and no observer upgrading existing spinners. Reusing the name
would make one name mean two different sets of promises depending on which
page you were reading, and somebody would eventually call `.stage()` on the
half that has none — and the elapsed line would be wrong here anyway, since
these are single short POSTs and a stopwatch on one is the noise
`hub-thinking.js`'s own note warns about. `.done()` puts the control back
exactly as it was and writes no "Done" and no tick, because whether the call
succeeded is the caller's answer.

There are now **three** server-side drawings of these glyphs — this module,
`_scan_mark.html` and the ad builder's `embed.html` — and that is a decision
rather than neglect. The scan pages are self-contained today and must not
gain a runtime dependency on a global to save a duplicate; the embed is
served straight off the Node renderer where no Jinja global exists at all.
`test_thinking.py` holds all four implementations in step, and the four
client-facing tests that already fetch these pages each assert the mark
reaches the **served** page — checking for the guard alone proves nothing,
since every call site contains it whether the block was emitted or not.

**Seven found by reading is how the eighth was missed.**
`modules/fan_radio/templates/share.html` is the page a rep mails a client to
approve a radio spot — `/r/<token>`, one of the three prefixes that module
declares public — and approving said *"Sending…"* in a message line and
grayed a button out, with nothing moving. It is the same defect as the seven
above and it was not on the list, because the list was written by opening
files. A list of the seven we fixed proves nothing about the ninth, so
`test_thinking.py` asks the question of **every public route** instead: it
reads each module's own `PUBLIC_PREFIXES` / `PUBLIC_PATHS`, finds the route
functions under them, collects the templates those render, and requires any
that runs a `fetch` or a `sendBeacon` to carry the mark or be named with a
reason. `test_blueprint_guards.py`'s rule, wearing a spinner.

Three things it has to get right. **A computed template name is still a
render** — `modules/scans` picks between `widget.html` and
`widget_audit.html` inside a helper and passes the result, so reading only
the literal arguments would skip the two pages a prospect most often meets;
the helper's own string constants are read, the looser reading
`check_orphan_templates()` settled on for the same reason. **Finding nothing
is a failure**, not a clean sweep — the count is asserted *and* four pages
are named, because a set of the right size and the wrong contents is the same
failure one step on. And **what it cannot reach is said rather than left
implied**: the display-ad proof is served straight off the Node renderer, so
no Flask route renders it and no Jinja global exists on it — the position
`embed.html` is in, and the reason both carry their own inlined glyphs.

The one exemption is the media calculator, named with its reason: it already
draws a complete inline mark of its own — its own SVG, `role="status"`,
`aria-live` and a reduced-motion rule, torn down in a `finally` — and it is
framed on smart1marketing.com, where the shared block would be several
kilobytes on a page whose whole job is to load, to replace six working lines.
Converging what it *draws* with the Hub's own arc is separate work, and the
check fails if the exemption ever outlives the page or the page gains the
block anyway.

**The screen with the longest waits in the Hub had no mark at all, and
nothing could have found it.** The Display Ad Builder's build screen makes
three billed calls — two image generations and a copy draft, each tens of
seconds — and carried no spinner, no `.spin`, no class the upgrader could
have caught: a sentence of text that did not change, which is the note this
file already makes about the QA reports saying "Running report…" in two
words with no sign they were still going. It is the one module that is not
Python, so a sweep for `.spin` in templates and stylesheets went straight
past it. `hub-thinking.js` was *there* the whole time —
`hub/ad_builder_proxy.register` is a blueprint on the hub app, so the hub's
own injector reaches it even though the response is streamed — and the page
simply never asked.

Six waits hang off one panel there and they are not alike, so `bgBusy(what,
kind)` takes the kind rather than each call site being edited again the next
time the mark changes: two are a model drawing, two are somebody else's
server (the client's own landing page, the stock libraries) and two are our
own storage. **Two of them change hands halfway** — the page is fetched and
*then* a model writes from it — so `attach()`'s handle gained
`stage(text, kind)`, which swaps the glyph and keeps the box. Two marks in
sequence would say the same thing and would read as two waits rather than
one that moved on, and each would restart the elapsed line, which on the
longest wait on that screen is the number that matters. And the reading step
is claimed **only when there is a page still to fetch**: `ensureLanding()`
caches, so a second draft goes straight to the model and announcing a step
that is not happening is the indicator claiming what it does not know.

**And the customer-facing half of that tool is a fourth surface, not a
fourth copy of the decision.** `modules/ad_builder/public/embed.html` is the
intake form a client frames on their own marketing site, served straight off
the renderer with `frame-ancestors` set — and it is not one of the proxy's
`PUBLIC_PATTERNS`, so a prospect never reaches it through the Hub and
`hub-thinking.js` is not on it and cannot be. Same answer as the three scan
pages: the glyphs are inlined, the same paths at the same 1.9s, with the
same reduced-motion rule, and `test_thinking.py` holds all three
implementations in step. What it also carried was **its own third copy of
the stage timer** — two captions alternating every 1.8 seconds, for ever,
which past about four seconds is exactly what a hung page looks like: the
words go round and nothing else changes. The caption is said once now and an
elapsed line carries the rest, silent until six seconds and stopping itself
when its box leaves the page, because half that form ends a wait by
assigning `innerHTML` over whatever was there.

Four escape sequences on that same form reached the customer as text. A JS
string literal written `'\\u2026'` is a backslash followed by `u2026`, so
somebody filling in the form read *"Creating your image\u2026"* and *"\u2713
This photo will be used"* — on the page they were looking at while they
waited, which is the only place they appear. Nothing in this repo could see
it: the file parses, the page renders, and the English is correct.

**Bubbles mount on late-rendered content.** Client 360, the SEO client page
and Image Creator draw panels from a fetch. `hub-help.js` runs a debounced
MutationObserver for this. A bubble added to a JS-rendered panel works; one
added before that observer existed did not.

**`audit.log()`'s first positional is `module`.** Passing `module=` in the
extras raises `TypeError` and silently zeroes cost tracking. Use `tool=`.

**And binding the logger is not calling it.** `/api/integrity`'s silent-module
check asked whether the *string* `"for_module("` appeared in a module's source,
which the binding alone satisfies. So seven modules — `calculators`,
`google_finder`, `image_optimizer`, `page_image_optimizer`, `pdf_optimizer`,
`sites_admin` and `tickets` — imported `hub.audit`, bound it to `_audit`,
wrapped it in a no-op fallback for running standalone, wrote a comment above
the import explaining exactly why attribution mattered there, and called it
nowhere. The comments are the part worth reading: pdf_optimizer's said *"work
that isn't logged is work nobody can point to later"*, and
page_image_optimizer's and sites_admin's said *"an unattributable change to a
client's account is one nobody can explain later"*. All three were true and
none of them wrote a row, so **deleting a client's live website, connecting a
domain, deploying a tag into somebody else's Tag Manager container and
compressing a client's documents were the least attributable actions in this
Hub** — behind a check reporting them clean. That is the declared-but-unwired
integration point this file already counts in `RECORD_HOOK`, `io_creative`,
`manifest()`, `thumb_url()`, `mark_pushed()` and `check_limits()`, wearing the
activity log. The check reads a **call** now, through the AST — an import
cannot satisfy it, and a docstring quoting `audit.log(` is not a call site,
the rule `hub/config.py`'s drift check gives. A module whose work genuinely
does not belong in the log is declared in `audit.NO_ACTIVITY` with the reason
rather than left as a dangling import: `calculators` is the only one, because
what a public estimate box produces is a **lead**, and leads go through
`hub/leads.py`.

**An exception is not a message, and both file tools handed one over.** The
Image Optimizer answered an unreadable upload with Pillow's own text —
*"cannot identify image file `<_io.BytesIO object at 0x7f…>`"*, a Python repr
with a memory address in it, printed where "that file is not an image we can
read" belongs and reading to whoever uploaded it like the tool had crashed
rather than like their file was wrong. The PDF Optimizer was worse: `_run()`
raises carrying **the last 2000 characters of Ghostscript's stderr**, and the
500 handler interpolated it straight into the response — absolute
temp-directory paths and the uploader's own filename, handed to a browser.
That is the rule `modules/fan_radio.fail()` states, in the two modules nobody
had tested. Both say something actionable now and log the cause; **our own
validation text is the one exception message that belongs on screen**, so
`ValueError` is caught apart from the provider's, or the fix would swallow
"Width must be a whole number" with it. A missing binary is separated again
and answers **503** pointing at `/status`, because that is a broken deployment
rather than a bad document and `[Errno 2] No such file or directory: 'gs'`
tells the person holding the PDF nothing.

**And its `/health` said `ok` while it could not work at all.** The PDF
Optimizer is a wrapper around `gs` and `qpdf`; with either missing every
optimize fails, and the probe went on answering `{"status": "ok"}`. The Hub's
own `/status` has reported *Ghostscript / qPDF* as an error the whole time, so
the module and the status page were two answers to one question and the
module's was the confident wrong one — the `/api/db/structure` versus
`/api/integrity` trap, one tool further out. It reads the same fact now.

**Those binaries were in the Dockerfile and in no check anywhere**, so the
compression the tool exists for ran in production and nowhere else. CI
installs them now. `test_image_pdf_optimizers.py` still passes without them
and **says so out loud** rather than reporting an unrun path as a clean run.
It also asserts its own animated fixture is animated before using it: a first
pass reported that animation was being flattened, and the fixture was what had
flattened it — four frames of one flat colour, collapsed by Pillow before the
module ever saw them. What is **reported rather than fixed** is that a PNG
target the 160px floor cannot reach comes back several times the size asked
for, `200 OK`, with nothing saying so.

**And a third way, which is the row arriving and being dropped at the door.**
`work_log()` reads the client from exactly five keys — `client`,
`client_name`, `company`, `business_name`, `tool_client` — and from nowhere
else. **UTM Builder** wrote `_log("links_saved", detail=client, …)` and
**Background Remover** wrote `_log("cutout_saved", detail=client, …)`, so
every tracked-link batch and every cut-out saved against a client was written
to the activity log, kept, indexed, and then dropped on the way to the record
it was written for. The bg_remover case is the one worth reading: the comment
directly beneath that call explains at length that a cut-out has to reach the
client's *gallery* or it is absent from the one page somebody opens to see
what we have produced — that half was done, and the activity-log half was one
keyword away. UTM Builder was dropped **twice over**, because it also logs
under the name `utm` while `WORK_KINDS` was keyed on `utm_builder`, so the
tool read on every client record as one nobody had ever used. `CLIENT_KEYS`
is the list, written beside the walk that checks it, and `check_client_attribution()`
runs at **high** on `/api/integrity`. It asks the question
`check_work_kinds()` asks from the other end: not *which names log a client
the table cannot name*, but *which names the table knows can never carry a
client at all*. Keyed on `utm` and declared in `audit.LOG_NAMES` rather than
renamed — the `display_ads` rule — because renaming the call site orphans
every row already on disk.

**A check with a false positive is a check somebody switches off, and it
takes the real findings with it.** The first draft reported `ads_builder`,
which names its client perfectly well: it mirrors through
`store.log_event(**details)` and the client arrives from `app.py` through
that forward, which the AST cannot follow. A call site forwarding `**kwargs`
is counted apart now — **not determinable is not the same answer as never
does**, the rule this file gives about a source that could not be read.
`test_work_attribution.py` drives the real `work_log()` rather than asserting
about source, checks all five keys land, and points the check at a fixture
that plainly has each bug.

**A module's own `log()` wrapper hid the same failure one step on.**
`check_work_kinds()` counted only a direct `audit.log("mod", …, client=…)`,
reasoning that a bare `log()` is a wrapper whose first argument is the event
rather than the module. True, and it drops those modules entirely — the name is
one level up, in whatever bound the wrapper (`log = audit.for_module("msa")`,
or `def log(event, **extra): hub_audit.log("radio_promo", event, **extra)`).
Four fell through, and **`radio_promo` is the one that shows the cost**:
`fan_radio` has been in `WORK_KINDS` since it was written and its sibling was
not, so a client who had a Fan Radio spot made appeared on their own record and
a client who had a Radio Promo spot made did not — two tools writing, casting
and recording a commercial for the same client, one of them invisible.
`gpt_ads`, `landing_ads` and `msa` were the others; `hub/prospect.py` and
`hub/stale_creative.py` surfaced with them and are the *other* answer, in
`NOT_WORK`. **And the check's two halves each had their own copy of the walk** —
`stale_work_exemptions()` asks what no longer logs, the same walk from the
opposite end, so the moment one learned to resolve a wrapper and the other did
not, every `NOT_WORK` entry added for a wrapper-shaped call site was reported
stale. They read one `_client_log_modules()` now, the
`/api/db/structure` versus `/api/integrity` rule. `test_activity_logging.py`
asserts all of it, and both checks were reverted and confirmed red first.

**And a file is not tracked just because one of its calls is.** Image Creator
generates images with OpenAI, and that route posted straight to
`/v1/images/generations` and recorded nothing — while the two text routes
beside it go through a helper that does. So every image it produced was billed
per press and invisible on the usage page. What kept it invisible is the check:
`untracked_openai_modules()` exempted the whole **file** the moment
`from hub import ai` appeared anywhere in it, and that helper is where it
appears — so the module read as fully tracked. The string satisfying the check,
which is the `for_module(` failure one provider over, and
`unmirrored_json_writers()` exempting each scanner because its own prose named
jsonstore.

It is asked per **call site** now, through the AST: a function that reaches an
OpenAI endpoint and names no recorder is a finding whatever the rest of the
file does. `openai_spend_unrecorded()` is lifted out of the walk so it can be
handed a source, and `test_api_usage.py` hands it the shape that was live and
requires it to say so — the file as it stood before the fix reads as
unrecorded, and the same file after it does not. **The model is passed
explicitly** where the image is recorded, because an images response carries no
`usage` block and `openai_cost()` prices anything named `gpt-image*` per image:
without the name there is nothing to price. And a **refused call keeps its
row** with `ok=False` — it spent nothing and is out of every billable total,
but a wall of them is what a spent allowance looks like from this side.

**A provider is not metered in calls just because you counted calls.**
`hub/quotas.py` estimates six providers now, and only three of them bill per
call. ElevenLabs bills the **character** of script, so counting renders makes
a five-second tag and a sixty-second read cost the same — and the long ones
are what spend the plan. Cloudinary bills in **credits**, one of which is a
thousand transformations *or* a gigabyte stored *or* a gigabyte delivered, so
what the Hub uploads is a fraction of the bill and Cloudinary's own
`/usage` meter is read as the authority beside it. Google bills **nothing**
and limits **requests per day**, so a monthly total would never show the
4pm cliff that actually stops a campaign deploy — `google_estimate()` compares
per API and per day, and files each call by URL because one helper in Google
Finder is used against four different APIs. Where a provider publishes no
ceiling worth citing, none is invented: the row says *not measured* and names
the environment variable that would set one.

`quotas.record_tts()`, `record_asset()` and `record_google()` are the call-site
helpers; each is one line and none can raise. An uninstrumented call site is
worse than a missing feature here, because the page keeps reporting a
confident, low number — so `/api/integrity` has a check that names any file
calling one of the three without recording it, and `test_api_usage.py` fails
on the same list.

**Env var names drifted.** This deployment sets `PEXELS_API` and
`PIXABAY_API`; much of the code was written against `..._API_KEY`. Config
accepts both. If a provider reports "no API key set" while the key is clearly
present, that's the cause. The Proposal Builder's Suite push was the worst
case: it read `GHL_PRIVATE_INTEGRATION_TOKEN`, `GHL_LOCATION_ID`,
`GHL_PIPELINE_ID` and `GHL_PIPELINE_STAGE_ID`, none of which this deployment
has ever set, so it reported "env vars not fully set" into a response nobody
reads and never once created an opportunity. `hub/suite_opportunity.py` reads
the real names and discovers the pipeline through the API.

**The spellings are a table now, and three things read it.** `hub/config.py`'s
`ALIASES` is the list, and `_first("A", "B")` argument lists are gone. That is
because the drift check used to *regex those calls out of config's source* —
so the day they became a table it found no groups, reported nothing, and read
as a clean bill of health with every module still drifting. It imports the
table now, `env_report()` renders it, and `test_env_config.py` feeds the check
a file that plainly drifts and requires it to say so, because a check that can
be silenced by an edit somewhere else is worse than no check.

Three rules in it. **Only spellings actually in use** go in: a speculative name
costs nothing to resolve and a great deal to police — adding `OPENAI_KEY`
beside `OPENAI_API_KEY` turned thirteen correct modules into findings about a
variable nobody has ever set, which is how a check gets switched off.
**Prose is not a call site**: three modules explain the drift they no longer
have by quoting `os.environ["PEXELS_API_KEY"]` in a docstring, and the check
reads the **AST** rather than matching text, or it reports the explanation of
the fix as the defect. And **`os.getenv` is the same read** — that spelling is
how `modules/sites_admin` reached `SECRET_KEY` past a check that only knew
`os.environ`, and ran the whole Sites module on `"dev-only-change-me"`.

The check covers `hub/` as well as `modules/` and is **high** severity now, so
CI fails on it. What it cannot see is a *setting* nobody declared, which is why
`env_report()` exists beside it.

**Which name answered is a question nobody could ask.** Accepting every
spelling resolves the key and then makes it impossible to tell which variable
did it — and on a second deployment that matters twice: a variable set under a
name this Hub reads *second* looks exactly like one that took effect, and two
names holding **different** values silently resolve to whichever comes first in
the table, with nothing anywhere reporting that the other is dead. `/diagnostics`
has an **Environment** panel and `/api/environment` the JSON: one row per
setting, every name accepted, the one that answered, and any that were set and
ignored. **No value is ever carried** — it is rendered into a page and pasted
into chats, the rule `services/provider_check.py` already works to. A conflict
also reaches `placeholder_warnings()`, so it shows on the status page beside
the quoted-value and placeholder warnings rather than only where somebody
thought to look.

**`PUBLIC_BASE_URL` is an origin, and one env group here holds a callback URL
in it** — the same string as `GOOGLE_ADS_REDIRECT_URI`, path and all. A
service-level value overrides a linked group's, so this deployment is fine and
the next one to link that group would not be: every share link, landing URL and
Insites scan callback would be built with `/tools/ads/oauth/callback` in the
middle of it and 404 somewhere nobody is watching. A path in it is reported now.

**A redirect URI is an exact string, and half of them carry a hostname
nobody chose.** A custom domain was pointed at this service and Render kept
the `onrender.com` subdomain live beside it, which is the default — so the Hub
answers on two hostnames, and three of its six OAuth flows build their
callback from *whichever one the browser used*: Google Finder and Hub sign-in
through `url_for(_external=True)` and `request.url_root`, QuickBooks through
`request.url_root` unless `QB_REDIRECT_URI` pins it. Google matches that
string exactly, so the day the second hostname existed, half of every
registration was missing and nothing anywhere said so. It fails at a consent
screen, which on the Google Access flow is **in front of a customer**.

The other three do not follow the browser at all — Google Access and the Suite
app are `PUBLIC_BASE_URL + path`, Smart 1 Ads is `GOOGLE_ADS_REDIRECT_URI`,
which is a whole URL and so does not follow `PUBLIC_BASE_URL` either. That
split is the thing worth knowing: somebody who registers the three that broke,
sees staff sign-in start working and stops has left the client-facing one
pointing at the old host, and it will keep working until the day the old host
is switched off. `hub/oauth_redirects.py` reports all six on `/diagnostics`
with the family each belongs to, one line per hostname for the host-derived
ones, and the console each is registered at. It **names the hosts it observed**
rather than implying it surveyed them — an app cannot enumerate its own custom
domains, so a third one added tomorrow is invisible here and the panel says as
much rather than reading as a complete list. A flow whose provider has no
credentials reads *not in use* rather than standing in amber for ever (Smart 1
Ads is parked on a developer token Google has not approved), and no client id
or secret is ever carried — this is pasted into chats, the
`services/provider_check.py` rule. `test_oauth_redirects.py` asserts all of
it, including that every one of the six paths is a route the composed app
actually serves.

**And the panel named a URI the code does not send.** That check asserts what
the panel *prints* and never asked whether the code agrees — which is the one
thing worth asserting about it, because the panel exists to say what string to
paste into a console and a console matches it exactly. `oauth_redirects.py`
trims `PUBLIC_BASE_URL` to its origin (`_origin()`); the two flows that
actually *build* a callback appended to the raw value with only a trailing
slash removed. So with the path this deployment's own linked env group carries
— the same string as `GOOGLE_ADS_REDIRECT_URI`, which the paragraph above
already names — the panel said
`https://smart1.agency/suite/oauth/callback` and `hub/ghl_oauth.py` sent
`https://smart1.agency/tools/ads/oauth/callback/suite/oauth/callback`.
Register what the panel says and consent fails on `redirect_uri_mismatch`;
register what is sent and the panel reports it as wrong. The
`/api/db/structure` versus `/api/integrity` trap, on the one screen whose
whole job is to be copied from — and it hit **google_access** too, which that
same table marks `client_facing` with the note *"a mismatch here fails in
front of them, for a reason that is nothing to do with them."*

`config.public_base_origin()` is the one reading now, and the field
`settings.public_base_url` is the origin as well — which fixes the other
readers that never got the memo, and makes the three modules that had already
worked it out and written their own `_origin()` (`llms_hosting`,
`image_picker/provisioning`, `social_planner/links`) no-ops rather than the
only correct ones. It is read at **call time**, because `settings` is built
once at import and this is the one variable somebody corrects mid-incident
after the panel names it — the reasoning `hub/ghl_oauth.py` already gives for
resolving its scopes per call, applied to the value in the same file that was
not. `modules/google_access/config.py` keeps the name `PUBLIC_BASE_URL`
through a module `__getattr__` rather than editing five call sites, the
`hub/blueprint_guard.py` rule: two of those five build the link a **client**
is emailed, so the sixth reader added next month is right by default.

**Trimmed and still reported**, which is the whole of it: the warning is what
tells somebody to fix the variable, and behaving sanely in the meantime is not
the same as papering over it. `public_base_url_raw` keeps what was actually
set so the report can quote it. The assertion is a **sweep** — every flow
whose source is `PUBLIC_BASE_URL` must declare which code builds its URI, and
one that declares none is a failure rather than a silent skip, so a seventh
flow cannot join by being unasserted.

**Cloudinary is published two ways and this account sets both.** One
`CLOUDINARY_URL`, and the three parts `CLOUDINARY_CLOUD_NAME` /
`CLOUDINARY_API_KEY` / `CLOUDINARY_API_SECRET`. Nine modules configure the SDK
with the three parts and every shared path reads the URL, so a deployment given
only the three-part group had a working Image Creator and a `cloudinary_ready`
of False — `hub/storage.py` silently on the local disk that is wiped on every
redeploy. Config composes the URL when it is absent, and
`export_cloudinary_url()` puts it back into `os.environ`, because
`cloudinary.config()` with no arguments reads it from there and that is how
hub/storage and nine modules configure themselves. It never overwrites an
explicit `CLOUDINARY_URL`.

**A module that is not Python still has to be attributable.** `/api/integrity`
reported `ad_builder` as a module that never logs, and the module logs — under
`display_ads`, from `hub/ad_builder_link.py`, because `modules/ad_builder` is a
TypeScript renderer whose Hub-side half lives in `hub/`. The check looked in
the directory, found one maintenance script, and was half right in the way that
matters least: the Hub-side *joins* logged, and the work itself — rendering a
size set, delivering a pack, approving a proof — passed through
`hub/ad_builder_proxy.py` and was recorded nowhere, which is creative a client
receives with nobody's name on it. The proxy logs every write that returns 2xx
now, so a route added in TypeScript next month cannot be silent: anything not
in `_ACTIONS` is still recorded under its own path. It reads only the status
line, never the body — the response is streamed to the browser and buffering a
multi-megabyte ad pack to learn the client name would cost more than the entry
is worth, so the client stays on the two link-side events that know one. The
log name is declared in `hub/audit.LOG_NAMES` rather than renamed: renaming it
would orphan every entry already written and every Client 360 card reading
them, to make a static check happy about a string. Declaring it is not enough
on its own — the check then looks for that name across `hub/`, and
`test_env_config.py` points it at a name nothing writes and requires the
finding back.

**And a log name nothing else knows is a log nobody reads.** Declaring
`display_ads` closed the integrity check and left the other half open: every
build started and every finished pack filed against a client was written to
the activity log, kept, and then dropped on the way to the record it was
written for, because `hub/client_brand.WORK_KINDS` was keyed on neither
`ad_builder` nor `display_ads` and `work_log()` skips a module it cannot name.
So a client who had just had a set of display ads built read as a client
nobody had done any work for — the tool's own screens complete, the log row
present, the client record confidently empty, and nothing erroring at any of
the three. `test_client_images.py` now asserts that **every name
`audit.LOG_NAMES` declares is one the work log can name**: the declaration
exists precisely because the directory and the log disagree, which makes it
exactly the case where a second table gets keyed on the wrong one.

**That assertion covers one entry, and the failure has now happened five
times.** `LOG_NAMES` holds exactly one mapping, so asserting over it caught
`display_ads` and nothing else. `ad_copy` and `website_audit` were each added
to `WORK_KINDS` later with a note in this file saying *the `display_ads`
failure, one tool later* and *two tools later* — and then **`io_builder`,
`landing_maker`, `stock_photos`, `brand` and `suite`** turned out to be doing
the same thing. The insertion order is the worst of them: `hub/io_clients.py`
exists precisely because a client whose only trace is an IO was invisible on
their own record, so the IO was registering the client and then not appearing
as work for them. Every one of the five was found the same way — somebody
opens a client's record and notices — which is not a way of finding the sixth.

`client_brand.check_work_kinds()` asks the question of **every call site**
instead, through the AST: any `audit.log(…, client=…)` whose module name is
in neither table is a finding, and `/api/integrity` runs it at **high**. It
went in green. Two things it had to get right, both of which caught a first
draft of it. **A bare `log()` is a module's own wrapper** whose first argument
is the *event* rather than the module — Radio Promo and Landing Ads both have
one — and counting those reported four modules as filing work under
`project.create`. And **prose is not a call site**: three modules explain this
very trap by quoting the call, so it reads the AST rather than matching text,
or the check reports the explanation of the fix as the defect —
`tools/spellcheck.py`'s rule, on a different shelf.

**`NOT_WORK` is the other side, written down rather than left as an
absence.** Ten landing modules and `hub/leads.py` log with a `client=` that is
the **prospect's** own business name off a form, and a lead on a client record
would be the Hub inventing a relationship — the distinction `hub/leads.py` and
`hub/prospect.py` are built around. `google_index`, `hub` and `qa` record a
join or a status rather than a deliverable. Each is named with its reason, so
the check can tell *decided to leave out* from *nobody has noticed yet*, which
is the only thing that lets it be green rather than a list somebody re-triages
on every run. `stale_work_exemptions()` fails on an entry naming a module that
no longer logs against a client, because an exemption that outlives its call
site goes on covering whatever is written under that name next.

**And a base read twice is a base with two answers.** `modules/sales_builder`
read `IO_API_BASE` in two places with **different defaults**: `_io_api_base()`
returned the `/tools/io` mount and carries a docstring explaining at length
that the old external default made every conversion call 404 — while
`/api/config` still returned that external default. `/api/config` is the read
that counts, because `index.html` seeds `CFG` with the mount and then assigns
this route's answer over the top. So `/health` reported the mount and looked
healthy while every proposal-to-IO conversion — the order number, both PDFs,
the Suite submit — posted to a **different Render service**: a cold start, a
different login, and "The IO API did not return an order number." in the
conversion log with nothing saying where the request had gone. It is one
reader now, the rule this codebase applies to rate cards, client keys and
gallery labels alike, and `test_io_start.py` fails if the two disagree.

**And a URL inside a module's own helper is one nothing checks either.**
`linkcheck` sees a literal only where it sits directly inside `fetch("…")`, so
`post('/api/seo/checks', body)` is invisible — on the SEO client record alone
that was twenty-seven paths, which is most of what the page does. Four files
declare a **pass-through** helper now and their URLs are resolved like any
other; the count went from 336 verified to 377.

**The helpers are not alike, which is why it is a table and not a list of
names.** `post` hands the URL straight to `fetch` in `seo_client.html` and is
`fetch(BASE + path)` in `ads_estimate.html`; `api` splits the same way between
the Suite panel and the Commercial Builder. Resolving a prefixed helper's
*fragment* as a root-absolute path reports a break that is not there, which is
the crying wolf `UNCHECKED` exists to avoid — so those are declared too, and
counted as unverified rather than left invisible. **Keyed on the file**,
because a bare `post(` also matches `app.post(` and `client.post(`: the first
run of it reported **292** breaks that were route decorators and test clients.

**`sendBeacon` was a request the checker had never seen at all** — this file
already spends a paragraph on how invisible it is, and it is now read like
`fetch`. It also produced the one finding worth keeping: the first run flagged
`test_landing_embeds.py:263`, a **comment** reading *"The bug this section
exists for: `sendBeacon('/api/partial-lead')`"* — the note describing the trap,
reported as the trap. **Prose is not a call site**, for the fifth time in this
file, and a browser call matched in a `.py` file is prose by definition: the
two browser-only patterns are scoped to front-end files. `test_linkcheck_helpers.py`
holds the tables against the helpers they name — an entry whose helper is gone,
or one classified as pass-through that actually prefixes, fails.

**A URL built by concatenation is a URL nothing checks.** `tools/linkcheck.py`
only sees a path literal that sits directly inside `fetch("…")`. Written as
`fetch(BASE + "/api/thing")` it is invisible, which is how three of the
Proposal Builder's AI buttons and all four of its IO-conversion calls came to
point at `IO_API_BASE + "/sales/builder/api/…"` — a path that exists on
neither app — while every page looked healthy. linkcheck now *lists* these
under "built at runtime, NOT verified" rather than guessing at them. Read that
list; prefer a same-origin literal.

**One campaign runs in more than one place.** Target areas are a list, in
`hub/target_areas.py`, shared by the Proposal Builder, the IO Builder and
`CampaignSpec`. The old single-geo fields (`geoType`/`geo`/`radius`, and the
IO's `geoOrigin`/`geoRadius`/`geoDMA`/`geoZipcodes`) are still written on every
save, because the IO PDF and the Suite webhook's `geographic_target` read them
— `from_legacy()` converts old records on read rather than migrating rows
nobody has re-opened. The wizard carries a JavaScript copy of the label and
sizing helpers so the reach panel updates live; `test_target_areas.py` asserts
the two produce identical output, because when they drift the proposal
contradicts the screen it was quoted from and nothing errors.

**A granted scope list is not the scope list you asked for.** HighLevel grants
the scopes it recognises at consent and says *nothing* about the rest — no
error, no warning. So a Marketplace app consented with half its scopes hands
back a perfectly healthy token, `status()` reports **Connected**, and every
feature behind a missing scope 401s months later looking exactly like a bad
token. The Suite panel used to print the granted list verbatim, which reads as
confirmation and is nothing of the kind: eight scopes look identical whether you
asked for eight or twenty. `hub/ghl_scopes.py` holds the set as data and
`compare()` diffs granted against requested, naming the **feature** each gap
costs rather than the string — and separating a scope we have authenticated with
before (a permission to grant) from one we have never confirmed (probably our
typo), because sending someone to re-consent over a misspelling wastes the one
manual step the whole app exists to stop repeating. A scope left out on purpose
is in `NOT_REQUESTED` with its reason, so a 401 is never ambiguous between an
oversight and a decision. And because a location token inherits only what the
agency token was granted, a scope missed at install is missed for every client
until somebody re-consents — which is why the write scopes are requested now
rather than when a feature turns out to need them. `test_ghl_scopes.py` and `/api/integrity` assert
that every GHL write call site in the Hub has a scope declared for it — by **walking the tree**, not by re-reading a list. The hand-written version could only re-confirm what somebody had already thought of, and two sites slipped past it within months: `hub/qa.py` grew an opportunity-status write, and the Social Planner's posting moved from `app.py` into `suite_client.py` while the table went on naming `app.py`. The invariant is deliberately the weak one — the file must be named by *some* scope, not that the right scope was picked, because inferring a scope from an endpoint is where false positives live and a check people ignore is worse than none. A file that reaches the API but needs no scope goes in `WRITE_EXEMPT` with its reason.

**A `SameSite=Lax` cookie is not sent into somebody else's iframe.** Which is
the whole difficulty of putting a Hub page inside Smart 1 Suite: the rep is
signed in, the browser declines to say so, `AuthGuard` redirects, and a login
form appears inside Suite for an account they already hold. Nothing errors.
Relaxing `s1hub_auth` to `SameSite=None` would fix it and would also attach
that cookie to every cross-site request, including a POST from a page an
attacker controls — and this Hub has delete buttons behind it. So `hub/suite_embed.py`
adds a **companion** cookie carrying the same signed value, accepted only for
**GET/HEAD** and only on an explicit path **allowlist**, which makes it useless
for that attack and makes an embedded page **read-only**. That last part is a
consequence to state, not to discover: a write-heavy tool added to `EMBEDDABLE`
would load, look complete and fail on save. The client-facing version cannot use
any of this — a client has no Hub session at all — and needs HighLevel's SSO
handshake instead; `SSO_NOT_BUILT` says what that involves and why the location
id in it is the entire security model.

**And a correct refusal, on the one screen everybody sees first, reads as a
broken app.** HighLevel frames whatever URL the app is configured with, the
Getting Started tab was pointed at the Hub root, and `/` is the staff dashboard
— so the allowlist refused it and the tab filled with *"This Hub page is not
available inside Smart 1 Suite."* Every layer behaved exactly as designed:
`framable()` refused a page that must not be framed, `refuse()` named the path
rather than going blank, and the app was installed in twelve sub-accounts with
its front door showing an error. **The app simply had no page to point that tab
at** — `/client360` is a client record and `/suite-app` is the client SSO
handshake, and neither answers *what is this and how do I use it*.

`/suite-app/start` is that page, and the most useful thing on it is the **two
menu-link URLs**, because a link aimed at the wrong path is precisely how
somebody meets that refusal next. It prints them from
`config.public_base_origin()` read at call time rather than from a typed
hostname — the `hub/oauth_redirects.py` rule, on another screen whose whole job
is to be copied from.

**A third route under a prefix whose docstring says two are deliberately the
whole of it**, so the reason it does not widen that rule is written down rather
than left to be re-derived: what those two routes are protecting against is
*somewhere a client could be shown another client's record*, and this page
reads nothing and renders nothing belonging to anybody. It is outside the login
for the same reason — the reader is an agency admin who may have no Hub account
in that browser, and a sign-in form in the getting-started tab teaches them the
app needs one.

**And it opened by recommending the half we had not switched on.** The page
listed the staff record and the client SSO frame side by side as two menu links
to add "whichever you need" — on the one screen whose whole job is to be copied
from, which is the `hub/oauth_redirects.py` failure exactly: a panel printing a
string nobody should paste. The client surface needs each client's sub-account
recorded against them first, and without that a client who opens it is told we
cannot tell whose account they are in — correct, and reading to them as broken.

`client_for_location()` has **exactly one caller**, `hub/suite_sso.py`, so that
link is load-bearing for nothing: not adding it costs a shortcut to content
those clients are already emailed, and removes the one surface where getting a
sub-account wrong shows somebody another client's record. It stays *documented*
rather than deleted — an absent option reads as one nobody thought of, and the
route is discoverable from the code either way — but it is drawn as **not
switched on**, with what has to be true before it is. `test_suite_embed.py`
asserts the distinction, because the old copy read perfectly well and every URL
on it was correct: nothing but an assertion separates documented from
recommended.

**Two of the first assertions written for it could not fail.** The frame header
rides on the `/suite-app` prefix, so a **404** at that path carries it too —
"it may be framed" passed with the route deleted. And `PUBLIC_BASE_URL` is
unset under test, so `public_base_origin()` is `""` and `bytes.count(b"")` is
always `>= 2`: the one assertion about the page printing a copyable origin was
vacuous. Both were found by deleting the route and requiring red, which turned
up four failures where there should have been six. The test sets a real origin
now and pins the header check to a 200.

Three things had to move for it, each its own quiet failure. `HubBar` already
skips the sidebar for an iframe, but the **hub app's own `after_request` did
not** — and Client 360, the page most worth embedding, is a hub route. Nor was
suppressing the injector enough: `base.html` calls the `hub_sidebar` global
**directly**, so the page rendered its own sidebar and the injector, which skips
a body that already has one, agreed there was nothing to do. And no
`X-Frame-Options` or CSP was set on hub pages at all, so adding an embed path
without `framable()` would have widened a clickjacking oversight into a feature.
Flask runs `after_request` handlers in **reverse** registration order, so the
chrome check asks `suite_embed.is_embedded()` itself rather than reading a flag the
policy handler sets — which would still be unset. `test_suite_embed.py` asserts
all of it.

**A client inside their own Suite has no Hub account, and must never be given
one.** `hub/suite_embed.py` solved the staff half with a companion cookie; its
own closing note said the client half needs HighLevel's SSO handshake.
`hub/suite_sso.py` is that half. The framed page asks its parent for the user
payload, HighLevel replies encrypted under the app's **SSO key** — a *third*
credential, separate from `GHL_CLIENT_ID` and `GHL_CLIENT_SECRET` — and the
Hub decrypts it server-side.

**The location id in that payload is the authorization, and it is the whole
security model.** Everything else in it — `userId`, `email`, `role` — is
carried for the audit line and is never joined on. Identity comes from the
sub-account, resolved to a client by `suite_accounts.client_for_location()`,
and from nothing else: the session route accepts no client name at all, so a
request that simply names one is refused as unreadable. Getting this wrong
shows one client another client's record, which is the worst outcome any tool
in this Hub can produce, and it is silent — the frame renders, the page looks
right, and it is the wrong client's data.

Five refusals, each its own state because each sends a different person
somewhere different. **No key is `not_configured` and never a lenient
session** — the tempting failure is treating an unverifiable frame as trusted
because it looks like it came from HighLevel, and a frame is a URL anybody can
point at us. **A payload that will not decrypt is `unreadable`, and a wrong
key, a tampered payload and a truncated one all give the identical answer** —
anything finer tells whoever is probing which guess was closer. **A payload
naming no sub-account is `no_location`**, never a fall-through to the first
client. **A sub-account no client records is `unknown_location`**, a setup gap
that says where it is fixed. And **a sub-account two clients claim is
`ambiguous`, named and refused** — picking between them is picking whose
record a stranger sees.

**And "identical" was true 199 times in 200.** The padding branch carried a
comment saying a wrong key *almost* always lands there, and the almost was the
whole finding: AES-CBC under the wrong key produces garbage, garbage ends in
bytes that are valid PKCS#7 padding about **0.6%** of the time, and those fell
through to `json.loads` and answered *"Payload is not JSON."* instead — the
finer answer the function exists not to give, since it tells a prober their
guess produced valid padding. Every refusal decided by bytes the key produced
says one thing now; the two structural ones before it (not base64, not the
envelope) stay distinct, because anybody can tell those about their own payload
without holding a key. The cause still rides the exception chain, so a genuine
HighLevel fault is diagnosable from a traceback — it is the answer *handed back
to the frame* that is one word.

**A property asserted once is a property asserted 0.6% of the time.**
`test_suite_sso.py` compared a single wrong key against a single tampered
payload, so it passed on 199 runs in 200 while the leak stood, and on the
two-hundredth it failed in CI reading exactly like a flake somebody re-runs —
which is how a real finding gets a re-run instead of a fix. It sweeps six
hundred distinct wrong keys now, which puts the odds of missing it below one in
10^15 and costs a tenth of a second, and the failure names the **count**:
"3 of 600" is a leak and "600 of 600" is a broken decrypt, and those are fixed
in different places.

**The client-facing surface is deliberately two routes and no more.** They
prove who is looking and then hand the client their *existing* content link;
the pages behind it are already client-facing, already scoped to one client
and already tested. The smallest way to build somewhere a client could be
shown the wrong record is not to build one.

**The path is `/suite-app`, not `/suite/app`.** `/suite` is a
dispatcher-mounted module, so a hub route under it never receives the
request — and it does not even 404, it is swallowed by the module and
redirects to a *staff login*, which a client would meet as a sign-in form for
an account they will never have. That is the first trap this file names, it
has now bitten four times, and `/api/integrity`'s high-severity check is what
caught this one before it shipped. `test_suite_sso.py` asserts the mount does
not serve the frame.

**The crypto is transcribed, and round-tripped rather than trusted.**
HighLevel encrypts with CryptoJS's `AES.encrypt`, which is OpenSSL's
`Salted__` envelope — AES-256-CBC with the key and IV from EVP_BytesToKey over
MD5. Nothing here has ever seen a live HighLevel payload, so
`test_suite_sso.py` carries its own independent encryptor and round-trips
against it: if the derivation drifts from the spec, the test stops passing.
MD5 appears only inside that derivation, where the format specifies it.

**`linkcheck` sees `fetch("…")` and nothing else, and `sendBeacon` is where
the leads were.** The trap above is about the URL it cannot verify; this is
about the call it cannot see at all. Six landing modules posted their
abandoned-form partial lead with
`navigator.sendBeacon('/api/partial-lead', …)` — root-absolute, so under
`/land/<x>` it leaves the module entirely and 404s on the hub app. It is
invisible three times over: the `fetch()` written beside it as the fallback
carried the mount and worked, `sendBeacon` returns a boolean nobody reads, and
it fires on `pagehide`, so no console is open when it fails. Stadium was worse
— its browser code named a *different Render service* as its API base, so its
leads were reaching neither this Hub nor nothing, which is harder to notice
than either. Use `{{ request.script_root }}` in a template; in a plain asset
with no Jinja, either a relative path (`'api/partial-lead'`, which resolves
against the document's directory) or a base derived from `location.pathname`.
`test_landing_embeds.py` fails on a root-absolute path passed *directly* to
`fetch` or `sendBeacon` — not on one appearing anywhere in the file, because
`apiUrl("/api/health")` is correct and a check that flags it teaches people to
ignore the check.

**Retiring a route is not done until nothing falls back to it.** `hub/leads.py`
retired the inbound Suite webhook and said so at length — and six landing
modules kept a POST to it one call level up, reached when
`capture_and_deliver` raised, while four of them (boat, legal, ski, recruit)
sent their abandoned-form partial lead *straight* there and through the panel
never. So the lead panel's own warning could say the remaining risk was
"outside the Hub" while the Hub was the risk. Both halves are invisible from
either end. The fallback fires precisely when a fallback must not — a timeout,
where the API write may well have landed — so it writes the **second contact**
the single route exists to prevent; and the partial goes out on `pagehide` by
`sendBeacon`, which returns a boolean nobody reads, so those leads were absent
from the panel while the trigger was live and would have gone on being absent,
behind a 200, once it was off. **Switching the trigger off in Suite is what
converts a duplicate into a silent hole**, which is why the code had to be
finished first and not after. Every landing lead and every partial goes
through `hub/leads.py` now, the calculators' shared `CALCULATORS_LEAD_WEBHOOK_URL`
fallback is gone with them (the per-calculator `CALC_WEBHOOK_…` override
stays: it is an explicit opt-out the page names), and `test_lead_delivery.py`
reads the module sources for both patterns — with an allowlist naming the
files that may mention such a variable and why, so the check did not start
life red. `GHL_WEBHOOK_URL` is on it: the IO Builder posts **insertion
orders** down that one, which is a different workflow, and it refuses by name
when unset instead of returning a quiet 200. Check the two do not hold the
same URL before switching a trigger off, or the thing that stops is insertion
orders.

**The queue that is the safety net for all of it was drained by nobody.**
`retry_undelivered()` has said *"Called by hand or the scheduler"* since the
day it was written, and **there was no such scheduler job** — the only thing
that drained it was a rep pressing *Retry undelivered* on the lead panel,
which is a button nobody has a reason to look at on a morning when nothing
looks wrong. So the whole design above — store first, deliver second, never
destroy a lead we already have — ended in a queue whose second half was
optional. Every landing page, every calculator, every scan widget and all
five standalone Render apps write down this one path, so what was owed was
owed across the Hub rather than in one tool. The declared-and-never-wired
failure this file counts seven of, on the safety net rather than on a
feature.

`job_retry_leads` runs hourly. Three rules on it, none of them new here.
**A skip is a state and a failure is an event** — an unconfigured Hub would
otherwise write an identical row into the activity log every hour for ever
with the real failures sitting in the middle of them, which is the noise
`hub/google_index.py` had to learn to stop making, so delivery being
unconfigured returns `skipped` before anything is attempted. **It is bounded
on both axes and says what it did not reach**: fifty calls and a four-minute
wall clock, because every retry is an HTTP call to GoHighLevel with no
ceiling of its own and scheduler jobs share one thread — and `left` is
counted and printed, since a queue that stops part-way and says nothing reads
exactly like one that is drained, which on this queue means concluding every
lead is in Smart 1 Suite when a hundred are not. The budget is checked
**after** a call rather than before it, or a deployment whose provider is
merely slow delivers nothing at all, for ever. And **it sits ahead of the
slow provider sweeps in `JOBS`**, which is insertion order and load-bearing:
`_loop` runs every due job synchronously on one thread, `google_index`
routinely spends twenty minutes on rate-limited GTM calls, and this is the
one job in the list whose starvation means a client's lead sits undelivered.
It does not get the cheap-and-local argument the two QA jobs get — it is a
network sweep — what it has instead is a hard ceiling, so what it can cost
everything behind it is bounded and what starving it costs is not.

**And wiring it would have shipped a worse bug than it fixed.** The lead
store is a JSONL file that is **appended** on capture — which is what makes
the public endpoint cheap — and rewritten whole by three callers that read
the file, change something in it and write the lot back. That is the
read-modify-write `hub/jsonstore.py` documents at length, and here what goes
missing is a **lead**: a visitor fills in a landing page on worker B while
worker A is part-way through a sweep, B appends the row, A's `os.replace`
lands a file read before that append, and the lead is gone — atomically,
silently, with a 200 already in front of the visitor. The lock in front of it
was a `threading.Lock`, which serialises the threads inside one worker and
says nothing whatever about the other one, and threads cannot show that
failure: it takes two real processes. Measured with two, appending against a
sweeping one, **30 of 60 leads survived**. It was survivable only because the
sole rewrite was a press nobody made; an hourly job turns it from unlikely
into a matter of traffic.

So `_rewrite` holds `jsonstore.exclusive()` — the same two locks, the thread
one and the `flock` on a sidecar, rather than a second implementation of
them — and re-reads the file **inside** the lock, keeping any row the caller
has never seen. That is safe here rather than generally because **this store
never deletes**: merging keeps the absorbed row, converting marks it, and
this module opens by saying a lead we already have is never destroyed. A row
the caller does not know about is therefore one that arrived while they were
working, and the only correct thing to do with it is let it survive. The
append takes the same lock, or it is serialised against other appends and not
against the rewrite, which is the half that was missing. `_exclusive` in
`hub/jsonstore.py` is public as `exclusive()` for it. 60 of 60 now, and
`test_lead_delivery.py` drives the real helper rather than reading the source
for the word *lock*: prose naming a lock is not a lock being taken.

**And five landing apps outside this repo were still on the webhook the Hub
retired.** `smart1boat`, `smart1legal`, `smart1ski`, `smarthvac` and
`smart1rv` are their own Render services, so every rule above was written
about modules the Hub can see and none of it reached them: each POSTed its
leads at a GoHighLevel inbound webhook, which confirms that GoHighLevel
accepted a *request* and never that a contact exists — the weak confirmation
`hub/ghl_contacts.py` exists to replace. smart1rv is the one that shows what
that costs: `SMART1_SUITE_WEBHOOK_URL` was never set on it, so **every lead
that app has ever taken was discarded**, behind an `ok: true` already on its
way to the visitor.

They post to `/api/leads/capture` now, and the Hub writes the contact. Routing
them through here rather than giving each one the Contacts API is the same
argument this file makes about `hub/storage.py`: the token and the sub-account
id stay in one place rather than on five more services, the retry machinery
already exists, and these leads are finally *in the panel* — they were
invisible to it, which is where anybody actually looks for a lead.

**A rate limit written for a browser is wrong for a server.** Three an hour
per address is generous for a person; every lead one of those apps takes
arrives from that app's single Render egress address, so a busy afternoon on
one page spends the whole allowance and the fourth visitor is refused with a
429 while they see their report and nothing on any screen says a lead was
turned away — the rate limit doing its job and losing leads doing it. A caller
presenting `LEADS_SOURCE_TOKEN` skips the per-IP check and **nothing else**:
the lead is stored, tagged and delivered down the same single path, and the
row records that it came from a trusted caller, because an exemption nobody
can see afterwards is one nobody can audit. Unset means **nobody** is trusted,
never everybody — a deployment that has not set it behaves exactly as it did,
which is the safe direction to be wrong in. It is reported on the panel as a
fact rather than a warning: a deployment with no standalone landing apps needs
no token, and a permanently amber row is the check people learn to skip.

**And `meta` was the one part of a lead row that was never cleaned.** `fields`
has always been cleaned per value; `meta` was written to the store verbatim
and unbounded, which mattered less while it was a Hub page's own dict and
matters now that five apps fill it in over an unauthenticated endpoint. It is
cleaned and capped like a field, with two exceptions that are exceptions on
purpose: a boolean stays a boolean (`trusted_source` is a real flag), and
`tags` stays a list, because `lead_tags.extra_tags()` reads it as one.

**Those tags are how segmentation survived the webhook's retirement.** boat,
ski and hvac drove GoHighLevel "Add Tag" workflow actions off `market_tag` and
`package_tag` in the webhook body; over the API a tag is a field on the
contact, so they are written directly and there is no workflow in the middle
to go missing. They are **free**, like the page tag and unlike the source tag,
because what a vocabulary protects — a workflow triggering on the string — is
not what these are for. What they may not do is impersonate the controlled
half: `meta` is the part of the payload a landing app fills in, so a row
naming `smart1-hub` or naming a source would put a lead into a triggered
audience it was never captured for, and that is a mistake to make impossible
rather than one to ask people not to make. The cap drops segmentation before
it drops the source tag, for the same reason.

**Three answers from the Hub, and a fourth that is not an answer at all.**
Each app records what came back, and they are separate states because they
need opposite handling. **delivered** is a contact id — the only thing that
means the lead is in Smart 1 Suite. **accepted** is the Hub having stored it
and taken over the retry: done as far as that app is concerned, and still not
a contact, so it is counted apart rather than merged; replaying one from the
app writes a second lead row for one visitor. **undeliverable** is a lead with
neither an email nor a phone — an abandoned form that never reached the
contact step, which smart1legal's and smart1ski's partial-lead routes produce
by design. It is deliberately not a failure: left as one it would sit in the
owed count for ever and be re-posted on every replay run, which is the
permanent red this file names elsewhere. And **failed** is owed, and replayed.
A `delivered: true` carrying no contact id is read as *accepted* — the safe
reading of a self-contradictory answer — and the contradiction is said out
loud rather than quietly rounded down to a state that looks routine.

**There is no fallback to the webhook, and that is the same rule one service
out.** The tempting version is: if the Hub is unreachable, fire the old URL
instead. The failure that matters most is a timeout, which is precisely the
case where nothing can tell whether the write landed — falling back there is
how one visitor becomes two contacts with nothing to reconcile them against.
Each app still *reads* its old webhook variable, for one reason: to say on
`/health` that a value is still sitting there. Nothing in any of the five
posts to it, so what could still write a second contact is outside all six
codebases — a Suite workflow triggered by that URL, or a page posting straight
at it. Turn the trigger off in Suite, then clear the variable.

**The marketing site's form and the Hub's form were two different forms.**
Nine industry tools live here (`/land/boat`, `/land/ski`, `/land/stadium`, …),
each of which writes every lead through `hub/leads.py` into Smart 1 Suite.
smart1marketing.com carried its own form on each of those pages, connected to
none of it — and `/api/leads/capture` sends no CORS headers, so a browser on
that domain could not have reached the Hub even if it tried. Nothing errored on
either side; the Leads panel simply reported a fraction of the prospects as if
it were all of them. `hub/embed.py` frames the real tool rather than shipping a
copy of the form, which is the part that matters: a pasted copy needs a host
spelled correctly (`smart1-multipart-embed.html` shipped
`https://YOUR-RENDER-APP.onrender.com`), needs its mount prefix concatenated
correctly, and goes stale the day a field is added here. A frame needs none of
those. `docs/smart1marketing-embeds.md` is the line to paste, per page.
`/embed` deliberately has no trailing slash — tourism's relative API path
resolves against the *directory* of the current URL, so `/embed/` is a 404 the
prospect does not meet until they press submit.

**Every rule above was written for a dispatcher-mounted module, and the
calculators are a blueprint.** Five marketing-site pages frame a Hub media
calculator — `/ims` frames the IMS Advertising Trade Calculator, and
`/ctv-ott-calculator`, `/digital-audio-calculator` and `/dooh-calculator` frame
the other three. `modules/calculators` registers on the hub app, so it passes
through neither `bare_prefixes` in `wsgi.py` nor `hub/embed.py`'s `install()`,
and **both halves failed at once**.

`suite_embed.is_embedded()` reads `Sec-Fetch-Dest`, which a browser sends
whoever owns the outer page — so it is true for the marketing site exactly as
it is for Smart 1 Suite. The hub app's `_embed_policy` then refused every
calculator path as not being in `EMBEDDABLE`, and a prospect on
smart1marketing.com/ims got **"This Hub page is not available inside Smart 1
Suite." in plain text, 403**, where the calculator should have been. The same
403 answered the `/api/<slug>/estimate` POST, so a frame that did render could
not compute either. Nothing errored at either end: the Hub was answering
correctly, to a question nobody had asked it. And `/tools/calculators/` was not
in `CHROMELESS`, so `/c/<slug>` — the standalone link an ad can point at —
arrived carrying the staff sidebar, live links to `/client360`, `/sales/leads`
and `/qa` among them, plus the help layer and the feedback tab.

`_embed_policy` now answers a **public prefix of the hub app's own** with
`hub/embed.py`'s marketing-site allowlist, checked *before* the Suite refusal so
that refusal can never reach a prospect, and `CHROMELESS` extends itself from
the same list. The list is read from `modules.calculators.public_paths()`
rather than restated — the rule `modules/ads_builder` gives `wsgi.py`: the mount
and the module must not be able to disagree about what is public. Nothing else
we run catches this. linkcheck resolves the URL, the template is valid, and the
page returns 200 to a member of staff opening it in a tab; it breaks only for
the one visitor it exists for. `test_calculator_embeds.py` asserts every half,
and asserts the staff index and leads pages keep their chrome — a prefix wide
enough to fix this goes wrong in the other direction just as quietly.

**A page on the marketing site is not a calculator in the Hub.**
`/paid-search-calculator` is live and there is no paid-search calculator here;
`female-18-34` is a working calculator with no page. `test_calculator_embeds.py`
names both as known absences rather than leaving them implicit, so building one
makes the assertion the reminder to point the page at it.

**A capped global read, filtered in Python, silently drops the OLDEST rows —
and drops them from one client at a time.** Every campaign-map reading in
`modules/reports` used to go through `store.mapped_campaigns(limit=N)`, which
orders by `mapped_at` descending and truncates. Callers then filtered that
list: `[m for m in mapped_campaigns(limit=10000) if m["client"] == c]` on the
pacing board, in `client_card.summary`, in `v2_tools.client_performance`; `if
(platform, account_id, campaign_id) in touched` on the CSV upload's cache
clear and in `normalize._log_clients`; `if m["pending"]` in
`pending_mappings`. Every one of them is correct until the map table passes
N, and then wrong about the longest-standing client only, which is the client
whose numbers somebody has been reading for a year.

It goes wrong asymmetrically, which is what made it worth a file of its own.
`facts_for` queries **by client**, so the spend keeps coming back; only the
campaign list truncates. The money reads right and the campaign count reads
zero. On the pacing board that combination is `band='unmapped'`,
`actual_to_date=0.00` for a funded, spending line — printed on a page a
client's rep reads, with nothing logged and nothing raised.
`pending_mappings` had the same shape one layer up: a confirmation queue
whose oldest items fall off it as the book grows, while `pending_count()`,
which counts in SQL, goes on reporting them.

The fix is not a bigger cap. `store.campaign_maps_for(clients)` filters on the
indexed `CampaignMap.client` in the database; `store.campaign_map(platform,
account_id, campaign_id)` is a primary-key `db.get`; `store.campaign_maps_by_key(keys)`
queries the touched keys in chunks; `pending_mappings` filters on
`confirmed_at IS NULL` in SQL. `mapped_campaigns(limit=N)` stays, with a
docstring saying never to filter it by client — it is the bounded read for the
recent-activity list on `/reports/mappings`, which genuinely wants the newest N.

`test_reports_map_reads.py` reproduces the truncation rather than asserting it
from the source, asserts the pacing band that follows from it, and holds the
guard that matters: it replaces `store.mapped_campaigns` with a **counting
spy** and asserts no filtering reader reaches it. A spy rather than a raise
because `pacing._overlay_pending` catches every exception on purpose — a guard
that raised would be swallowed there and the test would pass on the broken
code.

**The same cap sat on the budget book, where the consequence is absence
rather than a wrong number.** `store.budget_lines(limit=N)` orders by
`created_at` descending and truncates, and `pacing.compute`,
`client_card.summary`, `client_card._filed`, `v2_tools.client_performance`,
`budget_lines_for` and `budget_lines_named` all filtered its result in Python.
A budget line is what *puts* a row on the pacing board, so a truncated line
does not pace wrongly — it is **not on the board at all**, and a line nobody
sees is a line nobody paces. Reproduced: with the cap reached, the oldest
client's sold, funded, spending Streaming TV line vanished from
`pacing.compute` entirely while every newer filler line stayed.

Two counts went with it. `/reports` printed
`len(store.budget_lines(limit=1000))` in the "Budget lines" tile, so past 1000
the tile would have read `1000` forever; `/reports/budgets` printed
`rows|length` as its heading, which past the page size is the *page size*
printed as the book. Both are the house rule — never print a figure this Hub
did not measure — and both now go through `store.budget_line_count()`, counted
in SQL, with the budgets page saying "Showing the newest N" when it is showing
fewer than the total.

`store.budget_lines_for(clients, active_only=)` filters in the database for one
key or many, `all_budget_lines(active_only=)` is the uncapped read for the four
callers that genuinely need every line, and `budget_lines(limit=N)` stays for
the page that pages. `_active()` writes the active filter once, and it treats a
**NULL** status as active: a row written before that column existed is active
because nothing else could have written a status then, and `status == 'active'`
alone would have dropped every line filed before that migration — a second
silent-absence bug inside the fix for the first. `test_reports_map_reads.py`
asserts that case directly.

**And a third time on the quarantine queue, where the cap drops the days that
have been missing longest.** `quarantine.held(limit=N)` orders by `date`
descending, so `held_for_client` — "the days missing from their page, which is
the thing the staff page should say" — kept the members of a capped list and
under-reported exactly the oldest ones. Its default limit was **500**, not
5000. `reconcile._held_in` was worse in kind: `sum(1 for h in held(limit=5000)
if ...)`, a count over a capped read, printed beside a platform's own monthly
total on the reconcile screen — the one place an under-count reads as
*agreement* rather than as a gap. `held_for_keys(keys)` filters in the
database and `held_count(platform, start, end)` counts in SQL; `held(limit=N)`
stays for the queue screen with the same docstring warning.

Three tables, three screens, one shape. If you are about to write
`[x for x in some_read(limit=N) if ...]` or `len(some_read(limit=N))`, the
question is not whether N is big enough — it is whether the database can do the
filtering or the counting, and it nearly always can.
