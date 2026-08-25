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

**A provider's asset URL is signed and expires.** A HeyGen clip linked
directly plays today and 404s next week. Finished clips are mirrored into
Cloudinary through `cloudinary_service.upload_asset`, the way rendered
commercials already were, and the storyboard says out loud when a mirror
failed and it is showing you a link that will die.

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
attaching there records in Knack and clears the orphan too.

### Domains we bought renew whether or not anyone bills them

`hub/domain_purchase.py` and `/tools/domains`. The record was always in Knack
and nothing read it, so the only way to know what renews next month was to open
object_153 and sort it by eye. Only records where `field_2964` says yes appear —
`is_ours()` reads a Knack boolean *and* a yes/no dropdown, because the field can
be published either way. `field_3298` sorts it, the current month and the next
three are laid out from the clock (a hard-coded window is right the month it is
written), and a row with no renewal billing date goes in its own group saying so
rather than sorting to the top as if it were overdue.

There is no billed field in Knack, so the tick is the Hub's — and it is kept
**against the renewal billing date it was ticked for**, not against the record.
A domain renews every year; a tick that stayed green when next year's date
arrived would be a confident wrong answer of exactly the kind this codebase
keeps having to undo.

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

## Social posts are drafted here and published in Suite

`modules/social_planner` (`/tools/social`) builds a client's month of organic
posts in one pass. `hub/social_plan.py` is the spec — channels, post types and
their mix, the calendar arithmetic, the copy checks and the CSV layout — read by
the module, the exporter and the AI prompt alike, the same way
`hub/proposal_spec.py` is.

**It stops at a CSV on purpose.** Social Planner's write API needs
`social-media-posting.write`, and `DEFAULT_SCOPES` in `hub/ghl_oauth.py` does
not request it; adding a scope requires re-consent at the agency, a one-time
manual step. Ending at the bulk-upload CSV means the drafting pipeline earns its
keep while that is pending, and works regardless of whether it ever lands. When
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
python tools/linkcheck.py          # every internal URL resolves
python tools/pagecheck.py          # the page the browser actually receives
python tools/integritycheck.py     # known defect patterns
python3 test_jsonstore.py          # the database mirror really restores
python3 test_ads_module.py         # the Node ad builder behind its proxy
python3 test_target_areas.py       # target areas, delivery, the Suite push
python3 test_lead_delivery.py      # one write path per lead
python3 test_proposal_spec.py      # the 13-part spec, the creative gate, ROI math
python3 test_landing_maker.py      # built pages stay public and chrome-free
python3 test_quote_numbers.py      # uploaded quotes are numbered, drafts delete
python3 test_api_usage.py          # the Google/ElevenLabs/Cloudinary estimates
python3 test_social_plan.py        # the post mix, the copy checks, the CSV
python3 test_web_tickets.py        # the object_107 ids, the form, what a write carries
python3 test_dashboard_trends.py   # the KPI comparisons accumulate and name their months
python3 test_blog_publish.py       # blog taxonomy, approved topics, the CMS panels
python3 test_image_download.py     # image downloads, the shared zip builder
python3 test_alt_text.py           # the alt-text scan, its clamps, the Claude prompts
python3 test_gpt_ads.py            # the 1:1 gate, the copy checks, the ad-ops ZIP
python3 test_video_library.py      # the footage index, its status row, the page's palette
python3 test_sites_match.py        # live-only matching, and finding a client's missing URL
python3 test_domain_links.py       # attaching a domain everywhere, orphans, renewals
python3 test_google_links.py       # orphaned GA4/GTM/Search Console accounts
python3 test_msa_embed.py          # the signing page: public, chrome-free, ours to frame
python3 test_commercial_heygen.py  # the spokesperson clip actually arrives
python3 test_commercial_providers.py # a key that was added is read, and works
python3 test_io_start.py           # starting an IO from a proposal, a client or a file
python3 test_landing_spec.py       # what a landing page is for, and what it sells
python3 test_client_groups.py      # grouped clients: what merges, what must not double
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
literal against the route table of whichever app owns that path, so it catches
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
fails on `high` findings. Two `medium` ones stand today (`ad_builder` and
`msa` never write to the activity log); it prints them every run rather than
failing on them, so switching it on did not start life red.

Then boot through `wsgi.application` (not just the hub app — that's how mount
shadowing hides) and request the pages you touched. `/api/integrity` reports
known defect patterns; `/login/health` diagnoses sign-in without a session.

## Delivery

`git push` from the sandbox has always been blocked, so releases have gone out
as zips uploaded through GitHub's browser UI. **That uploader adds and
overwrites but never deletes**, which is why the repo root accumulated 65
stray files. If you can push directly, do — it removes the whole class of
problem.
