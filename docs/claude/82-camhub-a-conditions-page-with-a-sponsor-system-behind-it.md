# CamHub: a conditions page with a sponsor system behind it

`modules/camhub/`, built from `docs/camhub-spec.md` (the "Lake Cam 2.0 —
Design & Build Spec", exported from the Claude Doc on 2026-09-17). Buckeye
Lake Winery is the first page. Read the spec before changing the module: it
carries the reasoning this file only points at.

## What is built, and what is not yet

Sprints 1 and 2 of the spec's delivery plan:

- **Adapters** under `adapters/` — `nws` (grid, forecast, alerts, station),
  `usgs`, `beachguard`, `ndbc`, `coops`, `astro` (computed), `scrape`. Each
  exposes `probe(lat, lon, location_type)` and `fetch(config)`. NDBC and
  CO-OPS answer "not found" at Buckeye Lake and exist for the coastal client.
- **The cache and the health screen** — `camhub_sources` holds each feed's
  cadence, tolerance, last success and last error; `camhub_conditions_cache`
  holds the last *good* payload. A failed fetch records the error on the
  source and leaves the payload alone. `/tools/camhub/` lists every feed.
- **The refresh job** — `camhub_refresh` in `hub/scheduler.py`, every five
  minutes, pulling only what each source's own cadence says is due.
- **The page** — `/tools/camhub/cam/<slug>`, server-rendered: the tile
  strip, the verdict, the forecast, the advisory bars, the prose, JSON-LD,
  the temperature in the meta description. `data.json` beside it.
- **The Buckeye Lake seed** — `seeds.py` is the exact configuration the
  spec resolved. Provision it from the staff index with one button.

Sprint 3, the sponsor system, in `sponsors.py`:

- **Two tables**, `camhub_sponsors` (the advertiser as a business, with
  the category exclusivity is judged on) and `camhub_placements` (that
  business in one position on one page for one flight, with its creative).
  Separating them is what lets a sponsor hold the presenting slot in
  summer and a tile in winter without re-entering creative.
- **The presenting slot is one placement at a time.** Two active
  presenting flights that overlap are a `ValueError` at save, never a
  silent overwrite. A category clash is a warning on the form, and a
  person decides.
- **Status is what a person set** (draft, active, paused); **what the page
  shows is derived** from the flight dates on read (scheduled, live,
  ended). A flight that ends reverts its slot to house without anybody
  remembering to swap it.
- **Supporting slots fill by weight, shuffled per page load**
  (`_weighted_shuffle`, a draw without replacement), house placements in
  sort order for the rest, and the served position rides on every slot as
  `data-placement` / `data-position` for Sprint 4's impression log.
- **Five house placements** are created at provision from the page's
  config (`ensure_house_placements`); a page without them falls back to
  the config itself.
- **Copy is capped at the tile** (`LIMITS`), server-side and on the
  form's counters, because copy that overflows is the most common way an
  ad swap goes wrong.
- **The editor's live preview is the real page.** `/cam/<slug>?preview=`
  carries a signed, hour-long token (`hub/signing.py`, salt
  `camhub-preview`) naming the page, the position and the placement, plus
  the unsaved draft as urlsafe-base64 JSON; only copy fields are honored,
  so a draft cannot flip a slot to sold or move it. A preview answers
  `noindex` and `no-store`. An unsaved new placement previews too.
- **Creative uploads** go through `hub/images.optimise` (logos to PNG at
  640, images to WebP at 1600) and `hub/storage.put` under the `camhub`
  kind, per client, never to the disk.
- **Six treatments** on the presenting bar, per placement: `lower_third`
  (the broadcast card over the cam), `wipe`, `crossfade`, `shimmer`,
  `tagline`, `static`. All pure CSS in `cam.html`, every one off under
  `prefers-reduced-motion`; the feature image drifts (the spec's option F)
  only when the slot is sold. A sold link carries `rel="nofollow
  sponsored"`; a house link does not, because it is the client's own.

Sprint 4, tracking, in `tracking.py`:

- **A viewable impression is what the page's own script reports** after an
  `IntersectionObserver` has seen at least half of the unit for one
  continuous second (the IAB display standard), once per placement per
  pageview, never re-counted on scroll-back. The script batches
  everything and sends one payload with `sendBeacon` when the page is
  hidden, plus a first flush after five seconds, so a popular page is one
  request per visit. House ads carry no `data-placement` and are never
  units. A preview renders no script at all.
- **Every sold link goes through `/go/<placement_id>`**, which records the
  click and answers a 302; the destination never appears in the page. A
  second click within two seconds from the same session is a double click,
  kept and marked. A house link never goes through it: it is the client's
  own site.
- **Filtering never deletes.** A crawler user agent (`CRAWLER_RE` plus the
  names in `hub/no_crawl.py`), a click before the page could have been
  read, a click with no scroll on a tile below the fold, a session past
  sixty events a minute or an address past thirty pageviews a minute:
  each row is written with `filtered` and the reason, and the rollup
  leaves it out. A sponsor who asks what was excluded can be shown.
- **The address is hashed with the Hub secret and truncated** to 24
  characters; the page's random session token is hashed the same way.
  Nothing in the table can give a visitor's IP back.
- **The rollup** (`camhub_rollup`, hourly in `hub/scheduler.py`) writes
  one `camhub_daily_stats` row per placement per day and one per page,
  from unfiltered rows only, for today and yesterday, then purges raw
  events past ninety days. It is idempotent. `stats()` reads the rollup
  and never the raw table: impressions, clicks, CTR, unique sessions and
  the share of pageviews the unit was viewable on, the number that makes
  the presenting slot obviously worth more than a tile.

Not yet: reports and the Client 360 card (Sprint 5), the Cam Builder
screens (6). `builder.py` holds the geocode and
concurrent-probe library the screens will use; `/tools/camhub/api/probe`
exposes it to staff.

## Where this departs from the spec, and why

**The refresh is a scheduler job, not Render cron entries.** The spec wants
cron so a restart cannot silently stop the refresh. This repo runs every
background job in `hub/scheduler.py` under the leader lock (`docs/claude/03`:
two gunicorn workers), with a Diagnostics panel showing each job's last run,
failure streak and a Run now button. A second scheduling mechanism for one
module would be a second place to look when the page goes stale. One job,
one tick, each source on its own cadence.

**Tables are prefixed `camhub_`**, not the spec's `cam_pages` /
`conditions_cache`. `docs/claude/57` records what unprefixed names cost
SmartForecast. A scrape target is a source row with adapter `scrape` rather
than its own table: it has a cadence, a last success and a last error like
every other feed, which is the spec's own point about it.

**"Above normal pool" is held back.** The spec asks for one phone call to
the Buckeye Lake State Park office to confirm ODNR's pool table shares the
USGS gauge's NGVD29 datum. Until `config.pool_datum_confirmed` is true the
tile shows the raw elevation and names the normal pool beside it. The
verdict does the same.

**Nothing Buckeye-specific in code** is a test, not a convention:
`test_camhub.py` reads every module file as an AST and fails if the gauge
id or the lake's name appears outside `seeds.py`.

## The cam page is public and indexable, on purpose

`hub/no_crawl.py` stamps `noindex` on every response that does not set its
own `X-Robots-Tag`. The cam page sets `index, follow`, because a conditions
page exists to be indexed and the spec's SEO plan depends on it. It is
meant to be reached on the client's own domain by a reverse proxy to
`/tools/camhub/cam/<slug>` (the spec's first serving option); the Hub's own
robots.txt still refuses the hostname the Hub answers on, so the copy at
`smart1.agency` is not what a crawler indexes. `config.canonical_url` is the
client-domain address and feeds the canonical link, Open Graph and the
BreadcrumbList. The page carries no Hub chrome: `_mount()` hands `/cam/` to
both AuthGuard and HubBar.

## Three things the page will not do until somebody supplies them

Named on the staff page under "Still to fill in", per page:

1. **The cam stream.** The current YouTube stream has embedding disabled by
   its owner. The seed's `cam_embed_url` is blank and the page shows an
   "Offline" placeholder. Nothing else in the module matters until a stream
   that allows embedding is set.
2. **The canonical URL and the business website.** Blank in the seed; the
   house ads render without buttons rather than pointing at a guess.
3. **The pool datum**, above.

## The scrape target serves its seed

The ODNR winter-drawdown page's URL and selector are not confirmed; the
source is provisioned with `url: ""` and the spec's numbers (891.6 / 888.6
ft) as its `seed`, stamped with the seed date. `scrape.fetch` serves the seed
and says so in the payload. Once a URL and rules are set on the source, it
fetches annually, refuses a robots.txt disallow, and holds the last good
value if the number moves more than `max_change`.

## Where the checks are

`python3 test_camhub.py` — every provider answer is a recorded shape
(the sandbox this was written in could not reach any of the hosts), the
collapse rule, the datum gate, the failed-fetch-keeps-payload rule, the
public/guarded split through the composed app, and the job registration;
then the sponsor system: the one-at-a-time presenting slot, the category
warning, the caps, the weighted shuffle measured over 300 draws, the
flight that ends itself, the signed preview, the editor round trip, and
the upload through the shared pipeline; then tracking: a batch written
with no address in it, once per unit per visit, crawlers and instant
clicks kept with a reason, the redirect and the double click, the rollup
reading only unfiltered rows, the ninety-day purge.
`test_blueprint_guards.py` carries `/go/<id>` and the events endpoint as
public with their reasons.
`test_blueprint_guards.py` carries the two public routes with their reason.
`tools/integritycheck.py` needs `camhub` in `hub/client_brand.WORK_KINDS`,
which it has, so a refresh logged with `client=` reads on Client 360.

## Render environment

**CAMHUB_USER_AGENT** — optional. The identifying User-Agent every keyless
feed wants (NWS throttles a call without one). Unset, the code sends
`SmartHub-CamModule/1.0 (adops@smart1marketing.com)`; set it to name a
mailbox somebody reads. Declared in `render.yaml`; nothing else is needed.
Every feed is free with no account: NWS, USGS, Ohio BeachGuard, NOAA NDBC and
CO-OPS, the Census geocoder. AirNow (air quality) would need a free key and
is deferred, as the spec says.
