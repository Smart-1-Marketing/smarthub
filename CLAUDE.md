# Smart 1 Hub

Internal tool suite for Smart 1 Marketing. Flask, deployed on Render via
Docker, ~22 modules mounted under one login.

**Live:** https://smart1-hub.onrender.com · **Repo:** `Smart-1-Marketing/smarthub`

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

**And a snapshot history cannot answer about a month before it existed.** That
fix was correct and, on its own, still showed dashes on every card: the first
reading is taken the month the Hub is opened, so last month had no bucket and
the same month last year would not arrive for a year. "Check back next year"
is the answer nobody can act on. But the export already carries the history —
every insertion order has a start date, an end date and a monthly rate, so
*which IOs were billing in a given month* is arithmetic, not a memory.
`knack_data.period_totals()` does it, and `_compare()` prefers a recorded
snapshot and falls back to that rebuild. Three rules hold it up. **It never
mixes bases**: the headline count includes every IO Knack still calls Live
whatever its dates say (`is_running` is deliberately a union), which is about
140 products wider than a term rebuild — so when a comparison is rebuilt,
*both* ends are rebuilt, the card marks it `≈`, and the tooltip prints the
pair. The percentage is shown and the absolute difference is not, because a
dollar figure there invites subtracting it from the headline, which is the one
arithmetic that does not work. **It is not a new definition of "live"**:
rebuilding the two months Knack flags itself (`thisM` / `lastM`) reproduces
both exactly, and `test_dashboard_trends.py` asserts that equality — it is
what makes the other months trustworthy. **A month with no rows is not a month
with nothing in it**; it is outside the book, and comes back not measured
rather than as a 100% collapse. Websites carry no dated history at all, so
`websites_active`, `hm_monthly` and the total that contains them stay not
measured and say which of the two reasons it is.

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

### Video and audio are asked about before they are priced

`hub/creative_needs.py`. A Connected TV or digital radio buy that reaches an
insertion order with no spot behind it is a launch date nobody can hit, so
those two mediums are gated: does the client have creative, and if not does
the client pay or does Smart 1 comp it. **A comp on a medium spending under
$1,500 across the flight gets one explicit confirmation, with the number
shown**, and that confirmation lapses if the budget is later cut below what
was confirmed. Display is not gated — six banner sizes is a $250 rate-card
line.

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
  work on Cloudinary's own registered apps; our own client id only changes
  whose name is on the consent screen. So a missing key reads as *not measured*
  on the admin page, never as a cross, and never hides the tab — and an **empty**
  key is never sent, because the widget takes `dropboxAppKey: ""` at its word
  and fails the tab against it.
- **Recording an upload asks whether the source is one of ours, not whether it
  is switched on now.** A source turned off between the widget opening and the
  file landing must not file a real Instagram upload as `local`, which is the
  one thing the gallery's source column exists to say.

The paragraph the client reads is **built from the live list**, because a
sentence naming Dropbox on a deployment where Dropbox is off is a promise the
panel cannot keep.

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
apart deliberately. `this_month()` is the calendar — past, today and still to
come, each labelled, because a month is not a queue and a birthday does not
stop having happened on the 8th. `today()` is the only thing allowed to
interrupt anybody: a popup about a birthday four days out teaches people to
close it unread, and then they close the one that mattered.

- **A date nobody recorded is named.** Somebody with no birthday on file drops
  out of the list, and a list that quietly shrinks reads as a quiet month —
  so `not_recorded` carries the count and the block prints it with a link to
  where it is fixed.
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

## Conventions

- **No new Python dependencies** unless genuinely unavoidable.
- Module layout: `modules/<name>/app.py` (Flask app or blueprint),
  `templates/`, mounted in `wsgi.py` with a try/except and `_fallback_app()`.
- New tools get a tile in `hub/templates/tools.html` under the right group.
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
python3 test_jsonstore.py          # the mirror restores, and one answer on who is outside it
python3 test_ads_module.py         # Smart 1 Ads: the Ads Editor handoff, the client join
python3 test_ads_estimate.py       # the estimate a client reads, and what they can answer
python3 test_ads_explainer.py      # the bubbles, the per-screen tour, the walkthroughs
python3 test_target_areas.py       # target areas, delivery, the Suite push
python3 test_lead_delivery.py      # one write path per lead
python3 test_scan_widgets.py       # widget placements: leads counted, pause/edit/delete
python3 test_proposal_spec.py      # the 13-part spec, the creative gate, ROI math
python3 test_landing_maker.py      # built pages stay public and chrome-free
python3 test_quote_numbers.py      # uploaded quotes are numbered, drafts delete
python3 test_api_usage.py          # the Google/ElevenLabs/Cloudinary estimates
python3 test_social_plan.py        # the post mix, the copy checks, the CSV
python3 test_web_tickets.py        # the object_107 ids, the form, what a write carries
python3 test_campaign_assets.py    # campaigns waiting on an asset, by media partner
python3 test_dashboard_trends.py   # the KPI comparisons accumulate and name their months
python3 test_celebrations.py       # birthdays and anniversaries: the month, and who is interrupted
python3 test_blog_publish.py       # blog taxonomy, approved topics, the CMS panels
python3 test_image_download.py     # image downloads, the shared zip builder
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
python3 test_io_start.py           # starting an IO from a proposal, a client or a file
python3 test_landing_spec.py       # what a landing page is for, and what it sells
python3 test_client_groups.py      # grouped clients: what merges, what must not double
python3 test_ghl_scopes.py         # the Suite app's scopes, and the granted-vs-requested diff
python3 test_suite_embed.py        # Hub pages framed in Suite: the cookie, the chrome, who may frame
python3 test_display_ads.py        # the display layouts, and the build screen's contracts
python3 test_user_accounts.py      # the roster, the two levels, the crawler block, the throttle,
                                   #   and the signed-in headcount on the dashboard
python3 test_env_config.py         # one setting, every name it answers to, and who logs
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
