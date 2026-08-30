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

**Bubbles mount on late-rendered content.** Client 360, the SEO client page
and Image Creator draw panels from a fetch. `hub-help.js` runs a debounced
MutationObserver for this. A bubble added to a JS-rendered panel works; one
added before that observer existed did not.

**`audit.log()`'s first positional is `module`.** Passing `module=` in the
extras raises `TypeError` and silently zeroes cost tracking. Use `tool=`.

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
rather than when a feature turns out to need them. `test_ghl_scopes.py` asserts
that every GHL write call site in the Hub has a scope declared for it.

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

**A filtered list that reports an unfiltered total is a wrong answer with
two right ones either side of it.** `/tools/seo-images/api/gallery` filtered
its rows by client and then returned `len(load_archive())` as the total, so
Client 360 — which prints "Showing N of total" — said **"Showing 1 of 7 saved
images"** about a client with exactly one, and the gallery that sentence
linked to then showed the one. Neither screen was wrong; the sentence joining
them was, which is harder to notice than either being wrong. `total` is now
the total of what was *asked for* and the archive-wide figure is carried
beside it under its own name.

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
has watched — so a note on the first applies to two cuts already paid for. The
render route takes one format and refuses more by name. And `check_render`
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
one illustration, and they are **labelled as one**: no music library is
connected, so a picture that reads as a waveform of a chosen track would be
claiming a track exists. Each casting tile also prints the words it will
actually match on (`characteristics_detail()`), because "Announcer" is not a
mood — it is a search for *announcer, commercial, broadcast, promo* in the text
ElevenLabs publishes, and a screen that says so lets somebody pick differently
before listening to three wrong voices.

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

**Deleting a mirrored file needs `jsonstore.delete_json`, not `os.remove`.**
Removing only the file leaves the database copy to be restored by the next
read, so the delete appears to work and then undoes itself. This is the one
way the backup can bite you.

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
| Knack campaigns / websites | static JSON in `clients_app/data/` | **stale — nothing refreshes these** |
| Knack object_153 (website registry) | live API, `hub/knack_websites.py` | current |
| Knack tickets | live API, `hub/knack_api.py` | current |
| Insites scans | own SQLite/Postgres tables | current |
| GoHighLevel | live API | current |

**The static JSON exports are the biggest known problem.** Products are now
read live: `hub/knack_data.search_client()` prefers `hub.knack_products`
(object_135) and falls back to the export, and Client 360 labels which source
it used — before that, a client's insertion orders showed the last export's
line-up while the Knack pull reported success, because the two are different
sources and only one was live. **Campaigns and websites still come from the
export**, so the same trap remains for them; both need their Knack object IDs.

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

`test_image_download.py` asserts all of it, including that the image picker
still returns a zip now that it runs on the shared builder.

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
empty, confident table. The committed export carries none of these fields at
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

**And a panel redrawn under a callback is a callback writing to nothing.**
`drawControls()` replaces the whole left column, so an element captured before
a fetch is detached by the time the answer arrives: the write succeeds, the
screen does not change, and it reads as a button that did nothing. Re-read the
node after any redraw.

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
python3 test_report_cache.py       # one run per report per day; a failed run is never
                                   #   the answer, and a write drops what it changed
python3 test_ads_module.py         # Smart 1 Ads: the Ads Editor handoff, the client join
python3 test_ads_estimate.py       # the estimate a client reads, and what they can answer
python3 test_ads_explainer.py      # the bubbles, the per-screen tour, the walkthroughs
python3 test_target_areas.py       # target areas, delivery, the Suite push
python3 test_lead_delivery.py      # one write path per lead
python3 test_scan_widgets.py       # widget placements: leads counted, pause/edit/delete
python3 test_website_audit.py      # the spend block that leads the audit, the customer
                                   #   placement, the lead every scan files, merging two
                                   #   rows that are one prospect
python3 test_detail_ui.py          # one description of the record-page look, and the
                                   #   three module screens that read from it
python3 test_menu_layout.py        # the three index pages: every tool tiled once and
                                   #   only once, and the internal calculator that
                                   #   computes the same plan and captures nothing
python3 test_proposal_share.py     # the client's copy: who opened it, how often,
                                   #   and an acceptance tied to one revision
python3 test_proposal_targeting.py # the coverage map, the pasted location list,
                                   #   the competitor research, and a bulleted
                                   #   list that reaches the client as a list
python3 test_proposal_spec.py      # the 13-part spec, the creative gate, ROI math,
                                   #   the 2x quoted rate, the product a goal leads
                                   #   with, ZIP exceptions and what the Suite covers
python3 test_landing_maker.py      # built pages stay public and chrome-free
python3 test_quote_numbers.py      # uploaded quotes are numbered, drafts delete
python3 test_api_usage.py          # the Google/ElevenLabs/Cloudinary estimates
python3 test_social_plan.py        # the post mix, the copy checks, the CSV
python3 test_social_content.py     # multi-location requests, the client's four
                                   #   signed pages, the idea weighting, and a
                                   #   push failure that never reads as scheduled
python3 test_web_tickets.py        # the object_107 ids, the form, what a write carries
python3 test_ad_copy.py            # the ad copy object, discovered not guessed;
                                   #   one candidate or none, nothing invented
python3 test_campaign_support.py   # the object_121 ids, every option off the live
                                   #   object, and what a write may not contain
python3 test_campaign_assets.py    # campaigns waiting on an asset, by media partner
python3 test_stale_creative.py     # the row actions, the evergreen overlay, the login gate
python3 test_dashboard_trends.py   # the monthly readings accumulate; no card claims a comparison
python3 test_celebrations.py       # birthdays and anniversaries: what is still to come, and who is interrupted
python3 test_housekeeping.py       # warnings moved off pages nobody can act on, with the page named
python3 test_blog_publish.py       # blog taxonomy, approved topics, the CMS panels
python3 test_image_download.py     # image downloads, the shared zip builder
python3 test_client_images.py      # deleting a client image, the count, the one brand
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
python3 test_msa_embed.py          # the signing page: public, chrome-free, ours to frame
python3 test_landing_embeds.py     # the gameplan embeds: framable by us, leads land
python3 test_commercial_heygen.py  # the spokesperson clip actually arrives
python3 test_commercial_providers.py # a key that was added is read, and works
python3 test_commercial_review.py  # the client's review link: public and chrome-free,
                                   #   three answers, the strictest one wins, and a
                                   #   refusal that stops a delivery
python3 test_commercial_wizard.py  # the seven steps, the client join, the spec check,
                                   #   the QR destination and who owns the scan; the :06,
                                   #   shots inside beats with their grammar, the published
                                   #   thresholds and whose each is, and the Amazon warning
python3 test_io_start.py           # starting an IO from a proposal, a client or a file
python3 test_landing_spec.py       # what a landing page is for, and what it sells
python3 test_client_groups.py      # grouped clients: what merges, what must not double
python3 test_ghl_scopes.py         # the Suite app's scopes, and the granted-vs-requested diff
python3 test_suite_embed.py        # Hub pages framed in Suite: the cookie, the chrome, who may frame
python3 test_calculator_embed.py   # the media calculators framed on smart1marketing.com
python3 test_display_ads.py        # the display layouts, and the build screen's contracts
python3 test_user_accounts.py      # the roster, the two levels, the crawler block, the throttle,
                                   #   and the signed-in headcount on the dashboard
python3 test_env_config.py         # one setting, every name it answers to, and who logs
python3 test_spelling.py           # the spelling check still bites, and its
                                   #   exemptions still name real files
python3 test_client_prefill.py     # one client reader: what a form is offered,
                                   #   what it is never offered, and what a
                                   #   model is told about the client
python3 test_client_logos.py       # a logo we found reaches the client's gallery,
                                   #   once, labeled with where it came from
python3 test_thinking.py           # the mark that says a scan or a model is running:
                                   #   one implementation, three kinds, both halves
                                   #   of the app, and nothing claiming a result
python3 test_search.py             # the top box: a client the query names comes
                                   #   first, and every screen is findable
python3 test_oauth_redirects.py    # every OAuth callback, and the hostname each is built from
```

The test files need no pytest and no new dependencies; each runs against a
temporary data directory and a throwaway SQLite database, so none of them
touches `/var/data` or the real one.

**All of this runs on every pull request** — `.github/workflows/checks.yml`,
the single gate. CI runs the same scripts a person runs, so a green run means
the same thing in both places and no check exists only where nobody can
reproduce it.

Two workflows briefly existed: `checks.yml` and a `ci.yml` written in parallel
on another branch, overlapping on `jscheck` and `linkcheck` and each carrying
steps the other lacked. They are folded into `checks.yml` — the union, not the
intersection: the four test files and the composed-app boot from one, and
`checktemplates`, `pagecheck --strict` and `integritycheck` from the other.
Two gates disagreeing about what "green" means is worse than either alone.

It runs against a real Postgres rather than SQLite because Sites Admin refuses
to start without one and serves the 503 fallback instead: on SQLite a whole
module drops out of every check that boots the app, and nothing says so.

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
