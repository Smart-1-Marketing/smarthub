"""The weather trigger vocabulary — one table, read by the picker, the
scheduler and the reporting line alike.

## Why this exists

A weather-triggered ad is only as trustworthy as the condition behind it, and
that condition has to mean the same thing everywhere it is read: on the
client's picker card, in the scheduler job that turns the campaign on and
off, and in the report that later says "here is the weather that filled your
tables." Three readers of one fact drift the day any of them keeps its own
copy — the failure this codebase has paid for a dozen times over (the rate
card, the target-area sizing table, the KPI benchmarks). So this is the one
place a trigger's condition is written down, and everything else imports it.

## The cap is server-side

Below roughly the restaurant rate card's mid rung, a fourth trigger splits
the daily budget past the point where any one condition can win its auction
on the day it fires. `MAX_TRIGGERS` is enforced in `validate_picks()`, which
is also called from the write route — a rule the picker UI merely disables a
button for is not a rule, it is a suggestion nobody has broken yet.

## What "measured" means here

Not every condition below is exactly answerable from a single weather
snapshot. `gray-streak`'s cloud-cover streak and `first-freeze` /
`first-cool-night`'s "first time this season" both need state carried across
readings, not just today's numbers — that state lives on the campaign record
and is threaded through `evaluate_trigger()` rather than reinvented per call.
And a snapshot that does not carry a field a rule needs (a provider that
publishes no cloud cover, say) answers **not measured** for that trigger
rather than a guessed True or False — the same rule this codebase applies to
a QC check with no pixels to look at.

`rain-delay`'s condition is written in the spec as an intensity ("precip >=
0.10 in/h"), which the WeatherAPI-backed provider this Hub already has
(`modules/smartforecast/provider.py`) does not publish. Rather than invent a
number nobody measured, that trigger reads the closest thing the provider
does publish — chance of rain — and says so on the card, so the
approximation is a decision on screen rather than a silent substitution.

## The second vertical

HVAC is the second vertical, and it needed no new plumbing —
`Trigger.vertical` was already a field, `triggers_for_vertical()` already
filters on it, and `evaluate_trigger()`'s rule vocabulary (`temp_min`,
`temp_max`, `feels_like_max`, `heat_index_min`, `snow_in_min`,
`cloud_percent_min`, `alert_required`, `once_per_season`, `months`) already
covered every condition an HVAC book actually needs. Nothing here reads
`humidity` on its own or a plain overnight low outside `once_per_season`,
so the thirteen HVAC rows below were written to fit the vocabulary that
already exists rather than growing it — the same discipline `rain-delay`
and `evening-cooldown` already impose on the restaurant thirteen.

The psychology is different from a restaurant's and the rows say so: a
restaurant ad is an invitation ("come sit outside"), an HVAC ad is a
warning or a reminder ("book this before it fails"). `ac-overload` and
`heat-index-strain` are the emergency-repair pair for a cooling system
under strain; `hard-freeze` and `deep-freeze` are the same pair for
heating, escalating rather than duplicating one another; `first-hard-freeze`
is the once-per-season "the furnace gets tested for real" event, the exact
shape of the restaurant's `first-freeze` with its own id and its own
season-state key so the two never collide inside one campaign's carried
state. `spring-tune-up-day` and `fall-tune-up-day` are the two the whole
vertical exists to sell: a maintenance appointment booked in a comfortable
shoulder-season week is cheaper for everyone than an emergency call in the
week that follows it, which is why each names the season it is *for* in its
own `reason` rather than leaving a rep to infer it from the month strip.

`MONTHS` stays one flat table rather than becoming one nested by vertical
— `month_order()` already filters its ranked list down to
`triggers_for_vertical(vertical)` before returning, so a restaurant id
sitting in the same month tuple as an HVAC id costs nothing: each vertical
only ever sees the ids that belong to it, in the relative order they were
written in for that month. Splitting the table in two would be a second
shape doing the one job this filtering step already does.

## The third vertical

Retail / Home Goods, and the same claim held a second time: thirteen more
rows, still the same rule vocabulary, still no change to
`evaluate_trigger()`. The psychology here is neither an invitation nor a
service reminder — it is a purchase trigger, closer to a stock-up call than
either of the first two. `heat-wave-cooling` and `heat-index-retail` are the
day a fan or a window AC actually sells rather than being researched for
later; `deep-freeze-retail` and `cold-snap-retail` are the same shape for
space heaters and warm layers, escalating exactly the way `hard-freeze` and
`deep-freeze` do for HVAC. `first-frost-shop` is the once-per-season event —
patio-furniture covers and pipe insulation move before the freeze, not
after — with its own season-state key so it cannot collide with
restaurant's `first-freeze` or HVAC's `first-hard-freeze` inside one
campaign's carried state. `patio-season-open` and `fall-clearance-day` are
the two shoulder-season pushes the vertical leans on hardest, named for the
season they are *for* rather than left for a rep to infer from the month
strip. `storm-prep` is the alert-driven row, and its own `reason` says what
keeps it from reading as fear-mongering: it is a stock-up call for
flashlights and batteries, not an invitation to be outside in the alert.

## The fourth vertical

Auto Repair / Service, and the claim held a third time: thirteen more rows,
still the same rule vocabulary, still no change to `evaluate_trigger()`. The
psychology sits between HVAC's warning and retail's stock-up call — a car
does not fail on a comfortable day, so nearly every row here names the
specific system a weather condition puts under strain and the appointment
that heads it off. `battery-cold-test` and `deep-freeze-auto` are the
cold-weather pair: a battery that starts fine at 40°F fails at 10°F, so the
first is the test-it-before-it-strands-you ad and the second is the
emergency one, escalating the way `hard-freeze`/`deep-freeze` do for HVAC.
`ac-check-early` and `heat-index-auto` are the same shape for a cabin AC and
a cooling system under real load. `first-freeze-auto` is the once-per-season
event — a coolant and battery check before the first hard freeze finds a
weak one, with its own season-state key so it cannot collide with
restaurant's `first-freeze`, HVAC's `first-hard-freeze` or retail's
`first-frost-shop` inside one campaign's carried state. `pothole-season` and
`wiper-blade-season` are the two shoulder-season pushes this vertical leans
on hardest, named for the season they are *for* rather than left for a rep
to infer from the month strip. `storm-driving-prep` is the alert-driven
row, and its own `reason` says what keeps it from reading as an invitation
to be on the road in the alert: it is a get-your-wipers-and-tires-checked-
before-the-next-one ad, not a call to drive through this one.

## The fifth vertical

Landscaping / Lawn Care, and the claim held a fourth time: thirteen more
rows, still the same rule vocabulary, still no change to
`evaluate_trigger()`. The psychology sits closer to HVAC's service reminder
than retail's stock-up call — the work is mostly a booked visit rather than
a purchase. `dry-spell-watering` and `drought-stress` are an escalating
pair for irrigation, the way `hard-freeze`/`deep-freeze` escalate for HVAC.
`heavy-rain-growth-spurt` sells the mowing catch-up the day *after* a
soaking rain, not the day of it. `storm-cleanup` and `high-wind-debris`
split the same debris-cleanup need into an alert-driven row and a lesser
daily one, the way auto's `storm-driving-prep` and `cold-snap-auto` split
cold-weather risk into an alert and a threshold. `first-freeze-landscaping`
is the once-per-season event — winterize the irrigation before the freeze
cracks it — with its own season-state key so it cannot collide with any of
the other four verticals' first-freeze rows inside one campaign's carried
state. `spring-green-up-day` and `fall-leaf-peak` are the two
shoulder-season pushes this vertical leans on hardest, and
`spring-fertilize-window`/`fall-fertilize-window` are a second such pair
for feeding rather than cleanup — named for the season each is *for*
rather than left for a rep to infer from the month strip.
`first-snow-landscaping` and `mosquito-surge` round the book out: a first
snowfall for the plowing side of the business, and a run of overcast days
for the pest-control side, the same `cloud_percent_min`/`consecutive_days`
shape the restaurant vertical's `gray-streak` already uses.

## The sixth vertical

Pool & Spa Service, and the claim held a fifth time: thirteen more rows,
still the same rule vocabulary, still no change to `evaluate_trigger()`,
`store.py`, `app.py` or the staff template. The psychology is a service
reminder like HVAC and landscaping, built around chemical balance and
seasonal opening/closing rather than temperature comfort alone.
`chlorine-burn-off` and `algae-bloom-risk` are an escalating pair — hot and
sunny burns chlorine off fast, heat stacked on humidity is the real algae
risk — the same escalation shape as HVAC's `ac-overload`/`heat-index-strain`.
`first-freeze-pool`, `hard-freeze-pool` and `deep-freeze-pool` are a
three-step cold-weather ladder: the once-per-season event that opens the
season's winterizing conversation, a daily row for every hard freeze after
it (equipment left un-winterized is a risk every time, not only the
first), and an emergency row for genuinely extreme cold. `pool-opening-day`
and `pool-closing-day` are the two shoulder-season pushes the vertical
exists to sell, mirroring `spring-tune-up-day`/`fall-tune-up-day` for HVAC.
`spa-season-open` is the one row that runs opposite the rest of the book on
purpose — cold weather is when hot tub demand actually surges, the exact
inverse of what drives every other row here. `storm-debris-cleanup` and
`high-wind-debris-pool` split cleanup the way landscaping's alert and
daily rows do, and `heavy-rain-dilution`/`evaporation-watch` are the two
water-chemistry rows for rain diluting chemicals and heat evaporating the
water level, each escalating past the ordinary chlorine-burn-off condition
rather than duplicating it.

## The seventh vertical

Roofing & Exterior, and the claim held a sixth time: thirteen more rows,
still the same rule vocabulary, still no change to `evaluate_trigger()`,
`store.py`, `app.py` or the staff template. The psychology is a service
reminder like HVAC's, built almost entirely around damage inspection rather
than an appliance under strain — a roof does not fail on a comfortable day,
so nearly every row names the specific risk a weather condition puts a
shingle, a seal or a structure under and the inspection that heads it off.
`high-wind-shingle-risk` and `wind-damage-inspection` are an escalating
wind pair, the same shape as HVAC's `ac-overload`/`heat-index-strain` and
auto's `battery-cold-test`/`deep-freeze-auto`. `wind-driven-rain` is the one
row here that combines two conditions in a single rule (`wind_mph_min` and
`precip_prob_min` together) rather than one — the same multi-condition
shape the restaurant vertical's `patio-day` already uses — because wind and
rain together find a failing flashing seal that either alone rarely does.
`ice-dam-risk` is its own daily row rather than an escalation of anything:
a freeze-thaw band, not a hard freeze, is what actually melts and refreezes
snow at the eave. `first-freeze-roofing` is the once-per-season event —
check gutters and flashing before the freeze finds them — with its own
season-state key so it cannot collide with any of the other six verticals'
first-freeze rows inside one campaign's carried state, even though all
seven share the literal `once_per_season: "cold"` value: `trigger_state` is
stored per-pick, keyed on `trigger_id`, in `store.py`, so none of them
actually share a state dict. `deep-freeze-roofing` escalates past it for a
genuinely extreme cold snap, the way HVAC's `deep-freeze` escalates past
`hard-freeze`. `spring-roof-inspection` and `fall-roof-inspection` are the
two shoulder-season pushes the vertical leans on hardest, and
`gutter-cleaning-season` is a third, narrower fall push the way retail's
`fall-clearance-day` sits alongside its own shoulder-season pair.
`snow-load-roof`, `heavy-rain-leak-check` and `heat-wave-shingle-stress`
round the book out: a single heavy snowfall for structural risk, a heavy
rain for the leak it reveals, and sustained heat for the shingle damage it
causes on its own, needing no storm alert behind any of them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# A campaign with a fourth pick is not a campaign that reaches more people;
# below the restaurant rate card's mid rung it is one that reaches nobody's
# auction. Enforced here, not merely disabled in the picker.
MAX_TRIGGERS = 3

VERTICALS = ("restaurant", "hvac", "retail", "auto", "landscaping", "pool_spa", "roofing")
VERTICAL_LABELS = {"restaurant": "Restaurant", "hvac": "HVAC / Home Comfort",
                   "retail": "Retail / Home Goods", "auto": "Auto Repair / Service",
                   "landscaping": "Landscaping / Lawn Care", "pool_spa": "Pool & Spa Service",
                   "roofing": "Roofing & Exterior"}


@dataclass(frozen=True)
class Trigger:
    id: str
    name: str
    vertical: str
    reason: str
    condition_label: str
    rule: dict = field(default_factory=dict)
    # One or more (start, end) 24h "HH:MM" windows the ad is allowed to serve
    # in. Empty means all day.
    windows: tuple[tuple[str, str], ...] = ()
    cadence: str = "daily"          # daily | once_per_season | alert_driven
    tags: tuple[str, ...] = ()
    approximated: str = ""          # non-empty: what this rule stands in for


TRIGGERS: dict[str, Trigger] = {
    "cold-snap": Trigger(
        id="cold-snap", name="Cold Snap", vertical="restaurant",
        reason="A hard freeze keeps people home; comfort food and delivery "
               "are what gets them fed anyway.",
        condition_label="high ≤ 32°F",
        rule={"temp_max": 32.0},
        cadence="daily", tags=("comfort",),
    ),
    "snow-day": Trigger(
        id="snow-day", name="Snow Day", vertical="restaurant",
        reason="A snowed-in town still wants dinner. Say you're open before "
               "they assume you're not.",
        condition_label="snowfall ≥ 2 in / 24h",
        rule={"snow_in_min": 2.0},
        cadence="daily", tags=("open", "delivery"),
    ),
    "wind-chill": Trigger(
        id="wind-chill", name="Wind Chill", vertical="restaurant",
        reason="Feels-like, not the thermometer — this is the day the walk "
               "to the car decides where somebody eats.",
        condition_label="feels-like ≤ 15°F",
        rule={"feels_like_max": 15.0},
        windows=(("11:00", "21:00"),),
        cadence="daily", tags=("comfort",),
    ),
    "gray-streak": Trigger(
        id="gray-streak", name="Gray Streak", vertical="restaurant",
        reason="Three overcast days in a row is a mood, not a forecast — a "
               "reason to get out of the house that has nothing to do with "
               "temperature.",
        condition_label="cloud cover ≥ 80% for 3 consecutive days",
        rule={"cloud_percent_min": 80.0, "consecutive_days": 3},
        windows=(("11:00", "20:00"),),
        cadence="daily", tags=("mood",),
    ),
    "rain-delay": Trigger(
        id="rain-delay", name="Rain Delay", vertical="restaurant",
        reason="A soaked lunch or dinner rush is a delivery order waiting "
               "to happen.",
        condition_label="≥ 70% chance of rain",
        rule={"precip_prob_min": 70.0},
        windows=(("11:00", "13:00"), ("17:00", "19:00")),
        cadence="daily", tags=("delivery",),
        approximated="The spec's condition is a rain rate (≥ 0.10 in/h), "
                     "which this Hub's weather provider does not publish. "
                     "Chance of rain is read instead and named as the "
                     "stand-in on the card.",
    ),
    "storm-watch": Trigger(
        id="storm-watch", name="Storm Watch", vertical="restaurant",
        reason="A severe weather alert, run straight — never as an "
               "invitation to drive in one.",
        condition_label="NWS severe weather alert issued",
        rule={"alert_required": True},
        cadence="alert_driven", tags=("safety",),
    ),
    "patio-day": Trigger(
        id="patio-day", name="Perfect Patio Day", vertical="restaurant",
        reason="The single highest-lift condition on this list: comfortable, "
               "dry and calm enough that nobody hesitates to sit outside.",
        condition_label="68–82°F · 0% precip · wind < 12 mph",
        rule={"temp_min": 68.0, "temp_max": 82.0, "precip_prob_max": 0.0,
              "wind_mph_max": 12.0},
        windows=(("11:00", "21:00"),),
        cadence="daily", tags=("patio",),
    ),
    "warm-break": Trigger(
        id="warm-break", name="Warm Break", vertical="restaurant",
        reason="A genuinely warm day in the middle of winter is a surprise "
               "worth advertising, not just weather.",
        condition_label="high ≥ 70°F, October–March",
        rule={"temp_min": 70.0, "months": (10, 11, 12, 1, 2, 3)},
        windows=(("11:00", "20:00"),),
        cadence="daily", tags=("seasonal",),
    ),
    "heat-wave": Trigger(
        id="heat-wave", name="Heat Wave", vertical="restaurant",
        reason="When it's genuinely hot, people go out for someone else to "
               "do the cooking.",
        condition_label="high ≥ 88°F",
        rule={"temp_min": 88.0},
        windows=(("11:00", "21:00"),),
        cadence="daily", tags=("relief",),
    ),
    "heat-index": Trigger(
        id="heat-index", name="Heat Index", vertical="restaurant",
        reason="Humidity makes a hot day worse than the thermometer says — "
               "a separate condition on purpose, because it's the day they "
               "order in rather than go out.",
        condition_label="heat index ≥ 95°F",
        rule={"heat_index_min": 95.0},
        windows=(("11:00", "20:00"),),
        cadence="daily", tags=("delivery", "relief"),
    ),
    "evening-cooldown": Trigger(
        id="evening-cooldown", name="Evening Cooldown", vertical="restaurant",
        reason="A sharp drop after a hot day is exactly when a patio becomes "
               "comfortable again — late covers, not lunch.",
        condition_label="temp drops ≥ 10°F by sunset",
        rule={"evening_drop_min": 10.0},
        windows=(("18:00", "22:00"),),
        cadence="daily", tags=("patio", "evening"),
        approximated="Drop is measured against today's high rather than an "
                     "hourly forecast, which this Hub's provider does not "
                     "carry.",
    ),
    "crisp-day": Trigger(
        id="crisp-day", name="Crisp Day", vertical="restaurant",
        reason="Cool and clear reads as a seasonal-menu day rather than a "
               "cold one.",
        condition_label="45–62°F and clear",
        rule={"temp_min": 45.0, "temp_max": 62.0, "precip_prob_max": 20.0},
        windows=(("11:00", "20:00"),),
        cadence="daily", tags=("seasonal",),
    ),
    "first-freeze": Trigger(
        id="first-freeze", name="First Freeze", vertical="restaurant",
        reason="The first hard freeze of the season is an event, not a "
               "recurring condition — it fires once and retires itself.",
        condition_label="first low ≤ 32°F of the season",
        rule={"temp_low_max": 32.0, "once_per_season": "cold"},
        cadence="once_per_season", tags=("event",),
    ),
    "first-cool-night": Trigger(
        id="first-cool-night", name="First Cool Night", vertical="restaurant",
        reason="The first night cool enough to sit outside after summer — "
               "a patio reopening, effectively.",
        condition_label="first low ≤ 55°F after Aug 15",
        rule={"temp_low_max": 55.0, "once_per_season": "cool",
              "not_before": (8, 15)},
        windows=(("17:00", "22:00"),),
        cadence="once_per_season", tags=("event", "patio"),
    ),

    # -- HVAC -----------------------------------------------------------
    "ac-overload": Trigger(
        id="ac-overload", name="AC Overload Risk", vertical="hvac",
        reason="Sustained heat pushes a cooling system past what it was "
               "sized for — the day a breakdown call comes in.",
        condition_label="high ≥ 92°F",
        rule={"temp_min": 92.0},
        windows=(("09:00", "20:00"),),
        cadence="daily", tags=("cooling", "emergency"),
    ),
    "heat-index-strain": Trigger(
        id="heat-index-strain", name="Heat Index Strain", vertical="hvac",
        reason="Humidity stacked on heat is what actually burns out a "
               "compressor, not the number on the thermometer.",
        condition_label="heat index ≥ 100°F",
        rule={"heat_index_min": 100.0},
        windows=(("09:00", "20:00"),),
        cadence="daily", tags=("cooling", "relief"),
    ),
    "early-heat-wave": Trigger(
        id="early-heat-wave", name="Early Heat Wave", vertical="hvac",
        reason="An 85°+ day arriving before summer has properly started is "
               "exactly when a system that skipped its spring check-up "
               "gets caught out.",
        condition_label="high ≥ 85°F, April–June",
        rule={"temp_min": 85.0, "months": (4, 5, 6)},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("cooling", "seasonal"),
    ),
    "mild-winter-break": Trigger(
        id="mild-winter-break", name="Mild Winter Break", vertical="hvac",
        reason="A surprisingly mild day in the dead of winter is a quiet "
               "moment to ask whether a struggling system needs help, "
               "rather than an emergency-repair pitch.",
        condition_label="high ≥ 55°F, December–February",
        rule={"temp_min": 55.0, "months": (12, 1, 2)},
        windows=(("09:00", "18:00"),),
        cadence="daily", tags=("heating", "seasonal"),
    ),
    "spring-tune-up-day": Trigger(
        id="spring-tune-up-day", name="Spring Tune-Up Day", vertical="hvac",
        reason="The first comfortable stretch of spring is when people "
               "finally think about the AC they ignored all winter — sell "
               "the tune-up before the first heat wave finds the problem "
               "for them.",
        condition_label="65–80°F and dry, March–May",
        rule={"temp_min": 65.0, "temp_max": 80.0, "precip_prob_max": 20.0,
              "months": (3, 4, 5)},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("maintenance", "seasonal"),
    ),
    "fall-tune-up-day": Trigger(
        id="fall-tune-up-day", name="Fall Tune-Up Day", vertical="hvac",
        reason="The same logic in reverse — a mild fall day is the moment "
               "to get the furnace checked before the first cold snap "
               "makes the appointment book slam shut.",
        condition_label="50–70°F and dry, September–November",
        rule={"temp_min": 50.0, "temp_max": 70.0, "precip_prob_max": 20.0,
              "months": (9, 10, 11)},
        windows=(("09:00", "18:00"),),
        cadence="daily", tags=("maintenance", "seasonal"),
    ),
    "overcast-run": Trigger(
        id="overcast-run", name="Overcast Run", vertical="hvac",
        reason="Short, gray, cold days keep a furnace running nearly "
               "non-stop without anyone testing it first — the ad for "
               "catching a weak system before it becomes a no-heat call.",
        condition_label="cloud cover ≥ 80% for 3 consecutive days",
        rule={"cloud_percent_min": 80.0, "consecutive_days": 3},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("heating", "efficiency"),
    ),
    "wind-chill-strain": Trigger(
        id="wind-chill-strain", name="Wind Chill Strain", vertical="hvac",
        reason="Wind driving the cold straight through poor insulation is "
               "what turns an ordinary cold day into a heating-bill "
               "complaint.",
        condition_label="feels-like ≤ 0°F",
        rule={"feels_like_max": 0.0},
        windows=(("07:00", "21:00"),),
        cadence="daily", tags=("heating", "efficiency"),
    ),
    "hard-freeze": Trigger(
        id="hard-freeze", name="Hard Freeze", vertical="hvac",
        reason="Below this, a furnace runs nearly continuously — the day "
               "an aging system's weak point finds itself and \"no heat\" "
               "calls spike.",
        condition_label="high ≤ 15°F",
        rule={"temp_max": 15.0},
        windows=(("07:00", "21:00"),),
        cadence="daily", tags=("heating", "emergency"),
    ),
    "deep-freeze": Trigger(
        id="deep-freeze", name="Deep Freeze", vertical="hvac",
        reason="Below zero is where a marginal system actually fails — "
               "the emergency-repair ad, not the tune-up one.",
        condition_label="high ≤ 0°F",
        rule={"temp_max": 0.0},
        cadence="daily", tags=("heating", "emergency"),
    ),
    "first-hard-freeze": Trigger(
        id="first-hard-freeze", name="First Hard Freeze", vertical="hvac",
        reason="The first hard freeze of the season is when a furnace "
               "that coasted through fall gets tested for real — an "
               "inspection ad before it fails, not after.",
        condition_label="first low ≤ 28°F of the season",
        rule={"temp_low_max": 28.0, "once_per_season": "cold"},
        cadence="once_per_season", tags=("event", "heating"),
    ),
    "storm-power-risk": Trigger(
        id="storm-power-risk", name="Storm Power Risk", vertical="hvac",
        reason="A severe weather alert is also a power-outage risk — the "
               "moment a backup-power conversation is timely, never run "
               "as an invitation to be outside in it.",
        condition_label="NWS severe weather alert issued",
        rule={"alert_required": True},
        cadence="alert_driven", tags=("safety", "backup"),
    ),
    "snow-load": Trigger(
        id="snow-load", name="Snow Load", vertical="hvac",
        reason="Heavy snow is also the day an outdoor condenser gets "
               "buried and a vent gets blocked — an ad about keeping "
               "equipment clear, not just about being open.",
        condition_label="snowfall ≥ 4 in / 24h",
        rule={"snow_in_min": 4.0},
        windows=(("07:00", "19:00"),),
        cadence="daily", tags=("heating", "maintenance"),
    ),

    # -- Retail / Home Goods ---------------------------------------------
    "storm-prep": Trigger(
        id="storm-prep", name="Storm Prep", vertical="retail",
        reason="A severe weather alert is when people actually go buy "
               "flashlights, batteries and water — a stock-up ad, never "
               "an invitation to be outside in it.",
        condition_label="NWS severe weather alert issued",
        rule={"alert_required": True},
        cadence="alert_driven", tags=("safety", "stock-up"),
    ),
    "snow-gear-day": Trigger(
        id="snow-gear-day", name="Snow Gear Day", vertical="retail",
        reason="The day it's actually snowing is the day someone finds "
               "out they own no shovel, no salt and no boots that still "
               "fit.",
        condition_label="snowfall ≥ 2 in / 24h",
        rule={"snow_in_min": 2.0},
        windows=(("08:00", "20:00"),),
        cadence="daily", tags=("seasonal", "stock-up"),
    ),
    "first-frost-shop": Trigger(
        id="first-frost-shop", name="First Frost", vertical="retail",
        reason="The first frost of the season is when patio-furniture "
               "covers, pipe insulation and space heaters start moving "
               "— before the freeze, not after.",
        condition_label="first low ≤ 32°F of the season",
        rule={"temp_low_max": 32.0, "once_per_season": "cold"},
        cadence="once_per_season", tags=("event", "seasonal"),
    ),
    "heat-wave-cooling": Trigger(
        id="heat-wave-cooling", name="Cooling Sale Day", vertical="retail",
        reason="A genuinely hot day is when the fan or window AC that "
               "sat on a shelf all spring finally sells.",
        condition_label="high ≥ 90°F",
        rule={"temp_min": 90.0},
        windows=(("09:00", "20:00"),),
        cadence="daily", tags=("relief", "stock-up"),
    ),
    "patio-season-open": Trigger(
        id="patio-season-open", name="Patio Season Opener", vertical="retail",
        reason="The first comfortable outdoor stretch of the year is "
               "when patio furniture, grills and outdoor decor actually "
               "move.",
        condition_label="68–85°F and dry, April–June",
        rule={"temp_min": 68.0, "temp_max": 85.0, "precip_prob_max": 10.0,
              "months": (4, 5, 6)},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("seasonal", "home"),
    ),
    "fall-clearance-day": Trigger(
        id="fall-clearance-day", name="Fall Clearance Day", vertical="retail",
        reason="A cool, clear fall day is when people finally think "
               "about the yard and the garage before winter shuts both "
               "down.",
        condition_label="45–62°F and clear, September–November",
        rule={"temp_min": 45.0, "temp_max": 62.0, "precip_prob_max": 20.0,
              "months": (9, 10, 11)},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("seasonal", "clearance"),
    ),
    "rain-day-indoor": Trigger(
        id="rain-day-indoor", name="Rainy Day Browse", vertical="retail",
        reason="A rained-out weekend keeps people inside a store, or on "
               "the site, instead of outside — the day an indoor project "
               "finally gets started.",
        condition_label="≥ 70% chance of rain",
        rule={"precip_prob_min": 70.0},
        windows=(("10:00", "19:00"),),
        cadence="daily", tags=("indoor", "browse"),
    ),
    "wind-advisory": Trigger(
        id="wind-advisory", name="Wind Advisory", vertical="retail",
        reason="High wind is what tips over a patio umbrella and tears "
               "a trampoline net — a replace-and-tie-down ad, not a "
               "warning.",
        condition_label="wind ≥ 25 mph",
        rule={"wind_mph_min": 25.0},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("safety", "home"),
    ),
    "gray-streak-retail": Trigger(
        id="gray-streak-retail", name="Gray Stretch", vertical="retail",
        reason="Several gray days in a row is a mood worth lifting with "
               "something new for the house, not a specific need.",
        condition_label="cloud cover ≥ 80% for 3 consecutive days",
        rule={"cloud_percent_min": 80.0, "consecutive_days": 3},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("mood", "home"),
    ),
    "deep-freeze-retail": Trigger(
        id="deep-freeze-retail", name="Deep Freeze", vertical="retail",
        reason="Dangerous cold is when a space heater, pipe insulation "
               "or an emergency kit sells regardless of what else is on "
               "sale.",
        condition_label="high ≤ 10°F",
        rule={"temp_max": 10.0},
        windows=(("08:00", "20:00"),),
        cadence="daily", tags=("emergency", "stock-up"),
    ),
    "first-warm-weekend": Trigger(
        id="first-warm-weekend", name="Warm Weekend Surprise", vertical="retail",
        reason="An unseasonably warm day in the middle of winter is a "
               "rare window to sell outdoor goods nobody was thinking "
               "about yet.",
        condition_label="high ≥ 65°F, November–March",
        rule={"temp_min": 65.0, "months": (11, 12, 1, 2, 3)},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("seasonal", "surprise"),
    ),
    "cold-snap-retail": Trigger(
        id="cold-snap-retail", name="Cold Snap", vertical="retail",
        reason="Hard cold is when blankets, space heaters and "
               "warm-layer clothing move off the shelf.",
        condition_label="high ≤ 28°F",
        rule={"temp_max": 28.0},
        windows=(("08:00", "20:00"),),
        cadence="daily", tags=("comfort", "stock-up"),
    ),
    "heat-index-retail": Trigger(
        id="heat-index-retail", name="Heat Index Day", vertical="retail",
        reason="Humidity stacked on heat is when a portable AC or a "
               "dehumidifier gets bought the same day, not researched "
               "for later.",
        condition_label="heat index ≥ 95°F",
        rule={"heat_index_min": 95.0},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("relief", "stock-up"),
    ),
    # -- Auto Repair / Service -------------------------------------------
    "battery-cold-test": Trigger(
        id="battery-cold-test", name="Battery Cold Test", vertical="auto",
        reason="A battery that starts fine at 40°F can fail outright at "
               "10°F — the day a free test catches a weak one before it "
               "strands somebody.",
        condition_label="high ≤ 20°F",
        rule={"temp_max": 20.0},
        windows=(("08:00", "19:00"),),
        cadence="daily", tags=("battery", "maintenance"),
    ),
    "deep-freeze-auto": Trigger(
        id="deep-freeze-auto", name="Deep Freeze", vertical="auto",
        reason="Below zero is where a marginal battery actually dies — "
               "the tow-and-jump-start ad, not the tune-up one.",
        condition_label="high ≤ 0°F",
        rule={"temp_max": 0.0},
        cadence="daily", tags=("battery", "emergency"),
    ),
    "first-freeze-auto": Trigger(
        id="first-freeze-auto", name="First Freeze", vertical="auto",
        reason="The first hard freeze of the season is when a battery and "
               "coolant system that coasted through fall gets tested for "
               "real — a check-it-now ad before it fails on a cold "
               "morning, not a tow bill after.",
        condition_label="first low ≤ 32°F of the season",
        rule={"temp_low_max": 32.0, "once_per_season": "cold"},
        cadence="once_per_season", tags=("event", "battery"),
    ),
    "ac-check-early": Trigger(
        id="ac-check-early", name="Early AC Check", vertical="auto",
        reason="An 85°+ day arriving before summer has properly started "
               "is exactly when a cabin AC that was low on refrigerant "
               "all winter gets caught out.",
        condition_label="high ≥ 85°F, April–June",
        rule={"temp_min": 85.0, "months": (4, 5, 6)},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("cooling", "seasonal"),
    ),
    "heat-index-auto": Trigger(
        id="heat-index-auto", name="Heat Index Strain", vertical="auto",
        reason="Humidity stacked on heat is what actually strains a "
               "cabin AC compressor on a long drive, not the number on "
               "the thermometer.",
        condition_label="heat index ≥ 100°F",
        rule={"heat_index_min": 100.0},
        windows=(("09:00", "20:00"),),
        cadence="daily", tags=("cooling", "relief"),
    ),
    "spring-service-day": Trigger(
        id="spring-service-day", name="Spring Service Day", vertical="auto",
        reason="The first comfortable stretch of spring is when people "
               "finally think about the AC and the tires they ignored "
               "all winter — sell the check-up before the first heat "
               "wave finds the problem for them.",
        condition_label="65–80°F and dry, March–May",
        rule={"temp_min": 65.0, "temp_max": 80.0, "precip_prob_max": 20.0,
              "months": (3, 4, 5)},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("maintenance", "seasonal"),
    ),
    "fall-service-day": Trigger(
        id="fall-service-day", name="Fall Service Day", vertical="auto",
        reason="The same logic in reverse — a mild fall day is the "
               "moment to get the battery and coolant checked before the "
               "first cold snap makes the appointment book slam shut.",
        condition_label="50–70°F and dry, September–November",
        rule={"temp_min": 50.0, "temp_max": 70.0, "precip_prob_max": 20.0,
              "months": (9, 10, 11)},
        windows=(("09:00", "18:00"),),
        cadence="daily", tags=("maintenance", "seasonal"),
    ),
    "wiper-blade-season": Trigger(
        id="wiper-blade-season", name="Wiper Blade Weather", vertical="auto",
        reason="A heavy-rain forecast is when a streaking, worn wiper "
               "blade stops being a minor annoyance and starts being a "
               "visibility problem — the day it actually gets replaced.",
        condition_label="≥ 70% chance of rain",
        rule={"precip_prob_min": 70.0},
        windows=(("07:00", "19:00"),),
        cadence="daily", tags=("wipers", "safety"),
    ),
    "pothole-season": Trigger(
        id="pothole-season", name="Pothole Season", vertical="auto",
        reason="The freeze-thaw stretch that opens potholes is also when "
               "an alignment check and a tire inspection actually get "
               "booked, before a bent rim turns into a bigger bill.",
        condition_label="35–55°F, February–April",
        rule={"temp_min": 35.0, "temp_max": 55.0, "months": (2, 3, 4)},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("tires", "seasonal"),
    ),
    "snow-tire-day": Trigger(
        id="snow-tire-day", name="Snow Tire Day", vertical="auto",
        reason="The day it's actually snowing is the day somebody finds "
               "out their tires are bald — a swap-or-check ad, not a "
               "warning.",
        condition_label="snowfall ≥ 2 in / 24h",
        rule={"snow_in_min": 2.0},
        windows=(("08:00", "19:00"),),
        cadence="daily", tags=("tires", "seasonal"),
    ),
    "cold-snap-auto": Trigger(
        id="cold-snap-auto", name="Cold Snap", vertical="auto",
        reason="Hard cold is when tire pressure drops enough to trip a "
               "warning light and a marginal battery starts to struggle "
               "— a check-it-now ad rather than the emergency one.",
        condition_label="high ≤ 25°F",
        rule={"temp_max": 25.0},
        windows=(("08:00", "19:00"),),
        cadence="daily", tags=("battery", "maintenance"),
    ),
    "wind-chill-auto": Trigger(
        id="wind-chill-auto", name="Wind Chill", vertical="auto",
        reason="Feels-like, not the thermometer — the day a weak "
               "battery and a thin oil viscosity both get exposed on "
               "the first cold start.",
        condition_label="feels-like ≤ 10°F",
        rule={"feels_like_max": 10.0},
        windows=(("07:00", "20:00"),),
        cadence="daily", tags=("battery", "maintenance"),
    ),
    "storm-driving-prep": Trigger(
        id="storm-driving-prep", name="Storm Driving Prep", vertical="auto",
        reason="A severe weather alert is when wipers, tires and brakes "
               "actually matter — a get-it-checked-before-the-next-one "
               "ad, never an invitation to be on the road in this one.",
        condition_label="NWS severe weather alert issued",
        rule={"alert_required": True},
        cadence="alert_driven", tags=("safety", "tires"),
    ),
    # -- Landscaping / Lawn Care ------------------------------------------
    "spring-green-up-day": Trigger(
        id="spring-green-up-day", name="Spring Green-Up Day", vertical="landscaping",
        reason="The first comfortable, dry stretch of spring is when a lawn "
               "actually starts growing again — the week to book the first "
               "mow and the spring cleanup before it gets ahead of anybody.",
        condition_label="55–75°F and dry, March–May",
        rule={"temp_min": 55.0, "temp_max": 75.0, "precip_prob_max": 20.0,
              "months": (3, 4, 5)},
        windows=(("08:00", "19:00"),),
        cadence="daily", tags=("maintenance", "seasonal"),
    ),
    "dry-spell-watering": Trigger(
        id="dry-spell-watering", name="Dry Spell Watering", vertical="landscaping",
        reason="A hot, dry stretch is when an irrigation system that has "
               "not been checked all season gets caught out — a check-up "
               "ad before the lawn actually shows the stress.",
        condition_label="high ≥ 85°F, ≤ 10% chance of rain",
        rule={"temp_min": 85.0, "precip_prob_max": 10.0},
        windows=(("08:00", "19:00"),),
        cadence="daily", tags=("irrigation", "maintenance"),
    ),
    "drought-stress": Trigger(
        id="drought-stress", name="Drought Stress", vertical="landscaping",
        reason="Past a certain point, hot and dry stops being watering "
               "weather and starts being damage — the escalation of a dry "
               "spell into a lawn that is actually dying.",
        condition_label="high ≥ 92°F, ≤ 10% chance of rain",
        rule={"temp_min": 92.0, "precip_prob_max": 10.0},
        windows=(("08:00", "19:00"),),
        cadence="daily", tags=("irrigation", "emergency"),
    ),
    "heavy-rain-growth-spurt": Trigger(
        id="heavy-rain-growth-spurt", name="Heavy Rain Growth Spurt", vertical="landscaping",
        reason="A soaking rain is what actually makes a lawn grow fast — "
               "the days after it are when mowing falls behind, not the "
               "day of the rain itself.",
        condition_label="≥ 70% chance of rain",
        rule={"precip_prob_min": 70.0},
        windows=(("08:00", "19:00"),),
        cadence="daily", tags=("mowing", "maintenance"),
    ),
    "storm-cleanup": Trigger(
        id="storm-cleanup", name="Storm Cleanup", vertical="landscaping",
        reason="A severe weather alert is what actually brings down limbs "
               "and debris — a get-your-yard-checked-after ad, never an "
               "invitation to be out in the storm itself.",
        condition_label="NWS severe weather alert issued",
        rule={"alert_required": True},
        cadence="alert_driven", tags=("cleanup", "safety"),
    ),
    "high-wind-debris": Trigger(
        id="high-wind-debris", name="High Wind Debris", vertical="landscaping",
        reason="Sustained high wind is what actually breaks limbs and "
               "scatters debris short of a full storm alert — a cleanup "
               "call rather than an emergency one.",
        condition_label="sustained wind ≥ 30 mph",
        rule={"wind_mph_min": 30.0},
        windows=(("08:00", "19:00"),),
        cadence="daily", tags=("cleanup", "maintenance"),
    ),
    "first-freeze-landscaping": Trigger(
        id="first-freeze-landscaping", name="First Freeze", vertical="landscaping",
        reason="The first hard freeze of the season is when an irrigation "
               "system left un-winterized cracks — a book-it-now ad before "
               "the freeze, not a repair bill after.",
        condition_label="first low ≤ 32°F of the season",
        rule={"temp_low_max": 32.0, "once_per_season": "cold"},
        cadence="once_per_season", tags=("event", "irrigation"),
    ),
    "fall-leaf-peak": Trigger(
        id="fall-leaf-peak", name="Fall Leaf Peak", vertical="landscaping",
        reason="A run of cool, dry fall days is when leaves are actually "
               "down and dry enough to clear — the week leaf removal "
               "books solid.",
        condition_label="40–60°F and dry, October–November",
        rule={"temp_min": 40.0, "temp_max": 60.0, "precip_prob_max": 20.0,
              "months": (10, 11)},
        windows=(("08:00", "18:00"),),
        cadence="daily", tags=("cleanup", "seasonal"),
    ),
    "first-snow-landscaping": Trigger(
        id="first-snow-landscaping", name="First Snow", vertical="landscaping",
        reason="The first real snowfall is when a homeowner who has not "
               "booked plowing finds out the hard way — an ad for the "
               "morning it lands, not before.",
        condition_label="snowfall ≥ 2 in / 24h",
        rule={"snow_in_min": 2.0},
        windows=(("06:00", "18:00"),),
        cadence="daily", tags=("snow", "seasonal"),
    ),
    "mosquito-surge": Trigger(
        id="mosquito-surge", name="Mosquito Surge", vertical="landscaping",
        reason="Standing water breeds mosquitoes days after it rains, not "
               "during it — a run of overcast, wet-adjacent days is the "
               "signal for a treatment call, not the rain itself.",
        condition_label="cloud cover ≥ 80% for 3 consecutive days",
        rule={"cloud_percent_min": 80.0, "consecutive_days": 3},
        windows=(("08:00", "19:00"),),
        cadence="daily", tags=("pest-control", "seasonal"),
    ),
    "spring-fertilize-window": Trigger(
        id="spring-fertilize-window", name="Spring Fertilize Window", vertical="landscaping",
        reason="A mild, dry stretch in early spring is the window a "
               "pre-emergent or first feeding actually takes — sell it "
               "before the weeds get there first.",
        condition_label="50–70°F and dry, March–April",
        rule={"temp_min": 50.0, "temp_max": 70.0, "precip_prob_max": 20.0,
              "months": (3, 4)},
        windows=(("08:00", "19:00"),),
        cadence="daily", tags=("fertilizing", "seasonal"),
    ),
    "fall-fertilize-window": Trigger(
        id="fall-fertilize-window", name="Fall Fertilize Window", vertical="landscaping",
        reason="The same logic in reverse — a mild fall feeding is what "
               "actually strengthens a lawn's roots before winter, and the "
               "window is short.",
        condition_label="50–70°F and dry, September–October",
        rule={"temp_min": 50.0, "temp_max": 70.0, "precip_prob_max": 20.0,
              "months": (9, 10)},
        windows=(("08:00", "18:00"),),
        cadence="daily", tags=("fertilizing", "seasonal"),
    ),
    "heat-wave-lawn-stress": Trigger(
        id="heat-wave-lawn-stress", name="Heat Wave Lawn Stress", vertical="landscaping",
        reason="Heat index, not the thermometer — humidity stacked on heat "
               "is what actually pushes a lawn past what watering alone "
               "can fix.",
        condition_label="heat index ≥ 100°F",
        rule={"heat_index_min": 100.0},
        windows=(("08:00", "19:00"),),
        cadence="daily", tags=("irrigation", "relief"),
    ),
    # -- Pool & Spa Service -----------------------------------------------
    "pool-opening-day": Trigger(
        id="pool-opening-day", name="Pool Opening Day", vertical="pool_spa",
        reason="The first warm, dry stretch of spring is when a pool "
               "actually gets used — the week to book the opening before "
               "the first hot weekend finds it still covered.",
        condition_label="65–82°F and dry, March–May",
        rule={"temp_min": 65.0, "temp_max": 82.0, "precip_prob_max": 20.0,
              "months": (3, 4, 5)},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("opening", "seasonal"),
    ),
    "chlorine-burn-off": Trigger(
        id="chlorine-burn-off", name="Chlorine Burn-Off", vertical="pool_spa",
        reason="Hot, sunny days burn chlorine off fast enough that a pool "
               "left untested for a few days is already out of balance — "
               "a check-and-treat call, not an emergency one.",
        condition_label="high ≥ 85°F, ≤ 15% chance of rain",
        rule={"temp_min": 85.0, "precip_prob_max": 15.0},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("chemical", "maintenance"),
    ),
    "algae-bloom-risk": Trigger(
        id="algae-bloom-risk", name="Algae Bloom Risk", vertical="pool_spa",
        reason="Heat stacked on humidity is what actually turns an "
               "under-treated pool green — the escalation past ordinary "
               "chlorine burn-off into a real algae risk.",
        condition_label="heat index ≥ 95°F",
        rule={"heat_index_min": 95.0},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("chemical", "emergency"),
    ),
    "heavy-rain-dilution": Trigger(
        id="heavy-rain-dilution", name="Heavy Rain Dilution", vertical="pool_spa",
        reason="A heavy rain dilutes chemicals and can push water over the "
               "skimmer line — a rebalance-and-check call for the day "
               "after, not the day of.",
        condition_label="≥ 70% chance of rain",
        rule={"precip_prob_min": 70.0},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("chemical", "maintenance"),
    ),
    "storm-debris-cleanup": Trigger(
        id="storm-debris-cleanup", name="Storm Debris Cleanup", vertical="pool_spa",
        reason="A severe weather alert is what actually fills a pool with "
               "debris and can knock equipment offline — a get-it-checked-"
               "after ad, never an invitation to be out in the storm.",
        condition_label="NWS severe weather alert issued",
        rule={"alert_required": True},
        cadence="alert_driven", tags=("cleanup", "safety"),
    ),
    "high-wind-debris-pool": Trigger(
        id="high-wind-debris-pool", name="High Wind Debris", vertical="pool_spa",
        reason="Sustained high wind blows leaves and debris into open "
               "water short of a full storm alert — a cleaning call "
               "rather than an equipment one.",
        condition_label="sustained wind ≥ 25 mph",
        rule={"wind_mph_min": 25.0},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("cleanup", "maintenance"),
    ),
    "first-freeze-pool": Trigger(
        id="first-freeze-pool", name="First Freeze", vertical="pool_spa",
        reason="The first hard freeze of the season is when pipes and "
               "equipment left un-winterized crack — a book-it-now ad "
               "before the freeze, not a repair bill after.",
        condition_label="first low ≤ 32°F of the season",
        rule={"temp_low_max": 32.0, "once_per_season": "cold"},
        cadence="once_per_season", tags=("event", "winterizing"),
    ),
    "hard-freeze-pool": Trigger(
        id="hard-freeze-pool", name="Hard Freeze", vertical="pool_spa",
        reason="Every hard freeze after the first is still a risk to "
               "un-winterized equipment, not only the first one of the "
               "season — a repeat check, not a one-time event.",
        condition_label="high ≤ 20°F",
        rule={"temp_max": 20.0},
        windows=(("08:00", "18:00"),),
        cadence="daily", tags=("winterizing", "maintenance"),
    ),
    "deep-freeze-pool": Trigger(
        id="deep-freeze-pool", name="Deep Freeze", vertical="pool_spa",
        reason="Below zero is where un-winterized equipment actually fails "
               "outright — the emergency-repair ad, not the check-up one.",
        condition_label="high ≤ 0°F",
        rule={"temp_max": 0.0},
        cadence="daily", tags=("winterizing", "emergency"),
    ),
    "pool-closing-day": Trigger(
        id="pool-closing-day", name="Pool Closing Day", vertical="pool_spa",
        reason="The same logic in reverse — a mild, dry fall stretch is "
               "the window to close and winterize before the first hard "
               "freeze makes the appointment book slam shut.",
        condition_label="55–72°F and dry, September–October",
        rule={"temp_min": 55.0, "temp_max": 72.0, "precip_prob_max": 20.0,
              "months": (9, 10)},
        windows=(("09:00", "18:00"),),
        cadence="daily", tags=("closing", "seasonal"),
    ),
    "spa-season-open": Trigger(
        id="spa-season-open", name="Spa Season Open", vertical="pool_spa",
        reason="A genuinely cold day is when hot tub demand actually "
               "surges — the exact opposite of what drives the pool side "
               "of this business.",
        condition_label="high ≤ 45°F",
        rule={"temp_max": 45.0},
        windows=(("08:00", "20:00"),),
        cadence="daily", tags=("spa", "seasonal"),
    ),
    "evaporation-watch": Trigger(
        id="evaporation-watch", name="Evaporation Watch", vertical="pool_spa",
        reason="Sustained heat with no rain is what actually drops a "
               "pool's water level enough to trip the equipment's low-"
               "water cutoff — a top-off-and-check call before it does.",
        condition_label="high ≥ 92°F, ≤ 10% chance of rain",
        rule={"temp_min": 92.0, "precip_prob_max": 10.0},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("maintenance", "seasonal"),
    ),
    "heat-wave-pool-surge": Trigger(
        id="heat-wave-pool-surge", name="Heat Wave Pool Surge", vertical="pool_spa",
        reason="A genuine heat wave is when a pool sees the most use in a "
               "single week — more swimmers, faster chemical demand, and "
               "the busiest week to be booked for cleaning.",
        condition_label="high ≥ 95°F",
        rule={"temp_min": 95.0},
        windows=(("09:00", "20:00"),),
        cadence="daily", tags=("chemical", "relief"),
    ),
    # -- Roofing & Exterior -----------------------------------------------
    "storm-damage-inspection": Trigger(
        id="storm-damage-inspection", name="Storm Damage Inspection", vertical="roofing",
        reason="A severe weather alert is when hail and falling debris "
               "actually damage a roof — an inspection ad for the day "
               "after, never an invitation to be up on it during the storm.",
        condition_label="NWS severe weather alert issued",
        rule={"alert_required": True},
        cadence="alert_driven", tags=("inspection", "safety"),
    ),
    "high-wind-shingle-risk": Trigger(
        id="high-wind-shingle-risk", name="High Wind Shingle Risk", vertical="roofing",
        reason="Sustained wind past a certain point is what actually lifts "
               "a marginal shingle short of tearing it off outright — a "
               "check-it-before-it-fails ad, not an emergency one.",
        condition_label="sustained wind ≥ 40 mph",
        rule={"wind_mph_min": 40.0},
        windows=(("08:00", "19:00"),),
        cadence="daily", tags=("inspection", "maintenance"),
    ),
    "wind-damage-inspection": Trigger(
        id="wind-damage-inspection", name="Wind Damage Inspection", vertical="roofing",
        reason="Past a certain point, wind stops being a shingle risk and "
               "starts being the reason shingles are actually missing — "
               "the escalation past high-wind-shingle-risk into a real "
               "damage inspection.",
        condition_label="sustained wind ≥ 55 mph",
        rule={"wind_mph_min": 55.0},
        windows=(("08:00", "19:00"),),
        cadence="daily", tags=("inspection", "emergency"),
    ),
    "wind-driven-rain": Trigger(
        id="wind-driven-rain", name="Wind-Driven Rain", vertical="roofing",
        reason="Wind and rain together are what actually find a failing "
               "flashing seal or a lifted shingle edge — either alone "
               "rarely does — so this is the leak-check ad the two "
               "conditions stacked together earn on their own.",
        condition_label="wind ≥ 25 mph and ≥ 60% chance of rain",
        rule={"wind_mph_min": 25.0, "precip_prob_min": 60.0},
        windows=(("08:00", "19:00"),),
        cadence="daily", tags=("inspection", "maintenance"),
    ),
    "ice-dam-risk": Trigger(
        id="ice-dam-risk", name="Ice Dam Risk", vertical="roofing",
        reason="A freeze-thaw band in the dead of winter is exactly when "
               "attic heat melts snow on the roof that then refreezes at "
               "the eave — the ice-dam window, and the ad for gutter and "
               "insulation work that heads it off.",
        condition_label="20–34°F, December–March",
        rule={"temp_min": 20.0, "temp_max": 34.0, "months": (12, 1, 2, 3)},
        windows=(("08:00", "18:00"),),
        cadence="daily", tags=("inspection", "seasonal"),
    ),
    "first-freeze-roofing": Trigger(
        id="first-freeze-roofing", name="First Freeze", vertical="roofing",
        reason="The first hard freeze of the season is when gutters and "
               "flashing left unchecked over fall get tested for real — a "
               "book-it-now ad before the freeze, not a leak after it.",
        condition_label="first low ≤ 32°F of the season",
        rule={"temp_low_max": 32.0, "once_per_season": "cold"},
        cadence="once_per_season", tags=("event", "inspection"),
    ),
    "deep-freeze-roofing": Trigger(
        id="deep-freeze-roofing", name="Deep Freeze", vertical="roofing",
        reason="Below zero is where an already-failing seal or a pipe run "
               "under a poorly insulated roofline actually bursts — the "
               "emergency-repair ad, not the check-up one.",
        condition_label="high ≤ 0°F",
        rule={"temp_max": 0.0},
        cadence="daily", tags=("emergency", "inspection"),
    ),
    "snow-load-roof": Trigger(
        id="snow-load-roof", name="Snow Load Risk", vertical="roofing",
        reason="A heavy single-storm snowfall is what actually stresses a "
               "roof's structure — the day the load is on it, not a "
               "forecast of one.",
        condition_label="snowfall ≥ 6 in / 24h",
        rule={"snow_in_min": 6.0},
        windows=(("07:00", "18:00"),),
        cadence="daily", tags=("inspection", "safety"),
    ),
    "spring-roof-inspection": Trigger(
        id="spring-roof-inspection", name="Spring Roof Inspection", vertical="roofing",
        reason="The first comfortable, dry stretch of spring is when a "
               "roof that spent winter under snow and ice is finally safe "
               "to actually get up on and check.",
        condition_label="55–75°F and dry, March–May",
        rule={"temp_min": 55.0, "temp_max": 75.0, "precip_prob_max": 20.0,
              "months": (3, 4, 5)},
        windows=(("08:00", "19:00"),),
        cadence="daily", tags=("inspection", "seasonal"),
    ),
    "fall-roof-inspection": Trigger(
        id="fall-roof-inspection", name="Fall Roof Inspection", vertical="roofing",
        reason="The same logic in reverse — a mild, dry fall stretch is "
               "the window to catch a problem before the first freeze "
               "makes the appointment book slam shut.",
        condition_label="50–70°F and dry, September–October",
        rule={"temp_min": 50.0, "temp_max": 70.0, "precip_prob_max": 20.0,
              "months": (9, 10)},
        windows=(("08:00", "18:00"),),
        cadence="daily", tags=("inspection", "seasonal"),
    ),
    "gutter-cleaning-season": Trigger(
        id="gutter-cleaning-season", name="Gutter Cleaning Season", vertical="roofing",
        reason="A run of cool, dry fall days is when leaves are actually "
               "down and gutters are worth clearing — before the first "
               "hard freeze turns a clogged gutter into an ice dam.",
        condition_label="40–60°F and dry, October–November",
        rule={"temp_min": 40.0, "temp_max": 60.0, "precip_prob_max": 20.0,
              "months": (10, 11)},
        windows=(("08:00", "18:00"),),
        cadence="daily", tags=("maintenance", "seasonal"),
    ),
    "heavy-rain-leak-check": Trigger(
        id="heavy-rain-leak-check", name="Heavy Rain Leak Check", vertical="roofing",
        reason="A soaking rain is what actually reveals a leak that has "
               "been sitting there dry and invisible — the check-it call "
               "for the day after, not the day of.",
        condition_label="≥ 70% chance of rain",
        rule={"precip_prob_min": 70.0},
        windows=(("08:00", "19:00"),),
        cadence="daily", tags=("inspection", "maintenance"),
    ),
    "heat-wave-shingle-stress": Trigger(
        id="heat-wave-shingle-stress", name="Heat Wave Shingle Stress", vertical="roofing",
        reason="Genuine, sustained heat is what actually blisters and "
               "warps aging shingles — a check-it-before-it-blisters-"
               "further ad, not an emergency one.",
        condition_label="high ≥ 95°F",
        rule={"temp_min": 95.0},
        windows=(("09:00", "19:00"),),
        cadence="daily", tags=("inspection", "relief"),
    ),
}

# Ordering for the month strip: which triggers a rep browsing that month is
# most likely to want to talk about, most-relevant first. This governs
# display order only — a chosen trigger evaluates all year round, whatever
# month it was picked in.
MONTHS: dict[str, tuple[str, ...]] = {
    "Jan": ("cold-snap", "snow-day", "wind-chill", "warm-break", "gray-streak",
            "storm-watch", "crisp-day", "evening-cooldown", "heat-index",
            "heat-wave", "patio-day", "rain-delay", "first-freeze", "first-cool-night",
            "hard-freeze", "deep-freeze", "first-hard-freeze", "wind-chill-strain",
            "overcast-run", "storm-power-risk", "snow-load", "mild-winter-break",
            "spring-tune-up-day", "fall-tune-up-day", "early-heat-wave",
            "heat-index-strain", "ac-overload",
            "deep-freeze-retail", "cold-snap-retail", "first-warm-weekend",
            "snow-gear-day", "wind-advisory", "gray-streak-retail",
            "storm-prep", "fall-clearance-day", "patio-season-open",
            "heat-wave-cooling", "heat-index-retail", "first-frost-shop",
            "rain-day-indoor",
            "cold-snap-auto", "wind-chill-auto", "battery-cold-test", "deep-freeze-auto", "snow-tire-day", "storm-driving-prep", "first-freeze-auto", "wiper-blade-season", "pothole-season", "spring-service-day", "fall-service-day", "ac-check-early", "heat-index-auto",
            "first-freeze-landscaping", "first-snow-landscaping", "storm-cleanup", "high-wind-debris", "mosquito-surge", "spring-green-up-day", "dry-spell-watering", "drought-stress", "heavy-rain-growth-spurt", "fall-leaf-peak", "spring-fertilize-window", "fall-fertilize-window", "heat-wave-lawn-stress",
            "first-freeze-pool", "hard-freeze-pool", "deep-freeze-pool", "spa-season-open", "storm-debris-cleanup", "high-wind-debris-pool", "pool-opening-day", "chlorine-burn-off", "algae-bloom-risk", "heavy-rain-dilution", "evaporation-watch", "heat-wave-pool-surge", "pool-closing-day",
            "ice-dam-risk", "first-freeze-roofing", "deep-freeze-roofing", "snow-load-roof", "storm-damage-inspection", "high-wind-shingle-risk", "wind-damage-inspection", "wind-driven-rain", "heavy-rain-leak-check", "spring-roof-inspection", "fall-roof-inspection", "gutter-cleaning-season", "heat-wave-shingle-stress",
            ),
    "Feb": ("cold-snap", "snow-day", "wind-chill", "warm-break", "gray-streak",
            "storm-watch", "crisp-day", "evening-cooldown", "heat-index",
            "heat-wave", "patio-day", "rain-delay", "first-freeze", "first-cool-night",
            "deep-freeze", "hard-freeze", "first-hard-freeze", "wind-chill-strain",
            "mild-winter-break", "overcast-run", "storm-power-risk", "snow-load",
            "spring-tune-up-day", "fall-tune-up-day", "early-heat-wave",
            "heat-index-strain", "ac-overload",
            "deep-freeze-retail", "cold-snap-retail", "first-warm-weekend",
            "snow-gear-day", "wind-advisory", "gray-streak-retail",
            "storm-prep", "fall-clearance-day", "patio-season-open",
            "heat-wave-cooling", "heat-index-retail", "first-frost-shop",
            "rain-day-indoor",
            "cold-snap-auto", "wind-chill-auto", "battery-cold-test", "deep-freeze-auto", "pothole-season", "snow-tire-day", "storm-driving-prep", "first-freeze-auto", "wiper-blade-season", "spring-service-day", "fall-service-day", "ac-check-early", "heat-index-auto",
            "first-freeze-landscaping", "first-snow-landscaping", "storm-cleanup", "high-wind-debris", "mosquito-surge", "spring-green-up-day", "spring-fertilize-window", "dry-spell-watering", "drought-stress", "heavy-rain-growth-spurt", "fall-leaf-peak", "fall-fertilize-window", "heat-wave-lawn-stress",
            "first-freeze-pool", "hard-freeze-pool", "deep-freeze-pool", "spa-season-open", "storm-debris-cleanup", "high-wind-debris-pool", "pool-opening-day", "chlorine-burn-off", "algae-bloom-risk", "heavy-rain-dilution", "evaporation-watch", "heat-wave-pool-surge", "pool-closing-day",
            "ice-dam-risk", "first-freeze-roofing", "deep-freeze-roofing", "snow-load-roof", "storm-damage-inspection", "high-wind-shingle-risk", "wind-damage-inspection", "wind-driven-rain", "heavy-rain-leak-check", "spring-roof-inspection", "fall-roof-inspection", "gutter-cleaning-season", "heat-wave-shingle-stress",
            ),
    "Mar": ("warm-break", "crisp-day", "rain-delay", "cold-snap", "gray-streak",
            "wind-chill", "storm-watch", "patio-day", "evening-cooldown",
            "heat-index", "heat-wave", "snow-day", "first-freeze", "first-cool-night",
            "spring-tune-up-day", "mild-winter-break", "hard-freeze",
            "wind-chill-strain", "overcast-run", "storm-power-risk",
            "early-heat-wave", "snow-load", "fall-tune-up-day", "deep-freeze",
            "first-hard-freeze", "heat-index-strain", "ac-overload",
            "first-warm-weekend", "patio-season-open", "cold-snap-retail",
            "wind-advisory", "gray-streak-retail", "storm-prep",
            "fall-clearance-day", "snow-gear-day", "deep-freeze-retail",
            "heat-wave-cooling", "heat-index-retail", "first-frost-shop",
            "rain-day-indoor",
            "spring-service-day", "pothole-season", "wiper-blade-season", "cold-snap-auto", "wind-chill-auto", "battery-cold-test", "storm-driving-prep", "ac-check-early", "fall-service-day", "snow-tire-day", "deep-freeze-auto", "first-freeze-auto", "heat-index-auto",
            "spring-green-up-day", "spring-fertilize-window", "heavy-rain-growth-spurt", "storm-cleanup", "high-wind-debris", "mosquito-surge", "dry-spell-watering", "drought-stress", "first-freeze-landscaping", "first-snow-landscaping", "fall-leaf-peak", "fall-fertilize-window", "heat-wave-lawn-stress",
            "pool-opening-day", "spa-season-open", "heavy-rain-dilution", "storm-debris-cleanup", "high-wind-debris-pool", "chlorine-burn-off", "hard-freeze-pool", "first-freeze-pool", "deep-freeze-pool", "algae-bloom-risk", "evaporation-watch", "heat-wave-pool-surge", "pool-closing-day",
            "ice-dam-risk", "spring-roof-inspection", "wind-driven-rain", "storm-damage-inspection", "high-wind-shingle-risk", "wind-damage-inspection", "heavy-rain-leak-check", "first-freeze-roofing", "deep-freeze-roofing", "snow-load-roof", "fall-roof-inspection", "gutter-cleaning-season", "heat-wave-shingle-stress",
            ),
    "Apr": ("crisp-day", "patio-day", "rain-delay", "gray-streak", "storm-watch",
            "evening-cooldown", "warm-break", "heat-index", "heat-wave",
            "cold-snap", "wind-chill", "snow-day", "first-freeze", "first-cool-night",
            "spring-tune-up-day", "early-heat-wave", "overcast-run",
            "storm-power-risk", "wind-chill-strain", "hard-freeze",
            "mild-winter-break", "ac-overload", "heat-index-strain", "snow-load",
            "fall-tune-up-day", "deep-freeze", "first-hard-freeze",
            "patio-season-open", "wind-advisory", "storm-prep",
            "gray-streak-retail", "rain-day-indoor", "fall-clearance-day",
            "first-warm-weekend", "heat-wave-cooling", "heat-index-retail",
            "cold-snap-retail", "snow-gear-day", "deep-freeze-retail",
            "first-frost-shop",
            "spring-service-day", "pothole-season", "ac-check-early", "wiper-blade-season", "storm-driving-prep", "cold-snap-auto", "wind-chill-auto", "battery-cold-test", "fall-service-day", "snow-tire-day", "deep-freeze-auto", "first-freeze-auto", "heat-index-auto",
            "spring-green-up-day", "spring-fertilize-window", "heavy-rain-growth-spurt", "storm-cleanup", "high-wind-debris", "mosquito-surge", "dry-spell-watering", "drought-stress", "first-freeze-landscaping", "first-snow-landscaping", "fall-leaf-peak", "fall-fertilize-window", "heat-wave-lawn-stress",
            "pool-opening-day", "heavy-rain-dilution", "storm-debris-cleanup", "high-wind-debris-pool", "chlorine-burn-off", "spa-season-open", "algae-bloom-risk", "evaporation-watch", "heat-wave-pool-surge", "first-freeze-pool", "hard-freeze-pool", "deep-freeze-pool", "pool-closing-day",
            "spring-roof-inspection", "wind-driven-rain", "storm-damage-inspection", "high-wind-shingle-risk", "wind-damage-inspection", "heavy-rain-leak-check", "ice-dam-risk", "first-freeze-roofing", "deep-freeze-roofing", "snow-load-roof", "fall-roof-inspection", "gutter-cleaning-season", "heat-wave-shingle-stress",
            ),
    "May": ("patio-day", "crisp-day", "rain-delay", "storm-watch",
            "evening-cooldown", "heat-index", "heat-wave", "gray-streak",
            "warm-break", "cold-snap", "wind-chill", "snow-day",
            "first-freeze", "first-cool-night",
            "spring-tune-up-day", "early-heat-wave", "ac-overload",
            "heat-index-strain", "storm-power-risk", "overcast-run",
            "wind-chill-strain", "fall-tune-up-day", "hard-freeze",
            "mild-winter-break", "snow-load", "deep-freeze", "first-hard-freeze",
            "patio-season-open", "storm-prep", "wind-advisory",
            "rain-day-indoor", "heat-wave-cooling", "heat-index-retail",
            "gray-streak-retail", "fall-clearance-day", "first-warm-weekend",
            "cold-snap-retail", "snow-gear-day", "deep-freeze-retail",
            "first-frost-shop",
            "spring-service-day", "ac-check-early", "wiper-blade-season", "storm-driving-prep", "heat-index-auto", "pothole-season", "cold-snap-auto", "wind-chill-auto", "battery-cold-test", "fall-service-day", "snow-tire-day", "deep-freeze-auto", "first-freeze-auto",
            "spring-green-up-day", "dry-spell-watering", "heavy-rain-growth-spurt", "mosquito-surge", "storm-cleanup", "high-wind-debris", "drought-stress", "heat-wave-lawn-stress", "spring-fertilize-window", "first-freeze-landscaping", "first-snow-landscaping", "fall-leaf-peak", "fall-fertilize-window",
            "pool-opening-day", "chlorine-burn-off", "heavy-rain-dilution", "storm-debris-cleanup", "high-wind-debris-pool", "algae-bloom-risk", "evaporation-watch", "heat-wave-pool-surge", "spa-season-open", "first-freeze-pool", "hard-freeze-pool", "deep-freeze-pool", "pool-closing-day",
            "spring-roof-inspection", "storm-damage-inspection", "high-wind-shingle-risk", "wind-damage-inspection", "wind-driven-rain", "heavy-rain-leak-check", "heat-wave-shingle-stress", "ice-dam-risk", "first-freeze-roofing", "deep-freeze-roofing", "snow-load-roof", "fall-roof-inspection", "gutter-cleaning-season",
            ),
    "Jun": ("patio-day", "heat-wave", "heat-index", "rain-delay",
            "storm-watch", "evening-cooldown", "crisp-day", "gray-streak",
            "warm-break", "cold-snap", "wind-chill", "snow-day",
            "first-freeze", "first-cool-night",
            "early-heat-wave", "ac-overload", "heat-index-strain",
            "storm-power-risk", "spring-tune-up-day", "overcast-run",
            "fall-tune-up-day", "wind-chill-strain", "hard-freeze",
            "mild-winter-break", "snow-load", "deep-freeze", "first-hard-freeze",
            "patio-season-open", "heat-wave-cooling", "heat-index-retail",
            "storm-prep", "wind-advisory", "rain-day-indoor",
            "gray-streak-retail", "fall-clearance-day", "first-warm-weekend",
            "cold-snap-retail", "snow-gear-day", "deep-freeze-retail",
            "first-frost-shop",
            "ac-check-early", "heat-index-auto", "wiper-blade-season", "storm-driving-prep", "spring-service-day", "fall-service-day", "pothole-season", "cold-snap-auto", "wind-chill-auto", "battery-cold-test", "snow-tire-day", "deep-freeze-auto", "first-freeze-auto",
            "dry-spell-watering", "drought-stress", "heat-wave-lawn-stress", "mosquito-surge", "heavy-rain-growth-spurt", "storm-cleanup", "high-wind-debris", "spring-green-up-day", "spring-fertilize-window", "first-freeze-landscaping", "first-snow-landscaping", "fall-leaf-peak", "fall-fertilize-window",
            "chlorine-burn-off", "algae-bloom-risk", "evaporation-watch", "heat-wave-pool-surge", "heavy-rain-dilution", "storm-debris-cleanup", "high-wind-debris-pool", "pool-opening-day", "spa-season-open", "first-freeze-pool", "hard-freeze-pool", "deep-freeze-pool", "pool-closing-day",
            "heat-wave-shingle-stress", "storm-damage-inspection", "high-wind-shingle-risk", "wind-damage-inspection", "wind-driven-rain", "heavy-rain-leak-check", "spring-roof-inspection", "ice-dam-risk", "first-freeze-roofing", "deep-freeze-roofing", "snow-load-roof", "fall-roof-inspection", "gutter-cleaning-season",
            ),
    "Jul": ("heat-wave", "heat-index", "patio-day", "storm-watch",
            "rain-delay", "evening-cooldown", "crisp-day", "gray-streak",
            "warm-break", "cold-snap", "wind-chill", "snow-day",
            "first-freeze", "first-cool-night",
            "ac-overload", "heat-index-strain", "storm-power-risk",
            "early-heat-wave", "overcast-run", "spring-tune-up-day",
            "fall-tune-up-day", "wind-chill-strain", "hard-freeze",
            "mild-winter-break", "snow-load", "deep-freeze", "first-hard-freeze",
            "heat-wave-cooling", "heat-index-retail", "storm-prep",
            "patio-season-open", "wind-advisory", "rain-day-indoor",
            "gray-streak-retail", "fall-clearance-day", "first-warm-weekend",
            "cold-snap-retail", "snow-gear-day", "deep-freeze-retail",
            "first-frost-shop",
            "heat-index-auto", "ac-check-early", "storm-driving-prep", "wiper-blade-season", "spring-service-day", "fall-service-day", "pothole-season", "cold-snap-auto", "wind-chill-auto", "battery-cold-test", "snow-tire-day", "deep-freeze-auto", "first-freeze-auto",
            "dry-spell-watering", "drought-stress", "heat-wave-lawn-stress", "mosquito-surge", "heavy-rain-growth-spurt", "storm-cleanup", "high-wind-debris", "spring-green-up-day", "spring-fertilize-window", "first-freeze-landscaping", "first-snow-landscaping", "fall-leaf-peak", "fall-fertilize-window",
            "chlorine-burn-off", "algae-bloom-risk", "evaporation-watch", "heat-wave-pool-surge", "heavy-rain-dilution", "storm-debris-cleanup", "high-wind-debris-pool", "pool-opening-day", "spa-season-open", "first-freeze-pool", "hard-freeze-pool", "deep-freeze-pool", "pool-closing-day",
            "heat-wave-shingle-stress", "storm-damage-inspection", "high-wind-shingle-risk", "wind-damage-inspection", "wind-driven-rain", "heavy-rain-leak-check", "spring-roof-inspection", "ice-dam-risk", "first-freeze-roofing", "deep-freeze-roofing", "snow-load-roof", "fall-roof-inspection", "gutter-cleaning-season",
            ),
    "Aug": ("heat-wave", "heat-index", "patio-day", "storm-watch",
            "rain-delay", "first-cool-night", "evening-cooldown", "crisp-day",
            "gray-streak", "warm-break", "cold-snap", "wind-chill", "snow-day",
            "first-freeze",
            "ac-overload", "heat-index-strain", "storm-power-risk",
            "early-heat-wave", "overcast-run", "fall-tune-up-day",
            "spring-tune-up-day", "wind-chill-strain", "hard-freeze",
            "mild-winter-break", "snow-load", "deep-freeze", "first-hard-freeze",
            "heat-wave-cooling", "heat-index-retail", "storm-prep",
            "patio-season-open", "wind-advisory", "rain-day-indoor",
            "fall-clearance-day", "gray-streak-retail", "first-warm-weekend",
            "cold-snap-retail", "snow-gear-day", "deep-freeze-retail",
            "first-frost-shop",
            "heat-index-auto", "ac-check-early", "storm-driving-prep", "wiper-blade-season", "fall-service-day", "spring-service-day", "pothole-season", "cold-snap-auto", "wind-chill-auto", "battery-cold-test", "snow-tire-day", "deep-freeze-auto", "first-freeze-auto",
            "dry-spell-watering", "drought-stress", "heat-wave-lawn-stress", "mosquito-surge", "heavy-rain-growth-spurt", "storm-cleanup", "high-wind-debris", "fall-fertilize-window", "spring-green-up-day", "spring-fertilize-window", "first-freeze-landscaping", "first-snow-landscaping", "fall-leaf-peak",
            "chlorine-burn-off", "algae-bloom-risk", "evaporation-watch", "heat-wave-pool-surge", "heavy-rain-dilution", "storm-debris-cleanup", "high-wind-debris-pool", "pool-closing-day", "pool-opening-day", "spa-season-open", "first-freeze-pool", "hard-freeze-pool", "deep-freeze-pool",
            "heat-wave-shingle-stress", "storm-damage-inspection", "high-wind-shingle-risk", "wind-damage-inspection", "wind-driven-rain", "heavy-rain-leak-check", "fall-roof-inspection", "spring-roof-inspection", "ice-dam-risk", "first-freeze-roofing", "deep-freeze-roofing", "snow-load-roof", "gutter-cleaning-season",
            ),
    "Sep": ("patio-day", "first-cool-night", "rain-delay", "crisp-day",
            "evening-cooldown", "heat-index", "heat-wave", "storm-watch",
            "gray-streak", "warm-break", "cold-snap", "wind-chill",
            "snow-day", "first-freeze",
            "fall-tune-up-day", "ac-overload", "heat-index-strain",
            "overcast-run", "storm-power-risk", "early-heat-wave",
            "spring-tune-up-day", "wind-chill-strain", "hard-freeze",
            "mild-winter-break", "snow-load", "deep-freeze", "first-hard-freeze",
            "fall-clearance-day", "heat-wave-cooling", "heat-index-retail",
            "storm-prep", "wind-advisory", "rain-day-indoor",
            "gray-streak-retail", "first-frost-shop", "patio-season-open",
            "first-warm-weekend", "cold-snap-retail", "snow-gear-day",
            "deep-freeze-retail",
            "fall-service-day", "first-freeze-auto", "heat-index-auto", "ac-check-early", "storm-driving-prep", "wiper-blade-season", "pothole-season", "cold-snap-auto", "wind-chill-auto", "battery-cold-test", "snow-tire-day", "deep-freeze-auto", "spring-service-day",
            "fall-fertilize-window", "fall-leaf-peak", "dry-spell-watering", "heavy-rain-growth-spurt", "storm-cleanup", "high-wind-debris", "mosquito-surge", "drought-stress", "heat-wave-lawn-stress", "spring-green-up-day", "spring-fertilize-window", "first-freeze-landscaping", "first-snow-landscaping",
            "pool-closing-day", "chlorine-burn-off", "heavy-rain-dilution", "storm-debris-cleanup", "high-wind-debris-pool", "algae-bloom-risk", "evaporation-watch", "heat-wave-pool-surge", "spa-season-open", "pool-opening-day", "first-freeze-pool", "hard-freeze-pool", "deep-freeze-pool",
            "fall-roof-inspection", "gutter-cleaning-season", "storm-damage-inspection", "high-wind-shingle-risk", "wind-damage-inspection", "wind-driven-rain", "heavy-rain-leak-check", "heat-wave-shingle-stress", "spring-roof-inspection", "ice-dam-risk", "first-freeze-roofing", "deep-freeze-roofing", "snow-load-roof",
            ),
    "Oct": ("crisp-day", "first-cool-night", "first-freeze", "evening-cooldown",
            "rain-delay", "warm-break", "gray-streak", "storm-watch",
            "cold-snap", "wind-chill", "patio-day", "heat-index",
            "heat-wave", "snow-day",
            "fall-tune-up-day", "first-hard-freeze", "overcast-run",
            "storm-power-risk", "wind-chill-strain", "hard-freeze",
            "mild-winter-break", "ac-overload", "heat-index-strain",
            "snow-load", "spring-tune-up-day", "deep-freeze", "early-heat-wave",
            "fall-clearance-day", "first-frost-shop", "wind-advisory",
            "storm-prep", "rain-day-indoor", "gray-streak-retail",
            "cold-snap-retail", "snow-gear-day", "deep-freeze-retail",
            "heat-wave-cooling", "heat-index-retail", "patio-season-open",
            "first-warm-weekend",
            "fall-service-day", "first-freeze-auto", "cold-snap-auto", "wind-chill-auto", "battery-cold-test", "wiper-blade-season", "storm-driving-prep", "pothole-season", "snow-tire-day", "deep-freeze-auto", "heat-index-auto", "ac-check-early", "spring-service-day",
            "fall-leaf-peak", "fall-fertilize-window", "first-freeze-landscaping", "storm-cleanup", "high-wind-debris", "heavy-rain-growth-spurt", "first-snow-landscaping", "mosquito-surge", "dry-spell-watering", "drought-stress", "heat-wave-lawn-stress", "spring-green-up-day", "spring-fertilize-window",
            "pool-closing-day", "first-freeze-pool", "spa-season-open", "storm-debris-cleanup", "high-wind-debris-pool", "heavy-rain-dilution", "chlorine-burn-off", "algae-bloom-risk", "evaporation-watch", "heat-wave-pool-surge", "pool-opening-day", "hard-freeze-pool", "deep-freeze-pool",
            "fall-roof-inspection", "gutter-cleaning-season", "first-freeze-roofing", "storm-damage-inspection", "high-wind-shingle-risk", "wind-damage-inspection", "wind-driven-rain", "heavy-rain-leak-check", "snow-load-roof", "ice-dam-risk", "deep-freeze-roofing", "spring-roof-inspection", "heat-wave-shingle-stress",
            ),
    "Nov": ("first-freeze", "cold-snap", "gray-streak", "warm-break",
            "crisp-day", "wind-chill", "storm-watch", "snow-day",
            "rain-delay", "evening-cooldown", "patio-day", "heat-index",
            "heat-wave", "first-cool-night",
            "first-hard-freeze", "fall-tune-up-day", "hard-freeze",
            "overcast-run", "wind-chill-strain", "mild-winter-break",
            "storm-power-risk", "snow-load", "deep-freeze",
            "spring-tune-up-day", "ac-overload", "heat-index-strain",
            "early-heat-wave",
            "first-frost-shop", "cold-snap-retail", "fall-clearance-day",
            "wind-advisory", "gray-streak-retail", "storm-prep",
            "snow-gear-day", "deep-freeze-retail", "first-warm-weekend",
            "rain-day-indoor", "heat-wave-cooling", "heat-index-retail",
            "patio-season-open",
            "first-freeze-auto", "cold-snap-auto", "wind-chill-auto", "battery-cold-test", "fall-service-day", "snow-tire-day", "storm-driving-prep", "deep-freeze-auto", "wiper-blade-season", "pothole-season", "heat-index-auto", "ac-check-early", "spring-service-day",
            "fall-leaf-peak", "first-freeze-landscaping", "first-snow-landscaping", "storm-cleanup", "high-wind-debris", "heavy-rain-growth-spurt", "fall-fertilize-window", "mosquito-surge", "dry-spell-watering", "drought-stress", "heat-wave-lawn-stress", "spring-green-up-day", "spring-fertilize-window",
            "pool-closing-day", "first-freeze-pool", "hard-freeze-pool", "spa-season-open", "storm-debris-cleanup", "high-wind-debris-pool", "heavy-rain-dilution", "pool-opening-day", "chlorine-burn-off", "algae-bloom-risk", "evaporation-watch", "heat-wave-pool-surge", "deep-freeze-pool",
            "gutter-cleaning-season", "fall-roof-inspection", "first-freeze-roofing", "storm-damage-inspection", "high-wind-shingle-risk", "wind-damage-inspection", "wind-driven-rain", "snow-load-roof", "heavy-rain-leak-check", "ice-dam-risk", "deep-freeze-roofing", "spring-roof-inspection", "heat-wave-shingle-stress",
            ),
    "Dec": ("cold-snap", "snow-day", "wind-chill", "warm-break", "gray-streak",
            "storm-watch", "crisp-day", "rain-delay", "evening-cooldown",
            "heat-index", "heat-wave", "patio-day", "first-freeze",
            "first-cool-night",
            "hard-freeze", "deep-freeze", "first-hard-freeze", "wind-chill-strain",
            "overcast-run", "storm-power-risk", "snow-load", "mild-winter-break",
            "spring-tune-up-day", "fall-tune-up-day", "early-heat-wave",
            "heat-index-strain", "ac-overload",
            "deep-freeze-retail", "cold-snap-retail", "snow-gear-day",
            "first-warm-weekend", "wind-advisory", "gray-streak-retail",
            "storm-prep", "first-frost-shop", "fall-clearance-day",
            "rain-day-indoor", "heat-wave-cooling", "heat-index-retail",
            "patio-season-open",
            "cold-snap-auto", "wind-chill-auto", "battery-cold-test", "deep-freeze-auto", "first-freeze-auto", "snow-tire-day", "storm-driving-prep", "wiper-blade-season", "fall-service-day", "pothole-season", "heat-index-auto", "ac-check-early", "spring-service-day",
            "first-freeze-landscaping", "first-snow-landscaping", "storm-cleanup", "high-wind-debris", "fall-leaf-peak", "mosquito-surge", "dry-spell-watering", "drought-stress", "heavy-rain-growth-spurt", "fall-fertilize-window", "heat-wave-lawn-stress", "spring-green-up-day", "spring-fertilize-window",
            "first-freeze-pool", "hard-freeze-pool", "deep-freeze-pool", "spa-season-open", "storm-debris-cleanup", "high-wind-debris-pool", "pool-closing-day", "pool-opening-day", "chlorine-burn-off", "algae-bloom-risk", "heavy-rain-dilution", "evaporation-watch", "heat-wave-pool-surge",
            "ice-dam-risk", "first-freeze-roofing", "deep-freeze-roofing", "snow-load-roof", "storm-damage-inspection", "high-wind-shingle-risk", "wind-damage-inspection", "wind-driven-rain", "gutter-cleaning-season", "fall-roof-inspection", "heavy-rain-leak-check", "spring-roof-inspection", "heat-wave-shingle-stress",
            ),
}

MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def triggers_for_vertical(vertical: str) -> dict[str, Trigger]:
    return {k: t for k, t in TRIGGERS.items() if t.vertical == vertical}


def month_order(vertical: str, month_abbr: str) -> list[str]:
    """All of this vertical's trigger ids, ordered for that month's card
    strip. Falls back to the registry's own order for an unknown month."""
    available = [t for t in TRIGGERS if TRIGGERS[t].vertical == vertical]
    order = MONTHS.get(month_abbr, ())
    ranked = [t for t in order if t in available]
    ranked += [t for t in available if t not in ranked]
    return ranked


def validate_picks(trigger_ids: list[str], vertical: str = "restaurant") -> tuple[bool, str]:
    """Server-side enforcement of the cap and the vocabulary. Never trusts
    the picker UI to have refused an invalid choice on its own."""
    ids = [str(t or "").strip() for t in (trigger_ids or []) if str(t or "").strip()]
    if not ids:
        return False, "Choose at least one trigger."
    if len(ids) > len(set(ids)):
        return False, "The same trigger was picked twice."
    if len(ids) > MAX_TRIGGERS:
        return False, (f"You can run up to {MAX_TRIGGERS} triggers at once. "
                       "Drop one to add another.")
    known = triggers_for_vertical(vertical)
    unknown = [t for t in ids if t not in known]
    if unknown:
        return False, f"Not a recognized trigger: {', '.join(unknown)}."
    return True, ""


def _heat_index_f(temp_f: float, humidity_pct: float) -> float:
    """NOAA's Rothfusz regression, the published formula behind every US
    heat-index figure. Only meaningful at or above roughly 80°F with
    humidity present; below that the simple average is close enough and the
    full regression can produce nonsense."""
    t, r = float(temp_f), float(humidity_pct)
    if t < 80:
        return t
    hi = (-42.379 + 2.04901523 * t + 10.14333127 * r - 0.22475541 * t * r
          - 0.00683783 * t * t - 0.05481717 * r * r
          + 0.00122874 * t * t * r + 0.00085282 * t * r * r
          - 0.00000199 * t * t * r * r)
    return round(hi, 1)


def _within_windows(now_hhmm: str, windows: tuple[tuple[str, str], ...]) -> bool:
    if not windows:
        return True
    return any(start <= now_hhmm <= end for start, end in windows)


def _month_ok(rule: dict, today: date) -> bool:
    months = rule.get("months")
    if not months:
        return True
    return today.month in months


def _not_before_ok(rule: dict, today: date) -> bool:
    nb = rule.get("not_before")
    if not nb:
        return True
    month, day = nb
    return (today.month, today.day) >= (month, day)


def evaluate_trigger(trigger_id: str, snapshot: dict, state: dict | None = None,
                     *, now_hhmm: str = "12:00", today: date | None = None) -> dict:
    """Is this trigger's condition true right now?

    Returns ``{"active": True|False|None, "measured": bool, "detail": str,
    "state": dict}``. ``active`` is ``None`` exactly when ``measured`` is
    False — a snapshot missing a field the rule needs answers "not measured",
    never a guessed True or False. ``state`` is the campaign's own carried
    state for this trigger (season flags, streak counts), returned updated
    so the caller can persist it; nothing here reaches a store on its own.
    """
    trig = TRIGGERS.get(trigger_id)
    state = dict(state or {})
    if trig is None:
        return {"active": None, "measured": False,
                "detail": f"Unknown trigger {trigger_id!r}.", "state": state}
    today = today or date.today()
    rule = trig.rule
    snap = snapshot or {}

    if not _month_ok(rule, today):
        return {"active": False, "measured": True,
                "detail": "Outside this trigger's active months.", "state": state}
    if not _within_windows(now_hhmm, trig.windows):
        return {"active": False, "measured": True,
                "detail": "Outside the serving window.", "state": state}

    if "alert_required" in rule:
        alerts = snap.get("official_alerts") or []
        active = bool(alerts)
        detail = (f"Active alert: {alerts[0]}" if active
                  else "No official alert on file.")
        return {"active": active, "measured": True, "detail": detail, "state": state}

    if "once_per_season" in rule:
        if not _not_before_ok(rule, today):
            return {"active": False, "measured": True,
                    "detail": "Before this trigger's earliest date.", "state": state}
        low = snap.get("forecast_low")
        if low is None:
            return {"active": None, "measured": False,
                    "detail": "Today's low was not in the snapshot.", "state": state}
        season_key = f"season_fired_{rule['once_per_season']}_{today.year}"
        if state.get(season_key):
            return {"active": False, "measured": True,
                    "detail": "Already fired once this season.", "state": state}
        hit = float(low) <= float(rule["temp_low_max"])
        if hit:
            state[season_key] = today.isoformat()
        return {"active": hit, "measured": True,
                "detail": f"Today's low: {low}°F.", "state": state}

    if "cloud_percent_min" in rule:
        cloud = snap.get("cloud_percent")
        if cloud is None:
            return {"active": None, "measured": False,
                    "detail": "This weather provider does not report cloud "
                             "cover, so the streak cannot be measured.",
                    "state": state}
        streak = int(state.get("cloud_streak") or 0)
        streak = streak + 1 if float(cloud) >= float(rule["cloud_percent_min"]) else 0
        state["cloud_streak"] = streak
        need = int(rule.get("consecutive_days") or 1)
        return {"active": streak >= need, "measured": True,
                "detail": f"{streak} of {need} overcast days.", "state": state}

    if "evening_drop_min" in rule:
        high, temp = snap.get("forecast_high"), snap.get("temperature")
        if high is None or temp is None:
            return {"active": None, "measured": False,
                    "detail": "Today's high or current temperature is "
                             "missing from the snapshot.", "state": state}
        drop = float(high) - float(temp)
        return {"active": drop >= float(rule["evening_drop_min"]), "measured": True,
                "detail": f"{drop:.0f}°F below today's high.", "state": state}

    if "heat_index_min" in rule:
        temp, hum = snap.get("temperature"), snap.get("humidity")
        if temp is None or hum is None:
            return {"active": None, "measured": False,
                    "detail": "Temperature or humidity is missing from the "
                             "snapshot.", "state": state}
        hi = _heat_index_f(temp, hum)
        return {"active": hi >= float(rule["heat_index_min"]), "measured": True,
                "detail": f"Heat index: {hi}°F.", "state": state}

    # Plain range checks: temp, feels-like, precip probability, wind, snow.
    checks = (
        ("temp_min", "temperature", lambda v, want: v >= want),
        ("temp_max", "temperature", lambda v, want: v <= want),
        ("feels_like_max", "feels_like", lambda v, want: v <= want),
        ("precip_prob_min", "rain_probability", lambda v, want: v >= want),
        ("precip_prob_max", "rain_probability", lambda v, want: v <= want),
        ("wind_mph_max", "wind_mph", lambda v, want: v <= want),
        ("wind_mph_min", "wind_mph", lambda v, want: v >= want),
        ("snow_in_min", "snow_inches", lambda v, want: v >= want),
    )
    parts = []
    for rule_key, snap_key, test in checks:
        if rule_key not in rule:
            continue
        value = snap.get(snap_key)
        if value is None:
            return {"active": None, "measured": False,
                    "detail": f"{snap_key.replace('_', ' ')} is missing from "
                             "the snapshot.", "state": state}
        parts.append(bool(test(float(value), float(rule[rule_key]))))
    if not parts:
        return {"active": None, "measured": False,
                "detail": "This trigger has no evaluable rule.", "state": state}
    return {"active": all(parts), "measured": True,
            "detail": trig.condition_label, "state": state}
