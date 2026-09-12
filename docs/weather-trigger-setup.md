# Weather trigger setup — lead → landing pages → approved work order

**Date:** 2026-09-07 · **Verticals:** restaurant, hvac, retail, auto, landscaping, pool_spa · **Status:** built (v1 + hvac + retail + auto + landscaping + pool_spa)

This is the design spec the module in `modules/weather_setup/` and the
trigger vocabulary in `hub/weather_triggers.py` were built against. What
shipped and what did not is called out inline, because a spec that reads as
finished after only part of it is built is the confident-wrong-answer
failure this repo spends its own `CLAUDE.md` naming a hundred times over.

---

## 1. Where it starts and where it lives

A lead already arrives through `hub/leads.py` → Leads panel → Smart 1 Suite.
Today that's the end of the automated path; a rep picks it up by hand. This
adds one step: a rep opens `/tools/weather-setup/`, and either picks up a
recent lead or types a business name directly, then presses **Start weather
setup**, which creates a campaign row and an unguessable token
(`modules/weather_setup/store.create()`, `store.new_token()` —
`secrets.token_urlsafe(24)`, never a slug built from the client name and a
timestamp).

The client gets `https://smart1.agency/wx/<token>` — outside the internal
Hub, no login, the same public-route pattern the Scans widget uses for
`/scans/r/<token>`. It is a dedicated top-level mount (`/wx`) registered as
its own blueprint on the hub app with no `blueprint_guard` on it at all,
rather than nested under a longer internal mount, so the link stays short —
the same reasoning that gives the media calculators their `/c/<slug>` link.

One URL per client, permanent. Re-opening it after approval shows the
approved state and lets them start a change request (`api/request-change`)
— same link, no second link to find.

**What shipped differently from the original draft:** the spec's `?mode=guided`
query-string switch for a rep-initiated in-call session is recorded on the
campaign (`mode: "guided" | "self"`) but the wizard template does not yet
branch its copy on it — there is one code path and one set of copy, and the
guided/self distinction is available for the confirmation flow to use later
without needing a second template.

---

## 2. The trigger registry

`hub/weather_triggers.py` is the single source of truth — the same shape as
`hub/lead_tags.py` (constants, not free text), read by the picker, the
scheduler and the reporting line alike. Fourteen triggers shipped for
restaurant v1, exactly as specified, each carrying its condition as data
(`Trigger.rule`) rather than as a function nobody else can read. Thirteen
more shipped for the second vertical, HVAC — §7 below.

The month strip (`MONTHS`) orders the fourteen for browsing; **a chosen
trigger evaluates all year**, not only in the month it was picked in —
`month_order()` is display ordering, and `evaluate_trigger()` never
consults the calendar's month at all except for the two triggers whose own
rule explicitly names one (`warm-break`'s October–March window,
`first-cool-night`'s "not before August 15th").

**The cap is server-side.** `MAX_TRIGGERS = 3`, and `validate_picks()` is
called from `store.save_picks()` before anything is written — a route that
trusted the picker UI to have refused a fourth trigger would be one crafted
request away from breaking the budget rule the cap exists for.

**What "measured" means.** Not every condition is exactly answerable from a
single weather snapshot. Three approximations are named on the trigger's
own `approximated` field, shown on its card:

- `rain-delay`'s condition is written as an intensity (≥ 0.10 in/h); this
  Hub's weather provider (`modules/smartforecast/provider.py`, WeatherAPI)
  does not publish one, so chance of rain is read instead and named as the
  stand-in.
- `evening-cooldown`'s drop is measured against today's high rather than an
  hourly forecast, which the same provider does not carry.
- `gray-streak`'s cloud-cover streak needs a `cloud_percent` field the
  provider's current normalizer does not extract; a snapshot missing it
  answers **not measured**, never a guessed verdict either way.

A snapshot missing a field a rule needs always answers `active: None,
measured: False` — the same rule this codebase applies to a QC check with
nothing to look at, rather than a confident guess.

---

## 3. Data model

The spec asked for four tables — `wx_campaign`, `wx_pick`, `wx_asset`,
`wx_event`. They collapse into **one JSON record per campaign**
(`modules/weather_setup/store.py`, one file per token via `hub/jsonstore.py`)
rather than four SQL tables, matching this codebase's own convention for
exactly this access pattern (`hub/io_records.py`, `hub/drafts.py`): nothing
here is ever queried across campaigns except by token, so a table gains
nothing a nested document does not already give, and it is one more thing
this codebase would have to keep mirrored, migrated and locked under a
Postgres advisory lock at boot.

Every mutation goes through `jsonstore.update_json()` — a single
indivisible read-modify-write — never a bare read-then-write, which is
exactly the failure this codebase measured in `modules/radio_promo` before
that helper existed.

Every chosen image **is** copied into Cloudinary at selection time
(`modules/weather_setup/images.select()`), exactly as specified — a stock
provider rotates and deletes, and an approved ad whose image 404s three
months in is the failure that exists to prevent.

---

## 4. The five steps

Built as a single server-rendered wizard (`weather_setup_wizard.html`) whose
JavaScript drives the step transitions against the JSON API — the state
lives on the server, so the wizard resumes correctly wherever it was left
off (a picked-but-unworded trigger reopens on the wording step, an approved
campaign reopens on the confirmation).

**Step 0 — Start.** Business name and, where a ZIP was given at campaign
creation, the current reading resolved through `modules/smartforecast`'s
WeatherAPI-backed provider (real location name, not a fabricated station
id).

**Step 1 — Choose triggers.** The month-ordered card strip, capped at three,
refused server-side as well as disabled client-side.

**Step 2 — Wording.** Three drafts per trigger generated once
(`modules/weather_setup/copy.py`) via `hub.ai.chat()` where an OpenAI key is
configured, and cached on the campaign from then on — never regenerated on
reload. With no key, or on any model failure, a deterministic house-authored
draft per angle is used instead, and every draft — model or house — passes
through the same guardrails:

- **No unbacked promise.** A draft promising a follow-up message is checked
  against `hub/lead_tags.backed("weather_trigger_setup")`, which is `False`
  until a Suite workflow is built for that tag, so any such promise is
  replaced with a house draft that carries none.
- **No fabricated specifics.** A draft carrying a price, a discount or a
  "free"/"BOGO" offer is not silently rewritten — it is flagged
  (`needs_review`) so a rep confirms it before launch, per the spec.
- **Storms are not a promotion.** `storm-watch` copy is checked against a
  small blocklist of urgency/invitation language and replaced if it matches.

**Step 3 — Images.** All four sources are equal citizens, never a fallback
chain: stock (Pexels/Pixabay/Unsplash/our own library, fanned out in
parallel through the shared `hub/stock_search.py` — the same reader
`modules/stock_photos` uses, not a second copy of the fan-out), the client's
own gallery (`hub/client_context.gallery_images()`), a straight upload, and
an OpenAI-generated image via `hub.ai.image()`. "Show me more" re-runs the
same search with no cap on how many results come back.

**Step 4 — Review and approve.** Every trigger with its final copy and
image on one screen. Approving (`store.approve()`) requires a name, and:

1. freezes `ad_json` on every pick — nothing after approval may reword it
   silently; a client-initiated change re-opens as a new revision instead
   (`store.request_change()`), never an edit under an existing approval;
2. writes `audit.log("weather_trigger_setup", "approved", client=..., ...)`,
   so it reaches the client's Client 360 record (declared in
   `hub/client_brand.WORK_KINDS`);
3. cuts a work order number (`WO-#####`, a shared atomic counter in
   `jsonstore`, following `hub/io_records.py`'s own numbering shape rather
   than a second scheme);
4. calls `hub.leads.capture_and_deliver("weather_trigger_setup", ...)`,
   registered in `hub/lead_tags.SOURCES` with `workflow: None` — the honest
   answer today, because the confirmation email is a Suite workflow nobody
   has built yet, and the confirmation screen says nothing about an email
   arriving until one exists.

---

## 5. Approval → work order

**What did not ship as specified:** a shared `traffic_task` trafficking
queue. It does not exist anywhere in this codebase yet — grepping for
`traffic_task` or `WorkOrder` across the whole repo turns up nothing — so
"reuse the trafficking queue from the reporting/pacing spec" was not
something this change could do without inventing that queue itself, which
is out of scope for this change. The work order number is cut and recorded
on the campaign (`wx_campaign.work_order`) and in the activity log; wiring
it into a shared queue is future work, named here rather than silently
skipped.

---

## 6. What shipped vs. what is still open

**Shipped, real and tested** (`test_weather_triggers.py`,
`test_weather_setup.py`): the trigger vocabulary and evaluator for both
verticals, the server-side cap, the campaign store, the vertical-aware copy
guardrails, the four-source image picker with copy-on-select, the public
wizard end to end over HTTP, approval's three side effects, work order
numbering, and a scheduler job (`hub/scheduler.job_weather_triggers`,
ticking every 30 minutes like `job_smartforecast_weather`) that evaluates
every approved campaign's picks against a live snapshot and records
whether each is active right now.

**Open, and named rather than silently assumed:**

- **The Suite confirmation workflow.** Nobody has built it in Smart 1
  Suite yet (only Todd can), so the confirmation screen deliberately makes
  no promise about an email arriving, per `hub/lead_tags.py`'s own rule.
- **A shared trafficking queue.** See §5 above.
- **Real ad platform placement.** This tool ends at an approved work order
  and a scheduler that knows which triggers are live right now; it does not
  itself push spend to Google or Meta. That connection is a distinct piece
  of work for whoever picks up the work order.

---

## 7. The second vertical (HVAC)

Shipped. The claim two paragraphs above — "everything except the trigger
registry and the copy prompts is vertical-agnostic already" — held exactly:
no route, no store field, no evaluator rule and no wizard template needed
to change shape to add it.

**The registry.** Thirteen `Trigger` rows in `hub/weather_triggers.py`,
`vertical="hvac"`, built from the same rule vocabulary the restaurant
thirteen already exercise — `temp_min`/`temp_max`, `feels_like_max`,
`heat_index_min`, `snow_in_min`, `cloud_percent_min` with a
`consecutive_days` streak, `alert_required`, `once_per_season` with
`temp_low_max`, and `months`. Nothing in `evaluate_trigger()` changed. Six
are daily emergency/strain conditions escalating in a clear pair
(`ac-overload`/`heat-index-strain` for cooling, `hard-freeze`/`deep-freeze`
for heating, each pair naming which of the two is the tune-up ad and which
is the emergency one), two are the shoulder-season maintenance push the
whole vertical exists to sell (`spring-tune-up-day`, `fall-tune-up-day`),
one is the once-per-season "the furnace gets tested for real" event
(`first-hard-freeze` — the restaurant's `first-freeze` shape, its own id
and its own carried season-state key so the two can never collide inside
one campaign), and the rest cover wind chill, an early-season heat
surprise, a mild winter break, a severe-weather power risk and heavy snow
burying an outdoor unit.

`MONTHS` stayed one flat table rather than splitting per vertical:
`month_order()` already filters its ranked list down to
`triggers_for_vertical(vertical)`, so an hvac id sitting in the same
month's tuple as a restaurant id costs nothing — each vertical only ever
sees its own ids, in the relative order they were written for that month.
All thirteen hvac ids are listed in every month, exactly as the restaurant
thirteen are.

**The copy.** `modules/weather_setup/copy.py`'s house drafts and its model
prompt both branch on `Trigger.vertical` now — `_house_draft_restaurant()`
and `_house_draft_hvac()` are two separate templates per angle rather than
one generic template with the business name swapped in, because a
restaurant ad is an invitation ("come sit outside") and an HVAC ad is a
warning or a reminder ("book this before it fails"), and a shared template
answers a hard-freeze ad with something that reads as an invitation to eat
somewhere. The storm-copy blocklist — no jokes, no urgency language
inviting someone to be outside in a warned area — is checked by the
trigger's `cadence == "alert_driven"` now rather than by the literal id
`"storm-watch"`, because `storm-power-risk` carries the identical reasoning
and would otherwise have shipped unchecked.

**Starting a campaign.** `store.create()` already took a `vertical`
keyword and had since before this vertical existed; nothing called it with
anything but the default. `app.py`'s `api_start()` now reads `vertical`
from the request body and `weather_setup_staff.html` gained a `<select>`
for it, populated from `hub.weather_triggers.VERTICALS` /
`VERTICAL_LABELS` rather than a hand-typed pair of options — the same
reason `hub/qa_tasks.py`'s dropdown reads the Hub's own nav rather than
restating it. An unrecognized vertical, from either the form or a crafted
request, falls back to `"restaurant"` rather than creating a campaign that
can validate no picks against anything: `store.create()` and `api_start()`
both check against `VERTICALS` independently, because a route that trusted
the form to have sent a real value is a route one crafted request away
from a campaign nothing can be picked for.

The public wizard needed no changes at all — it was already driven
entirely by the server's catalog response, with no restaurant-specific
copy anywhere in its template.

## 8. The third vertical (Retail / Home Goods)

Shipped the same way, and the claim held a second time: thirteen more rows,
still the same rule vocabulary, still no change to `evaluate_trigger()`,
`store.py`, `app.py` or the staff template — all three already read
`VERTICALS` / `VERTICAL_LABELS` dynamically once HVAC added the plumbing, so
this vertical needed no code changes outside the registry and the copy.

**The registry.** Thirteen `Trigger` rows, `vertical="retail"`. The
psychology is neither an invitation nor a service reminder — it is a
purchase trigger, closer to a stock-up call than either of the first two.
`heat-wave-cooling`/`heat-index-retail` are the day a fan or a window AC
actually sells; `deep-freeze-retail`/`cold-snap-retail` are the same shape
for space heaters and warm layers. `first-frost-shop` is the once-per-season
event — patio-furniture covers and pipe insulation move before the freeze,
not after — with its own season-state key so it cannot collide with
restaurant's `first-freeze` or HVAC's `first-hard-freeze` inside one
campaign's carried state. `patio-season-open` and `fall-clearance-day` are
the two shoulder-season pushes the vertical leans on hardest. `storm-prep`
is the alert-driven row: a stock-up call for flashlights and batteries, not
an invitation to be outside in the alert — and it is caught by the same
`cadence == "alert_driven"` blocklist check HVAC's `storm-power-risk`
already exercises, with no per-id special-casing needed.

`MONTHS` gained the same treatment as HVAC's rows: all thirteen retail ids
listed in every month's tuple, in month-appropriate priority order,
alongside the restaurant and hvac ids already there. `month_order()`
filters to `triggers_for_vertical(vertical)`, so nothing about a shared flat
table costs a vertical anything.

**The copy.** `_house_draft_retail()` is a third per-angle template in
`modules/weather_setup/copy.py`, dispatched the same way as the restaurant
and hvac ones — by `Trigger.vertical`, not by a swapped-in name. Its
"Comfort" angle frames stock-up urgency for `stock-up`/`emergency`-tagged
triggers ("Stock up before it's gone") and a softer seasonal-browse note for
the rest ("New for your home"). `_PROMPT_CONTEXT["retail"]` gives the model
prompt a "retail / home goods store" noun and an "Inventory or promo notes"
label, and `_FALLBACK_NAME["retail"]` is "your store" where a restaurant
falls back to "your table" and HVAC to "your business".

**Starting a campaign.** No change needed: `VERTICALS`/`VERTICAL_LABELS`
already drive the dropdown and the fallback-to-`"restaurant"` guard in both
`store.create()` and `api_start()`.

## 9. The fourth vertical (Auto Repair / Service)

Shipped the same way a third time: thirteen more rows, still the same rule
vocabulary, still no change to `evaluate_trigger()`, `store.py`, `app.py` or
the staff template. Three verticals of precedent already proved the plumbing
generalizes; the fourth confirms it rather than testing it again.

**The registry.** Thirteen `Trigger` rows, `vertical="auto"`. The psychology
is closer to HVAC's service reminder than retail's stock-up call, because the
thing being sold is mostly a check-up rather than a purchase — but it is a
narrower, colder-weather-leaning one: a shop's business is disproportionately
battery, tires and AC. `battery-cold-test`/`deep-freeze-auto`/`cold-snap-auto`
are an escalating cold-weather ladder, each with its own threshold, so a
20°F day and a below-zero day do not compete for the same ad. `first-freeze-auto`
is the once-per-season event — the day a battery that coasted through fall
gets tested for real — with its own season-state key so it cannot collide
with restaurant's `first-freeze`, HVAC's `first-hard-freeze` or retail's
`first-frost-shop` inside one campaign's carried state, even though all four
share the literal `once_per_season: "cold"` value: `trigger_state` is stored
per-pick, keyed on `trigger_id`, in `store.py`, so the four never actually
share a state dict. `ac-check-early`/`heat-index-auto` are the summer half —
an early-season 85°F day or a genuinely humid one, the moment a cabin AC that
was low on refrigerant all winter gets caught out. `spring-service-day` and
`fall-service-day` are the two shoulder-season maintenance pushes, mirroring
each other in reverse. `wiper-blade-season`, `pothole-season` and
`snow-tire-day` are weather-specific parts triggers rather than temperature
bands. `storm-driving-prep` is the alert-driven row: a get-it-checked-before-
the-next-one call, never an invitation to be on the road during this one — and
it is caught by the same `cadence == "alert_driven"` blocklist check the other
three verticals' alert rows already exercise, with no per-id special-casing
needed.

`MONTHS` gained the same treatment as the prior two verticals: all thirteen
auto ids listed in every month's tuple, in month-appropriate priority order,
alongside the restaurant, hvac and retail ids already there. `month_order()`
filters to `triggers_for_vertical(vertical)`, so nothing about a shared flat
table costs a vertical anything.

**The copy.** `_house_draft_auto()` is a fourth per-angle template in
`modules/weather_setup/copy.py`, dispatched the same way as the other three —
by `Trigger.vertical`, not by a swapped-in name. Its urgency framing leans on
"before it fails" / "don't get stranded" for battery- and emergency-tagged
triggers, and a "get it checked" service-reminder note for the maintenance
and seasonal ones. `_PROMPT_CONTEXT["auto"]` gives the model prompt an
"auto repair / service shop" noun and a "Service notes" label, and
`_FALLBACK_NAME["auto"]` is "your shop" where retail falls back to "your
store" and HVAC to "your business".

**Starting a campaign.** No change needed: `VERTICALS`/`VERTICAL_LABELS`
already drive the dropdown and the fallback-to-`"restaurant"` guard in both
`store.create()` and `api_start()`.

## 10. The fifth vertical (Landscaping / Lawn Care)

Shipped the same way a fourth time: thirteen more rows, still the same rule
vocabulary, still no change to `evaluate_trigger()`, `store.py`, `app.py` or
the staff template.

**The registry.** Thirteen `Trigger` rows, `vertical="landscaping"`. The
psychology sits closer to HVAC's service reminder than retail's stock-up
call — the work is mostly a booked visit rather than a purchase.
`dry-spell-watering`/`drought-stress` are an escalating pair for irrigation,
the same shape as `hard-freeze`/`deep-freeze` for HVAC and
`battery-cold-test`/`deep-freeze-auto` for auto. `heavy-rain-growth-spurt`
sells the mowing catch-up the day *after* a soaking rain rather than the day
of it. `storm-cleanup` and `high-wind-debris` split debris cleanup into an
alert-driven row and a lesser daily one. `first-freeze-landscaping` is the
once-per-season event — winterize the irrigation before the freeze cracks
it — with its own season-state key so it cannot collide with any of the
other four verticals' first-freeze rows inside one campaign's carried
state, even though all five share the literal `once_per_season: "cold"`
value: `trigger_state` is stored per-pick, keyed on `trigger_id`, in
`store.py`, so none of them actually share a state dict. `spring-green-up-day`
and `fall-leaf-peak` are the two shoulder-season pushes this vertical leans
on hardest, and `spring-fertilize-window`/`fall-fertilize-window` are a
second such pair for feeding rather than cleanup. `first-snow-landscaping`
and `mosquito-surge` round the book out: a first snowfall for the plowing
side of the business, and a run of overcast days for the pest-control side,
the same `cloud_percent_min`/`consecutive_days` shape the restaurant
vertical's `gray-streak` already uses.

`MONTHS` gained the same treatment as the prior three verticals: all
thirteen landscaping ids listed in every month's tuple, in month-appropriate
priority order, alongside the restaurant, hvac, retail and auto ids already
there.

**The copy.** `_house_draft_landscaping()` is a fifth per-angle template in
`modules/weather_setup/copy.py`, dispatched the same way as the other
four — by `Trigger.vertical`, not by a swapped-in name. Its framing leans on
"get it checked before it's a bigger job" for urgent/alert-driven triggers
and "book it before the season gets away" for the seasonal maintenance
ones. `_PROMPT_CONTEXT["landscaping"]` gives the model prompt a
"landscaping / lawn care company" noun and a "Service notes" label, and
`_FALLBACK_NAME["landscaping"]` is "your business", matching HVAC's fallback
since the vertical has no single word as natural as "your table" or "your
shop".

**Starting a campaign.** No change needed: `VERTICALS`/`VERTICAL_LABELS`
already drive the dropdown and the fallback-to-`"restaurant"` guard in both
`store.create()` and `api_start()`.

## 11. The sixth vertical (Pool & Spa Service)

Shipped the same way a fifth time: thirteen more rows, still the same rule
vocabulary, still no change to `evaluate_trigger()`, `store.py`, `app.py` or
the staff template. Five verticals of precedent already proved the plumbing
generalizes; the sixth confirms it rather than testing it again.

**The registry.** Thirteen `Trigger` rows, `vertical="pool_spa"`. The
psychology is a service reminder like HVAC and landscaping, built around
chemical balance and seasonal opening/closing rather than an appliance
under strain. `chlorine-burn-off`/`algae-bloom-risk` are an escalating
pair — hot and sunny burns chlorine off fast, heat stacked on humidity is
the real algae risk — the same escalation shape as HVAC's
`ac-overload`/`heat-index-strain`. `first-freeze-pool`, `hard-freeze-pool`
and `deep-freeze-pool` are a three-step cold-weather ladder: the
once-per-season event that opens the season's winterizing conversation, a
daily row for every hard freeze after it (equipment left un-winterized is a
risk every time, not only the first), and an emergency row for genuinely
extreme cold. `pool-opening-day` and `pool-closing-day` are the two
shoulder-season pushes the vertical exists to sell, mirroring
`spring-tune-up-day`/`fall-tune-up-day` for HVAC. `spa-season-open` is the
one row that runs opposite the rest of the book on purpose — cold weather
is when hot tub demand actually surges, the exact inverse of what drives
every other row here. `storm-debris-cleanup` and `high-wind-debris-pool`
split cleanup the way landscaping's alert and daily rows do, and
`heavy-rain-dilution`/`evaporation-watch` are the two water-chemistry rows
for rain diluting chemicals and heat evaporating the water level, each
escalating past the ordinary `chlorine-burn-off` condition rather than
duplicating it. `first-freeze-pool`'s season-state key cannot collide with
any of the other five verticals' first-freeze rows inside one campaign's
carried state, for the same per-pick, per-`trigger_id` reason the other
five hold.

`MONTHS` gained the same treatment as the prior four verticals: all
thirteen pool_spa ids listed in every month's tuple, in month-appropriate
priority order, alongside the restaurant, hvac, retail, auto and
landscaping ids already there.

**The copy.** `_house_draft_pool_spa()` is a sixth per-angle template in
`modules/weather_setup/copy.py`, dispatched the same way as the other
five — by `Trigger.vertical`, not by a swapped-in name. Its framing leans on
"don't let it get out of balance" for urgent/alert-driven triggers and "get
ahead of it" for the ordinary seasonal ones. `_PROMPT_CONTEXT["pool_spa"]`
gives the model prompt a "pool & spa service company" noun and a "Service
notes" label, and `_FALLBACK_NAME["pool_spa"]` is "your business", the same
choice as landscaping's.

**Starting a campaign.** No change needed: `VERTICALS`/`VERTICAL_LABELS`
already drive the dropdown and the fallback-to-`"restaurant"` guard in both
`store.create()` and `api_start()`.
