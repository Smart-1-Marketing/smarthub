# Weather trigger setup — lead → landing pages → approved work order

**Date:** 2026-09-07 · **Vertical for v1:** restaurant · **Status:** built (v1)

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
scheduler and the reporting line alike. Fourteen triggers ship for
restaurant v1, exactly as specified, each carrying its condition as data
(`Trigger.rule`) rather than as a function nobody else can read.

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
`test_weather_setup.py`): the trigger vocabulary and evaluator, the
server-side cap, the campaign store, the copy guardrails, the four-source
image picker with copy-on-select, the public wizard end to end over HTTP,
approval's three side effects, work order numbering, and a scheduler job
(`hub/scheduler.job_weather_triggers`, ticking every 30 minutes like
`job_smartforecast_weather`) that evaluates every approved campaign's picks
against a live snapshot and records whether each is active right now.

**Open, and named rather than silently assumed:**

- **The Suite confirmation workflow.** Nobody has built it in Smart 1
  Suite yet (only Todd can), so the confirmation screen deliberately makes
  no promise about an email arriving, per `hub/lead_tags.py`'s own rule.
- **A shared trafficking queue.** See §5 above.
- **Real ad platform placement.** This tool ends at an approved work order
  and a scheduler that knows which triggers are live right now; it does not
  itself push spend to Google or Meta. That connection is a distinct piece
  of work for whoever picks up the work order.
- **A second vertical (HVAC).** Everything except the trigger registry and
  the copy prompts is vertical-agnostic already (`Trigger.vertical` is a
  field, `triggers_for_vertical()` filters on it), so adding HVAC is
  writing its own set of `Trigger` rows rather than new plumbing.
