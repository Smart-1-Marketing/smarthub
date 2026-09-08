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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# A campaign with a fourth pick is not a campaign that reaches more people;
# below the restaurant rate card's mid rung it is one that reaches nobody's
# auction. Enforced here, not merely disabled in the picker.
MAX_TRIGGERS = 3

VERTICALS = ("restaurant",)


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
}

# Ordering for the month strip: which triggers a rep browsing that month is
# most likely to want to talk about, most-relevant first. This governs
# display order only — a chosen trigger evaluates all year round, whatever
# month it was picked in.
MONTHS: dict[str, tuple[str, ...]] = {
    "Jan": ("cold-snap", "snow-day", "wind-chill", "warm-break", "gray-streak",
            "storm-watch", "crisp-day", "evening-cooldown", "heat-index",
            "heat-wave", "patio-day", "rain-delay", "first-freeze", "first-cool-night"),
    "Feb": ("cold-snap", "snow-day", "wind-chill", "warm-break", "gray-streak",
            "storm-watch", "crisp-day", "evening-cooldown", "heat-index",
            "heat-wave", "patio-day", "rain-delay", "first-freeze", "first-cool-night"),
    "Mar": ("warm-break", "crisp-day", "rain-delay", "cold-snap", "gray-streak",
            "wind-chill", "storm-watch", "patio-day", "evening-cooldown",
            "heat-index", "heat-wave", "snow-day", "first-freeze", "first-cool-night"),
    "Apr": ("crisp-day", "patio-day", "rain-delay", "gray-streak", "storm-watch",
            "evening-cooldown", "warm-break", "heat-index", "heat-wave",
            "cold-snap", "wind-chill", "snow-day", "first-freeze", "first-cool-night"),
    "May": ("patio-day", "crisp-day", "rain-delay", "storm-watch",
            "evening-cooldown", "heat-index", "heat-wave", "gray-streak",
            "warm-break", "cold-snap", "wind-chill", "snow-day",
            "first-freeze", "first-cool-night"),
    "Jun": ("patio-day", "heat-wave", "heat-index", "rain-delay",
            "storm-watch", "evening-cooldown", "crisp-day", "gray-streak",
            "warm-break", "cold-snap", "wind-chill", "snow-day",
            "first-freeze", "first-cool-night"),
    "Jul": ("heat-wave", "heat-index", "patio-day", "storm-watch",
            "rain-delay", "evening-cooldown", "crisp-day", "gray-streak",
            "warm-break", "cold-snap", "wind-chill", "snow-day",
            "first-freeze", "first-cool-night"),
    "Aug": ("heat-wave", "heat-index", "patio-day", "storm-watch",
            "rain-delay", "first-cool-night", "evening-cooldown", "crisp-day",
            "gray-streak", "warm-break", "cold-snap", "wind-chill", "snow-day",
            "first-freeze"),
    "Sep": ("patio-day", "first-cool-night", "rain-delay", "crisp-day",
            "evening-cooldown", "heat-index", "heat-wave", "storm-watch",
            "gray-streak", "warm-break", "cold-snap", "wind-chill",
            "snow-day", "first-freeze"),
    "Oct": ("crisp-day", "first-cool-night", "first-freeze", "evening-cooldown",
            "rain-delay", "warm-break", "gray-streak", "storm-watch",
            "cold-snap", "wind-chill", "patio-day", "heat-index",
            "heat-wave", "snow-day"),
    "Nov": ("first-freeze", "cold-snap", "gray-streak", "warm-break",
            "crisp-day", "wind-chill", "storm-watch", "snow-day",
            "rain-delay", "evening-cooldown", "patio-day", "heat-index",
            "heat-wave", "first-cool-night"),
    "Dec": ("cold-snap", "snow-day", "wind-chill", "warm-break", "gray-streak",
            "storm-watch", "crisp-day", "rain-delay", "evening-cooldown",
            "heat-index", "heat-wave", "patio-day", "first-freeze",
            "first-cool-night"),
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
