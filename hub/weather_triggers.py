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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# A campaign with a fourth pick is not a campaign that reaches more people;
# below the restaurant rate card's mid rung it is one that reaches nobody's
# auction. Enforced here, not merely disabled in the picker.
MAX_TRIGGERS = 3

VERTICALS = ("restaurant", "hvac", "retail", "auto")
VERTICAL_LABELS = {"restaurant": "Restaurant", "hvac": "HVAC / Home Comfort",
                   "retail": "Retail / Home Goods", "auto": "Auto Repair / Service"}


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
