# Smart 1 Hub

Internal tool suite for Smart 1 Marketing. Flask, deployed on Render via
Docker, ~22 modules mounted under one login.

**Live:** https://smart1.agency · **Repo:** `Smart-1-Marketing/smarthub`

---

## Architecture in one page

`wsgi.py` is the entry point. It builds the hub app and mounts every module
through `DispatcherMiddleware`, wrapped in `AuthGuard` (login) and `HubBar`
(injects the sidebar, help layer and breadcrumbs into every response).

Modules attach one of two ways, and the difference matters:

1. **Dispatcher-mounted** — a standalone Flask app under a URL prefix
   (`/scans`, `/tools/seo-images`). Has its own Jinja environment.
2. **Blueprint-registered** — registered on the hub app itself
   (`modules/tickets`, `calculators`, `image_picker`, `page_image_optimizer`,
   `google_access`).

Shared services live in `hub/`: `config.py` (typed settings), `storage.py`
(Cloudinary), `ai.py` (OpenAI + cost tracking), `images.py`, `audit.py`
(activity log), `extensions.py` (the shared SQLAlchemy instance),
`jsonstore.py` (JSON on the disk, mirrored to the database),
`scheduler.py` (background jobs).

---

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
template**, across eighteen of the twenty-eight scenarios. That is a
**backlog, not a regression**, and it is deliberately not an integrity
finding: a check switched on red is a check somebody turns off, and it would
take the bubble check down with it. `help_audit.demo_targets()` gathers it and
the **help layer** panel on `/diagnostics` lists it, so the scenarios written
against a screen that has since been rebuilt are a list somebody works down
rather than something a learner meets one step at a time.

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

Placing the rest is separate work and needs whoever knows each tool.

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

---

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

## Wiring four call sites is not wiring the module

`/api/integrity`'s silent-module check reads a **call** now rather than an
import, and the seven modules that had bound `hub.audit` and never used it
were fixed. That sweep wired a handful of call sites per module and stopped,
and nothing could see the remainder — because **the check one level up is
satisfied by one call site.** It asks whether a module logs *at all*, which is
the same shape as the check that read the string `for_module(` and counted the
binding. A module can be loudly attributable about a quarter of its work and
pass.

**Two modules were found doing exactly that, and in both the creating half of
a create/destroy pair was the half left out.**

`modules/sites_admin` recorded `delete_website` and not `add_site`, which
makes a client's website and can activate a paid plan — so the record showed
sites being deleted by somebody and appearing from nowhere. Nor
`personalization`, which writes brand colors onto their live pages, nor
`pricing`, which sets `client_price` **and** `internal_client_name` — the join
`hub/domain_links.py` writes and every domain-keyed report reads, so changing
it moves a website onto a different client's record. Nor `project_sso`, which
is not a change to the site but is the door the changes are made through.

`modules/google_finder` recorded `disconnect` and not `oauth_callback`, which
is the moment the Hub *gains* a refresh token for somebody else's Google
account — the grant every write in that module is made under. And it recorded
`gtm_deploy_event` while `gtm_deploy_pixel` went unrecorded: arbitrary code,
in a container we do not own, which is the very action this file already names
as one of the least attributable in the Hub. `api_gsc_bulk_add` writes
properties into their Search Console and was silent too.

**One walk, read by both.** `audit.write_route_attribution()` is the question
asked one level finer, and it lives beside the log rather than being copied
per module: two readings of one question drift the day either is edited, the
failure `_client_log_modules()` already had to undo. It reads the **AST**,
because both modules name `_audit` in comments explaining why it had gone
uncalled and a check matching text reports the fix as the defect; it resolves
a module's own `log()` wrapper, the shape `check_work_kinds()` had to learn;
and it is handed a silent route and required to name it.

**And a wrapper is resolved from its definition, not guessed from its name.**
The walk hard-coded `_audit` and `log`, which was two modules' spelling and not
a rule. `modules/seo_images` calls its wrapper `_log`, so a module recording
five of its seven writes read as recording **none** — a check inventing seven
findings, which is switched off faster than one that misses them. Worse, the
same blindness had already produced a **wrong exemption**: Google Finder's
`api_ga4_ask` logs through `_audit_mod.log(...)`, the walk could not see a
caller of that name, and it was duly declared as a route that records nothing.
An exemption covering a call site that never needed one. A wrapper is any
function in the file that itself reaches the shared logger, however spelled,
and the both-directions rule is what caught the stale entry.

**A route that writes without a write method is named rather than missed.**
Google redirects the browser to `oauth_callback`, so it is a `GET` by protocol
and a method-based walk cannot classify it — while what it does is store a
credential. It is asserted by name, because the one thing worse than a walk
that misses a route is a walk that misses it silently.

**`HOUSEKEEPING_ROUTES` is the other side**, per module, each entry with its
reason: the reads, the imports of our own tables, and GA4's `runReport`, which
is a POST that reads. Held in both directions, so an entry naming a route that
is gone — or one that has since started logging — fails.

**And every one of the new calls logs after the provider answered**, inside
the try, the shape `project_action`'s own comment already describes and
`approve_render` uses in the Commercial Builder: a change Simvoly or Google
refused is not written down as one that was made.

**What is deliberately not here is a repo-wide gate.** The same walk over
every module that logs finds about **229 silent write routes across 34
files**, and the great majority are genuinely housekeeping — autosaves,
drafts, previews, and POSTs that read. A check landing with 229 findings
nobody can act on is the one people learn to skip, which is the note
`help_audit.demo_targets()` already makes about the walkthrough backlog. The
modules that have been triaged declare their remainder and are held to it; the
rest is a list somebody works down, module by module.

**And the walk stopped one level short of its own stated rule.** Its comment
says a wrapper is *"a function in this file that itself reaches the shared
logger, however it is spelled"* — and the code counted a function calling
`audit.log(...)` by **attribute** and stopped there. A module that binds
`_cb_log = audit.for_module(...)` and then wraps *that* in a helper had every
route calling the helper reported silent. Four read that way, all four
recording their work perfectly well: the Commercial Builder's `submit_render`,
`send_for_review` and `client_decide`, and `image_audit.api_image_attach_many`,
which is the bulk attach that files orphaned images onto a client. That is a
check **inventing** findings rather than missing them — the failure the
paragraph above already names once about `_audit` and `log` being hard-coded —
and it is worse here than a gap, because the whole point of the walk is to let
a module be triaged: a triage built on that answer declares a **logging** route
as housekeeping, and the declaration is then held in both directions against a
lie. The set is closed transitively now, and it terminates because a pass that
adds nothing stops it.

## Deleting a client destroyed four tables and recorded none of it

`modules/commercial_builder` was the module the walk had been quietest about,
and triaging it found the same shape both earlier triages found, twice over:
it recorded **four** of its forty-three write routes, and the creating *and*
destroying halves of both its create/destroy pairs were among the thirty-nine.
A brand profile a rep spent an afternoon on appeared from nowhere and left the
same way.

**What the destroying half destroyed was not one row, and half of it did not
go.** `Client.projects` cascades and `CommercialProject` cascades to its scenes
and render jobs, so one unconfirmed DELETE took all of that. What it did not
take are the three tables keyed on a **project** that sit outside every one of
those relationships — the render approvals, the review shares with their
decisions and comments, and the compliance acknowledgments. Those stayed
behind pointing at ids that no longer resolve, which is not a record: a
compliance sign-off naming a project nobody can look up says nothing, and
those three are precisely the rows that exist for the day a client says **"we
never signed off on that"**. The route answered `{"ok": true}`, carried no
count of what had gone, and wrote nothing to the activity log — so the only
account of the deletion was the absence it left. Verified by running it rather
than read off the models.

`teardown.py` is the one reading of what a delete takes with it, because
`delete_project` had the identical failure one level down and would otherwise
have grown an identical fix — two readings of one question drift the day
either is edited. Four rules in it. **Nothing in it may raise**: a count that
cannot be taken must not cost the refusal it informs, and a sweep that fails
must not strand the delete somebody asked for. **The name is read before the
delete and it is the row's own**, never one the caller passed — the record
`modules/suite_panel` had to undo on the route that deletes a sub-account. A
client with work behind them is **refused with the counts named**, and a
`confirm` carrying their exact name is the way through, the rule
`modules/image_picker` applies to deleting a gallery: refusing outright would
be a check somebody switches off, and switching this off costs the recording
too. And **a spot does not weigh itself** — counted, every delete of an
untouched draft would come back asking the rep to type its title, which is the
friction that gets a confirmation clicked through without being read, and then
it is not a confirmation.

**The line the module records on is written down rather than left to
judgment**, because it is what decides all twenty-five declarations: a route
records when a file reaches the **client's own Cloudinary tree** or changes
their **own record**, and does not when it moves a draft forward. So the kept
voiceover records and the audition beside it does not; the upload records and
pointing a scene at an asset already in the library does not; and
`save_pronunciation` records, because it writes the same brand-profile field
`update_client` writes and two routes changing one field with only one of them
recorded is exactly what somebody would go looking for later. `client_comment`
was the subtler one: `review_spec.inbox()` already counts a client who left
four timecoded notes and pressed no button as having **answered**, so leaving
it silent while `client_decide` records means one reply reads two ways
depending on which control the client used.

Eighteen routes record and twenty-five are declared with their reason; nothing
is undeclared. `test_write_attribution.py` sweeps it like the other two — its
`path` takes a **list** now, because this module is a blueprint package and
`HOUSEKEEPING_ROUTES` belongs per file where the reason is, while the question
*is every write here attributable* is about the module.

## Two guards on one client account, and both worked about half the time

`modules/suite_panel` creates and deletes clients' Smart 1 Suite sub-accounts.
It is the most destructive thing in this Hub, none of it is undoable from the
panel, and it had no test of its own. Three findings, and the caller got a
clean answer on every one.

**A double-submit guard held in memory is a guard on one worker in two.**
`_idem` was a dict, gunicorn runs two workers, and a resubmitted idempotency
key that landed on the worker which had not seen the first one found nothing
cached and **created a second sub-account**. That is the `_state`-is-per-process
trap this file already names for the scheduler and for `clients_registry`'s
two-minute cache, on the route where it costs a duplicate client account. The
claim is a file on the shared data disk now.

**And it was written after the work, so it never covered a double-click at
all.** `idem_get` read at the top and `idem_set` wrote once the account
existed, so two requests arriving together both found nothing cached and both
created one — which is exactly the shape a double-submit is. The key is
**claimed before the work starts**, `O_EXCL` so the claim is atomic between
workers, and a request whose twin is still in flight is refused by name rather
than creating the second account. A claim is **released** when nothing was
created — a refused name, a duplicate the rep is about to confirm, a create
that failed — or the retry replays a refusal for five minutes and the rep
concludes the panel is broken.

**A check that could not run is not a check that passed.** The duplicate
search 500s, the route logged a warning and returned a clean 201, so a rep
could not tell "there is no account of this name" from "we could not look" —
`connected_accounts_result()`'s rule, on the one check that exists to stop a
client having two accounts. It is `clear` / `not_measured` / `skipped` now, in
the response and on the activity entry. And the confirm-and-resubmit path sets
`confirmDuplicate`, which switches the check off entirely, so on the retry both
guards were down at once and nothing said so.

**A record of a deletion that names the wrong account is worse than one that
names none.** The activity entry carried `?name=` from the query string, never
checked against the account being deleted: delete `loc_9` while passing another
company's name and that is what the log said, and omitting the parameter
recorded an empty one. It is the only record that the deletion happened, and it
is what somebody reconstructs an incident from. The account is read first and
**its own name** is recorded; a read that fails does not stop the deletion —
the rep asked for it and GoHighLevel is the authority on whether it can
happen — but the claimed name is then kept as `claimedName` with
`nameSource: "not confirmed"`, never promoted to fact. A deletion GHL refused
is not written down as one.

`test_suite_panel.py` asserts all of it, including that the claim cannot
quietly go back to being per-process.

## One company, several client records

National Background Check and Fast Fingerprints are one business. Every
insertion order and every invoice is filed under National Background Check,
and Fast Fingerprints exists in Knack as its own client record because that is
the name on the campaign. Open it in Client 360 and it reads as a client with
no products, no invoices and no history — a confidently wrong answer of exactly
the kind this codebase treats as worse than an error.

The **Group** button sits beside Expand all / Collapse all on Client 360,
because what it changes is the whole record rather than one card: products and
IOs, creative, client notes, work, proposals and invoices are then read across
every member of the group, from whichever member you opened.

`hub/client_groups.py` owns it, and the merge happens **server-side** so a card
added later reads the group without knowing there is one — `/api/c360`,
`/api/client/work`, `/api/client/profile`, `/api/client/proposals` and
`/api/qb/invoices?client=` each resolve the roster themselves. `roster()`
always answers, and an ungrouped client comes back with `names` holding only
itself, so no caller has to branch.

Every rule in it is a way to be wrong quietly:

- **Members match exactly or not at all** — canonical domain first, exact
  normalised name second, through `hub/client_key.py`. "Riverside HVAC" must
  not collect "Riverside HVAC Supply": attributing one company's insertion
  orders to another is the worst outcome available here. `search_client()`
  matches the *query* loosely on purpose, and `_exact_client_rows()` is
  deliberately a different, stricter test.
- **A duplicate is dropped once.** A product filed under the organisation name
  is found under the parent *and* the member; merged twice it doubles the
  "Active billing" pill, and a wrong total looks exactly like a right one.
  `merge_rows()` is the only place that decides what a duplicate is.
- **Every merged row keeps its own name.** The group is a billing relationship,
  not a rename, so each row carries `member` and the page prints it — and a
  proposal merged in from another record is written back to *that* record's
  store, not the one on screen. Posting the edit against the client on screen
  would read as saved and change nothing.
- **A client is in at most one group.** Two groups claiming one company makes
  "whose bill is this on?" unanswerable. The refusal names the group that
  already holds them.
- **Removing the parent dissolves the group** rather than promoting a member.
  Which sibling holds the bill is not a question this file can answer, and
  guessing it moves every invoice on the record.
- **Nothing is written to Knack.** This is a Hub overlay, like the discovered
  URLs in `hub/client_urls.py`: ungrouping leaves both client records exactly
  as they were. Stored through `jsonstore`, as names and URLs — never the
  derived key, for the reason `client_key` gives at length.
- **A member we could not find is named.** "This member has no invoices" and
  "we never found their QuickBooks customer" are different answers, and only
  one of them means stop chasing the bill.

`test_client_groups.py` asserts all of it.

---

## Whose client is this, and what is outstanding on them

`hub/client_owner.py`, `hub/client_health.py`, `/my-clients` and
`/qa/client-owners`. Every report in this Hub answers a question about *the
book* — which sites nobody is billing, which campaigns are waiting on artwork,
which quotes have gone unopened. Not one of them answers the question a person
actually opens the Hub with, which is **"what is on my desk?"**, because
nothing anywhere recorded whose desk a client was on. A rep read the whole
list, recognised four names, and the other hundred and fifty were somebody's.
Client 360 was no help either: it answers *what do we know about this client*
in nineteen cards and says nothing at all about what needs doing.

So the six things that actually stall a campaign were spread across six
screens — a clarification sitting on an insertion order, a spot nobody has
made, a proposal the client never opened, a price about to lapse, an IO ending
in three weeks, a dashboard that was never built — each with a report of its
own, each report a list of every client at once.

**An assignment is a Hub overlay and is never written to Knack.** The rule
`hub/client_urls.py` and `hub/client_groups.py` both work to: removing one
leaves the client record exactly as it was. **The client is stored by name and
the person by email** — never the derived key, for the reason
`hub/client_key.py` gives at length, and never a display name, because two
people on this roster share a first name and `hub/celebrations.mine()` has
already paid for that. **A client has at most one owner**: two makes "whose
client is this?" unanswerable, which is the whole question the record exists
to answer. Reassigning is allowed and carries who held it before — a handover
is the ordinary case, and refusing it would be two presses to record one
decision.

**A media partner is a standing rule, resolved on read rather than written
into rows.** "Everything Moto Media carries belongs to Erik" is the ordinary
shape of this book, and expanding it into one assignment per client meant a
re-press every time a partner gained one — which in practice means the new
client belongs to nobody until somebody notices.

What makes a standing rule safe is that it can never quietly become the only
account of who owns a client, and four things carry that:

- **A rule materialises nothing.** `resolved()` lays it over the direct
  assignments on every read, so a rule that changes, or a client who leaves
  the partner, takes effect at once and leaves no orphan row behind — and
  clearing a rule needs no row-by-row undo, because there were never any rows.
  The reason `hub/creative_evergreen.py` applies its mark on read: two
  gunicorn workers, and rows written by one of them are a second account of
  the truth. `test_client_owners.py` reads the stored file and requires a rule
  to have written no assignment rows at all.
- **Every row says where its owner came from** — `direct`, or `rule` with the
  partner named. The objection to a standing rule is that somebody ends up
  holding a client with nothing anywhere saying why; the provenance is on the
  row and all three screens print it.
- **A person beats a rule, in both directions.** A direct assignment wins, and
  taking a client off everybody **sticks**: where a rule would otherwise
  re-claim them, `unassign()` records an explicit "nobody" rather than
  deleting the row, or the press would undo itself on the next read — the
  failure `hub/client_urls.missing()` had to undo. `follow_rule()` is the way
  back, and it is its own verb rather than a second meaning for unassign,
  because "take this off everyone" and "let the rule decide again" are
  different answers and one control for both cannot say which it did.
- **Two rules claiming one client are named and refused.** A client billed
  under two media partners is under both rules, and picking between them is
  picking whose book a client is on. They are left unassigned, marked
  `contested` with both partners named — the rule `hub/suite_accounts.py`
  applies to two rows naming different sub-accounts.

The **one-off bulk assignment** stays beside it, because the two are different
statements: "these forty clients are Erik's" is a fact about those forty, and
"whatever this partner carries is Erik's" is a fact about the partner. Only
the second follows the book as it changes.

**The partner map is read once per run, not once per reader.** `resolved()`,
`summary()` and `unassign_many()` all take one, and `hub/client_health.build()`
hands over the grouping it already has — `qa.client_groups()` carries the
partners on every client's lines. Two pulls would be two answers to "who
carries this client", taken a moment apart. And the products are only read at
all when a rule exists: a book with no standing rules must not pay for a pull
it cannot use.

**A bulk assignment reports every row's own outcome, and writes the file
once.** The first half is `client_urls.accept_many()`'s rule — one number back
hides the two that failed. The second is this deployment's own arithmetic: the
largest partner here carries **eighty-seven** clients and `jsonstore` mirrors
every write into the database, so a loop calling the single-assignment path
would be eighty-seven read-modify-write cycles and eighty-seven round trips on
one press. The refusals stay per row; what is shared is the file, not the
answer.

**An owner whose account is gone is named, never read as unassigned.** "Nobody
owns this" and "the person who owned this no longer has an account" are
different situations and only the second has a handover behind it — the rule
`check_stale_json_exemptions()` works to, wearing a client. And
`assignable_users()` answers `(users, error)` with **which list answered on
every row**: the account table first, the census roster as the fallback, because
an account created or suspended since the census is only in the first, and a
picker drawn from an empty list over a table that would not answer is a screen
saying this company employs nobody.

### What "outstanding" is, and what it is not

Everything on `/my-clients` is **read**: the Knack product rows, the creative
audit, the proposal store, the review rounds, the last website reading.
Nothing is entered, so nothing can go stale by being forgotten — and each
source is asked in its own function, each returns `(answer, error)`, and **a
source that could not be read is named rather than counted as nothing**. A
client with a hole in their row must not read as a client with nothing
outstanding: that is the confident wrong answer this codebase keeps having to
undo, and here it sends a rep away from the one client who needed them. The
note at the top of the page says *"anything those would have raised is missing
from every row, not absent from it"*, which is the distinction the whole report
rests on.

Nothing is re-derived. `qa.client_groups()` is the products grouping every
report on that page already reads, `stale_creative.by_client()` is that audit
keyed on **its own** match key, `sales_status.by_client()` is `scoreboard()`
with the cap lifted, and the review rounds go through the Commercial Builder's
own `_acted_on()` — a second reading of *has this been dealt with* would put a
client's reply on this page after somebody had already handled it, or drop the
one they are waiting for. `upsell.audits_for()` gained the traffic figures
rather than this module querying the same scans table again: two readers of one
audit is how the client record and a QA report come to quote different numbers
for one business.

**Clients are sorted by how much is outstanding, worst first.** Not by name and
not by billing — the question is which client needs an hour today, and an
alphabetical list answers a different one. Ties break on the money, then the
name so the order is stable between runs.

**Every issue carries the screen it is fixed on, and that screen is never this
page.** `ISSUE_KINDS` is the table and the test asserts it: the work already
has somewhere to happen, and what was missing was knowing which client it was
about. This report finds the work; it does not become a fourteenth place to do
it.

**Ignore and Done are three states, not a Dismiss button.** "We are not going
to act on this" and "this has been dealt with" are different claims about the
same row, and folding them into one loses the only one of the two anybody would
want to audit later. Neither deletes anything: both move the row into its own
list under the client with who marked it and when, and one press puts it back —
a list that quietly gets shorter cannot be told from a report that failed to
load, `hub/creative_evergreen.py`'s rule.

**A Done mark is about the issue as it stood.** `fingerprint()` digests what
the issue actually *said*, so an asset ask that changes retires the mark and
the row says it was **superseded** rather than vanishing or standing over
something nobody has read — the shape
`modules/commercial_builder/compliance_spec.py` arrived at for a sign-off. It
is keyed on the evidence and not on the label, because rewording a label in
`ISSUE_KINDS` is our edit rather than the client's campaign changing and must
not retire every mark on the book.

**Owners, marks and notes are applied on read, never baked into the cache.**
The build is held for the day by `hub/report_cache.py` and there are two
gunicorn workers, so a mark folded into a cached payload is a button that
appears to do nothing to whichever worker did not take it —
`hub/creative_evergreen.py` had to undo exactly that with a five-minute cache
and a day-long one puts a much longer fuse on it. It is also why assigning a
client **does not** drop the cached run: where an overlay can be applied on
read, that beats dropping a cache, and dropping it would rebuild the products,
the creative audit, the proposal store and a batch of website audits once per
press.

**The search and the owner filter run per request.** They are not cache keys,
for the reason `hub/report_cache.py` gives: a free-text box types one file per
keystroke onto a 5 GB disk. The *build* is cached; the filter is not — the
split `domain_links.orphans()` already uses.

**Four kinds of nothing on the traffic block**, and each is a different thing
to do: never audited, audited and out of date, the scans table would not
answer, and a current reading. Every figure is left out where the plan did not
measure it rather than printed as a zero — a zero there reads as a claim about
the client's business instead of about our audit — and a genuine zero is kept.

**"My clients" cannot be answered from the session cookie.** It carries a
display *name* and nothing else, so `viewer()` reads the account row through
`users_routes.current_account()`. A `PANEL_PASSWORD` session has no account at
all and is **not** given somebody's book on a name match: "Shared login" is a
true statement about the session and a useless one where the whole value is
whose it is, which is `hub/ad_copy.py`'s refusal one form along. It is told so
and shown everybody's instead.

**And a zero on the dashboard card says which kind it is.** "Nothing is
outstanding on your clients" and "nobody has assigned you a client yet" render
identically as a nought and only the second is somebody's to fix. The card
reads the *same run* the page draws rather than counting again — two screens
answering "what is on my desk" separately is the `/api/db/structure` versus
`/api/integrity` trap.

**No `data-screen` and no `data-module` on either page.** A tour is offered
only where one is registered and `hub-demo.js` floats "Walk me through this"
onto any page carrying a module name, so naming one that does not exist is the
silence Smart 1 Ads shipped on Settings and Live campaigns. The bubbles that
*are* placed have entries behind them, and `test_client_owners.py` requires it:
a bubble whose key is missing is removed client-side, so the template reads as
helped and the screen shows nothing.

`test_client_owners.py` asserts all of it, including that every one of the
twelve routes refuses a stranger — they name every client, what is wrong with
each and who owns them.

## A renderer we host, and the two things that makes different

`hub/hyperframes.py`, `modules/commercial_builder/vox_spec.py` and
`modules/hyperframes_tools`. Every other provider this Hub reaches is a hosted
REST API: we post a request and somebody else's servers do the work. HyperFrames
is an **open-source rendering framework** (Apache-2.0) whose normal shape is a
local CLI driving headless Chrome frame by frame and encoding with FFmpeg —
Node 22, Puppeteer and FFmpeg, none of which is in this Flask image and none of
which belongs in it. So the renderer runs as its **own Render service**,
`hf-render-service`, and `hub/hyperframes.py` is the whole of the Hub's side of
that wire. **That service is a separate deliverable and is not in this repo**;
until it exists, `HF_RENDER_SERVICE_URL` is unset, which is the state every
screen below is written for.

Two skills ride on it and they are deliberately different sizes. **Paint
animation is a treatment** — p5.js handwriting, paint-on and living-painting —
so it is a *sixth scene source* in the Commercial Builder beside stock, AI, the
spokesperson, an upload and a client asset, never a replacement for them. **A
Vox explainer is a complete output**, a 60–90 second editorial collage, so it is
a *ninth commercial type* rather than a scene option: a scene source that
produced a finished video would be a scene that is the whole spot.

**No API key, and therefore no `hub/quotas.py` marker.** It is self-hosted;
what it costs is Render compute rather than a per-render bill, and counting
renders here would be counting something nobody is invoiced for. What *is*
billed is the OpenAI call that writes a beat list, and that is recorded where
it happens.

**Configured, reachable and working are three questions.** `is_configured()`
reads settings and costs nothing, so it is what a page gates on — the paint
button and both standalone forms are simply **absent** without a service,
rather than present and failing at the moment somebody is waiting. `check()` is
one request and is what QC and Diagnostics read. Neither is evidence of the
third, which is why a job reports its own outcome and nothing infers success
from the submit. Not configured is **not measured**, never a cross:
`services/provider_check.py`'s rule, one provider further out.

**A mock is marked and never filed.** With no service a submit answers a job
carrying `_mock` and no file — so the tool reads as switched off, which it is,
rather than as broken. `is_deliverable()` is the **single** reading every filing
call site asks, the refusal `approve_render` already makes about a mock
Creatomate render. A truthy `url` alone is not the test: a job still rendering
has one too.

**An unrecognised render state reads as still running.** Treating one as
finished attaches nothing while reporting success, which this module has
already learned with HeyGen and again with Runway. The status route is also
what **attaches** a finished file, never the browser, so a closed tab does not
lose minutes of rendering nobody will start again.

**The templates are pre-authored and parameterized, never authored per
request.** Having a model write fresh HyperFrames HTML each time is slow,
impossible to QC, and throws away the one thing this framework offers that
Runway does not — the same input always renders the same film. `TEMPLATES` is
the contract and a name absent from it is refused **here**, because a service
404 and a typo'd template name arrive identically and only one is fixed by
restarting anything.

**The beat list is the join, and it is the whole risk.** A model writes JSON, a
template consumes JSON, and nothing between them otherwise checks that the two
agree — the shape `submit_render` already paid for, where an audit line read
`project.name` and `project.length` and every render this tool had ever been
asked for returned a 500 that reached the browser as "Bad response from
server". `vox_spec.validate()` runs over what the model wrote **and** over
anything typed by hand, because a rule the form keeps while the write breaks it
is not a rule. A beat it cannot read is **dropped and counted with its reason**,
never repaired: inventing the missing half of somebody's explainer is this
module writing copy nobody asked for, and a silently shorter list is a video
missing exactly the point somebody wanted made.

**The window is arithmetic, and the per-beat cap has to hold inside it.**
`rebalance()` scales the beats to the 60–90s window rather than the prompt
being asked nicely for a total — a model told "75 seconds" writes beats summing
to 52 and puts 75 in a field beside them. The first version put the whole
remainder on the **longest** beat and produced a 52-second card in a collage
explainer, past a per-beat ceiling that exists because nobody watches one card
for the better part of a minute. A cap honoured everywhere except in the
correction is not a cap; the remainder is spread across the beats with room,
and a list too short to fill the window is **left short** rather than forced —
that list is already failing the beat count, and the honest total is what the
duration check should read.

**A Vox explainer is refused where nobody sells the slot, by the route.**
`vox_spec.PLATFORMS` is `youtube` and `social` and deliberately **not `both`**,
which `config.PLATFORMS` spells *"CTV and YouTube"* — allowed there, a 60–90s
editorial piece is a CTV placement the buy refuses, with the platform field
reading as though it had been checked. `VOX_LENGTHS` is its own list because
`COMMERCIAL_LENGTHS` stops at 60, which is this format's *minimum*. And it is
one length at a time: the multi-length build exists because a :15 is cut down
from a :30, and each Vox length is a different beat list rather than a trim of
another. The Start page hides what it cannot offer and the **create route
refuses it by name**, because the form is not the gate.

**A spot with no storyboard cannot answer a storyboard's checks, and that
nearly shipped as a gate nobody could pass.** `run_qc` runs twenty-odd checks
and six of them read Scene rows — timing, footage, narration length, the CTA,
the YouTube hook and the spelling pass. A Vox explainer has none, so all six
reported real failures about a spot that is fine, and `submit_render` refuses
on `not _all_passed`: **every Vox render there could ever be was refused**,
with the panel naming six things nobody could fix. `NOT_FOR_VOX` marks them
not-applicable with the reason each cannot be answered — the same answer
`_check_render_service` gives a Creatomate spot, from the other direction, so a
reader can still tell *we looked and it is fine* from *this question is not
about this spot*. Declared rather than a `continue`, so a check added later is
a decision about that list rather than an accident.

**The two new checks advise and never block.** The render service being down is
an outage rather than a defect in the spot, and refusing to let somebody finish
a beat list over it is the gate `QR_CODE_RULES` explains getting switched off.
The duration is measured off the **plan** rather than a rendered file, for the
reason `abcd_service` scores the plan: a length problem found on an MP4 is a
re-render and one found on the beat list is free. Both are in **both** JS label
maps — a key absent from one is skipped silently by that panel's loop, which is
the failure `scene_assets` already had.

**The finished file goes to the client's Cloudinary tree and not the image
gallery.** Both skills produce video, and `filing.file_asset` models an image or
a raw file — the Commercial Builder leaves its commercials, spokesperson clips
and voice tracks out of the gallery for exactly that reason, because a row whose
thumbnail can never render is worse than an absent one. So a kept render is
stored through `hub/storage.put_remote()` (Cloudinary fetches it; this process
does not download a video to post it back up) and written to the **activity
log**, which is what puts it on the 360 record — and the two are **reported
apart**, because "stored" and "stored and on their record" are different
outcomes and one tick over both is how somebody learns not to trust the tick.

**A client is optional and is never guessed at.** With none picked the render is
still made and still downloadable, the way the Background Remover and the UTM
Builder work. What is refused is a *typed* name: `client_key.resolve()` answers
for everything, so the test is `known` rather than truthiness — it hands back
the input verbatim under `client` when nothing matched, and reading that as a
match is the typed-name failure the whole check exists to refuse.

**Both tools log under their own names**, declared in
`client_brand.WORK_KINDS`, because a standalone paint animation is not a
commercial and reading as one on a client's record would say we made them a
spot we did not. The name is **data and the call is direct**: an earlier
version passed each tool's bound logger down as a parameter, and a logger
reached through a parameter is one no static walk can follow —
`/api/integrity`'s silent-module check duly reported the module as never
logging at all, which was a true statement about what it could see. The fix is
a call it can read rather than an exemption asking to be trusted.

**One module for two tools, and the templates are prefixed.** Submitting,
polling, storing and filing are identical for both; two directories would be two
copies of that and the next fix to the poll would land in one of them. They are
hub-app blueprints, so they share the hub's Jinja environment and a bare
`index.html` here would resolve against the hub's own templates first —
`hf_paint.html` and `hf_vox.html`, the trap `/api/integrity` has a
high-severity check for. Both carry `hub/blueprint_guard.install()`, because a
blueprint is not behind `AuthGuard`.

## Opportunistic migration — read this before editing any module

`hub/storage.py` (Cloudinary), `hub/images.py` (resize/convert),
`hub/jsonstore.py` (persisted JSON) and `hub/config.py` (settings) are the
shared implementations. **They are used by almost none of the modules.** Instead, 15 modules configure Cloudinary
themselves, 6 have their own resize code, and 55 files read environment
variables directly.

This has already caused real bugs twice. The "cap the longest edge before
converting" rule had to be found and fixed in several places separately. And
when the Pexels key was named `PEXELS_API` rather than `PEXELS_API_KEY`, the
fix went into `hub/config.py` — and the tool was still broken, because Image
Creator never called `config.py`. It had to be fixed a second time.

**The rule: when you are already editing a module for another reason, move
that module's Cloudinary, image and settings code onto the shared versions
while you are in there.** Not as a separate project — a big-bang rewrite of 22
working modules is risk with no feature at the end of it. But never leave a
module you have just touched still doing its own thing.

What that means in practice:

    cloudinary.config(...) + cloudinary.uploader.upload(...)
        -> from hub.storage import put;  put(data, kind="seo_images", ...)

    Image.open(...).save(..., "WEBP")
        -> from hub.images import optimise;  optimise(data, max_edge=1600)

    os.environ.get("PEXELS_API_KEY")
        -> from hub.config import settings;  settings.pexels_key
           (config already accepts every spelling in use)

    open(path, "w") + json.dump(...)   /   open(path) + json.load(...)
        -> from hub import jsonstore
           jsonstore.write_json(path, data)   # atomic, and mirrored
           jsonstore.read_json(path, default=[])
           jsonstore.delete_json(path)        # never bare os.remove

    base = "/var/data" if os.path.isdir("/var/data") else .../"data"
        -> jsonstore.data_dir("my_module")

If a shared function does not do what the module needs, extend the shared one
rather than keeping the local copy. That is the whole point — the next fix
should land once.

## There is one proposal builder

There were two, which is worth remembering because the shape of the problem
recurs. `modules/sales_builder` (`/sales/builder`) and
`modules/proposal_builder` (`/sales/proposals`) shared no code, no storage and
no idea of what a campaign is. The same client could be quoted two different
ways depending on which one a rep opened, and only one produced anything an
insertion order could read.

`modules/sales_builder` is now **the** Proposal Builder. `/sales/proposals`
redirects to it (carrying Client 360's prefill through) and serves only the
old tool's archive, which stays readable because those are real documents real
clients received — `/sales/builder/api/legacy/proposals/<id>/import` reopens
one as a live quote. What moved across: the industry library (now
`hub/industries.py`), AI-written narrative copy, the Cloudinary-hosted PDF, and
filing the finished proposal onto the client record.

Delivering a proposal now files it on the client and opens an opportunity in
Smart 1 Suite, through `hub/ghl_contacts.py` — one token, one location id and
one contact write path for the whole Hub. `hub/suite_opportunity.py` keeps
only the pipeline and opportunity logic that is genuinely its own. It briefly
resolved the location itself and fell back to `GHL_COMPANY_ID`, which on this
deployment holds the same value as the company id: a companyId used as a
locationId files against the *agency*, so every opportunity would have landed
where nobody goes looking. It looks up the contact first and **asks** when
there is none,
rather than creating one from the business name — an opportunity attached to a
contact nobody can call is worse than no opportunity, and it duplicates the
real contact next time anyone searches.

### The proposal has a specification, and it is data

`hub/proposal_spec.py` owns the 13-part outline, the standing directives, the
audience partner taxonomy, the Suite tiers and the operating facts a proposal
may cite. The builder, the PDF, the Word export and the AI prompt all read it,
so changing what a proposal contains is one edit rather than four.

Three of those directives are checked rather than merely requested. Copy that
mentions **Smart 1 Labs** is discarded before a rep sees it — a prompt is a
request, and "the model was told not to" is not evidence that it did not. The
**Expected Results & ROI** section is *computed* from `hub/rate_card.py`, never
written: a management fee reports no impressions at all rather than a plausible
number, because a projection that contradicts the media plan printed above it
is worse than no projection. And the **Investment Summary** keeps recurring
platform licensing apart from media spend and one-time production, so a client
can tell what stops if they pause the campaign.

`roi`, `mediaplan` and `packages` cannot be deleted from a proposal. An older
quote saved against the previous eight-section layout keeps its copy and gains
whichever required sections it is missing on the next save.

### Discovery drives the recommendation, and the proposal is read before it is edited

The four "what are they already doing" questions were captured and never read
— a rep could answer all four and the document came out identical.
`hub/current_marketing.py` makes them mean something and adds the three that
change what we recommend: are they retargeting, are they optimised for AI
search, are they happy with their website. The gaps become the **We Suggest
They Should** list, shown in its own colour so it reads as advice rather than
another form field, and whatever the rep keeps is written into the proposal's
friction section.

The last question is the one with money behind it: are they running
traditional media, and do we *supplement* it or *move* some of that budget.
The answer sets the proposal's posture and reaches Expected Results & ROI —
but the guidance handed to the writer also **forbids arguing it**. A model
given "they want to shift budget to digital" writes a case against radio, and
a proposal that opens by calling a client's existing spend wasted loses the
room before the media plan is read. `test_proposal_spec.py` asserts those
three prohibitions are still in the text.

The Proposal Document step opens on a **preview** — the document as the client
will read it, prose and real tables — with edit, AI rewrite and hide on every
section, and the media plan editable in place so changing one budget does not
mean walking back three steps. The section-order list is still there behind a
toggle. Writing the copy runs **one request per section** so the loader can
name what it is working on and one failed section does not cost the other
twelve.

The PDF scales its type down as the document grows (`_type_scale`), bounded at
0.82. An ordinary proposal is not shrunk at all: "lower the fonts when
necessary" means when there is more than usual in it, not always.

**Any generated table can be left out, or edited.** The standing rule is that
a table is computed and the copy above it introduces one — a proposal whose
prose and figures disagree is the failure the whole specification is built
around — and that rule still decides what the tables say by default. What it
never covered is a table that is right and still wrong for *this* client: a
row naming a location under NDA, a phase of the timeline they do not want
printed, a KPI they asked us to drop. The alternative a rep actually had was
exporting to Word and editing it there, which takes the document out of the
system entirely. So `section_table()` is the single reading of "what goes
under this section" — generated, excluded, or replaced — read by the preview,
the PDF and the Word export alike, in **one** gate in front of the twelve
`kind` branches rather than in each of them, because a branch per kind is how
a setting comes to be honoured by eleven of the twelve. An edited table is
drawn in amber in the builder with the generated one one click away, and its
badge says it no longer recomputes, which is exactly true.

### The one line the rate card does not name

`hub/product_intake.CONSULTING` has defined **Consulting & Strategic
Services** all along, and its own docstring says what it is for: a line *"used
when a proposal committed to something the rate card does not name, so the
commitment reaches the insertion order rather than being dropped for lack of a
product code."* **The proposal could not make that commitment.** Every push
into `S.items` came from a rate-card row, and the card has no consulting
product — 19 categories, and the nearest thing is *Google Analytics
Consultation* under SEO, which is installing and repairing GA goals rather
than strategy work.

So the catch-all was reachable only from the IO's **intake** — the path for a
proposal uploaded as a file, read back and classified line by line. A rep
selling strategy work either left it off the quote and added it at IO time, so
the client signed a document that never mentioned it, or did not sell it. The
mechanism was built at the far end of a journey the near end could not start.

**It is still not a row on the card, and that refusal is the load-bearing
part.** `product_intake.py` says why in a comment older than this feature:
that file is the wholesale card, `check_drift()` holds it against the IO
template's embedded copy, and inventing a product inside it would make both of
those lie. There is a **third** copy in the proposal wizard, and it must not
gain the line either. So the definition is **served** rather than mirrored —
`/api/config` carries it, beside the creative sizes and the markup rule, for
the reason that section already gives — and `test_proposal_consulting.py`
requires the wizard to hand-type the product string **nowhere**.

**The join is that string, and it is exact.** The IO recognises the catch-all
by product name, so a hand-typed copy in the wizard is a line the insertion
order silently drops the day either end is edited. Asserted byte-for-byte
against the IO template's own constant.

**A description is required, and that is `question_for()`'s rule rather than a
second one.** Every consulting line ever quoted prints the same product
string, so with none the client reads *"Consulting & Strategic Services —
$5,000"* against nothing and trafficking reads a line it cannot action. It is
refused **by name** at the control rather than added blank, because a line
that reports a clean success and arrives meaningless is the whole failure
being closed. One trap in reading that rule: `question_for()` asks the basis
and the term **before** the description, so a plan line must hand over both or
the question comes back as *"monthly or one-time?"* about a line whose basis
is on the screen.

**And the description has to survive both journeys, which is where it was
actually being lost.** The IO's intake already carried it to special
instructions — but `ioDataPayload()` sent **no `specialInstructions` at all**,
so a Hub proposal converted straight through arrived with a product name and a
budget. Both ends are closed now: the client's media plan draws it under the
product (decided once in `media_plan_rows()`, drawn by the preview, the PDF
and the Word export the way `monthly_label` already is), and the conversion
writes it onto special instructions in the shape the IO's own intake appends
to, plus into `internalRequirements` under its own product heading, which is
what the internal PDF prints and what trafficking reads.

**Two of the three downstream behaviours were already right**, which is worth
knowing before anybody "fixes" them: `gated_media()` returns nothing for a
consulting plan, so it does not ask who is supplying creative for a workshop;
and `channel_lines()` leaves it out of Recommended Channel Strategy, because a
line with no rate and no gated medium is a fee rather than a channel. Both are
asserted so they stay true.

**Consulting lines dedupe on the description, not the product.** Two
engagements at two prices is the ordinary case — a strategy workshop and a
quarterly review — and every other line on the plan dedupes on a product
string all of these share.

**And there are two consulting products, which is a thing to know before
renaming either.** `state["consulting"]` is a monthly **retainer** — Suite
coaching and campaign strategy, priced from estimated hours, riding beside the
licence in the Investment Summary because it is recurring platform work a
paused campaign does not stop. This is the other one: a single **engagement**,
scoped and priced on its own, quoted on the media plan and trafficked as an
insertion-order line. Both are real, and they arrived from two directions
within a day of each other.

What that cost is the name. A client reading *"Consulting & Strategy"* in the
Investment Summary and *"Consulting & Strategic Services"* on the media plan
cannot tell which charge is which — two names for what reads as one thing,
which is the drift most of the rules in this file exist to refuse. So the
engagement is a **Strategy Engagement** everywhere a person reads it:
the plan editor, the preview, the PDF, the Word export.

**The product string underneath does not move with it.** It is the join — the
IO recognises the catch-all by that exact name, `product_intake` owns it, and
renaming it would orphan every line already quoted under it. `audit.LOG_NAMES`
and `video_library.TAG_ALIASES`' rule, one document over: the stored name
stays and the displayed one changes. `is_consulting()` keys on the product
string rather than the display name for the same reason, and
`test_proposal_consulting.py` asserts both halves — that the client's row
prints the display name, and that the join is byte-identical to the IO's
constant regardless.

### One product, one name — or the IO quietly carries 88 of 90

Two products were both called **Google Grant** — a $125 one-time setup fee and
a 15% monthly management fee — and two more were both **Local Service Ads
(LSA)**. The published rate card at `/partner/rate-card-universal` had already
solved this, naming them *(Setup)* and *(Management)*; both copies of the card
in this repo had not. Three things went wrong at once and not one of them
errored:

* `rate_card.find()` walked the list and returned the **first** match, so a
  quote for management was costed against the setup fee;
* the IO builder's `productConfig` is a dict **keyed on the label**, so the
  second row overwrote the first — 90 card rows became **88 products**, and
  neither setup fee could be put on an insertion order at all;
* `check_drift()` is keyed on the label too, in *both* dicts, so the one check
  that exists to notice the two copies disagreeing could not have seen a
  difference between the pair it was collapsing.

The two ends failed in **opposite directions on the same product** — the
proposal quoted the setup fee, the IO could only bill management — which is
the shape that survives review, because each screen is internally consistent.

Both copies carry the published names now. The lookup is the other half:
**a name that could mean more than one product resolves to none of them**, the
`client_key.resolve()` rule wearing a rate, and `candidates()` is what a screen
shows instead of the refusal so it never reads as *not on the card*.
`product_intake.classify()` returns those candidates and commits to neither,
which is what that function already said it did. **A category resolves what a
name cannot** — four headings carry a product called *Behavioral* at four
different rates — so `find(name, category)` and the IO's `cardLabelFor(name,
category)` take one, and a line that recorded its heading is answered rather
than asked. `ai_match` offers the candidates rather than dropping the row: a
model that names an ambiguous product has not invented anything, and dropping
it leaves the rep with nothing.

`test_proposal_spec.py` asserts every label is unique in both copies, that the
IO's product list is the whole card rather than what survived a collision, and
that the names we publish are the names we quote from — the partner pages ship
in this repo, so that last one is checkable rather than remembered.

**And a rename for the reader is not a rename of the join.** The IO template
carries a `PRODUCT_RENAME` that turns `Select Tactics - Comes with Retargeting`
into the friendlier `Programmatic Campaign with Retargeting` — and it ran over
the very array `cardLabelFor()` searches, so after that line **no row answered
to the name the card actually uses**. A proposal quotes the card, so the
lookup returned `""` for the go-to display product: the one
`rate_card.CATEGORY_GOTO` names and every awareness and traffic goal
recommends first.

It failed differently at each of the two doors, and neither errored. An
**uploaded** proposal pushed it onto `unmatched` and dropped the line. A
**converted** one fell back to the raw product name — which is not a
`productConfig` key, since the labels are built after the rename — so the line
reached the insertion order with no rate, no benchmark, no requirements and no
timeline, looking like a product nobody had filled in.

The rename stays, because somebody chose that wording for the screen; what is
kept beside it is `originalProduct`, and `cardLabelFor()` matches both. The
refusals had to survive gaining a second name to match on, so
`test_io_start.py` asserts them in the same breath: a product four headings
share still resolves to none of them, and Google Grant's setup fee is still
not confused with its management fee. It lifts the functions out of the page
and runs them in node rather than restating them, or the test is a second
description of the join.

### A transcription is only as good as the day it was taken

`hub/creative_specs.py` transcribes the S1M CREATIVE SPEC KIT rather than
fetching it, and that is still right: a spec table pulled live changes what a
check says with no diff to point at. What the argument never covered is the
transcription going stale, and it had — in **both** directions, silently,
because the kit and the verdict are each internally consistent on their own.

The kit says in as many words that **"the flat 150 KB rule is gone"**, that
**970x250 is called Billboard** because the IAB retired the Rising Stars
programme, that **SVG is now accepted**, and that **"15 seconds or 3 loops" is
no longer a universal rule**. The code was enforcing every one of the retired
versions. Half Page and 970x250 were judged at 150 KB against a published
250 KB, so the checker **refused files the client had been told to send**; a
smartphone banner was allowed 150 KB against a published 50 KB, the same fault
running the other way; and the mobile interstitial was sized 320x480, which is
on none of the three rows the kit sells it at.

**A target is not a floor.** DOOH publishes "40 KB target / 750 KB max", and
that 40 was carried as `min_bytes` — which `check()` treats as a **fail**. A
clean 30 KB billboard was refused for being too small against a number nobody
published as a minimum. It is `target_bytes` now, and the parser that reads the
page takes the figure labelled *max* rather than the first one it finds,
because taking the first is the same confusion one level up.

**The names changed and the ids did not.** `tags_for()` writes `unit_<id>` onto
every file delivered through the upload manager, so renaming `rising_star`
would orphan the tags already on a year of creative in Cloudinary to correct a
label — `hub/audit.LOG_NAMES`' rule, wearing a spec. The id stays; the name is
what a person reads.

**The page ships in this repo, so the transcription is checkable rather than
remembered.** `kit_drift()` reads the unit tables out of
`hub/partner_pages/creative-specs.html` and compares; `/api/integrity` runs it
at **high**. It stays a *check* rather than becoming the source, for the reason
the transcription exists. And a page it cannot read is **not measured** rather
than no drift: that is the one state where a clean answer would be a lie. Only
the three sections whose table is Unit / Dimensions / weight are read — the
social sections publish prose per format, and a parser guessing at those would
report drift that is not there.

**Tablet display is ours.** The kit publishes no tablet section at all, so
those four units carry `source: "house"` rather than reading as transcribed —
the rule `HOUSE_LEGIBILITY` in `services/abcd_service.py` already works to.

**And the check covered three sections of twenty-three while answering "no
drift".** That is a clean bill of health about seven per cent of the thing it
audits, and the exclusion note explained only half of it: six Meta sections
genuinely publish prose per format, and seven more — native display, YouTube,
the CTV interactive formats, X, LinkedIn, Snapchat and TikTok — publish a
perfectly good *table* whose columns are Format / Copy / Media / File Size
rather than Unit / Dimensions / weight. The blanket reason was applied to all
twenty. The page in this repo is now the **2026** kit and says on itself
*"20 formats updated · 3 added"*, against a transcription taken from 2025, so
a section outside the parser is not a hypothetical gap — and what makes it
dangerous is that it is invisible: a section the *next* rebuild adds is
silently outside every check here for ever, with the panel green. The same
shape as a sweep that quietly stops sweeping.

Every published section is declared now — `_KIT_UNREAD` with the reason its
table cannot be read, and `kit_coverage()` reports one that is not, in **both**
directions: a section on the page nobody declared, and a declaration that
outlives the section it described. `compliance_spec.NOT_ENFORCED` and
`ghl_scopes.NOT_REQUESTED`'s rule, wearing a spec: a thing left out on purpose
is named with its reason, so its absence is never ambiguous between an
oversight and a decision. A page that cannot be read is **not measured**,
never "nothing undeclared" — that is the one state where a clean answer would
be a lie. It started empty, which is the only way it was worth adding.

**And the names were the half that reaches the client fastest.** `kit_drift()`
compares numbers and can only read the three sections whose table is Unit /
Dimensions / weight. The social sections publish a different table — but its
first column is a **format name**, and a name is what the requirement line
prints at the client. **X** is the case that shows the cost: its 2025 model
named eight formats and **not one of them is a format X still sells**. "Website
Card" and "Direct Message Card" are retired, and the two mobile/desktop pairs
modeled a split the kit says in as many words is gone — *"the
mobile-versus-desktop creative split is gone. one asset set serves both."* So a
client was asked to supply four things that do not exist, and two of them
twice, on the line the client document prints. Silent from both ends: every
name was a real format's name once, the sizes were real sizes, and nothing
errored.

X is transcribed against the 2026 kit now — Image Ads, Video Ads, Vertical
Video Ads, Carousel Ads, Conversation Button, Amplify Pre-roll, Spotlight
Takeover and Polls — and the old `text: {final: 256}` went with it, because the
page says media no longer consumes characters. **The ids are kept wherever the
format survives in substance**, the rule `billboard` already follows from when
the IAB retired the Rising Stars name: `tags_for()` has written `unit_<id>`
onto delivered creative and a gallery filters on it. The four with no 2026
equivalent are in `RETIRED_UNITS` rather than deleted — **out of `UNITS`, so
nothing asks a client for one, and still in `BY_ID`, so a row carrying the tag
resolves to a unit that says what replaced it.** Deleting them would orphan the
tag; leaving them in would go on asking.

`kit_name_drift()` is the check, at **high**, and it covers only the channels
declared transcribed against 2026 — `_KIT_NAME_CHECKED`, which is `x`,
`linkedin`, `tiktok`, `snapchat` and `youtube` today. What is still on the
2025 transcription is named in `_KIT_NAMES_PENDING` and carried by
`kit_coverage()`. A backlog named rather than left as an absence — a check
listing every platform on the day it is written is red on the day it is
written, and gets switched off.

**YouTube was the last of the four, and asked for a format that does not
exist.** Google repurposed *TrueView* in October 2025 as a **metric** —
TrueView views, spanning skippable in-stream, in-feed, Shorts and Masthead —
so the requirement line asked a client to supply a thing with no definition.
Shorts was absent entirely and only 16:9 was modelled against a kit selling
16:9, 9:16 and 1:1. And the weight was the half that refused real work: **10
MB against a published 256 GB**, the kit's own *"wrong by four orders of
magnitude"* — the third of the four transcriptions to run that way, after
TikTok's two units and Snapchat's pair.

Six formats now, and **no duration on skippable in-stream at all**: the kit
publishes *"no maximum, under 3:00 recommended"*, and a ceiling invented from
a recommendation refuses a cut the kit permits — the `target_bytes` rule
wearing a stopwatch. `youtube_trueview` keeps its id, because skippable
in-stream is what TrueView was and `tags_for()` has written
`unit_youtube_trueview` onto delivered creative: the rule `billboard` follows
from the IAB retiring the Rising Stars name.

**Native display was that different job, and it is done.** Its first column
is an *asset* rather than a format, and OpenRTB Native 1.2 sets no character
limits at all — each seller declares its own per placement — so the kit
publishes The Trade Desk and Google Demand Gen side by side and says to
**build to the strictest platform in the plan**. That is what each field
carries, with the looser platform named in the notes rather than lost: a
25-character short title because The Trade Desk publishes 25 where Demand Gen
allows 40, and a 150 KB logo because Demand Gen caps there where The Trade
Desk does not.

The 2025 model held two units and a single `headline: (15, 55)` /
`description: (25, 120)` range — which the kit's own update note quotes as the
thing that is wrong, *"character limits are per-platform, not a single 15–55 /
25–120 range"* — so a client was told a 55-character headline was fine on a
platform that takes 25. **Business name** and **call to action** are asset
fields a native ad renders and nothing here had ever asked for, and the HTML5
package the section publishes was absent too.

**And that section is where the kit retires a whole category of ours.** One
sentence under Native Display: *"Tablet Display retired as a category — IAB
removed device-class ad units. 300x250 and 728x90 serve on tablet as the same
units."* Four house units here modelled it, and two of them asked a client a
second time for a file they had already supplied. The third is the one that
showed: **`tablet_interstitial` at 1024x768** was on every display
requirement, because 300x250 and 728x90 dedupe against their desktop twins in
the size run and 1024x768 does not — an extra file, for a placement nobody
sells inventory for. All four are in `RETIRED_UNITS`, and the **channel is
unwired from the product map as well as emptied**: named there with no unit
behind it, `required_units()` reports *"the spec kit maps no unit for this"* —
a warning about our own dangling entry, printed at the client.

**A third state, because two would have been a lie either way.** Native
display is transcribed against 2026 and still cannot join `kit_name_drift()`:
four of its eight rows are character limits carried on the main image rather
than units, and the HTML5 package sits under its own heading outside the table
the parser reads, so the name pass would report our own unit as a format the
kit does not sell. Left in `_KIT_NAMES_PENDING` it would claim a 2025
transcription that is no longer there; added to `_KIT_NAME_CHECKED` it would
report a finding that is not one. `_KIT_NAMES_UNCHECKABLE` is the third
answer, with the reason, and `kit_coverage()` carries all three.

**And the branch that answers when the page cannot be read was missing
them.** `kit_coverage()`'s not-measured return carried no `names_*` keys at
all, so a caller reading one — `test_proposal_spec.py` does — would raise on
the one day the check exists for, rather than reporting that nothing was
measured. Both branches answer with the same keys now, asserted.

**And a codec list is a ceiling too.** That transcription carried five of the
nine formats the kit publishes — *"MPG (MPEG-2 / MPEG-4) preferred, plus MOV,
MP4, WEBM, ProRes, DNxHR, CineForm, HEVC"* — so a **ProRes master, which is
what a finishing house hands over**, was still refused by the checker. The
same shape as the 10 MB ceiling it had just replaced, one field along, and
invisible for the same reason: five real formats look like a complete list.
`_YOUTUBE_FORMATS` is named once, because every YouTube unit takes the same
nine and two hand-typed copies is how one of them comes to be missing HEVC.

**A run of nine codecs is the wall the sizes rule already exists for.**
Printed once per unit across a six-unit buy, on the line a client reads, it
buries everything else on it. `_describe_unit()` prints five whole — which is
every other unit in the kit — and past that says how many more, rather than
pretending the list is all of them.

**What did not move is the rate card.** It sells products called *TrueView*
and *TrueView - Targeted* — product names on an invoice rather than format
names in a creative requirement. Renaming one orphans every quote, every IO's
`productConfig` key and the published partner page, which is the migration
this codebase refuses to do casually.

**And naming the formats exposed the line that had been dissolving them.**
`units_line()` folds image units into one run of sizes, which is right for a
display buy — "Leaderboard" *is* 728x90, and eleven labels beside eleven sizes
is the wall its own comment describes. It is wrong wherever the kit's first
column is a **Format**, and it had been wrong on every such channel: an X buy
asked for **nine bare sizes** with Image Ads, Carousel Ads, Conversation
Button and Spotlight Takeover all dissolved into them; LinkedIn the same
across six; and native display printed *"1200x628, 200x200"* with nothing
saying which of the two is the brand logo. That is `_shape_of()`'s own note
running the other way — there a unit reaches the line as a bare name, here as
bare sizes with the name gone, and on a format-name channel the name is the
entire ask.

The discriminator is the published page's own structure rather than a
judgment. `SIZE_SET_CHANNELS` is derived from `_KIT_SECTIONS` — the three
sections whose table is Unit / Dimensions / weight, the same three
`kit_drift()` can read — plus `tablet_display`, which is ours and is the same
shape. Everywhere else the name leads and its sizes ride with it. Nothing
about display, DOOH, email, CTV or Meta changed, and **both `ADDITIONS`
entries are decided before the split** — the radio companion and Snapchat's
AR filter each sit on a channel that sells no size set, so filtering by
channel first would have retired the one rule that keeps *"plus a companion
banner: 300x250"* from reading as the whole requirement.
`test_proposal_spec.py` asserts both directions, and every new check was
confirmed red against the real defect first.

**And a name check cannot see a number, which is how LinkedIn was refusing
files the kit told the client to send.** Its 2025 model held five formats to
the kit's eleven and `Sponsored InMail` named a category LinkedIn has split
into *Message Ads* and *Conversation Ads* — the X failure, found the same way.
What the name pass could not reach is three ceilings that had each moved
**upward**: Message Ads at 40 KB against a published **2 MB**, Sponsored
Content video at 200 MB against **500 MB**, and that video carrying a
`max_width` of 1080 while the kit publishes 1920. A ceiling that is too low
fails in the direction nobody checks — the upload manager refuses a file that
is *correct*, the client is told to send it again smaller, and every screen
reads as working. That is the Half Page failure `kit_drift()` exists for, and
`kit_drift()` cannot see this one either: it reads the three Unit / Dimensions
/ weight sections and LinkedIn's table is Format / Intro / Headline / Media /
File size. Only transcribing it finds these, which is the whole argument for
working the `_KIT_NAMES_PENDING` list down rather than waiting for a check to
raise its hand.

**A size the kit publishes exactly is a size we judge exactly.** The old model
carried `1200x627` and the kit publishes `1200x628`, so that one pixel now
fails — named, with the four accepted sizes in the refusal, rather than
absorbed by a tolerance. Inventing a ±1 would be house guidance wearing the
kit's name, the thing `HOUSE_LEGIBILITY` is kept out of `THRESHOLDS` to avoid;
1200x628 is what the client is asked for on the requirement line, so it is
what the file is held to. **Six formats are new** — Document, Thought Leader,
Event, Connected TV, Click to Message and Conversation Ads — and two of them
have **no file to judge at all**: a Thought Leader ad runs an author's own post
and an Event ad pulls its 4:1 image off the LinkedIn Event page. Those are
modeled as `kind: "other"` with no ceilings rather than left out, so a
requirement can name them and `check()` is never handed one — the answer
`x_polls` already gives.

**And TikTok is the same finding with the cost the other way up.** Its 2025
model named three formats to the kit's six and not one of the three was a
format TikTok sells — found by the name pass, as X and LinkedIn were. What the
names could not reach is that **two of the three refused creative the kit
allows, and the third asked for a file that no longer exists.** The in-feed
video was capped at **:60** against a published **10 minutes** and took two
file types where the kit takes five; the image ad was pinned to **1200x628 at
500 KB**, when the kit specs images by ratio now and says in as many words that
1200x628 *"survives only as the horizontal carousel option"* — so a 720x1280
vertical, the shape TikTok itself recommends, was refused outright. That is the
LinkedIn ceiling failure one channel over, and the second time in two
transcriptions that the numbers were worse than the names.

**A format the kit stops selling is retired, never re-pointed.** `tiktok_image`
and `tiktok_profile` are in `RETIRED_UNITS` — out of `UNITS`, so nothing asks a
client for them, and still in `BY_ID`, so a row carrying `unit_tiktok_image`
resolves to a unit saying what replaced it. Quietly aiming that id at the
carousel instead would make a delivered 1200x628 read as one card of a
two-to-thirty-five image set, which is a wrong answer wearing a fix. Profile
Image goes for a reason that is not about pixels at all: Custom Identity is
being retired, so from January 2026 the avatar is **inherited from the linked
TikTok account** and there is nothing for a client to supply. `tiktok_video`
keeps its id through its rename, the `billboard` rule.

**A target is not a ceiling, and the carousel is where that bites next.** The
kit publishes *"100 KB suggested per image"*, which is `target_bytes` and not
`max_bytes` — carried as `min_bytes` once already, in DOOH, where a clean 30 KB
billboard was refused against a number nobody published as a minimum. Read as a
maximum here it would refuse a 140 KB card the kit is perfectly happy with.

**And Snapchat is the case where the names were right all along.** It is the
one platform of the four whose two format names the kit still sells, so the
name pass would never have raised it and `_KIT_NAMES_PENDING` recorded it as a
count — *seven formats against our two*. Both of the two were nonetheless
refusing creative the kit allows, which is the third transcription in a row
where the numbers were worse than the names and the whole argument for working
the list down rather than waiting for a check to raise its hand. Video was
capped at **:30** against a published **:03 to 3:00** — the kit's own update
note says *"the 30-second cap is gone"* — so a :45 spot was refused outright.

**One fact, three numbers, and collapsing them is what refused the file.** The
kit publishes *"9:16, 1080x1920"* as the media spec and says beside it that
**720x1280 is the stated minimum, not the target**. Carried as one fixed
`size`, a perfectly legal 720x1280 file failed on dimensions. It is the
`gpt_ads_square` rule: a **required** ratio is a `ratios` entry and a fail, a
**recommended** build size is `min_size` and a warn — it runs, it just runs
soft — and the floor is `min_width` and a fail under it. Three answers,
because there are three questions.

**A unit specified by ratio still has to say what it is.** `units_line()`
already knew an image unit with no size of its own must be *named* rather than
folded into the run of sizes, because folded in it vanishes. Named alone it
reached the client document as **"or Single Image Ads"** — nothing saying
9:16, nothing saying 1080x1920, nothing saying JPG — which is the same silence
one step less complete. `_shape_of()` is what a unit carries when it has no
fixed size, and every social unit is in that position.

**And an optional extra must not lead.** The companion rule was keyed on
`radio_companion` deliberately, after firing on a *count* once and announcing
Snapchat's and TikTok's primary images as optional companions. There are two
of them now: an AR filter is the same shape as the radio companion — a sized
extra beside a buy whose ask is a 9:16 spot — and being the only sized image
unit there, it led the requirement, announcing an AR filter as the whole of
what the client owed us. `ADDITIONS` maps each to **its own words**, because
"a companion banner" is a true sentence about one of them and not the other.

**AR Filters stays one unit carrying two shapes** — a static 945x2048 PNG and
a moving 720x1560 GIF. Splitting it would invent two names the kit does not
publish, which is exactly what `kit_name_drift()` exists to catch. No file
weight is published for it, so none is invented.

**Three of the twenty are a different kind of gap, and it reaches the client
document.** Instagram Reels, Facebook Reels and the six CTV interactive
formats are sold by the kit and this module holds **no unit** for any of them
— not "we cannot parse that table" but "there is nothing here to judge one
against". So a Meta requirement listed Stories and never Reels and read as
complete, on the page the client is sent, while the kit itself says in as many
words that *"Facebook Reels and Instagram Reels are not interchangeable —
different file types, text limits and duration rules."* That is the Pinterest
failure one placement along: judged against the nearest thing rather than
reported as not measured. `_KIT_NOT_MODELLED` names them against the channels
whose presence puts them in play, and `required_units()` carries them in the
payload **and** on the one line `units_line()` prints — left in the note alone,
the requirement a client actually reads still looks complete. `measured` stays
true, because the units we do have are measured; what is withdrawn is the
claim that the list is all of it.

### A category heading is not a word about the product

`creative_needs.medium_of()` read a blob of `category + product + label +
description`, and the **label** is `"<category heading> — <product>"`. A
heading describes a *section of the card*, not the thing in it. The card files
four IP-targeting products under a heading called **"Display & Video"**, so
the word *video* appeared in the label of `IP Targeted Display - New Movers` —
whose own description reads "deliver **display** ads" — and the video test
runs before the display one. All three IP display products were gated as
video, which asks a client for a TV spot to run a banner buy. The label is out
of the blob; category and product were both in it already, so it contributed
nothing else.

**And "other" is not a medium — it is the gate never being asked.** Every
product under `MOBILE ONLY`, `EMAIL MARKETING` and `SMART 1 SIGNAGE` answered
`OTHER`, and an ungated medium is one the Creative step never mentions. A
signage buy reached the insertion order with nobody having established that
artwork exists, exactly as a CTV buy used to before the gate existed. Those
three headings are in `CATEGORY_MEDIUM` now and `EMAIL` and `DOOH` are gated
mediums with their own production figures — email creative is the card's own
$150 line, so it is questioned at $400 rather than $1,500, the per-medium rule
display already followed.

**And paid social was the largest hole of the three.** A Meta-only plan
returned *nothing* from `gated_media()`: six real buys — Awareness, Targeted,
Programmatic Paid Social, Retargeting, Leads and Boosted Posts — each with
three to seven units published in the kit, and the Creative step never
mentioned one of them. The tempting reading is that paid social is usually a
boosted post the client already has, and that is exactly the assumption this
module exists to stop making. `SOCIAL` is gated now, at the card's own
"Social Media Ad Creation per platform" of **$35** rather than a figure
invented here — low enough that its comp confirmation is in practice never
raised, which is the right outcome for a $35 line rather than a threshold
nobody would act on.

**And gating it made the word "social" decisive, which caught two things that
are not media buys.** `Social Media Ad Creation per platform` is the card's own
$35 **production** line, so the gate asked whether the client already had the
creative that line exists to produce; and `Social Media Management` is a
$199/month organic posting retainer that buys no advertising at all. Both then
printed *"the spec kit maps no unit for this"* onto the client's creative
section, and both counted their spend into the **social medium** — which is the
figure the comp confirmation is measured against, so a production line sitting
beside a Meta buy raised the number that decides whether comping it is
questioned.

They are named by **category** rather than by product, and that is the point:
the other four lines under CREATIVE / DESIGN SERVICES answer OTHER only because
they happen to contain no medium keyword, so the next production line added
there would depend on that luck. It is also already written down —
`SPEC_AGREE_EXEMPT` carries `("other", "email")` with the reason *a
creative-production line item is not a media buy that needs creative supplied
for it* — so this is that exemption's rule applied one heading up rather than a
new judgment. `spec_disagreements()` cannot see any of it: the kit maps no unit
for either product, and an empty kit is skipped.

**One product runs the other way.** The card files
`LinkedIn - Display & Text Ads` under a heading called **SOCIAL ADS - VIDEO**,
and the heading is what the keyword pass reads — so a product whose own name
says *Display & Text Ads* was gated as video, asking a client for a spot to
run text ads. It is named in `EXPLICIT_MEDIUM`, where `card_drift()` will
report it if the card renames it. The other five under that heading are left
alone: the heading is right about them, and reclassifying a generic "Paid
Social Media Advertising" on our own reading of which platforms are
video-first would be inventing.

**And the heading was answering for a platform the kit has never heard of.**
The same reading one step further on: `channels_for_product()` matches on the
category as well as the product, and **Pinterest** is on the card with no name
rule of its own — so "SOCIAL ADS - VIDEO" hit the `social ads?` pattern and a
Pinterest buy was asked for **Facebook and Instagram units**. Not a near miss.
The kit publishes no Pinterest section at all, Pinterest's feed is 2:3 and what
was asked for is a 1:1 square and a 9:16 story, so a client who supplied
*exactly what the requirement listed* delivers creative Pinterest crops — and
nothing errors at either end, because the sizes are real sizes and the request
looks like every other social requirement on the screen. Snapchat, TikTok, X
and LinkedIn each carry a name rule above that pattern and were right all
along; Pinterest was the one platform the category was answering for, which is
why four of the five looked like proof the reading worked.

**And the busiest social family on the card was taking the narrower answer.**
The `instagram` rule sits above the Facebook one and returns a deliberately
narrower list, because it was written for a product named only Instagram —
and every Meta product on this card is called **"Facebook | Instagram …"**, so
five of the seven took it and were asked for an Instagram image and a Story
and never for the Facebook feed, the Facebook video or the carousel. On the
one whose own name says *Video* that is worse than it sounds: `facebook_video`
was dropped from a video buy. Nothing errors — every unit returned is a real
Meta unit, just not all of the ones being bought — and the two products named
"Facebook - …" got the full set the whole time, which is why it read as
working. A product naming **both** platforms now gets the whole set, above
both rules, and `_META_CHANNELS` is written once: two hand-typed copies of one
list is how one of them comes to be missing the carousel.

**An image unit with no size of its own vanished from the requirement.**
`units_line()` folds image units into a run of sizes and describes everything
else, so a unit carrying no size contributed nothing at all and was silently
absent. Every social unit is in that position — the kit publishes a ratio and
a recommended resolution for those rather than a fixed size — so a paid social
buy's entire creative requirement, the one line a rep and the client document
read, said **"Stories Video (MP4/MOV, 0–120s)"**: four image units gone, and
nothing anywhere saying an image was needed. That is this function's own audio
rule running the other way — there a unit is described in the wrong terms,
here in none — and it went live the day the paid social gate did, which is
what made it worth finding. TikTok's Profile Image and two of X's units were
disappearing the same way.

**And "plus a companion banner" is a claim about one unit, fired on a count.**
It belongs to digital radio's optional 300x250 — named first it reads as the
whole requirement, which is how somebody sends a banner and no audio — and it
was triggered by *one sized image plus anything described*. So Snapchat's
Single Image Ad and TikTok's In-Feed Image, which are the primary image of
those buys, were each announced to the client as an optional companion to the
video: the same sentence costing the image instead of the spot. It is keyed on
the unit now.

It maps to **nothing** now, and that is the fix rather than a guess at the
nearest platform: `required_units()` already says *the spec kit maps no unit
for Pinterest* when it is handed an empty list, which is the rule this module
works to everywhere else — a format the kit maps no unit for is *not measured*,
never judged against the nearest channel. The gate is unchanged and still asks
whether the creative exists; only the claim about **what** it has to be is
withdrawn. `spec_disagreements()` skips an empty kit rather than reporting one,
so this is silent to the check that would otherwise have caught it — which is
why `test_proposal_spec.py` asserts it directly, and asserts the other four
platforms still reach their own units: the tempting edit is to widen the entry
to cover "the social ones", and that would take Snapchat's and TikTok's real
sizes away to fix Pinterest's absent ones.

**Two readings of one question, disagreeing in both directions.**
`medium_of()` decides *whether* to ask for creative;
`creative_specs.channels_for_product()` decides *what* to ask for. They
disagreed on **25 of the 90 products**. The kit had known about mobile, email
and signage the whole time. Going the other way, the four programmatic
**video** products filed under the card's DISPLAY heading are named in
`EXPLICIT_MEDIUM` so the gate asks for a spot — and the kit's regex list
dropped them into the generic `display|programmatic` pattern, so the rep was
asked for a video and handed *Leaderboard 728x90, Medium Rectangle 300x250*.
Each screen was internally consistent, which is why it survived.

`spec_disagreements()` is the check, and `/api/integrity` runs it at **high**.
Its exemption list is pairs that are genuinely both right, each with its
reason: a retargeting *buy* whose files are banners or Instagram units, and a
creative-production line item that is not a media buy needing creative
supplied for it. The new video pattern in `_PRODUCT_CHANNELS` sits **below**
the audio rule on purpose — "Programmatic - Targeted" is also the $18.00 CPM
buy under DIGITAL RADIO, and that one needs a spot rather than a video.

The tables are deliberately **not** merged. The wizard mirrors `medium_of` in
JavaScript, and reaching into the kit's twenty-entry regex list would be a
third mirror of one fact — the cost this codebase has already paid twice. They
stay separate and are held together by the check. `test_proposal_spec.py` runs
the mirror over **every product on the real card** rather than a fixture: a
hand-written list proves the halves agree about the rows somebody thought to
write down, which is exactly the set that was already right — none of the four
the label bleed broke were in it.

### The listed rate is what Smart 1 pays; the quoted rate is what is sold

Every rate on the card is the buy-side number and the builder was quoting it
straight through — so a proposal promised the client the card's own CPM and
the delivery table computed impressions **at cost**, with no margin anywhere
in the document and nothing saying one was missing. A $1,000 line at $4.25
promised 235,000 impressions on the client's own ROI table and could only ever
have bought half of them.

`rate_card.sell_rate()` starts every CPM and CPV line at **2×**, editable per
line, and `_sell_rate()` in the builder is the one server-side reading of what
a line is quoted at — the delivery table, the packages and the media plan all
go through it. A management-fee, flat-fee or custom-quote line has `rate_type`
of None, nothing to multiply, and is left exactly as the card lists it: that
is what "not managed by percentage" means in practice. The line's own rate
also wins over the looked-up card row, because `find()` matches on the product
name and four categories carry a product called "Demographic" — a location
lookback line resolved to the $4.25 display row and was costed against a rate
nobody quoted.

**And a rate with no `rate_type` is not a rate that is sold.** All four IP
Targeting products carried a bare `listedRate` of `"25.0"` with `rateType:
null`, in both copies of the card, while the page we publish sells every one
of them per **CPM** — `$18.00`, `$25.00`, `$19.50`, `$31.00`. Two things fell
out of that, in opposite directions, and each screen stayed internally
consistent, which is why it stood. `sell_rate()` answers `None` for a line
with no rate type, so the **buy-side rate went onto the proposal with no
margin on it**, beside a display line correctly doubling $4.25 to $8.50. And
`estimate_delivery()` answered *"25.0 — not an impression-based rate, so
delivery isn't estimated here"* about a $25 CPM buy, so the media plan quoted
IP Targeting with **no impressions** and printed the bare float at the client.

Nothing here could see it. `check_drift()` holds the two copies of the card to
each other and they agreed — both were wrong the same way. The only place the
unit exists is the published page, which ships in this repo, so
`test_proposal_spec.py` now holds the card to it: a product the page sells per
CPM or CPV that the Hub does not mark as one is a failure. It **also asserts
how much of the card matched**, because the names do not all join — the Hub
spells one product "purchased seperately" and the page spells it "separately"
— and a name comparison that quietly stops matching is a check reporting a
clean bill of health about nothing.

The five genuine flat fees beside them keep `rateType: null`, which is what
that means, and gained the rate string the page prints: `250.0` reaching a
client document as *"250.0 — not an impression-based rate"* is the same bare
float one row over. An existing quote is unaffected either way — a saved line
carries its own rate, which is the rule `_sell_rate()` already works to.

### What a goal leads with, and what a client reads it as

`findProduct(category)` meant "the first row the card happens to list under
that heading", and the card's order is the order somebody typed it in. Three
wrong answers came out of that, each of which reads to a client as a
deliberate recommendation.

**Run of Network led DISPLAY.** RON is $3.50 CPM of untargeted inventory — a
volume top-up to a targeted buy — and it was the display product every
awareness and traffic goal recommended first, on a document arguing that
Smart 1 targets precisely. `ADD_ON_ONLY` and `CATEGORY_GOTO` are data in
`hub/rate_card.py` now: programmatic (DATA TARGETED DISPLAY, "Select Tactics",
which builds the custom audience and carries retargeting with it) is the
go-to, and RON stays addable by name and is never chosen for anybody.

**"Demographic" led LOCATION LOOKBACK.** Four categories carry a product
literally called "Demographic" or "Behavioral", so the quote line said
*Demographic* where the tactic sold was location lookback — a client reading
it cannot tell which of the four they bought, and neither can the IO.
`quote_label()` puts the category in front of the ambiguous names and leaves
the self-describing ones alone: "Connected TV - Targeted" is not improved by
having OTT bolted to the front of it.

**"Social Ads" was the whole of paid social, next to Meta.** That category is
Facebook and Instagram *video*, LinkedIn, TikTok and Pinterest, so it is
**SOCIAL ADS - VIDEO** on all three copies of the card. `check_drift()` still
reports the shared card and the IO template agree.

Both of the first two are data rather than rules in the wizard, because the IO
reads the same card and the two documents must not disagree about what was
sold.

### The Suite is an option, and it says what it is for

It was quoted on every proposal at a tier picked purely from media spend, with
the client never told which of the things they said they were not doing it
closes — so the one line on the Investment Summary that recurs for ever had no
stated reason for being there. It can be left off the quote, its price
adjusted with the reason recorded **internally** (a discount nobody recorded
is a discount nobody can renew, and it is not the client's business), and
`current_marketing.suite_coverage()` answers three ways, never two: what this
tier covers, what a **higher tier** would (named with the tier — offering
smart webchat against a Smart 1 licence sells something the client cannot
switch on), and *not measured*, because an unanswered discovery question is
not a gap the Suite gets credit for closing. A capability is claimed **once**
however many questions want it: both social questions are answered by the
social planner, and listed separately they read as two things the licence
buys, on the one panel whose job is to justify a recurring charge.

### Every discovery answer changes something, and `unanswered_keys()` proves it

`socialPosting` had no suggestion rule behind it from the day it was written,
so a client who posts nothing produced a proposal that never mentioned it —
the exact failure `hub/current_marketing.py` was written to undo, sitting
inside the module that undid it. Social post *scheduling* was never asked at
all. Both exist now, and `unanswered_keys()` names any question with neither a
suggestion rule nor a Suite feature behind it. It returns an empty list today,
which is the only way it was worth adding.

The suggestions also reach the screen where products are **chosen**. They were
raised on the discovery step, three steps earlier, in a panel a rep reads once
and walks away from — so the media mix was built from the goals alone and the
answers were, in practice, read by the proposal copy and by nothing that
decides what is on the plan. Nothing is added automatically: a plan that grows
a line by itself is a plan somebody has to audit before every send.

### A ZIP exception is a rule, not a note

A radius does not stop at a state line and a campaign frequently does — a
client licensed in one state, a franchise with a protected territory, a dealer
whose registration only works one side of the river. The restriction lived in
an email, and the only two outcomes were a rep deleting a hundred ZIPs by hand
or the list shipping as it came back. Both are silent: nothing in either
document said the list was supposed to be narrower, so the proposal quoted
reach the client could not use and the IO trafficked into a state nobody was
allowed to sell in.

`target_areas.parse_zip_rule()` reads it the way it is said ("only New Jersey
zip codes", "exclude 46032, 46033", "everything except Ohio") against a table
of USPS **prefixes** — the first three digits are the sectional centre and
never straddle a state line, so a five-digit range table would need
maintaining every time a facility opens. Three rules on it:

- **A rule nobody could read is never silently ignored.** It comes back
  `understood: False` with the text kept, and every screen — and the client
  document — says *not applied* beside the sentence. A restriction that reads
  as saved and does nothing is worse than one nobody typed.
- **Filtering only ever removes.** "Only New Jersey" on a radius touching no
  New Jersey ZIP leaves nothing, and that empty result is reported as an empty
  result rather than falling back to the unfiltered list.
- **The found list is kept.** Loosening a rule, or fixing a typo in it, must
  not mean running the radius lookup again — that call is billed, slow, and
  the second answer would differ from the first.

`all_zips()` and `to_legacy_geo()` return the **running** list, because those
are what the IO's ZIP field and the Suite webhook read: a rule that narrowed
the proposal and not the insertion order would leave the document a client
signed and the campaign that was trafficked disagreeing, with both looking
correct on their own. The exception is part of an area's identity in the
dedupe key too — the New Jersey half of a Philadelphia buy and the
Pennsylvania half are two areas, and without that the second collapses into
the first.

The parse is a **round trip**, not a fourth JavaScript mirror. Target areas
and the creative classifier each carry one already and each needs its own test
proving the halves agree; a third, carrying every state's ZIP prefixes, would
drift silently and be wrong about which state a campaign runs in. The browser
stores what comes back on the area, so every later read is local.

### Competitors are named, age and income are a range

The audience step offered "Competitor physical locations" and "Competitor
conquesting" as two chips, and a ticked chip is not a campaign: nothing
anywhere asked *which* competitors, or which addresses. So the proposal
promised to target a client's rivals without naming one, the IO arrived with
the same two words on it, and the first person who had to know — whoever
builds the geo-fence — went back to the rep weeks later. Meanwhile the client,
the only person in the room who knows who they lose business to, was never
asked. A row with **no address is still kept**: conquesting by brand and
browsing behaviour needs no location, and refusing the row until somebody
looks up a street address is how the list stays empty. Nothing is inferred
from a name — no guessed website, no geocoding — the rule
`modules/ads_builder/logo.py` works to.

Age and household income were thirteen tick boxes, so "25-34 and 55-64 but not
the fifteen years between them" was two clicks away and reached the IO looking
deliberate, while a low and a high took five. And nothing said what *no chips
ticked* meant: the reach estimate read it as everybody and the IO read it as
nothing. Both are a low, a high and an explicit **none** now — none being a
real answer, and not the same as a range covering everybody, which is a
decision to buy the whole distribution and is priced as one. The stops are the
buckets the estimate is actually built on rather than arbitrary years: a
slider offering 37 implies we can size 37. The bucket lists are still written
alongside, because the IO, the Suite webhook and `/api/estimate-audience` all
read those.

### A form bound to a throwaway object is a form that reports nothing

Three reports came off the floor about "Where should we run this?" on one
afternoon, and two of them were one bug. **Pressing Back off a half-typed
target area emptied the list while `S._areaView` still said `"edit"`** — the
one state the step's own seeding skipped, because it seeded only when the view
was *not* edit. The editor renders `currentArea() || blankArea()`, so from then
on every control on the step was bound to a throwaway object: picking DMA set a
property on it, the redraw read the list, and the select snapped back to
City/ZIP + Radius with the radius at 10. Nothing threw. No console error, no
failed request, no toast — the step simply could not be filled in, and
`_areaView` rides in the saved quote, so it stayed dead across reloads and for
every later visit.

Two halves, because either alone leaves a route in. The step seeds **whenever
the list is empty**, whatever the view says; and `setArea()` **seeds rather
than returning** when there is no current area, so no route into that state can
leave the controls writing into nothing. `onBack` also clears the view it can
no longer honor: leaving `"edit"` standing over an empty list is what got
written into the quote. `test_proposal_targeting.py` runs the step's own source
in node — the seeding block and the `onBack` body are lifted out of the page
rather than restated — and asserts that picking DMA sticks.

### A model cannot visit a page, and asking it to is how it says so

The landing-page review handed a URL to a model with the word "Visit". No model
here can, so the answer was either a confident review of a page nobody had
looked at — fiction, quoted to a client — or, once the model was honest about
it, the criteria it *would* have used followed by a sentence about not being
able to reach the site. That is what the third report described. It is the
failure `modules/ads_builder/landing_page.py` was written to undo, in the
module next door: it **fetches** the page and counts the conversion points off
the markup, each carrying the evidence found. It is read here rather than
copied, and the response carries `observed` beside `review` so the screen keeps
the fact and the judgment apart. A page that could not be fetched is **refused
rather than reviewed anyway**, and a model that fails costs the judgment and
not the reading.

**And the IO Builder was asking the same model to visit the same kind of
page**, into a worse document. Its review is printed on the internal PDF under
*Landing Page Review — Internal Needs*, which is what whoever traffics the
campaign reads, so a review of a page nobody looked at is a fix list somebody
works. It reads `landing_page.observe()` too now, keeps `observed` and
`summary` beside the review, refuses a page it could not fetch rather than
reviewing it anyway, and refuses an empty review rather than filing one. The
headings line the prompt needs moved to `landing_page.headings_line()` — it
describes that function's own output, and two readings of one shape drift the
day either end of it changes.

### The hosted tool that was meant to help is what stopped the button

`_openai_response` attached `{"type": "web_search"}` to **every** call in the
Proposal Builder, including the rewrites and JSON drafts that have nothing to
look up. Whether a hosted tool is available depends on the model, and the model
is `OPENAI_MODEL` — set to a 4o-class model on this deployment, not the
`gpt-5-mini` the default in that function assumes. A model that refuses the
tool refuses the whole request, so the search that was meant to help the ZIP
lookup was what stopped it, and stopped seven other buttons with it.

It is opt-in now, and the one caller that asks for it **falls back without it**
rather than losing the answer: a list assembled with no live lookup is worth
having and is labelled; no list at all is a button that does nothing. Two other
ways of failing are named rather than guessed at. `raise_for_status()` was
discarding the API's own sentence, so every button reported a different
invented diagnosis of one shared failure — "the AI returned no description",
"No ZIP Codes were returned" — and none of them was checkable; the body carries
the reason now. And an **incomplete** response is said to be that: reasoning and
tool tokens count against `max_output_tokens`, so a truncated answer arrives
with an empty text body, which every caller read as its own kind of nothing.

**And the fix landed in one of the two copies.** The IO Builder had a
`_openai_response` of its own — same function, same unconditional
`{"type": "web_search"}`, same `gpt-5-mini` default — and was never touched, so
the identical diagnosis stayed live one module over for as long as it took
somebody to press a button there. All four of that tool's AI buttons were dead:
the ZIP-radius lookup, the business description, the landing-page review and
the media-mix recommendation, each returning a different invented account of one
shared cause. That is the drift `hub/storage.py` and `hub/images.py` exist to
stop, wearing a model call, and it is the whole argument for the opportunistic
migration rule: the next fix should land once. `hub/openai_responses.py` is the
one reader now and both builders read it — with the transport left as each
module's own name, so a test already standing in front of it goes on biting.

**Two of those four reported a truncated answer as a success**, which is worse
than the two that got it wrong loudly. The landing-page review answered **200
with an empty review**, and the wizard stored it and the internal PDF printed it
under a heading — a page nobody had anything to say about, rather than a review
that never happened. The media mix answered **200** with every field blank,
under a warning blaming the model for having replied in prose. Neither errored
at either end. An empty answer is refused by name at both call sites now, and
`purpose` travels with each call: every model call in that module was filed
under the string `"business_description"`, so the usage page could not tell a
billed ZIP lookup from a billed landing-page review.

### Generated copy is cleaned, not trusted

The models write Markdown by habit and nothing downstream renders it: the
preview HTML-escapes the body, the PDF escapes it into a reportlab Paragraph,
and python-docx writes it as literal text — so `**Reach**` reached the client
as `**Reach**`, three ways, on a document quoting five figures. Emoji arrived
the same way, in a proposal. This is the Smart 1 Labs rule one step on: the
instruction is in the prompt **and** `proposal_spec.clean_ai_text()` runs over
whatever comes back, and over anything typed into the section editor by hand —
or the rule holds only until somebody pastes. Copy is *cleaned* rather than
discarded, because a section is thrown away for naming Smart 1 Labs and a
stray asterisk is not that. Bold survives, normalised to `<b>`, which
reportlab reads natively, `rich_runs()` turns into a bold run for Word, and
the preview un-escapes deliberately and alone.

### The proposal a client can answer, and a count that means what it says

A proposal reached a client as a PDF and stopped there. Nothing knew whether
it had been opened, and the status was a pill a rep clicked from memory —
`sales_builder` declared no `PUBLIC_PREFIXES` at all, so unlike Scans, Smart 1
Ads, Calculators and Social, none of it was reachable by a client.
`/sales/builder/p/<token>` is the document they open, and the one thing on it
is **accept**.

**No edits from the client, deliberately.** Smart 1 Ads offers three answers
because an estimate is negotiated line by line; a proposal is a document
somebody says yes to, and a change request arriving here would be a second
inbox for a conversation the rep is already having. A client who wants
something different says so, the rep edits the quote, and the same link shows
the new version.

**The page embeds the PDF rather than re-rendering the proposal in HTML.**
That is why it is cheap: the PDF, the Word export and the preview are already
three renderers of one document, and a fourth would be the drift this codebase
has paid for twice. The client reads exactly what was signed off, and there is
nothing to keep in step.

**"Opened 3 times" is a sentence a rep acts on**, so `hub/view_tracking.py`
holds what an open is. Four ways that number quietly comes to mean something
else, and each is closed:

- **A mail security gateway opens every link in the message.** Mimecast,
  Proofpoint and the rest fetch a URL within seconds of delivery, before any
  human sees it — so an open is recorded by the **page reporting itself**,
  which a scanner fetching HTML never does because it runs no JavaScript.
  Counted on the request instead, every proposal reads as opened the moment it
  is sent: a confident wrong answer that stops somebody chasing a client who
  has never seen it.
- **The rep opens it to check the link works.** A signed-in Hub session is
  never counted, and the client page *says so on itself* rather than leaving
  the rep to trust it. This is the rule the feature was asked for with, and it
  is the one that would break silently — the count would simply be one too
  high, with no way to tell which one.
- **A reload is not a second read**, so a visitor is collapsed inside a
  thirty-minute window — **per revision**, because the first sight of a
  version the rep has just sent is a new read whatever the clock says. That
  scoping was a defect the test caught: a client opening a revision five
  minutes after it was sent counted as nothing.
- **Nothing stores an address.** `visitor_hash` is a keyed digest used for the
  window check and for nothing else; the panel shows counts, times and whether
  it was a phone. The rule `hub/auth.py` already applies to its lockout table.

**An acceptance is a statement about one revision**, so it is a row rather
than a flag. A quote revised after a client said yes does not carry that yes
forward onto a document nobody agreed to — the panel says revision 1 was
accepted and is now superseded, and the client can accept the new one. The
share token is minted once and kept for the same reason a revision does not
mint a new one: a link is already in somebody's inbox.

Three smaller rules it inherits. A rep cannot press Accept on the client's
page — an acceptance filed in a client's name that the client never gave is
worse than none. A name and an email are required, because an acceptance
nobody can attribute is not one. And **revoked, deleted and never-existed all
answer the same 404**, because a client-facing URL that says "this one
expired" tells somebody probing which tokens are real.

`test_proposal_share.py` asserts all of it, including that a client with no
Hub login can open the page, that the Hub's chrome is not injected into it,
and that a rep can read the PDF without marking it read.

### The rate card is ours, and Expected Results is a framework

Two things a client should never have read, on the document they decide from.

**The rate card was named four times on a proposal a client receives** — the
PDF's rate note, the seeded ROI copy, the preview's default and the growth
note — while `DIRECTIVES` had been telling the model not to mention it since
the day it was written. That is the shape this codebase keeps finding: a rule
policed in the prompt and broken by our own strings, where no generated copy
was involved at all. Naming it invites the one question the document cannot
answer — *can I see it?* — and turns a quoted price into a list price somebody
might have marked up. The strings are gone, the directive stays, and
`proposal_spec.client_safe()` runs inside `clean_ai_text` so the rule cannot
hold only until somebody pastes. It drops the **sentence**, not the phrase:
swapping in "our rates" leaves copy that is grammatical about half the time
("Rates follow our rates", "adding one starts at our rates minimum"), and a
client reads the mangling rather than the intent — the Smart 1 Labs precedent,
which discards rather than paraphrases into something nobody wrote.

**"Expected Results & ROI" was a table of impressions**, which answers a
different question from the one its own title asks. An impression count is a
delivery figure: it says what the money bought, not what the business gets.
The insertion order has carried a **KPI Framework** all along — a primary KPI,
the secondary ones, what is reported monthly, and what each product is
measured on with a normal result for it — so the two documents described one
campaign two ways, and the client agreed to impressions while the campaign was
run against KPIs.

`hub/kpi_framework.py` is that one description now, and the proposal's ROI
section renders it. Three rules in it. A benchmark is a **range labelled as an
expectation**, said once in the client's own words ("what this inventory
normally delivers … not guarantees"), because a single figure printed under a
heading like that reads as a promise. A product the table does not know falls
through to *track against the campaign objective* rather than to the
nearest-looking row — a display benchmark against an audio buy is a number
nobody can hit. And **"not measured" is an answer**: a campaign with no KPI
chosen says which step chooses one instead of printing a confident framework
built from an empty list.

The IO builder still draws its screen from its own JavaScript copy of that
table, so `test_proposal_targeting.py` parses `benchmarkFor` out of the
template and requires the two to agree in **the same order** — the order is
load-bearing, since "video" tested before OTT reads every Connected TV line as
YouTube. `expected_results()` is kept and no longer rendered: it is the one
place that knows the delivery arithmetic — the quoted rate rather than the
listed one, a one-time line spread across the flight — and its docstring says
why nothing draws it.

**A list of things a client is meant to weigh is a list.** KPIs, success
metrics and audience layers were each rendered as `", ".join(...)` into a
sentence, so six KPIs arrived as a comma string and the fourth — the one they
would have argued with — was skimmed past. `proposal_spec.bullets()` is the
half of the bullet rule that covers the lists the *code* prints, the way
`_one_bullet_per_line` covers the ones a model writes; it returns a string, so
it goes through `blocks()` and each renderer draws the list it already knows
how to draw. What is printed beneath the KPIs excludes the KPIs themselves —
the metrics list repeated all four of them a line later, and a reader who sees
the same four twice stops reading the second list.

### A bullet inside a sentence is not a list

`clean_ai_text` normalised the markup and left the *shape* alone, and the
directive it works to actually asked for the wrong thing: "write the items as
sentences or separate them with the bullet character •". So a model obliged
with `We will reach three areas: • Carmel • Fishers • Noblesville` — one
paragraph, which all three renderers duly set as one paragraph, and a client
read a sentence with dots in it on the page listing where their money goes.
Nothing errored; the copy was even correct.

The shape is enforced now rather than requested, the Smart 1 Labs rule one
step on: `_one_bullet_per_line()` puts every bullet at the start of its own
line and keeps the lead-in above it, and the directive asks for one item per
line so the model mostly gets there on its own. What the renderers read is
`proposal_spec.blocks()` — paragraphs and lists, decided **once** — so the
preview builds a `<ul>`, the PDF gives each item its own bullet-indented
Paragraph and Word writes a List Bullet paragraph, and a fourth renderer
added later cannot go back to printing the bullet inside a sentence. The
browser carries the same split in `cleanCopy()`, because a rep pastes into
the section editor and must see it normalise there rather than in the PDF.

One thing it deliberately does not do: a sentence written *after* the last
bullet stays attached to that item. Where a list ends is not knowable from
the text, and cutting at the first full stop would split "Carmel, IN. 10
miles" into two items.

### The map is the part of the proposal the client can check

Geography was a table of sentences — "Carmel, IN + 10-mile radius" three
times — and it is the one section a client cannot read against what they
know, because the person reading it lives there. A map answers in a glance
what three sentences do not: that the rings overlap, that the whole buy sits
on one side of the city, that the suburb they care about is inside it.

`hub/target_map.py` draws it: OpenStreetMap tiles composed with Pillow, rings
computed from each radius, numbered pins and a key, and the tile attribution
printed **onto the image** — three renderers show this picture and a credit
written into one of them travels with none of the others. One PNG serves the
preview, the PDF and the Word export, so the three cannot disagree about
where the campaign runs. There is **no JavaScript map**: target areas and the
creative classifier each carry a mirror already and each needs a test proving
the halves agree, and a fourth renderer of the same fact is the cost this
codebase has already paid twice.

Every rule in it is a way to be confidently wrong:

- **A named state must match.** A geocoder handed "Carmel" answers
  Carmel-by-the-Sea, California — so an origin naming a state and finding
  nothing in it comes back *not found*, never the same name somewhere else.
  A map of the wrong Carmel is a wrong answer that looks exactly like a right
  one, and it is on a document a client recognises.
- **A DMA, a state and a national buy are not drawn.** There is no boundary
  data here and inventing a blob is a claim about coverage nobody can check.
  Those are *named under the map* as covered and not drawn, so the picture is
  never mistaken for the whole buy.
- **Four kinds of missing are four answers.** Covered-but-not-drawable, a
  spelling nothing could find, an area with no origin at all, and a tile
  server that did not answer. Only two are somebody's to fix, so only those
  two are offered as something to fix — and they are shown on the areas
  screen, where the fix is, never on the client's document.
- **A failure costs the picture and nothing else.** No blank grey box, no
  "map unavailable" graphic: the client document simply omits it, and
  `render()` returns `(None, reason)` so the builder can say why. A map made
  mostly of missing tiles is refused for the same reason.
- **The URL carries a signature of the areas.** A stale map is the worst
  failure available here — plausible, dated, and about somewhere else — so
  changing a radius changes the URL rather than letting the browser serve
  yesterday's picture.
- **The picture is bounded on both axes, and the crop stays landscape.**
  Scaling to the text column's width alone is only a bound if the picture is
  wider than it is tall — and three rooftops running north-south crop to a
  *portrait* map, which at that width is 8.8 inches high: most of page two,
  a half-empty page one above it, and one slightly taller campaign away from
  a flowable reportlab cannot place at all, which fails the whole PDF rather
  than the picture. `MIN_ASPECT` widens the crop (never trims its height —
  that would cut a ring off the thing the map exists to show), counting the
  key that will be drawn underneath it, and `MAP_MAX_H` caps what the page
  will draw whatever arrives. The map and its caption are one `KeepTogether`,
  because a caption orphaned onto the next page is a sentence about nothing.
  Found by building a real proposal and looking at it, which no assertion
  about a PNG's bytes would have done.
- **Taking it off is said out loud, not left to an icon.** A picture provokes
  exactly one question — *that doesn't look right, how do I get rid of it?* —
  and the answer was a 🗺 drawn at 45% opacity in a row of five section
  icons, which is the note `hub/templates/diagnostics.html` and the Smart 1
  Ads estimate's per-section pencils already make about a quiet control. It
  is a line of words under the picture, the removed state offers its own way
  back, and the areas screen says where the removal happens so the two
  screens do not each answer half. `showMap` is still one flag on the areas
  section, read by the preview, the PDF and the Word export.

`MAP_TILE_URL` and `MAP_TILE_ATTRIBUTION` are settings, so a deployment with
its own tile server (or a keyed one — the key rides in the URL) needs no
second code path. No provider key is asked for: this deployment has never had
a maps key, and a page inviting a credential nobody has set reads as broken
while the feature works perfectly well without one.

The route's first version caught **every** exception around reading the
quote and answered "quote could not be read", which turned an
`AttributeError` on a wrong column name into a 404 that looks exactly like a
proposal with no target areas — the whole feature silently absent, with
nothing anywhere saying why. Only a malformed blob is caught now.

### Eleven rooftops is not eleven trips through the area editor

The list already exists — in the email, the spreadsheet, the client's own
store locator — and typing it back in one box at a time is where a
multi-location campaign loses its third location. `target_areas.parse_paste()`
reads a pasted block, and `parse_places()` does the same for the competitors
and venues inside it. Deliberately two readers: a competitor line is a
business and an address, an area line is a geography and a radius, and one
parser trying to be both reads "Riverside Dental, 1200 Main St" as a city
called Riverside Dental.

Three rules, each about the way a paste goes wrong quietly:

- **Nothing is added by the reading.** The rows come back with a sentence per
  line saying how each was read and a rep presses Add. A paste that silently
  assumed ten miles on eight of twelve lines is eight decisions nobody made.
- **A line nobody could read comes back by name.** Twelve lines producing
  nine areas is a campaign missing three locations nobody can see are
  missing. The rule `knack_websites.py` applies to a value Knack would refuse.
- **A place is short.** A comma is not evidence — prose has commas — which is
  how "not a place at all, just a sentence somebody typed into the wrong box"
  became a target area with a ten-mile radius drawn on it. Over six words
  with no ZIP Code in them reads as a note.

A location already on the campaign is reported as a duplicate rather than
added twice, because pasting the whole list after adding one by hand is the
ordinary case.

### Who to go after is researched, and stays a suggestion until somebody ticks it

The client is the only person who knows who they lose business to, and they
are not in the room when the proposal is built — so the competitor list was
whatever the rep could remember. `/api/find-targets` researches it over the
web, scoped to the target areas already on the campaign, and refuses to run
at all with no area on it: a search with nowhere to look comes back with
national brands.

Everything it returns arrives `accepted: False`. Printing a researched list
on a proposal is us telling a client who their competitors are on a model's
say-so, and that is the paragraph a client checks hardest — the same rule
`modules/ads_builder` applies to its own competitor research. An address is
carried only where the model gave one, is labelled **unverified** on the
screen, and is never derived from the name: a geo-fence built on a wrong
address spends the budget outside somebody else's front door, the rule
`modules/ads_builder/logo.py` works to. A row with no address is a real
answer rather than a gap — conquesting by brand and behaviour needs no
location — and the screen says so in those words.

And the two empty answers are kept apart, for the reason
`connected_accounts_result()` gives in Google Finder: **"we could not look"**
is a 502 that says the campaign is unchanged, **"there is nobody worth
naming"** is a 200 that says so in as many words. Only the second one means
stop looking.

### Who built it is not who is on it

The proposals list showed `salesperson`, which is the sales contact *typed
onto the proposal for the client's benefit* — blank on most drafts and
sometimes somebody else's name entirely. So "who wrote this?" had no answer on
the one screen the question is asked. `created_by` is read off the Hub session
at creation and never rewritten, so the column cannot quietly become "last
touched by" while the heading says Created by; an uploaded proposal answers
the same question with its own field, and a row from before it was recorded
says *not recorded* rather than showing a blank somebody reads as nobody.

### The pipeline was knowledge the Hub had and told nobody

`hub/sales_status.py`, the **Proposals** card on the dashboard, and
`/api/sales/scoreboard`. Three phases of work gave the Hub real knowledge
about every proposal — who opened it and how many times, whether the pricing
still stands, whether the client accepted, what the campaign costs — and all
of it was readable only inside the Proposal Builder. The dashboard everybody
opens carried eleven KPIs about *live* business (clients, live products, live
budget, websites, billing) and **not one figure about pipeline**: nothing
quoted, nothing waiting on a client, nothing won and not yet trafficked. There
was no scheduled sweep either.

That is the shape `hub/social_status.py` already answers next door, and its
note applies word for word: there is no mailer in this Hub, so the honest
route is putting it where people already look.

**Five signals, kept apart.** *They have not opened it*, *they read it and
said nothing*, *the price lapses this week*, *the price has lapsed* and *they
said yes and nobody wrote the order* send somebody to five different actions,
and one "needs attention" figure covering all five is a figure nobody can act
on. A quote lands in exactly one of them.

**It reads the open book and nothing else** — Draft, Sent and Approved. A
Converted or Lost quote is finished, and walking every quote ever written on a
page that loads on every visit is the cost that gets a number turned off.

**A count is never a link to a page that cannot show it.** Each figure carries
`?focus=<signal>`, and the builder narrows its list to exactly the ids that
reading counted — not to a status tab that is nearly the same thing — saying
what it is showing and how to leave it. An empty bucket leaves the whole list
rather than an empty table that reads as a book with nothing in it.

**Each zero says which kind of zero it is.** "Nothing is waiting on a client"
and "no client link has ever been sent for any of these" render identically as
a nought and only the second is somebody's to fix.

**One reading, two screens.** The Proposal Builder's own dashboard is handed
the same block by `/api/dashboard`, because two screens answering "what needs
chasing" separately is how they come to disagree in front of the same rep —
the `/api/db/structure` versus `/api/integrity` trap. And the route is
deliberately **not** a Utilities path: the presence headcount already showed
what happens when a figure everybody sees is served by a path most accounts
are refused.

**Nothing is written anywhere.** It does not touch a quote, it does not record
having looked, and it deliberately sends nothing to Smart 1 Suite — a nudge
onto a client's CRM record is a different decision from surfacing a number on
our own dashboard. `test_sales_status.py` asserts that from the **AST** rather
than the text, because the module's own docstring names `ghl_hooks.py` as the
precedent it follows and a check that reads prose as a call site reports the
explanation as the defect.

### One proposal, three monthly figures, and a fourth on the insertion order

`campaign_cost()`. `summarize_into()` took `monthly_budget` from the selected
package or from `state["budget"]` — the number typed on the Budget step, which
is what the client **asked for** — while the media plan totalled the lines
actually being bought and `ioDataPayload()` billed those same lines. Editing a
line is the ordinary case, and the moment one is edited the document says four
different things:

| On the document | Monthly | Campaign |
|---|---|---|
| Cover | $8,000 | $48,000 |
| Media Mix & Budget Allocation | $5,750 | $34,500 |
| Investment Summary | $8,000 of media | $51,594 |
| The insertion order | $5,750 | — |

$2,250 a month between the document a client signs and the order that bills
them. Nothing errored, and every screen was internally consistent, which is
why it survived.

**The plan is the number.** Once there are line items they are what is being
bought, and the cover, the media plan, the investment summary, the packages,
the IO and the dashboard's pipeline all derive from `campaign_cost()`.

Four rules in it. **Recurring and one-time are never added together** — a
$1,500 shoot is not $1,500 a month, and the old `sum(i["dollars"])` fallback
said it was. **A line runs for its own term**, read once rather than in each
caller. **The Suite licence is not campaign cost**: it is a separate product
with its own line, and blending it is how a client comes to believe the
platform stops costing money when they pause the media. And **a quote with no
plan yet still answers**, with the ask, so nothing has to branch on whether
there are items.

**The working budget follows the plan, and what the client asked for is
kept.** `syncBudgetToPlan()` runs from both editors and from a change of
basis, because otherwise the number depends on which screen the rep happened
to edit the plan from; `budgetAsked` records the conversation and is never
overwritten; and the Budget step **says when the two have parted company**,
because a rep who set $8,000, built a $5,750 plan and came back reads $5,750
with no explanation and assumes the tool lost their answer. The **Recommended
package is the plan exactly** rather than the plan rounded to the nearest
$250 — a package table saying $5,750 beside a media plan saying $5,730 is the
disagreement this whole change exists to end.

**Every figure is labelled with its scope, and same-scope figures agree.**
There is no single number for two different questions: the cover and the media
plan's totals row are the campaign's own cost, and the Investment Summary adds
the licence and says *including licensing* on its total. What is forbidden is
two figures with the same label disagreeing.

**Two more figures the same mistake was hiding.** `creative_needs.medium_spend()`
multiplied *every* line by the flight, so a one-time production read as six
times its cost — printed on the client's creative section, and, worse, it
switches the comp confirmation **off**, since that question is only asked where
the spend is *below* the threshold, which is exactly the small campaign it
exists for. And the **Recommended Channel Strategy** table listed every line,
so "Video Production — top of funnel, builds awareness and trust on the screens
the household already watches" and "Management Fee — supports the campaign"
were printed on a document a client reads; `channel_lines()` keeps the lines
that are channels, and the preview filters through the same reading rather than
keeping its own.

**And the insertion order was handed the buy-side rate.** `lineForIO()` sent
the card's own rate, so an IO read `CPM 4.25` for a line the client had been
quoted at $8.50; it sends `sellRateOf()` now. Its management fee field asks
for "an amount, percentage, INCLUDED, or NONE" and was being handed
*"Yes — per rate card"* — not an amount, and our pricing sheet named on a
document that reaches a client. It is read off the plan's own fee lines, or
**NONE**, which is a real answer and the word that field expects.

`test_campaign_cost.py` asserts all of it, including that the browser keeps no
copy of the arithmetic.

### The insertion order is a record, not a PDF and a webhook

`hub/io_records.py`, the **Orders we have sent** card on Client 360, and
`/api/client/orders`. Submitting an insertion order allocated a number from a
Postgres sequence, built two PDFs into Cloudinary, wrote one line in the
activity log and POSTed the whole campaign to Smart 1 Suite. **Then it kept
nothing** — no orders table, no list of what had been sent, no way to reopen
one, and no answer to "what have we written for this client" until the campaign
appeared in Knack weeks later. A rep asked what went out in July opened
Cloudinary, or asked whoever built it.

Three things followed from that and all three were live. `hub/io_reconcile.py`
had to be assembled out of the **activity log**, which rotates — so the one
report about orders that were never trafficked could see only as far back as
the log did. That log line carried an **empty order number** on every entry the
route had ever written and nothing noticed for months, because nothing read it.
And the log line, the client overlay and everything else were written at the
**top** of the route, *before the request was validated* — so a submit refused
for missing documents still logged an order and still registered the client,
and the reconciliation would have reported it as a campaign nobody set up. All
three are one `_keep()` now, reached at every exit past the point where the
documents exist, so the three records cannot disagree about what was submitted.

Five rules on the store.

**One file per order, never one file holding all of them** — the
`hub/drafts.py` rule, and the stakes here are a signed document rather than a
draft. **A resubmission updates the order rather than adding a second one**: a
correction sent an hour later is the same order at a new revision, and two rows
under one number is how a client record grows three identical entries with no
way to tell which is current, which `upsert_from_ghl` learned from GoHighLevel
first. Each attempt is appended to a short history, and the **first**
submission's date survives — an order written in July must not be re-dated to
the day somebody fixed a typo in September.

**The row is written whether or not Suite took it.** An order the client has
been sent is an order, and "delivered" and "built, and Suite refused it" are
different states that send somebody to different places. That is three facts
rather than one: `delivered` is what the latest attempt did, `ever_delivered`
is whether Suite holds *any* version — a correction that failed after a first
submission that landed leaves Suite holding the old one, which is real and is
not the same as an order that never arrived — and only the second means the
order reached neither system.

**What is stored is the agreement, not the wizard.** The campaign state is tens
of kilobytes of answers, working notes and generated copy; a record has to
answer who, what, when, how much and where the document is. Lines are capped,
the row is capped, and a row too large drops its **lines** rather than being
refused — who and how much are what it exists for, and a record refused for
size is an order with no trace at all.

**And a number handed out that never became an order is deliberately not
tracked.** The sequence issues one at the *start* of the wizard, so an
abandoned IO burns a number and leaves a gap in the numbering — and nobody
here asks about those. A note recording them was built and then removed:
machinery kept alive for a question nobody puts is machinery to maintain, and
this file already counts five integration points that were declared and never
wired. This store records orders that were sent.

**The reconciliation reads the durable half now**, so its note stops saying the
activity log is the horizon — that sentence was true and would have gone on
being printed while understating what the report can see. It also names the
orders Suite never took and the numbers that never became orders.

**On the client record it is deliberately its own card.** Products & IOs is
Knack's answer to *what is running*; this is the Hub's answer to *what we
sent*, and the two exist at different moments — which is the whole point for a
client written up on their first IO, whose record is otherwise empty until
somebody sets the campaign up. Whether Knack has the campaign is read off the
products already on the page rather than from a second reconciliation that
would come to disagree with the first, and the flag is **not drawn at all**
when that card did not answer: absent data must not read as a finding. The
route is under `/api/client/` because `hub/suite_embed.EMBEDDABLE` allowlists
that prefix — a card pointed anywhere else renders on every screen except
inside the Suite frame, and fails silently there. `test_io_records.py` asserts
all of it.

### What is mapped in Knack, and what is still somebody's assumption

`hub/knack_map.py` and **QA → Data Quality → Knack Field Map**. Knack is the
system of record and this Hub reaches into it from nine modules, and there was
no one description of what it thinks each object and field is — so "which
mappings have we confirmed?" could only be answered by reading nine files and
holding the answer in your head. That question matters far more the moment
more is pushed into Knack: a field id pinned to the wrong column writes into
the wrong place on a live record, and Knack refuses the **whole** record over
one bad value, so an unconfirmed mapping costs the write rather than the field.

**It is not a second copy of the field ids.** Nine modules pin them and each
owns its own; a copy here is the drift `hub/config.py`'s ALIASES table and the
two rate cards have each paid for. `fields()` imports from the owning module —
`knack_api.field_ids()`, `knack_api.SUPPORT_FIELDS`, `knack_websites.FIELDS`,
`ad_copy.field_ids()`, the `knack_products` constants — so a field repinned
there moves here with no edit, and one that stops existing cannot linger in a
table nobody re-read. `test_knack_map.py` asserts that by repinning a constant
and requiring the map to follow, and asserts the file carries no `field_<n>`
literal of its own.

**What lives here is the part no module holds**: which object each map belongs
to, which tool creates the records, whether the ids are pinned or matched by
label, and whether a person has confirmed the mapping against the live builder.
Today that is **110 fields across 7 objects, 64 of them written**.

**A field is confirmed once, against the object, and every tool inherits it.**
Object 135's monthly cost is read by Client 360, the scorecards, the billing
reports and the IO reconciliation; checking it four times is four chances to
disagree. So a confirmation is keyed on object and field rather than on tool.

**A confirmation is a person, a date and a field** — never an object. "We
checked object_153" is the kind of assurance nobody can act on later, and the
point is to be able to say which of the eighteen were looked at. **The id is
stored with it**, so repinning a field *retires* the tick and the row says
**superseded** rather than carrying a confirmation from one column silently
onto another, which is the single way this record could become worse than
having none.

**A field matched by label is a finding, not a mapping.** `object_140`
(Campaign Change Requests) is still matched by label and is a **write target**;
`hub/knack_api.py`'s own comment says why that is dangerous — a renamed label
breaks label matching silently, which is exactly the state `object_107` was in
before its ids were pinned. It is reported as unpinned rather than listed as
though it were confirmed.

**Without Knack the live check is not measured, and the report is still
measured.** `verify()` reads the schema and says what Knack calls each id; with
no credentials it refuses rather than drawing ticks nobody earned. But the
report itself still answers, because the map *is* what it exists to show and
calling the whole thing unmeasurable would hide the record somebody is meant to
work down. The two halves are said apart.

**Nothing here writes to Knack.** It reads the schema and writes one small
Hub-side overlay of confirmations; the test asserts that from the AST, and that
the module does not import `requests` so it could not reach an API by accident.

**The five write paths in daily use are not gated on this.** Tickets, campaign
support, ad copy, the dashboard-URL button and the website record are live, and
switching them off until a hundred and ten rows are ticked would break working
tools to make a record tidy. What the map does is say which of them are running
on a mapping nobody has confirmed — 64 of 64 today — so that is a list to work
down rather than a gate that fires on the wrong day.

### An order we sent, and the campaign nobody set up

`hub/io_reconcile.py` and **QA → Data Quality → Orders With No Campaign**.
Submitting an insertion order does three things: it writes an activity-log
entry, it registers the client as an overlay when nobody has heard of them
(`hub/io_clients.py`), and it POSTs the order to Smart 1 Suite. Then the IO
Builder's job is over, and **nothing ever checked that the campaign was set
up**. An order signed in March whose products were never written into Knack
looks exactly like one that was: the log says it went, the overlay row goes on
standing in for a record that never arrived, and Client 360 keeps saying the
cards are empty because there is nothing to read — which is the sentence
`io_clients.py` added for a client who is *new*, not for one whose campaign
was dropped. Nobody is billed, nothing is trafficked, and the first person to
find out is whoever eventually asks why a client we wrote an order for has no
products. Both halves were already here — what we sent is in the activity log
and, for a converted proposal, on the quote; what landed is on Knack's
products, each carrying its IO number — and nothing compared them.

**Underneath it was a defect that would have made the report useless on the
day it shipped.** `submit_io()` logged `order=_body.get("order_number")`
against a payload whose key is `orderNumber`, so **every `io_submitted` entry
that route has ever written carries an empty order number** — while the
`client_registered` entry written three lines below it, through
`io_clients.register_from_io`, read the real key and got it right. Two readers
of one payload, the wrong one is the record a reconciliation depends on, and
nothing errored at either end. The entry now also carries the media partner,
the flight start and the monthly, because a chase list needs to know who to
ask and whether the campaign should already be running, and neither is
knowable from an order number.

**A source that could not be read is not measured, and this is the strongest
case of that rule in the Hub.** `knack_products.rows()` never raises: it falls
back to a stale cache, then to the private fallback, then to nothing. Read
against the fallback — a snapshot refreshed out of band, whose rows are the raw Knack
records rather than `_row()` output and so carry no IO number at all — *every*
order reads as never trafficked, which is a report accusing the whole traffic
team on the strength of a stale file. So the products must have come from
Knack itself, or this answers `measured: False` and says why, and
`report_cache` never freezes that into the shape of "there is nothing to see".

**An order newer than the product read is not judged at all.** A stale cache
is a real Knack read of an earlier day, and an order written after it was
taken could not appear in it however long ago it was sent — so those are
counted as waiting with the reason named, rather than the whole report being
refused over a cache that is perfectly good for everything older than itself.
**An order submitted this morning is not late** either: setting a campaign up
is not same-day work, so `GRACE_DAYS` is a week, and a report that fires on
every order the day it is written is one nobody reads.

**"Late to be set up" and "should be live right now" are two different
conversations.** An order whose flight has already started is running in
nobody's system — not trafficked, not billed, and the client is expecting it —
so those sort to the top, are counted apart and are drawn red, while a merely
late one is amber. A page of red is a page people scroll past.

**An order with no number is its own finding, not a missing campaign**: there
is nothing to look up for it, which is a different thing to do about it. And
**the activity log rotates**, so the note says how far back it can see rather
than implying it looked at everything — a converted proposal is the half that
does not rotate, because those live in the quotes table.

**A row somebody has settled leaves the list, and the mark is applied on
read.** Some orders are never going to appear: cancelled before trafficking,
renumbered, a test. Left in, they are permanent red on a report whose whole
job is to say what to act on this week — the failure `hub/creative_evergreen.py`
was written for — and the mark is read on every run rather than baked into the
cached rows, because there are two gunicorn workers and one folded into a
cached payload is a button that appears to do nothing to whichever worker did
not take it. The reason is one of a short list rather than free text, the
control reads that list off the payload so a screen cannot offer what the
write refuses, and the mark records **who and when**: a decision about a
campaign that nobody can attribute is one nobody can revisit.

**Nothing here writes to Knack, to Smart 1 Suite or to a quote.** The settle
mark is a small Hub overlay through `jsonstore` and everything else is a
reading. `test_io_reconcile.py` asserts all of it, from the AST rather than
the text — this module's own docstring names Suite and `io_clients.py` as the
things it does not touch, and a check reading prose as a call site reports the
explanation as the defect.

### And is it the campaign we sold?

`hub/io_reconcile.delivery()` and **QA → Data Quality → Campaigns Not At Order
Value**. *Orders With No Campaign* asks whether a campaign exists; this asks
whether it is the one that was sold. It is the next link, and the one
`hub/io_records.py` made possible — before the order record there was nothing
on our side to compare against, because the only trace of an order was a log
line carrying a number and a client name.

**The finding is the money, and the counts are never the finding.** An
insertion order of six lines may be trafficked in Knack as six product rows or
as one, and nothing readable from here says which convention this book
follows. A check that fired on every order because the shop writes one row per
campaign is a check somebody switches off within a week — the note
`hub/qr_codes.py` makes about a warning that fires on every social spot. So
the line counts are printed beside each row as context and no row is ever
raised for them; what is compared is the **monthly**, which is the same number
however many rows it was split across.

**Both figures are always shown, and so is the difference.** A report that
says "discrepancy" without printing the two numbers behind it is one nobody
can check, and the first person who finds it wrong stops reading the rest.

**Over and under are different conversations.** A campaign trafficked for less
than the order is delivery a client paid for and is not getting, and is drawn
red; one trafficked for more is billing nobody wrote an order for, and is
amber. They are counted apart and the row says which.

**A tolerance, and it is ours.** Nobody publishes one, so
`MONEY_TOLERANCE_PCT` / `MONEY_TOLERANCE_MIN` carry `TOLERANCE_SOURCE =
"house"` and the page says so in words — the rule `HOUSE_LEGIBILITY` in
`services/abcd_service.py` already works to. A campaign trafficked to the
exact dollar is not the normal case: a rounded rate and a part first month are
ordinary, and calling every one of those a finding is how a list stops being
read.

**A product row with no monthly cost is never counted as zero.** A blank there
would drag the campaign's total down and read as under-delivery invented out
of a field nobody filled in, so an order with any such row is *not measured*
and is listed with the reason — and its "In Knack" cell is a dash rather than
the partial total, because a figure printed beside that sentence is one
somebody reads as the answer.

**An order with no campaign at all is left to the other report.** Raising the
same order on two screens is how a reader learns the two disagree. It
inherits the rest: the products must have come from a live Knack read, an
order newer than that read or inside `GRACE_DAYS` is not judged (a campaign
part-entered is not a campaign short-delivered), and a settled order is out of
both. `test_io_reconcile.py` asserts all of it.

### A price with no end on it

`hub/quote_validity.py`. `VALID_STATUSES` has carried **Expired** since the day
it was written — a badge color, a ⏰ in the status picker, and nothing anywhere
that set it, so it was reachable only by a rep remembering to click it, which
in practice meant never.

That was cosmetic until the client got a link. `/sales/builder/p/<token>` lets
a client accept a proposal themselves, and the accept route checked that the
link was live, that the reader was not staff, and that this revision had not
already been accepted — and **nothing at all about when the quote was
written**. So a March link could be accepted in September at March's rates,
filed as a clean acceptance with the client's name and a timestamp on it,
while the rate card and the sell multiplier had both moved underneath it.

Six rules, each a way to be confidently wrong:

- **Only a document the client was given can expire.** A Draft was never
  sent — an old one is *abandoned*, a different word and a different thing to
  do about it; an Approved quote is one they said yes to, and expiring an
  acceptance takes back an agreement; Converted has an insertion order behind
  it. `Sent` and nothing else.
- **Derived on read, never stored** — the `hub/creative_evergreen.py` rule.
  Two gunicorn workers, so a status written by whichever one ran a sweep is
  one the other disagrees with, and a stored `Expired` would survive an
  extension: a quote reading as dead on the one screen a rep would go to
  revive it. `status` stays exactly as stored and `shown_status` is the same
  fact with the clock applied, so nothing can round-trip a derived value into
  the column.
- **The clock starts when the client could first see it**, which is the send
  rather than the writing — and *which date answered* is carried and printed,
  because "thirty days from when I sent it" and "from when I wrote it" are
  different promises and the client is holding one of them. Re-sending
  restarts it, since a re-send is the current document at current rates.
- **A quote with no date at all is not measured**, never expired. An absent
  timestamp reading as "expired today" would refuse an acceptance a client is
  entitled to give.
- **The client is never turned away.** Past the date the page says so *above*
  the document — the embed is 78vh tall, and underneath it a client reads four
  pages and only then finds out the price is stale — and names who to ask, with
  the accept form replaced rather than the page 404ing. A revoked or invented
  token still answers 404, because saying "that one expired" tells somebody
  probing which tokens are real; an expired quote is a real quote belonging to
  a real client who is trying to say yes. The accept route refuses in the same
  words, so the rule is not one the form merely keeps.
- **A window a rep chose is refused when it is out of range, not clamped.**
  Somebody who typed 3650 and got 365 has been told something different from
  what they asked, on a date a client relies on. It lives in the quote's own
  data blob — `create_all()` adds no column to an existing table — and is set
  from the share panel, where the send happens.

The follow-up nudge on the dashboard says which kind of follow-up it is:
"chase this" and "this one needs re-quoting before they can say yes" are
different jobs, and the second has a client sitting in front of a page that
will not let them accept.

### Two systems disagreeing about whether a proposal was won

`hub/ghl_hooks.sync_quote_status()`. The push into Suite has always recorded
`suite_opportunity_id` on the quote and **nothing ever read it back**: a deal
marked Won in Suite updated the client's Proposals card and left the Proposal
Builder's dashboard — the screen a rep actually looks at — still saying Sent.
Neither screen said the other existed.

Four rules. It matches on the **opportunity id and nothing else** — never the
client name, which for a client with three quotes is the guess
`hub/client_key.py` exists to refuse. **Only the decided outcomes write**
(`won` → Approved, `lost` → Lost): "open", "quoted" and "viewed" tell us
nothing the Hub does not already know better, and letting them write would
walk an approved quote backwards to Sent because somebody dragged a card in a
pipeline. **Converted is never moved** — an insertion order exists, Suite has
no way to know that, and it is the one change nobody could undo from either
screen. And **a status that changed by itself says who changed it**, on the
quote's own activity strip as well as in the Hub log, or a rep reading "Lost"
has no way to find out why. It reuses the module `wsgi.py` loaded (`salesb_app`)
rather than importing a second declarative mapping of the same tables, and it
can never fail the webhook: the client card is written either way, and
GoHighLevel retries a non-2xx.

### Delivery figures belong under the media plan

`media_plan_rows()`. Impression counts came off the client document with
"Expected Results & ROI", correctly — an impression count answers what the
money *bought*, not what the business gets, and printed under that heading it
read as a promise about outcomes. But it left the proposal with no answer to
the question the media plan itself asks, and a client comparing two proposals
had no way to tell a $4.25 CPM apart from an $8.50 one.

It is `expected_results()`'s arithmetic and never a second copy: the **quoted**
rate rather than the listed one, a one-time line spread across the flight and
labelled *once* rather than multiplied by it under a per-month heading, and a
management fee reporting **no units at all** rather than a plausible number.
The words travel with the figures — an estimate printed bare reads as a
guarantee — and the lines that are *not* in the headline total are named
rather than quietly under-reporting the campaign.

**One reading, three renderers.** The PDF drew five columns and the Word export
drew four, so one client's proposal already said two different things depending
on which file was sent; both read `media_plan_rows()` now, and the builder's
preview is handed the server's rows on the quote payload rather than carrying a
**fourth** copy of the arithmetic — the mirror this codebase has paid for twice.
A row priced against a budget that has since been edited reads *recalculating*
rather than stating a confident wrong number.

**And the copy above it was contradicting it.** The seeded media-plan paragraph
said "every rate is the Smart 1 card rate — there is no markup between the line
item and what runs", printed directly above a table quoting CPM at 2x, on a
document a client reads. `client_safe()` was written against "the rate card"
and this said "card rate", so the rule passed a sentence written for exactly
it; it matches both orders now, and the seeded copy says what the split is and
nothing about markup.

### An interruption cost the work in one builder and the place in the other

`hub/drafts.py`. Fifteen minutes of concentration is what an insertion order
or a proposal takes, and a rep almost never gets fifteen uninterrupted
minutes. Both builders already had half of the answer and each was missing
the other half, in opposite directions.

**The Proposal Builder saved the work and lost the place.** It autosaves to
the server on every keystroke, so nothing was ever lost — and `editLoaded()`
set `step=0`, so reopening a quote put the rep on step 1 of 14 pressing
Continue until they found the media plan they had been on. The position is
`S._step` now, stamped on every save and read back on open. **Inside the
quote's own data blob, never a new column**: `create_all()` adds no column to
an existing table, so one here would be silently absent on the live Postgres
with every local test green — the `client_key` rule. It is **clamped** on
read, because that number was written by whatever version of the wizard saved
it and an index past the end throws inside `renderStep()`, which is a blank
builder over a quote whose answers are all perfectly intact. And arriving on
step 7 **says so** and offers step one: a wizard that opens somewhere other
than the beginning with no explanation reads as having skipped ahead, and Back
from there is six presses. The proposals list carries it too — *Left at step 7
of 14* against a Draft, because "which of these was nearly finished?" is the
question somebody scans that list asking.

**The IO Builder kept the place and could lose the work.** Its draft went to
`localStorage`, which is the right instinct and survives exactly one browser:
somebody interrupted on their laptop and picking the IO up on a different
machine found no draft at all, and — worse — nothing on any screen said an
unfinished IO existed, so it was simply started again from the top. The local
copy stays, because it is instant and it is nearly always the right one; what
is new is a copy on the **server** and a list of them on the start screen.

Six rules in the store, each a way a draft quietly becomes a liability:

- **One file per draft, never one file holding all of them.** Two reps
  autosaving at the same moment would each write the whole collection back and
  the second write would drop the first one's work, which is precisely the
  failure a draft store exists to prevent.
- **Through `jsonstore`, so it outlives the disk** — and deleted through
  `jsonstore.delete_json`, never `os.remove`, or the mirror restores it and
  the discard undoes itself.
- **Nothing in it may raise.** A draft is insurance and insurance that breaks
  the thing it insures is worse than none, so every entry point returns a
  value, every route answers 200 with what happened in the body, and an
  autosave that could not write costs the server copy and never the IO
  somebody is in the middle of.
- **Bounded, and never in silence.** A per-owner cap and a per-draft size cap,
  because an autosave loop that fills the 5 GB disk takes the whole Hub with
  it — and when the cap drops the oldest draft the save **names it**, since a
  draft that goes missing quietly is the thing the feature exists to stop.
- **A colleague's unfinished IO is on the list too**, marked whose. Hiding it
  is how the same insertion order gets built twice. What the listing does not
  carry is the state blob: it is read into a page, and the blob is fetched
  only when a draft is actually resumed.
- **"Nobody has one" and "we could not look" are different answers**, the
  `connected_accounts_result()` rule — the list says which rather than drawing
  a clean empty panel over a store it could not read.

Three things the browser half has to get right. The autosave and the discard
**carry the mount** (`{{ request.script_root }}`): a root-absolute
`/api/draft` leaves this module and reaches the hub app, the trap
`test_landing_embeds.py` exists for. A closing tab is exactly the interruption
this is for and a debounce has usually not fired yet, so `pagehide` posts
through `sendBeacon` — which returns a boolean nobody reads, so a wrong path
there fails in total silence. And both deletes are `keepalive`, because
submitting the IO and pressing Reset are each followed by a navigation that
cancels a plain fetch, which would leave a finished IO sitting on the
unfinished list.

Starting a new IO and throwing the old one away are **different statements**.
The boot prompt's Cancel drops this browser's copy alone; the server draft
stays on the start screen, where discarding it is a deliberate press with the
name in the confirmation. Reset clears both, because its confirmation says it
clears the saved draft and that sentence has to stay true.
`test_drafts.py` asserts all of it.

### Every medium is asked about before it is priced

`hub/creative_needs.py`. A Connected TV or digital radio buy that reaches an
insertion order with no spot behind it is a launch date nobody can hit, so
those mediums are gated: does the client have creative, and if not does
the client pay or does Smart 1 comp it. **A comp on a medium spending under
the point where production pays for itself gets one explicit confirmation,
with the number shown**, and that confirmation lapses if the budget is later
cut below what was confirmed.

**Display and retargeting are gated too, and the paragraph that used to
exclude them was half right.** Six banner sizes genuinely is a $250 rate-card
line and genuinely is produced routinely — and none of that answers the
question, which is whether anybody has *asked*. A display plan reached the
insertion order with the creative box empty exactly as often as a CTV one did;
it simply cost $250 and a week rather than a shoot, so it was discovered at
trafficking instead of at launch and nobody called it a failure. Retargeting
is asked **separately** from display because it is a different set of files in
practice: the same six sizes carrying the offer that brings somebody back,
not the one that introduced the brand. A plan with both that answers once has
answered for one of them.

What stops that becoming noise is that the confirmation threshold is **per
medium** — `COMP_CONFIRM_BY_MEDIUM` puts banners at $500 against video's
$1,500 — because a warning that fires on every plan is a warning nobody reads,
which is the note `hub/qr_codes.py` makes about QR on social.

**"Do you have banners" has no answer until somebody says which sizes.** A
client who hands over a 300x250 and nothing else has answered yes and blocked
the buy. `required_units()` reads `hub/creative_specs.py` — the same S1M
CREATIVE SPEC KIT the IO's upload manager checks every delivered file
against — rather than restating it, so the proposal cannot ask for a set the
IO then refuses. Two rules on how that reads. A unit is **described in the
terms it is specified in**: an audio spot has no pixel size, it has a length
and a bitrate, and listing sizes alone made the audio row read *300x250* —
the **optional companion banner** presented as the whole requirement, which
is how somebody sends a banner and no spot. And the **ask leads**: a display
buy is a set of sizes and the HTML5 package is another way to deliver the
same set, so naming it first read as an extra thing to produce. A product the
kit maps no unit for is *not measured*, never an empty list rendered as
"nothing needed".

The classifier is the whole gate, and it cannot work from the rate card's
categories: four programmatic **video** products are filed under DISPLAY
beside banner inventory, and three of the four have names that identify
nothing — "Programmatic - Targeted" is $17.00 CPM video while "Category" next
to it is $4.25 CPM display. So those four are named explicitly, and
`/api/integrity` has a high-severity check that fires if one is renamed on the
card. Without it a renamed product silently reverts to the keyword guess, gets
read as display, and the gate stops asking while every screen still looks
healthy.

The wizard carries a JavaScript mirror of the classifier and both constants so
the Creative step reacts as a rep edits the plan; `test_proposal_spec.py`
asserts the two agree on every product, exactly as `test_target_areas.py` does
for the area helpers.

## A client's llms.txt is hosted here and reached from their own domain

`hub/llms_txt.py` builds one; `hub/llms_hosting.py` is everything after that.
The file is served at `/llms/<slug>/llms.txt` on this host and reached by a
**301** from `<clientdomain>/llms.txt`, which is a redirect rule in the
client's own site builder. No DNS change, no CDN, and no dependency on Smart 1
Sites ever supporting root-file uploads.

**One dedicated prefix, not a slug at the root.** `smart1.agency/<slug>/llms.txt`
was the obvious shape and is one path to allow in robots.txt, one to exempt
from the crawler header, one entry in the guard allowlist — and no chance of a
client slug shadowing a Hub route. A client called "status" or "activity" at
the root would have taken a staff page down, and the only sign would have been
a 404 nobody could explain.

**The brief named one layer that had to be opened and there were three.** Each
of the other two would have defeated the whole feature silently, with every
screen in the Hub reporting a clean publish while a crawler read nothing:

* **robots.txt.** `smart1.agency` refuses everything, correctly, and both the
  crawlers this is for honour it. `no_crawl.LLMS_READERS` is the eight agents
  that may read the prefix, and each gets `Allow:` **inside its own group**:
  robots.txt is matched by **user-agent group, never by substring**, so a
  group naming GPTBot says nothing whatever about ClaudeBot — and a bare
  `Allow:` under `User-agent: *` is not a fix either, since the original
  standard has no `Allow` directive and parser behaviour still varies.
  Deliberately a subset of `AI_CRAWLERS`: `Google-Extended` and
  `Applebot-Extended` are AI-**training** opt-outs and stay refused, because
  these files are for retrieval at answer time and nothing on this host is for
  a training set.
* **`X-Robots-Tag`.** `no_crawl.NoIndex` stamps `noindex, nofollow, …, noai,
  noimageai` onto *every* response in the composed app. `noai` on a file whose
  entire purpose is to be read by AI is the flattest contradiction available,
  and it is in no template and no route — it is added by middleware three
  layers out in `wsgi.py`, so it is invisible from the route that serves the
  file. That middleware already declined to overwrite a header a response set
  for itself, and its docstring said no route in the Hub had a reason to.
  One does now, and it says `noindex` alone: keep a raw text file out of
  search results, say nothing that tells its actual readers to leave it alone.
  **The 301 needs it too** — left to the default, a crawler that honours
  `noai` may not follow the redirect, closing the migration path for exactly
  the clients still on the old address. Found by requesting the route rather
  than by reading it.
* **The chrome.** `text/plain`, so the injector skips it on the mimetype —
  and `/llms/` is in `CHROMELESS` anyway, so it is a decision rather than a
  coincidence of that check.

**The slug is stored, which is the opposite of the rule everywhere else.**
`hub/client_key.py` refuses to store a derived key so a client renamed in
Knack re-joins on the next request. Here the same reasoning inverts: the slug
is written into a redirect rule **on somebody else's website**, so it has to
outlive the rename. Derived, a rename would 404 every request the client's own
site sends us, in silence. The registry is also the only place uniqueness can
be enforced — two businesses whose names slugify alike would otherwise share
one address, which is a third party's redirect quietly pointing at another
client's file — and it makes the public route a dict lookup rather than a walk
of the whole client book on every crawler request. Unpublishing **keeps** the
slug: a rule on their site still points here, and re-issuing a different
address later leaves it aimed at a 404 nobody is watching.

**Publishing is a separate act from saving, and saving used to undo it.**
`llms_txt.save()` assigned a fresh `{"text", "updated"}` over the whole record
— destroying the `published` copy beside it, so saving a draft took the live
file down and the next read adopted the half-written draft in its place, with
the screen reporting a clean save either way. The Commercial Builder's
`set_music` trap, one module over. **Every write to that record merges now.**

**The one migration is written down rather than assumed.** A record saved
before publishing existed has been served publicly all along, so refusing to
serve it now would take a live file off the air to satisfy a rule introduced
afterwards. Such a draft is adopted as published, once, and says
`from_draft` so the screen can tell it from a deliberate publish. Safe rather
than lenient: `save()` has always refused text containing a `NEED`
placeholder, which is the same gate publishing applies.

**The verifier is the point of the whole build.** The redirect is on a site we
do not control, the file is on a host whose crawler policy other work here
edits, and every failure is silent. `verify()` follows the chain **one hop at
a time** — `allow_redirects=True` collapses it into a final answer and throws
away the status codes, which is the entire question, since 301 and 302 both
end at the same 200 and only one of them is stored. It records the hop count,
the final status, content type, bytes and sha against the published copy, TLS
per hop, and robots at **both** ends per agent.

Three verdicts and a fourth state. **Pass** is one hop, a 301, a 200,
`text/plain`, bytes matching, robots allowing all three. **Warn** is reachable
and losing reach: a 302 (a crawler treats it as temporary), extra hops, a
landing host that is neither theirs nor ours — which is what "still on the
retired S3 bucket" looks like — or content that has drifted from what we
published. **Fail** is not reachable as a text file at all: a non-200, an HTML
content type, robots refusing at either end, or **a redirect landing on the
sign-in page**, which is the quietest of them: 200, a body, and a crawler
recording our login form as the client's llms.txt. And a robots.txt that 404s
means nothing is restricted, which is a real answer, while one we could not
**reach** is `measured: False` — reading a network failure as permission is a
green tick over a question nobody asked.

**One divergence from the written brief, stated rather than buried.** The
brief lists "final host is not the client's domain" as a Warn. Under this
design the final host is never the client's domain — that is what hosting in
the Hub means — so applied literally, Pass is unreachable and the column is a
wall of amber nobody reads. The actionable question is whether it landed
somewhere **unexpected**; the off-domain caveat is carried as a note on every
result instead, because it is a property of the architecture rather than a
finding about any one client.

**And the claim is kept honest in one place.** `CAVEAT` is the sentence, read
by the screen rather than restated in it: no major provider has confirmed it
reads llms.txt at inference time, a bot requesting the file proves access
rather than influence, and Google has said it does not use the file. It is a
low-cost, well-executed deliverable and must not be sold as a ranking lever.
`RUNBOOK` is the five Simvoly steps, likewise data, so the screen and the test
read one list.

**A nightly job asks, because nothing else will.** `llms_verify` re-checks
only the **published** clients — one with nothing live has nothing that can
have broken — writes each result against the client, and logs a row only when
something is failing: a clean sweep is a *state*, and writing one every night
for ever is the noise `hub/google_index.py` had to learn to stop making.

**`test_blueprint_guards.py` could not see this route, and the note saying
so was read for a year as a limitation rather than as the gap it was.** Its
sweep probed every route **with no variable in it**, so `/llms/<slug>/llms.txt`
was never requested — and neither was anything else with a `<` in it, which is
**330 of the 1048 routes the composed app serves**. That third is not a
remainder: every client-facing surface in this Hub is addressed by a token or
a slug, so "which parameterized routes answer a stranger" is very nearly the
question that file exists to ask, and it was the half nobody had asked.

It is swept now, with an inert value nothing in the book matches. **A 404 is
reached, not refused** — a guard runs in `before_request`, ahead of the view,
so it redirects whether or not the token resolves; a route answering 404 has
nothing in front of it. That is what makes an unresolvable id a fair probe
rather than a way of dodging the question, and it is why nearly every answer
in that section is a 404 and the reading still holds.

**It went in green**, which is the only way it was worth adding: all 63
reachable routes are the token- and slug-addressed client surfaces the design
intends — the scan widget and its report, the proposal a client accepts, the
paid-search estimate, the commercial review link, the radio approval page, the
client's four social pages, the upload picker, the calculators, the MSA PDF,
the Google Access consent flow and the static assets each of those loads. Each
is named with the reason it is public, in **two more dicts** rather than an
extension of the existing pair: an entry written for a fixed page must not
quietly cover a parameterized route added under the same prefix later, which
is the same reason reads and writes were split in the first place.

**Two things it had to get right, and the second nearly repeated the failure
this file was rewritten once to close.** A probe value has to *build*: an
integer converter refuses a word and a uuid converter refuses both, so a first
pass that picked the value from the converter's class name — and got that name
wrong for integers — silently dropped **76 rules**, printed a healthy-looking
count, and swept 254 rules while claiming 330. Values are tried in turn now
and anything that would not build at all is **named rather than passed over**.
And the two client-facing halves are asserted from opposite ends: four
surfaces are checked **by name** to still open for a client, because a
parameterized route falling *behind* the login is a sign-in form in front of
somebody who will never have an account — the failure Fan Radio shipped with
for as long as it took anybody to send the link.

The direct assertion in `test_llms_hosting.py` stays — anonymous, through the
composed app, headers included — because that one is about the crawler headers
as well as the openness.

## A placement is judged by its leads, so the page counts them

The scan widget --- the embeddable AI-visibility check --- is built at
`/scans/widgets`, and it was reachable only from inside Site Scans. It is a
lead-capture page you paste on a client's site, which is nobody's idea of a
site scan, so it now has its own tile on `/tools` under **Landing Pages**
beside the industry pages doing the same job. The implementation stays in
`modules/scans`: the placements, their runs and the pages those placements
serve are all there, and a second home for it would be a second description of
what a placement is.

The list said nothing about whether a placement had ever produced anything.
`leads.placement_stats_result()` answers that, and every rule in it is a way to
be wrong quietly:

- **A check is not a lead.** A public box on somebody's home page is typed into
  by passers-by; the number that matters is the visitor who handed over a name,
  business, email and phone, which is the same moment the row is written to
  `hub/leads.py`. Counting runs would report a placement converting nobody as
  the best one we have. Both numbers are shown, the lead as the headline.
- **`(stats, error)`, never a bare dict**, for the reason
  `connected_accounts_result()` gives in Google Finder: *nobody has used this
  placement* and *we could not count* are different answers, and the first is
  what somebody deletes a placement over. A failed read reads **not measured**
  on the page and **blocks the delete** rather than drawing a column of noughts.
- **A lead captured but not filed is counted apart.** `_capture_lead` writes
  `lead_id=""` when hub.leads answers without an id, so the count uses
  `nullif` --- an empty string is not null, and a plain count would file a real
  person nobody can find in the panel as filed.
- **Runs count only from the placement's own `created_at`.** A slug deleted and
  created again is a *different* placement at the same address --- the embed
  code is the same three lines --- so without that the old one's leads land on
  the new one's total, which is the single number the column exists to state.

Three actions, and the difference between them is said out loud on the page.
**Pause** leaves everything where it is and is undone by pressing it again; the
embed on the client's site says the scan isn't available. **Edit** names the
placement it is editing --- the route used to upsert on whatever the name
slugified to, so a second placement called "Smart 1 home page" silently
replaced the first: same address, new headline, and the only sign was a list
that did not get longer. **The address is not editable at all**, because it is
in the embed code already pasted on somebody's website and renaming it takes
the widget off that page while this screen reports a clean save. **Delete** is
refused once for a placement that has captured leads, with the count in the
refusal, and it deletes the placement only --- the runs stay, because they are
the evidence of where a real person in the Leads panel came from.

The lead count links into `/sales/leads?page=<tag>`, and the panel reads `page`
and `days` off the URL now; without that the link opened on every lead for
thirty days and the count on this page looked wrong rather than unfiltered.
`test_scan_widgets.py` asserts all of it, the tile included --- a tool with no
tile is invisible, and this file counts six that were.

## And the visitor's own half of that placement was tested by nobody

The placement admin is the tool a rep opens. The three pages a **stranger**
meets on a client's own website — the widget, the audit form, the waiting
page — are the rest of the second-largest module in this Hub, and nothing
exercised them. Booting the visitor's path found four, and every one answered
a prospect confidently.

**The callback token reached Insites unencoded.** `_callback_url()`
interpolated `SCANS_CALLBACK_TOKEN` straight into a query string, and that
token is a secret somebody typed into Render rather than a string this code
chose: a `+` comes back as a space, an `&` or a `#` truncates it at the
receiving end, and `secrets.compare_digest` then fails on a token that is
perfectly correct. Every callback 403s — and `api_callback` **deliberately**
leaves a refused row `running` rather than errored, which is right for a
malformed POST and is what makes this silent: the audit we paid for never
attaches, the visitor's page polls until it gives up, and nothing anywhere
says why. This is where the `SCANS_CALLBACK_TOKEN="abc"` quoting trap this
file already names actually lands; the quotes survive the round trip and the
characters around them are what do not.

**And it was two lines the shared reader already held.** `_callback_url()`
exists and is called by both staff paths; `_start_widget_scan` built the same
string itself, so the encoding fix would have landed in two of the three
places a scan is started — the drift `hub/storage.py` exists to stop, wearing
a query string. The check counts the *compositions* rather than asserting the
call, so a fourth one cannot be added quietly.

**Both poll loops read `ready` and never `status`.** A run at `error` or
`unconfigured` will never become `complete`: the first is a provider that
refused, the second is a deployment with no Insites key, where the lead is
still captured — correctly, since a lead is a lead whether or not Insites ever
answers — and no audit is ever bought. Those were polled to the ceiling and
then told *"Your deep scan is still running"* and *"Still working … open the
link below in a few minutes and it will be there"*, which is the one thing
neither is. The status was on that response the whole time and nothing read
it: the failure `campaign_assets.report()` already has, where a warning is
computed and dropped on the way to the reader. `STOPPED_SCAN_STATUSES` is the
list, and it is **the server's** — `api_widget_status` answers `stopped` with
the sentence to show, because a second list of which statuses are over, in two
templates, is two more answers to one question. The server-rendered waiting
page had the same gap in Jinja: it tested `status == "error"` and not
`unconfigured`, so a Hub with no Insites key drew a spinner and a 30-second
meta refresh for ever, on a run where nothing was ever started. It stops
refreshing when there is nothing left to wait for.

**And two of those pages promised an email.** *"We'll email your report the
moment it lands"* and *"close it and open the link in your email when it
lands"* — to a stranger, on somebody else's website. **There is no mail sender
in this Hub**, which this file says five times over, so it was a promise
nothing here could keep; and the first one fired on every run that crossed ten
minutes rather than only on the failures. Both say where the report will be
instead, which is what the audit placement's own copy had said correctly all
along.

**A second unlock rewrote the contact on somebody else's run.** The lead is
filed once, properly guarded by `first_unlock` — and the four contact fields
were written unconditionally, directly beside an `unlocked_at` that
deliberately was not (`row.unlocked_at or _now()`). So a second post of the
same token left the run row naming one person while carrying the **lead id of
another**, and the run row is the evidence of where that lead came from: what
the placement list counts and what the report page prints. Anybody holding the
token can post that route, so the second name is not necessarily a typo being
corrected — and correcting the row without correcting a lead that has already
gone to Suite and is never re-delivered makes the two disagree rather than
agree. The contact follows the timestamp now.

**The email sweep is a sweep, and it caught two things a reading would not.**
A list of the two pages we fixed proves nothing about the third, so
`test_scan_run.py` reads every template this module serves without a login.
Two rules it needed. **Prose is not a call site**, for the sixth time in this
file: the first run reported the comment *explaining* the fix as the promise
it describes, so block comments and whole-line `//` ones are stripped — and a
mid-line `//` is left alone, because that is a URL. And **the copy was written
with a backslash in it**: the line that was live is a JS literal reading
`We\'ll email`, which a character class that did not allow one reads straight
past — a sweep that misses the sentence it was written to find is a sweep
reporting a clean page. Both are asserted against the exact text that was
there.


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

## Social posts are drafted here and published in Suite

`modules/social_planner` (`/tools/social`) builds a client's month of organic
posts in one pass. `hub/social_plan.py` is the spec — channels, post types and
their mix, the calendar arithmetic, the copy checks and the CSV layout — read by
the module, the exporter and the AI prompt alike, the same way
`hub/proposal_spec.py` is.

**It stops at a CSV on purpose.** Social Planner's write API needs
`social-media-posting.write`. That scope is now in `hub/ghl_scopes.py` and is
asked for at consent, but *requested is not granted* — until the agency
re-consents and the Suite panel's scope report shows it granted, the write path
does not exist. Ending at the bulk-upload CSV means the drafting pipeline earns
its keep while that is pending, and works regardless of whether it ever lands. When
it does, `PickerClient.ghl_location_id` already holds the sub-account id per
client — that mapping does not need building. **Resolve a client to a location
by domain, never by name**: posting to the wrong sub-account publishes one
client's content on another client's page, which is the worst outcome any tool
in this Hub can produce.

**The copy checks are code, not prompt text.** A price, a percentage, a phone
number or a deadline that is not in what the strategist typed is a *blocking*
flag and the plan cannot be approved with one outstanding — unlike a proposal,
which a rep reads before a client does, a month of posts is bulk work that gets
skimmed. Superlatives warn. Channel limits block. `test_social_plan.py` asserts
all of it, with deliberately plausible fixtures: the failure mode is not
gibberish, it is confident and wrong.

**Tone is a set of options, not a text box.** A free-text tone field got one of
three answers — nothing, "professional", or a sentence pasted since 2023 — so
every month came out in the same middle register. Each option in `TONES` carries
the *instruction* rather than the label ("write the way you would talk to
someone over a fence"), several can be combined, and the free-text box survives
beside them for a client whose voice is genuinely their own.

**The month is asked what it is promoting.** Without `promote`, the model
spreads itself evenly across everything the business does, which is how a plan
ends up promoting the service they least want more of. It is a focus, not a
mandate: the prompt says not to force it into an educational or community post,
and a service named there counts as authorised text, so mentioning it is not
flagged as invented.

**Social media holidays are offered as fill, and the list is ours.** The middle
of a month drifts into generic filler because a local business does not have
twenty things happening; a dated hook the audience recognises is better filler
than an invented one. There is no authority publishing "national days" and the
lists that circulate contradict each other, so `HOLIDAYS` is deliberately short
and checkable, every row says `source: house`, and the screen says so. The
moving ones — Thanksgiving, Mother's Day, Memorial Day — are **computed**, never
listed: a hard-coded date is right for one year and quietly wrong every year
after, and the calendar still renders. Days carry `tags`, so one tagged for
retail is not offered to a roofing company. A holiday landing on an existing
slot is attached to it; one landing on a non-posting day adds a slot, because a
day you wanted to mark is no use marked three days late.

**There is no JavaScript mirror of the grid.** Target areas and the creative
gate each carry one so a wizard can react live, and each needs a test asserting
the two halves still agree. That cost is paid twice already; here the calendar
is one API call and the browser renders what comes back. Keep it that way.

The page hands the browser its vocabulary in a `<script type="application/json">`
blob rather than interpolating Jinja into a real script block, which is why
`tools/jscheck.py` can hand that block to `node --check` instead of skipping it.
`tools/pagecheck.py` did not know that script types other than JavaScript exist
and failed the page for a syntax error in something that was never code; it now
shares jscheck's `NON_JS_TYPES` list.

### The layer above a month plan: requests, ideas and the push

`hub/social_content.py`, `modules/social_planner/intake.py`, `ideas.py`,
`agent.py`, `links.py` and `suite_client.py`. The planner drafts a month for a
client we manage; everything here is what surrounds it — where an ask came
from, who asked, what the client is being offered to swipe on, and what has to
be true before anything reaches Suite.

**It is in the planner and not in a module beside it.** A `modules/social_content`
next to `modules/social_planner` would be a second description of what a post
is, which is the failure the Proposal Builder cost a year: the same client
quoted two ways depending on which of two tools a rep opened. A month drafted
by a strategist, an idea a client liked and a photograph a location manager
sent in converge on the same slot, in the same batch, going out through the
same export or the same push. `hub/social_content.py` sits beside
`hub/social_plan.py` for the same reason the proposal spec sits beside the
rate card: it is data and arithmetic with no Flask in it, read by the module,
the client pages and the test alike.

**One client account is one social presence and often several shops.** A
location here is a Hub-only organizing idea — it sorts and attributes a
request and never gets its own posting destination, because one shared page is
what the client has. There is one signed link per **client**, not per
location: a link per location is the inbox-per-shop arrangement this replaces,
reissued as URLs. The link is *derived* (`itsdangerous` over `SECRET_KEY`), so
it is the same string every time, cannot be created twice and cannot go
missing; the only thing written is a small revocation list, and a revoked link
answers exactly what a link that never existed answers — the rule
`modules/ads_builder` settled for the client estimate.

**A client's own words are authorization, and this is the load-bearing half.**
`social_plan.validate_copy()` blocks a price, a percentage, a phone number or
a deadline that appears in copy and in none of the facts a human typed,
because a model inventing "$50 off through Friday" gets the client a phone
call about an offer they never ran. A location manager typing that sentence
into the request form **is** the supply. So a promoted request carries its own
words onto the slot as `supplied`, and `validate_slot()` merges them — in that
one function rather than at each call site, because a rule two of three
callers keep is not a rule. Without it the tool blocks the client's own offer,
on the client's own request, and reads to a strategist as broken rather than
as careful. Nothing errors either way.

**A flag is computed on read and never stored, and never crosses a client.**
Overdue and possible-duplicate are both functions of today and of the other
rows as they stand now; baked in at write time, a request that went overdue
overnight stays green until somebody edits it, and there are two gunicorn
workers to disagree about which copy is current — the reason
`hub/creative_evergreen.py` applies its mark on read. Duplicates group by
client first: two businesses wanting the same Friday is a Friday, and a queue
full of pairings nobody can act on is a colour people stop reading. It is a
plain date-overlap test on purpose — fuzzy matching on a location manager's
own wording produces a confident wrong pairing, and the whole point is that a
person reads both and decides. **Nothing is auto-merged and nothing is
auto-declined.**

**ASAP is a real answer and is not converted into today.** A request that says
"whenever" is not overdue the moment it is submitted, and treating it as
naming today would flag every one of them by tomorrow.

**A turnaround time is measured or it is not promised.** The confirmation
screen wants to say "ready to review by X". `turnaround_note()` computes it
from the requests actually triaged and says *not measured* until there are
three, rather than quoting a plausible number nobody has checked into a
commitment made on the client's behalf. `mark_triaged()` stamps once, so the
figure cannot report the tool getting faster the more a row is fiddled with.

**A location that is not set up never blocks a submission.** The form takes it
as typed and the queue says it matched nothing. A form that turns away a
location manager who is trying to send us a photograph has cost us the
photograph, and the dropdown is our housekeeping rather than theirs. A
location id from *another* client's link is refused rather than silently
re-filed — it is the one field that decides whose queue a request lands in.

**Promoting carries the ask across, both ways.** The slot records
`source_request_id` and the location it came from; the request records the
batch and slot it became and then moves with it (`sync_from_post`), forward
only, so a strategist un-approving a slot to fix a typo does not walk a
client's request back to New. Without the join a request is a dead form entry
and somebody re-reads the whole queue to work out which ones were done.
Promotion is **staff-reviewed**, which is the spec's open item 6 answered the
way this codebase answers it everywhere: a client liking a one-line title has
not approved a post. And it will not invent a plan — with no month built it
refuses and names the step, because a batch conjured to hold one request is a
month nobody chose the channels or the mix for.

**A swipe steers the mix, never the words.** `tag_weight()` is
`liked / (liked + passed + 1)` — one line, reproducible in a reader's head
from the two counts printed beside it, and it only ever decides which *kinds*
of post get offered next. That is what makes it safe to be this crude. A tag
the client asked for on the preference form outranks the weighting, because
that is the one signal that was not inferred; a fixed share of each batch goes
to tags nobody has answered on, or the mix converges on whatever they liked
first and stops being able to learn anything. A second tap on a phone is not a
second answer.

**"We could not ask the model" is not "this business has nothing to say."** A
failed idea call still returns a batch built from the tag prompts, marked
`source: "house"`, and the screen says which it got — the rule
`modules/image_picker/profile.py` arrived at. The do-not-mention list is
**checked** against what comes back as well as being put in the prompt, the
`hub/blog_spec.py` rule: a prompt is a request, and "the model was told not
to" is not evidence that it did not.

**Nothing is pushed until the scope is granted, and that is asked rather than
discovered.** `hub/suite_accounts.publishing()` diffs
`social-media-posting.write` against what HighLevel actually granted, and it
is tri-state — granted, not granted, or **not measured** because HighLevel
omitted the scope list, which is not evidence the scope is missing and is not
permission either. Until it is granted the whole drafting pipeline still earns
its keep through the CSV under Suite's Bulk Upload, and every screen says
which of the two routes it is offering rather than drawing a push button that
fails at the moment somebody is waiting on it. The endpoints are
**transcribed** from the build spec rather than fetched, and the collection
path is one environment variable (`SOCIAL_SUITE_POST_PATH`) because nothing
has ever been able to exercise them.

**A failed push leaves the post approved.** Never `scheduled`, never
`published` — a client-approved post that quietly reads as scheduled is gone,
and the queue says it is handled. `apply_push_result()` is the one place that
guard lives. Retry is a button and never automatic: a flaky response is
exactly the case where the write may well have landed, and an automatic retry
there is a double post on somebody's page. A 200 carrying **no post id** is
refused as a success for the same reason — without an id nothing can read the
status back, and a retry would post twice.

**One post at a time, never a month in a loop.** A loop that pushes twenty and
fails on the eleventh leaves a person working out which ten landed.

**Which sub-account a client is has one answer now.** `hub/suite_accounts.py`
reads `PickerClient.ghl_location_id` — the one place in this Hub that records
it — and `modules/commercial_builder/client_link.suite_location_id` delegates
to it rather than carrying a second copy, the way
`modules/radio_promo/voices.py` re-exports `hub/voice_casting.py`. The join is
an exact domain or an exact normalised name and nothing else, and two rows
naming different sub-accounts for one business is **named, never picked
between**: posting to the wrong sub-account publishes one client's content on
another client's page, which is the worst outcome any tool here can produce.

**Three answers on a post, not two.** "Yes", "yes with my changes" and "not
this one" are the three real replies, and an approve/reject pair forces the
middle one into whichever end is nearest — `modules/ads_builder/spec.py`'s
rule. A change request **requires the words**, and it clears the approval, or
a post the client asked to change could be pushed while the request sat
unread.

**What a client sees is built server-side, not left to a template.**
`_posts_for_client()` returns the fields and only the fields: no flags, no
internal status, no strategist's name, nothing of any other client. A subset a
renderer merely happens to omit is one the next renderer prints, which is why
`modules/scans` strips its audit the same way. Only slots actually put to the
client appear — a slot somebody is still writing is not a decision anybody is
waiting on, and showing it invites a change request on a draft. **Sending
holds back a slot with a blocking flag** and says how many, because asking a
client to approve an unauthorized claim is asking them to authorize it after
the fact.

**A location manager is not a lead.** Every other capture point in this Hub
writes through `hub/leads.py`; this one deliberately does not. The person
filling in the request form works for a client we already have, and filing
them as a lead puts a live client into the sales panel as a new business.

**The agent reads and reports; it never writes and never publishes.** Its
contribution to a batch is a list of *tags*, which a client still swipes on
and a strategist still promotes. Every finding carries what was measured and
the screen it is acted on from, never a product name — the
`hub/website_audit.py` rule. Four of its five inputs can be unavailable on any
day and each has its own kind of nothing, so `signals()` answers with a state
per input and performance reads **not measured** with the reason rather than
zero. Nothing it raises is drawn red: a page of red is a page people scroll
past.

**Ideas are offered on a schedule, or the client's link opens on nothing.**
`generate()` was reachable only from a button in the staff queue, so a client
who opened their swipe link saw "Nothing to look at just yet" — for ever,
unless a strategist had remembered that week. `ideas.sweep()` runs on
`hub/scheduler.py`, hourly ticks deciding a weekly interval **per client from
that client's own last sweep** rather than from the job's schedule, so a
redeploy cannot offer two batches in a day — the `purchased_domains` shape,
for the same reason. Bounded on both axes (eight clients, three minutes)
because these are model calls sharing one scheduler thread and a call has no
useful ceiling.

Three gates on who is swept, each a way to spend a model call on nobody.
**Somebody at the client must have swiped at least once** — the first batch is
the strategist's to send, and a client who has never opened the link would
otherwise accumulate ideas nobody reads at a call a week for ever. **Their
deck must be nearly empty**, or the backlog grows faster than anyone answers
it. And **not more often than weekly**. What was skipped is named rather than
counted: "nobody was eligible" and "we could not read the client list" are
different answers.

**A client record that says nothing about the work is the failure this repo
counts six of.** Client 360 already had a Social Media card — that is their
profile URLs, a different question from whether anybody at this client is
asking us for anything. A client could have three requests overdue, a link
nobody had sent them and four posts sitting unanswered, and none of it was on
the one screen a rep opens. `hub/social_status.py` answers it, and the
dashboard scoreboard reads the same module so the two cannot disagree — the
`/api/db/structure` versus `/api/integrity` trap, where two checks asking one
question answered it differently on one panel.

**Nothing told anybody a request had arrived.** A location manager submitted
at four on a Friday and it sat in a queue only somebody who opened the tool
would ever see. There is no mailer in this Hub, so the honest route is putting
it where people already look: a scoreboard on the dashboard, above System
status, where every figure opens the rows behind it rather than the tool the
reader would then have to filter. It counts **requests only** — reading posts
awaiting approval means opening every plan file for every client on the book,
on a page that loads on every visit, and a number that costs a page load is a
number somebody turns off. The per-client card can afford that; the dashboard
cannot, and the split is stated rather than discovered.

**An empty scoreboard says which kind of empty.** "Nothing waiting" and
"nobody has been sent their link yet" render identically as a zero and only
the second is somebody's to fix, so the line says so in words beside the
figure. Same on the card: a client with no requests is told that the link may
never have gone out.

**A photograph the client sends reaches Cloudinary and not the gallery.**
That is the half of the asset pipeline that was actually missing.
`storage.put()` stores the bytes; every screen that offers "the client's own
assets first" — the planner's own image assignment, Image Creator — reads
`client_context.gallery_images()`, which reads the image picker's gallery. So
a photograph a location manager sent in was invisible to the tool built to
prefer it, while the client had been told it arrived, and nothing errored at
either end. `_file_into_gallery()` files it, labelled with the shop it came
from, and the two writes are **reported apart** — the `hub/domain_links.py`
rule, since "stored" and "stored in one of two places" are different
outcomes. Nothing branches on the gallery write: the client is watching and
their upload has already succeeded, so a gallery that will not answer costs
the composer a picture and never costs them the photograph.

The gallery row is **created** where a client has none, which is a deliberate
exception to `provisioning.py`'s "creating is asked for, not assumed" — there
the question is whether a link should exist, and here there are already bytes
from a named client on a link we sent them. It is not pushed to the Suite
media library: this is a photograph somebody sent us to consider, not
approved work.

**The canvas presets were already there.** 1080x1080, 1080x1920 and 1200x630
have been in `modules/image_creator.CANVAS_PRESETS` all along, under Social.
Worth writing down, because the obvious reading of the spec is that they need
adding and adding them again is how one tool comes to offer two Instagram
Posts of different sizes.

**The queue shipped with no explanation on it, which is how Smart 1 Ads
shipped.** `hub/help.py`, `hub-help.js` and the tour machinery all working,
and nothing on the page opting into any of it. Eight bubbles now, every one
guarded `if help_dot is defined` so a module whose Jinja environment never
got `install_template_helpers()` loses the icon rather than the page. There
is deliberately **no `data-screen`**: a tour is offered only where one is
registered, and naming one that does not exist is the same silence one step
earlier. `test_social_content.py` reads the template and requires every key
it places to resolve in the registry — a bubble whose key is missing is
removed client-side, so the template reads as helped and the page shows
nothing, and nothing anywhere reports it.

**The queue is linked from the planner, not tiled separately.** It is the
other half of one tool; a second tile is two things to keep in step and only
one of them ever gets updated. It carries `data-demo="off"` and no
`data-screen`, because the module's walkthrough was written against the
month-planning screen and offering a tour that does not exist is the silence
Smart 1 Ads shipped on Settings and Live campaigns.

`test_social_content.py` asserts all of it, including that the client's four
pages are reachable with no login and that the staff queue and its API are
not — both halves, because a client-facing page behind `AuthGuard` is a login
form in front of somebody with no account, and a staff queue outside it is
every client's name answering 200 to anyone with the URL, which is the hole
`modules/commercial_builder` shipped with.

## A blog post carries more than a title, and none of it was being asked for

The SEO section planned topics and wrote copy from the client's own website and
nothing else. Four things the account manager knew never reached the writer, and
`hub/blog_spec.py` is where they now live — the taxonomy rules, the approved
topic list, the default author and the client's guardrails, read by the planner,
the writer, the client document and the CMS panel alike, the same way
`hub/proposal_spec.py` is read by four things at once.

**The settings are visible before there is anything to plan.** The Blogs card
used to appear only once blogs were switched on in Client Setup, which hid the
author, the guardrails and the approved-topic list — the things filled in
*before* planning — until after something had been planned. The card is always
shown now, says when blogs are off, and opens its settings panel while it is
still empty. A collapsed panel is exactly as invisible as no panel to somebody
who does not know it is there.

**A plan made before the taxonomy existed can gain one.** Re-planning would do
it and would also replace every title and discard written copy, so
`blog_tag_posts()` fills in categories and tags and touches nothing else —
otherwise those rows read "not set" forever with nothing to do about it.

**Categories are structure; tags are detail.** A model asked for "categories
and tags" invents a fresh category almost every time, and twelve posts arrive
under twelve categories — a sidebar of one-post categories that helps nobody.
So the model is told the categories this client already uses and whatever it
returns goes through `clamp_taxonomy()`, which keeps the known ones, allows at
most **one** new category per post, dedupes case-insensitively and caps the
counts. The client's set grows deliberately and slowly. Same clamp on the edit
route, or the rule holds only until someone types into the box.

**The approved-topic upload sits beside the planning question.** It spent a
release inside the collapsed settings panel, where nobody found it. What a
setting *changes* decides where it lives: the author and the guardrails are
set-and-forget and belong in a drawer; the approved list changes what the next
plan contains, so it sits in the Blogs card in its own panel, above the button
that acts on it, saying what is loaded without anything being opened.

**An approved topic is reproduced, not paraphrased.** A topic list a client
signed off in advance is a commitment. `parse_approved_topics()` reads the
document we emailed them — PDF, Word or pasted text, through the same
`_read_document()` the IO Builder uses — and the approved titles are written
into the plan **in code**, after the model has answered, because "use these
titles as written" is a request and a paraphrased title is a topic the client
did not approve. Each post records whether it came off that list. With
`approved_only` the schedule stops when the list runs out instead of topping
itself up with invented topics.

The parse is two-pass: a document that numbers or bullets its topics has told
us which lines are topics, so every other line is notes on the one above.
Guessing by line length read a 118-character sentence of notes as a topic of
its own.

**"Never mention" is a check, not a sentence in the prompt.** This is the Smart
1 Labs rule again — a prompt is a request, and "the model was told not to" is
not evidence that it did not — and here it is usually a legal instruction. The
list goes to the model *and* `scan_forbidden()` reads the finished copy and the
meta description, flags every hit with the sentence around it, and the flag
follows the post into the table, the client document and the publish panel until
someone rewrites it. It strips the HTML first: scanning raw markup matched
`class="guarantee-band"` and flagged a post whose copy never said it. The free
guidance box still goes to the model unchecked, because most of it is context —
how they operate, what they are licensed for, how the warranty works.

## A reviewer answers "none", and the question still needs an answer

`hub/schema_questions.py` asks 35 questions and refuses to let a schema be
approved while any is unanswered. Its docstring is emphatic about why — *"An
empty field is honest; a plausible guess is not … structured data is consumed
by machines that treat it as fact"* — and ends the paragraph **"The block is
the feature."** The block could not be cleared for the commonest honest
answer there is.

`_blank()` reads `none`, `n/a`, `unknown` and `-` as an unfilled field. That
is right for a value coming off a **record**, where it means nobody typed
anything, and exactly wrong for one a person types into *"does the business
hold any licenses?"* — where it is the true answer for most small businesses,
as it is for awards, trade associations and a slogan. So a reviewer answered
`awards: none`, `save_answers()` stored it, `build()` read it back as blank,
and it came out **NEED ANSWER** again. They typed it, saved it, reloaded, and
approval was still blocked by a question they had answered. For ever.

What a person saved is kept apart from the record now and held to the looser
test: theirs is the best source there is, and the one place "none" means
none. **And the first fix for it did not work**, which is worth recording
because it is the same shape as the bug: `_lookup()` returned the typed
answer correctly and the line immediately after **re-tested it** with the
strict rule, because that call site cannot know the value came from a person.
It is `val is None` now — one reading, asked once.

**The panel contradicted itself on the one question it exists to answer.**
The GET builds with AI and reported `can_approve` on the strength of
inferences; the POST beside it calls `can_approve()`, which re-derives with
`use_ai=False` and turns every inference back into a NEED ANSWER. So the same
screen said *"Every question answered. Ready to approve."* in green on load
and *"N still marked NEED ANSWER, so approval stays blocked."* in red on
save — the `/api/db/structure` versus `/api/integrity` trap, on a schema
builder. `_blocking()` is the one reading, and an **AI answer blocks**: this
module says an inference is "always worth checking" and that a plausible
guess is worse than an empty field, so saving one is the check, and saving is
what unblocks it. The two paths now agree by construction rather than by
coincidence — without AI those rows are `needed`, with it they are `ai`, and
both count.

**And two confidence levels were always zero.** The docstring promised each
question is answered *"from the Hub's own records first, then the client's
website, then a web search"*. Neither of the last two is built — nothing
fetches their pages, and the AI call is told to use only what it is given —
and `by_confidence` reported `site: 0, search: 0`, which reads as *their
website had nothing on it* rather than as *nothing looked*. `NOT_BUILT` names
them with the reason and the two keys are gone from the count, the
`_KIT_UNREAD` rule one module over. `test_schema_questions.py` asserts all of
it.

## Another agency's photograph, captioned as the client's own premises

`hub/landing_images.py` picks the pictures on a landing page a prospect
reads. Its docstring names its best source — *"**The client's own site.** A
photo of their actual premises, van or team beats any stock library, and it
is the only source that is genuinely about them"* — and ends with the rule
the whole module is under: *"Stock photography … is never captioned as the
client's own work … a page may be short, it may not lie."* It was breaking
both halves.

**`from_site()` had no domain check at all.** It regexed every image URL out
of a 400 KB scan payload and labelled all of them `their site`. A scan
payload is 440 fields of whatever the crawler saw, so those URLs belong to
all sorts of people: against a realistic one, six pictures came back and
**five were somebody else's** — the scan vendor's own screenshot, a Facebook
social card, a Google static map, a Google ad creative, and **another
agency's Cloudinary folder**. Any of them could become the hero of a landing
page presented as the client's own premises. That is
`client_urls.NOT_A_WEBSITE` one module over, and it was not hypothetical
there either: on this deployment's own export *every single* click-thru
domain turned out to be a file host.

`theirs()` is the test, and it reads `client_context.canonical_domain()`
rather than comparing strings, so it cannot drift from every other join in
the Hub. A **subdomain counts** — `cdn.`, `www.` and `images.` are ordinarily
theirs — and a lookalike does not: `acme-tyre.com.evil.test` ends with the
domain and is refused, which is why the test is `endswith("." + domain)` and
not a containment. What is dropped is **counted** (`not_theirs`), because a
list that quietly gets shorter cannot be told from a site with no pictures on
it.

**A picture off their site carries no dimensions, and a missing size read as
a large one.** `pick()` asked `img.get("wide", True)`, so every unmeasured
picture qualified as a hero and `_MIN_HERO_WIDE` was skipped entirely for the
source this module prefers — a thumbnail off their page could be the
full-bleed band. It is `wide: None` now, *not measured*, and the test is `is
not False`: a stock image measured and found narrow is still skipped exactly
as before, and their own site still leads, which is this module's stated
order and the reason the old default read as harmless.

**And `source` described the search rather than the set.** It said `their
site` whenever the site search returned anything at all, however much of what
was actually picked came from a stock library. It reads the pictures that
were chosen now, and answers `their site and stock` where it is both — which
is the docstring's own rule, in one word. `test_landing_images.py` asserts
all of it.

## A featured image named after a title two posts share

`hub/blog_images.py` generates the image every blog post needs before it can
be published, holds it `pending` until a person looks at it, and files the
approved one into the client's gallery. It named the Cloudinary object after
the post's **title**, with `overwrite=True` and `unique_filename=False` — and
a title is chosen by a model and is not unique. That is not a coincidence to
guard against: `hub/seo.py` tops a short plan up from a list of **six**
fallback titles and **cycles** it, so a client on twelve posts a month gets
each of those titles twice, verbatim, in one plan. Both posts then generate
into one object. **Post 3's featured image becomes post 9's picture** — at
the same URL, in the store, in the client's gallery and on their live site —
and approving the second overwrote the first's approved, filed copy as well.
A long title reached the same collision through the 60-character truncation.
Nothing errors at any point: two posts, one perfectly good photograph.

The post id is unique by construction and was already being written into the
upload context, so `image_name()` puts it in the name. **Nothing is re-keyed**
— every existing row carries its own `public_id` and `_promote()` and
`_file_in_gallery()` read that rather than deriving one, so a post with no id
falls back to exactly the old spelling: the rule `audit.LOG_NAMES` and
`video_library.TAG_ALIASES` already work to.

**A badge counting posts the list no longer shows.** `/api/seo/blogs` filters
`archived` out of the working list and says so in a comment directly above the
filter; `status()` did not, and its number is drawn as a badge on a Blogs
section that is **collapsed by default** — the one signal that says somebody
needs to look at these. Archiving a post with a pending image left *"1 image
to approve"* above a table with no row to click, amber for ever with nothing
anywhere to clear it: two readings of which posts are in play, disagreeing on
one screen, which is the `/api/db/structure` versus `/api/integrity` trap
wearing a badge. What leaves the badge is **counted rather than dropped** —
that post's file is still sitting in `pending/` and nobody is going to approve
it — because a badge that quietly gets shorter cannot be told from one that
failed to load.

**A 3 MB hero, filed in silence.** `_optimise_bytes()` returns nothing at all
when Pillow cannot read the bytes and `staged or raw` fell back to the
original, which is right — an image nobody can shrink is still the image.
Saying nothing was not: this module's own docstring calls a 3 MB PNG *"a Core
Web Vitals problem on the very page the post was written to rank"*, and one
went into the client's gallery with `bytes` recording 3 MB and every screen
reporting a clean success. `optimised` is on the record now and the note names
the size, because that is the one number on it somebody would act on.

**And the pending folder's whole purpose was undone by the audit.** Pending
images live in `seo_images/<client>/Blogs/pending/` *"so an unapproved image is
never mistaken for a finished asset by anything browsing the gallery"* — and
`hub/image_audit.reconcile()` lists that tree by prefix like any other, while
no store it reads had a row for what is in it. So an unapproved image read as
an **orphan**, on QA → Unattached Images, with a client picker beside it: one
press files the six-fingered plumber into the client's gallery labelled *"SEO
images"*. The approved half was safe only by accident, because `file_asset()`
had already given it a row. `image_audit.STORES` has a reader for the SEO
stores now, so the audit is told the store has a row rather than the folder
being quietly skipped — a folder silently left out of a completeness report is
the same failure the report is about.

**And `gallery_folder` was assigned after the save.** `img` is a reference
into `store`, so writing to it once `save_store()` had run left the value in
memory alone: it reached the browser and the next read of the record had never
heard of it. It saves twice now — once before the gallery write, because a
gallery that is unavailable must not cost somebody an approval, and once after
when there is something new to keep.

**`_optimise_and_store()` is deleted rather than left standing.** Sixty-nine
lines implementing resize-then-convert-then-file, written when approval was
meant to be the step that optimised; nothing has called it since that work
moved into `generate()`, and the module's docstring still described its path.
`test_unwired.py` could never have said so — it skips names beginning with an
underscore, because a private helper called from inside its own module is the
ordinary case. `test_blog_images.py` asserts all of it.
## A number a stranger controls, and the sweep that did not finish

`hub/webargs.py` is fifty-one lines reached from twenty files, and its
docstring is a list of three faults it was written to end: `int()` outside a
try (`?limit=abc` is a 500), an upper bound and no lower one (`?limit=-1`
reaches `rows[:-1]`, *"a wrong answer delivered with no indication anything
was wrong"*), and the same clamp written out twice by people who could not
tell whether it was already there.

The helper is right. **The sweep it implies is what did not finish**, and
each of the three call sites left over is reachable from a URL. Smart 1 Ads
searched the client list with `limit=min(int(…) or 12, 50)` over a
`search_clients()` that ends `[:limit]`, so `?limit=-5` returned every client
except the last five as a clean answer. The Suite panel clamped both ends of
its audit-log limit and had no try — on the activity log of the panel that
creates and deletes client sub-accounts, which is the record somebody
reconstructs an incident from. And the Commercial Builder's stock search had
**neither**, on a `per_provider` that goes straight into
`pexels_service.search()` and `pixabay_service.search()` once per expanded
query: an unbounded caller-controlled fan-out to two billed providers.

**And the check that exists for this found none of them.**
`check_unclamped_limits()` matched the read as **text** and then skipped any
window containing `min(`, `max(` or `clamp` — a guard against crying wolf
that made it blind to precisely the two shapes that were live, because an
upper bound with no lower one contains `min(` and both-bounds-no-try contains
both. It also needed `hub/webargs.py` exempted **by name**, because that
file's docstring quotes the bad pattern to explain it — prose is not a call
site, for the fifth time in this file, and it duly reported the new test
file's own fixtures three times over. It reads the AST now and asks two
narrow questions: a bare `int()` over a caller's value outside a try, and a
`min()` over one with no `max()` or `clamp_int()` around it. Both empty the
day it changed.

**The helper's own promise had a hole in exactly the place its comment
names.** *"OverflowError is here because float() accepts 'inf' and int() then
refuses it — the one input that still crashed a helper written to make
crashing impossible."* That guard was on the **inner** branch, which is the
one the *string* `"inf"` takes; a real `float('inf')` is refused by `int()`
on the **outer** branch and propagated. Not hypothetical and not only a query
string: Python's `json.loads` accepts the bare literal `Infinity`, and three
call sites pass a JSON body value straight in — `google_finder`,
`video_backgrounds` and the Hub's own blog planner — so `{"limit": Infinity}`
was a 500 out of the function whose first promise is that it never raises. An
infinity now takes the documented fallback to the **default** rather than the
ceiling, which is what `"inf"` and `NaN` already did: `"1e5"` is capped
because it parses to a real number above the ceiling, and an infinity parses
to no number at all.

`_page_arg()` in the Suite panel was the third fault standing on its own —
the same rule, worked out independently and correctly, in a module that could
have imported it. That one was **not** a defect, and it is worth saying so:
the only observable difference is the shared rule's own, that a float
truncates rather than being thrown away for the default.
`test_suite_panel.py` asserted it by matching the literal `max(lo, min(hi,
int(` in the source — the implementation restated in the test, a third thing
to keep in step, which duly failed on a change that made the code better. It
drives the function now.

## A comparison keyed on a string Google does not send

`hub/analytics_ask.py` turns a plain-English question into a GA4 report, and
its docstring opens by saying what it replaced: a keyword matcher that
answered *"how did conversions do in July versus June?"* with a thirty-day
source/medium table, **"which is worse than refusing — it answers confidently
with the wrong report."** It was doing the same thing one layer down.

`shape()` decided which period a row belonged to with `tag.endswith("_1")`.
GA4 values the `dateRange` dimension with the range's **name** where one was
given, and only falls back to `date_range_0` / `date_range_1` where none was
— and `_PLAN_SCHEMA_NOTE` *requires* names: *"for a comparison, give exactly
two dateRanges, each with a name."* So "July" and "June" both tested false,
both rows landed in the same bucket, and the second overwrote the first.

Dublin at 900 sessions in July against 600 in June rendered as **600**, with
no previous and no change, and the totals row read **"600, up 100% on 0"**.
Every figure on the page wrong, and every one of them a real number from the
property. The identical data with unnamed ranges worked perfectly, so **the
path that works is the one the planner is told never to take** — which is why
nothing ever looked broken in development.

`range_index()` reads the name first and the index tag second, and a tag it
can place in neither is **counted rather than folded into the first**:
`compared` is the answer to *were the two periods told apart*, `comparing` is
the answer to *were two asked for*, and only the first may draw a change. The
old code answered the second question and printed a percentage.

**A time series re-sorted into a ranking.** `shape()` ended with an
unconditional sort by the first metric, discarding the `orderBys` this module
had just sent to GA4 and GA4 had honoured — so "sessions by day for July"
came back in date order and was rendered 2nd, 3rd, 4th, 1st. Every number
right, and the one thing a time series is for gone. It sorts only when
nothing was asked for, which is what that default was written to cover.

**And "total" was the total of whatever came back.** GA4 returns totals only
where `metricAggregations` was requested, which this module does not request,
so the fallback sums the rows — and under `limit: 25` on a property with
three hundred cities that is the top 25 presented as the whole. `totals_of`
says which it is, and Google's own totals row is read where one is there
rather than being summed over. It reaches the **model's payload** too, with
`compared` in place of `comparing`: `narrate()` is handed the shaped numbers
precisely so it cannot introduce a figure the table does not show, and
handing it a comparison flag for a comparison nothing computed is the
invented-figure failure `hub/audit_summary.py` exists to refuse, one module
over. `test_analytics_ask.py` asserts all of it.

## A client's document, published to the agency's own blog

`hub/ghl_blog.py` publishes a client's llms.txt into Smart 1 Suite as a blog
post — a public URL on somebody's blog, which is as client-facing as anything
here gets — and `_location()` fell back to `GHL_COMPANY_ID` /
`SUITE_COMPANY_ID`. That is the mistake `hub/ghl_contacts.py` spends a section
of its own docstring on (*"companyId is not locationId"*) and that
`hub/suite_opportunity.py` was fixed for, and **this was the third module to
make it**: on this deployment those variables hold the same value as the
company id, so a companyId went out as a locationId and **the client's
document was published to the agency's own blog**, under the agency's domain,
titled with the client's name.

Nothing errors, which is the whole difficulty. The agency location is a real
location with a real blog, real authors and real categories, so the post is
created, a URL comes back, and the only sign is that it is the wrong blog. The
location is its own setting now, and a value matching the company id is
refused **by name** — *"set to the same value as the agency company id"* and
*"no location is set"* send somebody to two different places.

**A guard switched off by exactly the failure it was written for.**
`slug_taken()` swallowed every error and answered `False`, and the caller
reads `False` as *there is no post at that address*. This module's own
docstring says a missing scope "produces a 401 from HighLevel that looks like
a bad token" — and `blogs/check-slug.readonly` is the scope whose absence made
that answer `False`, so a token missing it silently created the second post
the guard exists to refuse, the one leaving *"two files claiming to describe
the same client"* in the comment directly above it. It is tri-state now, and
the publish still goes ahead: refusing every first-time publish over a missing
read scope is a check somebody switches off, which is the `QR_CODE_RULES`
lesson. What changed is that the answer carries `slug_checked` and says the
check did not run, rather than implying it passed.

`check_access()` did not test that scope either — three of the four readable
ones, and the missing one was the only one whose failure is silent — so it
reported a token healthy that would go on to publish duplicates.

**And the link was built from the slug we asked for.** HighLevel suffixes a
collision rather than refusing, and `urlSlug` comes back on the response and
was never read: the address handed to somebody as the published one pointed at
a page that is not there. It reads the assigned slug now and says when the two
differ. A `blog_id` naming nothing fell through to `blogs[0]` the same way —
a stale id published a client's document to a different blog on a different
domain, reporting a clean success — and is refused, naming what is actually
there. A `domain` field arriving with its own scheme composed
`https://https://…`, which is a dead link presented as the live one.

`test_ghl_blog.py` asserts all of it, including that no refusal carries the
token: `BlogError`'s own docstring promises that, and a 401 body from
HighLevel has carried token fragments before.

## Publishing is a prompt, not a panel and not a button

Every blog post, JSON-LD block, FAQ accordion and alt tag we produce has to be
typed into a CMS by somebody. Smart 1 Sites (the Simvoly whitelabel) exposes
projects, plans and websites through its API — not page content — and a
client's WordPress is someone else's server with someone else's plugins on it.
So `hub/cms_publish.py` does not publish, and it no longer asks a rep to retype
thirty fields either. **It writes a prompt for Claude in Chrome.**

**Claude → Smart 1 Sites** and **Claude → WordPress** sit on the blog table,
the schema table, the FAQ table and the alt-text table. Tick what is going up,
the CMS opens in a new window, and the panel hands back one block of text: the
rules, how that CMS behaves, and the finished content. The rep signs in, pastes
it into the Claude side panel on that tab, and approves each action.

What that changes about what a good output is:

- **The prompt carries the content, not a description of it.** The browser
  agent cannot see this Hub, so "add the blog post" is useless — the whole body
  HTML, the slug, the categories and the author have to be in the pasted text.
- **It carries the rules that stop it improvising.** Approved copy is
  reproduced, not paraphrased. A missing field is reported, not guessed at. A
  category that does not exist is created with the exact name rather than filed
  under the nearest match. Nothing is published; everything stops as a draft
  for a human. An agent left to its own judgment on any of those produces
  something plausible that nobody approved.
- **It never carries a credential.** This Hub stores the site login and
  password under Client Setup, and interpolating them into a block of text
  destined for a chat window is the easiest possible mistake to make here. The
  human signs in first; the prompt says so and tells the agent not to ask.
  `test_alt_text.py` asserts no stored credential reaches any of the eight
  CMS × kind prompts.
- **The field-by-field list stays underneath it.** Claude in Chrome is not on
  every machine, and a rep fixing one field should not have to dig it out of a
  wall of prompt text.

Three things that follow, unchanged from when this was a paste panel:

- **Nothing is invented.** With no site URL on the client there is no WordPress
  admin to open, and the panel says which setting is missing. A guessed
  `https://<clientname>.com/wp-admin` opens a stranger's login page.
- **Smart 1 Sites opens through the Hub.** Sites Admin already holds every
  Simvoly project and already has the builder SSO, so the project page is the
  address that gets a rep into the right builder without a second password. The
  match is by **domain**, never by name, for the reason `hub/sites_match.py`
  gives at length — and two projects on one domain returns the search rather
  than picking one, because the wrong pick edits another client's website.
- **A field with no home says so.** Simvoly's blog has categories and no tag
  field; the prompt tells the agent to say so rather than put the tags
  somewhere else.
- **Where an FAQ block goes on the page is asked, not left to the agent.**
  "Somewhere sensible" is how an accordion lands above the hero on one page and
  in a sidebar on the next. The panel offers the positions (`PLACEMENTS`,
  default: the last section before the footer) and the answer is written into
  the prompt as an instruction. Changing it rebuilds the prompt rather than
  patching the text — a panel showing one position while the clipboard holds
  another is the worst version of this.

The window is opened in the click handler, before the fetch — a `window.open()`
inside a promise callback is a popup the browser blocks, and a blocked popup
looks exactly like a button that does nothing.

## Alt text is read from the site, not invented for it

`hub/alt_text.py`. The Schema Builder and the FAQ Builder both read a client's
own pages and hand the result to a CMS; alt text was the gap, and it is the
finding an audit reports most often because fixing it by hand means opening
every page and writing a sentence per image.

**The first five sitemap pages, by default.** A crawl is one request per page
against somebody else's server, and a 200-page site is 200 requests before a
word is written. Five is the home page plus the top-level service pages on
almost every site we build. The limit is a parameter so a second pass can go
deeper deliberately, rather than a default that hammers a client's host.

**`alt` absent and `alt=""` are different answers.** An empty alt is a decision
— this image is decorative — and a missing one is an omission. Report both as
`""` and every genuinely missing alt hides inside a list of images that were
already handled correctly, which is exactly the number the audit is counting.

**A decorative image keeps its empty alt.** The whole rewrite path exists to
fill in blanks, so the one case where blank is *correct* has to survive it: a
1px spacer described as "air conditioning repair in Dublin" is worse than the
spacer with no alt at all. `is_decorative()` reads `role="presentation"`, the
filename hints a builder emits, and a tiny declared size.

**Three of the writing rules are enforced, not requested.** Length (both
engines and every screen reader truncate around 125 characters), the "image
of" preamble (a screen reader already says it is an image), and stripped
markup. Asked politely, a model gets each of them wrong often enough to matter,
so `_clean_alt()` runs over whatever comes back — and over anything typed by
hand in the panel, or the rule holds only until someone edits the box.

The output is the same two shapes as schema: **See the code**, which prints the
old tag and the new one because a find-and-replace needs the string that is
actually in the file, and the two Claude buttons above.

## Getting a file back out is storage's job, not each module's

`hub/storage.attachment_url()` and `hub/storage.bundle_zip()`. Three modules
were solving this separately and a fourth was about to.

**A cross-origin `download` attribute does nothing.** Browsers ignore it, so an
`<a download href="https://res.cloudinary.com/…">` opens the image in a tab and
the button reads as broken. `fl_attachment` is what actually works — Cloudinary
sends `Content-Disposition` — and the name after the colon is what the file is
called on the way down. In the SEO Image Pipeline that is the whole point of
the tool: a file that lands in Downloads as `v1699_xk3.webp` has lost the work.
`attachment_url()` rewrites a Cloudinary delivery URL and returns anything else
unchanged rather than into something that 404s.

**More than one file is a zip, not a loop.** A browser blocks every download
after the first when they are triggered in sequence, so the person gets one
file, no error, and no reason to think anything went wrong. `bundle_zip()`
fetches each stored file, de-duplicates names (two images can genuinely share
one, and a zip silently keeps only the last), skips what it cannot fetch into a
`MISSING.txt` *and* returns that list so the page can say so too, and caps both
file count and total bytes — this streams through the Hub, and an unbounded
"select all" on a thousand-row archive is the one request that takes two
gunicorn workers down.

**A zip is delivery, and delivery is what Cloudinary bills.** A credit is a
gigabyte delivered, so `bundle_zip()` records the bytes it pulled rather than
the number of files — counting files would make a 40 KB thumbnail and a 4 MB
hero cost the same on the usage page. Single downloads redirect to the CDN
instead, so they cost the Hub nothing and are not counted here.

The SEO Image Pipeline's saved step and its project archive both offer download
of one or several, and the archive's row actions are icons — download, copy
URL, edit alt, delete — each carrying a rollover that says what it does and, for
delete, that it cannot be undone. An icon with no label is a guess.

**Every archive row needs an id.** The row's buttons all address it by one, and
the Image Optimizer's save path wrote rows without it: those images appeared in
the archive and then ignored every button on their row. `load_archive()`
backfills once on the first read that finds one missing.

**And a gallery drew the original into a box a fraction of its size.** Every
tile in this Hub was `<img src="{the full asset}">` — the client gallery's
64x48 row thumb, Client 360's 120px tiles, the picker's grid, the SEO archive.
A client uploads forty photographs off a phone and the staff gallery delivers
something like a hundred and sixty megabytes to draw forty small boxes.
Nothing errors and the pictures are right; the cost is a slow page and a
Cloudinary bill, which is charged in **credits, one of which is a gigabyte
delivered** — so this is the line item, not what we upload.
`hub/storage.thumb_url()` had been written for exactly this, docstring and
all — *"Galleries must never request the full asset"* — and had **no caller**,
the fourth declared-but-unwired integration point in this corner after
`RECORD_HOOK`, `io_creative` and `manifest()`. The one place the rule was
being applied was Google Drive, whose thumbnails are asked for at `sz=w400`,
because that one is not ours and somebody had to think about it.

`preview_url()` is the sibling that takes a stored delivery URL, and four
rules keep it from turning a working tile into a broken one. **Anything not
ours comes back unchanged** — a stock CDN, a Drive link, a `data:` URI — the
answer `attachment_url()` already gives, since rewriting a URL we do not own
produces a 404 where there was a picture. **Only an image**: Cloudinary keeps
images, raw files and video in separate namespaces, so an image
transformation on a PDF is "not found", the lesson `cloudinary_sink.destroy()`
paid for; the row's own `resource_type` is believed first and the URL's own
segment decides when the row says nothing. **Idempotent**, so a row rewritten
here and handed to a caller that rewrites again does not chain two. And
**`c_limit`, never `c_fill` or a bare width** — it caps without upscaling or
cropping, so a 180px logo stays 180px rather than being blown up and
re-encoded.

**One derived size for the whole Hub, and deliberately not one per box.**
Cloudinary bills a credit per thousand transformations and caches each
derivative separately, so a 64px row, a 120px tile and a 300px cell asked for
at their own sizes is three derivatives of every image in the account to save
bytes nobody would notice.

**It is derived on the row, never stored and never mirrored into JavaScript.**
`SavedImage.to_dict()` and `seo_images.load_archive()` add a `thumb` beside
the `url`, and `image_audit._read()` does it in the one funnel all six store
readers pass through — deriving it per reader is six chances for the seventh
store to draw full assets and for nobody to notice. Stored, it would outlive
the size it was computed at and be *restored* from the jsonstore mirror rather
than recomputed, the `client_key` rule. Written into the twelve templates that
draw a tile, it would be the drift `hub/storage.py` exists to stop. Each tile
reads `thumb || url`, so a row from a producer nothing has wired yet draws
exactly what it drew before.

**And the two places it must not happen are the deliverables.** The gallery's
copy button and the CSV export hand out `<img>` markup that goes onto the
client's own website; a 400px gallery thumbnail pasted there is the wrong file
on their page for ever. Those keep the original, as does the lightbox, whose
whole job is the full asset.

**What is still full-size is a table with a reason against each row**, not an
omission — a screen silently missing from a completeness report is the same
failure the report is about. Logos (small, one per page, as often observed off
the client's own site as stored by us), the just-uploaded strip (that URL comes
back from the Cloudinary widget in the browser and never passes through a row
here, so previewing it would mean a copy of the rule in JavaScript), and the
lightbox, whose whole job is the full asset. `test_image_download.py` fails on
a tile with no reason on file **and** on a reason whose line has gone, and it
started green.

**And that staleness half is what retired the one exemption that was wrong.**
The Display Ad Builder's background grid went into that table as *the one
gallery this cannot reach from Python* — the renderer is TypeScript and its
rows do not pass through `hub/storage.py`. They pass through
`hub/ad_builder_link.client_gallery()`, which is Python: the editor fetches
`/_hub/gallery` precisely because **the renderer does not know who our clients
are and must not learn**. So the preview is derived there, on the Hub side,
rather than mirrored into the renderer — and the check reported its own
exemption as stale the moment that was done, which is the whole reason it
carries markers from the lines themselves rather than file names.

**A tile and a picture are different things there.** The grid draws `thumb`;
`applyBackground()` and the magnifier read `url`, because a 400px preview
placed behind an ad is the wrong file in the creative, and "see it full size"
means what it says. Only the gallery source carries a preview at all — stock,
AI and a fresh upload pass none and fall back to the asset, so they draw
exactly what they drew before.

**And a fixture that does not look like the real thing leaves the rule
untested.** `test_ads_module.py` seeded rows as
`res.cloudinary.com/x/<id>.jpg` — no `/upload/` segment, so `preview_url()`
correctly declined to touch them and the first version of the assertion passed
against a preview that had never been computed. The fixture carries the real
delivery shape now. The assertion that went with it was worse: `all(row.get
("thumb"))` is true when `thumb` falls back to the asset, so it passed on the
bug as well — it requires the cap now, and reads every field with `.get()`,
because an assertion that raises on the missing field takes every check after
it out of the run.

`test_image_download.py` asserts all of it, including that the image picker
still returns a zip now that it runs on the shared builder.

## A cache that is careful with credits, on one worker in two

`modules/bg_remover`. The module docstring opens by saying it is deliberately
careful with credits and lists how: the balance is read before you spend
anything, files are validated so a credit is never spent on something that was
going to fail, and **results are cached by content hash, so re-running the
same image — a double-click, a retry after a resize tweak — is free**. The
last of those was a module-level dict, and gunicorn runs two workers.

So it was free about half the time. The second request lands on whichever
worker the balancer picks, that worker's dict is empty, remove.bg is asked
again and charges again. Every screen reports a clean success either way, and
the only evidence anywhere is a credit balance falling faster than the number
of cut-outs anybody made — which nobody reconciles, because the tool has an
account panel that reports the balance and no reason to doubt it. That is the
`_state`-is-per-process trap this file already names for the scheduler panel,
`clients_registry`'s two-minute cache and `suite_panel`'s double-submit claim,
on the one module whose stated design goal is not spending money twice. The
claim is a file on the shared data disk now, exactly as suite_panel's is.

**It is a cache and it is written as one.** Deliberately not through
`hub/jsonstore.py`: there is nothing to restore, a wiped disk costs one credit
on the next retry and no data at all, and mirroring a few megabytes of PNG per
cut-out into the database to protect against that would be the backup rule
applied where it does not earn anything. It is bounded on **total size** as
well as on age — an unbounded cache on the 5 GB disk takes every other module
with it — and nothing in it may raise, because a cache that can break the tool
it accelerates is worse than no cache: every failure in it costs a credit on a
retry and nothing else. A read that hits the disk warms the worker that
missed, so the next request on that worker is local.

**A cut-out was filed with dimensions the tool had already measured.**
`api_save` passed `width=res.get("width")` into the client's gallery, and
`res` is built from a `StoredAsset` — which has no `width` field and never
has. So every cut-out this tool has ever filed carried `None` for both, in a
row whose whole purpose is to describe the image, while `api_remove` opened
the identical bytes two functions earlier to draw the result panel and threw
the answer away. `_dimensions()` is the one reading now, called from both, and
it answers `(None, None)` for something it cannot read rather than zero. It
measures **what is being stored**, not what the browser says: a dimension the
page supplies is one the page can be wrong about.

**And two caps contradicted each other, with only one of them on screen.**
The page offers ten images at twelve megabytes each; `MAX_CONTENT_LENGTH` was
set to forty. A batch inside every rule the page states was therefore refused
by the framework *before the view ran* — and Werkzeug answers that with an
**HTML** 413, which `.then(r => r.json())` cannot parse, so the page reported
`Failed: SyntaxError` and said nothing whatever about sending fewer at a time.
Every one of this module's own carefully worded per-file refusals — over the
size, not an image, empty file, more than ten — sits behind that gate and was
unreachable for the batch that most needed one. The cap is **derived** from
the two numbers the tool actually offers rather than typed a third time, so
the framework cannot go back to refusing what the screen invites, and a 413
reached from any other direction now arrives as a sentence in the same JSON
shape as every other refusal here. `/health` reports all three numbers, so a
screen need not restate them either. `test_utm_bg_tools.py` asserts all of it.

## A GPT ad is five deliverables, and four of them used to arrive separately

`modules/gpt_ads` (`/tools/gpt-ads`) builds one GPT ad pack for a client and
hands ad operations a single ZIP. `hub/gpt_ads_spec.py` is the spec — the five
deliverables on their requirement sheet, the copy limits, the readiness gate,
the landing-page check, the AI prompts and the export — read by the module, the
brief and the manifest alike, the same way `hub/proposal_spec.py` is.

Nothing on that sheet is hard. What was slow is that it arrived in five places:
the image in a thread, the copy in a doc, the URL in an email, the brand
colours in somebody's memory and the offer in the client's own words three
weeks ago. A pack went over with four of the five, came back, and the launch
date moved.

**The image spec is in `hub/creative_specs.py`, not in the module.** 1:1 is
required so it is a `ratios` entry and a *fail*; 256x256 is recommended so it is
`min_size` and a *warn* — a 200px square runs, it just runs soft, and collapsing
those two into one warning is what teaches people to ignore warnings. That unit
is the one thing in the kit that is **not** transcribed from S1M CREATIVE SPEC
KIT 2025, so it carries `source` and `catalogue()` no longer claims the kit for
all of them. No file-weight ceiling and no format list are published for the
placement: none is invented, and the notes say so.

**The image is measured, never described.** The pixels are read off the bytes
with `hub.images.dimensions` before anything is stored, and a file that is not
1:1 is refused at the door with its measured size and a pointer at the resizer.
A form field saying "1080x1080" over a 1200x628 crop is the ordinary way a
rejected ad happens, and a red flag on a pack somebody exports anyway is not a
gate. An upload is stored **exactly as it arrived** — "provide the
highest-quality brand-approved version" is on the sheet, and re-encoding an
approved asset to save a few KB is how a logo picks up artefacts nobody
approved. Only generated squares go through `hub.images.optimise`.

**The landing page is fetched, not ticked.** "Confirmation that the page is
live and mobile-friendly" is a deliverable, so it is answered by requesting the
page: status, redirect chain, and whether the document declares a viewport —
reported as *declares a viewport*, not as *mobile-friendly*, because the first
is what was measured. A check that could not run says **not measured** and never
shows a tick, and changing the URL discards the check that belonged to the old
one rather than leaving a green light against a page nobody has fetched.

**The copy checks are code, and they are the Social Planner's.**
`hub/gpt_ads_spec.py` imports `MONEY_RE`, `PHONE_RE`, `DEADLINE_RE`,
`SUPERLATIVE_RE`, `PLACEHOLDER_RE` and `BANNED_PHRASES` from `hub/social_plan.py`
rather than restating them — same failure mode, so the next fix to those
patterns lands once. A price, a percentage, a phone number or a deadline that is
not in the offer or brand fields a human filled in is a **block**; superlatives
warn. So does an expiry date already in the past, except that one blocks:
shipping it means running something false.

**The character limits are ours and say so.** The sheet publishes none, so
`LIMITS` is house guidance, labelled as house guidance on the screen and in the
brief, and going over is a warning — never a block, because a block implies a
rejection nobody has published. Everywhere else the sheet is silent, the export
prints *not supplied* rather than a plausible value.

The ZIP carries the square, `ad-copy.csv`, a plain-text brief in the sheet's own
section order and `manifest.json`. When the image cannot be embedded the pack
still goes, with the reason at the top of the brief and in the manifest — three
files where there should be four is a difference ad ops assumes they caused.

There is **no JavaScript mirror** of the gate. Target areas and the creative
classifier each carry one and each needs a test proving the halves still agree;
that cost is paid twice already. Every save returns the server's own readiness
and the page renders what comes back. `test_gpt_ads.py` asserts all of it.

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

## A stale list that can only be read is a list nobody works

`hub/stale_creative.py` says how long it has been since we last made creative
for each client running a product today, and every row was a fact with nothing
to do about it: a rep read the number, went and found the client somewhere else,
and the row aged another week. The end of the row is three actions now —
**Evergreen**, **New** and **Create** — each opening the thing that already
exists rather than a fourth copy of it. *New* is `/campaign-request.js`, the
same Campaign Change Request form Client 360 and the dashboard open, handed the
client's real insertion orders from `/api/c360` so the campaign/IO dropdown is
populated rather than a free-text box. *Create* is
`/tools/display-ads/_hub/start?client=…`, which fills the client in and files
the build against that record.

**The Source column went with it.** Which of our tools filed the last creative
is not a decision the person reading the row makes — the note
`modules/ads_builder/logo.py` already makes about naming Brandfetch to somebody
who cannot rotate its key. Each creative in the expanded panel still says where
it came from, which is where opening it is the point.

**Evergreen is the answer to a row that is not a gap.** An always-on brand
spot, a sponsorship board, a rebate banner that runs unchanged until the offer
ends: the creative is fixed for the campaign, so the elapsed time is not
something anybody is going to close, and left in the list those rows are
permanent red on a report whose whole job is to say what to act on this week.
`hub/creative_evergreen.py` is that overlay, and four rules hold it up.

**It is applied on every read of the cache, not baked into it.** The audit is
cached for five minutes and there are two gunicorn workers, so a mark taken in
one of them would go on being ignored by the other until its own cache expired —
a button that appears to do nothing, which is exactly the failure
`hub/client_urls.missing()` had to undo. **The mark is stored against the
client's name, never the derived match key**, for the reason `hub/client_key.py`
gives at length; the key is re-derived on read with whatever matcher the report
is using. **Nothing disappears in silence**: the row moves to an Evergreen
section with the group it came from, who marked it and when, and one press puts
it back — a list that quietly gets shorter cannot be told from a list that
failed to load, and the tile row carries the count beside the other five.
And **a mark says who and when**, because "this is evergreen" is somebody's
decision about a campaign and one nobody can attribute is one nobody can
revisit.

**The blueprint had no guard at all.** `/qa/stale-creative` and its APIs
answered 200 to anyone with the URL — every active client and how far behind we
are on each — because `wsgi.py` wraps only *dispatcher-mounted* modules in
`AuthGuard` and the hub app guards its own views one at a time. The gate sits on
the blueprint now rather than on each route, the arrangement
`modules/commercial_builder/__init__.py` arrived at for the same reason: the
write route added here must not have to remember, and neither must the next one.

**And it was reading two of its four sources.** `SOURCES` is a table of field
names for four modules this file does not own, and three of the four guessed
wrong — silently, each differently, on the report whose entire purpose is
*what have we made for this client*. **Image Picker** reflected over
`SavedImage` expecting Flask-SQLAlchemy's `.query`; it is a plain declarative
model with its own `session()`, so `_load_source` returned `[]` at the guard —
and that is the store `filing.file_asset` writes to, which is every asset every
tool files against a client. **Image Creator** asked for `created_at` /
`updated_at` where the index writes `created` / `updated`, so every row was
dropped by `if not when: continue`. **Commercial Builder** asked for
`client_name` where the row carries `client_id`, and that one is the worst of
the three because the rows were *not* empty: the source counted as **live**,
inflated `totals.creatives`, and every record was then dropped for having no
client. So a client whose gallery, canvas graphics and commercial were all
produced this month read as *"No creative on file"*, with two of the three
showing only as a footer line and the third showing as nothing at all.

**`hub/image_audit.py` reads exactly those stores and had them right the whole
time.** Two modules each guessing at one store's columns is the drift
`hub/storage.py` exists to stop, wearing a report — so the image sources are
not described here any more: `store` names one of its `STORES` entries and the
tuples read that reader's normalised shape. The reflection branch is gone with
them, because a table of column names for somebody else's model is what broke
this; a new source writes a reader, and `_commercial_rows()` is the example.
That one is **an approved render rather than a project row**: a cut nobody has
watched is not creative the client received, the distinction `approve_render`
already draws, and it resolves the name through `cb_clients` rather than
guessing at a column. `_thumb()` went with it — it was a second reading of
`hub/storage.preview_url()` that disagreed on both halves, `c_fill` where the
rule is `c_limit` and a second derived size Cloudinary caches and bills
separately, with neither of that function's guards, so a video URL was rewritten
as an image transformation.

**And `measured` covered one half of a join.** `_registry_clients()` tries four
paths and swallows each failure, so a client list that refused returned `[]` and
the audit reported **nought clients** while the creative sources answered
perfectly well — `measured: True`, and held as the day's answer. It is both
halves now, and each is named, because *the client list refused* and *no source
answered* send somebody to different places. The page draws it: `measured` was
on the payload for exactly this and **no template read it**, so a morning where
everything refused rendered the full six tiles and a "No creative on file"
section naming the whole book, with the only clue being *"Sources read: none."*
in the footer below everything. `test_stale_creative.py` seeds each store and
requires **every** entry in `SOURCES` to produce a record with a client, a date
and a title — a sweep, because a test naming the three that were wrong proves
nothing about the fourth.

**Four smaller ones, each the same shape: a rule computed and then dropped
on the way to the reader.** `campaign_assets.report()` put `labels()` on the
payload and `labels()` copied out the label and its source, throwing away the
`warning` `field_check()` had just computed — so the page's own
`(d.warnings||[])` loop read a key the report never wrote and was always
empty. Renumber `field_2346` and the report says *"No campaign in scope is
waiting on a clarification or an asset"* about the whole book, because
`measurable()` still passes on `clarification` alone; the one warning that
exists to make that visible stopped at the function that produced it.

`file_orphan()` ran **two vocabularies through one door**: the folder key from
`RECONCILE_KINDS` went through as the *provider*, and the kind was looked up in
`_KIND_FOR`, which is keyed on `STORES` names. Only `seo_images` is in both, so
eight of the nine fell through to `"upload"` — which `filing.SOURCE_LABELS`
calls **"Client upload"**. Attaching an orphaned commercial filed it in the
client's gallery as a file *the client sent us*, under a bare `commercials`
chip the gallery has no heading for, in the tier that claims nothing.
`_FOLDER_FILING` maps each folder to the `(kind, provider)` pair its own tool
files with, so a row this audit writes is indistinguishable from one the tool
wrote — which is the only way the gallery's grouping stays true. It also
surfaced a live one: `modules/social_planner` has filed under
`provider="social_request"` since it was written and the table never named it,
so a photograph a location manager sent in arrived as a bare key under no
heading and, unlisted, in the tier that claims nothing.

`io_records._summary()` stores the **shared** campaign start, and the wizard
clears it the moment one product is given its own term — *"Because at least
one product runs its own dates, I'll ask for dates product by product"*. So
`start` is `""` on every multi-product IO, and `io_reconcile`'s `started`
could never be true for one: the report's own headline urgency, *"it should be
running now and nothing is trafficked"*, silently blind to a whole class of
orders. `flight_start()` is the shared reading — the campaign start where
there is one, otherwise the earliest line's — and the record still stores the
agreement rather than the derivation.

And `check_orphan_templates()` **could not see the orphan in its own
templates folder**. Its include pass had no *"not its own name"* guard, the
one the bare-`.html` pass beside it has always had, so a template whose header
comment reads `drop {% include "me.html" %} into the dashboard` registered
itself as rendered. `_scorecard_stale_creative.html` did exactly that: written
for the dashboard, fetching a live and tested `/api/qa/stale-creative/scorecard`,
included by nothing, with the check reporting no orphans at all. The guard is
there now and the tile is wired where the partial's own first line asks for
it — above System status, the reason `hub/celebrations.py` gives, because a
key that is set is housekeeping and a client we have made nothing for is work.

## A report that has been opened has already been run

`hub/report_cache.py`. Every QA report and every report-shaped tool page
re-ran its whole build on each open — a year of QuickBooks invoices, a walk of
the GoHighLevel pipeline, a full Knack pull, a name match per row, a Cloudinary
scan of the whole account. For an answer that changes when somebody edits a
record, which is a few times a day. Two people opening the Sites Billing Report
in the same minute paid for it twice; one person pressing Back paid for it
again; the Google orphan list ran `suggest_for()` over the whole book on every
page of twenty-five, so walking two thousand orphans ran two thousand
suggestions eighty times over.

So a report runs **once, on the first open of the day**, and every open after
that reads what was written. **Refresh** re-runs it. Six rules hold it up, each
a way a cache lies.

**The day is the report's own day.** The key comes from `date.today()` — the
same clock `active_clients()` measures "this month" from and `stale_90()`
measures ninety days from. A cache on any other clock serves yesterday's rows
under today's heading on exactly the days it matters.

**A failed run never becomes the answer.** If the build raises, or comes back
carrying `error`, `unavailable` or `measured: False`, it is not stored: the
previous run is served with the failure named beside it. The `knack_products`
rule, with a second edge on it — "QuickBooks isn't connected yet" is a
perfectly successful function call and is not an answer, and storing it would
leave the page saying so all day to somebody who connected QuickBooks at ten
past nine. Which is why `is_answer()` is a shared test rather than a check per
report, and why `stale_creative.build_audit()`, `domain_links` and
`sites_match` now return `measured`: every source in all three degrades to an
empty list rather than raising, so a morning where they all refused produced a
complete-looking page saying every client was overdue for creative and every
Simvoly project was already matched. `sites_match.sites_error()` names that
one — the Sites module failing to start returned no projects at all, which is
the emptiest possible confident answer. With nothing stored to fall back on
the exception is **re-raised** rather than answered with a payload of our
own: half these reports are a columns/rows table and half are not, and a
caller handed the wrong shape fails somewhere that says nothing about why.

**The age travels with the rows.** Every payload carries a `cache` block and
every page renders its `line` — "Run at 9:14 AM today; this is that copy." A
cached figure with no date on it is read as today's, which is the whole way a
cache comes to mislead. `test_report_cache.py` asserts the line is on the page,
not merely in the payload.

**Refresh is a POST.** A GET builds only when nothing is held for today and can
never force a rebuild, however the query string is spelled — a GET that
rebuilds is one a reload, a prefetch or a link preview fires without anybody
asking, which is the entire cost this exists to stop. `?refresh=1` on Stale
Creative was exactly that door. `hub/domain_purchase.py` settled the same point
for the domain calendar.

**A write drops what it changed.** Marking an accounting request, assigning an
invoice to a partner, skipping a client that needs no dashboard, attaching a
Google property, accepting a discovered URL, matching a Simvoly project — each
takes a row off the report the button is on. Left cached, the row is still
there on the next open and the button reads as having done nothing, so it gets
pressed again. The drop lives **beside the write** rather than at the route:
`qa.skip_dashboard()` drops its own reports, `google_index._forget_reports()`
runs inside the sweep *and* `set_client()` *and* `apply_domain_matches()`, and
`client_urls._forget_registry_cache()` gained the reports next to the registry
cache it already cleared for the same reason. Two descriptions of when to
invalidate is one that drifts.

The **evergreen** mark above needs none of this, and the reason is worth
keeping: `_apply_evergreen()` is applied on every *read* of the audit rather
than baked into it, so a mark taken in one worker is never held by the other's
copy. That rule was written against a five-minute memo; a day-long hold puts a
much longer fuse on the same failure, and the mark still costs one small JSON
read per page instead of an invalidation somebody has to remember. Where an
overlay can be applied on read, that beats dropping a cache.

**A free-text search is not a cache key.** `q=acme` and `q=acm` are two files
on a 5 GB disk and a search box types one per keystroke. Where a report filters
after it builds, the *build* is cached and the filter runs per request —
`domain_links.orphans()` and `google_links.orphans()` are split that way, and
their sort moved above the search so the order does not change with what is
typed. Where it cannot be split, `cacheable()` refuses the key and the report
runs live: a slow search beats a full disk.

Entries are `durable=False` — a cache of something that rebuilds by being asked
for again, so mirroring it would cost a write per report per day for rows
nobody would restore. A deploy wipes the disk and the first person to open each
report pays for one run, which is what every open cost before this existed.

`/api/report-cache` says what is held and how old each entry is — names, days
and row counts, never payloads, because a report's rows carry client names and
this is read into a page. `/api/report-cache/clear` empties it, behind
Utilities, because pressing it makes every report on the Hub run again.
`REPORT_CACHE=off` turns the whole thing off for a deployment that must see
live numbers, and the page then says it is not cached rather than showing an
age it does not have.

**Two tests turn it off, and that is the honest thing rather than a
workaround.** `test_domain_links.py` and `test_google_links.py` swap a source
out from under a report between assertions — a Knack that answers, then one
that times out — which is the one thing a report held for the day cannot see.
They assert the report; `test_report_cache.py` asserts the holding.

**And `measured: False` was read by the cache and by nothing on the page.**
That flag is the whole reason a refused run is not frozen into the day's
answer — and `qa_report.html` never looked at it. Its branch order was
`error`, then `needs_qb`, then `unavailable`, then an all-clear, so a report
that carried its refusal in `note` alone fell through to **"Nothing to
report — all clear ✓"**. Four returns are in that shape:
`upsell._unmeasured()` when the client list or the site audits refuse,
`prospect_queue._unmeasured()` when the lead store does, and the two
uploads-gallery refusals in `qa.uploads_not_in_suite()`. So a Knack timeout
drew a green tick directly above the report's own sentence reading *"which is
not the same as nobody"*, and *"Couldn't read the uploads database"* was
rendered as every client's files being safely in Suite — the page contradicting
the line printed beneath it, which is worse than either half alone. The
page's own comment had said exactly this for three years: *"'We looked and it
is fine' and 'we could not look' are different answers, and rendering both as
a green tick is how a page ends up confidently telling you the opposite of the
truth."* It said it about `unavailable`, and `unavailable` was the one case
that already worked.

`cannotLook(d)` is the one reading now — `unavailable` keeps its action button
because it has one, and `measured: false` becomes the same panel with the note
as its message. A report that genuinely found nothing **keeps its green tick**,
because crying wolf on every clean run is its own failure and is the one that
gets a page ignored. `campaign_assets.html` had this right all along and was
the only one of the QA screens that did.

**And `is_answer()` did not read `needs_qb` either, which is the same gap on
the other side of the wire.** This module's own docstring names it — *"it is
also what stops 'QuickBooks isn't connected yet' being cached for a day by
whoever opened the report before anyone connected it"* — and `serve()`'s
comment three hundred lines down calls the connect call-to-action a payload
that "ran and could not measure". `is_answer()` tested `error`, `unavailable`
and `measured is False` and never `needs_qb`, so all three billing reports
stored it: somebody who opened Invoice Off at 08:50, before the QuickBooks
connect at 09:10, left *"QuickBooks isn't configured"* and an Open System
Status button on Customer Billing Comparison, Invoice Off and Sites Billing
for everybody for the rest of the day — and the person who had just connected
it read that as the connection having failed. A POST-Refresh cleared it; a GET
never could.

`test_qa_reports.py` asserts both directions, and it is **a sweep rather than
the four that were wrong** — a test naming those four proves nothing about the
fifth. It reads the **AST** of every module a report is built from and fails
any return of no rows carrying nothing the page draws, because three of those
modules explain this very trap in prose and a check that matches text reports
the explanation as the defect. The decision block is **lifted out of the page**
between its own markers and run in node, the arrangement `test_menu_layout.py`
uses over `hub-crumbs.js`: a copy restated in the test is a third thing to keep
in step, and a regex pinned to one line's formatting fails the day somebody
reindents it, which is how a check gets switched off.

## An ad copy request is fourteen fields, and the form asked four

`hub/ad_copy.py`, `/api/client/ad-copy`, and the button on Client 360 and on
the dashboard. Ad Copy was a **Campaign Change Request with its subject
pre-written** — one shared object, four boxes, and a rep retyping the client,
the campaign, the current order number and the media partner out of the
record on the screen behind them. The campaign team's own form has fourteen
fields, and ten of them arrived blank on every request the Hub raised, so the
first thing anybody did with one was go and ask.

The ids are pinned (`AD_COPY_FIELDS`, one environment override each) for the
reason `hub/knack_api.py` gives at length. What is **not** pinned is the
object: nobody has told us its number, and inventing one writes ad copy
requests into whichever object answers — which reads on every screen as a
form that worked. So it is **discovered from the ids**: whichever object
carries `field_1804` is the Ad Copy Request object. Knack publishes each
object's fields inline on some plans, and where it does that is one call;
where it does not, the two request objects this Hub already knows are tried
before the walk, the walk is bounded, and a discovery that fails **names
`KNACK_AD_COPY_OBJECT`** rather than falling back to something plausible.

Four rules on the prefill, each a way to be confidently wrong on a form
somebody sends without re-reading it:

- **Exactly one candidate, or none.** A client with two campaigns gets a
  dropdown; a client with one gets it filled in, with the order number and
  the media partner that campaign's insertion order carries. Filling in the
  first of two is a plausible answer nobody proof-reads, which is worse than
  a blank — the `client_key` rule, wearing a prefill.
- **Nothing is invented.** The due date, the deadline and the change itself
  are blank: there is no source for any of them here, and a date the Hub made
  up is a date the campaign team works to. Submitted Date is the clock,
  because sending the form *is* submitting, and Status opens on whatever
  **Knack itself publishes** as that field's default — reading a default is
  not the same as choosing one.
- **An empty answer says which kind of empty it is.** "This client has no
  insertion orders", "we could not read the client list" and "this session
  has no account behind it, so there is no email address" are three
  situations and they read identically as a blank box. `current_user()` is
  deliberately *not* what fills Seller Name: it answers **"Shared login"** for
  a `PANEL_PASSWORD` session, which is a true statement about the session and
  a wrong one in a box the campaign team reads as a person. `NOT_A_PERSON`
  holds that refusal for the *write* as well as the prefill — the write has
  its own attribution fallback, and a rule the form keeps while the write
  breaks it is not a rule.
- **A file field is not a text box.** `field_1813` is a Knack file field, and
  a file field is written by uploading the bytes to Knack's own asset endpoint
  and putting the id it hands back on the record. Posting a string into it
  writes nothing — and because Knack refuses the whole record over one bad
  value, it would cost the request rather than the attachment. It is drawn,
  disabled, saying where files actually go, because a request sent believing
  the artwork went with it is worse than one that says it did not.

The write goes through the same `knack_api.coerce_field` that writes tickets
and website records, so a value Knack would refuse is refused **here**, by
name, and the rest of the record still goes. The request is logged under
**`ad_copy`**, not `hub`: `client_brand.work_log()` skips a module its own
table cannot name, and a skipped module reads on the record as a client
nobody has done any work for — the `display_ads` failure, one tool later.

**And the controls are drawn once, not once per object.**
`hub/static/knack-form.js` draws this form, the web ticket and campaign
support — three objects asking one question. What is decided *here* rather
than in the browser is which of this client's own answers each field can
offer: `_decorate()` hangs the campaigns, the order numbers, the partners and
the client's URLs onto the fields as `suggest`, and the drawer renders them as
a datalist, which suggests without restricting. There is deliberately **no
JavaScript mirror of the prefill** — target areas and the creative classifier
each carry a mirror already and each needs a test proving the halves still
agree. The one thing the page decides for itself is that picking a campaign
fills in the order number beside it, and it writes into the box rather than
redrawing the row: a container that re-renders while somebody is typing into
it eats what they typed. `test_ad_copy.py` asserts all of it.

## A dropdown that cannot hold the answer is worse than a text box

Every control on the four request forms is read off the live Knack object,
which is the rule these forms are built on: a dropdown's choices are Knack's,
and a form that guesses one writes a value Knack refuses over the *whole*
record. So `multiple_choice` becomes a select, `boolean` a yes/no, and a field
publishing no choices degrades to a text box, which is honest.

**A connection is the one control that can be a picker and still be wrong.**
`connection_choices()` asked for `rows_per_page=500` and returned whatever came
back, so a connection pointing at the client book or the insertion orders came
back **alphabetically truncated** — 500 of several thousand, in a complete-
looking `<select>`, with nothing saying so. A rep who cannot find their IO in
it concludes it does not exist; and `coerce_field` then refused a typed name
as matching "no record on this connection (of 500)", quoting the fraction as
though it were the book. Both halves read as a fact about the client's record
rather than about our paging.

`connection_records()` reads Knack's own `total_records` beside the page and
reports `truncated`, `connection_note()` turns it into the line the field
carries, and the refusal names the real total and `KNACK_CONNECTION_LIMIT` —
the variable that fixes it, because a warning nobody can act on is furniture.
A full page with no count published is *assumed* truncated: a page that came
back exactly full is the shape of a list with more behind it, and that is the
safe way to be wrong. A picker that is complete gets no note at all, or a
warning on every field is a warning nobody reads.

It also stops conflating the two empties. An object with no records and a read
that failed both came back as `[]`, and the form said "could not be read"
about both — the `connected_accounts_result()` rule from Google Finder, one
form later. `error` is set only for the second.

**And what the Hub knows is offered wherever Knack publishes nothing.** The
client's own campaigns, IO numbers, products and **media partner** ride on the
fields as `suggest`, which the drawer renders as a datalist: it suggests
without restricting, because the IO that needs help is not always one we hold
a row for and a picker that refuses an unknown number is a form somebody gives
up on. The partner was the one value on those rows that the campaign support
form did not offer while the ad copy form offered it from the identical data.

## A web ticket is eight fields, and the form asks for all eight

`hub/knack_api.py` pins object_107's field ids in `TICKET_FIELDS` — they were
pinned because label matching broke silently when a label was renamed, which
is how the Issue column on the Accounting report came to be empty. But pinning
an id is not the same as asking for its value: Client 360's ticket modal sent
a title, a website, a description and a name, and everything else on the
record was left blank on every ticket the Hub raised. `TICKET_CREATE_FIELDS`,
`TICKET_MANAGE_FIELDS` and `update_ticket()` existed with no caller at all.

The write set is title, client organization, media partner, client website
URL, type of ticket, revision requires billing, describe the changes, and
**are you ready to submit** (`field_1696`), which Knack's own workflow reads
and which a ticket arriving blank leaves sitting in nobody's queue. It is a
button rather than a question — sending the form is the act of submitting, so
it opens on yes and one click turns it off for a ticket someone is filing to
finish later. Revision Requires Billing is two radios, because a field with
two answers should not hide one behind a click. Partner Contact was on the
list and came back off it: pinned and read, not asked for.

The website URL opens on the record the ticket was raised from, with the
client's other sites offered beside it and the box still free text — the site
that needs the work is not always one we hold a record for.

The wider set this object carries — web services, the six service checkboxes,
the new website URL, the pause and cancellation fields, status, developer —
stays pinned and is still **read** for the ticket list. It is deliberately not
written: a form asking for a field nobody fills is how twenty questions became
eight answers and twelve blanks. `test_web_tickets.py` asserts that in both
directions — every one of the eight is written and drawn, and none of the
others is in either write set.

The form draws from the live object. It has to: the ids are ours, but a
dropdown's **choices** are Knack's, and Knack refuses the whole record over one
bad choice — so a value it would refuse is refused here, by name, and the
ticket is still created. `/api/client/tickets/fields` returns the control each
field needs; `/web-ticket.js` draws them, and Manage Ticket edits an existing
ticket through `/api/client/tickets/update` (the record id travels in the body
so the URL stays a literal `tools/linkcheck.py` can verify).

Three rules in that path, each of which is a way to lose data quietly:

- **A connection needs a record id, never a name.** Writing the display text
  creates nothing and clears the link, which is why create_ticket used to skip
  those fields entirely. `connection_choices()` offers the real records, and a
  name is resolved only when it matches exactly one of them — "Riverside HVAC"
  against "Riverside HVAC LLC" is refused and listed, not guessed, for the same
  reason `client_key.resolve()` refuses a substring.
- **Nothing is dropped in silence.** Both write paths return `rejected`, and
  both modals show it. A ticket created with half its fields missing must not
  read as a clean success.
- **Title is not editable after creation.** Renaming a ticket breaks the thread
  for whoever raised it, so it is in the create set and not the manage set.

Assigner and the discovered Requested By are written but never asked for —
nobody types them, and a ticket the web team cannot put a name to is one they
have to come asking about.

The audit module at `/tools/tickets` describes the same object, and used to
keep its own copy of the ids — two maps agreeing only for as long as somebody
kept them in step. There is one now: the audit's own field names
(`summary`, `details`, `assignee`, which its reports are written against),
mapped onto `hub.knack_api.field_ids()`. `field_ids()` is the pinned set with
its environment overrides applied and no schema read, so a module that only
wants the ids does not have to reach Knack for them.

That module also used to default its object to `""` and then tell you to go
and map it, on a deployment where the ids were pinned all along. It defaults
to the shared object now, and the fields nobody has pinned — the dates, the
ticket number, priority — are matched against the live object's labels on
first use, the same match the setup page performs when a person clicks
Auto-detect. **A saved map still wins**: someone who has corrected a guess
must not have it re-guessed under them. `test_web_tickets.py` asserts the two
name sets translate, so a pinned id that moves cannot leave a report column
reading a field that no longer means what its heading says.

## A campaign support request is twenty-three fields, and we sent four

`object_121` has carried the whole of a support request for years — the
insertion order, the due date, what kind of support is being asked for, the
pixel URL, the timeline, whether it is a rush and why, who to notify at the
client and at the partner, the notes, the IO number, the campaign and the
product. The Hub sent a subject, a description, a client name and an IO, and
every other field arrived blank on every request Client 360 and the dashboard
raised. Nothing errored: the campaign team filled the rest in by going back
and asking, which is the same state the web ticket was in before
`TICKET_FIELDS` was pinned.

The ids are pinned now, in `hub/knack_api.SUPPORT_FIELDS`, each overridable by
`KNACK_SUPPORT_<KEY>`. **Pinned, not matched by label**, for the reason that
file gives at length — the old map found its six roles by looking for the word
"request" or "name" in a label, so a subject landed on whichever field matched
first and a rename would have moved it again in silence.

**Every option on the form comes off the live object, and none is invented.**
The ids are ours; a dropdown's choices, the records a connection may point at
and whether a field is a date or a paragraph are Knack's. So Campaign Support
offers its own multi-select, Timeline and IOP Status their own dropdowns,
Insertion Order and Media Partner and Client real record pickers, and Notify
Client? a yes and a no. A field Knack publishes nothing for degrades to a text
box rather than an empty picker — a form that guesses a choice writes a value
Knack **refuses the whole record over**, which costs the request rather than
the field. `coerce_field` catches one before it is sent and names it.

Four more rules in it.

**A field that cannot be written is drawn and says so.** Uploaded Files is a
Knack file field, written by its own upload call and not by a value on the
record, so a box for it would take a filename and drop it. It is on the form
as a line saying where files actually go — a deliverable left off entirely is
one nobody knows to supply.

**The subject leads the issue, because this object has no subject field.**
Folding it into `field_1819` is checkable; writing it onto whatever matched
"name" was not.

**What the Hub already knows is a suggestion, never a restriction.** Picking a
campaign from the client's real insertion orders fills the IO number, the
campaign and the product, and those stay editable behind a `datalist`: the IO
that needs help is not always one we hold a row for, and a picker that refuses
an unknown number is a form somebody gives up on. The Knack `client` field is
resolved to **exactly one** connection record or left for the rep — a near
match is not a match, the `hub/client_key.py` rule.

**Nothing is dropped in silence.** Every write returns `written` and
`rejected`, and both modals show the second: a request created with half its
fields missing must not read as a clean success.

The controls themselves are `hub/static/knack-form.js`, shared with the web
ticket form — the two ask the same question of two objects, and a second copy
of that renderer is the failure this file names twice already. `wsgi.py` does
not serve it; `hub/help_routes.py` does, root-level like the scripts beside
it, and the three templates that load a form load it first.
`test_campaign_support.py` asserts all of it, including that every field the
campaign team named is both written and drawn — a field can be pinned, be
writable, and still be on no screen, which is exactly the state this object
was in.


## A client's photos are already somewhere, and it is not their laptop

`modules/image_picker/upload_sources.py`. The client-facing picker
(`/tools/image-picker/pick/<token>`) has always been able to take uploads
through Cloudinary's own widget, which speaks Google Drive, Google Photos,
Dropbox, Facebook, Instagram and a web image search out of the box. It offered
three: a file dialog, the camera, and a URL box — because `PICKER_UPLOAD_SOURCES`
defaulted to `local,camera,url` and nobody had ever set it. So a client asked
for "your photos" got a file dialog while the photos sat in their own Instagram
feed and the agency's Dropbox, and what actually happens then is that they do
not send them.

The sources are a catalogue with what each one is for, rather than a comma list
in an environment variable, and four rules follow from that:

- **A source is offered from the catalogue or not at all.** A name the widget
  does not know draws a broken tab or no tab, and both read as our page being
  broken. An unrecognised entry is dropped and **named on the admin page**
  rather than forwarded — the same answer `hub/knack_websites.py` gives a value
  Knack would refuse.
- **A billed add-on is off until somebody turns it on.** Shutterstock, Getty,
  iStock and Unsplash are Cloudinary add-on subscriptions; listed without one,
  the client gets a tab that consents and then fails for a reason that is
  nothing to do with them, which is exactly why Google Ads came off the Google
  Access list. `PICKER_STOCK_SOURCES` names the ones the account actually has.
- **A per-source key is an override, not a gate.** Drive, Dropbox and Instagram
  work on Cloudinary's own registered apps, and the **client signs in to their
  own account either way** — the Hub never sees that password. Our own client id
  only changes the name on the consent screen, so a missing key never hides a
  tab and is not something a staff screen reports; an **empty** key is never
  sent at all, because the widget takes `dropboxAppKey: ""` at its word and
  fails the tab against it.
- **Recording an upload asks whether the source is one of ours, not whether it
  is switched on now.** A source turned off between the widget opening and the
  file landing must not file a real Instagram upload as `local`, which is the
  one thing the gallery's source column exists to say.

The paragraph the client reads is **built from the live list**, because a
sentence naming Dropbox on a deployment where Dropbox is off is a promise the
panel cannot keep. The **staff** page says none of this. It carried a Services
tick-row and a thirteen-row source table, and neither answered a question
anybody was asking: a service that is working is not a finding, and a roster of
green ticks is read once and skipped for ever while pushing the client list —
the reason the page is open — below the fold. What is left is a note when
Cloudinary is unset, a note when no stock provider is, and a note naming a
source string the widget does not recognise, which is the one that draws a
broken tab and needs somebody to correct a variable.

**The staff pick page 500'd on every visit.** `/tools/image-picker/c/<id>`
includes the upload panel and never passed it the panel's variables, and
`{{ sources|tojson }}` over an Undefined raises while Flask is *rendering* — so
it was never a broken widget, it was the whole page, exactly like
`url_for('website_check_limits')` in Sites Admin. `tools/pagecheck.py` covers
the module root now and `test_image_picker.py` covers the page that needs a
gallery id.

**Deleting a gallery deletes files nobody can get back**, so the name is typed
rather than an OK button pressed: the button sits in a row of four safe ones,
and for anything the client uploaded our copy is very often the only copy. What
Cloudinary removed and what it refused are **counted apart** — `hub/domain_links.py`
says at length why one tick for both is how somebody learns not to trust the
tick — and the Suite copies are named as staying, because a file already in the
client's media library may be in a funnel. `cloudinary_sink.destroy()` takes the
resource type now: Cloudinary keeps images and raw files in separate namespaces,
so a brochure PDF asked for as an `image` comes back "not found", which the old
signature reported as a **clean success** with the row gone and the file still
in the account.

**"General Business" is the busiest entry in the industry dropdown**, because
"none of the above" always is — and it handed out four generic chips: a team, a
counter, a storefront, a handshake. `modules/image_picker/profile.py` asks that
client two questions instead (what kind of business, and what do you sell or
show on your website) and the answers do three things, because **an answer that
was captured must be used** — the Proposal Builder shipped four discovery
questions that were read by nothing and produced an identical document whatever
was typed. They become the client's **own** topic and service chips, they are
blended into every free-text search from then on, and they are kept on the row
so the next visit and the next rep picking on their behalf start from the same
answers.

Three rules in it. The model writes **search terms and nothing else is
trusted**: `clamp()` caps the collections, the queries per collection and the
lengths, and strips everything a stock query is not — these strings reach three
provider APIs with three quoting rules and a page. **"We could not ask the
model" is not "this business has no topics"**: the chips are still built, from
the client's own words folded into the General Business queries, and the row
records `source: "typed"` so a staff screen can tell that apart from copy
written for this client. And **only General Business is overridden** — a staff
member switching the industry selector to a real trade is asking for that
trade's curated chips, not for a client's description to quietly replace them.
`test_image_picker.py` asserts all of it.

## One design, the whole size set — and the fourth copy it refused to be

`modules/magic_resize` (`/tools/magic-resize`) takes a finished design and
produces every size in a bundle from it. It is a **new module rather than a
feature on Image Creator**, because the two have structurally different
project shapes: Image Creator is one canvas, one size, one Fabric JSON, which
is right for what it does, and this is one design and many frames derived from
it. What it does **not** do is fork that tool's services — the photo search,
the asset lookups and the AI proxies are provider-agnostic and are called, not
copied.

**It is not `/tools/display-ads`, and that is the first trap in this file
rather than a preference.** That prefix is the Display Ad Builder's — the
TypeScript renderer proxied by `hub/ad_builder_proxy.py` — and
DispatcherMiddleware routes purely by URL prefix, so a second module under it
never receives a request and does not even 404: it is swallowed by whichever
mount owns the prefix. The two tools are also different jobs and the names say
so. The Display Ad Builder *generates* a size set from copy and a brand
against hand-authored per-size layouts; this *resizes* a design somebody drew.

**Every dimension the spec kit publishes is read from the kit, and none is
restated here.** This repo already held **three** descriptions of display ad
sizes: `hub/creative_specs.py`, which is the transcription `kit_drift()` holds
against the published page; `modules/ad_builder/src/config/platforms/*.json`,
the renderer's own; and `modules/image_creator.CANVAS_PRESETS`, a canvas
picker. They agree by luck rather than by construction, which is the drift the
two rate cards cost a year. A fourth would have been the one that went stale,
because it is the one no check reads — and the build plan's own table already
disagreed with the kit transcription about the HTML5 package (600 KB against
the kit's 200 KB) before a line of it was written. `sizes.py` names a
`creative_specs` unit id and nothing else: the width, the height and the
weight ceiling are read at import, and a delivered file is judged by
`creative_specs.check()`.

**A unit the kit stops publishing is dropped and named, never guessed at.** It
lands in `UNRESOLVED` and `check_kit_alignment()` reports it, and the bundle
comes back short — a size we cannot verify is one we must not silently deliver
against, and a fallback dimension carried here to keep the bundle whole is the
second copy the module exists to refuse. Six sizes **are** ours, each with
`source: "house"` and its reason: the 336x280 and 320x100 IAB units the kit
does not weigh, Google's two Responsive Display asset sizes (an asset pool
Google lays out itself rather than a banner it serves whole, judged on
Google's published 5 MB with that provenance printed), and the two social
resolutions the kit publishes as a ratio rather than a size. A house size with
no published ceiling is reported **not measured** rather than judged against
the unit it resembles.

**Two tiers, because two different things are being asked.** Between
neighbouring shapes the answer is arithmetic and objects **re-anchor** — each
is measured against the edge it was nearest and put back the same distance
from that edge in the new frame's terms, because scaling raw x/y drifts
everything toward the origin as a frame shrinks and a right-hand button walks
into the middle. Between distant shapes the answer is a layout decision, and
`templates_layout.py` holds four **house-authored, fixed** arrangements —
leaderboard, skyscraper, square/rectangle and story — the choice
`SOCIAL_STRUCTURE_TEMPLATES` makes in the Commercial Builder and for the same
reason: a layout a model picks differs between two renders of one campaign,
and the whole promise of a size set is that it is the same ad. Which tier a
frame took, and why, is printed on the frame.

**A slot contains and never covers, and that is mechanical rather than
taste.** Covering means overflowing; a background may overflow because the
canvas clips it, and a slot clips nothing unless the object carries a clip
path. This module emits plain Fabric objects, so a covered slot is a
photograph hanging off the ad — which the guard then reports as clipped, on
every frame, for ever. The first version did exactly that, and the test is
what found it.

**A frame the engine is unsure about is `needs_review`, never `auto`.** The
guard runs after placement and names the objects: an overlap, or anything past
the frame edge. A background is exempt from both because covering the frame is
its job, and a decorative object is exempt from the overlap check alone — a
rule behind a headline is the design — but not from the bounds check, since a
flourish hanging off the edge is still clipped. The tolerance is there for the
same reason `QR_CODE_RULES` is advisory: a guard that fires on a hairline
touch is one somebody switches off, and switching this one off costs the real
findings.

**Nothing is dropped in silence, and copy is the line that decides.** Every
object a template had no place for is named in `unplaced` and stays on the
design. One carrying **words** marks the frame, and one carrying none is a
note beside it — a photograph left off a 728x90 is obvious to anybody looking
at the frame, and a line of rate copy that did not fit is not, and it is the
client's own words going missing. Nothing is invented for an empty slot
either: a headline slot with no headline is left empty and said so, because
filling it with the next nearest thing is how a disclaimer ends up where the
headline goes.

**The propagation rule is two halves that fail in opposite directions.** A
**copy** edit on the design reaches every frame, a hand-tuned one included —
making a rep retype a headline eight times is how one of the eight goes out
with last week's offer on it — and an edited frame that receives one is
*flagged*, because new words may not fit a box somebody set by hand and
nothing here can see that. A **layout** edit reaches `auto` and `ai` frames
and never an `edited` one: somebody moved that button on purpose, and
regenerating it destroys a decision with nothing on any screen saying so. The
skip is reported rather than silent, since a frame that regenerated into
exactly what it already was and one that was never touched read alike.

**AI recompose is the fallback on one frame, never a pass over the set.**
There is no route that recomposes a set: a model asked for eight layouts
produces eight a person then has to check, which is the work the templates
already did. It is handed roles and boxes and **not** the client, the brand or
the copy; it returns *positions* for objects that already exist; an id it
invented is dropped and counted; an object it did not mention keeps its place;
and its answer goes back through the same guard a template's output does,
because a proposal that arrives with a collision must not read as the fix for
the collision it was asked about. Keeping one is a press, and it is `ai` and
not `auto` — which frames a template produced and which a model adjusted is
what somebody scanning a set wants to know.

**The dimensions are the unit, and are never traded for weight.** Export
judges the browser's rendered bytes against the ceiling and, over it, runs a
quality ladder with `max_edge` pinned above the frame's own longest side so
the shared optimiser cannot resize on the way past: a 300x250 shrunk to
280x233 to save weight is not a Medium Rectangle any more. A frame that cannot
get under its ceiling above the quality floor is **left out of the pack and
named in it** — the rule `deliverProject` already applies to a QA-failing
size, because seven files where there should be eight is a difference ad
operations assumes they caused.

**The type floor is ours and warns rather than blocking.** No platform
publishes a minimum type size for display, so a hard failure would be house
guidance wearing a platform's name — the reason `HOUSE_LEGIBILITY` is kept out
of `THRESHOLDS` in `services/abcd_service.py`. It matches the per-size
`minFontPx` the display-ad renderer already carries, so the two halves of this
Hub at least agree, and the build plan asks for a figure signed off rather
than assumed.

**What is deliberately not built yet**, so its absence is a decision rather
than something to rediscover: the Fabric editing surface is **not** shared
between this module and Image Creator. That refactor is real — `editor.js` is
1,845 lines of module-scope globals with no export structure — and it is the
highest-risk, lowest-immediate-value piece of the plan, which is the note this
file already makes about a big-bang rewrite of working modules. Until it
lands, a frame is produced, judged and reviewed here and hand-tuning one goes
through `POST .../frames/<size>` with objects the editor supplies; `Adjust
Variant` and the browser-side render behind the export button are the half
that needs it. `brand_profile_ref` on a project is a **placeholder read by
nothing**, pending the separate BrandTemplate decision — carried as a
reference precisely so it cannot quietly become a working integration nobody
signed off, which is the declared-but-unwired failure this file counts six of.

## The one module that is not Python

The **Display Ad Builder** (`modules/ad_builder`) is a Node service, not a
Flask module. It is ~10,000 lines of TypeScript with a native image pipeline
(sharp rasterises SVG and steps a quality ladder until each ad fits the
platform's file-weight limit -- Amazon allows 40 KB for some placements), so
porting it to Pillow would change creative that clients already receive.

It runs as a **second process in the same container**. `docker-start.sh` starts
it on 127.0.0.1 with a restart loop and then execs gunicorn as PID 1;
`hub/ad_builder_proxy.py` proxies `/tools/display-ads/*` to it behind the Hub
login and adds the admin token server-side, so nobody needs a second password.

Things that follow from that, each of which has a comment where it lives:

- **Two processes, one plan.** This costs ~150-200 MB of image and a second
  build step. If Render builds start timing out or memory gets tight, the ad
  builder ships its own `render.yaml` and can move to its own service --
  only `AD_BUILDER_URL` changes. Nothing else in the Hub knows the difference.
- **The pages link from the site root.** `fetch('/api/render')` is correct
  standalone and wrong under a mount, so `src/basepath.ts` injects a shim that
  prefixes fetch, XHR and href/src/action from `X-Forwarded-Prefix`. Without
  it the tool loads perfectly and no button does anything.
- **`ADBUILDER_ADMIN_TOKEN` must be set** (16+ characters) or the renderer
  refuses its own internal routes. `/status` says so in words.
- **The client and proposal joins are Python**, in `hub/ad_builder_link.py`.
  The renderer never learns who our clients are; finished ads are filed into
  the client gallery through `modules/image_picker/filing.file_asset`, which
  records the public_id Cloudinary already has rather than re-uploading.
- **Its own tests need an `npm install` CI does not do**, so the gate is
  `test_display_ads.py` — pure Python over the files. That is a weak substitute
  for most of a renderer and exactly the right test for two things. The layouts
  are hand-authored coordinates, so whether a box exists, sits inside the safe
  area and clears every other block is a fact about the JSON. And the build
  screen talks to the render server across a wire nothing typechecks: the
  generate route answered `{ candidate }`, the screen read `{ candidates }`,
  and a generation that had just succeeded reported "image generation is not
  configured" — no runtime in between, so both halves are asserted together.

**A field the build screen offers is not a field any layout draws.** Every size
gets Headline, Supporting line, Offer, Proof point and Call to action. Not one
template carried a `trust` box, so the proof point was typed in, saved,
word-counted and rendered nowhere on every ad this tool has ever produced. The
box exists now wherever there is room — beside the button on the rectangles,
above it on the skyscrapers — and the two canvases with genuinely no room
(728x90, 320x50) say so beside the field rather than accepting copy they will
throw away. `/api/build/options` reports which blocks each size draws, so a
family added later cannot reintroduce the silence.

**A copy edit is per size; a text-box style is per concept.** The panel headed
"Text boxes for 300x250" was applying to all eight, so a type size set to suit
the leaderboard shrank the skyscraper with it while the heading insisted
otherwise. Copy genuinely is per size, and now asks which it means the first
time each field is edited — the answer stays on screen as a toggle rather than
being a dialog nobody can revisit. "Every size" writes the default **and**
clears that field's per-size overrides, or the override keeps winning and the
edit reads as having failed.

**Nine positions cannot express "a bit further down".** A background photo is
drawn to cover the canvas and the overflow is cut off, and the crop anchor was
one of SVG's nine `preserveAspectRatio` alignments — which answers "top or
bottom" and nothing between, and cannot express zoom at all. `svg.coverRect()`
computes the rectangle instead, from an offset and a zoom, so the control is a
pad of arrows and a slider. The offset is a **fraction of the picture's own
overflow, not pixels**, so one setting shows the same part of the photograph on
a 300x250 and a 970x250 — which is what "the same ad in eight sizes" has to
mean. Its sign names *the part of the picture that shows*: -1 is "show me the
top", which slides the picture **down** until its top edge meets the canvas.
Backwards, every arrow moves the opposite way from the one pressed, and on a
symmetrical photograph that survives a glance. The nine old alignments are kept
and converted, because concepts saved before this carry one; a source with no
intrinsic size (an SVG, which sharp reports as 0x0) still falls back to
`preserveAspectRatio`, or every number would be NaN.

**Two blocks printed on top of each other is invisible to every other check.**
Contrast samples what is behind the ink and finds the other block's fill; the
fit pass finds copy that fits its own box perfectly; the safe-area pass finds
both boxes inside the margin. The ad has a headline printed through a button.
It was unreachable while every box came from a hand-authored template — the
diagnostics page checks those for overlaps at boot — and became reachable the
moment the button and the logo gained nudge arrows. `qa.ts` has a `collision`
check now, and it is a **fail**.

**"Align" means two different things, and the button had the wrong one.** For
type it is where the line sits inside its box. For a button, the label is
already centred by every template — so setting the CTA's `align` moved
nothing, and Left, Center and Right rendered identically. On the button it
moves the *button*, within the safe region.

**A control for something the layout does not draw is the proof-point mistake
again.** "Full background with copy panel" puts the copy on a filled card, and
that fill decides whether it can be read — it was a template constant, so a
client whose primary is a mid-grey got copy nobody could read. It is a control
now, offered only on the sizes whose layout actually draws a panel;
`/api/build/options` reports that alongside which copy blocks each size draws.

**A logo is the one asset nobody may edit, so the palette is what moves.**
`modules/ad_builder/src/palette.ts` proposes whole palettes that make the
existing mark read, each with the contrast it achieves,
and a person picks one. It is **arithmetic, not a model**: asking AI for "a
colour that contrasts" is a slow, non-deterministic way to do a subtraction
whose right answer is defined by a published formula, and the result has to be
checkable because the entire point is that it provably reads. Three rules keep
the proposals coherent — a palette that already works gets none at all, a
"change" to the colour it already is is not a proposal, and `light`/`dark` are
never inverted, because those two roles' *names* are what every template
resolves ink against. A dark mark on the `dark` role therefore gets no
recolour: that one needs the reverse logo, and the screen says so.

**The Insites scan already knows the client's real palette.** A scan reports
`colour_scheme` (primary/secondary background, text and accent — observed off
the live pages, not declared), `logo.logo_url` with `has_detected_logo`, and
desktop/mobile screenshots. That is better evidence than Brandfetch, which
routinely returns a palette without labelling which entry is the brand colour.
`/tools/display-ads/_hub/site-brand` reads it through
`modules.scans.app.latest_payload_for_domain`, joined **by domain, never by
name**, and a client with no scan is the ordinary case rather than an error.
The colours are offered beside the swatches and **copied, not applied**: which
of the five roles a site colour should become is a judgement, and guessing it
moves four other things.

**Only the rebuild route filed a finished render onto its project.** The batch
is what carries the proof URL, so a render started from the build screen wrote
a proof to disk that nothing linked to — and the screen told the operator to go
and look at a proof with no way to reach it. `fileJobOntoProject()` is the one
place that does it, and both routes call it.

**A control that cannot do the thing its label says is worse than a missing
one.** "Attach to client" and "Render all sizes" both stood on the toolbar
from the moment the page opened, on a build that had never been written down —
and both act on what is on the **server**, which on an unsaved build is the
previous version. So attaching filed the ads somebody had just finished
replacing, onto a client record, and reported a clean success for doing it.
The toolbar is the order of the work now: Save is the only thing offered on
arrival, Render appears once there is a saved build to render, and Attach once
there are rendered files to attach. The gate lives in `saveCampaign()` rather
than in the Save button's handler, because switching size with unsaved edits,
duplicating a set and starting a render all leave the server holding what is on
screen too — a gate only one of five doors opens is one people learn to resent.

**Four jobs, one button.** Render, render-and-file, file, and package were
spread across three places: a toolbar link that predated any render, a Deliver
button that only appeared once a status had changed, and the render itself. So
the ordinary job — build these and put them on the client's record — was two
controls with a page in between. The render button asks now. **Filing waits for
the files to exist**: started alongside the render it would copy the previous
build onto the client record, which is the failure above wearing a different
hat. And **a download is not a delivery** — `deliverProject` sets the project
complete, writes a "Delivered" note and mails the team, so the ZIP button asks
for `record: false`; the zip is byte-for-byte the same either way. A QA-failing
size is still withheld, and now *named* rather than silently missing from the
folder.

**A link that lands on a staff login is not a link you can send a client.** The
proof was behind the Hub session, so "here is the link, tell us what you think"
put a client on a login form for an account they do not have — and the *static*
`proof_<id>.html` the batch records is worse than that: it is rendered with no
action endpoint behind it, so its Approve button rewrites the page to say
"Approved" and posts nowhere. A client could sign a set off on it and no screen
here would ever know. Everything points at the live `/proof/<requestId>` route
now, `PUBLIC_PATTERNS` in `hub/ad_builder_proxy.py` lets a client reach that
page and the two decisions on it, and the entry is in `CHROMELESS` so a
prospect does not meet the staff sidebar. Two things fall out. **Our
credentials must not travel with an anonymous request** — forwarding the admin
token would tell the renderer a client is staff, which is the exact question it
asks to decide whether to draw the live editor, so a public page would quietly
gain an operator's controls. And **rebuild stays behind the login**: it
re-renders the creative for everyone holding the link and reaches endpoints
that are billed per call.

**And making that page public changed what may be interpolated into it.**
`renderProof` hands six values to an inline `<script>` through
`JSON.stringify` — the copy, the colours, the meta, the per-size overrides,
the delivered pack. That produces perfectly valid JavaScript and does **not**
escape `</script>`, and an HTML parser ends a script block at that literal
string wherever it appears, inside a quoted string included: one of them in
the data closes the block early and everything after it is parsed as markup.
It was harmless while only staff could open the page and is not now.
`meta.promoting` is the one worth naming — it falls back to
`campaign.landing.summary`, which is text read off the *client's own website*,
so it is nobody here's to vouch for. `jsonScript()` escapes `<` (`\u003c` is
the same character to `JSON.parse` and invisible to the HTML parser), and
U+2028/U+2029 with it, since both are legal inside a JSON string and are line
terminators to a JavaScript parser — either one unescaped is a syntax error
that costs the whole page rather than one value. `basepath.ts` needs none of
this: its prefix is refused by allowlist rather than escaped, because nothing
legitimate in a mount path has an angle bracket in it. `tests/proof.test.ts`
asserts all six are escaped rather than one of them, that the value still
round-trips through `JSON.parse` (an escape that changed the data would be a
different bug wearing a fix), and both halves of the editor split — a client
keeps Approve and Request changes, and dropping those alongside the editor
would look like a tidy-up and retire the feature.

**Approving is one event and two doors.** `recordDecision()` is shared, because
approval is the trigger for packaging and two copies of that drift into two
ideas of what "approved" delivers. What is *not* shared is the claim: a record
saying "approved by the client" about a decision an account manager made in the
office is the difference between a campaign that is signed off and one somebody
expects to be. The client's route reads no name at all — it is reached with
nothing but the project id, so a name claimed there is one anyone with the link
could claim — and the staff route sits under `/api/project`, which the admin
gate covers, so the name comes from the Hub proxy. A change request with no
detail is refused by both.

**Meta had a config file, every template drew its sizes, and one line dropped
it.** `.filter(p => p === 'google' || p === 'amazon')`, written out three times
— in the request route, the auto-render branch and the validator. A Meta buy
came back as a set of Google banners with nothing anywhere saying so.
`registry.acceptPlatforms()` is the one answer now, from the directory listing,
and it **names what it refused** rather than quietly building something
smaller. Two things had to be corrected with it: `meta.json` carried Google's
150 KB ceiling, which makes the quality ladder step a 1080x1920 story frame
down until it is mushy to satisfy a limit Meta does not impose; and the start
form never offered the choice at all.

**A URL is mandatory now, because it is what the tool reads.** The page is
fetched, its conversion points counted, and its own words become the first
draft of the headline, the supporting line, the offer, the call to action and
the proof point — and the picture is drawn against it too. It is required in
`start_project()` and not only on the form, because a `required` attribute is a
courtesy to somebody typing rather than a rule. The bug underneath it: the Hub
sent `website` and the renderer reads `landingPage`, so **every build started
from the Hub had no page analysis at all** while builds from the public form
did, and nothing on either screen said which kind you were looking at.

**And the analysis nothing asked for.** It sat on the project record and the
build screen's "write this for me" and "draw me a picture" both worked from a
business name and a headline somebody had already typed. Both read it now.
`suggestCopy()` also answered three of the five fields the screen offers, so a
draft left the offer and the proof point empty on templates that draw both —
and those two are exactly the ones that must never be invented. The prompt
forbids it twice, the code tops neither up, and an empty answer is **reported**
("their page says nothing about the offer, so it was left blank rather than
invented") rather than hidden. The draft fills empty fields only and writes to
every size: a drafted line is the set's copy, and landing it as a per-size
override would leave the other seven empty with the panel insisting the field
was filled.

**A generated picture is a draft, and the sweep removes it.** So keeping one
means moving it to Cloudinary first — `POST /api/imagery/keep`, which accepts
only a path this service wrote, because a route that uploads whatever URL it is
handed is an open relay into our own account. Filing it onto the client stays
the Hub's job: the renderer does not know who our clients are, which is the
line `hub/ad_builder_link.py` draws. Without that move, the gallery gains a row
that opens today, 404s after the sweep, and was never openable by the client
whose gallery it is in.

**And "only a path this service wrote" was one route's rule, not the app's.**
`POST /api/images/generate` takes a `previousUrl` too — on a revision the
previous picture becomes the primary reference, which is what makes "make the
sky darker" iterate rather than re-roll — and it did `path.join(OUT,
url.replace(/^\/files\//, ''))` with nothing in between. `path.join(OUT,
"../../../etc/passwd")` is `/etc/passwd`, and the route then copies whatever it
finds into the campaign's cache directory, which lives under `imagery/` and is
served at `/files/`. So an arbitrary readable file could be lifted into a
web-served folder and handed to an image model, from a value in a POST body.
Nothing errors: a path that resolves is a path that copies. `keep`'s check sat
one route above it in the same file, comment and all — this is the second copy
of a rule that was never written rather than one that drifted, which is the
same failure arriving from the other direction. `assets.generatedImagePath()`
is the one reading now.

**And the reference photos on that route skipped the guard the neighbours use.**
`referenceImages` is a list of URLs this server then fetches, and the loop
tested `^https?://` and nothing else — which admits plain http to any host:
`169.254.169.254`, `127.0.0.1` (the Hub's own gunicorn shares this container),
and every private range. `assetUrlIsSafe` already stands behind
`/api/background/apply`, `/api/logo/apply` and `/api/palette/variants`, and its
own note says why it applies even to a gated route: staff credentials leak and
the check costs nothing. It is applied here too now, with a ceiling and a
timeout — `arrayBuffer()` on an arbitrary URL wrote the whole body to the
volume `retention.ts` exists to keep clear, the shape `landing-images.ts`
already had right. The one real caller passes Cloudinary `secure_url`, so
nothing legitimate is refused. `tests/asset-paths.test.ts` holds both.

**Except that the sweep was not removing it, and the paragraph above was the
rule people reasoned from.** `retention.PRUNABLE` listed `google`, `amazon`,
`cache` and `jobs`; `imagery/` was in neither that list nor the protected one,
so every generated hero stayed on the volume for ever — on the one module whose
whole job is to stop this service eating the disk its neighbours live on.
Nothing errored in either direction: `keep` worked, the drafts simply also
survived. A rule the code does not keep is worse than no rule.

The platform directories were the same failure with a name on it. `render.ts`
writes to `<outDir>/<platform>/<concept>` and the list here said google and
amazon while `meta.json` sat in the registry being rendered — the **fourth**
hardcoded platform list in this app, after the three `.filter(p => p ===
'google' || p === 'amazon')` calls that dropped a Meta buy outright. It is read
from `loadPlatforms()` now, so a platform added next month is swept without
anybody remembering.

And **`deliveries/` is named in `PROTECTED` rather than merely left out**,
which is the difference between a decision and an oversight: an omission reads
as something to fix, and the next person to widen this list takes the file
behind the proof page's download button with it — the link a client opens
whenever they like, turned into a 404 for the one person the tool is for.
`tests/retention.test.ts` drives the clock rather than waiting on it, and holds
all three.

**The enforcer nobody tested said things that were not true.**
`image-budget.fitImageToBudget` is the one place guaranteeing every ingest
path -- a customer upload, a Pixabay hit, an AI generation -- ends up a valid
raster under 150 KB, and `imagery.ts` states the rule "holds by construction:
there is no path here that skips the enforcer". Nothing tested it, and two of
the things it *reported* were wrong.

`reencoded` was `encoded.length !== original.length`, which is true of very
nearly every image, because the file is always re-encoded and the bytes always
differ. Its own description said *"had to be compressed or downscaled to
fit"* -- the field meant **we did**, and it claimed **it had to**. So a
600-byte logo came back flagged, carrying a note that it had been optimized
*"to meet the 150 KB limit"*, about a file at 0.4% of that limit; and
`toFixed(0)` printed its size as **"0 KB"**, on a string whose whole job is to
be read by a person.

And a function that exists to make a file smaller could hand back a larger
one. A high-entropy source already saved hard -- a Pixabay `webformatURL`, a
low-quality photo -- overshoots on the q82 first pass; the ladder pulls it back
under budget and the answer is still bigger than what arrived. Measured, 52 KB
in and 137 KB out, described as optimized. The original is kept now where it
already fits, is inside the dimension cap and is already the format we would
write, and that test asks nothing about whether the ladder ran: **work having
been done is not a reason to prefer a worse result.**

Two things the tests pin that are correct and worth not losing. A source that
genuinely cannot fit is **refused in words** rather than written over budget --
the ladder is bounded at twelve steps and spends four of every five on quality
before it shrinks, so a large incompressible image runs out of them, and the
message says what to do. And the file docstring no longer claims 150 KB is
"deliberately below Google's 150 KB delivered-creative limit", which is not a
thing 150 can be than 150: what keeps a finished ad inside its platform ceiling
is the quality ladder `render.ts` steps on the composed raster, and this budget
keeps a 6 MB phone photo from being the input to it -- a different job, worth
having, just not the one the sentence claimed.

**And the gallery beside it could come back empty with everything healthy.**
Cloudinary publishes a folder as `asset_folder` in dynamic-folder mode and
`folder` in fixed, and a search asking for the wrong one returns **zero**: the
request succeeds, the page renders, and a client's gallery reads as a client
with nothing in it. `cloudinary.searchFolder` picked between the two from
`CLOUDINARY_FOLDER_MODE` -- a variable set in `modules/ad_builder/render.yaml`,
which is the manifest for running the renderer as its *own service*. Here it is
a second process in the Hub's container whose Cloudinary settings
`docker-start.sh` derives from `CLOUDINARY_URL`, and nothing sets the mode. So
the default answered for an account nobody had checked, and `gallery.ts` is
what reads it.

`hub/video_library.py` reached this first, ran both fields against this account,
found they answer identically and asks for **both** -- so the extra clause costs
nothing and there is no setting left that can be silently wrong. The renderer
does the same now, through one exported `folderExpression()` rather than an
expression built inline where nothing could test it. It also takes that note's
other half: the exact form is `=` and the subtree form the trailing wildcard,
because neither alone is enough and the old expression used `:` for both, so
the folder's own assets were matched by a contains rather than an equality.
`folderMode` still decides the shape of a dry-run public_id, which is a
different question and a real one.

**And fixing the search left the folder with two readings of itself.**
`folderExpression()` trims a trailing slash before it builds its clause; the
gallery's *heading* and its *output filename* took the string exactly as
handed in. That asymmetry is the whole failure, because the README's own
folder tree prints the folder **with** a trailing slash -- so pasting it
searched the right tree, found the right assets, and then wrote them to
`gallery_.html`, which is the same file for every project: build a second
gallery and it silently replaces the first, with a success line naming the
file it had just overwritten. The heading went the same way, `slice(-2)` on
`[..., 'summer-solar', '']` giving *"summer-solar — "* -- the client's name
gone and a dash left hanging. A doubled separator does it to the heading
alone, since `pop()` cannot see an empty segment in the middle.
`normalizeFolder()` is the one reading now, and `folderExpression()` reads it
too rather than keeping its own.

**And a relative path is relative to the page that carries it.** A dry run's
manifest holds no hosted URL, so a simulated asset is drawn from a file on
this disk -- `path.relative()` against `out/reports`, hard-coded, whatever
`--out` had actually been given. A gallery written anywhere else had **every
image broken** and still printed the file it had written. The output path is
decided before the assets are built now and `assetsFromManifest()` takes the
directory, because the page's own location is the only thing that path can be
computed from. It was right for the default, which is exactly why it stood.

`main()` is guarded on `require.main` so importing the file for its helpers
does not run the command -- unguarded, a test that imports it throws on an
empty argv and calls `process.exit(1)` on the test runner.
`tests/gallery.test.ts` asserts all of it, and one of its assertions had to be
retargeted first: the doubled-separator case was pinned on the *filename*,
where `pop()` is immune to an empty middle segment, so it was a property that
could not fail -- the same shape as pinning a rounding bug on a figure too
large to round to zero.

**The scan photographed their website and nobody was shown the photograph.**
`website_screenshot` came back from `/_hub/site-brand` and was drawn nowhere,
so an operator judging brand colour on a dark canvas had to open the client's
site in another tab to remember what they were matching — and mostly did not,
which is how an ad comes back "not really them" with nobody able to say why.
It is beside the swatches now, desktop and mobile, because half the sizes in a
display package run on a phone and the two are often laid out nothing alike.
**Reference and never a source**: `lightbox()` takes its "Use this picture"
button only when a caller passes one, so a screenshot opens without it — a
picture of somebody's website is not a background, and the logo in it is a
logo photographed off a page, which `hub/scan_facts.py` already refuses to
merge into what we hold. The panel also stopped giving up when the palette was
empty: keyed on the colours alone, it hid the picture on every site whose
colours the scan could not read.

**`has_google_font_api` says a site loads Google Fonts and never says which
face.** So it is passed on as the weak signal it is rather than dressed up as
a font recommendation, or somebody reads it as one and types a family the
renderer does not have. The useful direction is the one people do not expect:
a **false** is the actionable answer, because their type is self-hosted or
licensed and nothing offered here will match it by accident. It is tri-state —
the check lives in the scan's GDPR section, and a plan that did not run it
leaves the field out entirely, which must not read as "no". Not measured says
nothing at all rather than filling the space.

**And a panel redrawn under a callback is a callback writing to nothing.**
`drawControls()` replaces the whole left column, so an element captured before
a fetch is detached by the time the answer arrives: the write succeeds, the
screen does not change, and it reads as a button that did nothing. Re-read the
node after any redraw.

**Motion is a second pass over an ad that already exists, and the sequencing
is the feature.** `modules/ad_builder/src/animation.ts`. A GIF here is the
static ad played two or three ways: a frame is one more `compose()` with a
different `CopySet` or a different CTA fill, so there is no second renderer and
no way for the moving version to disagree with the still one about anything
except the thing that is moving. It is offered only once a build has been
**saved** — not once a client has approved it, which is a different and later
question — and it runs as its **own job** on the same queue rather than extra
work bolted onto the render. Both halves matter: a set nobody wants animated
costs exactly what it cost before, and an animation asked for on a Friday does
not mean re-rendering eight ads that were signed off on Tuesday. The gate is
enforced on the server (the campaign file on disk is what "a static build
exists" means) as well as in the build screen, because a rule the form keeps
while the write breaks it is not a rule.

**Four published numbers, and two of them are invisible on the screen they are
broken on.** Google requires an animated image ad to be 150 KB or less, to run
at **5 frames a second or slower**, and to **stop animating within 30
seconds** — loops included. A GIF with a loop count of 0 repeats for ever,
renders correctly in every browser, passes every eye here, and is outside the
rule; one at 20fps looks *better* than one at 5. So the loop count is
**computed** from the cycle length rather than chosen (`loopsWithin`, floored
at 1, so `loop: 0` is unreachable from that file), the frame delay has a 200ms
floor, and both are printed on the panel in words beside the preview. The
browser recomputes none of it — a second copy of that arithmetic is a second
answer to "is this legal", and the two disagree the day either is edited.
`ANIMATION_RULES` carries a **source** per number, and `maxSlides: 3` /
`maxFrames: 5` are marked as **ours**, the `services/abcd_service.py` rule:
"Google requires three slides" about a number Google has never published is a
claim a client can talk us out of once they check. (sharp writes `loop - 1`
into the file's Netscape block — the GIF format's count of iterations *after*
the first — and readers disagree about that byte, so the arithmetic is done
against the larger reading and `totalMs` can never understate what a browser
will play.)

**QA runs per frame, and that is the half most likely to be quietly wrong.**
Slide 2 is different copy in the same box: it can overflow, collide with the
button, or lose its contrast where slide 1 fit perfectly. Not hypothetical —
the first run of this against the sample campaign passed the static 320x50 and
**failed** the animated one on a clipped headline, twice, naming the slide. So
every frame goes through `runQa`, frame 1's findings are kept whole (frame 1
*is* the static ad) and later frames contribute only what they got wrong,
tagged by slide. A finding frame 1 already carries is dropped rather than
repeated once per frame, or one note about the type hierarchy becomes five and
buries the one that is about slide 2. The background pass is composed **once**:
none of the motions offered changes what is behind the ink, and re-composing it
per frame triples the slowest step to produce identical bytes.

That failure is also why slides carry **per-size overrides** (`sizeSlides`,
resolved slide by slide and field by field, the way `copyForSize` already
resolves static copy). Without them a set animates at seven sizes and fails at
the eighth on copy nobody can shorten.

**Which sizes take one is read from the platform config, never decided.** Only
Google's eight banner sizes list `gif`; Amazon's specs here are static at
40-50 KB, Meta converts an uploaded GIF into a video, and Google's three
**responsive-display image assets** are image assets — Google composes its own
headline around those. A size that cannot carry one is **refused by name** on
the panel and in the job's own `animationSkipped`, because a set that came back
with five moving ads out of eight and nothing saying which three or why is the
silence this module exists to avoid. `render.ts` still strips `gif` from the
static raster's format list, and the comment there now says why: `gif` in a
format list means that placement will *also* take an animated file.

**And it never replaces the static file.** Most placements on a buy take the
still one, so a folder holding only GIFs is a set that cannot be trafficked.
The GIF is written beside its sibling with `_animated` on the end.
`AnimatedResult` is deliberately not a `RenderResult` with `format: 'gif'`, and
animations are their own list on the project rather than a row on
`RenderBatch` — a batch is a static delivery pack, and one containing only GIFs
would be read by `deliverProject` as the whole of what was built.

**One animation is one decision and one file, and the zip carries none of
them.** They shipped inside the delivery ZIP under `animated/` for exactly one
release, and that was wrong for a reason worth writing down: **a zip is one
act.** It is built once, downloaded once, and every file in it goes out on the
strength of the same press. An animation is not delivered on that press — each
is watched, approved and sent on its own, because somebody who likes the
728x90 may want the 300x250's second slide rewritten. Bundled, an animation
nobody had watched went to a client inside a package somebody approved the
*static set* of, which is the whole distinction the approval draws.

So `deliverProject` **names** them and encloses none: the README says they are
not in the zip and which have been approved, and the machine manifest carries
`inThisZip: false` rather than a path an ops person will not find in the
folder. Silence would be worse than either — a client shown a moving version
who opens a package without one needs the package to account for it.

**The approval is now the only gate between a clipped second slide and a
client's library**, since the zip is no longer what withholds a QA failure. So
`approveAnimation` refuses a failing row **by name** rather than quietly doing
nothing, and `hub/ad_builder_link.approved_animations()` refuses it a second
time — a gate enforced in one place is a gate that moves the day somebody adds
a second door. A sign-off is about the file as it was, so re-animating a size
retires its approval and carries `previouslyApprovedAt`: *"approved on the 3rd,
and rebuilt since"* and *"nobody has looked at this"* are different things to
tell somebody.

**Approving is what sends it, and that is three writes reported apart.** The
renderer records the decision, uploads the file, and the Hub files it onto the
client's record; "approved", "approved and stored" and "on their record" are
different outcomes and one tick for all three is how somebody learns not to
trust the tick — `hub/domain_links.py`'s rule. The decision **survives** a
failed upload and says so, because a Cloudinary that would not answer must not
cost somebody the judgement they made; pressing approve again retries only the
half that did not happen, and an already-stored file is never re-uploaded.
**Who approved comes from the proxy's `X-S1-User` header and never the request
body** — a name a browser can put in a POST is a name anybody can put in a
POST, and it is the entire content of the record.

**And the panel went on describing the delivery it had stopped being part
of.** The build screen's own success message said the files *"are written
beside the static ones and go into the delivery ZIP under `animated/`"* — true
for exactly one release, and nothing corrected it the day approving one became
how it reaches the client. Both halves stayed internally consistent, which is
why it survived: the deliverer really does withhold them and the panel really
does build them, so an operator built eight moving ads and waited for a folder
that was never going to exist. The wording about a failing size was the same
mistake one clause on — *"will not be delivered"* describes a zip that is now
all-static either way; what is actually true is that it **cannot be approved,
and approving is the only thing that sends one**. `test_display_ads.py` asserts
it from **both ends**, because either alone reads as fine.

**Three waits arrived on the one screen this file had already fixed for
having none.** The note above about `bgBusy(what, kind)` was written because
the Display Ad Builder's build screen made three billed calls behind a
sentence of text that did not change. The animation panel then added three
more — encoding a real GIF to preview it, running the job, and the Cloudinary
upload behind Approve — and each said a word in plain text, because `bgBusy`
was **hardwired to the background panel** and nothing generalised it. A helper
that only one panel can reach is how the next panel writes its own.

`waitIn()` and `waitBtn()` are the one reading of *put the mark here*, and
`bgBusy` delegates to the first rather than keeping the copy it had. **Two of
them because there are two targets**, which `hub-thinking.js` already draws
differently: a box gets the glyph and the elapsed line, a button keeps its
width and its **original label** — the failure that helper's own note names,
where a hand-written swap loses the label and re-enables the button in
whichever of the two exit paths the author remembered. That was live here:
`animApprove` ended by redrawing the row from the server, and a fetch that
failed there never rewrote it, leaving the button disabled reading
*"Sending…"* with nothing coming. The handle is ended before the redraw now
rather than by it. The preview is the opposite case and is left to
`isConnected`: five returns each write over the stage, and requiring every one
of them to remember a `.done()` is how one forgets.

**The upload is its own Cloudinary call, and that is not tidiness.**
`uploadCreative` passes `quality: 100`, which is an incoming transformation,
and **any** re-encode of a GIF rewrites its frame delays and its loop block —
the two numbers the compliance check measured. The stored file would still
play, still look right, and no longer have the properties that were verified.
`uploadAnimation` passes nothing but the destination.

**And they reach the client's gallery under their own kind.** `finished_ads()`
reads `project["batches"]`, which animations are deliberately not in, so
without `attach_animations()` an animated ad delivered to a client would be
invisible on that client's own record — the failure this file has already
counted six times, one tool later. `filing.KIND_LABELS` declares `animated_ad`,
because a kind nothing names arrives in a gallery as a bare key under no
heading, and it is filed in the same change that declares it: a label declared
and written by nothing is the `io_creative` failure.

**And the check for that only ran in one direction.** `test_image_audit.py`
has always required every provider `PRODUCERS` *declares* to have a heading —
which catches a label somebody forgot to write, and cannot catch a value
somebody forgot to declare. Those are different failures and only the second
is silent: the file is filed, every count on every screen stays correct, and
the gallery draws it under a bare key. It had already happened twice.
`social_request` was found by somebody opening a client's gallery and is
recorded in that test as one assertion about one string; `animated_ad` arrived
the same way one release later. A list of the two we fixed proves nothing
about the third, so `image_audit.undeclared_providers()` asks every producer
module instead, through the **AST** — prose naming a provider is not a call
site, the rule `hub/config.py`'s drift check gives. It resolves a literal and
a module-level constant holding one (which is how `ANIMATION_KIND` is actually
written) and **names a runtime value as unknowable rather than guessing at
it**, the answer `tools/linkcheck.py` gives about a concatenated URL. It
started green, which is the only way it was worth adding.

**The weight ladder is `raster.ts`'s in GIF terms, and it is cheap for a
reason worth knowing.** A GIF has no quality setting: it has a palette, a
dither, and how different two frames must be before the second re-encodes a
pixel. Both motions offered leave the background **byte-identical** between
frames, so the encoder only ever pays for the words or the button — measured
against this repo's own hero photo, a three-slide 300x250 is 19 KB and a
970x250 is 27 KB, against a 150 KB ceiling. `test_display_ads.py` asserts all
of it, including that Amazon and Meta are offered none at any size.

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

## Three index pages, and the question each one answers

There are three: **Creative** (`/creative`), **Client Tools** (`/tools`) and
**QA Reports** (`/qa`). What decides which one a tool belongs on is the
question somebody has in their head when they go looking:

- **Creative** — *I have to make something.* Grouped by what comes out:
  **Images**, **Videos**, **Audio**. A finished asset a client receives.
- **Client Tools** — *I have to do something.* **Sales**, **Media Tools**,
  **Content**, **Landing Pages**, **Google**, **Access & Setup**.
- **QA Reports** — *what is wrong, and what do we owe?* The audits, the
  matching lists and the chase lists, whether or not they are built as
  table-returning functions.

That last one is where six tools moved from, and the reason is the whole point
of having three pages. Scan All Clients, Match Sites to Clients, Match Google
Accounts, Campaign Assets Needed, Domain Renewals and Web Tickets were filed
under a Client Tools group called "Client Work", which named where the work
came from rather than what the screen is for — and every one of them is a
queue somebody works down. A report nobody thinks to look for is a report
nobody works, which is the same failure `hub/housekeeping.py` exists to undo
one step earlier. They keep their own URLs: the tile moved, the page did not,
so every Client 360 crumb, bookmark and link in this repo still resolves. They
are `extras` in `qa_home()` rather than `REPORTS` entries, because a REPORTS
entry has to be a function returning a table and these are whole tools.

**The tiles carry a headline and nothing else, four across.** Both pages used
to put a paragraph under each of forty-odd tools, which turned the index into
a scroll nobody read to the bottom of — so the tool at the end was as good as
untiled, which is the failure the tile rule exists to prevent. The prose is on
the tool's own page. `.tool-tiles.compact` in `hub/static/hub.css` is that
shape; the three-column tile beside it still carries prose and QA Reports
still uses it, because there the description **is** the finding. Both shapes
exist rather than one being converted into a worse version of the other.

**A tile that moves must leave the page it moved off.** GPT Ads Builder and the
Social Content Planner went to Client Tools (they write copy and a plan, not a
finished asset); the Display Ad Builder came the other way. Left in both
places, each would be two tiles for one tool and only one of them would ever be
updated — the drift that put Stadium to Screen in one copy of the landing-page
list and not the other. `test_menu_layout.py` asserts every named tool is tiled
exactly where it now belongs *and* absent from where it was, and
`test_gpt_ads.py` and `test_stock_search.py` assert their own tiles from the
other end.

**Video Backgrounds is Video Search.** The tool searches the footage library by
what is on screen; "backgrounds" named one thing the clips get used for. The
mount, the help key and the Cloudinary folder called `Video Backgrounds` in
`hub/video_library.py` are all unchanged — renaming the mount breaks every
existing link, renaming the help key orphans the bubble, and the folder is a
folder on the account.

### The same calculator, asked twice

`/tools/calculators/internal/<slug>` is the staff copy of a media calculator:
the identical fields, the identical `catalog.run()`, and the whole plan in one
response. The public copy at `/c/<slug>` withholds the plan until a validated
name, email and phone are captured, which is exactly right in front of a
prospect and is pure friction on our own screen — worse than friction, in fact,
because a rep sizing a buy for a client we have had for eleven years had to
type *some* contact into that form, and whatever they typed landed in the leads
panel reading exactly like a live prospect. **The internal route stores
nothing**: no estimate row, no contact, no webhook to the Suite.

Four rules on it. It is **deliberately not under `/api/`** — that prefix is in
`PUBLIC_PREFIXES` by declaration, so a route added there is a route outside the
login, and this one answers with the plan the public path is built to withhold.
It runs the **same compute function**, never a second copy of the maths, or the
two versions of one calculator would quote a client different numbers depending
on which link they were sent. **Every calculator in the catalogue has an
internal page** even though only four are tiled under Media Tools: a slug
missing from `INTERNAL_ORDER` loses its tile, not its page. And the page
**says on itself** that it is the internal one, because the client-facing copy
is one URL away and looks almost identical.

**And the guard that made it possible closed an older hole.** `modules/calculators`
is a blueprint on the hub app, so `wsgi.py`'s `AuthGuard` — which wraps only
dispatcher-mounted modules — never saw it, and the hub app has no blanket gate:
`/tools/calculators/leads`, a table of captured names, emails and phone
numbers, answered 200 to anyone with the URL. The guard now sits on the
blueprint rather than on each view, the arrangement
`modules/commercial_builder/__init__.py` arrived at for the same reason, and it
exempts `PUBLIC_PREFIXES` — a guard that also refuses the embedded calculator
is a broken grey box on a client's website, which is the failure
`test_calculator_embed.py` was written about.

## One description of what a record page looks like

`hub/static/hub-detail.css`. The SEO client page is the shape every record-like
screen in this Hub should have — a crumb and a title with the actions beside
them, white cards with a small navy heading and a control on the right,
key/value rows that line up, muted secondary text, one blue button — and it was
written as ninety lines of `.seoc-*` rules inside that one template. So the
three module screens beside it each grew their own idea of what a card is:
Sites Admin with a dark "Smart 1 Sites Admin" bar of its own, the Suite panel
with a second one, and the client lookup at `/clients` still in the old
near-black and lime green. Four screens of one product, three palettes, and a
person moving between them reading it as three different tools.

The primitives are in one stylesheet now, declared under **both** the `s1d-`
names the modules use **and** the `seoc-` ones the SEO page already had, *in
the same rule*. That is the whole point: a change to what a card looks like
lands once, and the page the look came from cannot drift away from the pages
that adopted it. It is loaded twice because the Hub is two apps —
`hub/templates/base.html` links it for the hub's own pages and `wsgi.py`'s
HubBar injects it beside `theme.css` for every mounted module — so a module
that adopts the class names needs no stylesheet of its own. `.s1d-card` carries
its own background and border rather than assuming hub.css's `.card`
underneath it, because a mounted module never loads hub.css.

What each module keeps is what is genuinely its own: Sites keeps the filter
row, the website blocks and the pager; the Suite panel keeps the fact that a
button there may hold a spinner. **A second branded header bar is not one of
them** — the Hub's sidebar is already on the page, so that bar was chrome
twice, and it is what made each of these read as a separate product. What those
modules do still need is a *second level* of navigation (Accounts / Inventory /
Packages; Create / Manage / Activity / Status), which is `.s1d-subnav`. Sites
marks the current section from `request.endpoint` rather than having every view
pass one in — a nav that has to be told which entry to highlight is a nav that
gets it wrong on the next page somebody adds — and the shared strip answers to
`.active` as well as `.on`, because the Suite panel's tabs are driven by a
script that has written `active` since they were an underline bar. Renaming
that in the script to suit a stylesheet would be the stylesheet deciding what
the page's state is called.

**A status pill says the same thing everywhere.** `sites_admin.status_class()`
returned `good` / `warn` / `bad` / `muted`, which are not the modifiers the
shared sheet defines, so its pills were a second set that looked nearly like
the Hub's. It returns `ok` / `warn` / `bad` and **`""`** now — and that last
one is the point: a status this app has never seen is not a *bad* status, so it
is grey rather than red, the confident wrong answer this codebase keeps having
to undo.

**A prebuilt bundle can be restyled, and cannot be rebuilt.** `clients_app/` is
a compiled React app: the minified JS and CSS are committed and there is no
source in this repo, so its markup cannot be edited. What it can be given is a
later stylesheet, and `hub/static/clients-theme.css` is injected by
`clients_index()` *after* the bundle's own `<link>` so equal rules win.
Remapping the five variables it declares does most of the work; the rest is
there because the bundle also hardcodes colors in rules carrying no variable at
all, and a half-converted palette is worse than an unconverted one. Two things
it deliberately does not do. `--s1-dark` is that bundle's ink **and** its dark
surfaces — one variable doing two jobs — so the surfaces are named individually
rather than remapped, or the body text would come out as heavy as a heading.
And nothing in it changes layout: this is a color pass over a working tool, not
a rebuild of one. It is scoped to a `body` class even though it is injected on
one page, because `.kpi`, `.badge`, `.tabs` and `.search` are ordinary words and
an unscoped rule for one of them would restyle a module nobody was thinking
about.

**`:not(:has(.main))` was matching modules, and one of them was laid out from
x=0.** The sidebar offsets `<body>` by 224px except where the page already
offsets itself — hub.css lays the Hub's own pages out with
`.main{margin-left:224px}`, and applying both pushed the content 448px right.
The guard was `.main` anywhere in the document, and "main" is one of the most
ordinary class names there is: the client lookup names its content wrapper
`.main`, so **the whole React app got no offset and its first column of tiles
sat behind the sidebar**, on every visit, with nothing erroring and every page
still passing linkcheck and pagecheck. It is `.shell > .main` now — only
`base.html` puts a `.main` directly inside a `.shell`, which is precisely the
layout the rule needs to keep its hands off. Image Creator, which also uses
`.main` and reads `--s1hub-offset` to size a full-height canvas, was reading 0
for the same reason.

`test_detail_ui.py` asserts all of it, including that the SEO page no longer
restates the rules it handed over: a copy left behind is not a broken page, it
is a page that silently stops matching the others the next time one of them is
edited.

**The look reaches the Hub three ways, and a sheet added to two of them
reaches most of it and not the rest.** `hub/templates/base.html` links it for
the Hub's own pages, `wsgi.py`'s HubBar injects it into the twenty
dispatcher-mounted modules, and the hub app's own `after_request` injects it
into the blueprints registered on it — Google Access, the Image Picker, Page
Image Optimizer, Tickets, the Calculators, Video Search and the Commercial
Builder. That is the same three-way split `hub-thinking.js` already names, and
it bit again here: `theme.css` had never been on any of those blueprint pages,
so adopting the shared look on them did nothing at all until the third injector
carried it.

**An opt-in page layer, because the sweep is otherwise hundreds of edits.** A
module puts `s1d-page` on its content wrapper and the ordinary elements inside
it — a bare `<button>`, a `<table>`, an `<input>`, an `<h1>` — take the Hub's
look without a class on each one. Site Scans alone declared **three palettes
across five staff templates** (`--blue` as `#5b8bff`, `#2563eb` and the cyan
`#009ED2`), each restating `button {}`, `table {}` and `.card {}` in almost the
same words; converting that element by element is hundreds of edits and a fresh
chance to miss one, where deleting a stylesheet block and adding one class is a
change you can read.

Opt-in and scoped, both deliberately: `button`, `table` and `.card` are far too
ordinary to style globally in a sheet twenty modules receive, and a module that
has not asked for it is untouched. It **accepts the names pages already use**
rather than requiring a rename — `ghost` and `sec`, `.on` and `.active`, a tile
written as `<b>` and `<span>` as well as `.v` and `.l` — because a stylesheet
that needs the page renamed to suit it does not get adopted. And it **excludes
the Hub's own injected controls by name**: `hub-help.js` renders a help bubble
as a `<button>`, so a bare element rule turned every help dot on every adopting
module into a pill at once, which is how a broad rule goes wrong.

**A label sits above its control, except one that contains it.** That shape is a
tick box and its wording. The exclusion is a `:has()` rather than something for
the page to win back, because the injected `<link>` comes *after* the module's
inline `<style>` — so a same-specificity local rule loses, which is exactly what
makes adoption a matter of deleting rules.

**And one stray `</div>` closes the wrapper, after which none of it applies.**
The page still renders, every link resolves, `pagecheck` passes — and every rule
scoped to `.s1d-page` silently stops at the break. That is what one extra
closing tag did to Smart 1 Ads, and it is not visible in the diff, so
`test_detail_ui.py` walks the rendered HTML and asserts the wrapper still
contains the page.

**What is kept local is kept for a stated reason.** Smart 1 Ads' buttons default
to *secondary* — nearly every one is a row action and `.b-primary` is the one
that commits, so a page of blue buttons would have no primary action. Google
Finder's `.btn` stays because `app.js` builds most of them with a per-platform
background that color-codes the action to the account it acts on. Neither is
taste; both are the module's own meaning, and the shared sheet carries the
shape either way.

**Client-facing templates are untouched throughout.** The scan widget and its
reports, the client's proposal and social pages, the public estimate, the
gated calculators and the Google Access connect flow are served to somebody
with no Hub account, and a staff look is not what they should arrive wearing.
`test_detail_ui.py` asserts both directions.

**Adopting the primitives and adopting the element layer are two decisions,
and the Proposal Builder takes only the first.** `s1d-subnav` and `s1d-tile`
are asked for one at a time by name; `s1d-page` turns on a layer of bare
element rules, and `.s1d-page button` carries three `:not()`s, which makes it
(0,5,1) — above every one of that module's six single-class button names.
Taking the layer there would have drawn `btn-gold`, `btn-line`, `btn-ghost`
and `btn-back` as solid brand blue, so *Back* and *Convert to IO* would have
looked like the same offer, on the wizard where the difference is a signed
insertion order. The element layer is for a page with no vocabulary to lose —
Image Creator's project list, which had four local rules and now has none.
Both directions are asserted, and the layer check reads the **class
attributes** rather than the file's text: the reason the Proposal Builder
declines it is written in a comment in that template, and a check a file's own
explanation of itself can fail is one somebody deletes.

**What the Proposal Builder did have to lose is the second branded bar.** A
sticky navy strip reading SMART 1 SALES BUILDER sat above the Hub's own
sidebar — chrome twice, and what made the tool read as a separate product
standing next to Client 360. Its four views are a real second level of
navigation and survive as the shared strip, `id="topnav"` and the `on` class
kept because `nav()` selects on both; the rep's name survives as a control in
that strip rather than a chip on the bar, because it is the attribution
written onto every proposal built here and "Set your name" has to be legible
as unset. A sub-nav button is *excluded* from the page button rule rather than
out-specified — three `:not()`s make that rule hard to beat, and an exclusion
does not depend on winning a race.

**A gallery tile is not a card.** Image Creator's project list called its
thumbnail `.card`, which is the name the shared layer uses for a record card,
so adopting the layer would have put a record card's padding and border round
a photograph. Renamed `.proj`, and the collision is the ordinary way a shared
element layer bites: the class was correct in isolation and wrong the moment
somebody else meant something by it. The editor itself takes none of this — it
is a full-height canvas workbench with its own toolbar and tool rail, which is
the shape the Hub collapses its sidebar for.

**Three tools shipped the same second branded bar, and the last two are gone
now.** The Commercial Builder's said *Creative Hub · Commercial Builder* and
the IO Builder's said *SMART1 Campaign Builder AI* — both sticky, both
full-width, both above the Hub's own sidebar, which is chrome twice and is
what makes a tool read as a separate product standing next to Client 360. The
IO's also named the tool a third thing: the tile, the sidebar and Client 360
all call it the IO Builder, and the browser tab did not.

What each needed in its place is not the same, which is the point. The
Commercial Builder's Dashboard and Spot Library are a real second level of
navigation and become the shared strip — **marked from the request**, because
a nav that has to be told which entry to highlight gets it wrong on the next
page somebody adds, and a **wizard step marks nothing**, since the step has
its own stepper and lighting up Dashboard on step four says somebody is
somewhere they are not. The IO Builder is one screen with a progress bar under
its chat, so there was nothing to put back at all.

**And the strip goes inside the page container, not above it.** A full-bleed
bar at the top of the viewport is the branded bar again wearing the shared
classes — it sits over the Hub's own breadcrumb and pins the page's primary
action to the edge of the screen rather than to the column it belongs to.

**A button in the strip is a button, not a nav link.** The Commercial Builder
had already paid for this inside its own sheet: `.cb-topnav a` is (0,1,1) and
`.cb-btn-primary` is (0,1,0), so the muted gray won and painted the
*+ New Commercial* label a dull gray-brown on solid blue. Adopting the shared
strip would have done it again and the module could no longer have answered,
because `hub-detail.css` is injected **after** a module's own stylesheet and
wins every tie the module used to win. So the exclusion lives on the strip —
`a:not([class*="btn"])`, matched on the class containing *btn* rather than on
a list of names, for the reason `ghost` and `sec` are both accepted: the name
is the page's.

**The wide tool that never asked for the rail to fold was the one named for
it.** This file has listed the IO's printable documents beside the Display Ad
Builder's bench and the Proposal Builder's wizard since `collapsed_default`
was written, and the other two are covered — one by the `/tools/display-ads`
prefix, one by `data-s1hub-collapse="1"` — while the IO Builder carried
neither. Its two panels want 970px between them before anything wraps, so
224px of a nav nobody reads while they work is what turns that into a
horizontal scroll on an ordinary laptop. It carries the attribute now, and
**not** `data-module`: no walkthrough is registered for it, and offering a
tour that does not exist is the silence Smart 1 Ads shipped on Settings and
Live campaigns.

## Declared and never wired

The single failure this codebase has paid for most often, and there was no
check for it until now. Every instance is written up above and each cost a
feature that looked complete from every screen: Page Image Optimizer's
`>>> INTEGRATION POINT <<<` naming three writers that have never existed;
`hub/storage.manifest()`, whose docstring says it "feeds the orphaned-asset
audit" for an audit that had never been built; `io_creative` sitting in
`filing.KIND_LABELS` with no writer; `simvoly_client.check_limits()` written
with no caller while the page that needed it 500'd on every visit;
`TICKET_CREATE_FIELDS`, `TICKET_MANAGE_FIELDS` and `update_ticket()` existing
unused while the Hub wrote four of a ticket's eight fields; and
`openai_service.write_runway_prompt()`, written and uncalled until the button
was built. None of them errored, which is the whole difficulty — an uncalled
function is indistinguishable from a working one until somebody goes looking
for the feature it was half of.

`test_unwired.py` is the sweep. Four rules on it.

**It is an allowlist, not a rule.** A thin client over somebody else's API is
reasonably kept whole, and `check_limits()` is the proof in both directions:
it was unwired *and* it was exactly what was needed. So every survivor carries
the reason it survives, which is what makes the next one somebody adds a
decision rather than an accident. Held to
`check_stale_json_exemptions()`'s discipline: an entry naming a function that
is gone, or one something now calls, **fails**.

**Textual, not a call graph.** This repo reaches functions from Jinja, from
JavaScript, from an entry in a table and through `getattr`, and a call-graph
walk would report every one of those as unwired. A name appearing nowhere else
in any file is the only claim that survives all four. Decorated functions are
skipped entirely — a route is called by its framework, and naming it nowhere
is normal.

**Whole words, and one pass.** `name in src` per name per file is 1,700 names
against 1,500 files and takes the best part of a minute, which is a check
somebody drops from CI; counting identifier tokens once is six seconds. It is
also the more honest question, and it earned that immediately:
`assign_customer` had been reading as referenced because **`unassign_customer`
contains it** — the substring trap this file already names about `.btn`
matching `subtle`.

**It excluded its own allowlist by accident, first time out.** `ALLOW` names
every survivor as a string, so counting this file as a reference made the
sweep report a clean nothing — which is exactly how
`unmirrored_json_writers()` came to exempt each scanner, its test being
`"jsonstore" not in src` while every one of them explained jsonstore in its
own prose. It skips itself, it is handed a function nothing calls and required
to name it, and it started green.

**And prose was counting as a call site.** The first pass scanned `.md` too,
so the paragraph you are reading — which names `assign_customer` to explain
why it is allowed — read as somebody calling it and silenced the finding it
was written about. That is `hub/config.py`'s drift rule inverted: there,
matching text reported a docstring explaining the fix as the defect. Code and
templates only; a function is not reached from a Markdown file. Excluding it
immediately surfaced two more, both of which had been masked by their own
explanations: `qrcode_service.is_available()`, whose docstring said *"asked by
the CTA route"* when the route reads the reason off `generate_qr()`'s own
result instead — the better answer, since a separate pre-check is a second
reading of one question — and `google_client.verify_ga4()`, which needs an
agency GA4 token nobody holds.

**Three came out rather than in.** `hub/target_areas.running_zips()` was
byte-for-byte `all_zips()` — two readings of one question, which is the drift
this codebase names most often. `hub/ai.usage_summary()` was a second
reading of the by-module OpenAI spend `/api/quotas` already reports from the
same log. And `hub/access.may_view()`'s docstring said it was "the whole
access rule, in one expression that **both call sites read**" while neither
did — a shared rule that nothing shares is worse than no shared rule, because
the next reader believes it.

**One entry was a finding rather than an exemption, and it has been
removed.** `modules/sites_admin/seed_boot.py` promised that a freshly-recreated
database *"repopulates itself on the next startup with no manual
paste/upload"* — and it was imported by nothing, there is no
`seed/portfolio.json` for it to read and there never has been, so it had never
run and could not. The allowlist said REPAIR THIS OR REMOVE IT and named both
halves; repairing it needs a seed file exported from the live portfolio, which
is not a thing a check or a cleanup can conjure.

So the promise is gone rather than left standing. It is the same failure as a
report that answers zero when it could not look, in a docstring: **the Sites
Admin portfolio does not repopulate itself, and the file saying otherwise was
the only reason anybody would think it did.** That matters here more than most
places, because this Hub's own backup rule is about exactly this — the Render
disk is not backed up, and a plan change, region move or resize hands back an
empty one. Recovering that table is a management-panel *list-projects* export
imported through Sites Admin's own inventory import, by hand, and nothing in
the boot path shortens that. Wiring a seed at boot would also be a database
write in **two** gunicorn workers, which is the `create_all()` advisory-lock
problem one layer up, so it is not a one-line restoration either.

Removing it is checkable rather than remembered: the allowlist entry went with
the file, and `test_unwired.py` fails on an entry naming a function that is
gone — so the removal cannot be half-done.

## Conventions

- **No new Python dependencies** unless genuinely unavoidable.
- Module layout: `modules/<name>/app.py` (Flask app or blueprint),
  `templates/`, mounted in `wsgi.py` with a try/except and `_fallback_app()`.
- New tools get a tile on the index page that answers the question they answer
  — `hub/templates/creative.html`, `hub/templates/tools.html`, or the `extras`
  list in `qa_home()` — under the right group, and on **one** of them.
  A tool with no tile is invisible — six were, for weeks.
- Anything producing client work should call `audit.log(...)` with
  `client=` so it appears on that client's 360 record.
- Summary first, detail behind a click. Label absent data explicitly.
- Guard boot-time failures, but **record them** — a swallowed exception is how
  `/signup` 404'd for a day with no clue why.

## Verifying a change

Booting the app catches what static analysis misses; several serious bugs were
only found by running it.

```bash
python3 -c "import ast,pathlib; [ast.parse(p.read_text(errors='ignore')) \
  for p in pathlib.Path('.').rglob('*.py') if '_attic' not in p.parts]"
python tools/jscheck.py            # every .js file and inline block, via node
python tools/checktemplates.py     # the Jinja-carrying blocks jscheck skips
python tools/linkcheck.py          # every internal URL resolves, every url_for has a route
python tools/pagecheck.py          # the page the browser actually receives
python tools/integritycheck.py     # known defect patterns
python tools/spellcheck.py         # American English in everything a person reads
python3 test_jsonstore.py          # the mirror restores, and one answer on who is outside it
python3 test_db_boot.py            # a database blip at boot is not a verdict for
                                   #   the life of the worker, and sign-in says
                                   #   so in words rather than answering 500
python3 test_scheduler_health.py   # the jobs working, not just the loop alive:
                                   #   overdue, failure streaks, and the worker
                                   #   that cannot see the timings
python3 test_smartforecast.py      # weather lifecycle rules, immutable history,
                                   #   public embeds and Render disk recovery
python3 test_report_cache.py       # one run per report per day; a failed run is never
                                   #   the answer, and a write drops what it changed
python3 test_qa_reports.py         # every report on /qa answers and is drawable,
                                   #   and one that could not look never renders
                                   #   as "all clear"
python3 test_ads_module.py         # Smart 1 Ads: the Ads Editor handoff, the client join
python3 test_ads_estimate.py       # the estimate a client reads, and what they can answer
python3 test_ads_keyword_plan.py   # measured CPC, the access tier, the deploy preflight
python3 test_ads_explainer.py      # the bubbles, the per-screen tour, the walkthroughs
python3 test_help_layer.py         # every bubble placed has help behind it, both
                                   #   ways one is placed, a key built at runtime
                                   #   named rather than guessed at, the
                                   #   walkthrough saying which step it cannot
                                   #   run, a selector that tests nothing
                                   #   clearing no floor, and coverage measured
                                   #   against the tiles rather than a list
                                   #   that went stale
python3 test_target_areas.py       # target areas, delivery, the Suite push
python3 test_lead_delivery.py      # one write path per lead
python3 test_scan_widgets.py       # widget placements: leads counted, pause/edit/delete
python3 test_scan_run.py           # what a prospect on somebody else's
                                   #   website is told: a callback token
                                   #   that survives the URL, a run that
                                   #   is over saying so rather than
                                   #   being polled to the ceiling, no
                                   #   promise of an email nothing here
                                   #   can send, and one unlock per run
python3 test_prospect_queue.py     # who to call, in the order the work has to happen
python3 test_upsell_report.py      # what the audit says we could sell each client:
                                   #   coverage named, recorded vs observed kept apart
python3 test_prospect_record.py    # the record a scan produces: four kinds of empty on
                                   #   the Suite card, a timeline that names what it
                                   #   could not read, files, and converting
python3 test_unwired.py            # nothing is defined and left uncalled
                                   #   without a reason written down
python3 test_audit_summary.py      # the paragraphs a prospect reads: a
                                   #   measured figure re-typed is not an
                                   #   invented one, and an amount nobody
                                   #   measured is still refused
python3 test_website_audit.py      # the spend block that leads the audit, the customer
                                   #   placement, the lead every scan files, merging two
                                   #   rows that are one prospect
python3 test_prospect_explainer.py # the two screens explain themselves: every key
                                   #   resolves, every tour step rings a card the
                                   #   page draws, and none of it reaches a prospect
python3 test_detail_ui.py          # one description of the record-page look, and the
                                   #   three module screens that read from it
python3 test_magic_resize.py       # one design into the whole size set: sizes read
                                   #   from the kit rather than restated a fourth
                                   #   time, a frame the engine is unsure about
                                   #   marked rather than shipped, copy that
                                   #   reaches a hand-tuned frame and layout that
                                   #   never does, and a model that proposes
                                   #   positions rather than a picture
python3 test_menu_layout.py        # the three index pages: every tool tiled once and
                                   #   only once, and the internal calculator that
                                   #   computes the same plan and captures nothing
python3 test_sales_status.py       # the pipeline on the dashboard: five signals,
                                   #   one reading, and counts that land on rows
python3 test_knack_map.py          # what is mapped in Knack and what is
                                   #   assumed: read from the owning modules,
                                   #   a confirmation retired when repinned
python3 test_io_reconcile.py       # the orders we sent against the campaigns
                                   #   Knack has: a stale source never reads as
                                   #   proof, a row can be settled, and the
                                   #   money a campaign is trafficked at
python3 test_io_records.py         # the order written down: one row per
                                   #   number, a resubmission that revises it,
                                   #   and bookkeeping that cannot fail a submit
python3 test_campaign_cost.py      # one number for what the campaign costs: the
                                   #   cover, the plan, the summary and the IO
python3 test_quote_validity.py     # how long a price stands, the Expired nothing
                                   #   set, what Suite may decide, and delivery
                                   #   back under the media plan
python3 test_proposal_share.py     # the client's copy: who opened it, how often,
                                   #   and an acceptance tied to one revision
python3 test_proposal_targeting.py # the coverage map, the pasted location list,
                                   #   the competitor research, and a bulleted
                                   #   list that reaches the client as a list
python3 test_proposal_consulting.py # the one line the card does not name: the
                                   #   card stays the wholesale card, the join
                                   #   is an exact string, a description is
                                   #   required because the product name is
                                   #   shared, and it survives to both the
                                   #   client's plan and the insertion order
python3 test_proposal_spec.py      # the 13-part spec, the creative gate, ROI math,
                                   #   the 2x quoted rate, the product a goal leads
                                   #   with, ZIP exceptions and what the Suite covers
python3 test_rate_card_coverage.py # every product on the card, bought on a
                                   #   proposal that renders: eight campaigns
                                   #   derived from the card's own categories,
                                   #   a ninth holding every name that means
                                   #   two products, and a new category that
                                   #   fails by name rather than being skipped
python3 test_landing_maker.py      # built pages stay public and chrome-free
python3 test_quote_numbers.py      # uploaded quotes are numbered, drafts delete
python3 test_api_usage.py          # the Google/ElevenLabs/Cloudinary estimates
python3 test_social_plan.py        # the post mix, the copy checks, the CSV
python3 test_social_content.py     # multi-location requests, the client's four
                                   #   signed pages, the idea weighting, and a
                                   #   push failure that never reads as scheduled
python3 test_web_tickets.py        # the object_107 ids, the form, what a write carries
python3 test_ad_copy.py            # the ad copy object, discovered not guessed;
                                   #   one candidate or none, nothing invented, and
                                   #   the triage control on all three forms
python3 test_campaign_support.py   # the object_121 ids, every option off the live
                                   #   object, and what a write may not contain
python3 test_campaign_assets.py    # campaigns waiting on an asset, by media partner
python3 test_stale_creative.py     # the row actions, the evergreen overlay, the login gate
python3 test_dashboard_trends.py   # the monthly readings accumulate; no card claims a comparison
python3 test_celebrations.py       # birthdays and anniversaries: what is still to come, and who is interrupted
python3 test_housekeeping.py       # warnings moved off pages nobody can act on, with the page named
python3 test_blog_publish.py       # blog taxonomy, approved topics, the CMS panels
python3 test_webargs.py            # a caller's number: never a 500, never a
                                   #   negative slice, and the three call
                                   #   sites the shared helper never reached
python3 test_signing.py            # what this Hub signs with: a literal
                                   #   fallback is a forgeable admin session,
                                   #   a placeholder is set and is not a
                                   #   secret, and two readers of one salt
                                   #   that each generated their own random
                                   #   secret refused each other's cookies
python3 test_analytics_ask.py      # a GA4 comparison keyed on the tag Google
                                   #   actually sends, a time series left in
                                   #   the order it was asked for, and a total
                                   #   that says what it is the total of
python3 test_schema_questions.py   # "none" is an answer to "any awards?", one
                                   #   reading of whether a schema can be
                                   #   approved, and two sources that were
                                   #   reported as zero rather than not built
python3 test_landing_images.py     # a picture on a client's landing page is
                                   #   theirs or it is not captioned as
                                   #   theirs, and a size nobody measured is
                                   #   not a size
python3 test_blog_images.py        # one image per post rather than one per
                                   #   title, a badge that counts the posts
                                   #   the list still shows, a hero filed at
                                   #   full size saying so, and a pending
                                   #   image the audit knows is not an orphan
python3 test_seo_tasks.py          # one page, however its URL was written:
                                   #   the ticket dedupe compared the raw
                                   #   string while the title beside it was
                                   #   already canonical
python3 test_seo_page.py           # the SEO list and record: a pill with four
                                   #   answers, a name nobody gave, a failed
                                   #   record that is not an empty one, SEO
                                   #   work reaching the client's own page,
                                   #   two editors that keep what was typed,
                                   #   and the book read live with the source
                                   #   named on both screens
python3 test_image_pdf_optimizers.py  # the two file tools: what they refuse,
                                   #   animation that survives a resize, and
                                   #   no Pillow repr or Ghostscript stderr
                                   #   reaching the person who uploaded
python3 test_utm_bg_tools.py       # the two tools no test named: a search
                                   #   whose count was the page's own length,
                                   #   a CSV that searched five of eleven
                                   #   fields and came back empty, a cut-out
                                   #   filed with dimensions measured and
                                   #   dropped, a credit cache one worker in
                                   #   two could see, and a batch the screen
                                   #   offers and the framework refused in HTML
python3 test_image_download.py     # image downloads, the shared zip builder, and the
                                   #   preview every gallery draws instead of the original
python3 test_image_audit.py        # every image attached to a client or a lead,
                                   #   a gallery you can search, and nothing
                                   #   filed under a provider nobody declared
python3 test_client360_health.py   # the derived health strip: every pill from
                                   #   the stores, a source that refuses drawn
                                   #   as its own state, and both halves of the
                                   #   blogs rule answering to one day
python3 test_client360_layout.py   # the record's cards land in their rail
                                   #   sections by name, driven in node — a
                                   #   match list that stops matching piles
                                   #   every card into Overview with the page
                                   #   still looking complete — and the four
                                   #   actions the accordion's toolbar carried
python3 test_commercial_dashboard_layout.py
                                   #   the Commercial Builder dashboard's own
                                   #   sections, and the rail label assistive
                                   #   technology reads
python3 test_client_images.py      # every module that logs client work is one the
                                   #   record can name; deleting a client image, the
                                   #   count, the one brand
                                   #   card, the contact details offered into the strip,
                                   #   the display-ads work log, and the way back
python3 test_client_uploads.py     # the client upload link, and the client an IO creates
python3 test_image_picker.py       # upload sources, deleting a gallery, the two questions
python3 test_stock_search.py       # four sources in one search; a missing folder is not an empty one
python3 test_alt_text.py           # the alt-text scan, its clamps, the Claude prompts
python3 test_gpt_ads.py            # the 1:1 gate, the copy checks, the ad-ops ZIP
python3 test_video_library.py      # the footage index, its status row, the page's palette
python3 test_sites_match.py        # live-only matching, the name pass, a client's missing URL
python3 test_domain_links.py       # attaching a domain everywhere, orphans, renewals,
                                   #   the QuickBooks match and do-not-renew
python3 test_sites_billing.py      # hosting charges joined to sites: unbilled, and billed-but-dead
python3 test_google_links.py       # orphaned GA4/GTM/Search Console accounts
python3 test_google_access.py      # the paused Ads flow, and who an invite is for
python3 test_google_index.py       # the Google sweep: no request, and none vs cannot look
python3 test_analytics_ids.py      # two names for one property are not a
                                   #   disagreement: the measurement id Knack
                                   #   holds against the property id Google
                                   #   returns, and what must keep saying
                                   #   mismatch
python3 test_msa_embed.py          # the signing page: public, chrome-free, ours to frame
python3 test_landing_embeds.py     # the gameplan embeds: framable by us, leads land
python3 test_calculator_embeds.py  # the calculator embeds: framed, public, chrome-free
python3 test_work_attribution.py   # work filed against a client reaches that
                                   #   client's record: the five keys work_log
                                   #   reads, and a table keyed on the name a
                                   #   module actually logs under
python3 test_activity_logging.py   # every module's work is attributable: an
                                   #   import is not a call, a module's own
                                   #   log() wrapper is resolved, and the
                                   #   remainder is declared with its reason
python3 test_radio_builders.py     # the two radio builders: the client's own
                                   #   approval page public and chrome-free,
                                   #   nobody's trademark leaving the building,
                                   #   and a long read flagged rather than trimmed
python3 test_commercial_parity.py  # the copy a spot carries: a shared rule for
                                   #   whether the words actually say the brand,
                                   #   the address and the phone, an end card
                                   #   deleted out from under the check that read
                                   #   the client record instead, and the finished
                                   #   cut reaching the Suite once rather than twice
python3 test_radio_ads.py          # the Radio Ad Creator's second half: a bed
                                   #   composed to the spot's own length rather
                                   #   than a prompt saved and a tone played, the
                                   #   dB pair read from the one shared music
                                   #   table, the browser's WAV encoder held
                                   #   against the server's own probe in node,
                                   #   not-measured never folded into pass, an
                                   #   override that needs a reason and a name,
                                   #   and a variation that carries the scripts
                                   #   without the audio
python3 test_radio_parity.py       # Radio Promo's half of that list: the :10
                                   #   and the :60 that were unbuildable, the
                                   #   cost note said at pick time rather than
                                   #   after the read exists, the beats moved
                                   #   out of the prompt's own prose, and a
                                   #   named script panel run on the copy --
                                   #   where certainty rather than severity
                                   #   decides what may refuse a billed record
python3 test_commercial_heygen.py  # the spokesperson clip actually arrives
python3 test_commercial_providers.py # a key that was added is read, and works
python3 test_commercial_meter.py   # every billed call records, no invented price,
                                   #   and a library of what was actually delivered
python3 test_commercial_audio.py   # generated sound effects and music: a published
                                   #   limit refused by name rather than clamped, a
                                   #   retry that cannot re-spend on either worker, a
                                   #   length derived or not measured, a generation
                                   #   counted apart from a character of speech, and
                                   #   an effect capped to the shot it sits on
python3 test_commercial_library.py # what a spot is versus how it is made, the
                                   #   twelve archetypes and what each one needs
python3 test_commercial_compliance.py # which published rules a spot engages, whose
                                   #   they are, and the acknowledgment before filing
python3 test_commercial_mock.py    # the mark that says a provider is not live:
                                   #   named where the work is rather than as a chip
                                   #   on another screen, only for routes that really
                                   #   report it, and never on the client's page
python3 test_commercial_review.py  # the client's review link: public and chrome-free,
                                   #   three answers, the strictest one wins, a
                                   #   refusal that stops a delivery, and the
                                   #   answers reaching the dashboard
python3 test_hyperframes.py        # the sidecar renderer and its two skills:
                                   #   configured is not reachable is not
                                   #   working, a mock that is never filed, a
                                   #   beat list validated rather than trusted,
                                   #   a per-beat cap that holds inside the
                                   #   window, and a Vox explainer refused
                                   #   where nobody sells the slot
python3 test_commercial_wizard.py  # the seven steps, the batch an approval opens,
                                   #   the client join, the spec check,
                                   #   the QR destination and who owns the scan; the :06,
                                   #   shots inside beats with their grammar, the published
                                   #   thresholds and whose each is, and the Amazon warning
python3 test_commercial_explainer.py # the bubbles, the per-screen tours, and a
                                   #   walkthrough that drives the page it is on
python3 test_io_start.py           # starting an IO from a proposal, a client or a file
python3 test_io_builder.py         # the IO Builder's own model calls: one reader,
                                   #   the hosted tool opt-in, an answer cut short
                                   #   named as that, the landing page read rather
                                   #   than imagined, and a refused order that is
                                   #   not filed as an order
python3 test_drafts.py             # interrupted work: the IO's server draft and
                                   #   its list, the proposal reopening where it
                                   #   was left, and a cap that names what it drops
python3 test_landing_spec.py       # what a landing page is for, and what it sells
python3 test_client_groups.py      # grouped clients: what merges, what must not double
python3 test_client_owners.py      # whose client is this, and what is outstanding
                                   #   on them: one owner, a partner selected
                                   #   rather than stored as a rule, marks
                                   #   applied on read, and a source that could
                                   #   not be read named rather than counted
                                   #   as nothing
python3 test_ghl_scopes.py         # the Suite app's scopes, and the granted-vs-requested diff
python3 test_ghl_blog.py           # a client's llms.txt published to their
                                   #   own sub-account rather than the
                                   #   agency's blog, a duplicate guard that
                                   #   says when it could not look, and the
                                   #   address Suite actually assigned
python3 test_write_attribution.py   # every write into a client's own account
                                   #   has a name against it: in both modules
                                   #   the creating half of a pair was the half
                                   #   left out, and the remainder is declared
                                   #   rather than left as an absence
python3 test_suite_panel.py        # creating and deleting Suite sub-accounts:
                                   #   a claim taken before the work and shared
                                   #   between workers, a duplicate check that
                                   #   says when it could not look, and a
                                   #   deletion recorded against the account it
                                   #   deleted rather than the name typed at it
python3 test_suite_embed.py        # Hub pages framed in Suite: the cookie, the chrome, who may frame
python3 test_suite_sso.py          # the client half: the location id is the
                                   #   authorization, and every way that goes wrong
python3 test_calculator_embed.py   # the media calculators framed on smart1marketing.com
python3 test_display_ads.py        # the display layouts, the build screen's contracts, and
                                   #   the animated GIF: whose rule each number is, a loop
                                   #   that can never be endless, QA on every frame, and
                                   #   one approval per file -- never the zip
python3 test_user_accounts.py      # the roster, the two levels, the crawler block, the throttle,
                                   #   and the signed-in headcount on the dashboard
python3 test_blueprint_guards.py   # nothing answers a stranger: every route the
                                   #   composed app serves -- reads and writes,
                                   #   fixed paths and the third addressed by a
                                   #   token or a slug, hub app and all
                                   #   thirty-one mounts -- probed with no
                                   #   session, against allowlists that say why
                                   #   each is public; and a walk that finds no
                                   #   mounts, or a rule it could not build a
                                   #   probe for, is a failure rather than an
                                   #   empty sweep
python3 test_env_config.py         # one setting, every name it answers to, and who logs
                                   #   and a template nothing renders, which no
                                   #   other check here can see
python3 test_knack_websites_source.py # websites live where Knack answers, the
                                   #   export where it will not, and a failed
                                   #   pull that never empties a good one
python3 test_spelling.py           # the spelling check still bites, its exemptions
                                   #   still name real files, and it reads the one
                                   #   module that is not Python
python3 test_client_prefill.py     # one client reader: what a form is offered,
                                   #   what it is never offered, and what a
                                   #   model is told about the client
python3 test_client_logos.py       # a logo we found reaches the client's gallery,
                                   #   once, labeled with where it came from
python3 test_ai_proposals.py       # the model proposes, the code decides, a person
                                   #   presses: project names, client photos, ticket type
python3 test_thinking.py           # the mark that says a scan or a model is running:
                                   #   one implementation, three kinds, both halves
                                   #   of the app, nothing claiming a result, and
                                   #   the three inline copies held in step
python3 test_llms_hosting.py       # a client's llms.txt: robots per user-agent
                                   #   group, the header that would have said
                                   #   noai, the 301 the redirect has to be,
                                   #   and a robots we could not reach that is
                                   #   never read as permission
python3 test_search.py             # the top box: a client the query names comes
                                   #   first, and every screen is findable
python3 test_oauth_redirects.py    # every OAuth callback, the hostname each is
                                   #   built from, and — the half nothing
                                   #   asserted — that the code sends the
                                   #   string the panel tells you to register
python3 test_ghl_oauth.py          # the Suite install: a refresh that keeps
                                   #   the token it was not given, a disconnect
                                   #   that does not undo itself, a rotated key
                                   #   that reads as re-consent rather than a
                                   #   crash, and a status carrying no secret
python3 test_site_blocks.py        # the website blocks a page is built from
python3 test_hub_help_layer.py    # the hub's own tours: offered at all,
                                   #   and a walkthrough button only where a
                                   #   scenario is written for that page --
                                   #   swept across every hub page, by a sweep
                                   #   that does not sign itself out partway
python3 test_linkcheck_helpers.py # the URLs linkcheck could not see: a
                                   #   module's own request helper, and
                                   #   sendBeacon; and prose is not a
                                   #   call site
python3 test_ci_gate.py            # the gate runs every check a person runs
```

The test files need no pytest and no new dependencies; each runs against a
temporary data directory and a throwaway SQLite database, so none of them
touches `/var/data` or the real one.

**All of this runs on every pull request** — `.github/workflows/checks.yml`,
the single gate. CI runs the same scripts a person runs, so a green run means
the same thing in both places and no check exists only where nobody can
reproduce it.

**And that sentence was not true, in the file that makes it.** Seven of the
files this list names were run by nobody but somebody who thought to type
them: `test_unwired.py`, `test_thinking.py`, `test_menu_layout.py`,
`test_detail_ui.py`, `test_ai_proposals.py` and the two explainer files. What
they hold is not marginal — that nothing is declared and left unwired, that
one tool is tiled once and its trail names it, that four copies of the wait
mark agree, that the three places a model proposes carry no route to a write.
An eighth, `test_site_blocks.py`, was in neither this list nor the workflow,
which is the same gap one step further on. Every one of them passed; they were
simply gated by nobody, and the list saying otherwise is what stopped anybody
noticing — a sweep that has quietly stopped sweeping, reporting a clean bill of
health about the part it still covers.

The claim is **asserted** now rather than made. `test_ci_gate.py` reads the
workflow and holds it to this list in **both** directions: a `test_*.py` in
the repo that no step invokes, and a step naming a file that is not here —
which runs nothing at all. Several steps are deliberately written
`if [ -f x ]; then … else echo "not on this branch"`, so that second half
reads the guard rather than the filename, or it would report the thing that
keeps this workflow mergeable on an older branch. `EXEMPT` is the way out and
carries its reason, and it is **empty**, which is the only way this was worth
adding.

Two workflows briefly existed: `checks.yml` and a `ci.yml` written in parallel
on another branch, overlapping on `jscheck` and `linkcheck` and each carrying
steps the other lacked. They are folded into `checks.yml` — the union, not the
intersection: the four test files and the composed-app boot from one, and
`checktemplates`, `pagecheck --strict` and `integritycheck` from the other.
Two gates disagreeing about what "green" means is worse than either alone.

It runs against a real Postgres rather than SQLite because Sites Admin refuses
to start without one and serves the 503 fallback instead: on SQLite a whole
module drops out of every check that boots the app, and nothing says so.

**A setting that is right about a thing that has never happened.** smart1-hub
is configured `autoDeploy: yes`, `autoDeployTrigger: checksPass`, branch
`main`, and the checks it is waiting on pass — and Render has never once
deployed it by itself. Every deploy in its history, past the hundredth and
back to the week the service was created, is trigger `manual` or `api`, and
**no service in the workspace has a single `new_commit` in its history**. So
what is missing is the webhook rather than the setting, which is why reading
the service config says nothing is wrong: each screen is internally
consistent, and the one number that shows it is a column nobody scrolls to.
The stored repo path is the pre-transfer one (`smart1marketing/smarthub`,
where the repo now lives under the `Smart-1-Marketing` org), and git follows
that redirect happily — so a manual deploy builds the right code and only the
event subscription is absent. Reconnecting the repository under the org, with
Render's GitHub App installed there, is the fix at that end and is the only
half of this that cannot be done from the repo.

The half that can is the `deploy` job in `checks.yml`, which makes the same
promise on the side that demonstrably works: the commit whose checks just went
green is the commit that ships. It is the workflow's one exception to *no
secrets*, and it is a separate job for exactly that reason — it never runs on
a pull request, so a fork's run and a contributor's branch still have no
credential and no path to production.

Three rules on it. It deploys **`ref=<sha>` and never a bare hook**: main takes
a merge every few minutes here, so "check main is green, then trigger a
deploy" is not atomic, and a deploy triggered that way has already picked up a
commit that landed in the intervening seconds — naming the sha ships what was
tested. A **missing secret is a refusal**, not a skip, because a green tick
over a deploy that did not happen is the confident wrong answer this file
spends its length undoing. And **the hook URL is never echoed**: the whole URL
is the credential, anyone holding it can deploy, and the `services/provider_check.py`
rule about never carrying a key into something a person reads applies to a CI
log as much as to a page.

`test_ci_gate.py` asserts all of it. Its first draft could not fail on the
refusal: the window it searched for an `exit 1` after the guard was wide enough
to reach the *other* `exit 1` further down the step, so a branch changed to
echo and carry on still passed — the assertion that cannot fail, in the file
written about checks that cannot fail. It is scoped to the branch now, and all
five were confirmed red against the defect each guards.

`tools/linkcheck.py` boots the composed app and checks every internal URL
literal against the route table of whichever app owns that path — and every
`url_for('name')` against the endpoints of whichever app renders that template, so it catches
the mount trap above — a module page written as `fetch("/api/lead")` works
standalone and 404s under a mount. It exits non-zero, so it can gate a
release. **Run it after touching any module template**: that one bug was live
on seven landing pages for two days, and it took down the lead capture on all
of them without anything looking wrong.

`tools/pagecheck.py` asks a different question: not what the template says,
but what the browser receives *after* `HubBar` and the hub's `after_request`
have rewritten the response. Both inject the sidebar and five script tags into
HTML they did not write, and injecting into the wrong place breaks the page
while leaving every template valid and every link resolving. That is not
hypothetical — HubBar injected at the FIRST `</body>` in the response, and the
IO Builder builds two printable documents as JavaScript template literals that
each carry their own `</body>`, so the sidebar landed inside a string, closed
the page's script early, and the entire tool rendered blank. It checks that
the chrome arrives as an *element* (`html.parser` goes raw-text inside
`<script>` exactly as a browser does, so chrome hidden in a literal is not
seen) and that every browser-delimited script block still parses.

`tools/jscheck.py` and `tools/checktemplates.py` split the JavaScript between
them. jscheck hands every file and every inline block to `node --check`, the
real parser, but *skips* blocks containing `{% %}` or `{{ }}` because Jinja is
not JavaScript and Node would reject it for the wrong reason. checktemplates
is what checks those: it blanks the Jinja to same-width filler, so line numbers
still line up, and runs a bracket/string/template balance check over what is
left. Neither is redundant — jscheck is stricter on what it can read, and
checktemplates is the only thing that reads the rest.

`tools/integritycheck.py` runs `/api/integrity` from the command line and
fails on `high` findings. It is at **zero** — the six `medium`/`low` findings
that used to stand every run are cleared, and `provider_key_drift` is `high`
now rather than `medium`, as the note that sat beside it asked for once its
list was empty. A check that starts life red is a check somebody switches off;
one that has been green is one a new finding actually interrupts.

Then boot through `wsgi.application` (not just the hub app — that's how mount
shadowing hides) and request the pages you touched. `/api/integrity` reports
known defect patterns; `/login/health` diagnoses sign-in without a session.

## Delivery

`git push` from the sandbox has always been blocked, so releases have gone out
as zips uploaded through GitHub's browser UI. **That uploader adds and
overwrites but never deletes**, which is why the repo root accumulated 65
stray files. If you can push directly, do — it removes the whole class of
problem.
